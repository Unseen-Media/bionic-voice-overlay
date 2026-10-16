"""Voices the overlay can speak with: Kokoro's built-in pack (54 voices, 9 languages) plus
any extra voice files dropped into ./voices (.pt from Kokoro generators, or .npy).

Kokoro voice names start with two letters: language (a = American English, b = British, ...)
and gender (f/m), e.g. af_heart. Each voice speaks its own language, so a Spanish voice
reads text as Spanish.
"""
import logging
import os
import pickletools
import zipfile

import numpy as np

log = logging.getLogger(__name__)

# name prefix -> (menu name, espeak language used to pronounce the text, sample sentence)
LANGUAGES = {
    "a": ("American English", "en-us", "Hi, I'm {}."),
    "b": ("British English", "en-gb", "Hi, I'm {}."),
    "e": ("Spanish", "es", "Hola, soy {}."),
    "f": ("French", "fr-fr", "Bonjour, je suis {}."),
    "h": ("Hindi", "hi", "नमस्ते, मैं {} हूँ।"),
    "i": ("Italian", "it", "Ciao, sono {}."),
    "j": ("Japanese", "ja", "こんにちは、{}です。"),
    "p": ("Portuguese (Brazil)", "pt-br", "Olá, eu sou {}."),
    "z": ("Mandarin Chinese", "cmn", "你好，我是{}。"),
}
BEST = ["af_heart", "af_bella"]  # the top-rated voices on the Kokoro model card, best first
STYLE_WIDTH = 256

# A .pt voice is a zip with a tiny pickle that just rebuilds one float tensor.
# These are the only imports such a pickle needs; anything else is refused unread.
_SAFE_PICKLE_GLOBALS = {"torch._utils _rebuild_tensor_v2", "torch FloatStorage", "collections OrderedDict"}


def _has_prefix(name):
    return len(name) > 3 and name[0] in LANGUAGES and name[1] in "fm" and name[2] == "_"


def lang_of(name):
    return LANGUAGES[name[0]][1] if _has_prefix(name) else "en-us"


def label(name):
    """af_heart -> Heart; custom file names without a Kokoro prefix are kept whole."""
    return (name[3:] if _has_prefix(name) else name).replace("_", " ").capitalize()


def sample_sentence(name):
    return LANGUAGES[name[0] if _has_prefix(name) else "a"][2].format(label(name))


def sort_key(name):
    order = list(LANGUAGES)
    return (order.index(name[0]) if _has_prefix(name) else len(order), name[1:2] != "f", name)


def builtin(pack_path):
    with np.load(pack_path) as pack:
        return sorted(pack.files, key=sort_key)


def english_groups(builtin_names, custom_names):
    """Columns for the voice picker: [(title, [(subtitle, [names])])], best-rated voices first."""
    def pick(prefix):
        return sorted((n for n in builtin_names if n.startswith(prefix)),
                      key=lambda n: (BEST.index(n) if n in BEST else len(BEST), n))

    groups = [(title, [("Female", pick(p + "f_")), ("Male", pick(p + "m_"))])
              for title, p in (("United States", "a"), ("United Kingdom", "b"))]
    if custom_names:
        groups.append(("Custom", [("", sorted(custom_names))]))
    return groups


def _read_pt(path):
    """Read a Kokoro .pt voice without unpickling it (pickles can run code)."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        pkl = next(n for n in names if n.endswith("/data.pkl"))
        used = set()
        for op, arg, _ in pickletools.genops(z.read(pkl)):
            if op.name == "GLOBAL":
                used.add(arg)
            elif op.name in ("STACK_GLOBAL", "INST", "OBJ"):
                raise ValueError("unexpected pickle contents")
        if not used <= _SAFE_PICKLE_GLOBALS:
            raise ValueError(f"unexpected pickle imports: {sorted(used - _SAFE_PICKLE_GLOBALS)}")
        blobs = [n for n in names if "/data/" in n and not n.endswith("/")]
        if len(blobs) != 1:
            raise ValueError("expected exactly one tensor")
        order = z.read(next(n for n in names if n.endswith("/byteorder"))).decode() if any(
            n.endswith("/byteorder") for n in names) else "little"
        raw = np.frombuffer(z.read(blobs[0]), dtype="<f4" if order == "little" else ">f4")
    return raw.astype(np.float32)


def load_custom(folder):
    """{name: style array} for every readable voice file in `folder`."""
    voices = {}
    if not os.path.isdir(folder):
        return voices
    for file in sorted(os.listdir(folder)):
        name, ext = os.path.splitext(file)
        path = os.path.join(folder, file)
        try:
            if ext == ".pt":
                data = _read_pt(path)
            elif ext == ".npy":
                data = np.load(path, allow_pickle=False).astype(np.float32)
            else:
                continue
            if data.size % STYLE_WIDTH:
                raise ValueError(f"size {data.size} is not a Kokoro voice")
            voices[name] = data.reshape(-1, 1, STYLE_WIDTH)
        except Exception as e:
            log.warning("skipping voice file %s: %s", file, e)
    return voices
