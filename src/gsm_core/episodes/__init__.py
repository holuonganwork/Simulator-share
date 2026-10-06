"""Composite episode layer: derived driver-journey state shared by every capability."""

from gsm_core.episodes.contract import (
    Authority,
    CompositeEpisode,
    ComponentSignal,
    DataMode,
    DerivedFact,
    EpisodeContractError,
    EpisodeNumber,
    EpisodeStatus,
    EvidenceMode,
    InterruptionClass,
    SCHEMA_VERSION,
    Surface,
    validate_episode,
)
from gsm_core.episodes.engine import EpisodeRunResult, run_episodes, surface_bundle
from gsm_core.episodes.view import ActorRunView

# The four capabilities that are not driver-facing are deliberately NOT re-exported here.
# Importing ``gsm_core.episodes`` should not put a relocation renderer or a tenure profile
# one attribute away from a caller who was only reaching for the episode engine.  They are
# reachable by their full module path, which makes any use of them visible in a diff.

__all__ = [
    "ActorRunView", "Authority", "CompositeEpisode", "ComponentSignal", "DataMode",
    "DerivedFact", "EpisodeContractError", "EpisodeNumber", "EpisodeRunResult",
    "EpisodeStatus", "EvidenceMode", "InterruptionClass", "SCHEMA_VERSION", "Surface",
    "run_episodes", "surface_bundle", "validate_episode",
]
