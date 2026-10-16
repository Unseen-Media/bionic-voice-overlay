"""Settings: built-in defaults merged with config.json next to this file."""
import copy
import json
import os
import sys
import threading

VERSION = "1.0.1"

# Next to BionicVoice.exe when packaged, next to this file when run from source.
APP_DIR = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")

DEFAULTS = {
    "hotkeys": {"talk": "f8", "clear": "f7", "stop_voice": "f9", "replay": "shift+f9"},
    "hold_seconds": 0.4,        # pressing the talk key longer than this = hold-to-talk; shorter = tap (hands-free)
    "auto_send": True,          # press Bionic's Send once the transcription is final
    "auto_stop": True,          # after a tap, stop recording after `silence_seconds` without speech
    "silence_seconds": 4,
    "silence_options": [2, 3, 4, 5, 6, 8, 10],
    "vad_threshold": 0.5,       # 0-1; raise it if background noise keeps the recording going
    "vad_model": "models/silero_vad.onnx",
    "input_device": None,       # microphone for silence detection; None = Windows default (what Bionic uses)
    "read_aloud": True,         # speak every finished answer
    "read_mode": "final",       # "final": last assistant message of the turn; "all": every message in the turn
    "voice": "am_michael",      # any built-in Kokoro voice (language follows the voice) or a file name from custom_voices_dir
    "speed": 1.0,
    "speeds": [0.9, 1.0, 1.15, 1.3],
    "model": "models/kokoro-v1.0.onnx",
    "voices_file": "models/voices-v1.0.bin",
    "custom_voices_dir": "voices",  # drop extra .pt / .npy Kokoro voices here
    "output_device": None,      # sounddevice name or index; None = Windows default
    "dock_side": "right",       # "right" or "left" of the Bionic window
    "dock_offset": None,        # px from the window's top edge; None = vertically centered
    "bionic_home": "~/.lmstudio/apps/bionic",
}

OBSOLETE = ["push_to_talk",  # hold vs. tap is now decided per key press
            "voices", "lang"]  # every voice is offered now, and each speaks its own language

_lock = threading.Lock()


def _merge(base, override):
    out = copy.deepcopy(base)
    for key, value in override.items():
        out[key] = _merge(out[key], value) if isinstance(out.get(key), dict) and isinstance(value, dict) else value
    return out


def load():
    user = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            user = json.load(f)
    cfg = _merge(DEFAULTS, user)
    stale = [key for key in OBSOLETE if key in cfg]
    for key in stale:
        del cfg[key]
    if stale or not os.path.exists(CONFIG_PATH):
        save(cfg)
    return cfg


def save(cfg):
    with _lock, open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def path(relative):
    return relative if os.path.isabs(relative) else os.path.join(APP_DIR, relative)
