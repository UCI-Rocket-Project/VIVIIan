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

Candidate (explicitly "thinking out loud", not a decision): an operation might
declare it can only be invoked from a certain *type* of state. Worth considering
once we've read the real procedures more carefully.

## determinism checker — build this immediately after the machine itself

Called out as the next thing to build after the state machine logic works.

**You must never end a control cycle with two equally valid transitions
available.** The computer must never pick a state because it happened to
evaluate first in a for loop. That is how you cause a large amount of problems.

Ways out, to pick from:
- **hierarchy** — if the cycle produced both a hot fire transition and an abort
  transition, abort wins, unconditionally. Anything equally likely does not
  happen.
- **operator input** — if two are valid and neither dominates, stop and
  ask.

Also: **only evaluate transitions at the end of the control cycle.** Mid-cycle,
we don't care what's momentarily true.

## most transitions are automatic, not manual

Correction to my earlier assumption. Some transitions need a human, but **most
will not**, because states are going to be small and mechanical:

> open this valve until pressure is at a certain point, then immediately open the
> next one

So the machine needs real sensor-driven transitions, not just a guarded Next
button.

## first implementation target: §3 Pressure Decay Test

Recommended starting point, because it exercises all four ingredients at once:

|         ingredient        | example from §3 |
|---------------------------|-----------------|
|     **preconditions**     | verify valve states are ALL OFF before starting |
|      **manual steps**     | bottle operator opens the regulator; crew sprays soap and checks for leaks — physically unverifiable in code, needs an operator button |
| **automatic transitions** | close GN2 Fill 1 once COPV PT reaches 350 psig, then immediately move on |
|     **postconditions**    | expected valve states *and* expected pressures at the end (Tables 14/15) |

(The transcript calls the automatic one "step 3.8"; in the template I read it's
the "closes GN2 Fill 1 once 350 psig has been attained on COPV PT" step. Worth
confirming we're numbering the same copy.)

Exit/reward state can be arbitrary placeholder for now — just use ALL OFF.

Procedure doc:
<https://docs.google.com/document/d/1kGY1s4ODOznzf9VR1rpAl6Mk8TwQzJjOZ2U9wVmrtfo/edit>

## safe state

We don't have a separately defined safe state yet, and for now we probably don't
need one: **the system is designed to fail safe, normally off**, so ALL OFF is
effectively the safe state. Use it as the placeholder until prop defines
something specific.

Worth recording because it lines up with what the valve polarity actually does:
ALL OFF de-energizes every solenoid, and since PV 2 and the LOX/LNG vent valves
are plumbed normally-open, de-energizing leaves the vents open. The safe state
falls out of the plumbing, not out of software.

## manual control

Two rules that pull against each other, and both hold:

1. **never take manual control away.**
2. **make sure manual control is never dangerous.**

So manual actions probably need to be modelled, not just allowed. Either:
- implement a manual action as an ordinary exit transition, or
- implement it as an **interrupt** that suspends an automation without resetting
  it, so you can do a thing and then resume

Undecided, and it's a question for prop rather than for us — see open questions.

Underneath this: **the operator must be able to do anything, even something the
system believes is unsafe.** The reasoning is that we can only encode hazards we
already know about. If the tanks are doing something weird — say prop filled them
differently and we now have to run some unusual vent procedure because we're
flowing LOX where fuel should be — the software must not be the thing that
prevents us.

The answer is not to forbid it, it's to make it *hard*: gate it behind a
deliberate multi-step confirmation. Reference point mentioned: Starship operators
reportedly go through several prompts to force an action — unlock, type the
value, confirm the username, "are you sure", type the hostname.

## panic button

The abort button today does exactly one thing (applies one fixed valve table),
and **that fixed action may itself be dangerous depending on which state we're
in**. What's wanted is a panic control that resolves to a *state-appropriate*
safing action.

This is distinct from the current abort. Probably means each state (or each
system state) declares its own safe-out.

## 67 captcha

The confirmation gate for forcing a dangerous manual action. Escalated version
from the meeting: don't just ask for `67` — display an expression that evaluates
to 67 (e.g. `67 × 10^n` with a random n, so it changes every time and can't be
memorized) and make the operator type its square root. Forces them to actually
stop and get a calculator.

Status: explicitly acknowledged as *probably a bad idea to actually use*. Wanted
mainly so it can be demoed at recruiting. Build it, don't necessarily wire it to
anything real.

## python, not toml (for now)

Keep everything in Python. Prop can learn Python — not a problem.

The expectation is that eventually the vocabulary of things we need turns out to
be small — control tank pressures, vent them if they get too high, read some
sensor values — and at that point it could move back into config files. Not yet.

Deferred idea for when we get there: **Mako** (Python templating library) for
generating lots of similar state/config files from criteria instead of
copy-pasting them. Explicitly "don't worry about this right now."

## gui scope

- **yes**: buttons on `frontendv2` for whole operations — e.g. a "Pressure Decay"
  button that runs that entire sequence. These replace the current per-table
  state buttons.
- **no**: a state editor in the GUI. Explicitly out of scope — "a fuck ton of
  work" for something we'd nearly never use.
- **yes**: manual valve control stays, always.

---

# open questions to take to prop

Ask in the campfire, framed as "I'm implementing this, here's what I'm thinking":

1. **How should manual control behave during an automation?** Should pressing a
   manual button abandon the running operation, or interrupt-and-resume it
   without resetting? This is the big one.
2. **Do you want a defined safe state per operation**, or is ALL OFF good enough
   as a universal safe-out?
3. **Are the §3 step numbers stable** between the template and the per-test
   instances, so we can reference them in code comments?
4. Longer term: which operations are reusable across procedures, and are there
   any that should only be callable from specific system states?

---

# impact on what's already built (GUI2.1 sequence work)

Recording this so nobody assumes the current implementation is the
target design. The meeting supersedes several decisions in it.

**Superseded:**

- *TOML sequence format* — states move into Python. The TOML loader/dumper may
  come back later; it is not the near-term shape.
- *GUI sequence editor* (`sequence_ui.SequencePanel.draw_editor`) — explicitly
  out of scope. Should be removed rather than maintained.
- *"no auto-advance, no sensor thresholds"* — reversed. Most transitions are
  sensor-driven and automatic. The `advance_when` hook I left room for is now the
  main path, not an optional extra.
- *ordered list with a single implicit next* — replaced by explicit named
  transition targets, potentially several per state, plus a determinism check.
- *abort as one fixed table* — replaced by per-state safing (panic button).

**Worth keeping:**

- the **valve registry** and polarity handling (`_VALVE_KINDS`, normally-open
  handling for `pv2` / `tank_vent`) — this is physical fact, not design
- **echo confirmation** (`EchoSnapshot`, `read_echo_snapshot`) — still exactly how
  you verify a commanded state actually took effect, and now doubles as the input
  to postcondition checks
- the **three valve categories** — latching solenoids are confirmable, MVAS and
  igniters are pulse and are not, alarm is an annunciator with its own rules
- **`apply_valve_state`** and the alarm semantics (apply only when declared, never
  force off) — including the fix for abort not sounding the alarm
- **edge-triggered send + grace window** — commands go once per transition, and
  you can't judge the echo for ~0.5 s after sending
- the **fake-board test harness** — full loop with no hardware, this is how the
  new machine gets tested too
- the insight that **ALL OFF is the safe state** because the plumbing is fail-safe

**Design note carried forward:** the current machine is a Moore machine — output
is a function of the state alone, so re-entering a state issues identical
commands. Worth preserving. It makes transitions idempotent, which matters a lot
once transitions fire automatically.
