"""Bionic Voice Overlay: talk to Bionic with a hotkey and hear its answers in a local voice.

Run with start.bat (no console) or `python main.py`.
"""
import logging
import os
import queue
import sys
import threading
import time

import winutil

winutil.set_dpi_awareness()  # before Tk or UI Automation touch any window

import tkinter as tk  # noqa: E402

import keyboard  # noqa: E402
import uiautomation as auto  # noqa: E402

import config  # noqa: E402
from bionic_ui import BionicError, BionicUI  # noqa: E402
import autostart  # noqa: E402
import hotkeys  # noqa: E402
import labels  # noqa: E402
import voices  # noqa: E402
from panel import Panel  # noqa: E402
from replies import ReplyWatcher  # noqa: E402
from speech import Speaker  # noqa: E402
from vad import SilenceDetector  # noqa: E402

log = logging.getLogger("overlay")


class Controller:
    """Runs Bionic actions one at a time on a worker thread and keeps the panel's status current."""

    def __init__(self, cfg, ui, custom_voices):
        self.cfg, self.ui = cfg, ui
        self.jobs = queue.Queue()
        self.recording = False
        self.hands_free = False         # the recording was started with a tap, so silence may end it
        self.finished_by_press = False  # this press ended a recording; ignore its release
        self.cancel_requested = False   # Clear was pressed: don't send what's being transcribed
        self.last_reply = ""
        self.speaker = Speaker(config.path(cfg["model"]), config.path(cfg["voices_file"]),
                               voice=cfg["voice"], speed=cfg["speed"], device=cfg["output_device"],
                               custom_voices=custom_voices, on_state=self._on_speaking)
        try:
            self.vad = SilenceDetector(config.path(cfg["vad_model"]), device=cfg["input_device"],
                                       threshold=cfg["vad_threshold"])
        except Exception as e:
            log.exception("silence detection unavailable")
            self.vad = None
            ui.toast(f"Auto-stop is unavailable ({e}). Run download_models.py.", "error", 12)
        self.phase = "idle"
        self.watcher = ReplyWatcher(cfg["bionic_home"], self._on_reply, lambda: self.cfg["read_mode"],
                                    on_silent_turn=self._on_silent_turn)
        threading.Thread(target=self._worker, daemon=True, name="bionic").start()
        threading.Thread(target=self._report_voice_model, daemon=True).start()
        self.watcher.start()

    # ---- entry points (hotkey thread / Tk thread) -----------------------------

    def talk_down(self):
        if winutil.find_bionic_window() is None:
            return  # Bionic is closed (the overlay is idling in the background): leave the key alone
        self.jobs.put("press")

    def talk_up(self, held_seconds):
        """Held long enough: hold-to-talk, so releasing sends. A quick tap: keep listening hands-free."""
        self.jobs.put("release_hold" if held_seconds >= self.cfg["hold_seconds"] else "release_tap")

    def clear(self):
        """Discard the current dictation: stop listening without sending and empty the chat box."""
        if winutil.find_bionic_window() is None:
            return
        self.cancel_requested = True  # seen right away by a transcription/send already in progress
        self.jobs.put("clear")

    def stop_voice(self):
        self.speaker.stop()

    def settings_changed(self):
        self.jobs.put("rearm")

    def replay(self):
        if self.last_reply:
            self.speaker.say(self.last_reply)
        else:
            self.ui.toast("No answer to replay yet.")

    # ---- worker ---------------------------------------------------------------

    def _worker(self):
        with auto.UIAutomationInitializerInThread():
            bionic = None
            while True:
                job = self.jobs.get()
                log.info("job %s (recording=%s)", job, self.recording)
                if bionic is None:  # Bionic's labels in its UI language, read once from its install
                    bionic = BionicUI(winutil.find_bionic_window, labels.load([winutil.bionic_install_dir() or ""]))
                try:
                    self._handle(job, bionic)
                except BionicError as e:
                    log.info("job %s: %s", job, e)
                    self._set_recording(False)
                    self._status("error", "Error")
                    self.ui.toast(str(e), "error", seconds=8)
                except Exception:
                    log.exception("action %s failed", job)
                    self._set_recording(False)
                    self._status("error", "Error")
                    self.ui.toast("Something went wrong. Details are in overlay.log.", "error", seconds=8)

    def _handle(self, job, bionic):
        if job == "press":
            self.finished_by_press = self.recording
            if self.recording:
                self._finish(bionic)  # a press while listening hands-free sends
            else:
                self._start(bionic)
        elif job in ("release_hold", "release_tap"):
            if self.finished_by_press:
                self.finished_by_press = False
            elif self.recording and job == "release_hold":
                self._finish(bionic)
            elif self.recording:
                self.hands_free = True
                self._arm_auto_stop()
        elif job == "silence" and self.recording and self.hands_free:
            self._finish(bionic)
        elif job == "clear":
            self._clear(bionic)
        elif job == "rearm":
            self._arm_auto_stop()

    def _set_recording(self, on):
        self.recording = on
        self.ui.set_recording(on)
        if not on and self.vad:
            self.vad.stop()

    def _arm_auto_stop(self):
        """Listen for silence while recording hands-free; while the key is held, the user is still talking."""
        if not self.vad:
            return
        if not (self.recording and self.hands_free and self.cfg["auto_stop"]):
            self.vad.stop()
            return
        try:
            self.vad.start(self.cfg["silence_seconds"], on_silence=lambda: self.jobs.put("silence"),
                           on_countdown=self._countdown)
        except Exception as e:
            log.exception("could not open the microphone for silence detection")
            self.ui.toast(f"Auto-stop can't open the microphone ({e}). Press "
                          f"{hotkeys.label(self.cfg['hotkeys']['talk'])} to stop.", "error", 8)

    def _countdown(self, seconds_left):
        if not self.recording:
            return
        verb = "Send" if self.cfg["auto_send"] else "Stop"
        self._status("listening", f"{verb} in {seconds_left}" if seconds_left else "Listening")

    def _status(self, kind, text):
        self.phase = kind
        self.ui.status(kind, text)

    def _start(self, bionic):
        self.speaker.stop()  # don't let the mic hear the previous answer
        self.hands_free = False
        self._status("working", "Starting")
        bionic.start_transcription()
        self._set_recording(True)
        self._status("listening", "Listening")

    def _clear(self, bionic):
        try:
            self._status("working", "Clearing")
            if self.recording:
                bionic.finish_transcription()  # let Bionic stop and settle, so nothing lands after we clear
                self._set_recording(False)
            bionic.clear_composer()
        finally:
            self.cancel_requested = False
        self._status("idle", "Cleared")
        self.ui.toast(f"Cleared. Press {hotkeys.label(self.cfg['hotkeys']['talk'])} to try again.", seconds=3)

    def _finish(self, bionic):
        self._status("working", "Working")
        text = bionic.finish_transcription()
        self._set_recording(False)
        if self.cancel_requested:
            return  # Clear was pressed meanwhile; its queued job empties the chat box
        if not text:
            self._status("idle", "Ready")
            self.ui.toast("Didn't catch anything.")
            return
        if not self.cfg["auto_send"]:
            self._status("idle", "Ready")
            self.ui.toast("Transcribed. Send it from Bionic when you're ready.")
            return
        self._status("working", "Sending")
        if bionic.send(on_busy=lambda: self._status("working", "Waiting"), cancelled=lambda: self.cancel_requested):
            self._status("thinking", "Thinking")

    # ---- callbacks from the reply watcher / speaker ---------------------------

    def _on_reply(self, text):
        log.info("answer received (%d chars); read_aloud=%s voice_ready=%s voice_error=%s", len(text),
                 self.cfg["read_aloud"], self.speaker.ready.is_set(), self.speaker.error)
        self.last_reply = text
        if self.recording:  # never talk into an open mic
            self.ui.toast(f"Answer ready. {hotkeys.label(self.cfg['hotkeys']['replay'])} to hear it.")
        elif self.cfg["read_aloud"] and self.speaker.ready.is_set() and not self.speaker.error:
            self.speaker.say(text)
        else:
            self._status("idle", "Answered")

    def _on_silent_turn(self):
        if self.phase == "thinking":
            self._status("idle", "Ready")

    def _on_speaking(self, speaking):
        log.info("speaking=%s", speaking)
        if speaking:
            self._status("speaking", "Speaking")
        elif not self.recording and self.phase == "speaking":
            self._status("idle", "Ready")

    def _report_voice_model(self):
        self.speaker.ready.wait()
        if self.speaker.error:
            self.ui.toast(f"Voice model failed to load ({self.speaker.error}). Run download_models.py.", "error", 12)


