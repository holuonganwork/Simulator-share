"""A human-in-the-loop session: one actor driven by a person, with the world frozen while it
waits for a decision.

The mechanism is running the scheduler until an event. The scheduler may be run repeatedly on one
environment; given an event as its stopping condition it runs until that event is processed and
then returns, leaving every other process exactly where it was in the queue. That is what freezes
the whole world: no background thread, no deep copy — impossible, since generators cannot be
pickled — and no suspending one actor while others keep running.

    step      create an event and run until it, so the simulation stops at a decision point
    command   succeed the decision event, injecting the answer into the suspended process, then
              step again to continue

Two gates, and why exactly two.

The instinct gate returns the instinct's value immediately and never yields. Delegating to a
generator that does not yield leaves the process entirely unsuspended, so event interleaving is
identical to the autonomous arm. That is how the two seams in the world are shown to be inert: a
run naming a human actor must produce a fingerprint identical to an ordinary one. Without this
gate, "the seam changes nothing" would be an assertion rather than a demonstration.

The interactive gate yields for real, freezing the world.

A correction worth keeping: it was once written here that because this gate yields, event
interleaving differs from the autonomous arm, so its figures could not be compared. Remeasured,
that is wrong. Across five seeds and three actors, with the person following the suggestion at
every stop, all fifteen fingerprints matched the autonomous arm exactly. Suspension does not move
the world: the human actor's process is rescheduled, but at that instant almost no other event
intervenes, so the result is unchanged.

That is a measured property rather than a proven invariant. A gate holds it, and if it ever goes
red it means some seed or configuration exposes an interleaving that does matter.

The correct consequence: the decision not to claim uplift from these figures stands, but because
the person clicking in a demo is not a real driver, not because the engine diverges. If anything
the opposite — a demo can honestly say that following the suggestions reproduces the model's own
measured result.

Replay uses no third gate. The temptation is a replay gate that reads the command log and answers
at once. That would be false replay: such a gate does not yield, so interleaving differs from an
interactive session and the rerun produces a different world. Replay therefore uses the
interactive gate and injects logged commands through the ordinary command path — the same code,
equivalent by construction rather than by promise.

Known limits:

- sessions live in memory. A restart abandons them; resuming is not faked.
- offers do not expire in a session: a person may take as long as they like. Losing the time
  pressure is deliberate rather than an oversight.
- the charge and bonus-tier stops are signals in the snapshot; their corresponding commands
  belong to a later slice.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from ..behavior import AcceptDecision, IdleAction
from ..config import Config
from .commands import (
    ALLOWED_COMMANDS,
    Command,
    CommandKind,
    PauseKind,
    PausePoint,
    RejectLayer,
    ValidationResult,
    validate_command,
)

# The session lifecycle. A pending-end state exists because finishing does not interrupt a
# trip in progress.
SESSION_STATES = ("CREATED", "READY_TO_START", "ACTIVE", "END_REQUESTED", "COMPLETED", "ABANDONED")


# =============================================================================================
# Gate one: the instinct, which never yields. It exists to demonstrate that the two seams in
# the world are inert.
# =============================================================================================

class InstinctGate:
    """Return the instinct's value unchanged and record it. It never yields, so event ordering
    is unaffected.

    The decision log says exactly what the instinct decided at each of the human actor's stops.
    It is used to build goldens — what happens if the person simply follows the instinct — and to
    measure the interaction budget before a new stop is enabled for real users."""

    def __init__(self, human_actor_id: int | None = None):
        self.human_actor_id = human_actor_id
        self.decision_log: list[dict[str, Any]] = []
        self.world = None

    def attach_world(self, world) -> None:
        self.world = world

    def ask_accept_offer(self, actor, order, assignment, instinct: AcceptDecision):
        self.decision_log.append({"kind": "OFFER", "t_min": round(self.world.env.now, 3),
                                  "offer_ref": str(getattr(order, "order_id", "")),
                                  "instinct": bool(instinct.accepted)})
        return instinct
        yield  # pragma: no cover - makes this a generator, and is never reached

    def ask_idle_action(self, actor, now: float, action, target, goi_y=None):
        self.decision_log.append({"kind": "IDLE", "t_min": round(now, 3),
                                  "instinct": getattr(action, "value", str(action)),
                                  "target": target, "goi_y": goi_y})
        return action, target, {}
        yield  # pragma: no cover - as above

    def ask_ve_loi(self, actor, now: float, o_goi_y: str):
        self.decision_log.append({"kind": "OUT_OF_CORE", "t_min": round(now, 3),
                                  "o_goi_y": o_goi_y})
        return o_goi_y
        yield  # pragma: no cover - as above


# =============================================================================================
# Gate two: interactive, yielding for real so the world freezes
# =============================================================================================

@dataclass
class _PendingDecision:
    """A stop awaiting an answer. The event is where a command is injected into the suspended
    process."""

    pause: PausePoint
    evt: Any
    instinct: Any = None                  # the instinct's own value, so the interface can show what the system would do
    context: dict = field(default_factory=dict)


class InteractiveGate:
    """Suspend the human actor's process at a decision point until a command arrives.

    Why the pending stops are a queue rather than a single slot: two different processes can reach
    a stop for the same human actor at the same instant — the dispatcher offering a trip and the
    actor loop finding itself idle — because at the moment of the offer the actor is still idle
    rather than en route. With a single slot the second stop would overwrite the first, or
    succeeding the pause event twice would raise. A queue serves them first in, first out, which
    is the order the engine reached them and therefore the autonomous arm's order.

    The interaction budget. Measured over a full day, the engine asks the human actor between
    eighty and a hundred and sixty times, depending on when their shift starts. That is unusable
    in a demo.

    By default the gate asks only when the instinct intends something other than waiting, which
    brings it to roughly thirty stops and puts them where the value is: relocating, swapping,
    resting and finishing.

    A correction to an earlier claim: the reduction is three to fivefold, not an order of
    magnitude. And the fleet-median idle count varies enormously with shift start — an actor
    starting in the evening has a fraction of it — so it must not be used to infer any particular
    actor's budget. Disable the filter for measurement, never for a demo.
    """

    def __init__(self, human_actor_id: int, *, filter_idle: bool = True):
        self.human_actor_id = human_actor_id
        self.filter_idle = filter_idle
        # A weekly target the driver sets themselves; absent means unset.
        # Absent rather than zero: "not set" is not "set to zero", and the latter would make the
        # solver report the target met from the first day of the week.
        self.muc_tieu_tuan_vnd: int | None = None
        self.pending: deque[_PendingDecision] = deque()
        self.pause_evt = None
        self.world = None
        self.idle_asks_filtered = 0   # how many idle turns were filtered out: the evidence behind the interaction budget

    def attach_world(self, world) -> None:
        self.world = world

    # --- Suspension ----------------------------------------------------------------------

    def _suspend(self, pause: PausePoint, instinct, context: dict):
        """Register the stop, notify the session, then suspend until a command arrives."""
        env = self.world.env
        evt = env.event()
        self.pending.append(_PendingDecision(pause, evt, instinct, context))
        # Wake the session once for a cluster of stops at the same instant: a second stop that
        # finds the event already triggered stays silent, and the session serves it on the next
        # step.
        if self.pause_evt is not None and not self.pause_evt.triggered:
            self.pause_evt.succeed()
        cmd: Command = yield evt
        return cmd

    # --- The two real stops --------------------------------------------------------------

    def ask_accept_offer(self, actor, order, assignment, instinct: AcceptDecision):
        """The offer stop. It returns the instinct's decision with its probability, net figure and
        standardised score preserved.

        Those three are the grounds that answer why a driver refused. The person overrides
        acceptance without erasing the economic context of the offer; erasing it would make the
        human actor's decline events incomparable with every other actor's."""
        pause = PausePoint(
            kind=PauseKind.OFFER,
            t_min=round(self.world.env.now, 3),
            offer_ref=str(getattr(order, "order_id", "")),
            hint={"instinct_accepts": bool(instinct.accepted),
                  "p_accept": round(float(instinct.p_accept), 4),
                  "instinct_reason": instinct.reason},
        )
        context = {
            "gross_vnd": int(getattr(order, "gross_vnd", 0)),
            "pickup_dist_km": round(float(getattr(assignment, "pickup_dist_km", 0.0)), 3),
            "dist_km": round(float(getattr(order, "dist_km", 0.0)), 3),
            "pickup_cell": getattr(order, "pickup_cell", None),
            "drop_cell": getattr(order, "drop_cell", None),
            # Pickup and dropoff coordinates. The order has carried them all along, declared as
            # being for movement and display, but they never reached the interface, so a driver
            # was told a pickup was half a kilometre away without knowing in which direction. The
            # client has no grid library and cannot derive them from a cell.
            "pickup_lat": round(float(getattr(order, "pickup_lat", 0.0)), 6),
            "pickup_lon": round(float(getattr(order, "pickup_lon", 0.0)), 6),
            "drop_lat": round(float(getattr(order, "drop_lat", 0.0)), 6),
            "drop_lon": round(float(getattr(order, "drop_lon", 0.0)), 6),
            # The trip's points.
            #
            # Points are what lead to a bonus tier, but the trip card showed only money, so a
            # driver had to guess how much a trip contributed toward a tier. This is a policy
            # figure, keyed to the hour the passenger ordered, deterministic and computed by the
            # same formula the world uses to credit points on completion. It is not a prediction,
            # so exposing it crosses no boundary.
            #
            # It reads the running policy rather than copying the table, because a copy is a
            # second source of truth for one rule.
            "diem_cuoc": int(self.world.policy.trip_points(
                int(float(getattr(order, "t_min", 0.0)) // 60) % 24)),
        }
        cmd = yield from self._suspend(pause, instinct, context)
        accepted = cmd.kind is CommandKind.ACCEPT_OFFER
        return AcceptDecision(accepted=accepted, p_accept=float(instinct.p_accept),
                              net_vnd=float(instinct.net_vnd), z=float(instinct.z),
                              reason="human_accept" if accepted else "human_decline")

    def ask_idle_action(self, actor, now: float, action, target, goi_y=None):
        """The idle stop, filtered by the interaction budget.

        A suggestion must be able to break through that filter. The positioning channel speaks
        only when the instinct intends to wait, which is exactly the turn the filter skips.
        Without the suggestion clause below, the one channel with a measured effect on earnings
        would be swallowed entirely, and a demo would enable the advisor with nobody hearing
        anything.
        """
        if self.filter_idle and action == IdleAction.WAIT and goi_y is None:
            self.idle_asks_filtered += 1
            return action, target, {}
        # The hint no longer speaks the instinct's suggestions to the driver: anything that is
        # not advice from the advisor is not printed. The instinct value is kept because replay
        # and the logs need it to reconstruct a session, and a gate forbids the interface from
        # reading that field.
        hint = {"instinct": getattr(action, "value", str(action)), "target": target,
                "source": "instinct"}
        if goi_y:
            hint.update(goi_y)          # source="advisor" + channel + target + decision_id
        pause = PausePoint(kind=PauseKind.IDLE, t_min=round(now, 3), hint=hint)
        context = {"cell": actor.cell, "soc_pct": round(float(actor.soc_pct), 2),
                   "online_min": round(float(actor.online_min), 1),
                   "idle_streak_min": round(float(getattr(actor, "idle_streak_min", 0.0)), 1)}
        cmd = yield from self._suspend(pause, (action, target), context)
        return self._doc_lenh(cmd, action, target)

    def ask_ve_loi(self, actor, now: float, o_goi_y: str):
        """The out-of-core stop, returning the core cell the driver chooses.

        This stop is exempt from the interaction filter. The filter exists to skip idle turns
        where the instinct only intends to wait; here the opposite holds — without asking, the
        world moves the driver by itself, which is the defect being fixed. A stop where skipping
        means losing agency is not a place to economise on clicks.

        The suggested cell is the nearest core cell, the same one the engine chose before.
        """
        # Always attributed to the system, never to the advisor.
        #
        # Dropoffs frequently fall outside the operating area, and the return prompt was being
        # attributed to the advisor on the reasoning that it had become a spoken recommendation
        # the driver could refuse. That reasoning is wrong at the root: there is no demand outside
        # the core because the *model* does not model demand there. That is a boundary of the
        # simulated world, not a claim about the market. A real driver who drops off in another
        # district keeps working in that district, and no assistant tells them to come back.
        #
        # The cost of the misattribution is not small: when everything is "advice from the
        # assistant", the real advice — repositioning by supply and demand, the one channel with a
        # measured effect on earnings — is lost in the noise.
        hint = {"source": "system",
                "channel": "return_to_core",
                "action": IdleAction.RELOCATE.value, "target": o_goi_y,
                "decision_id": f"vecore-{actor.actor_id}-{int(now)}"}
        pause = PausePoint(kind=PauseKind.OUT_OF_CORE, t_min=round(now, 3), hint=hint)
        context = {"cell": actor.cell, "soc_pct": round(float(actor.soc_pct), 2),
                   "ngoai_loi": True}
        cmd = yield from self._suspend(pause, (IdleAction.RELOCATE, o_goi_y), context)
        chon = (cmd.payload or {}).get("cell")
        return str(chon) if chon else o_goi_y

    @staticmethod
    def _doc_lenh(cmd: Command, action, target):
        """Translate a person's command into an action, a target and its parameters.

        The parameters are either empty or a single key, and the engine reads them only in the
        matching action branch, so a stray parameter can never be used by mistake. It is a static
        function so the translator can be tested without building a world.

        Every value here has already passed validation, so the conversions below cannot raise.
        Validating there rather than here is deliberate: by this point the command has been
        accepted, and refusing now would be refusing after promising."""
        p = cmd.payload or {}
        if cmd.kind is CommandKind.FOLLOW_SUGGESTION:
            # Do exactly what the system suggested, including finishing the shift. This command
            # predates the full command set and is kept because replay and every existing log
            # still use it.
            return action, target, {}
        if cmd.kind is CommandKind.END_SHIFT:
            return IdleAction.END_SHIFT, None, {}
        if cmd.kind is CommandKind.RELOCATE:
            return IdleAction.RELOCATE, str(p["cell"]), {}
        if cmd.kind is CommandKind.GO_SWAP:
            st = p.get("station_id")
            return IdleAction.GO_SWAP, None, {} if st is None else {"station_id": int(st)}
        if cmd.kind is CommandKind.GO_CHARGE:
            return IdleAction.GO_CHARGE, None, {}
        if cmd.kind is CommandKind.START_REST:
            m = p.get("rest_min")
            return IdleAction.REST, None, {} if m is None else {"rest_min": float(m)}
        # Continue waiting means standing still. The instinct's action is deliberately not
        # returned: someone who chose to wait must wait, and returning the action would silently
        # ignore their command.
        return IdleAction.WAIT, None, {}


# =============================================================================================
# The session
# =============================================================================================

class LoopGuardTripped(RuntimeError):
    """The step loop exceeded its slice budget: fail loudly rather than hang."""


class InteractiveSimSession:
    """Owns a live world, its clock, and an append-only command log."""

    # A guard against hanging forever: a day of a hundred or so decisions needs far fewer slices
# than this.
    MAX_SLICES = 20000

    def __init__(self, world, deps: dict, gate, session_id: str):
        self.world = world
        self._deps = deps
        self.gate = gate
        self.session_id = session_id
        self.human_actor_id = getattr(gate, "human_actor_id", None) if gate else None
        self.state = "READY_TO_START"
        self.applied_count = 0
        self.command_log: list[dict[str, Any]] = []
        # Server-owned journal of rising-edge proactive signals.  It is deliberately separate
        # from command_log: OFFER/IDLE pauses are decisions, not reminders, and GET snapshots
        # must never manufacture history.
        self.reminder_log: list[dict[str, Any]] = []
        self._active_reminder_codes: set[str] = set()
        self.served: dict[str, dict] = {}
        self._day_over = False
        self._settled = False
        self._slices = 0
        self._result = None
        self._end_evt = None

    # --- Construction --------------------------------------------------------------------

    @classmethod
    def create(cls, cfg: Config, seed: int, human_actor_id: int | None = None, *,
               session_id: str | None = None, filter_idle: bool = True):
        """Create a session. With no human actor nobody is driving: the session merely runs the
        scheduler in slices and must produce the same result as an ordinary run, which a gate
        checks."""
        from ..runner import build_world
        gate = InteractiveGate(human_actor_id, filter_idle=filter_idle) \
            if human_actor_id is not None else None
        world, deps = build_world(cfg, seed, human_actor_id=human_actor_id, hil_gate=gate)
        if gate is not None:
            gate.attach_world(world)
        sid = session_id or f"hil-{seed}-{human_actor_id}-{world.run_id[-8:]}"
        return cls(world, deps, gate, sid)

    # --- Reading state -------------------------------------------------------------------

    @property
    def version(self) -> int:
        """The optimistic-concurrency token: the number of commands applied."""
        return self.applied_count

    @property
    def t_min(self) -> float:
        return round(self.world.env.now, 3)

    @property
    def pause(self) -> PausePoint | None:
        if self.gate is None or not self.gate.pending:
            return None
        return self.gate.pending[0].pause

    @property
    def current_pending(self) -> _PendingDecision | None:
        if self.gate is None or not self.gate.pending:
            return None
        return self.gate.pending[0]

    def allowed_commands(self) -> list[str]:
        """The commands the interface may offer right now: the single source of truth for buttons.

        Unlike the pure per-stop table the validator uses, this also subtracts commands *this
        world* would certainly refuse. Rendering a button only for it to be rejected teaches a
        driver that buttons cannot be trusted, and trust in the buttons is the whole thing this
        demonstration is testing.

        The validator's table is deliberately not narrowed to match: a command still travels the
        full validation path and is refused with a named reason. The filtering here is about
        display; the real block stays in the validator.
        """
        if self.state == "READY_TO_START":
            return [CommandKind.START_SHIFT.value]
        p = self.pause
        if p is None:
            return []
        from ..entities import FleetType

        cho_phep = set(ALLOWED_COMMANDS[p.kind])
        a = self.world.actors.get(self.human_actor_id) if self.human_actor_id is not None else None
        if a is not None and a.fleet is not FleetType.SWAP:
            # a fifth of drivers charge at home, with a fixed pack and nothing to swap
            cho_phep.discard(CommandKind.GO_SWAP)
        return sorted(x.value for x in cho_phep)

    # --- The run loop --------------------------------------------------------------------

    def step(self) -> PausePoint | None:
        """Advance to the next stop, or return nothing once the day is over.

        Called while a stop is still unanswered it returns that same stop, so refreshing the
        interface never advances the simulation by a minute."""
        if self.state in ("COMPLETED", "ABANDONED"):
            return None
        if self.state == "READY_TO_START":
            # Before the shift: the stop is built by the session rather than by the engine. The
            # shift begins at the archetype's own start time; choosing that time is a later
            # slice.
            return PausePoint(kind=PauseKind.BEFORE_SHIFT, t_min=self.t_min,
                              hint=self._before_shift_hint())
        return self._run_to_pause()

    def _before_shift_hint(self) -> dict:
        a = self.world.actors.get(self.human_actor_id) if self.human_actor_id is not None else None
        if a is None:
            return {}
        return {"shift_start_min": round(float(a.shift_start_min), 1),
                "shift_len_min": round(float(getattr(a, "shift_len_min", 0.0)), 1),
                "archetype": getattr(getattr(a, "archetype", None), "value", None),
                "cell": a.cell}

    def _end_marker(self):
        """The end-of-day event, created once and reused for every slice.

        Recreating it per slice would push hundreds of timeouts into the queue. One suffices: it
        only has to trigger once, at the end of the day."""
        if self._end_evt is None:
            env = self.world.env
            self._end_evt = env.timeout(max(0.0, self.world.end_min - env.now))
        return self._end_evt

    def _run_to_pause(self) -> PausePoint | None:
        """Run the scheduler until a stop occurs or the day ends.

        Why the time bound is required, from a real failure: running until the pause event alone
        hangs indefinitely. Once the human actor goes offline there are no more stops, the event
        never triggers, and the simulation runs for twenty times the length of a day with the
        queue still full — one step call processing hundreds of thousands of events without
        returning.

        The wrong assumption was that every process loops until the end of the day and so the
        queue empties there. The ordinary run never needed that: it bounds itself by *time*.
        Processes schedule past the end of the day quite harmlessly *because* of that bound.
        Removing the bound exposed behaviour nobody had ever reached.

        Each slice therefore runs until either the pause event or the end marker. The marker is
        created once; an already-triggered condition returns immediately, so an extra callback on
        it is harmless.

        A runtime error is still caught as a second line of defence: if some configuration
        empties the queue before the end of the day, that is also the end of the day rather than
        a fault.
        """
        # A guard against a silent failure that happened for real: running the scheduler with no
        # processes queued does not raise. It jumps to the end of the day and returns a world in
        # which nobody did anything and every figure is zero.
        if self.state == "READY_TO_START":
            raise LoopGuardTripped(
                "chạy thế giới trước START_SHIFT — process chưa được sinh, `env.run` sẽ nhảy "
                "thẳng tới hết ngày và trả về một ngày RỖNG mà không báo lỗi gì")
        if self.gate is None:
            # nobody is driving: run one slice to the end of the day
            if not self._day_over:
                self.world.env.run(until=self.world.end_min)
                self._day_over = True
            return None
        while not self.gate.pending and not self._day_over:
            self._slices += 1
            if self._slices > self.MAX_SLICES:
                raise LoopGuardTripped(
                    f"vòng step() vượt {self.MAX_SLICES} lát ở t={self.t_min}′ mà chưa hết ngày "
                    f"— nghi phạm: một điểm dừng được đăng ký nhưng không ai trả lời, hoặc gate "
                    f"trả về mà không pop hàng chờ")
            env = self.world.env
            self.gate.pause_evt = env.event()
            het_ngay = self._end_marker()
            try:
                env.run(until=env.any_of([self.gate.pause_evt, het_ngay]))
            except RuntimeError as e:
                if "until" not in str(e) and "No scheduled events left" not in str(e):
                    raise
                self._day_over = True
            else:
                if het_ngay.processed:
                    self._day_over = True
        return self.pause

    # --- Accepting commands --------------------------------------------------------------

    def command(self, cmd: Command) -> ValidationResult:
        """Validate through every layer, then apply. A bad command yields a named result rather
        than an exception."""
        res = validate_command(cmd, session_id=self.session_id, state=self._state_for_validation(),
                               version=self.version, pause=self._pause_for_validation(),
                               served=self.served)
        if not res.ok or res.is_duplicate:
            return res
        xau = self._gio_vao_ca_khong_hop_le(cmd) or self._tham_so_khong_hop_le(cmd)
        if xau is not None:
            return ValidationResult(ok=False, layer=RejectLayer.BAD_PAYLOAD, reason=xau)
        self._apply(cmd)
        return res

    def _tham_so_khong_hop_le(self, cmd: Command) -> str | None:
        """Validate the parameters of the parameterised commands, returning a reason or nothing.

        Why here rather than in the pure validator: these rules need the world — which cells are
        core, which stations exist, what kind of vehicle this is — while the validator is
        deliberately pure so it can be tested without building one. It returns a *reason* and the
        caller turns that into a bad-payload result; no exception leaves the simulation layer,
        since the rejection type belongs to the HTTP layer above.
        """
        from ..entities import FleetType

        p = cmd.payload or {}
        a = self.world.actors.get(self.human_actor_id) if self.human_actor_id is not None else None
        if cmd.kind in (CommandKind.RELOCATE, CommandKind.GO_SWAP, CommandKind.GO_CHARGE,
                        CommandKind.START_REST) and a is None:
            return "phiên không có actor người để nhận lệnh này"

        if cmd.kind is CommandKind.RELOCATE:
            cell = p.get("cell")
            if not isinstance(cell, str) or not cell:
                return f"RELOCATE cần payload.cell là ô H3, nhận được {cell!r}"
            if not self.world.grid.is_core(cell):
                # Outside the core the distance still computes and the actor still arrives, but
                # at a cell with no demand, no landmarks and no measurement. A loud refusal beats
                # accepting and dropping the driver off the map.
                return f"ô {cell!r} không thuộc lõi pilot"
            if cell == a.cell:
                return f"đang đứng ở {cell!r} rồi — đổi chỗ sang chính ô đó là lệnh rỗng"
            return None

        if cmd.kind is CommandKind.GO_SWAP:
            if a.fleet is not FleetType.SWAP:
                # The fleet type is a battery strategy rather than a vehicle class: a fixed pack
                # charged at home has nothing to remove, so swapping is physically impossible
                # rather than merely a poor choice.
                return "xe của bác sạc tại nhà (không tháo pin) — không đổi pin ở trạm được"
            st = p.get("station_id")
            if st is None:
                return None                  # optional: the engine picks the nearest station
            if isinstance(st, bool) or not isinstance(st, int):
                return f"station_id phải là số hiệu trạm, nhận được {st!r}"
            if st not in self.world.station_by_id:
                return f"không có trạm số {st} trong thế giới đang chạy"
            return None

        if cmd.kind is CommandKind.START_REST:
            m = p.get("rest_min")
            if m is None:
                return None                  # optional: the engine draws a duration as the instinct would
            if isinstance(m, bool) or not isinstance(m, (int, float)):
                return f"rest_min phải là số phút, nhận được {m!r}"
            con_lai = float(self.world.end_min) - self.t_min
            if not (0.0 < float(m) <= con_lai):
                # Bounded by the day rather than by the shift: the shift's end time is no
                # longer shown to the driver, since finishing is a button, so bounding by it
                # would bound by an invisible number. The day's end is in the snapshot.
                return (f"nghỉ {float(m):.0f}′ không lọt trong phần còn lại của ngày "
                        f"({con_lai:.0f}′)")
            return None

        return None

    def _gio_vao_ca_khong_hop_le(self, cmd: Command) -> str | None:
        """Validate a driver-chosen shift start, returning a reason or nothing.

        The start time used to be the archetype's own: a simulation parameter the player could
        not touch, even though they are the one deciding what time to start today.

        Swallowing a bad payload and quietly using the default is how a player comes to believe
        they chose something when they did not, so every branch here returns a refusal with a
        reason.
        """
        if cmd.kind is not CommandKind.START_SHIFT:
            return None
        gio = (cmd.payload or {}).get("shift_start_min")
        if gio is None:
            return None                      # optional: omitted, the archetype's own schedule stands
        if self.human_actor_id is None or self.human_actor_id not in self.world.actors:
            return "phiên không có actor người để đặt giờ vào ca"
        if isinstance(gio, bool) or not isinstance(gio, (int, float)):
            return f"shift_start_min phải là số phút trong ngày, nhận được {gio!r}"
        # A shift cannot start in the past: the world's clock has already passed that point, and
        # moving back would rewrite the history of a running world.
        if not (self.t_min <= float(gio) < float(self.world.end_min)):
            return (f"giờ vào ca {float(gio):.0f}′ phải nằm trong "
                    f"[{self.t_min:.0f}′, {float(self.world.end_min):.0f}′)")
        return self._gio_ket_ca_khong_hop_le(cmd, float(gio))

    def _gio_ket_ca_khong_hop_le(self, cmd: Command, vao: float) -> str | None:
        """The time the driver *intends* to finish. Optional; omitted, the archetype's shift length
        stands.

        Why the field has to exist: nothing actually knows when a driver stops using the app, yet
        three solvers — whether a bonus tier is still reachable, what to do in each window, and
        which mission to take — all use the archetype's finish time as their time budget. Three
        solvers were computing on a number the player had never stated, and the agent was then
        presenting their conclusions as fact.

        Letting the driver state their intended finish turns that number from an assumption into
        a declaration. It can still be wrong — changing their mind is their right, and the finish
        button is still there — but then it is wrong honestly: wrong because a person changed
        their mind, not because a machine guessed."""
        gio = (cmd.payload or {}).get("shift_end_min")
        if gio is None:
            return None
        if isinstance(gio, bool) or not isinstance(gio, (int, float)):
            return f"shift_end_min phải là số phút trong ngày, nhận được {gio!r}"
        if float(gio) <= vao:
            return (f"giờ kết ca {float(gio):.0f}′ phải SAU giờ vào ca {vao:.0f}′ — "
                    f"một ca dài 0 phút thì không có gì để lập kế hoạch")
        if float(gio) > float(self.world.end_min):
            return (f"giờ kết ca {float(gio):.0f}′ vượt quá cuối ngày "
                    f"({float(self.world.end_min):.0f}′)")
        return None

    def _state_for_validation(self) -> str:
        """A session ready to start must pass the state layer in order to receive the start command.

        That layer admits only active states, which is right for in-shift commands. The start
        command arrives while the session is still ready-to-start, so it is presented as active
        for the check. The layer itself is deliberately not widened: widening it would admit
        *every* command before the shift begins, leaving the per-stop table as the only block and
        losing the matrix's resolution."""
        return "ACTIVE" if self.state == "READY_TO_START" else self.state

    def _pause_for_validation(self) -> PausePoint | None:
        if self.state == "READY_TO_START":
            return PausePoint(kind=PauseKind.BEFORE_SHIFT, t_min=self.t_min)
        return self.pause

    def _apply(self, cmd: Command) -> None:
        """Apply a command: log it, bump the version, and inject it into the suspended process."""
        record = {"seq": self.applied_count, "kind": cmd.kind.value, "t_min": self.t_min,
                  "client_command_id": cmd.client_command_id, "payload": dict(cmd.payload)}
        if cmd.kind is CommandKind.START_SHIFT:
            # The chosen start time was validated when the command was accepted, so all that
            # remains here is to apply it. Validating at this layer would be too late: by the
            # time a command is being applied it has already been accepted, and a bad payload can
            # no longer be refused cleanly.
            gio = (cmd.payload or {}).get("shift_start_min")
            if gio is not None:
                a = self.world.actors[self.human_actor_id]
                dai = max(0.0, float(getattr(a, "shift_end_min", 0.0) or 0.0)
                          - float(a.shift_start_min))
                a.shift_start_min = float(gio)
                # Keep the archetype's shift length when no finish time is given: moving the
                # start does not mean intending to work longer.
                a.shift_end_min = min(float(self.world.end_min), float(gio) + dai)
                record["shift_start_min"] = round(float(gio), 1)
            # The time the driver *intends* to finish, replacing the solvers' planning horizon
            # with a figure they stated themselves. Actually finishing is still the button; this
            # is an intention, and the solvers get to know it.
            het = (cmd.payload or {}).get("shift_end_min")
            if het is not None:
                a = self.world.actors[self.human_actor_id]
                a.shift_end_min = float(het)
                record["shift_end_min"] = round(float(het), 1)
            # A weekly target the driver sets for themselves.
            #
            # The weekly solver and its pipeline were complete but blocked, because the operator
            # had not supplied real quota figures. Letting the driver enter one means the number
            # comes from a person rather than from us, which crosses no boundary and does not
            # wait on the operator.
            #
            # Two constraints are mandatory: it is not called a quota, and no clawback is
            # computed. Quotas and clawbacks are the operator's figures; attaching a deduction to
            # a target someone set for themselves would invent a penalty for a commitment made to
            # nobody. The solver already treats an absent clawback rate as zero, so it is simply
            # not passed.
            muc_tieu = (cmd.payload or {}).get("muc_tieu_tuan_vnd")
            if muc_tieu is not None:
                self.muc_tieu_tuan_vnd = int(muc_tieu)
                record["muc_tieu_tuan_vnd"] = int(muc_tieu)
            # Processes are spawned here rather than at construction: before the shift starts
            # the world may not advance by a single minute. Spawning only registers processes
            # without running them, so an earlier call would still be safe; this is simply the
            # correct business boundary.
            #
            # Omitting this line was a real failure: running the scheduler on an empty queue
            # jumps straight to the end of the day and returns a world in which nobody did
            # anything. The run still finishes and still produces a result; every figure is just
            # zero, with no exception and no warning. One gate catches it.
            self.world.start_processes()
            self.state = "ACTIVE"
        else:
            pending = self.gate.pending.popleft()
            record["pause"] = pending.pause.kind.value
            if cmd.kind is CommandKind.END_SHIFT:
                # Finishing does not interrupt a trip: the engine takes the actor offline at
                # the next idle turn and the trip in progress runs to completion. The session only
                # records the intent.
                self.state = "END_REQUESTED"
            pending.evt.succeed(cmd)
        self.command_log.append(record)
        self.served[cmd.client_command_id] = record
        self.applied_count += 1

    # --- Finishing -----------------------------------------------------------------------

    def run_to_end(self, *, auto=None) -> None:
        """Run to the end of the day, answering every remaining stop automatically.

        The supplied rule decides each answer; by default it follows the instinct. Used to end a
        demonstration early, and to build goldens where the rest of the day runs as usual."""
        auto = auto or follow_instinct
        n = 0
        while True:
            p = self.step()
            if p is None:
                break
            n += 1
            if n > self.MAX_SLICES:
                raise LoopGuardTripped(f"run_to_end quá {self.MAX_SLICES} lệnh tự động")
            kind = auto(p, self.current_pending)
            res = self.command(Command(kind=kind, session_id=self.session_id,
                                       client_command_id=f"auto-{self.version}",
                                       expected_version=self.version,
                                       payload=payload_tu_dong(p, kind)))
            if not res.ok:
                raise LoopGuardTripped(
                    f"lệnh tự động {kind.value} bị chặn ở {res.layer} ({res.reason}) — luật "
                    f"`auto` sinh lệnh không hợp điểm dừng {p.kind.value}")

    def finish(self):
        """Close the day and return the run result. A second call returns the same object."""
        if not self._day_over:
            self.run_to_end()
        if not self._settled:
            self.world._settle_end_of_run()
            self._settled = True
            self.state = "COMPLETED"
            from ..runner import result_from_world
            self._result = result_from_world(self.world, self._deps)
        return self._result

    def abandon(self) -> None:
        """Discard the session. Sessions live in memory, so there is no resume path; that is stated
        rather than pretended otherwise."""
        if self.state != "COMPLETED":
            self.state = "ABANDONED"


