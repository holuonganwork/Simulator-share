"""Primary arbitration — which single episode owns the driver's attention right now.

The policy is **lexicographic, not weighted**.  Each key is compared only when every key
before it ties, so the ordering can be audited one line at a time and a test can pin a
single rule without re-tuning a score.  A weighted score would let a large enough
confidence gap outrank a safety class, which is exactly the failure this ordering exists
to make impossible.

No randomness, no model call, no learned weights: the same set of active episodes always
yields the same primary.
"""

from __future__ import annotations

from gsm_core.episodes.contract import (
    Authority,
    CompositeEpisode,
    EvidenceMode,
    InterruptionClass,
    Surface,
)

# Lower is stronger.  Energy and rest are safety-adjacent and are placed above every
# money objective on purpose: a bonus target must never push a battery warning aside.
SAFETY_CLASS: dict[str, int] = {
    "energy_pressure_building": 0,
    "waiting_and_rest_record": 1,
    "energy_episode_recovered": 2,
    "plan_stability": 3,
    "plan_slipping_not_driver_failing": 4,
    "stop_chasing_infeasible_target": 5,
    "income_quality_not_amount": 6,
    # Operational context sits last: it explains what already happened and never competes
    # with a live constraint for the driver's attention.
    "operational_phase_change": 7,
}

# Money-shaped episode types, kept explicit so the override rule below is checkable.
MONEY_TYPES = frozenset({"stop_chasing_infeasible_target", "income_quality_not_amount"})
SAFETY_TYPES = frozenset({"energy_pressure_building", "waiting_and_rest_record"})

AUTHORITY_RANK: dict[Authority, int] = {
    Authority.SOLVER: 0,
    Authority.POLICY: 1,
    Authority.OBSERVATION: 2,
    Authority.DERIVED_RULE: 3,
}

INTERRUPTION_COST: dict[InterruptionClass, int] = {
    InterruptionClass.INTERRUPTIVE: 3,
    InterruptionClass.PERSISTENT: 1,
    InterruptionClass.PASSIVE: 0,
    InterruptionClass.ON_DEMAND: 0,
    InterruptionClass.DEFERRED: 0,
}


def priority_tuple(episode: CompositeEpisode) -> tuple:
    """The audit-friendly ordering key.  Smaller sorts first.

    Order of keys, and why each one is where it is:

    1. ``safety_class`` — energy and rest above money, unconditionally.
    2. ``authority`` — *within* a safety class, a solver verdict outranks our own reading
       of the same data.

    Note the deviation from the obvious "authority first" ordering.  Authority first
    breaks the product rule it is supposed to serve: the S1 infeasible-bonus verdict
    carries ``SOLVER`` authority while the rest-versus-idle episode is a ``DERIVED_RULE``,
    so authority-first would let a bonus card outrank a rest signal — exactly the
    override the rule forbids.  Safety class first keeps both intents intact.

    3. ``actionable`` — an episode with a canonical action to prepare for beats a report.
    4. ``interruption_cost`` inverted — among equals, prefer the one already interrupting,
       so the driver sees a decision rather than two half-cards.
    5. ``confidence`` — higher first.
    6. ``evidence_mode`` — observed before inferred.
    7. ``detected_at`` then ``episode_id`` — a total order, so ties never depend on
       dictionary iteration.
    """
    actionable = 0 if (episode.current_action is not None
                       and episode.recommended_surface is Surface.NUDGE) else 1
    return (
        SAFETY_CLASS.get(episode.episode_type, 8),
        AUTHORITY_RANK.get(episode.authority, 9),
        actionable,
        -INTERRUPTION_COST.get(episode.interruption_class, 0),
        -round(float(episode.confidence), 4),
        0 if episode.evidence_mode is EvidenceMode.OBSERVED else 1,
        round(float(episode.detected_at), 6),
        episode.episode_id,
    )


def select_primary(episodes: list[CompositeEpisode]) -> CompositeEpisode | None:
    """The single episode allowed to hold the primary slot.

    Only ``INTERRUPTIVE`` episodes compete: a passive insight or an explanation never
    takes the slot, however confident it is.
    """
    competing = [e for e in episodes
                 if e.interruption_class is InterruptionClass.INTERRUPTIVE]
    if not competing:
        return None
    return min(competing, key=priority_tuple)


def order_for_display(episodes: list[CompositeEpisode]) -> list[CompositeEpisode]:
    """Full deterministic ordering for a surface that shows several episodes."""
    return sorted(episodes, key=priority_tuple)


def safety_is_preserved(episodes: list[CompositeEpisode]) -> bool:
    """True when no money episode outranks a live safety episode.

    Used as an executable statement of the rule rather than a comment about it.
    """
    ordered = order_for_display(episodes)
    seen_money = False
    for episode in ordered:
        if episode.episode_type in MONEY_TYPES:
            seen_money = True
        elif episode.episode_type in SAFETY_TYPES and seen_money:
            return False
    return True
