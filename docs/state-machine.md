# GSE Control State Machine

Running working notes for the GSE control / state machine work.
This document should be transitioned into a proper design doc once the design is stable.

---

# 2026-07-26 Meeting

# Coming in
- converted table states to toml
- add state editor to gui
- create path-agnostic state machine

# Going out
- define states and exit conditions in python
- state machine should handle 'operations' and 'states'
- have panic button with 67 captcha
- no state editor in gui
- prop can always move between states manually in gui
- states can transition autonomously
- prop should define 'safe' exit states for abort
- handle control with a 67 captcha confirmation gate

---

## Logic is specific to each state, not global

Each state owns its own exit criteria, and the control loop only
evaluates the criteria belonging to the state it is currently in.

Example from the meeting:

- we're in `prop_load`. say we have tank fill sensors.
- every iteration of the control loop, `prop_load` checks: am I at 100% fill?
- at 100%, take the transition out to quick disconnect / hot fire.
- **once we're in the QD state, we stop checking fill level entirely.**

The math and comparisons done in one state should not run in every state.

## Dispatcher shape

Naive plan:

- a Python dispatcher object owns all the state objects prop defines
- each state object declares its exit criteria as predicates
- sensor data is exposed into the state objects so criteria can read it
  (solenoid states too, though maybe not necessary)
- dispatcher evaluates the current state's criteria; when one is met, the
  dispatcher moves to the state that criterion names

Possible refinement: a lot of this can be made functional/generic with
lambdas so you aren't manually passing state through everything.

## Transitions must name their target explicitly

If states are defined in isolation: "here is my exit condition" with no destination state, 
you get a real hazard over time. Say we build a machine
with 10 states today, then add more tomorrow without rigorously checking each
pair. Now there are reachable paths nobody ever vetted, and some of them are
dangerous.

Not decided: should the other end also be explicit should a
state declare which states it is allowed to be entered *from*? Arguments both
ways; call it once the procedure shapes are clearer.

## Vocabulary: states vs steps vs operations

"State" is overloaded and it's causing confusion. Working split:

- **steps / operations** — things in the procedure that we *do*. Reusable. The
  same operation might be run 20 times across a test.
- **states** — conditions the system is *in*, between operations.
- an operation takes the system from state A to state B. Different operations can
  make the same A→B move, depending on which procedure we're following.
- there is a **safe state**, from which you can go anywhere.

## Determinism Chcker

Build after the state machine logic works.

**You must never end a control cycle with two equally valid transitions
available.** The computer must never pick a state because it happened to
evaluate first in a for loop. That is how you cause a large amount of problems.

Possible resolutions:
- **priority**: if the cycle produced both a hot fire transition and an abort
  transition, abort wins. Anything equally likely does not
  happen.
- **manual input**: if two are valid and neither dominates, stop and
  ask.

Also only evaluate transitions at the end of the control cycle.
We don't care what's momentarily true.

## Most transitions are automatic

Some transitions need a human, but most
will not, because states are going to be small like this example:

> open this valve until pressure is at a certain point, then immediately open the
> next one

So the machine needs sensor-driven transitions, not just a Next
button.

## First implementation target: Pressure Decay Test

Exit state can be arbitrary placeholder for now, just use ALL OFF.

Procedure doc:
<https://docs.google.com/document/d/1kGY1s4ODOznzf9VR1rpAl6Mk8TwQzJjOZ2U9wVmrtfo/edit>

## Safe state

We don't have a separately defined safe state yet, and for now we probably don't
need one because the system is designed to fail safe, so ALL OFF is
effectively the safe state. Use it as a placeholder until prop defines
something specific.

## Manual control

Always follow these two rules:

1. **Never take manual control away.**
2. **Make sure manual control is never dangerous.**

So manual actions probably need to be modelled, not just allowed. Either:
- implement a manual action as an ordinary exit transition, or
- implement it as an **interrupt** that suspends an automation without resetting
  it, so you can do a thing and then resume

Undecided, and it's a question for prop rather than for us see open questions.

The operator must be able to do anything, even something the
system believes is unsafe. The reasoning is that we can only encode hazards we
already know about. If the tanks are doing something weird the software must not be the thing that
prevents us.

The key is to make manual overrides difficult, and in the meeting we discussed a "67 captcha"
gate to force the operator to stop and think before doing a dangerous manual action.

## Panic button

This is distinct from the current abort, because it may not always be the best option for every state.
Probably means each state (or each system state) declares its own safe-out.

## 67 captcha