def lenh_tu_dong(pause: PausePoint, *, nhan_cuoc: bool | None = None) -> tuple:
    """The command answering any one stop: the single source for every driving loop.

    Left unset, an offer follows the instinct; set, it forces acceptance or refusal.

    Why a function rather than a condition copied at each site: when a new stop kind was added,
    every hand-copied loop went red at once for the same reason. The next new stop would repeat
    that if the rule stayed scattered."""
    if pause.kind is PauseKind.BEFORE_SHIFT:
        return CommandKind.START_SHIFT, {}
    if pause.kind is PauseKind.OFFER:
        co = pause.hint.get("instinct_accepts") if nhan_cuoc is None else nhan_cuoc
        kind = CommandKind.ACCEPT_OFFER if co else CommandKind.DECLINE_OFFER
        return kind, payload_tu_dong(pause, kind)
    if pause.kind is PauseKind.OUT_OF_CORE:
        return CommandKind.RELOCATE, payload_tu_dong(pause, CommandKind.RELOCATE)
    if pause.hint.get("instinct") == IdleAction.END_SHIFT.value:
        return CommandKind.END_SHIFT, {}
    return CommandKind.FOLLOW_SUGGESTION, {}


def payload_tu_dong(pause: PausePoint, kind: CommandKind) -> dict:
    """The minimal payload for an automatic command at a given stop.

    The single source for every self-answering loop. Each site used to build its own payload, so
    adding a stop that took a parameter turned them all red at once."""
    if kind is CommandKind.ACCEPT_OFFER or kind is CommandKind.DECLINE_OFFER:
        return {"offer_ref": pause.offer_ref} if pause.offer_ref else {}
    if kind is CommandKind.RELOCATE:
        dich = (pause.hint or {}).get("target")
        return {"cell": dich} if dich else {}
    return {}


