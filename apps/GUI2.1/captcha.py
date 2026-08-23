"""The 67 captcha: a confirmation gate for dangerous manual actions.

From docs/state-machine.md — the point is to make an override *difficult*, not
impossible. The operator must always be able to do the thing; they just have to
stop and think first.

The challenge is never a bare "type 67". It displays an expression derived from
67 with a random exponent, so the answer changes every time and cannot become
muscle memory.

Built and available, wired only to ``force_state`` and operations that set
``requires_captcha``. Per the doc: build it, don't necessarily wire it up yet.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Challenge:
    prompt: str
    answer: int

    def accepts(self, entry: str) -> bool:
        text = entry.strip().replace(",", "").replace(" ", "")
        if not text:
            return False
        try:
            return int(text) == self.answer
        except ValueError:
            return False


def generate(rng: random.Random | None = None) -> Challenge:
    """A randomized expression whose value derives from 67."""
    rng = rng or random.Random()
    form = rng.randrange(4)
    n = rng.randint(1, 4)

    if form == 0:
        return Challenge(f"67 x 10^{n}", 67 * 10**n)
    if form == 1:
        return Challenge(f"6700 / 10^{n}" if n <= 2 else f"6700 / 10^2", 6700 // 10 ** min(n, 2))
    if form == 2:
        k = rng.randint(2, 9)
        return Challenge(f"67 x {k}", 67 * k)
    k = rng.randint(11, 99)
    return Challenge(f"(67 + {k}) - {k}", 67)


class Captcha67Gate:
    """Modal confirmation gate. One instance per panel."""

    MODAL_ID = "##captcha67"

    def __init__(self) -> None:
        self.challenge: Challenge | None = None
        self.reason = ""
        self.entry = ""
        self.on_confirm: Callable[[], None] | None = None
        self._open_requested = False

    @property
    def pending(self) -> bool:
        return self.challenge is not None

    def require(self, reason: str, on_confirm: Callable[[], None]) -> None:
        self.challenge = generate()
        self.reason = reason
        self.entry = ""
        self.on_confirm = on_confirm
        self._open_requested = True

    def cancel(self) -> None:
        self.challenge = None
        self.reason = ""
        self.entry = ""
        self.on_confirm = None
        self._open_requested = False

    def render(self, imgui) -> None:
        if self.challenge is None:
            return

        if self._open_requested:
            imgui.open_popup(self.MODAL_ID)
            self._open_requested = False

        # imgui_bundle returns either a bool or a (visible, p_open) pair here
        # depending on binding version; accept both.
        result = imgui.begin_popup_modal(self.MODAL_ID, None)
        opened = result[0] if isinstance(result, tuple) else result
        if not opened:
            return

        imgui.text_unformatted("Confirm dangerous action")
        imgui.separator()
        imgui.text_wrapped(self.reason)
        imgui.spacing()
        imgui.text_unformatted(f"Type the value of:   {self.challenge.prompt}")
        changed, self.entry = imgui.input_text("##captcha_entry", self.entry)

        correct = self.challenge.accepts(self.entry)
        if imgui.button("Confirm") and correct:
            confirm = self.on_confirm
            self.cancel()
            imgui.close_current_popup()
            if confirm is not None:
                confirm()
            imgui.end_popup()
            return
        imgui.same_line()
        if imgui.button("Cancel"):
            self.cancel()
            imgui.close_current_popup()
            imgui.end_popup()
            return
        if self.entry and not correct:
            imgui.text_unformatted("incorrect")

        imgui.end_popup()
