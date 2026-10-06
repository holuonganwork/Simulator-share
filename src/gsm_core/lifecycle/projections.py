"""Projections: one rule for interpreting the advice lifecycle, shared by the interface and
the simulation.

The approval condition was that both read one projection, one rule and one database, so every
function here is a pure function over an iterable of dictionaries. The interface and the pipeline
feed it database rows; the simulation feeds it events converted in memory, without touching the
database inside the tick loop.

A projection is not a source of truth: deleting it and replaying from the events must reproduce it
exactly, and tests and mutations guard that. Processing order is deterministic, sorted by time and
event id and deduplicated by event id with the first occurrence winning, which matches the store's
own insert semantics.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from gsm_core.lifecycle.advice_topics import classify

# Terminal states. A later non-terminal event cannot demote one, while a later terminal event
# may replace an earlier terminal one, as when a followed decision is subsequently superseded.
_TERMINAL = {"followed", "dismissed", "expired", "superseded", "suppressed"}


def _ts(iso: str) -> datetime:
    """Sort by real time rather than by string. Three origins write three time
    zones, and a lexicographic comparison can order a later instant before an earlier one.

    A second rail: mixing timestamps with and without an offset makes sorting raise. The schema
    blocks that on write, but projections also accept in-memory lists that never passed through the
    store, so this fails loudly here with a traceable message."""
    t = datetime.fromisoformat(iso)
    if t.tzinfo is None:
        raise ValueError(
            f"occurred_at '{iso}' thiếu offset múi giờ — mọi timestamp lifecycle phải "
            f"timezone-aware (trộn naive/aware làm chết toàn bộ projection)")
    return t


def _ordered(events) -> list[dict]:
    """Order by occurrence, then by observation, then by event id.

    The product path's occurrence time comes from a client-supplied value that is constant per card
    kind, so every action on one card in one day ties, and the tie-break decided everything. The old
    tie-break compared event ids as strings, and the action names happened to sort so that
    compliance always came last and therefore always won, whatever the driver pressed last: the
    product could not record a change of mind.

    The observation time is when the server received the event, which is the true causal order, and
    it is present on every event from both paths. The event id remains as the final tie-break so the
    order stays deterministic.
    """
    seen: set[str] = set()
    out = []
    for e in sorted(events, key=lambda e: (_ts(e["occurred_at"]),
                                           str(e.get("observed_at") or ""),
                                           e["event_id"])):
        if e["event_id"] in seen:
            continue
        seen.add(e["event_id"])
        out.append(e)
    return out


def decision_state(events) -> dict[str, dict]:
    """A state machine per decision: decided, then displayed, then one of
    followed, dismissed, expired or superseded. Suppression is the system's own branch off decided.

    Returns a record per decision id. Late or duplicate events cannot corrupt the state, so replay
    is idempotent.
    """
    st: dict[str, dict] = {}
    for e in _ordered(events):
        did = e["decision_id"]
        row = st.setdefault(did, {
            "state": None, "displayed": False, "driver_id": e["driver_id"],
            "run_id": e.get("run_id"), "topic": (e.get("payload") or {}).get("topic"),
            "reason_code": None, "first_at": e["occurred_at"], "last_at": e["occurred_at"],
        })
        row["last_at"] = e["occurred_at"]
        topic = (e.get("payload") or {}).get("topic")
        if row["topic"] is None and topic is not None:
            row["topic"] = topic
        et = e["event_type"]
        if et == "displayed":
            row["displayed"] = True
            if row["state"] not in _TERMINAL:
                row["state"] = "displayed"
        elif et == "decided":
            if row["state"] is None:
                row["state"] = "decided"
        else:  # terminal
            row["state"] = et
            if e.get("reason_code") is not None:
                row["reason_code"] = e["reason_code"]
    return st


def adherence_view(events) -> dict[tuple[str | None, str, str | None], dict]:
    """Counts keyed by run, driver and topic, with the denominator being
    decisions issued.

    The run is part of the key because the simulation derives driver ids from actor ids: actor zero
    of one run and actor zero of another are two different people in two universes. Measured,
    pooling two runs collapsed seventy keys into fifty-five, cross-contaminating fifteen drivers,
    which is exactly the paired use case this layer exists to serve. The interface and pipeline
    paths have no run id, so their keys remain stable.

    Two units, two names. The same data yields different figures depending on what is counted: per
    decision, a channel that fires every couple of minutes but whose decisions are bucketed at half
    an hour reads far higher than per event. The view therefore returns both, fully named, and a
    bare adherence key is forbidden, with a test enforcing it: a number whose unit is unclear is a
    number that will be misread.

    Per decision, each decision id counts once and its outcome is its final state, with decisions
    issued as the denominator. Per event, each time the advisor spoke and each compliance counts.
    Both rates are reported, and a zero denominator yields no value rather than a fabricated zero.

    Soft advice does not appear here at all. Topics registered as soft, such as weather, rest
    suggestions and traffic, are excluded entirely: not returned as null but absent from the keys.
    Measuring compliance with health advice would turn health into an optimisation target, which the
    product boundary forbids.

    Why absent rather than null: null is the signal for a zero denominator, which is what previous
    investigations used to find broken instruments. Returning null for soft advice would blend it
    into that signal, and a later reader would set about fixing a boundary that is working
    correctly.
    """
    # The events are iterated twice, and a generator would be exhausted by the first pass,
    # silently yielding a zero event count that reads as "no data". Materialised once, so the
    # iterable contract still holds.
    events = list(events)
    view: dict[tuple[str | None, str, str | None], dict] = {}

    def _row(key):
        return view.setdefault(key, {
            "decided": 0, "followed": 0, "dismissed": 0, "suppressed": 0,
            "event_decided": 0, "event_followed": 0,
        })

    states = decision_state(events)
    # Which decisions count as soft advice, used by both loops below.
    #
    # Why it is anchored to the decision rather than to each event's own topic: the simulation
    # bridge sets a topic only where the detail carries a channel, so an event belonging to the
    # same decision may have none, and a per-event test would let it through. Anchoring to the
    # decision makes the boundary independent of whether every producer remembered to fill a field.
    #
    # Every event is scanned, not just the topic the decision resolver settled on. That resolver
    # takes the topic of the first event that has one and the state of the last, so a decision
    # whose first event carries a measured topic and whose later event carries a soft one resolves
    # to the measured topic. Checking only the resolved value let a soft compliance into a measured
    # topic's numerator, which measured as a perfect adherence. The event loop already checked both
    # directions while the decision loop did not, so this layer's claim to be an independent second
    # check was not true in that direction.
    #
    # A declared trade: a decision carrying both a soft and a measured topic is a producer error,
    # and no path today can create one. When it happens, the whole decision is dropped rather than
    # keeping the measured part, which can cost a legitimate denominator entry. That is deliberate,
    # because the two errors are not equivalent: losing a denominator entry costs precision, while
    # letting a soft compliance into the numerator breaks a settled boundary.
    #
    # The exclusion criterion is "not measured" rather than "soft". An earlier version excluded only
    # soft topics, so an unregistered topic went straight into the measured group. Measured against
    # a dirty development store, dozens of soft events were correctly excluded while an unregistered
    # key still entered the view: the read layer failing open where it had been declared to fail
    # closed. The write-side rejection only blocks new unknown topics arriving through one client,
    # not existing records, the simulation path, or the pipeline.
    #
    # Why failing closed is the right direction: an unknown topic may be soft advice that someone
    # forgot to register, and measuring it would create exactly the compliance metric the boundary
    # forbids.
    #
    # An absent topic is still measured, and that is not an oversight. Absent means a producer with
    # no notion of topics at all, which is the simulation path; unknown means a producer that named
    # something nobody has classified, which signals that someone has just added something. Merging
    # the two would discard valid simulation data.
    #
    # The exclusion is never silent: the drop record below counts it, and the metrics layer suspends
    # the result when an unknown topic appears, under the principle that an incomplete denominator
    # makes every difference suspect.
    _soft_dids = {did for did, r in states.items() if classify(r["topic"]) != "measured"}
    _soft_dids |= {e["decision_id"] for e in events
                   if classify((e.get("payload") or {}).get("topic")) != "measured"}

    for did, row in states.items():
        if did in _soft_dids:
            continue          # soft advice has no denominator, by design
        agg = _row((row["run_id"], row["driver_id"], row["topic"]))
        if row["state"] == "suppressed":
            # Suppressed means the advisor did not speak, so it does not belong in a
            # denominator of times spoken against times heeded. Counted separately, to know
            # how much the cadence blocks.
            agg["suppressed"] += 1
            continue
        agg["decided"] += 1
        if row["state"] in ("followed", "dismissed"):
            agg[row["state"]] += 1
    # "The advisor spoke" has two names: the simulation records a decision while the product
    # records a display. A denominator accepting only the first left the product path permanently
    # at zero, so its event-level adherence was undefined, half of a two-path instrument dying
    # silently; and worse, its numerator could exceed its denominator, an impossible state no gate
    # caught. Displays are therefore folded into the event denominator.
    # They are deliberately not folded into the decision denominator, which is a different unit and
    # already handles displays through the terminal-state logic.
    _EVENT_SPOKEN = ("decided", "displayed")
    for e in _ordered(events):
        et = e["event_type"]
        if et not in (*_EVENT_SPOKEN, "followed"):
            continue
        topic = (e.get("payload") or {}).get("topic")
        # Both loops must filter: the decision loop and the event loop are two separate ways into
        # the view, and filtering one leaves the other open, the same failure as fixing one of two
        # names for "the advisor spoke" and believing the job done.
        #
        # The condition here is membership alone, without a second per-topic test, even though an
        # earlier version had both. The second was dropped not for brevity but because it was
        # redundant and had deceived the test of the first: the membership set gathers topics from
        # the states and from every event, and the resolver creates a row for every decision id, so
        # an event with a non-measured topic always implies membership. With a mutation injected
        # into one clause, the other still filtered, the gate stayed green, and it nearly read as
        # "the gate does not fire". Two redundant conditions shielding each other make a mutation
        # test lie. One point of enforcement, one point to cut.
        if e["decision_id"] in _soft_dids:
            continue
        agg = _row((e.get("run_id"), e["driver_id"], topic))
        agg["event_decided" if et in _EVENT_SPOKEN else "event_followed"] += 1
    for agg in view.values():
        agg["decision_adherence"] = (agg["followed"] / agg["decided"]
                                     if agg["decided"] else None)
        agg["event_adherence"] = (agg["event_followed"] / agg["event_decided"]
                                  if agg["event_decided"] else None)
    return view


def adherence_drops(events) -> dict:
    """Count the decisions the adherence view excluded, and why.

    Why it is needed: the view excludes decisions by skipping them, so the exclusion leaves no
    trace, and the gate watching it inspects only keys that are present and cannot see an absent
    one. If a producer ever emits an unregistered or mixed topic, the denominator falls with nobody
    knowing, and every difference computed on it still looks normal.

    That is the shape of a defect that survived dozens of artifacts because no mechanism complained.
    This function exists so the exclusion can speak.

    It returns, and further keys may be added without breaking these:
      soft            decisions excluded for a soft topic. Normal: the boundary working.
      unknown         decisions excluded for an unregistered topic. Abnormal: suspend the result.
      mixed           decisions carrying both a measured and an excluded topic: a producer error.
      topics_unknown  the unregistered topic names, so whoever fixes it knows what to register.

    A separate function rather than folding this into the view: that view has real consumers, and
    changing the shape of its return value would break a working contract. Adding a function adds
    without breaking anything.
    """
    events = list(events)
    states = decision_state(events)

    # Topics per decision: everything the events sharing a decision id carry.
    topics_theo_did: dict[str, set] = {}
    for e in events:
        topics_theo_did.setdefault(e["decision_id"], set()).add(
            (e.get("payload") or {}).get("topic"))
    for did, r in states.items():
        topics_theo_did.setdefault(did, set()).add(r["topic"])

    dem = {"soft": 0, "unknown": 0, "mixed": 0}
    ten_unknown: set[str] = set()
    for did in states:
        cls = {classify(t) for t in topics_theo_did.get(did, {None})}
        if cls <= {"measured"}:
            continue
        if "measured" in cls:
            dem["mixed"] += 1          # mixed: the whole decision is dropped, a declared trade
        elif "unknown" in cls:
            dem["unknown"] += 1
        else:
            dem["soft"] += 1
        ten_unknown |= {str(t) for t in topics_theo_did.get(did, set())
                        if classify(t) == "unknown"}
    dem["topics_unknown"] = sorted(ten_unknown)
    return dem


# ---------- sim adapter: events RAM → lifecycle envelope ----------

# Simulation event kinds mapped to the lifecycle events one of them produces.
#
# Why the mapping is not one-to-one by kind, as an adversarial review measured, wrongly on every
# channel: compliance is not carried by the kind but by a flag in the detail, and each channel logs
# by a different convention.
#
# One kind always logs and carries the flag, while its paired followed event is written only when
# the advice actually changed the action, so "complied, but the advice matched the instinct" leaves
# no event at all. Another carries the flag with no separate followed event. Two more were only
# ever logged once the action had been taken, so their mere existence meant compliance. And the
# positioning kind exists only for compliers, its denominator living in the assignment event.
#
# Mapping by kind produced adherence figures of a few percent, zero, zero and a hundred percent,
# against true values around a half, a half, one and a half.
#
# The always-followed set is now empty. It once held the two kinds whose existence implied
# compliance, and that reasoning was true of the code at the time, but it pinned those channels'
# adherence to exactly two possible values. This layer ignored the compliance flag entirely, so
# fixing the bridge and the world without fixing this would have left the figure at a hundred
# percent regardless.
#
# Measured over three seeds, one channel reported a perfect rate against a true rate of about a
# half. Both channels now write events carrying the flag on both branches, so they are read like
# the others.
#
# The name is kept and left empty rather than deleted: if some kind genuinely only ever exists for
# compliers, this is where it must be declared, with measured evidence, rather than added quietly.
_ALWAYS_FOLLOWED: set[str] = set()
_FOLLOW_FLAG_KINDS = {"advice_given", "advice_bonus_gate",
                      "advice_shift_extend", "advice_rest_window"}
_DECIDED_KINDS = _ALWAYS_FOLLOWED | _FOLLOW_FLAG_KINDS
# One followed marker is deliberately not mapped: it is written only when the advice changed the
# action, and it always accompanies a given event in the same tick already carrying the compliance
# flag, so mapping both would count one compliance twice, measured as a few percentage points on
# the event-level rate.
#
# The positioning channel's followed marker was once mapped and no longer is. It evidences
# execution, meaning the actor genuinely moved, and execution is compliance and feasibility
# together. The cases of compliance without execution are real code paths: already standing in the
# right cell, busy until the end of the shift, or the instinct choosing something other than
# waiting under the conservative mode. Counting those as non-compliance biased adherence by a
# couple of percentage points against the null, enough to suspend the statistical gate at a hundred
# seeds.
#
# Compliance now derives from the draw outcomes recorded in the assignment event, which is the same
# source of truth as the draw itself, and the execution rate became a separate metric. They are two
# different questions: did the driver agree, and having agreed, could they act.
_TERMINAL_ONLY = {
    "advice_suppressed": ("suppressed", "system"),
}
# Kinds that evidence execution: they stay out of the adherence numerator and feed the
# execution rate instead.
EXECUTION_KINDS = {"standby_followed"}


def _sim_steps(kind: str, detail: dict) -> list[tuple[str, str]]:
    """The lifecycle steps one simulation event produces: none, one or two."""
    if kind in _DECIDED_KINDS:
        steps = [("decided", "advisor")]
        if kind in _ALWAYS_FOLLOWED or detail.get("followed"):
            steps.append(("followed", "driver"))
        return steps
    step = _TERMINAL_ONLY.get(kind)
    return [step] if step else []

# A default epoch for callers that do not supply the run's real date. It exists only to encode
# minutes as comparable timestamps within one run; the real date lives in the run's config.
_SIM_EPOCH = "2026-01-01T00:00:00+07:00"


def _iso_from_t_min(t_min: float, epoch_iso: str) -> str:
    t0 = datetime.fromisoformat(epoch_iso)
    return (t0 + timedelta(minutes=float(t_min))).isoformat()


def _offer_events(e, run_id: str, epoch_iso: str) -> list[dict]:
    """An assignment event carrying assigned ids produces a decision event
    for each person assigned, including those who then do not comply.

    Without this step the positioning channel's denominator would contain only compliers and its
    adherence would always be a hundred percent, as it was before the fix."""
    detail = e.detail or {}
    ids = detail.get("assigned_ids") or []
    coin_true = set(detail.get("coin_follow_ids") or [])
    iso = _iso_from_t_min(e.t_min, epoch_iso)
    out = []
    for aid in ids:
        did = detail.get("decision_ids", {}).get(str(aid))
        if not did:
            # A silent drop here would reopen the missing-denominator hole from the
            # producer's side, so this fails loudly.
            raise ValueError(
                f"standby_alloc t={e.t_min}: actor {aid} có trong assigned_ids nhưng "
                f"không có decision_id (decision_ids có: "
                f"{sorted(detail.get('decision_ids', {}))}) — producer lệch, mẫu số "
                f"positioning sẽ hụt im lặng nếu bỏ qua")
        out.append({
            "event_id": f"sim-{run_id}:{aid}:standby_offer:{e.t_min:g}",
            "decision_id": did, "display_id": None, "driver_id": str(aid),
            "run_id": run_id, "event_type": "decided", "reason_code": None,
            "occurred_at": iso, "observed_at": iso, "actor": "advisor",
            "origin": "sim", "source": "MOCK", "context_revision": None,
            "payload": {"topic": "positioning", "t_min": e.t_min,
                        "sim_kind": "standby_alloc", "target_cell": e.cell},
            "schema_version": "1.0.0",
        })
        if aid in coin_true:
            # The compliance draw's outcome at assignment time, independent of whether it
            # could be carried out.
            out.append({
                "event_id": f"sim-{run_id}:{aid}:standby_coin:{e.t_min:g}",
                "decision_id": did, "display_id": None, "driver_id": str(aid),
                "run_id": run_id, "event_type": "followed", "reason_code": "coin",
                "occurred_at": iso, "observed_at": iso, "actor": "driver",
                "origin": "sim", "source": "MOCK", "context_revision": None,
                "payload": {"topic": "positioning", "t_min": e.t_min,
                            "sim_kind": "standby_alloc_coin", "target_cell": e.cell},
                "schema_version": "1.0.0",
            })
    return out


def sim_events_to_lifecycle(sim_events, run_id: str | None = None,
                            epoch_iso: str = _SIM_EPOCH) -> list[dict]:
    """Map in-memory simulation events onto schema-valid lifecycle events.

    The run id comes from the event itself, every event being stamped with one; the parameter is
    only a fallback for events built by hand. A parameter contradicting the event raises: before
    this, the function wrote the run id column from the parameter while the decision id embedded
    the real one, so a self-contradicting record still passed the schema. That is a real risk in
    multi-day runs, where each day has its own run id but a caller may easily pool them and pass
    one value.

    Purely deterministic in its input, with no identifiers or wall-clock reads, so an exact repeat
    of a simulation is preserved. Events colliding on their natural key are suffixed in order.
    """
    out: list[dict] = []
    used: dict[str, int] = {}
    for e in sim_events:
        detail = e.detail or {}
        ev_run = getattr(e, "run_id", "") or None
        if ev_run and run_id and ev_run != run_id:
            raise ValueError(
                f"run_id mâu thuẫn: Event mang '{ev_run}' nhưng caller truyền "
                f"'{run_id}' — bỏ tham số để dùng run_id của chính event")
        rid = ev_run or run_id
        if e.kind == "standby_alloc":
            out.extend(_offer_events(e, rid, epoch_iso))
            continue
        steps = _sim_steps(e.kind, detail)
        if not steps or "decision_id" not in detail:
            continue
        payload = {k: v for k, v in detail.items()
                   if k not in ("decision_id", "assigned_ids", "decision_ids")}
        payload["t_min"] = e.t_min
        payload["sim_kind"] = e.kind
        if "channel" in detail:
            payload["topic"] = detail["channel"]
        iso = _iso_from_t_min(e.t_min, epoch_iso)
        for event_type, actor in steps:
            base = f"sim-{rid}:{e.actor_id}:{e.kind}:{event_type}:{e.t_min:g}"
            n = used.get(base, 0)
            used[base] = n + 1
            out.append({
                "event_id": base if n == 0 else f"{base}-{n}",
                "decision_id": detail["decision_id"],
                "display_id": None,
                "driver_id": str(e.actor_id),
                "run_id": rid,
                "event_type": event_type,
                "reason_code": detail.get("reason"),
                "occurred_at": iso,
                "observed_at": iso,
                "actor": actor,
                "origin": "sim",
                "source": "MOCK",
                "context_revision": None,
                "payload": payload,
                "schema_version": "1.0.0",
            })
    return out