def follow_instinct(pause: PausePoint, pending) -> CommandKind:
    """The default automatic rule: do exactly what the instinct would."""
    if pause.kind is PauseKind.BEFORE_SHIFT:
        return CommandKind.START_SHIFT
    if pause.kind is PauseKind.OFFER:
        return (CommandKind.ACCEPT_OFFER if pause.hint.get("instinct_accepts")
                else CommandKind.DECLINE_OFFER)
    if pause.kind is PauseKind.OUT_OF_CORE:
        # Outside the core there is only one thing to do: return to the core cell the advisor
        # suggests, which is the same cell the engine chose before, so running to the end follows
        # the old path.
        return CommandKind.RELOCATE
    if pause.hint.get("instinct") == IdleAction.END_SHIFT.value:
        return CommandKind.END_SHIFT
    # Every other suggestion — relocate, swap, rest, wait — is expressible as following the
    # suggestion. Returning "continue waiting" here would force the actor to stand still, which
    # was measured as an order of magnitude more stops than expected.
    return CommandKind.FOLLOW_SUGGESTION


# =============================================================================================
# Replay
# =============================================================================================

def replay(cfg: Config, seed: int, human_actor_id: int, command_log: list[dict], *,
           filter_idle: bool = True, session_id: str | None = None):
    """Replay a session from its command log, returning the session and the run result.

    It uses the interactive gate and injects commands through the ordinary command path, so it is
    equivalent by construction rather than by promise. A fast-forward gate answering immediately
    would not yield, interleaving would differ, and the replay would produce a different world.

    Where the log runs out, because the person stopped partway, the remainder follows the
    instinct, and that is stated in the log reader so nobody takes the whole day for the person's
    own choices."""
    ses = InteractiveSimSession.create(cfg, seed, human_actor_id,
                                      session_id=session_id or "replay", filter_idle=filter_idle)
    for record in command_log:
        p = ses.step()
        if p is None:
            raise LoopGuardTripped(
                f"nhật ký còn lệnh {record['kind']} (seq {record['seq']}) nhưng sim đã hết ngày "
                f"— nhật ký không khớp cấu hình/seed đang phát lại")
        res = ses.command(Command(kind=CommandKind(record["kind"]), session_id=ses.session_id,
                                  client_command_id=f"replay-{record['seq']}",
                                  expected_version=ses.version,
                                  payload=dict(record.get("payload") or {})))
        if not res.ok:
            raise LoopGuardTripped(
                f"phát lại lệnh #{record['seq']} {record['kind']} bị chặn ở {res.layer} "
                f"({res.reason}) — nhật ký lệch với thế giới đang phát lại")
    ses.commands_from_log = len(command_log)
    return ses, ses.finish()
