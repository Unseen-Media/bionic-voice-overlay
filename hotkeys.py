"""Hotkey names, labels and the rules for assigning them (format: "ctrl+shift+f8", as `keyboard` uses)."""

# Config key -> what the hotkey picker calls it, in display order.
ACTIONS = [
    ("talk", "Talk (hold or tap)"),
    ("clear", "Clear / retry"),
    ("stop_voice", "Stop reading"),
    ("replay", "Replay answer"),
]
MODIFIERS = ["ctrl", "shift", "alt", "windows"]
_TYPING_KEYS = {"space", "enter", "tab", "backspace"}
_ALIASES = {"alt gr": "alt", "control": "ctrl", "win": "windows", "+": "plus"}
_NICE = {"ctrl": "Ctrl", "shift": "Shift", "alt": "Alt", "windows": "Win", "plus": "+", "esc": "Esc"}


def normalize(name):
    """A key name from a keyboard event -> the name used in hotkey strings (left/right sides merged)."""
    if not name:
        return None
    n = name.lower()
    for side in ("left ", "right "):
        if n.startswith(side):
            n = n[len(side):]
    return _ALIASES.get(n, n)


def combo(pressed):
    """Keys pressed together -> "ctrl+shift+f8", or None if only modifiers were pressed."""
    mods = [m for m in MODIFIERS if m in pressed]
    keys = [k for k in pressed if k not in MODIFIERS]
    return "+".join(mods + keys) if keys else None


def label(hotkey):
    if not hotkey:
        return "—"
    return "+".join(_NICE.get(p, p.upper() if len(p) <= 3 else p.title()) for p in hotkey.split("+"))


def problem(hotkey, action, current):
    """Why `hotkey` can't be used for `action`, or None if it's fine."""
    parts = hotkey.split("+")
    keys = [p for p in parts if p not in MODIFIERS]
    guarded = any(m in parts for m in ("ctrl", "alt", "windows"))
    if not guarded and any(len(k) == 1 or k in _TYPING_KEYS for k in keys):
        return f"{label(hotkey)} would also fire while you type. Add Ctrl or Alt, or use a function key."
    for other, name in ACTIONS:
        if other != action and current.get(other) and set(current[other].split("+")) == set(parts):
            return f"{label(hotkey)} is already used for {name}."
    return None
