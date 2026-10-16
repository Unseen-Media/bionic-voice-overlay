"""The side panel docked to Bionic's window: status, talk/stop/replay, and setting toggles."""
import logging
import queue
import time
import tkinter as tk
import tkinter.font as tkfont

import hotkeys
import voices
import winutil

log = logging.getLogger(__name__)

GLYPH = {"mic": "", "stop": "", "replay": "", "send": "", "volume": "",
         "mute": "", "voice": "", "speed": "", "swap": "", "close": "",
         "timer": "", "erase": "", "setup": ""}

# Cells whose value can be stepped with right-click or the mouse wheel, and the setting each one steps.
STEPPERS = {"voice": "voice", "speed": "speed", "auto_stop": "silence_seconds"}

COLOR = {"key": "#010203", "bg": "#1b1d21", "border": "#30333a", "hover": "#2a2d33", "on": "#24324a",
         "on_fg": "#7fb0ff", "fg": "#d4d7dd", "dim": "#80858e", "sep": "#2c2f35", "red": "#e5484d",
         "bubble": "#26292f", "error": "#ff8c42"}
STATUS_COLOR = {"idle": "#80858e", "listening": "#e5484d", "working": "#f5a524", "thinking": "#6ea8ff",
                "speaking": "#3ecf8e", "error": "#ff8c42", "offline": "#55595f"}


class Dropdown:
    """A dark popup beside the panel, drawn on one canvas.

    It closes when it loses focus, on Esc, when the pointer wanders off it for a moment, or
    when its panel cell is clicked again. Subclasses fill `self.hits` with clickable areas
    (key, x0, y0, x1, y1), draw in `_draw` and react in `_click`.
    """

    def __init__(self, root, scale):
        self.root, self.s = root, scale
        self.win = self.canvas = None
        self.hits = []
        self.hover = None
        self._leave_job = None
        self.f_head = tkfont.Font(family="Segoe UI Semibold", size=-int(12 * scale))
        self.f_sub = tkfont.Font(family="Segoe UI", size=-int(10 * scale))
        self.f_item = tkfont.Font(family="Segoe UI", size=-int(12 * scale))

    @property
    def is_open(self):
        return self.win is not None

    def _show(self, w, h, x_left, x_right, y, prefer_right, work_area):
        win = self.win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        self.canvas = tk.Canvas(win, width=w, height=h, bg=COLOR["bg"], highlightthickness=1,
                                highlightbackground=COLOR["border"], bd=0, cursor="hand2")
        self.canvas.pack()
        self._draw()
        wl, wt, wr, wb = work_area
        x = max(wl, min(x_right if prefer_right else x_left - w, wr - w))
        win.geometry(f"{w + 2}x{h + 2}+{x}+{max(wt, min(y, wb - h))}")
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Button-1>", lambda e: self._click(self._hit(e.x, e.y)))
        win.bind("<FocusOut>", lambda _e: self.root.after(150, self._close_if_unfocused))
        win.bind("<Escape>", lambda _e: self._on_escape())
        win.bind("<Leave>", lambda _e: self._on_leave())
        win.focus_force()

    def redraw(self):
        if self.canvas is not None:
            self._draw()

    def _hit(self, x, y):
        return next((h[0] for h in self.hits if h[1] <= x < h[3] and h[2] <= y < h[4]), None)

    def _on_motion(self, event):
        if self._leave_job:
            self.root.after_cancel(self._leave_job)
            self._leave_job = None
        hit = self._hit(event.x, event.y)
        if hit != self.hover:
            self.hover = hit
            self._draw()

    def _on_leave(self):
        self.hover = None
        self._draw()
        if self._leave_job:
            self.root.after_cancel(self._leave_job)
        self._leave_job = self.root.after(1500, self._close_if_pointer_away)

    def _on_escape(self):
        self.close()

    def _busy(self):
        """True while the dropdown must stay open even if the pointer leaves it."""
        return False

    def _close_if_unfocused(self):
        if self.win is None:
            return
        focused = self.win.focus_get()
        if focused is None or not str(focused).startswith(str(self.win)):
            self.close()

    def _close_if_pointer_away(self):
        self._leave_job = None
        if self.win is None or self._busy():
            return
        if self.win.winfo_containing(*self.win.winfo_pointerxy()) is None:  # not over the dropdown or the panel
            self.close()

    def _on_close(self):
        pass

    def close(self):
        if self._leave_job:
            self.root.after_cancel(self._leave_job)
            self._leave_job = None
        if self.win is not None:
            self._on_close()
            self.win.destroy()
            self.win = self.canvas = None
            self.hits, self.hover = [], None


