"""Auto-stop on silence: listens to the microphone while Bionic records and reports when
nobody has spoken for a while.

Bionic still does the transcription; this only measures whether someone is talking,
with the Silero VAD model (2 MB, ONNX, runs locally on the CPU).
"""
import logging
import math
import queue
import threading
import time

import numpy as np
import onnxruntime as ort
import sounddevice as sd

log = logging.getLogger(__name__)

RATE = 16000
FRAME = 512       # 32 ms: the block size Silero expects at 16 kHz
CONTEXT = 64      # Silero v5 also sees the last 64 samples of the previous block
MIN_VOICED = 3    # ~100 ms of speech before it counts, so clicks and bumps don't
COUNTDOWN_FROM = 3


class SilenceDetector:
    def __init__(self, model_path, device=None, threshold=0.5):
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = opts.intra_op_num_threads = 1
        self.session = ort.InferenceSession(model_path, sess_options=opts, providers=["CPUExecutionProvider"])
        self.device, self.threshold = device, threshold
        self._stop = threading.Event()

    def start(self, seconds, on_silence, on_countdown=None):
        """Call on_silence() once nobody has spoken for `seconds` (counted from now).

        on_countdown(n) reports the last few seconds (3, 2, 1) and None when speech resumes.
        Raises if the microphone can't be opened.
        """
        self.stop()
        stop = self._stop = threading.Event()
        blocks = queue.Queue()
        stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32", blocksize=FRAME,
                                device=self.device, callback=lambda data, *_: blocks.put(data[:, 0].copy()))
        stream.start()
        threading.Thread(target=self._run, args=(stream, blocks, stop, seconds, on_silence, on_countdown),
                         daemon=True, name="vad").start()

    def stop(self):
        self._stop.set()

    def _run(self, stream, blocks, stop, seconds, on_silence, on_countdown):
        state = np.zeros((2, 1, 128), np.float32)
        context = np.zeros((1, CONTEXT), np.float32)
        rate = np.array(RATE, dtype=np.int64)
        last_voice, voiced, shown = time.monotonic(), 0, None
        try:
            while not stop.is_set():
                try:
                    block = blocks.get(timeout=0.1)
                except queue.Empty:
                    block = None
                if block is not None and len(block) == FRAME:
                    x = np.concatenate([context, block[None, :]], axis=1)
                    prob, state = self.session.run(None, {"input": x, "state": state, "sr": rate})
                    context = x[:, -CONTEXT:]
                    voiced = voiced + 1 if prob[0][0] >= self.threshold else 0
                    if voiced >= MIN_VOICED:
                        last_voice = time.monotonic()
                remaining = seconds - (time.monotonic() - last_voice)
                if remaining <= 0:
                    if not stop.is_set():
                        on_silence()
                    return
                tick = math.ceil(remaining) if remaining <= COUNTDOWN_FROM else None
                if tick != shown and on_countdown and not stop.is_set():
                    shown = tick
                    on_countdown(tick)
        except Exception:
            log.exception("silence detection stopped")
        finally:
            stream.stop()
            stream.close()
