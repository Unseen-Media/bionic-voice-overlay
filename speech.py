"""Local text-to-speech with Kokoro-82M (ONNX, runs offline).

Text is spoken a sentence or two at a time: the next chunk is synthesized while
the current one plays, so speech starts quickly and runs without long gaps.
"""
import logging
import queue
import re
import threading
import time

import sounddevice as sd

import voices

log = logging.getLogger(__name__)

_FENCE = re.compile(r"```.*?(?:```|\Z)", re.S)
_TABLE_RULE = re.compile(r"\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?")


def to_speech(markdown):
    """Turn a markdown answer into plain sentences that sound right read aloud."""
    t = _FENCE.sub("\nCode block omitted.\n", markdown)
    t = re.sub(r"<think>.*?</think>", " ", t, flags=re.S)
    t = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"https?://\S+", "link", t)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"</?[A-Za-z][^>\n]*>", " ", t)
    lines = []
    for line in t.splitlines():
        s = line.strip()
        if not s or _TABLE_RULE.fullmatch(s) or re.fullmatch(r"[-*_]{3,}", s):
            continue
        s = re.sub(r"^#{1,6}\s*", "", s)
        s = re.sub(r"^>\s?", "", s)
        s = re.sub(r"^([-*+]|\d+[.)])\s+", "", s)
        if s.count("|") >= 2:
            s = ", ".join(cell.strip() for cell in s.strip("|").split("|") if cell.strip())
        if s and s[-1] not in ".!?:;,。！？，、":
            s += "."  # headings and list items get a pause
        lines.append(s)
    t = " ".join(lines)
    t = re.sub(r"(\*\*|__|~~)", "", t)
    t = re.sub(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])", r"\1", t)
    return re.sub(r"\s+", " ", t).strip()


def split_chunks(text, limit=260):
    """Sentence-sized pieces; the first is a single sentence so audio starts fast."""
    chunks, current = [], ""
    for sentence in re.split(r"(?<=[.!?…])\s+", text):
        while len(sentence) > limit:  # a run-on sentence: break at a comma or space
            cut = max(sentence.rfind(", ", 0, limit), sentence.rfind(" ", 0, limit))
            cut = cut if cut > limit // 3 else limit
            if current:
                chunks.append(current)
                current = ""
            chunks.append(sentence[:cut + 1].strip())
            sentence = sentence[cut + 1:].strip()
        if not chunks and not current:
            chunks.append(sentence)
        elif current and len(current) + len(sentence) + 1 > limit:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return [c for c in chunks if c]


class Speaker:
    def __init__(self, model_path, voices_path, voice, speed, device=None, custom_voices=None, on_state=None):
        self.voice, self.speed, self.device = voice, speed, device
        self.custom_voices = custom_voices or {}  # name -> style array, from ./voices
        self.on_state = on_state or (lambda speaking: None)
        self.ready = threading.Event()
        self.error = None
        self._paths = (model_path, voices_path)
        self._jobs = queue.Queue()
        self._generation = 0
        self._kokoro = None
        self._wanted = threading.Event()  # the model (~350 MB of RAM) loads only once it's needed
        threading.Thread(target=self._run, daemon=True, name="tts").start()

    def preload(self):
        self._wanted.set()

    def say(self, markdown):
        text = to_speech(markdown)
        if not text:
            return
        self._wanted.set()
        self._generation += 1
        sd.stop()
        self._jobs.put((self._generation, text))

    def stop(self):
        self._generation += 1
        sd.stop()

    def _load(self):
        from kokoro_onnx import Kokoro  # imported here so a missing model doesn't break startup
        self._kokoro = Kokoro(*self._paths)
        self._kokoro.create("Ready.", voice="af_heart", lang="en-us")  # warm-up
        log.info("voice model loaded")

    def _synth(self, text):
        voice = self.voice
        style = self.custom_voices.get(voice, voice)
        samples, rate = self._kokoro.create(text, voice=style, speed=self.speed, lang=voices.lang_of(voice))
        return samples, rate

    def _run(self):
        self._wanted.wait()
        try:
            self._load()
        except Exception as e:
            self.error = e
            log.exception("could not load the voice model")
        self.ready.set()
        while True:
            generation, text = self._jobs.get()
            if generation != self._generation or self._kokoro is None:
                continue
            self.on_state(True)
            try:
                self._speak(generation, text)
            except Exception:
                log.exception("speech failed")
            finally:
                if generation == self._generation or self._jobs.empty():
                    self.on_state(False)

    def _speak(self, generation, text):
        chunks = split_chunks(text)
        upcoming = self._synth(chunks[0])
        for i in range(len(chunks)):
            if generation != self._generation:
                return
            samples, rate = upcoming
            sd.play(samples, rate, device=self.device)
            # Synthesize the next piece while this one plays.
            upcoming = self._synth(chunks[i + 1]) if i + 1 < len(chunks) else None
            stream = sd.get_stream()
            while stream.active and generation == self._generation:
                time.sleep(0.02)
        if generation != self._generation:
            sd.stop()