class VoicePicker(Dropdown):
    """Every US and UK voice (plus custom ones), one column per group.

    It stays open so voices can be auditioned one after another.
    """

    def __init__(self, root, scale, on_pick):
        super().__init__(root, scale)
        self.on_pick = on_pick
        self.layout = []  # (kind, text, x, y) for headings
        self.current = None

    def open(self, groups, current, *where):
        self.close()
        s = self.s
        pad, row_h, check_w = int(10 * s), int(24 * s), int(18 * s)
        self.layout, self.hits, self.current = [], [], current

        # Lay the groups out as columns, each as wide as its longest entry.
        x, height = pad, 0
        for title, sections in groups:
            entries = [n for _, names in sections for n in names]
            col_w = max([self.f_head.measure(title)] + [check_w + self.f_item.measure(self._text(n)) for n in entries])
            col_w += int(12 * s)
            cy = pad
            self.layout.append(("head", title, x + int(4 * s), cy))
            cy += int(24 * s)
            for subtitle, names in sections:
                if subtitle and names:
                    cy += int(4 * s)
                    self.layout.append(("sub", subtitle.upper(), x + int(4 * s), cy))
                    cy += int(18 * s)
                for name in names:
                    self.hits.append((name, x, cy, x + col_w, cy + row_h))
                    cy += row_h
            x += col_w + int(8 * s)
            height = max(height, cy)
        self._show(x - int(8 * s) + pad, height + pad, *where)

    @staticmethod
    def _text(name):
        return voices.label(name) + (" ★" if name in voices.BEST else "")

    def _draw(self):
        c, s = self.canvas, self.s
        c.delete("all")
        for kind, text, x, y in self.layout:
            font, color = (self.f_head, COLOR["fg"]) if kind == "head" else (self.f_sub, COLOR["dim"])
            c.create_text(x, y, text=text, anchor="nw", fill=color, font=font)
        for name, x0, y0, x1, y1 in self.hits:
            on = name == self.current
            if name == self.hover:
                c.create_rectangle(x0, y0, x1, y1, fill=COLOR["hover"], outline="")
            mid = (y0 + y1) // 2
            if on:
                c.create_text(x0 + int(9 * s), mid, text="✓", fill=COLOR["on_fg"], font=self.f_item)
            c.create_text(x0 + int(18 * s), mid, text=self._text(name), anchor="w",
                          fill=COLOR["on_fg"] if on else COLOR["fg"], font=self.f_item)

    def _click(self, name):
        if name:
            self.select(name)
            self.on_pick(name)

    def select(self, current):
        self.current = current
        self.redraw()


