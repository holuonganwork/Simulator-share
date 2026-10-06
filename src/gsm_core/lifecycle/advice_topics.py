"""Classifying advice topics: which are measured for compliance and which are not.

Why this is a boundary rather than an interface detail.

Measuring compliance with a health recommendation is turning health into a target to optimise.
Once a rest-adherence figure exists it will be seen as something to improve, and improving the
share of drivers who agree to rest is optimising over health, which the objective model forbids. A
"follow this" button on a rest card is not a widget; it is an exchange rate between health and a
performance metric, built into the product.

Soft advice therefore has no denominator, and that is a design rather than missing data. The
distinction matters: an undefined adherence caused by a zero denominator is a possible bug, while a
soft topic being absent from the view entirely is a boundary being enforced.

Dismissal has two roles, and confusing them is the mistake. As cadence it means "stop showing me
this in this phase", so the driver is not pestered, and it is kept. As adherence it would mean "the
driver disagreed", which is forbidden for soft advice. One press, two meanings: the resolution was
to keep the dismiss button and remove the follow button, so a driver can still silence a card while
the system never infers consent from it.

Fail closed: an unregistered topic classifies as unknown and a gate turns red. Silently doing the
wrong thing by default is a failure this repository has paid for several times, and the first soft
card someone adds without registering it would otherwise land in the measurement table
automatically, silently, and in the harmful direction.
"""
from __future__ import annotations

from typing import Literal

# Topics whose compliance is measured. These are economic recommendations: whether a driver
# follows them is legitimate information about the advisor, and no health exchange rate is created.
MEASURED_TOPICS: frozenset[str] = frozenset({
    # --- the product's own card kinds ---
    "brief",            # pre-shift card
    "nudge",            # in-shift card
    "recap",            # end-of-shift card
    "bonus",            # the historical default of the action topic; still in tests and old data
    # --- the pipeline's vocabulary ---
    # "online" means keep working, the default when no action is given. It is economic advice, so
    # measuring compliance is legitimate. It is declared here because the episode store once wrote
    # no topic at all, so every pipeline decision had none and fell into the measured table by
    # default. The rest and shift topics the pipeline also uses are declared below, not repeated.
    "online",
    # --- the simulation's channels ---
    "positioning",      # a pseudo-channel: positioning, the only one currently enabled
    "shift_plan",       # disabled after measuring as harmful, but measured when on
    "accept_lift",
    "shift_extend",
    # The rest-deferral channel has moved to the soft group below. This note stays because this is
    # where someone will look for it: that channel was once conditionally measured, and the
    # condition was settled by a preregistered measurement.
    #
    # --- the checkpoint contract's vocabulary ---
    # These were unified into this registry. Before that the contract had its own vocabulary whose
    # intersection with the registry was empty, so the classification boundary could not reach a
    # single one of its events, and a rest checkpoint was accepting consent responses: a record of
    # agreement with a health recommendation.
    #
    # The unification is one of authority rather than of names. The contract and the stored data
    # keep their existing strings, because renaming would break a client contract and every
    # historical record, for far more than it solves. What has to be single is the place that
    # decides which topics are measured.
    "bonus_eligibility",     # the bonus-tier solver. Economic: it reads no rest, fatigue, charge or safety input
                             # (continued)
    # The rate-constraint topic covers the acceptance and completion conditions of the same solver.
    #
    # Measured for the same reason as bonus eligibility: it is about the conditions for being paid,
    # and that solver reads nothing about rest, fatigue, charge or safety, so measuring compliance
    # creates no health-to-money exchange rate.
    #
    # It is a separate topic rather than folded into bonus eligibility because the two answer
    # different questions — how many points are missing, and whether they will count at all — so
    # their compliance rates are different quantities. Merged, the effect of the rate warning could
    # not be separated out.
    "ty_le_rang_buoc",
    # The energy topic covers battery swaps. Measured, though not for the reason first written
    # here: charge is called a health rail elsewhere in this repository. The correct reason is that
    # the battery has a modelled harm channel — the world logs being stranded and the metrics count
    # it — whereas fatigue has none. Measuring compliance with swap advice creates no
    # health-to-money exchange rate, because its consequence is already inside the objective
    # legitimately.
    "energy",
    # The shift-boundary topic covers both ending and extending a shift, since the checkpoint
    # builder returns the same string for both. An earlier comment described only the ending half
    # and attributed extension to another topic below: both were wrong, and the extension half fell
    # through the gap between two comments, so it was never considered when the classification was
    # made. Extending — working longer to reach a bonus tier — is an open question rather than
    # purely economic; it is here to preserve the status quo pending a decision, not as a verdict.
    "shift_boundary",
    # The shift-timing topic is not extension or rescheduling. It is the final default branch of
    # the topic mapper, catching several action codes. Measured: extension resolves to the boundary
    # topic and never reaches this one.
    "shift_timing",
    "positioning_sim_only",  # the positioning solver, simulation only
    # The early-swap channel moves a swap that has to happen anyway into a cheap moment — a long
    # idle stretch with a short queue — rather than a forced one mid-job. Economic for the same
    # reason as energy: the battery harm has a modelled channel, so measuring compliance creates no
    # health-to-money exchange rate.
    "swap_early",
    # The station-choice channel suggests where to swap from live global state — queues, batteries
    # ready and travel time — rather than the instinct's nearest station with one step around a
    # long queue. It belongs to the positioning family, where the advisor's value is concentrated.
    # Purely economic: waiting and travel.
    "station_choice",
    # The policy-information topic has no producer at all: the whole repository contains two
    # references, this declaration and a pinned table in a test. It comes from the contract enum
    # rather than from any card-producing code. It is kept so the enum and the registry cannot
    # drift, but it is not evidence that the channel exists.
    "policy_info",
    # --- the role-play session's proactive lane ---
    # The topics below are economic and the driver can act on them, so measuring compliance is
    # legitimate.
    "idle_waste",       # which hours are being wasted waiting
    "mission",          # the day's missions within the hours remaining
    "weekly_quota",     # how much revenue is still short of the weekly target
    "return_to_core",   # the core cell to return to after a dropoff outside the pilot area
    # The peak-points topic.
    #
    # The pilot config pays double points during the peak hours it lists, and points convert
    # directly into the daily bonus tier. The data had been there all along and no channel ever
    # mentioned it.
    #
    # Measured rather than soft: it carries a verifiable economic claim, the multiplier coming from
    # policy rather than from an estimate, and the driver can act on it, since working inside that
    # window rather than outside it is a real action.
    "point_window",     # a double-points window about to open
})

