"""Episode engine — run every detector over one actor-run and route the results.

The engine is a pure function of the view.  It draws no randomness, calls no solver, and
touches no simulator state, so switching it on cannot change what the drivers in a run
actually did.  ``tests/test_episode_non_interference.py`` asserts that property against a
real run rather than trusting this docstring.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from gsm_core.episodes.arbitration import order_for_display, select_primary
from gsm_core.episodes.contract import (
    CompositeEpisode,
    EpisodeContractError,
    Surface,
)
from gsm_core.episodes.detectors import DETECTORS
from gsm_core.episodes.routing import (
    FunnelCounts,
    RoutedEpisode,
    apply_funnel,
    needs_checkpoint,
)
from gsm_core.episodes.view import ActorRunView


@dataclass
class EpisodeRunResult:
    actor_id: int
    driver_id: str
    seed: int
    run_id: str
    episodes: list[CompositeEpisode] = field(default_factory=list)
    routed: list[RoutedEpisode] = field(default_factory=list)
    counts: FunnelCounts = field(default_factory=FunnelCounts)
    contract_errors: list[str] = field(default_factory=list)

    @property
    def shown(self) -> list[RoutedEpisode]:
        return [row for row in self.routed if row.shown]

    def shown_at(self, at_min: float) -> list[CompositeEpisode]:
        """Episodes a driver could have on screen at ``at_min``.

        Validity is respected: an episode whose window closed is gone, and one detected
        later has not happened yet.  This is what the replay reads at each cursor step.
        """
        live: dict[str, CompositeEpisode] = {}
        for row in sorted(self.shown, key=lambda r: (float(r.shown_at or 0.0),
                                                     r.episode.episode_id)):
            episode = row.episode
            if float(row.shown_at) > float(at_min) + 1e-6:
                continue
            if episode.status.value == "EXPIRED":
                live.pop(episode.episode_id, None)
                continue
            if (episode.valid_until is not None
                    and float(episode.valid_until) < float(at_min) - 1e-6):
                live.pop(episode.episode_id, None)
                continue
            live[episode.episode_id] = episode
        return order_for_display(list(live.values()))

    def primary_at(self, at_min: float) -> CompositeEpisode | None:
        return select_primary(self.shown_at(at_min))

    def to_dict(self) -> dict:
        return {
            "actor_id": self.actor_id, "driver_id": self.driver_id,
            "seed": self.seed, "run_id": self.run_id,
            "counts": self.counts.to_dict(),
            "contract_errors": list(self.contract_errors),
            "episodes": [
                {**row.episode.to_dict(), "routing": {
                    "verdict": row.verdict,
                    "shown_at_min": row.shown_at,
                    "queued_from_min": row.queued_from,
                    "queue_delay_min": row.queue_delay_min,
                    "is_update_in_place": row.is_update_in_place,
                    "needs_checkpoint": needs_checkpoint(row.episode),
                }} for row in self.routed
            ],
        }


def detect_episodes(view: ActorRunView, *, enabled: set[str] | None = None
                    ) -> tuple[list[CompositeEpisode], list[str]]:
    """Run detectors in a fixed name order and collect contract failures explicitly.

    A detector that produces an invalid episode is reported, not silently skipped: a
    swallowed contract error would look exactly like "this situation never happens".
    """
    episodes: list[CompositeEpisode] = []
    errors: list[str] = []
    for name in sorted(DETECTORS):
        if enabled is not None and name not in enabled:
            continue
        try:
            episodes.extend(DETECTORS[name](view))
        except EpisodeContractError as exc:
            errors.append(f"{name}: {exc}")
    return episodes, errors


def run_episodes(view: ActorRunView, *, enabled: set[str] | None = None,
                 queue_horizon_min: float | None = None) -> EpisodeRunResult:
    episodes, errors = detect_episodes(view, enabled=enabled)
    routed, counts = apply_funnel(
        view, episodes, queue_horizon_min=queue_horizon_min)
    counts.contract_rejected = len(errors)
    return EpisodeRunResult(
        actor_id=view.actor_id, driver_id=view.driver_id, seed=view.seed,
        run_id=view.run_id, episodes=episodes, routed=routed, counts=counts,
        contract_errors=errors)


def presented_bundle(result: EpisodeRunResult, at_min: float) -> dict:
    """Everything the client should draw at ``at_min``, already rendered by template.

    Rendering happens here rather than in the browser so the copy, the caveats and the
    provenance badges are produced once, deterministically, on the server that owns the
    facts.  The client places them; it does not compose them.
    """
    from gsm_core.episodes.templates import present  # local: keeps engine import-light

    live = result.shown_at(at_min)
    primary = select_primary(live)
    by_surface: dict[str, list[dict]] = {}
    for episode in live:
        if primary is not None and episode.episode_id == primary.episode_id:
            continue
        by_surface.setdefault(episode.recommended_surface.value, []).append(
            present(episode))
    return {
        "at_min": round(float(at_min), 3),
        "primary_nudge": present(primary) if primary is not None else None,
        "surfaces": by_surface,
        "counts": {"live": len(live),
                    "interruptions_so_far": result.counts.interruptions},
    }


def surface_bundle(result: EpisodeRunResult, at_min: float) -> dict[str, list[dict]]:
    """Group the live episodes at ``at_min`` by surface, ready for the UI contract.

    ``RECAP`` is only released at the end of the shift, and the primary nudge is lifted
    out so the client cannot accidentally render two of them.
    """
    live = result.shown_at(at_min)
    primary = select_primary(live)
    bundle: dict[str, list[dict]] = {surface.value: [] for surface in Surface}
    for episode in live:
        if primary is not None and episode.episode_id == primary.episode_id:
            continue
        bundle[episode.recommended_surface.value].append(episode.to_dict())
    return {
        "primary_nudge": [primary.to_dict()] if primary is not None else [],
        **bundle,
    }