class HotkeyPicker(Dropdown):
    """Each action's hotkey with an Assign button that records a new key combination."""

    HINT = "Click Assign, then press the new keys. Ctrl, Shift, Alt and Win can be combined. Esc cancels."

    def __init__(self, root, scale, current, actions, on_changed):
        super().__init__(root, scale)
        self.current = current  # callable -> the hotkeys dict from the config
        self.actions, self.on_changed = actions, on_changed
        self.capturing = None
        self.message, self.message_is_error = self.HINT, False

    def open(self, *where):
        self.close()
        s = self.s
        pad, row_h = int(12 * s), int(34 * s)
        name_w = max(self.f_item.measure(name) for _, name in hotkeys.ACTIONS)
        self.chip_w = max(int(120 * s), self.f_item.measure("Ctrl+Shift+Alt+Page Down") + int(16 * s))
        self.btn_w = self.f_item.measure("Cancel") + int(24 * s)
        self.x_chip = pad + name_w + int(16 * s)
        self.x_btn = self.x_chip + self.chip_w + int(8 * s)
        w = self.x_btn + self.btn_w + pad
        self.rows, self.hits = [], []
        y = pad + int(28 * s)
        for action, name in hotkeys.ACTIONS:
            self.rows.append((action, name, y, y + row_h))
            self.hits.append((("assign", action), self.x_btn, y + int(5 * s), self.x_btn + self.btn_w, y + row_h - int(5 * s)))
            y += row_h
        y += int(6 * s)
        self.hits.append((("reset", None), pad, y, pad + self.f_item.measure("Reset to defaults") + int(4 * s), y + int(22 * s)))
        self.reset_y = y
        y += int(30 * s)
        self.message_y = y
        y += int(40 * s)
        self.startup_y = y  # "Startup" heading, then the checkbox row
        y += int(26 * s)
        self.hits.append((("autostart", None), pad, y, w - pad, y + int(42 * s)))
        self.autostart_y = y
        y += int(42 * s)
        self.width, self.pad, self.height = w, pad, y + pad
        self.message, self.message_is_error = self.HINT, False
        self._show(w, self.height, *where)

    def _draw(self):
        c, s, pad = self.canvas, self.s, self.pad
        c.delete("all")
        c.create_text(pad, pad, text="Hotkeys", anchor="nw", fill=COLOR["fg"], font=self.f_head)
        current = self.current()
        for action, name, y0, y1 in self.rows:
            mid, top, bottom = (y0 + y1) // 2, y0 + int(5 * s), y1 - int(5 * s)
            c.create_text(pad, mid, text=name, anchor="w", fill=COLOR["fg"], font=self.f_item)
            capturing = self.capturing == action
            c.create_rectangle(self.x_chip, top, self.x_chip + self.chip_w, bottom,
                               fill=COLOR["on"] if capturing else COLOR["hover"],
                               outline=COLOR["on_fg"] if capturing else "")
            c.create_text(self.x_chip + self.chip_w // 2, mid, font=self.f_item,
                          text="Press keys…" if capturing else hotkeys.label(current.get(action)),
                          fill=COLOR["on_fg"] if capturing else COLOR["fg"])
            hot = self.hover == ("assign", action)
            c.create_rectangle(self.x_btn, top, self.x_btn + self.btn_w, bottom,
                               fill=COLOR["on"] if hot else COLOR["bg"], outline=COLOR["border"])
            c.create_text(self.x_btn + self.btn_w // 2, mid, text="Cancel" if capturing else "Assign",
                          fill=COLOR["on_fg"], font=self.f_item)
        c.create_text(pad, self.reset_y + int(11 * s), text="Reset to defaults", anchor="w", font=self.f_item,
                      fill=COLOR["fg"] if self.hover == ("reset", None) else COLOR["dim"])
        c.create_text(pad, self.message_y, text=self.message, anchor="nw", width=self.width - 2 * pad,
                      fill=COLOR["error"] if self.message_is_error else COLOR["dim"], font=self.f_sub)

        c.create_line(pad, self.startup_y - int(8 * s), self.width - pad, self.startup_y - int(8 * s), fill=COLOR["sep"])
        c.create_text(pad, self.startup_y, text="Startup", anchor="nw", fill=COLOR["fg"], font=self.f_head)
        on, y0 = self.actions["autostart_get"](), self.autostart_y
        if self.hover == ("autostart", None):
            c.create_rectangle(pad - int(4 * s), y0, self.width - pad, y0 + int(42 * s), fill=COLOR["hover"], outline="")
        box = int(16 * s)
        bx, by = pad, y0 + int(4 * s)
        c.create_rectangle(bx, by, bx + box, by + box, outline=COLOR["on_fg"] if on else COLOR["dim"],
                           fill=COLOR["on"] if on else "")
        if on:
            c.create_text(bx + box // 2, by + box // 2, text="✓", fill=COLOR["on_fg"], font=self.f_item)
        c.create_text(bx + box + int(8 * s), by + box // 2, text="Start automatically with Windows", anchor="w",
                      fill=COLOR["fg"], font=self.f_item)
        c.create_text(bx + box + int(8 * s), by + box + int(4 * s), anchor="nw", font=self.f_sub, fill=COLOR["dim"],
                      text="Waits invisibly and appears when Bionic opens.")

    def _say(self, message, error=False):
        self.message, self.message_is_error = message, error
        self.redraw()

    def _click(self, hit):
        if hit is None:
            return
        kind, action = hit
        if kind == "autostart":
            on = not self.actions["autostart_get"]()
            try:
                self.actions["autostart_set"](on)
                self._say("Bionic Voice will start with Windows and appear when Bionic opens." if on
                          else "Bionic Voice won't start automatically any more.")
            except OSError as e:
                self._say(f"Couldn't change the startup setting ({e}).", error=True)
        elif kind == "reset":
            self._stop_capture()
            self.actions["reset_hotkeys"]()
            self.on_changed()
            self._say("Hotkeys are back to the defaults.")
        elif self.capturing == action:
            self._stop_capture()
            self._say("Cancelled.")
        else:
            self._stop_capture()
            self.capturing = action
            self._say(f"Press the new keys for {dict(hotkeys.ACTIONS)[action]}. Esc cancels.")
            self.actions["capture_hotkey"](lambda combo, a=action: self._captured(a, combo))

    def _captured(self, action, combo):
        if self.capturing != action:
            return  # cancelled meanwhile
        self.capturing = None
        if combo is None:
            self._say("Cancelled.")
            return
        error = self.actions["set_hotkey"](action, combo)
        self.on_changed()
        self._say(error or f"{dict(hotkeys.ACTIONS)[action]} is now {hotkeys.label(combo)}.", error=bool(error))

    def _stop_capture(self):
        if self.capturing is not None:
            self.capturing = None
            self.actions["cancel_capture"]()

    def _busy(self):
        return self.capturing is not None

    def _on_escape(self):
        if self.capturing is None:  # while recording, Esc is the "cancel" key the recorder sees
            self.close()

    def _on_close(self):
        self._stop_capture()


class Bubble:
    """A small tooltip/toast window shown beside the panel."""

    def __init__(self, root, fonts, scale):
        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.label = tk.Label(self.win, bg=COLOR["bubble"], fg=COLOR["fg"], font=fonts, justify="left",
                              wraplength=int(260 * scale), padx=int(10 * scale), pady=int(6 * scale),
                              highlightthickness=1, highlightbackground=COLOR["border"])
        self.label.pack()
        self.win.update_idletasks()
        winutil.style_panel(winutil.toplevel_of(self.win.winfo_id()))
        self._hide_job = None

    def show(self, text, anchor_x_left, anchor_x_right, y, prefer_right, color=None, seconds=None):
        self.label.configure(text=text, fg=color or COLOR["fg"])
        self.win.update_idletasks()
        w = self.win.winfo_reqwidth()
        x = anchor_x_right if prefer_right else anchor_x_left - w
        self.win.geometry(f"+{x}+{y}")
        self.win.deiconify()
        self.win.lift()
        if self._hide_job:
            self.win.after_cancel(self._hide_job)
            self._hide_job = None
        if seconds:
            self._hide_job = self.win.after(int(seconds * 1000), self.hide)

    def hide(self):
        self._hide_job = None
        self.win.withdraw()


class Panel:
    def __init__(self, root, cfg, actions, show_when_offline=True):
        self.root, self.cfg, self.actions = root, cfg, actions
        self.show_when_offline = show_when_offline  # False when started at sign-in
        self.seen_bionic = False
        self.inbox = queue.Queue()
        self.status_kind, self.status_text = "idle", "Ready"
        self.recording = False
        self.offline = None  # unknown until the first look for Bionic's window
        self.hover = None
        self.pressed = None
        self.talk_pressed_at = 0.0
        self.drag = None
        self.pulse = False
        self.bionic = None
        self.last_find = 0.0
        self.visible = True
        self.pos = None
        self.outside_right = True
        self.tip_job = None
        self.last_dock_error = None

        s = self.s =winutil.dpi_scale(winutil.find_bionic_window())
        self.w, self.pad = int(58 * s), int(6 * s)
        self.cells = []  # (key, top, bottom)
        y = self.pad
        for key, height in self._layout():
            self.cells.append((key, y, y + int(height * s)))
            y += int(height * s)
        self.h = y + self.pad

        icon_family = next((f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if f in tkfont.families(root)),
                           "Segoe UI Symbol")
        self.f_icon = (icon_family, -int(17 * s))
        self.f_caption = ("Segoe UI", -int(10 * s))
        self.f_status = ("Segoe UI Semibold", -int(10 * s))

        root.overrideredirect(True)
        root.configure(bg=COLOR["key"])
        root.attributes("-transparentcolor", COLOR["key"])
        root.geometry(f"{self.w}x{self.h}+-10000+-10000")
        self.canvas = tk.Canvas(root, width=self.w, height=self.h, bg=COLOR["key"], highlightthickness=0, bd=0)
        self.canvas.pack()
        root.update_idletasks()
        self.hwnd = winutil.toplevel_of(root.winfo_id())
        winutil.style_panel(self.hwnd)
        self.tip = Bubble(root, ("Segoe UI", -int(12 * s)), s)
        self.toast_bubble = Bubble(root, ("Segoe UI", -int(12 * s)), s)
        self.picker = VoicePicker(root, s, on_pick=self._picked_voice)
        self.hotkey_picker = HotkeyPicker(root, s, lambda: self.cfg["hotkeys"], actions, on_changed=self._draw)

        c = self.canvas
        c.bind("<Motion>", self._on_motion)
        c.bind("<Leave>", self._on_leave)
        c.bind("<ButtonPress-1>", self._on_press)
        c.bind("<B1-Motion>", self._on_drag)
        c.bind("<ButtonRelease-1>", self._on_release)
        c.bind("<ButtonRelease-3>", self._on_right_click)
        c.bind("<MouseWheel>", self._on_wheel)

        self._draw()
        self._drain_inbox()
        self.root.after(100, self._follow_bionic)  # once the app has finished starting up
        self._pulse()

    # ---- thread-safe API (called from worker threads) -------------------------

    def status(self, kind, text):
        self.inbox.put(("status", kind, text))

    def toast(self, text, kind="info", seconds=5):
        self.inbox.put(("toast", text, kind, seconds))

    def set_recording(self, on):
        self.inbox.put(("recording", on))

    def call(self, fn):
        """Run fn on the Tk thread."""
        self.inbox.put(("call", fn))

    # ---- layout & drawing -----------------------------------------------------

    @staticmethod
    def _layout():
        return [("status", 44), ("talk", 46), ("clear", 46), ("stop", 46), ("replay", 46), ("sep", 9),
                ("auto_send", 46), ("auto_stop", 46), ("read_aloud", 46), ("voice", 46), ("speed", 46),
                ("sep", 9), ("keys", 46), ("dock", 46), ("close", 46)]

    def _button(self, key):
        """(glyph, caption, tooltip, toggled_on) for a button cell."""
        c, hk = self.cfg, self.cfg["hotkeys"]
        talk_key = hotkeys.label(hk.get("talk"))
        if key == "talk":
            verb = "send" if c["auto_send"] else "stop"
            if self.recording:
                return GLYPH["mic"], verb.capitalize(), f"Listening. Release, or press {talk_key} again, to {verb}.", None
            return GLYPH["mic"], "Talk", (f"Hold {talk_key} (or this button) while you talk; let go to {verb}.\n"
                                          f"Or tap it and talk hands-free."), None
        if key == "clear":
            return GLYPH["erase"], "Clear", (f"Misspoke? Clear and retry ({hotkeys.label(hk.get('clear'))}).\n"
                                             "Stops listening without sending and empties Bionic's chat box."), None
        if key == "stop":
            return GLYPH["stop"], "Stop", f"Stop reading aloud ({hotkeys.label(hk.get('stop_voice'))})", None
        if key == "replay":
            return GLYPH["replay"], "Replay", f"Read the last answer again ({hotkeys.label(hk.get('replay'))})", None
        if key == "keys":
            return GLYPH["setup"], "Setup", "Hotkeys, and starting automatically with Windows", None
        if key == "auto_send":
            return GLYPH["send"], "Auto", "Auto-send after transcribing: " + ("on" if c["auto_send"] else "off"), c["auto_send"]
        if key == "auto_stop":
            n, on = c["silence_seconds"], c["auto_stop"]
            state = f"on: hands-free talk ends after {n} s of silence" if on else "off: tap Talk again to finish"
            return GLYPH["timer"], f"{n}s" if on else "Off", (
                f"Auto-stop {state}\nClick: on/off. Right-click or scroll: change the time."), on
        if key == "read_aloud":
            on = c["read_aloud"]
            return GLYPH["volume" if on else "mute"], "Read", "Read answers aloud: " + ("on" if on else "off"), on
        if key == "voice":
            name = voices.label(c["voice"])
            caption = name if len(name) <= 8 else name[:7] + "…"
            return GLYPH["voice"], caption, f"Voice: {name}\nClick: pick from all US and UK voices. Scroll: next / previous.", None
        if key == "speed":
            return GLYPH["speed"], f"{c['speed']:g}×", "Speaking speed (click: faster, right-click: slower)", None
        if key == "dock":
            return GLYPH["swap"], "Side", "Dock on the other side of Bionic", None
        if key == "close":
            return GLYPH["close"], "Quit", "Quit the voice overlay", None
        return "", "", "", None

    def _round_rect(self, x0, y0, x1, y1, r, **kw):
        pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1,
               x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
        return self.canvas.create_polygon(pts, smooth=True, **kw)

    def _draw(self):
        c, s = self.canvas, self.s
        c.delete("all")
        self._round_rect(1, 1, self.w - 1, self.h - 1, int(12 * s), fill=COLOR["bg"], outline=COLOR["border"])
        cx = self.w // 2
        for key, top, bottom in self.cells:
            if key == "sep":
                mid = (top + bottom) // 2
                c.create_line(int(12 * s), mid, self.w - int(12 * s), mid, fill=COLOR["sep"])
                continue
            if key == "status":
                kind = "offline" if self.offline else self.status_kind
                color = STATUS_COLOR.get(kind, COLOR["dim"])
                if kind == "listening" and self.pulse:
                    color = "#8f2b2f"
                r = int(5 * s)
                dy = top + int(14 * s)
                c.create_oval(cx - r, dy - r, cx + r, dy + r, fill=color, outline="")
                text = "Open Bionic" if self.offline else self.status_text
                c.create_text(cx, top + int(32 * s), text=text, fill=color, font=self.f_status)
                continue
            glyph, caption, _, on = self._button(key)
            inset = int(5 * s)
            hot = self.hover == key
            fg = COLOR["fg"] if on is None else (COLOR["on_fg"] if on else COLOR["dim"])
            if key == "talk" and self.recording:
                self._round_rect(inset, top + 2, self.w - inset, bottom - 2, int(9 * s), fill=COLOR["red"], outline="")
                fg = "#ffffff"
            elif on:
                self._round_rect(inset, top + 2, self.w - inset, bottom - 2, int(9 * s),
                                 fill=COLOR["hover"] if hot else COLOR["on"], outline="")
            elif hot:
                self._round_rect(inset, top + 2, self.w - inset, bottom - 2, int(9 * s), fill=COLOR["hover"], outline="")
            c.create_text(cx, top + int(18 * s), text=glyph, fill=fg, font=self.f_icon)
            c.create_text(cx, top + int(36 * s), text=caption, fill=fg, font=self.f_caption)

    def _cell_at(self, y):
        for key, top, bottom in self.cells:
            if top <= y < bottom:
                return key
        return None

    # ---- events ---------------------------------------------------------------

    def _on_motion(self, event):
        key = self._cell_at(event.y)
        if key != self.hover:
            self.hover = key
            self._draw()
            self._schedule_tip(key)

    def _on_leave(self, _event):
        self.hover = None
        self._draw()
        self._schedule_tip(None)

    def _schedule_tip(self, key):
        if self.tip_job:
            self.root.after_cancel(self.tip_job)
            self.tip_job = None
        self.tip.hide()
        if key in (None, "sep"):
            return
        self.tip_job = self.root.after(550, lambda: self._show_tip(key))

    def _show_tip(self, key):
        self.tip_job = None
        if key == "status":
            text = ("Waiting for Bionic. Open Bionic and this panel docks to its window." if self.offline
                    else f"{self.status_text}\nDrag to move along the window edge.")
        else:
            text = self._button(key)[2]
        top = next(t for k, t, _ in self.cells if k == key)
        self._bubble(self.tip, text, top)

    def _bubble(self, bubble, text, cell_top, color=None, seconds=None):
        x, y = self.root.winfo_rootx(), self.root.winfo_rooty()
        gap = int(6 * self.s)
        bubble.show(text, x - gap, x + self.w + gap, y + cell_top, prefer_right=self.outside_right,
                    color=color, seconds=seconds)

    def _on_press(self, event):
        key = self._cell_at(event.y)
        self.pressed = key
        self.tip.hide()
        if key == "status":
            self.drag = (event.y_root, self.root.winfo_rooty())
        elif key == "talk":
            self.talk_pressed_at = time.monotonic()
            self.actions["talk_down"]()

    def _on_drag(self, event):
        if not self.drag or not self.bionic:
            return
        start_mouse, start_y = self.drag
        _, top, _, _ = winutil.frame_rect(self.bionic)
        self.cfg["dock_offset"] = start_y + (event.y_root - start_mouse) - top
        self._place(force=True)

    def _on_release(self, event):
        key, pressed = self._cell_at(event.y), self.pressed
        self.pressed = None
        if self.drag:
            self.drag = None
            self.actions["save"]()
            return
        if pressed == "talk":  # counts even if the pointer slid off the button while held
            self.actions["talk_up"](time.monotonic() - self.talk_pressed_at)
            return
        if key != pressed or key is None:
            return
        if key == "clear":
            self.actions["clear"]()
        elif key == "stop":
            self.actions["stop_voice"]()
        elif key == "replay":
            self.actions["replay"]()
        elif key in ("auto_send", "auto_stop", "read_aloud"):
            self.actions["toggle"](key)
        elif key == "voice":
            self._toggle_dropdown(self.picker, "voice", lambda *where: self.picker.open(
                self.actions["voice_groups"](), self.cfg["voice"], *where))
        elif key == "keys":
            self._toggle_dropdown(self.hotkey_picker, "keys", self.hotkey_picker.open)
        elif key == "speed":
            self.actions["cycle"]("speed", 1)
        elif key == "dock":
            self.cfg["dock_side"] = "left" if self.cfg["dock_side"] == "right" else "right"
            self.actions["save"]()
            self._place(force=True)
        elif key == "close":
            self.actions["quit"]()
        self._draw()

    def _toggle_dropdown(self, dropdown, cell, open_at):
        """Open `dropdown` beside `cell` (on the side away from Bionic), or close it if it's open."""
        if dropdown.is_open:
            dropdown.close()
            return
        for other in (self.picker, self.hotkey_picker):
            other.close()
        self.tip.hide()
        x, y = self.root.winfo_rootx(), self.root.winfo_rooty()
        top = next(t for k, t, _ in self.cells if k == cell)
        gap = int(6 * self.s)
        area = winutil.work_area(self.bionic) if self.bionic else (
            0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight())
        open_at(x - gap, x + self.w + gap, y + top - int(60 * self.s), self.outside_right, area)

    def _picked_voice(self, name):
        self.actions["set_voice"](name)
        self._draw()

    def _on_right_click(self, event):
        key = self._cell_at(event.y)
        if key in STEPPERS:
            self.actions["cycle"](STEPPERS[key], -1 if key in ("voice", "speed") else 1)
            self._draw()
            self._show_tip(key)

    def _on_wheel(self, event):
        key = self._cell_at(event.y)
        if key not in STEPPERS:
            return
        up = event.delta > 0
        # Numbers go up when scrolling up; the voice list moves down when scrolling down.
        self.actions["cycle"](STEPPERS[key], (-1 if up else 1) if key == "voice" else (1 if up else -1))
        self._draw()
        if key == "voice" and self.picker.is_open:
            self.picker.select(self.cfg["voice"])
        else:
            self._show_tip(key)

    # ---- background loops -----------------------------------------------------

    def _drain_inbox(self):
        changed = False
        try:
            while True:
                msg = self.inbox.get_nowait()
                if msg[0] == "status":
                    self.status_kind, self.status_text = msg[1], msg[2]
                    if self.status_kind == "error":  # the toast explains it; don't stay red forever
                        self.root.after(8000, self._clear_error)
                elif msg[0] == "recording":
                    self.recording = msg[1]
                elif msg[0] == "call":
                    msg[1]()
                elif msg[0] == "toast":
                    _, text, kind, seconds = msg
                    color = STATUS_COLOR["error"] if kind == "error" else None
                    if self.visible:
                        self._bubble(self.toast_bubble, text, 0, color=color, seconds=seconds)
                changed = True
        except queue.Empty:
            pass
        if changed:
            self._draw()
        self.root.after(50, self._drain_inbox)

    def _clear_error(self):
        if self.status_kind == "error":
            self.status_kind, self.status_text = "idle", "Ready"
            self._draw()

    def _pulse(self):
        if self.status_kind == "listening" and not self.offline:
            self.pulse = not self.pulse
            self._draw()
        self.root.after(450, self._pulse)

    def _follow_bionic(self):
        try:
            self._place()
        except Exception as e:
            if repr(e) != self.last_dock_error:  # log each distinct problem once, not every 100 ms
                self.last_dock_error = repr(e)
                log.exception("docking to Bionic failed")
        finally:
            self.root.after(100, self._follow_bionic)

    def _place(self, force=False):
        now = time.monotonic()
        if force or now - self.last_find > 0.5 or not winutil.is_window(self.bionic):
            self.bionic = winutil.find_bionic_window()
            self.last_find = now
        offline = self.bionic is None
        if offline != self.offline:
            self.offline = offline
            self._draw()
            if not offline:
                self.seen_bionic = True
                self.actions["bionic_appeared"]()
        if offline:
            # Started at sign-in, or Bionic was open and has closed: wait out of sight.
            if self.seen_bionic or not self.show_when_offline:
                self._show(False)
                return
            # Started by hand before Bionic: park at the screen edge so it's clear the overlay runs.
            self._show(True)
            if self.pos is None:
                self._move(self.root.winfo_screenwidth() - self.w - int(12 * self.s),
                           (self.root.winfo_screenheight() - self.h) // 2)
            return
        if winutil.is_minimized(self.bionic):
            self._show(False)
            return
        self._show(True)

        left, top, right, bottom = winutil.frame_rect(self.bionic)
        wl, wt, wr, wb = winutil.work_area(self.bionic)
        gap = int(6 * self.s)
        offset = self.cfg.get("dock_offset")
        y = top + offset if offset is not None else top + (bottom - top - self.h) // 2
        y = max(wt, min(y, wb - self.h))
        # (x, tooltips open to the right?) — outside the window if the screen has room, else just inside its edge.
        outside_r, outside_l = (right + gap, True), (left - gap - self.w, False)
        inside_r, inside_l = (right - gap - self.w, False), (left + gap, True)
        fits_r, fits_l = right + gap + self.w <= wr, left - gap - self.w >= wl
        if self.cfg["dock_side"] == "right":
            x, self.outside_right = outside_r if fits_r else outside_l if fits_l else inside_r
        else:
            x, self.outside_right = outside_l if fits_l else outside_r if fits_r else inside_l
        self._move(x, y, force)
        winutil.keep_visible_with(self.hwnd, self.bionic)

    def _move(self, x, y, force=False):
        if force or self.pos != (x, y):
            self.pos = (x, y)
            self.root.geometry(f"{self.w}x{self.h}+{x}+{y}")

    def _show(self, visible):
        if visible == self.visible:
            return
        self.visible = visible
        if visible:
            self.root.deiconify()
        else:
            self.root.withdraw()
            self.tip.hide()
            self.toast_bubble.hide()
            self.picker.close()
            self.hotkey_picker.close()