The confirmation gate for forcing a dangerous manual action. Don't just ask for `67`,
display an expression that evaluates to 67 (e.g. `67 × 10^n` with a random n,
so it changes every time and can't be memorized).

Build it, but don't necessarily wire it up yet.

## Prop writes Python

Keep everything in Python. Prop can learn Python.

The expectation is that eventually the vocabulary of things we need turns out to
be small and at that point it could move back into toml config files.

Idea for when we get there: **Mako** (Python templating library) for
generating lots of similar state/config files from criteria instead of
copy-pasting them. Told "don't worry about this right now."

## GUI Scope

- Buttons on `frontendv2` for whole operations

## A command is not a command until the board says so

Found while running the sim against the state machine: the abort table's two
vent-opens were reaching the board and then being taken away again.

`sync_gse2v1_command_buttons_from_echo` rewrites every button from the board's
command echo each frame, with `GSE2V1_ECHO_GRACE_SECONDS` (0.5s) of protection
after a send. That grace is a bet that the round trip beats a timer, and it
does not always: a command acknowledged at t=0.8 spends 0.3s looking like the
board saying "off", so the sync reverts the button and undoes the command.
Survivable for a fill solenoid, not survivable for the abort table.

The echo now only speaks for a field once it agrees with what we asked for
(`GseCommandClient.awaiting_echo`), bounded by `_pending_until` so a command the
board genuinely refuses still surfaces rather than pinning the button. Covered
by `tests/test_gse2v1_commands.py`.

---

# Action items from the follow-up meeting

Six items came out of the meeting. All six are in. Notes on what each one
turned into, and where it lives.

## Abort on a state mismatch

A valve disagreeing with `State.expected_state` means the system is not where
the procedure thinks it is, which is worse than most of what we could hit
downstream. So it now runs that state's safe-out instead of printing a warning
and carrying on.

`State.on_mismatch` is a `MismatchPolicy`, defaulting to `ABORT`. `WARN` is the
old behaviour if some state wants it, `IGNORE` skips the check entirely.

There is a grace period (`State.mismatch_grace_s`) before the check bites,
because valves take time to move and checking on arrival would abort on entry
to every state.

**Only while armed.** Disarmed means the operator is hand-flying, and in
`PD_00_ALL_OFF` — which pins all nine steady valves — touching any raw valve
button would otherwise slam the abort table onto someone who never armed
anything. That is rule 1 above being broken by the safety feature. Disarmed,
a mismatch shows in the panel and says what arming would do.

**`PD_ABORTED` is `WARN`, not `ABORT`.** Its safe-out is the abort table, which
is the state it is already in, so aborting on a mismatch there panics into
itself once a cycle forever. `validate()` now rejects that shape, and
`_panic_now` halts rather than repeating a panic whose destination is the
current state.

## Explicit start state

`start=True` on the state the procedure begins from. `Machine.build` finds it;
two of them or none of them is a `StartStateError` rather than a machine that
quietly starts wherever. `pressure_decay.py` also names `START_STATE` in the
build call, so the flag and the name have to agree.

## Better names in the procedures

Thresholds now say what they limit instead of restating their own value:
`COPV_FILL_200_PSI` became `SYSTEM_CHARGE_TARGET_PSI`, and so on. `_Latch` is
`OneShotLatch`, `eff` is `effector` everywhere, and the valve configurations
that were being spelled out repeatedly are now `TANKS_PRESSURIZING` and
`GSE_VENTED`.

## Don't care in an expected state

`DONT_CARE` for a valve the procedure genuinely does not pin, built with
`table_states.unchecked(table, "valve_a")`.

It raises on `bool()`, deliberately. `bool(DONT_CARE)` would quietly be `True`,
and `True` means "expect open" - a wrong assertion about a valve that nobody
would spot in review.

Still never leave a valve out of a table: an absent key means "expect closed"
and means it.

## Operation feedback, and lead time

An operation is now four steps rather than one: actions, lead time, destination
check, transition.

`Operation.lead_time_s` is how long we wait between the last command and
believing the board about it - 0.75 s by default, 1.5 s for whole-table
commands since those move several valves at once.

`Operation.feedback` carries the phase, duration, both hashes, the mismatches
and anything the board is not reporting. The dispatcher keeps `last_feedback`
and, separately, `last_failure` - a failure is immediately followed by a panic,
and the panic's own tidy "done" would otherwise be the only thing on screen.

## Runtime timing

The ImGui frame loop is uncapped, but the dispatcher runs at a fixed 50 ms
period (`CONTROL_PERIOD_SECONDS`) so control behavior does not vary with render
rate. The default decay measurement window is 180 s
(`DECAY_WINDOW_SECONDS`); `run_sim.py` shortens it for walkthroughs.

The 10 s settle window (`SETTLE_WINDOW_SECONDS`) is intentionally distinct from
the decay window. It identifies an ended fill transient, while the decay window
is reset at the start of the measurement and is used only for the decay result.

After staging an action, an operation waits 0.75 s by default
(`DEFAULT_LEAD_TIME_SECONDS`) before checking the board. The same grace applies
after startup and a forced state. A stale GSE feed has a further 5 s
(`VERIFY_FEED_GRACE_SECONDS`) to recover during destination verification; this
distinguishes a temporary loss of visibility from evidence that a valve failed
to move.

## Control invariants

An **action** is one atomic command or wait. An **operation** is a reusable
sequence of actions with an explicit destination state. A **state** is the
stable condition between operations and owns the exit criteria evaluated while
the machine is in it. Hardware and ImGui integration live behind the
`Effector` and `ValveMap` adapters; the engine does not import button code.

Each control cycle reads a single snapshot. Missing or stale sensor data is
represented as `NaN` or `None`, never zero. Guards must therefore be written
positively: `slope <= 3.0` safely evaluates false for `NaN`, while
`not (slope > 3.0)` would incorrectly pass. A state with automatic exits must
have a `max_seconds` watchdog because a guard reading `NaN` may never fire.

An operation is complete only after its actions, settling time, and destination
table check succeed. The table check compares the valves pinned by the
destination state with the board report; disagreement fails the operation and
triggers its safe-out. `DONT_CARE` is the explicit way to leave a valve
unchecked; omitted table keys mean closed.

## Pressure-decay assumptions

The current procedure uses a 300 psig vent-recovery target and considers a
section settled when every monitored pressure changes by at most 10 psi/min over
the 10 s settle window. These are engineering assumptions, not values specified
by the source procedure. `GSE_VENTED_HOLD` also deliberately leaves GN2 VV and
GN2 Fill 1 unchecked after the GSE is vented, because the procedure does not
specify their required hold positions. The open questions below track each of
these choices.

## Operation hash check

At the end of the lead time: hash the valves the destination state pins, hash
what the board reports, compare. A difference fails the operation, and a failed
operation panics.

A valve the board is not reporting is a third outcome, not a failure. If the
GSE feed has gone stale we are blind, not in trouble — the valves have not
moved, we just cannot see them — so the operation holds in `VERIFYING` for up
to `VERIFY_FEED_GRACE_SECONDS` and carries on if the feed returns. Only a
healthy feed with a missing status field fails immediately, because that is a
wiring fault rather than a blip. The first sim run panicked a good procedure on
a two-second telemetry gap during a manual gate that moved no valves at all.

`verify_dest=False` opts out, and every abort path uses it: an abort has to
land, and a failed check could only abort again.

`Machine.validate()` runs the same comparison at build time, predicting where
an operation's actions would leave the valves and checking that against the
state it claims to enter. Same mistake, found before anyone arms the machine
rather than at 350 psig.

---

# Questions for Prop

Ask in the campfire:

1. How should manual control behave during an automation? Should pressing a
   manual button abandon the running operation, or interrupt-and-resume it
   without resetting?
2. Do you want a defined safe state per operation, or is ALL OFF good enough
   as a universal safe-out? ALL OFF and ABORT are not the
   same table: ABORT opens the GN2 and COPV vents and closes MVAS, ALL OFF
   leaves those two vents shut. Panic applies ABORT today, but these notes
   call ALL OFF the safe state.
3. Longer term: which operations are reusable across procedures, and are there
   any that should only be callable from specific system states?
4. Where do GN2 VV and GN2 Fill 1 sit through the decay measurement? §3 step 16
   opens both and §3 never mentions them again, so they are currently
   unchecked rather than asserted at a position we guessed. See
   `GSE_VENTED_HOLD` in `procedures/pressure_decay.py`.
5. How long does a solenoid actually take to move and report back? The lead
   time before we believe the board is 0.75 s, which is a guess. Too short and
   good operations start failing their own destination check.
6. §3 step 11.2 says to repressurise the vent line but gives no target.
   `VENT_RECOVERY_TARGET_PSI` is 300 psig, picked to sit clear of the 150 psig
   floor that triggers the fallback. What should it be?
7. §3 step 12 says to read the PTs "once pressure has stabilized" without
   saying what that means. `PRESSURE_SETTLE_LIMIT_PSI_PER_MIN` is 10 psi/min
   over a 10 s window — looser than the 3 psi/min decay criterion, since this
   is "the fill transient is over" rather than "the system does not leak".
8. The decay criterion is currently unsigned: `worst_slope` takes a magnitude,
   so a section *climbing* faster than 3 psi/min fails the test as well as one
   falling. §3 step 15 only says "lose". A rise during the hold, with GN2 VV
   and GN2 Fill 1 open, looks like something wrong rather than something to
   pass — but it is a deliberate reading of the procedure rather than what it
   literally says. Confirm or drop it.
