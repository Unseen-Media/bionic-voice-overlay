"""Download the Kokoro-82M voice (about 350 MB) and the Silero VAD model (2 MB) into ./models."""
import os
import urllib.request

KOKORO = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
FILES = {
    "kokoro-v1.0.onnx": KOKORO + "kokoro-v1.0.onnx",
    "voices-v1.0.bin": KOKORO + "voices-v1.0.bin",
    "silero_vad.onnx": "https://raw.githubusercontent.com/snakers4/silero-vad/v5.1.2/src/silero_vad/data/silero_vad.onnx",
}
MODELS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")


def main():
    os.makedirs(MODELS, exist_ok=True)
    for name, url in FILES.items():
        target = os.path.join(MODELS, name)
        if os.path.exists(target):
            print(f"{name}: already there")
            continue
        print(f"{name}: downloading...")
        urllib.request.urlretrieve(url, target + ".part")
        os.replace(target + ".part", target)
    print("Done.")


if __name__ == "__main__":
    main()
