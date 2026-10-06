"""Surface routing and the eligibility funnel.

An episode is a statement about the journey; a surface is a claim on the driver's
attention.  This module is the only place allowed to convert one into the other, and it
is deliberately a table plus a fixed sequence of gates rather than a score.  A table can
be read by a reviewer and asserted by a test; a weighted score cannot.

The funnel is also the measurement instrument.  ``FunnelCounts`` records why episodes
disappeared, so "how often would a driver actually see this" is a number rather than an
opinion — and the difference between *candidate*, *eligible* and *shown* stays visible
instead of collapsing into one headline.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from gsm_core.episodes.contract import (
    CompositeEpisode,
    EpisodeStatus,
    InterruptionClass,
    Surface,
)
from gsm_core.episodes.view import ActorRunView

# Which surfaces may be blocked by the driver moving.  A recap read at the end of a shift
# and an explanation the driver tapped for are not pushed at anyone, so they are exempt.
SAFETY_GATED_SURFACES = frozenset({Surface.NUDGE, Surface.PLAN_STRIP, Surface.BANNER,
                                   Surface.INSIGHT_DRAWER, Surface.PRIORITY_BOARD})

# A surface that pushes text needs breathing room; one that the driver pulls does not.
COOLDOWN_MIN: dict[Surface, float] = {
    Surface.NUDGE: 60.0,
    Surface.BANNER: 0.0,
    Surface.INSIGHT_DRAWER: 120.0,
    Surface.PRIORITY_BOARD: 120.0,
    Surface.PLAN_STRIP: 0.0,      # updates in place; a new value is not a new card
    Surface.TIMELINE: 0.0,
    Surface.RECAP: 0.0,
    Surface.WHY: 0.0,
    Surface.BRIEF: 0.0,
}

# How long the single primary slot stays occupied once a nudge takes it.
PRIMARY_SLOT_MIN = 20.0


@dataclass
class FunnelCounts:
    """Every stage of the funnel, counted separately and never merged."""

    candidate: int = 0
    contract_rejected: int = 0
    expired_before_display: int = 0
    deferred_while_moving: int = 0
    dropped_never_safe: int = 0
    duplicate_material: int = 0
    cooldown_suppressed: int = 0
    lost_primary_arbitration: int = 0
    deferred_for_cadence: int = 0
    deferred_for_primary: int = 0
    eligible: int = 0
    shown: int = 0
    by_type: dict[str, int] = field(default_factory=dict)
    shown_by_type: dict[str, int] = field(default_factory=dict)
    shown_by_surface: dict[str, int] = field(default_factory=dict)
    interruptions: int = 0
    update_in_place: int = 0
    # An episode that is a new *state of an existing card* rather than a new card.  Kept
    # separate from ``update_in_place`` (a new revision of an episode we raised ourselves)
    # because this one repaints a card minted by a solver, which is a different claim.
    superseded_existing_card: int = 0
    # Queue outcomes, split so "deferred" is never read as "delivered".
    expired_in_queue: int = 0
    material_changed_in_queue: int = 0
    # Per-capability drop ledger: type -> reason -> count.  §3.6 wants the funnel per
    # capability, and a single global total hides a capability that dies at one gate.
    drop_by_type: dict[str, dict[str, int]] = field(default_factory=dict)

    def note_drop(self, episode_type: str, reason: str) -> None:
        self.drop_by_type.setdefault(episode_type, {})
        self.drop_by_type[episode_type][reason] = (
            self.drop_by_type[episode_type].get(reason, 0) + 1)

    def merge(self, other: "FunnelCounts") -> None:
        self.superseded_existing_card += other.superseded_existing_card
        self.expired_in_queue += other.expired_in_queue
        self.material_changed_in_queue += other.material_changed_in_queue
        for key, reasons in other.drop_by_type.items():
            bucket = self.drop_by_type.setdefault(key, {})
            for reason, value in reasons.items():
                bucket[reason] = bucket.get(reason, 0) + value
        self.candidate += other.candidate
        self.contract_rejected += other.contract_rejected
        self.expired_before_display += other.expired_before_display
        self.deferred_while_moving += other.deferred_while_moving
        self.dropped_never_safe += other.dropped_never_safe
        self.duplicate_material += other.duplicate_material
        self.cooldown_suppressed += other.cooldown_suppressed
        self.lost_primary_arbitration += other.lost_primary_arbitration
        self.deferred_for_cadence += other.deferred_for_cadence
        self.deferred_for_primary += other.deferred_for_primary
        self.eligible += other.eligible
        self.shown += other.shown
        self.interruptions += other.interruptions
        self.update_in_place += other.update_in_place
        for key, value in other.by_type.items():
            self.by_type[key] = self.by_type.get(key, 0) + value
        for key, value in other.shown_by_type.items():
            self.shown_by_type[key] = self.shown_by_type.get(key, 0) + value
        for key, value in other.shown_by_surface.items():
            self.shown_by_surface[key] = self.shown_by_surface.get(key, 0) + value

    def to_dict(self) -> dict:
        return {
            "candidate": self.candidate,
            "contract_rejected": self.contract_rejected,
            "expired_before_display": self.expired_before_display,
            "deferred_while_moving": self.deferred_while_moving,
            "dropped_never_safe": self.dropped_never_safe,
            "duplicate_material": self.duplicate_material,
            "cooldown_suppressed": self.cooldown_suppressed,
            "lost_primary_arbitration": self.lost_primary_arbitration,
            "deferred_for_cadence": self.deferred_for_cadence,
            "deferred_for_primary": self.deferred_for_primary,
            "eligible": self.eligible,
            "shown": self.shown,
            "interruptions": self.interruptions,
            "update_in_place": self.update_in_place,
            "superseded_existing_card": self.superseded_existing_card,
            "expired_in_queue": self.expired_in_queue,
            "material_changed_in_queue": self.material_changed_in_queue,
            "by_type": dict(sorted(self.by_type.items())),
            "shown_by_type": dict(sorted(self.shown_by_type.items())),
            "shown_by_surface": dict(sorted(self.shown_by_surface.items())),
            "drop_by_type": {key: dict(sorted(value.items()))
                             for key, value in sorted(self.drop_by_type.items())},
        }


# The closed vocabulary an agent may use to answer "why am I not seeing a card?".
#
# It is a *mapping*, not a list, and the direction matters: the router speaks in verdict
# suffixes (``never_safe``) while the funnel counts in its own names
# (``dropped_never_safe``).  Pairing them here means the sentence a driver reads and the
# number an analyst reads cannot drift apart — and adding a drop branch without declaring it
# fails a test instead of silently producing an unexplainable silence.
SUPPRESSED_REASONS: dict[str, str] = {
    "expired": "expired_before_display",
    "expired_after_cadence": "expired_before_display",
    "expired_after_primary": "expired_before_display",
    "expired_in_queue": "expired_in_queue",
    "never_safe": "dropped_never_safe",
    "never_safe_after_cadence": "dropped_never_safe",
    "never_safe_after_primary": "dropped_never_safe",
    "duplicate_material": "duplicate_material",
}


@dataclass(frozen=True)
class RoutedEpisode:
    """An episode plus the outcome of routing it."""

    episode: CompositeEpisode
    shown_at: float | None
    verdict: str                # shown | queued_then_shown | dropped:<reason>
    queued_from: float | None = None
    is_update_in_place: bool = False

    @property
    def shown(self) -> bool:
        return self.shown_at is not None

    @property
    def queue_delay_min(self) -> float | None:
        if self.shown_at is None or self.queued_from is None:
            return None
        return round(float(self.shown_at) - float(self.queued_from), 3)


def needs_checkpoint(episode: CompositeEpisode) -> bool:
    """Only an interrupting card becomes an ``AdviceCheckpoint``.

    A strip that repaints, a timeline row, a priority-board status and a recap section
    are read straight from the episode projection.  Minting a checkpoint for them would
    inflate the checkpoint population with records that were never a decision to
    interrupt anybody — and would force a new surface value into a versioned contract
    (``advice_checkpoint.surface`` is ``brief|nudge|recap``) for no product gain.
    """
    return episode.recommended_surface is Surface.NUDGE


def _sort_key(episode: CompositeEpisode) -> tuple:
    """Deterministic processing order: time, then priority, then a stable id."""
    return (round(float(episode.detected_at), 6), int(episode.priority),
            episode.episode_type, episode.episode_id, int(episode.material_revision))


def apply_funnel(view: ActorRunView, episodes: list[CompositeEpisode], *,
                 queue_horizon_min: float | None = None
                 ) -> tuple[list[RoutedEpisode], FunnelCounts]:
    """Run every episode through the fixed gate sequence, in time order.

    Gate order is intentional and fail-closed: validity, then safety, then duplication,
    then cadence, then the single-primary rule.  Safety is checked before cadence so a
    card is never "spent" from the budget at a minute the driver could not read it.
    """
    counts = FunnelCounts()
    routed: list[RoutedEpisode] = []
    last_shown_by_surface: dict[Surface, float] = {}
    seen_material: dict[str, str] = {}          # episode_id -> material fingerprint
    last_shown_by_episode: dict[str, float] = {}
    primary_until: float | None = None

    def queue_horizon(episode: CompositeEpisode, from_min: float) -> float:
        """Wait only while the decision can still be useful.

        A caller may pass an explicit horizon for a bounded test/probe.  Product routing
        otherwise uses the episode deadline, or the known shift boundary for a persistent
        state.  The old fixed 45-minute horizon silently destroyed valid decisions on
        longer rides and could also outlive a short decision window.
        """
        deadline = (float(episode.valid_until)
                    if episode.valid_until is not None else float(view.shift_end_min))
        useful = max(0.0, deadline - float(from_min))
        if queue_horizon_min is None:
            return useful
        return min(useful, max(0.0, float(queue_horizon_min)))

    for episode in sorted(episodes, key=_sort_key):
        counts.candidate += 1
        counts.by_type[episode.episode_type] = counts.by_type.get(
            episode.episode_type, 0) + 1
        at = float(episode.detected_at)
        surface = episode.recommended_surface

        # --- validity ---------------------------------------------------------
        if episode.valid_until is not None and float(episode.valid_until) < at - 1e-6:
            routed.append(RoutedEpisode(episode, None, "dropped:expired"))
            counts.expired_before_display += 1
            counts.note_drop(episode.episode_type, "expired")
            continue

        # --- safety -----------------------------------------------------------
        shown_at = at
        queued_from: float | None = None
        if (episode.status is not EpisodeStatus.EXPIRED
                and surface in SAFETY_GATED_SURFACES and view.is_moving(at)):
            resume = view.next_safe_min(at, queue_horizon(episode, at))
            counts.deferred_while_moving += 1
            if resume is None:
                routed.append(RoutedEpisode(episode, None, "dropped:never_safe",
                                            queued_from=at))
                counts.dropped_never_safe += 1
                counts.note_drop(episode.episode_type, "never_safe")
                continue
            if (episode.valid_until is not None
                    and float(resume) > float(episode.valid_until) + 1e-6):
                routed.append(RoutedEpisode(episode, None, "dropped:expired_in_queue",
                                            queued_from=at))
                counts.expired_before_display += 1
                counts.expired_in_queue += 1
                counts.note_drop(episode.episode_type, "expired_in_queue")
                continue
            queued_from = at
            shown_at = float(resume)

        counts.eligible += 1

        # --- duplication / update in place -----------------------------------
        previous = seen_material.get(episode.episode_id)
        is_update = previous is not None
        if previous == episode.material_fingerprint:
            routed.append(RoutedEpisode(episode, None, "dropped:duplicate_material",
                                        queued_from=queued_from))
            counts.duplicate_material += 1
            counts.note_drop(episode.episode_type, "duplicate_material")
            continue
        if is_update and queued_from is not None:
            # The statement changed while the card was waiting for a safe minute.  It is
            # still shown — the newer statement is the true one — but it is counted, so a
            # long queue cannot quietly deliver stale text.
            counts.material_changed_in_queue += 1
        seen_material[episode.episode_id] = episode.material_fingerprint

        # --- new state of a card that already exists --------------------------
        # A7 is the case this exists for: the S1 verdict already has its own READY
        # checkpoint, so this episode repaints it.  Charging cadence or an interruption
        # for a repaint would spend the driver's attention budget on a card that is
        # already on their screen, and raising a second card would say the same thing
        # twice.  Cooldown and the primary slot are both skipped deliberately.
        if episode.supersedes_checkpoint_id:
            last_shown_by_episode[episode.episode_id] = shown_at
            routed.append(RoutedEpisode(episode, shown_at, "shown", queued_from, True))
            counts.shown += 1
            counts.update_in_place += 1
            counts.superseded_existing_card += 1
            counts.shown_by_type[episode.episode_type] = counts.shown_by_type.get(
                episode.episode_type, 0) + 1
            counts.shown_by_surface[surface.value] = counts.shown_by_surface.get(
                surface.value, 0) + 1
            continue

        # A new revision of an episode the driver is *already looking at* repaints that
        # card.  It is not a second interruption and must not be charged to cadence:
        # charging it would make the system go quiet exactly when the situation it is
        # describing is still developing.  A persistent strip is always on screen; a
        # nudge counts as on screen only while it still holds the primary slot.
        last_shown = last_shown_by_episode.get(episode.episode_id)
        still_on_screen = is_update and last_shown is not None and (
            episode.interruption_class is InterruptionClass.PERSISTENT
            or (episode.interruption_class is InterruptionClass.INTERRUPTIVE
                and shown_at < last_shown + PRIMARY_SLOT_MIN - 1e-6))
        if still_on_screen:
            last_shown_by_episode[episode.episode_id] = shown_at
            routed.append(RoutedEpisode(episode, shown_at, "shown", queued_from, True))
            counts.shown += 1
            counts.update_in_place += 1
            counts.shown_by_type[episode.episode_type] = counts.shown_by_type.get(
                episode.episode_type, 0) + 1
            counts.shown_by_surface[surface.value] = counts.shown_by_surface.get(
                surface.value, 0) + 1
            continue

        # --- cadence ----------------------------------------------------------
        # Cadence controls *when*, not whether, a distinct useful event is delivered.
        # The old implementation destroyed the later episode here.  Hold it until the
        # surface breathes, then re-check both safety and validity.
        cooldown = COOLDOWN_MIN.get(surface, 0.0)
        last = last_shown_by_surface.get(surface)
        if cooldown > 0 and last is not None and shown_at - last < cooldown - 1e-6:
            queued_from = at if queued_from is None else queued_from
            shown_at = float(last + cooldown)
            counts.deferred_for_cadence += 1
            if surface in SAFETY_GATED_SURFACES and view.is_moving(shown_at):
                resume = view.next_safe_min(shown_at, queue_horizon(episode, shown_at))
                if resume is None:
                    routed.append(RoutedEpisode(
                        episode, None, "dropped:never_safe_after_cadence", queued_from))
                    counts.cooldown_suppressed += 1
                    counts.dropped_never_safe += 1
                    counts.note_drop(episode.episode_type, "never_safe_after_cadence")
                    continue
                shown_at = float(resume)
            if (episode.valid_until is not None
                    and shown_at > float(episode.valid_until) + 1e-6):
                routed.append(RoutedEpisode(
                    episode, None, "dropped:expired_after_cadence", queued_from))
                counts.cooldown_suppressed += 1
                counts.expired_before_display += 1
                counts.expired_in_queue += 1
                counts.note_drop(episode.episode_type, "expired_after_cadence")
                continue

        # --- single primary ---------------------------------------------------
        if episode.interruption_class is InterruptionClass.INTERRUPTIVE:
            if primary_until is not None and shown_at < primary_until - 1e-6:
                queued_from = at if queued_from is None else queued_from
                shown_at = float(primary_until)
                counts.deferred_for_primary += 1
                if surface in SAFETY_GATED_SURFACES and view.is_moving(shown_at):
                    resume = view.next_safe_min(shown_at, queue_horizon(episode, shown_at))
                    if resume is None:
                        routed.append(RoutedEpisode(
                            episode, None, "dropped:never_safe_after_primary", queued_from))
                        counts.lost_primary_arbitration += 1
                        counts.dropped_never_safe += 1
                        counts.note_drop(episode.episode_type, "never_safe_after_primary")
                        continue
                    shown_at = float(resume)
                if (episode.valid_until is not None
                        and shown_at > float(episode.valid_until) + 1e-6):
                    routed.append(RoutedEpisode(
                        episode, None, "dropped:expired_after_primary", queued_from))
                    counts.lost_primary_arbitration += 1
                    counts.expired_before_display += 1
                    counts.expired_in_queue += 1
                    counts.note_drop(episode.episode_type, "expired_after_primary")
                    continue
            primary_until = shown_at + PRIMARY_SLOT_MIN
            counts.interruptions += 1

        last_shown_by_surface[surface] = shown_at
        last_shown_by_episode[episode.episode_id] = shown_at
        routed.append(RoutedEpisode(
            episode, shown_at, "queued_then_shown" if queued_from is not None else "shown",
            queued_from, is_update))
        counts.shown += 1
        if is_update:
            counts.update_in_place += 1
        counts.shown_by_type[episode.episode_type] = counts.shown_by_type.get(
            episode.episode_type, 0) + 1
        counts.shown_by_surface[surface.value] = counts.shown_by_surface.get(
            surface.value, 0) + 1
    return routed, counts


def with_session(episode: CompositeEpisode, session_id: str | None) -> CompositeEpisode:
    return replace(episode, session_id=session_id)
