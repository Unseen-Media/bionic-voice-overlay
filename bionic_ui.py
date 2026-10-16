"""Presses Bionic's composer buttons through Windows UI Automation.

Nothing here moves the mouse or types: buttons are pressed with accessibility
actions, so Bionic does not need focus and the user's cursor is left alone.
Controls are found by their labels in every UI language Bionic has (see labels.py).
"""
import time

import uiautomation as auto
from uiautomation.uiautomation import _AutomationClient

import labels as bionic_labels

TREE_SCOPE_DESCENDANTS = 4
BUTTON = auto.ControlType.ButtonControl
EDIT = auto.ControlType.EditControl


class BionicError(Exception):
    """A problem worth showing to the user."""


def _clean(text):
    return (text or "").replace("​", "").replace("﻿", "").strip()


class BionicUI:
    """Must be created and used on one thread that has UI Automation initialized."""

    def __init__(self, find_window, labels=None):
        self.find_window = find_window
        self.uia = _AutomationClient.instance().IUIAutomation
        self.L = labels or bionic_labels.english()  # key -> the label in every language
        self.mic_names = [n for key in ("start", "stop", "setup", "ask_permission", "denied") for n in self.L[key]]

    def _is(self, name, key):
        return name in self.L[key]

    # ---- lookup ---------------------------------------------------------------

    def _window(self):
        hwnd = self.find_window()
        if not hwnd:
            raise BionicError("Bionic isn't open.")
        return self.uia.ElementFromHandle(hwnd)

    def _any_name(self, names):
        """A condition matching any of `names`.

        Built as a balanced tree: UI Automation fails with E_UNEXPECTED on deeply nested
        conditions, and a simple chain over every language's labels gets ~55 levels deep.
        """
        if len(names) == 1:
            return self.uia.CreatePropertyCondition(auto.PropertyId.NameProperty, names[0])
        mid = len(names) // 2
        return self.uia.CreateOrCondition(self._any_name(names[:mid]), self._any_name(names[mid:]))

    def _find(self, root, names, control_type=None, visible_only=True):
        cond = self._any_name(list(names))
        if control_type is not None:
            cond = self.uia.CreateAndCondition(
                cond, self.uia.CreatePropertyCondition(auto.PropertyId.ControlTypeProperty, control_type))
        found = root.FindAll(TREE_SCOPE_DESCENDANTS, cond)
        out = []
        for i in range(found.Length if found else 0):
            el = found.GetElement(i)
            r = el.CurrentBoundingRectangle
            if not visible_only or (not el.CurrentIsOffscreen and r.right > r.left):
                out.append(el)
        return out

    def _composer(self, wait=4.0):
        """(window, mic button) of the visible chat composer.

        Chromium builds its accessibility tree lazily on first use, so the
        first lookups after a while can come back empty; retry briefly.
        """
        deadline = time.time() + wait
        while True:
            win = self._window()
            mics = self._find(win, self.mic_names, BUTTON)
            if mics:
                return win, max(mics, key=lambda el: el.CurrentBoundingRectangle.bottom)
            if time.time() > deadline:
                raise BionicError("Bionic's chat box isn't on screen. Open a chat in Bionic "
                                  "(not Settings), then try again.")
            time.sleep(0.3)

    def _same_row(self, win, mic, names):
        m = mic.CurrentBoundingRectangle
        row = [el for el in self._find(win, names, BUTTON) if abs(el.CurrentBoundingRectangle.top - m.top) < 24]
        return row[0] if row else None

    def _edit(self, win, mic):
        """The composer text box: the edit field just above the mic button."""
        m = mic.CurrentBoundingRectangle
        best, best_gap = None, None
        cond = self.uia.CreatePropertyCondition(auto.PropertyId.ControlTypeProperty, EDIT)
        found = win.FindAll(TREE_SCOPE_DESCENDANTS, cond)
        for i in range(found.Length if found else 0):
            el = found.GetElement(i)
            r = el.CurrentBoundingRectangle
            if el.CurrentIsOffscreen:
                continue
            if r.left <= m.left <= r.right + 60 and r.bottom <= m.top + 8:
                gap = m.top - r.bottom
                if best_gap is None or gap < best_gap:
                    best, best_gap = el, gap
        return best

    def _mic_name(self):
        _, mic = self._composer()
        return mic.CurrentName

    def composer_text(self):
        win, mic = self._composer()
        edit = self._edit(win, mic)
        if edit is None:
            return ""
        try:
            return _clean(auto.Control.CreateControlFromElement(edit).GetValuePattern().Value)
        except Exception:
            return ""

    # ---- actions --------------------------------------------------------------

    def _press(self, el):
        ctrl = auto.Control.CreateControlFromElement(el)
        for press in (lambda: ctrl.GetInvokePattern().Invoke(),
                      lambda: ctrl.GetLegacyIAccessiblePattern().DoDefaultAction()):
            try:
                press()
                return
            except Exception:
                continue
        raise BionicError(f"Bionic didn't accept a press on '{el.CurrentName}'.")

    @staticmethod
    def _wait(condition, timeout, step=0.15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if condition():
                    return True
            except BionicError:
                raise
            except Exception:
                pass  # elements vanish while Bionic re-renders; just look again
            time.sleep(step)
        return False

    def start_transcription(self):
        _, mic = self._composer()
        name = mic.CurrentName
        if self._is(name, "stop"):
            return
        if self._is(name, "setup"):
            raise BionicError("Voice transcription isn't set up in Bionic yet. "
                              "Click the mic button in Bionic's chat box once and pick a voice model.")
        if self._is(name, "denied"):
            raise BionicError("Bionic doesn't have microphone access. Allow it in Windows Settings > "
                              "Privacy & security > Microphone.")
        self._press(mic)
        # The first start can take a while: Bionic loads its speech model.
        if not self._wait(lambda: self._is(self._mic_name(), "stop"), 25):
            raise BionicError("Bionic didn't start recording. Try the mic button in Bionic once.")

    def finish_transcription(self):
        """Stop recording, wait for Bionic's cleanup pass, and return the composer text."""
        win, mic = self._composer()
        if self._is(mic.CurrentName, "stop"):
            self._press(mic)
        if not self._wait(lambda: not self._is(self._mic_name(), "stop"), 30):
            raise BionicError("Bionic is still recording.")

        def processing():
            return bool(self._find(self._window(), self.L["processing"], visible_only=False))

        # Bionic re-checks the dictated text after stopping and announces it while it works.
        if self._wait(processing, 1.5, step=0.1):
            self._wait(lambda: not processing(), 45, step=0.2)
        return self._stable_text()

    def _stable_text(self, quiet=0.6, timeout=8):
        last, since = None, time.time()
        deadline = time.time() + timeout
        while time.time() < deadline:
            text = self.composer_text()
            if text != last:
                last, since = text, time.time()
            elif time.time() - since >= quiet:
                break
            time.sleep(0.1)
        return last or ""

    def clear_composer(self):
        """Empty Bionic's chat box (e.g. to retry a dictation)."""
        win, mic = self._composer()
        edit = self._edit(win, mic)
        if edit is None:
            raise BionicError("Can't find Bionic's chat box.")
        auto.Control.CreateControlFromElement(edit).GetValuePattern().SetValue("")
        if not self._wait(lambda: not self.composer_text(), 2):
            raise BionicError("Bionic didn't clear the chat box.")

    def send(self, on_busy=None, busy_timeout=900, cancelled=lambda: False):
        """Press Send. Returns False if `cancelled()` became true before the message went out."""
        win, mic = self._composer()
        if not self.composer_text():
            raise BionicError("Nothing to send: the chat box is empty.")
        # While Bionic is still answering, Send is replaced by Interrupt.
        if self._same_row(win, mic, self.L["send"]) is None and self._same_row(win, mic, self.L["interrupt"]) is not None:
            if on_busy:
                on_busy()
            if not self._wait(lambda: cancelled() or self._send_button() is not None, busy_timeout, step=0.5):
                raise BionicError("Bionic is still busy; your message is waiting in the chat box.")
        for _ in range(4):
            if cancelled():
                return False
            if not self._wait(lambda: self._send_button() is not None and self._send_button().CurrentIsEnabled, 10):
                break
            self._press(self._send_button())
            sent = self._wait(lambda: not self.composer_text() or self._interrupt_visible(), 4)
            if sent:
                return True
            time.sleep(1.0)  # e.g. "Finish syncing transcription before sending."
        raise BionicError("Bionic didn't send the message. It is still in the chat box.")

    def _send_button(self):
        win, mic = self._composer()
        return self._same_row(win, mic, self.L["send"])

    def _interrupt_visible(self):
        win, mic = self._composer()
        return self._same_row(win, mic, self.L["interrupt"]) is not None