class App:
    def __init__(self, background=False):
        self.cfg = config.load()
        self.greeted = False
        builtin = voices.builtin(config.path(self.cfg["voices_file"]))
        custom = voices.load_custom(config.path(self.cfg["custom_voices_dir"]))
        self.voice_groups = voices.english_groups(builtin, list(custom))
        # Scrolling over the Voice cell steps through the picker's voices in order.
        self.voice_order = [v for _, sections in self.voice_groups for _, names in sections for v in names]
        if self.cfg["voice"] not in builtin and self.cfg["voice"] not in custom:
            self.cfg["voice"] = config.DEFAULTS["voice"]
        self.root = tk.Tk()
        self.root.title("Bionic Voice")
        self.panel = Panel(self.root, self.cfg, {
            "talk_down": lambda: self.ctl.talk_down(),
            "talk_up": lambda held: self.ctl.talk_up(held),
            "clear": lambda: self.ctl.clear(),
            "stop_voice": lambda: self.ctl.stop_voice(),
            "replay": lambda: self.ctl.replay(),
            "toggle": self.toggle,
            "cycle": self.cycle,
            "voice_groups": lambda: self.voice_groups,
            "set_voice": self.set_voice,
            "capture_hotkey": self.capture_hotkey,
            "cancel_capture": self.cancel_capture,
            "set_hotkey": self.set_hotkey,
            "reset_hotkeys": self.reset_hotkeys,
            "autostart_get": autostart.is_enabled,
            "autostart_set": autostart.set_enabled,
            "bionic_appeared": self.bionic_appeared,
            "save": lambda: config.save(self.cfg),
            "quit": self.quit,
        }, show_when_offline=not background)
        self.ctl = Controller(self.cfg, self.panel, custom)
        self._capture_hook = None
        self.bind_hotkeys()
        autostart.refresh()

    def bionic_appeared(self):
        self.ctl.speaker.preload()
        if not self.greeted:
            self.greeted = True
            key = hotkeys.label(self.cfg["hotkeys"]["talk"])
            self.panel.toast(f"Bionic Voice is ready. Hold {key} to talk, or tap {key} to talk hands-free.", seconds=5)

    def bind_hotkeys(self):
        keyboard.unhook_all()
        hk = self.cfg["hotkeys"]
        pressed_at = [None]

        def down():
            if pressed_at[0] is None:  # ignore key auto-repeat while held
                pressed_at[0] = time.monotonic()
                self.ctl.talk_down()

        def up(_event):
            if pressed_at[0] is not None:
                held, pressed_at[0] = time.monotonic() - pressed_at[0], None
                self.ctl.talk_up(held)

        bindings = [("talk", lambda key: (keyboard.add_hotkey(key, down),
                                          keyboard.on_release_key(key.split("+")[-1], up)))]
        for action, callback in (("clear", self.ctl.clear), ("stop_voice", self.ctl.stop_voice),
                                 ("replay", self.ctl.replay)):
            bindings.append((action, lambda key, cb=callback: keyboard.add_hotkey(key, cb, trigger_on_release=True)))
        for action, bind in bindings:
            if not hk.get(action):
                continue
            try:
                bind(hk[action])
            except (ValueError, KeyError) as e:  # e.g. a typo in config.json
                log.warning("can't use hotkey %r for %s: %s", hk[action], action, e)
                self.panel.toast(f"Hotkey '{hk[action]}' isn't valid. Pick another with the Keys button.", "error", 8)

    # ---- hotkey assignment (from the Keys dropdown) ---------------------------

    def capture_hotkey(self, on_done):
        """Record the next key combination and call on_done(combo, or None if cancelled) on the Tk thread.

        All hotkeys are switched off meanwhile, so pressing e.g. F8 assigns it instead of starting to talk.
        """
        self.cancel_capture()
        keyboard.unhook_all()
        pressed = []

        def finish(result):
            self.cancel_capture()
            self.panel.call(lambda: on_done(result))

        def on_key(event):
            name = hotkeys.normalize(event.name)
            if not name:
                return
            if event.event_type == keyboard.KEY_DOWN:
                if name not in pressed:
                    pressed.append(name)
            elif pressed:
                result = hotkeys.combo(pressed)
                pressed.clear()
                if result:  # released after a real key (not just modifiers); Esc alone cancels
                    # Unhooking from inside a keyboard callback isn't safe; do it from another thread.
                    threading.Thread(target=finish, args=(None if result == "esc" else result,), daemon=True).start()

        self._capture_hook = keyboard.hook(on_key)

    def cancel_capture(self):
        if self._capture_hook is not None:
            keyboard.unhook(self._capture_hook)
            self._capture_hook = None
            self.bind_hotkeys()

    def set_hotkey(self, action, combo):
        """Assign `combo` to `action`; returns a message if it can't be used."""
        error = hotkeys.problem(combo, action, self.cfg["hotkeys"])
        if error:
            return error
        self.cfg["hotkeys"][action] = combo
        config.save(self.cfg)
        self.bind_hotkeys()
        return None

    def reset_hotkeys(self):
        self.cfg["hotkeys"] = dict(config.DEFAULTS["hotkeys"])
        config.save(self.cfg)
        self.bind_hotkeys()

    def toggle(self, key):
        self.cfg[key] = not self.cfg[key]
        config.save(self.cfg)
        if key == "auto_stop":
            self.ctl.settings_changed()
        if key == "read_aloud" and not self.cfg[key]:
            self.ctl.stop_voice()

    def cycle(self, key, step):
        options = self.voice_order if key == "voice" else self.cfg[{"speed": "speeds", "silence_seconds": "silence_options"}[key]]
        current = self.cfg[key]
        i = options.index(current) if current in options else -step
        value = options[(i + step) % len(options)]
        if key == "voice":
            self.set_voice(value)
            return
        self.cfg[key] = value
        config.save(self.cfg)
        if key == "silence_seconds":
            self.ctl.settings_changed()
            return
        self.ctl.speaker.speed = self.cfg["speed"]
        if not self.ctl.recording:  # a short sample of the new speed
            self.ctl.speaker.say("This is my speaking speed.")

    def set_voice(self, name):
        self.cfg["voice"] = name
        config.save(self.cfg)
        self.ctl.speaker.voice = name
        if not self.ctl.recording:  # a short sample of the new voice
            self.ctl.speaker.say(voices.sample_sentence(name))

    def quit(self):
        keyboard.unhook_all()
        self.ctl.speaker.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def selftest(path):
    """--selftest: check the voice, silence detection and Bionic labels load, without any UI."""
    lines = []
    try:
        cfg = config.load()
        from kokoro_onnx import Kokoro
        kokoro = Kokoro(config.path(cfg["model"]), config.path(cfg["voices_file"]))
        samples, rate = kokoro.create("Self test.", voice="af_heart", lang="en-us")
        lines.append(f"voice: ok ({len(samples) / rate:.1f}s of audio)")
        SilenceDetector(config.path(cfg["vad_model"]))
        lines.append("silence detection: ok")
        found = labels.load([winutil.bionic_install_dir() or ""])
        lines.append(f"bionic labels: {len(found['start'])} languages")
        with auto.UIAutomationInitializerInThread():
            ui = BionicUI(winutil.find_bionic_window, found)
            lines.append("ui automation: ok")
            if winutil.find_bionic_window() is None:
                lines.append("bionic chat box: skipped (Bionic isn't open)")
            else:
                try:
                    _, mic = ui._composer()
                    lines.append(f"bionic chat box: found (mic button: {mic.CurrentName})")
                except BionicError as e:
                    lines.append(f"bionic chat box: not checked ({e})")
        lines.append(f"voices: {len(voices.builtin(config.path(cfg['voices_file'])))} built-in")
        lines.append("RESULT: ok")
    except Exception as e:
        log.exception("self test failed")
        lines.append(f"RESULT: failed: {e!r}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    logging.basicConfig(filename=config.path("overlay.log"), filemode="w", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    os.chdir(config.APP_DIR)
    if "--selftest" in sys.argv:
        return selftest(config.path("selftest.txt"))
    background = "--background" in sys.argv  # started at sign-in: stay invisible until Bionic opens
    if not winutil.single_instance("BionicVoiceOverlay"):
        if not background:
            winutil.message_box("Bionic Voice is already running. It shows up next to Bionic's window.",
                                "Bionic Voice")
        return
    try:
        App(background=background).run()
    except Exception:
        log.exception("overlay crashed")
        raise


if __name__ == "__main__":
    sys.exit(main())
