"""Operator panel for the state machine.

Shows the state the machine is in, the criteria it is watching with their live
values, the operations available *from this state*, how the last operation
actually went, and the decay result.
"""
from __future__ import annotations

import math

from captcha import Captcha67Gate
from gui_elements import (
    BUTTON_STATE_MISMATCH_COLOR,
    BUTTON_STATUS_ON_COLOR,
    COLOR_WHITE,
)
from state_machine import (
    Dispatcher,
    DispatcherMode,
    MismatchPolicy,
    OpPhase,
    Operation,
    safe_guard,
)

COLOR_DIM = (0.62, 0.62, 0.66, 1.0)
COLOR_ALERT = (0.95, 0.35, 0.30, 1.0)
COLOR_OK = BUTTON_STATUS_ON_COLOR
COLOR_WARN = BUTTON_STATE_MISMATCH_COLOR

LOG_VISIBLE_LINES = 8


def _fmt(value: float, unit: str = "") -> str:
    if value is None or not math.isfinite(value):
        return "--"
    return f"{value:.1f}{unit}"


class StateMachinePanel:
    def __init__(self, dispatcher: Dispatcher, *, decay_sections: tuple[str, ...] = ()) -> None:
        self.dispatcher = dispatcher
        self.decay_sections = decay_sections
        self.captcha = Captcha67Gate()
        self.show_log = True

    # -- helpers ------------------------------------------------------------

    def _text(self, imgui, color, text: str) -> None:
        imgui.text_colored(imgui.ImVec4(*color), text)

    def _tooltip(self, imgui, text: str) -> None:
        if text and imgui.is_item_hovered():
            imgui.set_tooltip(text)

    def _run(self, op: Operation) -> None:
        if op.requires_captcha:
            self.captcha.require(
                f"{op.name}\n\n{op.description}",
                lambda: self.dispatcher.request(op),
            )
        else:
            self.dispatcher.request(op)

    # -- sections -----------------------------------------------------------

    def _header(self, imgui) -> None:
        d = self.dispatcher
        imgui.text_unformatted(f"procedure: {d.machine.name}")

        imgui.same_line()
        mode_color = {
            DispatcherMode.IDLE: COLOR_DIM,
            DispatcherMode.RUNNING: COLOR_OK,
            DispatcherMode.SUSPENDED: COLOR_WARN,
            DispatcherMode.AMBIGUOUS: COLOR_ALERT,
            DispatcherMode.HALTED: COLOR_ALERT,
        }[d.mode]
        self._text(imgui, mode_color, f"[{d.mode.value}]")

        self._text(imgui, COLOR_WHITE, f"state: {d.current.name}")
        imgui.same_line()
        self._text(imgui, COLOR_DIM, f"({d.ctx.in_state_for():.0f}s)")
        if d.current.description:
            self._text(imgui, COLOR_DIM, d.current.description)

    def _controls(self, imgui) -> None:
        d = self.dispatcher

        if d.armed:
            if imgui.button("Disarm automation"):
                d.disarm()
        else:
            if imgui.button("Arm automation"):
                d.arm()
            self._tooltip(
                imgui,
                "Automatic transitions only run while armed. Manual operations and the raw "
                "valve buttons work either way.",
            )

        imgui.same_line()
        if imgui.button("PANIC"):
            d.panic()
        self._tooltip(imgui, "Applies this state's safe-out: abort configuration, alarm on.")

        if d.mode is DispatcherMode.SUSPENDED:
            imgui.same_line()
            if imgui.button("Resume"):
                d.resume()
            self._tooltip(imgui, "Continue the suspended operation from where it stopped.")

        if not d.ctx.healthy():
            snap = d.ctx.snap
            missing = [
                name
                for name, ok in (
                    ("gse", snap.gse_fresh),
                    ("echo", snap.echo_connected),
                    ("nidaq", snap.nidaq_fresh),
                )
                if not ok
            ]
            self._text(imgui, COLOR_WARN, f"feeds not healthy: {', '.join(missing)}")

    def _problems(self, imgui) -> None:
        for problem in self.dispatcher.problems:
            self._text(imgui, COLOR_ALERT, f"VALIDATE: {problem}")

    def _mismatches(self, imgui) -> None:
        d = self.dispatcher
        if not d.warnings:
            return
        will_abort = d.current.on_mismatch is MismatchPolicy.ABORT and d.armed
        color = COLOR_ALERT if will_abort else COLOR_WARN
        for item in d.warnings:
            self._text(
                imgui,
                color,
                f"mismatch: {item.button_id} expected {item.expected}, "
                f"board reports {item.actual}",
            )
        if will_abort:
            self._text(imgui, COLOR_ALERT, f"  {d.current.name} aborts on a valve mismatch")
        elif d.current.on_mismatch is MismatchPolicy.ABORT:
            self._text(imgui, COLOR_DIM, "  disarmed, so this only warns; arming here would abort")

    def _active_operation(self, imgui) -> None:
        d = self.dispatcher
        op = d.active_op
        if op is None:
            return
        index, total = op.progress()
        if op.phase is OpPhase.ACTING:
            action = op.current_action()
            detail = action.describe() if action is not None else ""
            self._text(imgui, COLOR_OK, f"running: {op.name}  [{index}/{total}]  {detail}")
            return
        detail = op.feedback.detail if op.feedback is not None else ""
        color = COLOR_WARN if op.phase is OpPhase.SETTLING else COLOR_OK
        self._text(imgui, color, f"running: {op.name}  [{op.phase.value}]  {detail}")

    def _feedback(self, imgui) -> None:
        """How the last operation went.

        A failure wins over the most recent report, because a failure is
        immediately followed by a panic and the panic's own tidy "done" would
        otherwise be the only thing on screen.
        """
        d = self.dispatcher
        feedback = d.last_failure or d.last_feedback
        if feedback is None:
            return
        color = COLOR_ALERT if feedback.phase is OpPhase.FAILED else COLOR_DIM
        self._text(imgui, color, f"last: {feedback.summary()}")
        for item in feedback.mismatches:
            self._text(
                imgui,
                COLOR_ALERT,
                f"  {item.button_id}: commanded {item.expected}, board reports {item.actual}",
            )
        if feedback.unreadable:
            self._text(
                imgui,
                COLOR_ALERT,
                f"  no board feedback: {', '.join(feedback.unreadable)}",
            )
        if feedback.verified is False:
            self._text(
                imgui,
                COLOR_DIM,
                f"  table hash: expected {feedback.expected_hash}, "
                f"read {feedback.observed_hash}",
            )

    def _tie(self, imgui) -> None:
        d = self.dispatcher
        if d.mode is not DispatcherMode.AMBIGUOUS:
            return
        self._text(
            imgui,
            COLOR_ALERT,
            "Two transitions are equally valid. The machine will not choose for you.",
        )
        for op in d.tie_candidates:
            if imgui.button(f"{op.name} -> {op.dest_name()}##tie_{op.name}"):
                d.choose(op)

    def _operations(self, imgui) -> None:
        d = self.dispatcher
        manual = d.current.manual_operations()
        if manual:
            imgui.text_unformatted("operations")
        for op in manual:
            enabled = d.mode in (DispatcherMode.IDLE, DispatcherMode.HALTED)
            if not enabled:
                imgui.begin_disabled()
            if imgui.button(f"{op.name}##op_{op.name}"):
                self._run(op)
            if not enabled:
                imgui.end_disabled()
            self._tooltip(imgui, f"{op.description}\n\n-> {op.dest_name()}" if op.description else f"-> {op.dest_name()}")

    def _criteria(self, imgui) -> None:
        d = self.dispatcher
        auto_ops = [op for op in d.current.operations if op.auto]
        watched = list(d.machine.global_transitions) + auto_ops
        if not watched:
            return
        imgui.text_unformatted("watching")
        for op in watched:
            met = safe_guard(op.guard, d.ctx, f"panel:{op.name}")
            color = COLOR_OK if met else COLOR_DIM
            text = op.guard_text or op.name
            self._text(imgui, color, f"  {'MET' if met else '   '}  {text}  -> {op.dest_name()}")
            self._tooltip(imgui, op.description)

        if d.current.max_seconds is not None:
            remaining = d.current.max_seconds - d.ctx.in_state_for()
            self._text(imgui, COLOR_DIM, f"  watchdog: {remaining:.0f}s remaining")

    def _decay(self, imgui) -> None:
        from procedures.pressure_decay import DECAY_RESULT

        d = self.dispatcher
        if not self.decay_sections:
            return

        imgui.text_unformatted("decay (psi/min, positive = losing pressure)")
        ready = d.ctx.slope_ready()
        for section in self.decay_sections:
            live = d.ctx.decay_psi_per_min(section)
            psi = d.ctx.psi(section)
            recorded = DECAY_RESULT.get(section)
            line = f"  {section:<9} {_fmt(psi, ' psi'):>12}   decay {_fmt(live):>7}"
            if isinstance(recorded, float):
                line += f"   recorded {_fmt(recorded)}"
            self._text(imgui, COLOR_WHITE if ready else COLOR_DIM, line)

        if not ready:
            self._text(
                imgui,
                COLOR_DIM,
                f"  window filling: {d.ctx.slopes.span_seconds():.0f}s "
                f"of {d.ctx.slopes.window_seconds:.0f}s",
            )

        verdict = DECAY_RESULT.get("verdict")
        if verdict:
            self._text(imgui, COLOR_OK if verdict == "PASS" else COLOR_ALERT, f"  verdict: {verdict}")

    def _log(self, imgui) -> None:
        if not self.show_log:
            return
        lines = list(self.dispatcher.log)[-LOG_VISIBLE_LINES:]
        for line in lines:
            self._text(imgui, COLOR_DIM, line)

    # -- entry point --------------------------------------------------------

    def render(self, imgui) -> None:
        self._header(imgui)
        self._problems(imgui)
        self._controls(imgui)
        self._mismatches(imgui)
        self._active_operation(imgui)
        self._feedback(imgui)
        self._tie(imgui)
        imgui.spacing()
        self._operations(imgui)
        imgui.spacing()
        self._criteria(imgui)
        imgui.spacing()
        self._decay(imgui)
        imgui.spacing()
        self._log(imgui)
        self.captcha.render(imgui)
