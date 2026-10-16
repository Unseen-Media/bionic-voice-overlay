"""Build a shareable Bionic Voice package: dist/BionicVoice/ (BionicVoice.exe + models) and a .zip of it.

    python build.py

Needs the dev setup (pip install -r requirements.txt pyinstaller) and the models (python download_models.py).
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "build")
DIST = os.path.join(HERE, "dist")
APP = os.path.join(DIST, "BionicVoice")
SOURCES = ["main.py", "panel.py", "bionic_ui.py", "labels.py", "replies.py", "speech.py", "vad.py", "voices.py",
           "hotkeys.py", "winutil.py", "config.py", "autostart.py", "download_models.py", "build.py",
           "requirements.txt", "start.bat", "README.md", "LICENSE", ".gitignore"]
MODELS = ["kokoro-v1.0.onnx", "voices-v1.0.bin", "silero_vad.onnx"]


def make_icon(path):
    """A microphone glyph on a dark rounded square, like the panel's Talk button."""
    from PIL import Image, ImageDraw, ImageFont
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((8, 8, size - 8, size - 8), radius=56, fill=(27, 29, 33, 255), outline=(127, 176, 255, 255), width=8)
    font = None
    for name in ("SegoeIcons.ttf", "segmdl2.ttf"):
        try:
            font = ImageFont.truetype(os.path.join(os.environ["WINDIR"], "Fonts", name), 150)
            break
        except OSError:
            continue
    if font:
        draw.text((size / 2, size / 2), "", font=font, fill=(127, 176, 255, 255), anchor="mm")
    img.save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def main():
    missing = [m for m in MODELS if not os.path.exists(os.path.join(HERE, "models", m))]
    if missing:
        sys.exit(f"Missing models: {missing}. Run: python download_models.py")
    os.makedirs(BUILD, exist_ok=True)
    icon = os.path.join(BUILD, "icon.ico")
    make_icon(icon)

    subprocess.run([
        sys.executable, "-m", "PyInstaller", os.path.join(HERE, "main.py"),
        "--name", "BionicVoice", "--windowed", "--noconfirm", "--clean", "--icon", icon,
        "--distpath", DIST, "--workpath", os.path.join(BUILD, "work"), "--specpath", BUILD,
        # Packages that load data files or DLLs at runtime:
        "--collect-all", "kokoro_onnx", "--collect-all", "espeakng_loader", "--collect-all", "phonemizer",
        "--collect-all", "uiautomation", "--collect-data", "language_tags", "--collect-data", "csvw",
        "--collect-data", "segments",
        "--exclude-module", "PIL", "--exclude-module", "matplotlib", "--exclude-module", "IPython",
    ], check=True, cwd=HERE)

    # Everything the app reads next to the exe, plus notices and the source.
    shutil.copytree(os.path.join(HERE, "models"), os.path.join(APP, "models"), dirs_exist_ok=True,
                    ignore=lambda _d, names: [n for n in names if n not in MODELS])
    os.makedirs(os.path.join(APP, "voices"), exist_ok=True)
    for name in ("package/HOW-TO-INSTALL.txt", "package/THIRD-PARTY-NOTICES.txt", "LICENSE"):
        shutil.copy(os.path.join(HERE, name), APP)
    source = os.path.join(APP, "source")
    os.makedirs(source, exist_ok=True)
    for name in SOURCES:
        shutil.copy(os.path.join(HERE, name), source)
    shutil.copytree(os.path.join(HERE, "package"), os.path.join(source, "package"), dirs_exist_ok=True)

    sys.path.insert(0, HERE)
    from config import VERSION
    archive = os.path.join(DIST, f"BionicVoice-{VERSION}-windows-x64")
    shutil.make_archive(archive, "zip", DIST, "BionicVoice")
    print(f"\nBuilt {APP}\nZip:  {archive}.zip ({os.path.getsize(archive + '.zip') / 2**20:.0f} MB)")


if __name__ == "__main__":
    main()
