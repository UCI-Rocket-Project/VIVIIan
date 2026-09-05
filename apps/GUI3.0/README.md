# GUI3.0 — UCIRPL Ops View

An operator front end implementing the `UCIRPL Ops View.dc.html` design from
Claude Design, in ImGui, against the existing control stack.

```bash
python apps/GUI3.0/run_sim.py                 # whole stack against the simulator
python apps/GUI3.0/run_sim.py --profile fail  # decay exceeds 3 psi/min
python apps/GUI3.0/main.py                    # just the view, against a running stack
```

`run_sim.py` re-runs itself under `.venv` if the interpreter it was launched with
is missing `pyarrow`, `imgui_bundle`, `glfw` or `psutil`. All four have to be in
one place: the children need the first three, and the leftover sweep needs psutil
*here*. Resolving only the children would leave the sweep silently skipped, the
previous run still holding the Flight ports, and the new stack failing on bind
with an error that says nothing about the real cause.

`main.py` has no such fallback — run it with the venv python directly.

## Relationship to GUI2.0 and GUI2.1

Neither is modified. GUI3.0 imports GUI2.1's engine, connectors, valve tables and
calibration and replaces only the rendering layer:

| Reused from `apps/GUI2.1` | What GUI3.0 does with it |
| --- | --- |
| `state_machine.py` | `Dispatcher`, `ControlContext` — drives the whole view |
| `procedures/pressure_decay.py` | the procedure; `build_machine()` unchanged |
| `procedures/operations.py` | `GseEffector`, `GseValveMap`, `attach_manual_listener` |
| `gui_gse2v1.py` | `GseCommandClient`, the command buttons, echo sync |
| `generic_connector.py` | `LatestServer` on the same Flight ports |
| `frontendv2.PT_SCALES` | imported, not copied, so calibration cannot drift |
| `captcha.py` | the 67 gate, on force-state and `requires_captcha` operations |

`main.py` puts `apps/GUI2.1` on `sys.path` and imports by name. Only two things
are adjusted at runtime, both in this process and neither touching a GUI2.1
file: `Dispatcher._log` is wrapped to mirror events into a classified log
(`events.attach`), the same technique `attach_manual_listener` already uses on
the buttons; and `run_sim.py` swaps the frontend entry in the leftover-sweep list.

Because `state_machine.py` imports no GUI code and `Effector`/`ValveMap` are
Protocols, this needed no new seam — the existing one already supported it.

## Mapping the design onto this procedure

The source design is a hotfire console. This procedure is a pressure decay test,
so several slots name something that does not exist here. Each was filled with
the equivalent real thing rather than faked:

| Design | GUI3.0 |
| --- | --- |
| Phase chip `PRESSURIZATION` | `DispatcherMode`, plus an ARMED/DISARMED chip |
| `T− 00:04:12` countdown | time in the current state — several states are held open by a watchdog measured from exactly that |
| `REC 02:11:04` | dropped: nothing here records, and GUI2.1 showed no such clock |
| Link chips `GSE 12ms` | real per-feed age for GSE, ECHO and NIDAQ, red when stale |
| `BACKEND 0.41 Mbps` | dropped: the feed chips already say whether the link is alive |
| Alarm banner | validation problems, ties, failed operations, valve mismatches, lost feeds — highest severity first, acknowledgeable |
| `SEQUENCE`, 7 phases | the machine's 23 states; DONE from `dispatcher.history`, not list position, because the procedure has a recovery branch that goes backwards |
| `ACTIVE STEP` + `COMPLETE` | current state, its description, and every guard the dispatcher is watching |
| Step check `PASS`/`WAIT` | one row per watched guard — MET or WAIT, and where each one leads — plus the state's watchdog |
| `HOLD` | dropped: the ARM tile already disarms |
| `EVENT LOG` + severity filters | `dispatcher.log`, classified into info/ok/warn/crit; the filters toggle, without a running tally |
| P&ID | the real feed system (below) |
| `TANK PRESSURES` | COPV, LOX, LNG with redlines from the procedure's own limits |
| `THRUST` headline | worst-section decay rate — the number this procedure exists to produce |
| `ENGINE & THERMAL` | VENT, ingress and pot PTs, PT 10, thrust |
| Strip charts | UPPER FEED, INGRESS & POTS, and one that follows the selected channel |
| `GSE COMMANDS` | the nine steady valves, on the Commands tab |
| `ECU COMMANDS` | MVAS open/close and the two igniters (momentary), on the Commands tab |
| `ARM`, hold 1.5 s | `dispatcher.arm()` |
| `IGNITER A·B` | `RESUME` — a decay run has no ignition, but it does have operations suspended by a manual command |
| `ABORT`, hold 1 s | `dispatcher.panic()` |

