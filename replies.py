"""Watches Bionic's session databases and reports every finished assistant turn.

Bionic keeps each project's chats in projects/<id>/.internal/ng-sessions.sqlite:
a `sessions` row points at its newest entry, and entries form a linked list
(entry_json.previousId). A completed turn ends with a
{"type": "forkPoint", "forkPoint": "assistantTurnEnd"} entry; the answer is the
"text" parts of the assistant messages after the user's prompt ("reasoning"
parts are the model thinking and are skipped). Databases are opened read-only.
"""
import glob
import json
import logging
import os
import pathlib
import sqlite3
import threading
import time

log = logging.getLogger(__name__)


class ReplyWatcher(threading.Thread):
    def __init__(self, bionic_home, on_reply, read_mode, on_silent_turn=None, poll=0.4):
        super().__init__(daemon=True, name="replies")
        home = os.path.expanduser(bionic_home)
        self.pattern = os.path.join(home, "projects", "*", ".internal", "ng-sessions.sqlite")
        self.on_reply = on_reply
        self.on_silent_turn = on_silent_turn or (lambda: None)  # a turn ended without new text (e.g. interrupted)
        self.read_mode = read_mode  # callable -> "final" | "all"
        self.poll = poll
        self.started_ms = int(time.time() * 1000)  # never speak answers that existed before we started
        self.heads = {}
        self.handled = set()

    def run(self):
        while True:
            for db in glob.glob(self.pattern):
                try:
                    self._check(db)
                except sqlite3.Error as e:  # Bionic is mid-write or the file is locked; try next round
                    log.debug("skipping %s: %s", db, e)
                except Exception:
                    log.exception("reply watcher failed on %s", db)
            time.sleep(self.poll)

    def _check(self, db):
        con = sqlite3.connect(pathlib.Path(db).as_uri() + "?mode=ro", uri=True, timeout=1)
        try:
            rows = con.execute(
                "SELECT session_id, committed_head_entry_id FROM sessions "
                "WHERE is_transient = 0 AND parent_session_id IS NULL").fetchall()
            for session_id, head in rows:
                if not head or self.heads.get(session_id) == head:
                    continue
                seen_before = session_id in self.heads
                self.heads[session_id] = head
                text = self._finished_turn_text(con, head)
                if text:
                    self.on_reply(text)
                elif text == "" and seen_before:
                    self.on_silent_turn()
        finally:
            con.close()

    @staticmethod
    def _entry(con, entry_id):
        row = con.execute("SELECT entry_json, previous_id FROM chat_entries WHERE id = ?", (entry_id,)).fetchone()
        if not row or not row[0]:
            return None
        entry = json.loads(row[0])
        entry.setdefault("previousId", row[1])
        return entry

    def _finished_turn_text(self, con, head):
        entry = self._entry(con, head)
        if not entry or entry.get("type") != "forkPoint" or entry.get("forkPoint") != "assistantTurnEnd":
            return None
        answers = []  # newest first
        entry_id = entry.get("previousId")
        for _ in range(5000):
            entry = self._entry(con, entry_id) if entry_id else None
            if entry is None:
                break
            message = entry.get("message") or {}
            if entry.get("type") == "message" and not entry.get("hidden"):
                if message.get("role") == "user" and "source" not in entry:
                    break  # reached the prompt that started this turn
                if message.get("role") == "assistant":
                    text = "\n\n".join(p.get("text", "") for p in message.get("parts", [])
                                       if p.get("type") == "text").strip()
                    if text:
                        answers.append((entry.get("id"), entry.get("createdTimestamp", 0), text))
            entry_id = entry.get("previousId")

        fresh = [a for a in answers if a[0] not in self.handled and a[1] >= self.started_ms]
        self.handled.update(a[0] for a in answers)
        if not fresh:
            return ""  # the turn ended, but there is nothing new to read
        if self.read_mode() == "all":
            return "\n\n".join(a[2] for a in reversed(fresh))
        return fresh[0][2]
