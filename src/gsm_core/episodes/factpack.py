"""Deterministic FactPack — the only thing an LLM is ever allowed to read.

A raw simulator trace answers "what happened to every actor in the world".  A model asked
to explain one driver's plan needs a much smaller and much stricter object: the handful of
statements we can defend, each already labelled with who recorded it and when.  Building
that object here rather than at the provider boundary means three things hold by
construction:

* **Nothing reaches the model that is not already validated.**  A ``FactPack`` is built
  from :class:`CompositeEpisode` instances that passed ``validate_episode``, so past-only
  evidence and provenance closure are inherited rather than re-checked.
* **Every number the model may write down is enumerated in advance.**  The verifier
  compares generated text against :attr:`FactPack.allowed_number_tokens`; a number that is
  not in that set cannot have come from our data.
* **The deterministic answer is computed first.**  ``template_baseline`` is the sentence we
  would have shipped with no model at all.  It is the fallback, the eval comparison point,
  and the reason a provider outage is a quality regression rather than an outage.

The module is pure: no clock, no network, no randomness.  Two calls with the same episodes
produce byte-identical packs, which is what makes the narration cache safe to key on
:attr:`FactPack.fingerprint`.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from gsm_core.episodes.contract import CompositeEpisode, EpisodeStatus, EvidenceMode
from gsm_core.episodes.templates import STATE_LABELS, present

FACTPACK_SCHEMA_VERSION = "1.0.0"

TASK_WHY = "WHY_PLAN_CHANGE"
TASK_REVIEW = "SHIFT_REVIEW"
# HIL Lát 1.5: thuật lại một điểm dừng cho người đóng vai tài xế. Pack do
# `hil_factpack.build_hil_pack` dựng từ snapshot phiên HIL — không đi qua CompositeEpisode.
TASK_HIL_PAUSE = "HIL_PAUSE"

# Output budgets from the product spec: the driver is reading this at a kerb, not at a desk.
MAX_WHY_CHARS = 400
MAX_REVIEW_CHARS = 1200
# 🔴 NỚI 2026-08-24 (Cường: *"phần kết ca có thể viết dài, không cần cap độ dài, cần chi
# tiết để tài xế có thể hiểu được cái gì làm ổn rồi, cái gì chưa ổn"*).
#
# 140 từ ≈ 5 câu — đủ để liệt kê, không đủ để GIẢI THÍCH. Một bản tổng kết mà tài xế
# đọc xong vẫn không biết mình làm tốt chỗ nào thì nó chỉ là một bảng số biết nói.
#
# ⚠ Không bỏ hẳn trần: một bản review không giới hạn sẽ nuốt cả ngân sách token và đẩy
# phần đáng đọc xuống dưới. 400 từ ≈ một màn hình điện thoại cuộn hai lần.
MAX_REVIEW_WORDS = 400
MIN_REVIEW_WORDS = 40
# Sáu ý thay vì bốn: một ca có tiền, cuốc, điểm, pin, giờ chờ, và ít nhất một điều
# đáng rút kinh nghiệm. Bốn ô thì luôn phải bỏ hai thứ.
MAX_REVIEW_POINTS = 6
MIN_REVIEW_POINTS = 2

# Fact budgets.  These are not tidiness limits — they are the difference between a pack and
# a log.  An unbounded pack for a busy shift measured 22.124 characters (8.715 prompt
# tokens): every component signal of eleven episodes, which is exactly the raw dump §4 says
# never to send.  A model given that spends its budget reading and returns nothing.
# The caps keep the most recent evidence, because a plan question is about what just
# changed, and drop the tail rather than summarising it into something unverifiable.
MAX_WHY_FACTS = 12
MAX_CONTEXT_FACTS = 6
MAX_REVIEW_FACTS = 24
MAX_DERIVED_PER_EPISODE = 2
# Caveats accumulate one per episode and repeat the same three boundaries; the model needs
# the boundaries, not sixteen restatements of them.  The template fallback still uses the
# episode's own first caveat, so nothing is lost on the deterministic path.
MAX_MODEL_CAVEATS = 4

# Canonical action vocabulary, split by how safely a phrase identifies an action.
#
# ``ACTION_PHRASES`` are unambiguous: "đổi pin" in a driver-facing sentence is always about
# a swap.  ``PRESCRIPTIVE_ACTION_PHRASES`` are ordinary Vietnamese words that only denote an
# action when something prescribes them — "nghỉ" appears in the sentence "thời gian chờ và
# thời gian nghỉ được theo dõi riêng", which recommends nothing.  Checking the second group
# unconditionally would reject our own template copy, so the verifier requires a
# prescriptive marker near the phrase before treating it as an action claim.
ACTION_PHRASES: dict[str, tuple[str, ...]] = {
    "SWAP": ("đổi pin", "thay pin"),
    "END": ("kết thúc ca", "tan ca", "kết ca"),
    "EXTEND": ("kéo dài ca", "gia hạn ca", "làm thêm ca"),
    "ONLINE": ("bật online", "mở nhận cuốc"),
    "PROTECT_ELIGIBILITY": ("giữ điều kiện", "bảo vệ điều kiện"),
    "REPOSITION_SIM_ONLY": ("di chuyển khu vực", "đổi khu vực", "chuyển vùng"),
}

PRESCRIPTIVE_ACTION_PHRASES: dict[str, tuple[str, ...]] = {
    "REST": ("nghỉ",),
}

# Words that turn a bare noun into a recommendation or a scheduled step.
PRESCRIPTIVE_MARKERS: tuple[str, ...] = (
    "nen", "hay", "can", "chuan bi", "ke hoach", "se ", "du kien", "khung ",
    "sap toi", "buoc tiep theo",
)

ACTION_LABELS: dict[str, str] = {
    "SWAP": "đổi pin",
    "REST": "nghỉ",
    "END": "kết thúc ca",
    "EXTEND": "kéo dài ca",
    "ONLINE": "online",
    "PROTECT_ELIGIBILITY": "giữ điều kiện",
    "REPOSITION_SIM_ONLY": "di chuyển khu vực",
    "NO_ACTION": "không có hành động",
}

# Sent to the model as explicit prohibitions.  They duplicate the verifier on purpose: the
# prompt lowers the rejection rate, the verifier is what actually holds the line.
FORBIDDEN_CLAIMS: tuple[str, ...] = (
    "Không được viết bất kỳ con số nào không có trong validated_numbers.",
    "Không được nói một sự việc GÂY RA sự việc khác; chỉ được nói thứ tự thời gian.",
    "Không được dự đoán hoặc hứa thu nhập, không nói 'kiếm thêm', 'bù lại', 'tăng thu nhập'.",
    "Không được nhận định về sức khoẻ, mệt mỏi hay nhu cầu nghỉ của tài xế.",
    "Không được khuyên nhận, từ chối hay huỷ một cuốc cụ thể.",
    "Không được gợi ý địa điểm, khu vực hay điểm nóng để di chuyển tới.",
    "Không được biến kế hoạch sắp tới thành việc đang xảy ra.",
    "Không được nói payout đang chờ là đã chốt.",
    "Không được nói tài xế đã làm theo hay không làm theo lời khuyên.",
    "Không được kết luận một mốc thưởng là bất khả thi nếu pack không có phán quyết solver.",
    "Không được viết tên khoá kỹ thuật (dạng có dấu gạch dưới) ra câu cho tài xế.",
    "Không viết ý rỗng: '0 lần huỷ', 'là 0', 'chưa có bước tiếp theo' — bỏ hẳn ý đó.",
)

_WS = re.compile(r"\s+")
_NUMERIC_TOKEN = re.compile(r"\d[\d.,:]*")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def _hm(value: Any) -> str | None:
    """ISO stamp → ``HH:MM``.  Drivers read clock time, not offsets."""
    text = str(value or "")
    return text[11:16] if len(text) >= 16 else None


def _window_label(window: dict | None) -> str | None:
    if not window:
        return None
    start, end = _hm(window.get("start")), _hm(window.get("end"))
    if start and end:
        return f"{start} → {end}"
    return start or end


def _clock(at_min: float) -> str:
    """Minutes from the run base day → ``HH:MM``, the only time form a driver reads."""
    minute = int(round(float(at_min))) % 1440
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _money(value: int) -> str:
    return f"{int(value):,}".replace(",", ".")


def _display(key: str, value: Any, unit: str) -> str:
    """The exact string a driver may see for this number.

    The verifier's allowed-token set is derived from this, so a formatting choice made
    here is automatically a formatting choice the model is permitted to reproduce.
    """
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if unit == "vnd" or key.endswith("_vnd"):
        try:
            return _money(value)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


@dataclass(frozen=True)
class Fact:
    """One defensible statement, addressable by id so the model can cite it."""

    fact_id: str
    kind: str                      # observation | derived | plan_delta | action | outcome
    text: str
    evidence_mode: str             # OBSERVED | INFERRED
    at_min: float | None = None
    source_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"fact_id": self.fact_id, "kind": self.kind, "text": self.text,
                "evidence_mode": self.evidence_mode,
                "at_min": (None if self.at_min is None else round(float(self.at_min), 1)),
                "source_refs": list(self.source_refs)}


@dataclass(frozen=True)
class FactNumber:
    """A number the model is allowed to reproduce verbatim, and nothing else."""

    number_id: str
    key: str
    label: str
    value: Any
    unit: str
    display: str
    source: str

    def to_dict(self) -> dict:
        return {"number_id": self.number_id, "key": self.key, "label": self.label,
                "value": self.value, "unit": self.unit, "display": self.display,
                "source": self.source}


@dataclass(frozen=True)
class FactPack:
    task_type: str
    schema_version: str
    episode_id: str
    episode_type: str
    run_id: str
    actor_id: int
    driver_id: str
    material_revision: int
    at_min: float
    data_mode: str
    is_mock: bool
    evidence_mode: str
    lifecycle_state: str
    state_label: str
    current_action: dict | None
    future_plan: tuple[dict, ...]
    plan_deltas: tuple[dict, ...]
    observed_facts: tuple[Fact, ...]
    inferred_facts: tuple[Fact, ...]
    validated_numbers: tuple[FactNumber, ...]
    episode_refs: tuple[str, ...]
    source_refs: tuple[str, ...]
    caveats: tuple[str, ...]
    template_baseline: str
    max_output_chars: int
    forbidden_claims: tuple[str, ...] = FORBIDDEN_CLAIMS
    income_settled: bool = True
    # Whether the thing being described is still happening, and until when.  Without these
    # the pack could describe a wait in full and still not answer "am I still waiting?" or
    # "how long is this good for?" — so any validity phrasing in the prose was a number the
    # model had made up.  ``valid_until`` is minutes-from-shift-start like every other
    # anchor in the pack; ``model_payload`` renders it as a clock time.
    status: str = "OPEN"
    valid_until: float | None = None
    # Baseline broken into the bullets the review surface renders.  Kept beside the joined
    # string so the fallback fills the same shape as a model answer instead of collapsing a
    # structured recap into one paragraph the moment the provider is unavailable.
    baseline_points: tuple[str, ...] = ()
    # Actions that appeared in the driver's plan at some point during the shift.  A review
    # has no ``current_action`` — the shift is over — but it still needs to be able to say
    # "kế hoạch đã chuyển sang đổi pin".  Without this the review pack authorises no action
    # vocabulary at all, and the verifier cannot tell a truthful recollection from an
    # invented recommendation, so it has to reject both.
    historical_actions: tuple[dict, ...] = ()

    # ------------------------------------------------------------------ derived
    @property
    def facts(self) -> tuple[Fact, ...]:
        return tuple(self.observed_facts) + tuple(self.inferred_facts)

    @property
    def fact_ids(self) -> frozenset[str]:
        return frozenset(fact.fact_id for fact in self.facts)

    @property
    def allowed_action_codes(self) -> frozenset[str]:
        codes = set()
        if self.current_action:
            codes.add(str(self.current_action.get("code")))
        for step in self.future_plan:
            codes.add(str(step.get("code")))
        for delta in self.plan_deltas:
            for side in ("from", "to"):
                value = delta.get(side)
                if value:
                    codes.add(str(value))
        for action in self.historical_actions:
            codes.add(str(action.get("code")))
        return frozenset(code for code in codes if code)

    @property
    def future_only_action_codes(self) -> frozenset[str]:
        """Actions that are planned but are *not* happening now.

        The single most damaging failure mode for this feature is a sentence that reads
        "bạn đang đổi pin" when the swap is scheduled for 18:40.  Naming the set here lets
        the verifier check present-tense framing against it directly.
        """
        current = str((self.current_action or {}).get("code") or "")
        return frozenset(str(step.get("code")) for step in self.future_plan
                         if str(step.get("code")) and str(step.get("code")) != current)

    @property
    def allowed_number_tokens(self) -> frozenset[str]:
        """Every numeric string the model may legitimately produce.

        Includes clock times from any action window: a plan that says 18:40 is a plan the
        explanation is allowed to mention, even though 1840 never appears as a number.
        """
        tokens: set[str] = set()
        for number in self.validated_numbers:
            tokens.add(str(number.display))
            tokens.add(str(number.value))
        for window in self._windows():
            for stamp in (_hm(window.get("start")), _hm(window.get("end"))):
                if stamp:
                    tokens.add(stamp)
                    tokens.add(stamp.split(":")[0])
                    tokens.add(stamp.split(":")[0].lstrip("0") or "0")
        for fact in self.facts:
            for stamp in re.findall(r"\b\d{1,2}:\d{2}\b", fact.text):
                tokens.add(stamp)
                tokens.add(stamp.split(":")[0])
            # The model is shown each fact's time as a clock string, so it must be
            # permitted to repeat it.  Without this the pack would hand over a time and
            # then reject the answer for containing it.
            if fact.at_min is not None:
                stamp = _clock(fact.at_min)
                tokens.add(stamp)
                tokens.add(stamp.split(":")[0])
                tokens.add(stamp.split(":")[0].lstrip("0") or "0")
        # Anything our own deterministic copy already puts on the driver's screen.  Without
        # this the fallback sentence could fail the very verifier that selected it, which
        # would make a provider outage look like a content violation.
        for token in _NUMERIC_TOKEN.findall(self.template_baseline):
            tokens.add(token.strip(".,"))
        return frozenset(token for token in tokens if token)

    def _windows(self) -> list[dict]:
        windows: list[dict] = []
        if self.current_action and self.current_action.get("window"):
            windows.append(self.current_action["window"])
        for step in self.future_plan:
            if step.get("window"):
                windows.append(step["window"])
        return windows

    def to_dict(self) -> dict:
        return {
            "task_type": self.task_type,
            "schema_version": self.schema_version,
            "episode_id": self.episode_id,
            "episode_type": self.episode_type,
            "material_revision": int(self.material_revision),
            "lifecycle_state": self.lifecycle_state,
            "state_label": self.state_label,
            "data_mode": self.data_mode,
            "is_mock": bool(self.is_mock),
            "evidence_mode": self.evidence_mode,
            "current_action": self.current_action,
            "future_plan": [dict(step) for step in self.future_plan],
            "plan_deltas": [dict(delta) for delta in self.plan_deltas],
            "historical_actions": [dict(action) for action in self.historical_actions],
            "observed_facts": [fact.to_dict() for fact in self.observed_facts],
            "inferred_facts": [fact.to_dict() for fact in self.inferred_facts],
            "validated_numbers": [n.to_dict() for n in self.validated_numbers],
            "episode_refs": list(self.episode_refs),
            "caveats": list(self.caveats),
            "forbidden_claims": list(self.forbidden_claims),
            "income_settled": bool(self.income_settled),
            "status": self.status,
            "valid_until": self.valid_until,
            "max_output_chars": int(self.max_output_chars),
        }

    def model_payload(self) -> dict:
        """The pack as the provider sees it — narrower than :meth:`to_dict` on purpose.

        Three things are stripped, each for its own reason:

        * **Source refs and episode ids** are provenance for us.  The model has no use for
          them and quoting one at a driver is the failure they invite.
        * **Duplicated number fields** (``key``, ``value``, ``source``) say the same thing
          as ``display`` in three more ways, and ``display`` is the only form the verifier
          will accept back.
        * **Absolute minute offsets** become clock times, because "1131.0" is not something
          any driver reads and a model that echoes it fails the number rule anyway.

        This is not only tidiness.  The full pack for a busy shift is 13.874 characters;
        the configured reasoning model spent its entire completion budget on it and
        returned an empty string.  Narrowing the payload is what makes the review answerable
        at all.
        """
        def fact(item: Fact) -> dict:
            entry = {"fact_id": item.fact_id, "kind": item.kind, "text": item.text,
                     "evidence_mode": item.evidence_mode}
            if item.at_min is not None:
                entry["at"] = _clock(item.at_min)
            return entry

        return {
            "task_type": self.task_type,
            "schema_version": self.schema_version,
            "episode_type": self.episode_type,
            "lifecycle_state": self.lifecycle_state,
            "state_label": self.state_label,
            "data_mode": self.data_mode,
            "evidence_mode": self.evidence_mode,
            "current_action": self.current_action,
            "future_plan": [dict(step) for step in self.future_plan],
            "plan_deltas": [dict(delta) for delta in self.plan_deltas],
            "historical_actions": [dict(action) for action in self.historical_actions],
            "observed_facts": [fact(item) for item in self.observed_facts],
            "inferred_facts": [fact(item) for item in self.inferred_facts],
            "validated_numbers": [
                {"number_id": n.number_id, "label": n.label, "display": n.display,
                 "unit": n.unit} for n in self.validated_numbers],
            "caveats": list(self.caveats[:MAX_MODEL_CAVEATS]),
            "forbidden_claims": list(self.forbidden_claims),
            "income_settled": bool(self.income_settled),
            # Clock form, like every other time the model sees: "1131.0" is not a thing a
            # driver reads, and a model that echoes a raw minute offset fails the number
            # rule anyway.
            "status": self.status,
            "valid_until": _clock(self.valid_until) if self.valid_until is not None else None,
            "max_output_chars": int(self.max_output_chars),
        }

    @property
    def fingerprint(self) -> str:
        """Digest of everything that could change the answer.

        Keyed on the model-visible payload rather than the full pack: two packs that
        differ only in an internal ref would produce the same sentence, so they should
        share a cache entry.
        """
        return hashlib.sha256(
            _canonical_json(self.model_payload()).encode("utf-8")).hexdigest()

    @property
    def cache_key(self) -> tuple[str, str, int, str]:
        return (self.task_type, self.episode_id, int(self.material_revision),
                self.fingerprint)


# --------------------------------------------------------------------------- builders
def _action_dict(action: Any) -> dict | None:
    if action is None:
        return None
    return {"code": action.code,
            "label": ACTION_LABELS.get(action.code, action.code),
            "window": action.window,
            "window_label": _window_label(action.window),
            "authority": action.authority.value}


def _numbers_from(episodes: Iterable[CompositeEpisode]) -> tuple[FactNumber, ...]:
    """Collect numbers across episodes, de-duplicated by (key, value).

    Two episodes reporting the same payout must not become two different numbers the model
    could contrast against each other.
    """
    from gsm_core.episodes.templates import _NUMBER_LABELS

    seen: dict[tuple[str, str], FactNumber] = {}
    for episode in episodes:
        for number in episode.numbers:
            display = _display(number.key, number.value, number.unit)
            key = (number.key, display)
            if key in seen:
                continue
            seen[key] = FactNumber(
                number_id=f"n{len(seen) + 1}", key=number.key,
                label=_NUMBER_LABELS.get(number.key, number.key),
                value=number.value, unit=number.unit, display=display,
                source=number.source)
    return tuple(seen.values())


def _outcome_fact(episode: CompositeEpisode, index: int) -> Fact:
    """One line saying what this episode was and how it ended."""
    label = STATE_LABELS.get(episode.lifecycle_state, episode.lifecycle_state)
    return Fact(
        fact_id=f"f{index}", kind="outcome",
        text=f"{episode.episode_type}: {label}",
        evidence_mode=episode.evidence_mode.value,
        at_min=float(episode.last_updated_at),
        source_refs=(episode.episode_id,))


def _facts_from(episode: CompositeEpisode, start_index: int, *,
                max_signals: int | None = None,
                max_derived: int | None = None) -> tuple[list[Fact], list[Fact]]:
    """Split one episode's evidence into what the world recorded and what we inferred.

    The split is not cosmetic.  ``observed`` may be stated plainly; ``inferred`` is our own
    reading and the model is told to hedge it.  Collapsing the two is exactly how a derived
    guess starts being read as a measurement.

    When a cap applies we keep the *latest* signals: a question about a plan that just
    changed is answered by recent evidence, and the opening minutes of the shift are the
    part safe to leave out.
    """
    observed: list[Fact] = []
    inferred: list[Fact] = []
    index = start_index
    signals = sorted(episode.component_signals, key=lambda s: float(s.at_min))
    if max_signals is not None:
        # ``signals[-0:]`` is the whole list, not an empty one.  Spell the zero case out.
        signals = signals[-max_signals:] if max_signals > 0 else []
    derived_facts = list(episode.derived_facts)
    if max_derived is not None:
        derived_facts = derived_facts[:max_derived]
    for signal in signals:
        index += 1
        fact = Fact(
            fact_id=f"f{index}", kind="observation",
            text=f"{signal.signal_type} = {signal.value} {signal.unit}".strip(),
            evidence_mode=signal.evidence_mode.value, at_min=signal.at_min,
            source_refs=(signal.source_ref,))
        (observed if signal.evidence_mode is EvidenceMode.OBSERVED
         else inferred).append(fact)
    for derived in derived_facts:
        index += 1
        fact = Fact(
            fact_id=f"f{index}", kind="derived",
            text=f"{derived.key} = {derived.value} {derived.unit} ({derived.method})".strip(),
            evidence_mode=derived.evidence_mode.value,
            source_refs=tuple(derived.from_signals))
        (observed if derived.evidence_mode is EvidenceMode.OBSERVED
         else inferred).append(fact)
    return observed, inferred


def _plan_deltas(episode: CompositeEpisode) -> tuple[dict, ...]:
    """Old plan → new plan, read from the params the detector already validated.

    Nothing is recomputed here.  If the detector did not record a previous plan, there is
    no delta to show and the model is given none — an explanation with an invented
    "before" is worse than an explanation with no "before".
    """
    params = episode.presentation_params
    deltas: list[dict] = []
    pairs = (("previous_now", "new_now", "hiện tại"),
             ("previous_next", "new_next", "bước tiếp theo"))
    for before_key, after_key, slot in pairs:
        before, after = params.get(before_key), params.get(after_key)
        if before is None and after is None:
            continue
        if str(before) == str(after):
            continue
        deltas.append({"slot": slot, "from": before, "to": after})
    return tuple(deltas)


def _status_at(episode: CompositeEpisode, at_min: float | None) -> str:
    """Is this episode still live at the moment we are answering about it?

    Two things can end it and they are not the same thing: the lifecycle can have closed
    the episode (the wait ended), or its validity window can have run out while the
    lifecycle still says OPEN (the plan it showed went stale).  Both answer "no" to the
    driver's question, so both map to EXPIRED here; the lifecycle state stays in the pack
    separately for anyone who needs to tell them apart.
    """
    if episode.status is not EpisodeStatus.OPEN:
        return episode.status.value
    anchor = float(episode.last_updated_at if at_min is None else at_min)
    if episode.valid_until is not None and float(episode.valid_until) < anchor - 1e-6:
        return EpisodeStatus.EXPIRED.value
    return EpisodeStatus.OPEN.value


def build_why_factpack(episode: CompositeEpisode, *, at_min: float | None = None,
                       context: Iterable[CompositeEpisode] = ()) -> FactPack:
    """Pack for "why did my plan change", built from one episode plus its context.

    ``context`` carries the other episodes live at the same moment.  They contribute their
    observations — a swap and a plan revision in the same shift are part of the same story
    — but never their actions: only the subject episode's plan may be described as the
    plan, or the answer would blend two different cards into one.
    """
    rendered = present(episode)
    observed, inferred = _facts_from(episode, 0, max_signals=MAX_WHY_FACTS)
    index = len(observed) + len(inferred)
    # Context episodes contribute a one-line outcome, never their raw signals.  They are
    # here to say "a swap also happened in this shift", which is one fact; replaying their
    # evidence would answer a question nobody asked and crowd out the subject's own.
    others = [other for other in context if other.episode_id != episode.episode_id]
    for other in sorted(others, key=lambda e: float(e.last_updated_at),
                        reverse=True)[:MAX_CONTEXT_FACTS]:
        index += 1
        observed.append(_outcome_fact(other, index))
    all_episodes = [episode, *others]
    return FactPack(
        task_type=TASK_WHY, schema_version=FACTPACK_SCHEMA_VERSION,
        episode_id=episode.episode_id, episode_type=episode.episode_type,
        run_id=episode.run_id, actor_id=int(episode.actor_id),
        driver_id=episode.driver_id,
        material_revision=int(episode.material_revision),
        at_min=float(episode.last_updated_at if at_min is None else at_min),
        data_mode=episode.data_mode.value, is_mock=bool(episode.is_mock),
        evidence_mode=episode.evidence_mode.value,
        lifecycle_state=episode.lifecycle_state,
        state_label=STATE_LABELS.get(episode.lifecycle_state, ""),
        current_action=_action_dict(episode.current_action),
        future_plan=tuple(_action_dict(step) for step in episode.future_plan),
        plan_deltas=_plan_deltas(episode),
        observed_facts=tuple(observed), inferred_facts=tuple(inferred),
        validated_numbers=_numbers_from(all_episodes),
        episode_refs=tuple(e.episode_id for e in all_episodes),
        source_refs=tuple(episode.source_checkpoint_refs
                          + episode.source_solver_report_refs),
        caveats=tuple(episode.caveats),
        template_baseline=str(rendered["body"]),
        status=_status_at(episode, at_min),
        valid_until=episode.valid_until,
        max_output_chars=MAX_WHY_CHARS)


def _review_baseline(episodes: list[CompositeEpisode]) -> tuple[str, tuple[str, ...]]:
    """The deterministic end-of-shift note, and the fallback when the model is rejected.

    One sentence per episode that actually happened, in time order.  It is deliberately
    plain: this is the floor the LLM has to beat in the evaluation, not a straw man.  Note
    what it cannot do — it cannot decide that three of seven episodes were the interesting
    ones, so it lists all of them.  That limitation is the actual case for a model here.
    """
    if not episodes:
        return "Ca này chưa ghi nhận diễn biến nào đáng lưu ý.", ()
    summary = f"Ca này ghi nhận {len(episodes)} diễn biến."
    points = tuple(_WS.sub(" ", present(episode)["body"]).strip()
                   for episode in episodes)
    return " ".join((summary, *points)), points


def build_review_factpack(episodes: Iterable[CompositeEpisode], *, at_min: float,
                          run_id: str = "", actor_id: int = 0, driver_id: str = "",
                          income_settled: bool = True) -> FactPack:
    """Pack for the end-of-shift review, built from the episodes that actually occurred.

    Ordering is by ``started_at`` then id so two runs of the same shift produce the same
    pack, and so the model reads the shift in the order the driver lived it.
    """
    ordered = sorted(episodes, key=lambda e: (float(e.started_at), e.episode_id))
    observed: list[Fact] = []
    inferred: list[Fact] = []
    index = 0
    # A review is about what the shift amounted to, so each episode contributes its outcome
    # and at most a couple of derived readings.  Raw component signals are deliberately
    # absent: eleven episodes' worth of them is the log dump, not the summary.
    outcomes: list[Fact] = []
    details: list[tuple[Fact, bool]] = []
    for episode in ordered:
        index += 1
        outcomes.append(_outcome_fact(episode, index))
        extra_observed, extra_inferred = _facts_from(
            episode, index, max_signals=0, max_derived=MAX_DERIVED_PER_EPISODE)
        index += len(extra_observed) + len(extra_inferred)
        details.extend((fact, True) for fact in extra_observed)
        details.extend((fact, False) for fact in extra_inferred)
    # Outcomes come first when the cap bites: dropping "this episode happened" to keep one
    # of its derived readings would leave the model describing a detail of something the
    # pack never says occurred.
    observed.extend(outcomes[:MAX_REVIEW_FACTS])
    room = MAX_REVIEW_FACTS - len(observed)
    for fact, is_observed in details[:max(0, room)]:
        (observed if is_observed else inferred).append(fact)
    first = ordered[0] if ordered else None
    baseline_text, baseline_points = _review_baseline(ordered)
    historical: dict[str, dict] = {}
    for episode in ordered:
        for action in (episode.current_action, *episode.future_plan):
            entry = _action_dict(action)
            if entry is not None and entry["code"] not in historical:
                historical[entry["code"]] = {
                    "code": entry["code"], "label": entry["label"],
                    "seen_at_min": round(float(episode.last_updated_at), 1)}
    return FactPack(
        task_type=TASK_REVIEW, schema_version=FACTPACK_SCHEMA_VERSION,
        episode_id=f"shift-{run_id or (first.run_id if first else '')}-{int(actor_id)}",
        episode_type="shift_review",
        run_id=run_id or (first.run_id if first else ""),
        actor_id=int(actor_id), driver_id=driver_id,
        material_revision=sum(int(e.material_revision) for e in ordered),
        at_min=float(at_min),
        data_mode=(first.data_mode.value if first else "SIMULATED"),
        is_mock=bool(first.is_mock) if first else True,
        evidence_mode=("INFERRED" if any(
            e.evidence_mode is EvidenceMode.INFERRED for e in ordered) else "OBSERVED"),
        lifecycle_state="SUMMARISED", state_label=STATE_LABELS["SUMMARISED"],
        current_action=None, future_plan=(), plan_deltas=(),
        observed_facts=tuple(observed), inferred_facts=tuple(inferred),
        validated_numbers=_numbers_from(ordered),
        episode_refs=tuple(e.episode_id for e in ordered),
        source_refs=(),
        caveats=tuple(dict.fromkeys(
            caveat for episode in ordered for caveat in episode.caveats)),
        template_baseline=baseline_text, baseline_points=baseline_points,
        historical_actions=tuple(historical[code] for code in sorted(historical)),
        max_output_chars=MAX_REVIEW_CHARS,
        income_settled=bool(income_settled))