## Tabs

The top bar carries two pages, in the reference console's nav style:

- **Ops** — sequence, active state with the guards it is watching, the
  operations that state offers, the event log, P&ID, telemetry and strip
  charts. Arm, resume and abort sit in a strip along the bottom.
- **Commands** — every raw GSE and ECU command, at a size you can hit. A valve
  the board disagrees with puts a red dot on the tab, since the tiles that would
  otherwise say so are a page away.

Arm, resume and abort stay on Ops rather than moving with the valve tiles: they
are how this procedure is run and stopped, and ABORT is never a tab away.

## The P&ID

Drawn from the plumbing the procedure commands: GN2 supply, the four fill
solenoids on a parallel manifold, COPV, PV 1 and PV 2 into the LOX and LNG tanks,
the vent header, and MVAS into the engine.

- A valve is a **ring**, the way the reference console draws them: green when
  *open*, which is not the same as energised — `pv2` and `tank_vent` are
  normally open, and the glyph reflects the valve, not the solenoid.
- A valve the board **disagrees** with rings red, everywhere it appears.
- A valve the board is **not reporting** rings amber. Per
  `docs/state-machine.md` that is a third outcome, not a failure: we are blind,
  not in trouble.
- A vessel over its abort limit outlines red.
- Pipes carry the fluid: green pressurant, blue fuel, red oxidiser, grey vent.
- Every pressure tag is a bordered readout, clickable, and drives the trend
  selection.

**Assumption to confirm with prop:** the four GN2 fill solenoids are drawn as
parallel branches off a common supply header. §3 only exercises GN2 Fill 1, so
the topology of 2–4 is a guess. If they are in series, or feed different legs,
the schematic is wrong and should be corrected.

## Rendering approach

The design is a fixed dashboard, so panels paint absolute rectangles onto one
window draw list rather than stacking ImGui widgets; `draw.Painter` carries the
primitives and `model.OpsModel` is the per-frame view model (the counterpart of
`renderVals()` in the source). Letter-spacing, which the design uses on every
small-caps label and ImGui has no concept of, is drawn glyph by glyph.

Fonts resolve to the closest thing the machine has — Segoe UI Semibold for
Archivo, Cascadia Mono or Consolas for JetBrains Mono — falling back to the faces
bundled in `imgui_bundle`. Monospace is not cosmetic: a proportional face makes a
pressure readout jitter sideways as its digits change.

The layout scales uniformly from the 1920×1080 design size and is clamped so text
stays legible on a smaller window.

## Tests

```bash
python -m unittest tests.test_gui3_ops_view -v
```

ImGui renders without a GPU, so these build the real machine, drive it through
every state, and paint a complete frame each time — including cold start with no
telemetry at all, a tank over redline, a missing valve echo, an ambiguous
transition, and windows well below the design size. Absolute-rectangle drawing
means a NaN or a zero-width panel reaches the draw call with no library in
between, which is exactly what these cover.

## Known limitation

`dispatcher.tick()` runs inside the frame loop, as it does in GUI2.1. The
dispatcher self-paces to its 50 ms control period so cadence does not follow the
frame rate, but control is still *driven* by the renderer: on Windows, dragging
or resizing the window blocks `glfw.poll_events()`, and control stops until the
mouse is released. Moving the dispatcher onto its own thread would fix this and
is independent of this front end.
