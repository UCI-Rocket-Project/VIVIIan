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

Exit/reward state can be arbitrary placeholder for now, just use ALL OFF.

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

---

# Questions for Prop

Ask in the campfire:

1. How should manual control behave during an automation? Should pressing a
   manual button abandon the running operation, or interrupt-and-resume it
   without resetting?
2. Do you want a defined safe state per operation, or is ALL OFF good enough
   as a universal safe-out?
4. Longer term: which operations are reusable across procedures, and are there
   any that should only be callable from specific system states?
