"""Bionic's control labels in every UI language, read from the installed Bionic itself.

The overlay finds Bionic's buttons by their accessible names, which follow Bionic's UI
language. Bionic ships each translation under a fixed message ID inside its renderer
bundle, so reading them from there works for any language Bionic supports, and keeps
working after updates as long as the IDs stay the same. English is always included.
"""
import json
import logging
import os
import re

log = logging.getLogger(__name__)

# What the overlay needs -> (Bionic message ID, English text)
MESSAGES = {
    "start": ("20qUvS", "Start transcription"),
    "stop": ("lQIPma", "Stop transcription"),
    "setup": ("AyWLYA", "Set up voice transcription"),
    "ask_permission": ("guN+pS", "Request microphone permission"),
    "denied": ("zCzIEc", "Microphone permission denied"),
    "send": ("9WRlF4", "Send"),
    "interrupt": ("PYWls5", "Interrupt"),
    "processing": ("0x9XMF", "Processing dictated text."),
}
BUNDLE = os.path.join("resources", "app", ".webpack-bionic", "renderer", "main_window.js")
DEFAULT_INSTALL = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Bionic")


def english():
    return {key: [text] for key, (_, text) in MESSAGES.items()}


def load(install_dirs=()):
    """{key: [label in each language, English first]} from the first Bionic install found."""
    labels = english()
    for folder in [*install_dirs, DEFAULT_INSTALL]:
        path = os.path.join(folder, BUNDLE)
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError as e:
            log.warning("can't read %s: %s", path, e)
            continue
        by_id = {msg_id: key for key, (msg_id, _) in MESSAGES.items()}
        ids = "|".join(re.escape(msg_id) for msg_id in by_id)
        pattern = re.compile('"(' + ids + ')":' + re.escape('[{"type":0,"value":"') + r'((?:[^"\\]|\\.)*)"\}\]')
        for match in pattern.finditer(text):
            try:
                value = json.loads('"' + match.group(2) + '"')
            except ValueError:
                continue
            bucket = labels[by_id[match.group(1)]]
            if value and value not in bucket:
                bucket.append(value)
        log.info("read Bionic's labels in %d languages from %s", len(labels["start"]), path)
        break
    else:
        log.warning("Bionic install not found; only English labels are known")
    return labels
