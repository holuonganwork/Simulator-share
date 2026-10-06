"""Simulation entities: the driver actor and the swap station. Orders live in the demand
module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ActorState(str, Enum):
    OFFLINE = "offline"
    IDLE = "idle"
    ENROUTE = "enroute"   # heading to the pickup
    ON_TRIP = "on_trip"
    CHARGING = "charging"  # swapping or charging
    REST = "rest"


class FleetType(str, Enum):
    SWAP = "swap"     # swap at a station
    CHARGE = "charge"  # charge at home


@dataclass
class Actor:
    actor_id: int
    archetype: str            # P1..P5
    fleet: FleetType
    home_cell: str
    shift_start_min: float
    shift_end_min: float
    # behavioural parameters, sampled from the archetype
    demand_prior_sigma: float
    accept_base: float
    fatigue_threshold_min: float
    meal_hour: int
    # vehicle info for car/taxi simulation
    vehicle_class: str = "A_COMPACT"
    vehicle_model: str = "VF5"
    seat_capacity: int = 4
    # mutable state
    state: ActorState = ActorState.OFFLINE
    cell: str = ""
    lat: float = 0.0        # current continuous position
    lon: float = 0.0
    soc_pct: float = 100.0
    # per-shift counters
    trips_done: int = 0
    orders_offered: int = 0
    orders_accepted: int = 0
    orders_completed: int = 0
    orders_cancelled: int = 0      # cancellations after acceptance only
    # Minutes idle since the last order was offered. Being offered an order is evidence that
    # demand exists here, so it resets the streak; no evidence for long enough makes the driver
    # impatient enough to travel further.
    idle_streak_min: float = 0.0
    # The destination cell of a voluntary move, whether a relocation or a deadhead back to the
    # core. The en-route state alone is not enough: it is shared with driving to a pickup, and
    # the cell only changes on arrival, so "who is heading where" cannot be inferred from it.
    # Without this field the market state cannot subtract incoming supply, and the advisor sends
    # the whole fleet to one cell.
    # None while stationary or carrying a passenger, where the destination is the dropoff.
    enroute_cell: str | None = None
    # A separate cost ledger, kept apart from payout as the product boundary requires.
    # Kilometres accrue at the charge-consumption call, the single choke point for every move
    # that costs battery. Cost is kilometres times the cash rate plus swap fees; the default
    # configuration sets both to zero, so the baseline stays bit-identical.
    km_driven: float = 0.0
    cost_vnd: int = 0
    orders_soc_skipped: int = 0    # skipped for insufficient charge, which is not a cancellation
    gross_vnd: int = 0
    payout_vnd: int = 0
    points: int = 0
    online_min: float = 0.0
    empty_min: float = 0.0      # moving without a passenger: pickup, relocation and deadhead
    occupied_min: float = 0.0   # with a passenger; the utilisation numerator
    idle_min: float = 0.0       # waiting in place for an order
    rest_min: float = 0.0       # resting
    charge_min: float = 0.0     # swapping or charging, including the queue
    stranded_count: int = 0
    meals_taken: int = 0        # at most one meal break a day, inside the meal window
    # in-simulation rating, scored after a trip; reset daily
    ratings_n: int = 0
    ratings_sum: int = 0
    ratings_5: int = 0
    # newcomer status; tenure grows by the day and is never reset
    tenure_days: int = 365
    newbie_topup_vnd: int = 0     # daily revenue guarantee top-up; reset daily
    # daily missions; progress resets daily
    mission_progress: dict = field(default_factory=dict)   # mission_id -> count
    mission_reward_vnd: int = 0   # daily mission reward; reset daily
    # A temporary lift to the acceptance baseline when the driver acts on advice that their
    # acceptance rate is below the bonus threshold. It lasts for the shift and is capped.
    # This is not advice to accept or refuse a specific order, which the product boundary
    # forbids: it moves a rate, which is the level at which the policy sets its condition.
    accept_lift: float = 0.0
    # Idle minutes accumulated per hour, the input to the idle-reduction solver. The total
    # alone is not enough: the solver has to know which hours the waiting falls in before it can
    # name an hour worth resting through.
    idle_by_hour: dict = field(default_factory=dict)
    rest_deferred_min: float = 0.0   # total minutes of rest deferred on advice
    # Counted once, for the exact deferred interval, at the moment of commitment rather than
    # per tick. The policy-locked cap now measures the quantity it is meant to guard.
    #
    # The hour to concentrate rest in comes from the idle-reduction solver reading yesterday's
    # idle time. It is absent on the first day, for want of history, which is correct rather
    # than missing.
    planned_rest_hour: int | None = None
    # Deferring rest is a commitment, not a veto. The veto version turned most rest into empty
    # waiting without producing a single extra order.
    #   The due mark is the absolute minute-of-day at which the promised rest hour begins, and
    #   is absent when no commitment is open. Minutes rather than hours, so that shifts crossing
    #   midnight survive.
    #   The broken flag records a commitment that could not be kept because the whole hour was
    #   busy. The right to rest is then returned: no further rest instinct may be overridden
    #   until rest actually happens.
    rest_commit_due_min: float | None = None
    rest_commit_broken: bool = False
    shift_extended_min: float = 0.0   # minutes the shift end was deferred on advice

    @property
    def effective_accept_base(self) -> float:
        """The acceptance baseline plus any lift, capped below one: nobody accepts everything."""
        return min(0.98, self.accept_base + self.accept_lift)
    # personal experience: a demand prior per cell and hour, built lazily
    demand_prior: dict = field(default_factory=dict)

    @property
    def orders_decided(self) -> int:
        """How many times the driver was actually asked, which is how often the accept decision ran.

        The offered counter increments before the charge gate, so an order skipped for
        insufficient charge never reaches the decision at all. The two are different quantities
        and now have separate names:

        - offered: how many orders the dispatcher routed here. This is what the advice bridge
          needs to estimate the rate at which work arrives.
        - decided: how many times the driver was asked. This is the denominator of the
          acceptance rate.
        """
        return max(0, self.orders_offered - self.orders_soc_skipped)

    @property
    def acceptance_rate(self) -> float:
        """The acceptance rate over the turns the driver was actually asked.

        With the offered counter as denominator, a turn blocked by the charge gate counted
        against the driver as a refusal. That was not merely a reporting error: the value feeds
        the daily bonus, which pays nothing at all below the acceptance threshold regardless of
        points. Measured over a fleet of ninety across ten days, about five percent of
        driver-days were pushed below the threshold by charge skips alone, and a third of those
        genuinely lost money.

        The zero-over-zero convention returning one is kept deliberately: a consumer that needs
        to distinguish "not yet known" from "perfect" must go through the estimator rather than
        read this property directly.
        """
        d = self.orders_decided
        return self.orders_accepted / d if d else 1.0

    @property
    def completion_rate(self) -> float:
        return self.orders_completed / self.orders_accepted if self.orders_accepted else 1.0

    def consume_soc(self, dist_km: float, pct_per_km: float) -> None:
        self.soc_pct = max(0.0, self.soc_pct - dist_km * pct_per_km)
        self.km_driven += float(dist_km)   # the single choke point where kilometres reach the cost ledger

    # An explicit list of what resets each day, rather than "everything except", because this
    # is easy to get wrong in both directions: forgetting a reset lets points and trips
    # accumulate without bound and corrupts the daily bonus, while resetting too much destroys
    # the history that learning from yesterday depends on.
    _DAILY_RESET_INT = ("trips_done", "orders_offered", "orders_accepted", "orders_completed",
                        "orders_cancelled", "orders_soc_skipped", "gross_vnd", "payout_vnd",
                        "points", "stranded_count", "meals_taken",
                        # rating, daily newcomer top-up and daily missions are day state
                        "ratings_n", "ratings_sum", "ratings_5",
                        "newbie_topup_vnd", "mission_reward_vnd", "cost_vnd")
    _DAILY_RESET_FLOAT = ("online_min", "empty_min", "occupied_min", "idle_min", "rest_min",
                          "charge_min", "accept_lift", "rest_deferred_min", "shift_extended_min",
                          "km_driven",
                          # The idle streak is day state. Without this, day two of a
                          # multi-day run opens with the streak left over from day one, and the
                          # impatience instinct reads a driver who has just woken up as one who
                          # has been waiting for the better part of an hour: wrong in the
                          # direction of more impatience than is real. Harmless in single-day
                          # runs, and a precondition for the rest-commitment work.
                          "idle_streak_min")

    def reset_for_new_day(self, soc_pct: float, shift_start_min: float,
                          shift_end_min: float) -> None:
        """Start a new day: clear day state, keep identity.

        Untouched: the actor id, archetype, fleet, home cell, acceptance baseline, prior sigma,
        fatigue threshold and meal hour. Those describe a person rather than a day. Rolling
        history lives outside the actor, so it is safe as well.
        """
        for name in self._DAILY_RESET_INT:
            setattr(self, name, 0)
        for name in self._DAILY_RESET_FLOAT:
            setattr(self, name, 0.0)
        self.idle_by_hour = {}
        self.mission_progress = {}
        self.demand_prior = {}
        self.enroute_cell = None               # a movement target is day state
        # The rest commitment is day state, reset explicitly rather than through the numeric
        # table, because it resets to absent rather than to zero. Forgetting this line opens day
        # two with yesterday's commitment still standing.
        self.rest_commit_due_min = None
        self.rest_commit_broken = False
        # tenure grows by one each morning: the passage of time is the only identity that moves
        self.tenure_days += 1
        self.state = ActorState.OFFLINE
        self.soc_pct = float(soc_pct)          # charged or swapped overnight
        self.shift_start_min = float(shift_start_min)
        self.shift_end_min = float(shift_end_min)
        self.cell = self.home_cell             # the day starts from home


@dataclass
class BatteryInStation:
    """One battery in a cabinet, recharged after a driver returns it empty."""
    soc_pct: float
    ready_at_min: float  # when the ready threshold is reached, while charging


@dataclass
class Station:
    node_id: int
    cell: str
    lat: float
    lon: float
    slots: int
    ready_soc_pct: float
    # the batteries currently in the cabinet
    batteries: list[BatteryInStation] = field(default_factory=list)
    queue_len: int = 0

    def available_full(self, now_min: float) -> int:
        return sum(1 for b in self.batteries if b.soc_pct >= self.ready_soc_pct and b.ready_at_min <= now_min)