# Soft topics: said because they are right for the driver, carrying no monetary claim, and never
# measured for compliance.
SOFT_TOPICS: frozenset[str] = frozenset({
    # --- explanation rather than instruction ---
    # These two solvers recount something that has already happened: why money was deducted, and
    # which suspicion flags are open. There is no action to follow, so measuring compliance is a
    # category error rather than an ethical choice. Being soft says exactly what is needed: no
    # numerator, no denominator, and never a compliance flag.
    "penalty_explain",  # the penalty explainer
    "anomaly_alert",    # the anomaly explainer
    "weather",          # a weather warning
    "rest_nudge",       # a suggestion to rest when overworked, distinct from deferring rest
    "traffic",          # a traffic-density warning, of the same family as weather
    # The rest-deferral channel moved here under a decision rule locked before the measurement, so
    # this enforces a preregistered rule rather than a choice made after seeing numbers.
    #
    # The revert condition was registered as: no positive effect, or not significant, or any stop
    # condition firing. Measured over a hundred seeds, two independent conditions were met — the
    # payout difference was small and not significant, and a stop condition fired, with total rest
    # falling by several percent on an interval well clear of zero.
    #
    # Why this is not a failed channel: the preregistration recorded in advance that a non-positive
    # difference was the model's correct expectation, because the simulated world does not model
    # the consequences of fatigue. The measured difference fell inside the predicted interval. The
    # measurement succeeded and the model predicted correctly.
    #
    # But the part that matters more than the money: this channel clearly eats into rest, with both
    # overwork measures rising on intervals excluding zero. That is a health harm, and it does not
    # disappear when the channel becomes soft advice. The channel is disabled in the product config
    # and stays disabled pending the follow-up work.
    #
    # What may be said: within this world, which has no fatigue consequence, a rest channel is a
    # pure cost. What may not be said: that rest suggestions are worthless in reality. The two
    # differ by a coefficient for which there is no data at all.
    "rest_window",
    # --- the checkpoint and cadence vocabularies, soft portion ---
    # A correction worth keeping about the rest topic. An earlier note called it ambiguous for
    # merging two concepts produced by the idle-reduction solver. That was wrong about both the
    # producer and the nature of the thing.
    #
    # That solver does not run on the product path at all: the product orchestrator calls only the
    # bonus and shift solvers, and the contract forbids others. On the product path this topic
    # comes from the shift solver's rest action.
    #
    # No producer emits the other concept. The idle-reduction solver defers rest into low-demand
    # windows, and its input contains no fatigue or health field, so it cannot know a driver is
    # overworked. The shift solver's rest is a minimum-rest floor placed in a low-demand bucket.
    # Both are advice about timing.
    #
    # So the topic is not ambiguous for merging two things: it reserves a place for something that
    # does not exist yet.
    #
    # It stays soft, now for a stronger reason than the original one: the topic mapper routes on
    # the rest action code, so any future solver returning that action, including a genuine fatigue
    # producer, lands on this key. Classifying it as measured would open a permanent door for
    # health advice into the measurement table, in exchange for the denominator of one slice of
    # today's shift solver. The two errors are not equivalent.
    "rest",
    "safety_reserved",  # a safety warning: health and safety, so never measured
    "safety",           # the cadence layer's own name for the same concept
})

ALL_TOPICS: frozenset[str] = MEASURED_TOPICS | SOFT_TOPICS

TopicClass = Literal["measured", "soft", "unknown"]

assert not (MEASURED_TOPICS & SOFT_TOPICS), (
    "một topic không thể vừa được đo vừa là khuyên mềm: "
    f"{sorted(MEASURED_TOPICS & SOFT_TOPICS)}")


def classify(topic: str | None) -> TopicClass:
    """An absent topic classifies as measured: older write paths set none, and
    they are economic channels.

    Absence is not a hiding place for soft advice. Soft advice must name itself explicitly, or this
    boundary cannot be checked by machine.
    """
    if topic is None:
        return "measured"
    if topic in SOFT_TOPICS:
        return "soft"
    if topic in MEASURED_TOPICS:
        return "measured"
    return "unknown"


def is_soft(topic: str | None) -> bool:
    """True when the topic must never enter an adherence numerator or denominator, and never receives a compliance flag."""
    return classify(topic) == "soft"
