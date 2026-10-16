"""Small Win32 helpers (ctypes): find Bionic's window and keep the panel docked to it."""
import ctypes
import os
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
GW_HWNDPREV = 3
GA_ROOT = 2
HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
MONITOR_DEFAULTTONEAREST = 2
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_ALREADY_EXISTS = 183

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.MonitorFromWindow.restype = wintypes.HANDLE
user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE

_exe_names = {}
_mutex = None


def set_dpi_awareness():
    """Work in physical pixels everywhere, so window rects and Tk geometry agree."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


def single_instance(name):
    """False if another copy of the overlay is already running."""
    global _mutex
    _mutex = kernel32.CreateMutexW(None, False, name)
    return ctypes.get_last_error() != ERROR_ALREADY_EXISTS


def _exe_path(pid):
    path = ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if handle:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            path = buf.value
        kernel32.CloseHandle(handle)
    return path


def _exe_name(pid):
    if pid in _exe_names:
        return _exe_names[pid]
    if len(_exe_names) > 512:
        _exe_names.clear()
    _exe_names[pid] = os.path.basename(_exe_path(pid)).lower()
    return _exe_names[pid]


def bionic_install_dir():
    """Folder of the running Bionic.exe, or None."""
    hwnd = find_bionic_window()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    path = _exe_path(pid.value)
    return os.path.dirname(path) if path else None


def message_box(text, title):
    user32.MessageBoxW(None, text, title, 0x40)  # MB_ICONINFORMATION


def find_bionic_window():
    """The topmost Bionic main window (possibly minimized), or None."""
    found = []

    def check(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindowTextLengthW(hwnd) == 0:
            return True
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value != "Chrome_WidgetWin_1":
            return True
        if not user32.IsIconic(hwnd):
            left, top, right, bottom = frame_rect(hwnd)
            if right - left < 400 or bottom - top < 300:  # menus, popups, mini windows
                return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if _exe_name(pid.value) != "bionic.exe":
            return True
        found.append(hwnd)
        return False  # EnumWindows walks top to bottom, so the first hit is the frontmost

    user32.EnumWindows(WNDENUMPROC(check), 0)
    return found[0] if found else None


def is_window(hwnd):
    return bool(hwnd) and bool(user32.IsWindow(hwnd)) and bool(user32.IsWindowVisible(hwnd))


def is_minimized(hwnd):
    return bool(user32.IsIconic(hwnd))


def frame_rect(hwnd):
    """Visible frame (left, top, right, bottom), without the invisible resize border."""
    rect = wintypes.RECT()
    if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect)):
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def work_area(hwnd):
    info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST), ctypes.byref(info))
    r = info.rcWork
    return r.left, r.top, r.right, r.bottom


def dpi_scale(hwnd=None):
    try:
        dpi = user32.GetDpiForWindow(hwnd) if hwnd else user32.GetDpiForSystem()
    except Exception:
        dpi = 96
    return (dpi or 96) / 96


def toplevel_of(tk_window_id):
    return user32.GetAncestor(tk_window_id, GA_ROOT)


def style_panel(hwnd):
    """No taskbar button, and clicking the panel never takes focus from the user's window."""
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)


def _is_topmost(hwnd):
    return bool(user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST)


def _is_cloaked(hwnd):
    """Hidden by Windows itself (another virtual desktop, suspended app) although 'visible'."""
    cloaked = ctypes.c_int(0)
    dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
    return bool(cloaked.value)


def _on_screen(hwnd):
    return bool(user32.IsWindowVisible(hwnd)) and not user32.IsIconic(hwnd) and not _is_cloaked(hwnd)


def _overlaps(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _visible_above(hwnd):
    above = user32.GetWindow(hwnd, GW_HWNDPREV)
    while above and not _on_screen(above):
        above = user32.GetWindow(above, GW_HWNDPREV)
    return above


def keep_visible_with(mine, target):
    """Stack `mine` so it is visible whenever `target` is.

    The panel sits beside Bionic, so a window that doesn't cover any part of Bionic must not
    hide it either, even if that window is higher in z-order. So:
      - nothing covers Bionic: the panel is always-on-top. (Windows ignores a background
        app's request to raise its window above the active app, e.g. right after Bionic
        is restored; making it topmost is allowed.)
      - something covers Bionic: the panel is a normal window just below the lowest such
        window, so it hides exactly when Bionic does.
    """
    target_rect = frame_rect(target)
    cover = None
    above = user32.GetWindow(target, GW_HWNDPREV)
    while above:
        if above != mine and _on_screen(above):
            if _is_topmost(above):
                break
            if _overlaps(frame_rect(above), target_rect):
                cover = above
                break
        above = user32.GetWindow(above, GW_HWNDPREV)

    flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
    if cover is None:
        if not _is_topmost(mine):
            user32.SetWindowPos(mine, HWND_TOPMOST, 0, 0, 0, 0, flags)
        return
    if _is_topmost(mine):
        user32.SetWindowPos(mine, HWND_NOTOPMOST, 0, 0, 0, 0, flags)
    if _visible_above(mine) != cover:
        user32.SetWindowPos(mine, cover, 0, 0, 0, 0, flags)
