"""Start the overlay when the user signs in to Windows (per-user Run key, no admin rights needed).

It starts with --background: nothing is shown and no voice model is loaded until a Bionic
window appears, so to the user it looks like the overlay starts together with Bionic.
"""
import os
import sys
import winreg

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "BionicVoice"


def command():
    if getattr(sys, "frozen", False):  # the packaged BionicVoice.exe
        return f'"{sys.executable}" --background'
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    main = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
    return f'"{pythonw}" "{main}" --background'


def _current():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, NAME)[0]
    except OSError:
        return None


def is_enabled():
    return _current() is not None


def set_enabled(on):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if on:
            winreg.SetValueEx(key, NAME, 0, winreg.REG_SZ, command())
        elif is_enabled():
            winreg.DeleteValue(key, NAME)


def refresh():
    """If enabled, point the entry at this copy (e.g. after the folder was moved)."""
    if is_enabled() and _current() != command():
        set_enabled(True)
