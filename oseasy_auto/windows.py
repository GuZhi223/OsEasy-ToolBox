from __future__ import annotations

import ctypes as c
import secrets
from ctypes import wintypes as w

from .model import Identity


class POINT(c.Structure):
    _fields_ = [("x", w.LONG), ("y", w.LONG)]


class RECT(c.Structure):
    _fields_ = [("left", w.LONG), ("top", w.LONG), ("right", w.LONG), ("bottom", w.LONG)]


class PLACEMENT(c.Structure):
    _fields_ = [("length", w.UINT), ("flags", w.UINT), ("showCmd", w.UINT),
                ("min", POINT), ("max", POINT), ("rect", RECT)]


class MONITORINFO(c.Structure):
    _fields_ = [("size", w.DWORD), ("monitor", RECT), ("work", RECT), ("flags", w.DWORD)]


class ControllerOwner:
    """Windows releases this handle on a crash; it never persists ownership."""

    def __init__(self):
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [c.c_void_p, w.BOOL, w.LPCWSTR]
        self.kernel.CreateMutexW.restype = w.HANDLE
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.kernel.CloseHandle.restype = w.BOOL
        c.set_last_error(0)
        self.handle = self.kernel.CreateMutexW(None, False, "Local\\OsEasyAuto.Controller")
        error = c.get_last_error()
        if not self.handle:
            raise c.WinError(error)
        if error == 183:
            self.close()
            raise OSError("已有自动模式正在运行，请先停止另一个窗口中的自动模式")

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class WindowBackend:
    OWNER_PROPERTY = "OsEasyAuto.WindowOwner"
    CAPTION = 0xC00000
    FRAME = 0xCF0000
    TOPMOST = 0x8
    CHILD = 0x40000000
    CALLBACK = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

    def __init__(self):
        self.u = c.WinDLL("user32", use_last_error=True)
        specs = {
            "EnumWindows": ([self.CALLBACK, w.LPARAM], w.BOOL),
            "GetWindowThreadProcessId": ([w.HWND, c.POINTER(w.DWORD)], w.DWORD),
            "IsWindow": ([w.HWND], w.BOOL), "IsWindowVisible": ([w.HWND], w.BOOL),
            "IsIconic": ([w.HWND], w.BOOL), "GetClassNameW": ([w.HWND, w.LPWSTR, c.c_int], c.c_int),
            "GetWindowRect": ([w.HWND, c.POINTER(RECT)], w.BOOL),
            "GetWindowPlacement": ([w.HWND, c.POINTER(PLACEMENT)], w.BOOL),
            "SetWindowPlacement": ([w.HWND, c.POINTER(PLACEMENT)], w.BOOL),
            "SetWindowPos": ([w.HWND, w.HWND, c.c_int, c.c_int, c.c_int, c.c_int, w.UINT], w.BOOL),
            "MonitorFromWindow": ([w.HWND, w.DWORD], w.HANDLE),
            "GetMonitorInfoW": ([w.HANDLE, c.POINTER(MONITORINFO)], w.BOOL),
            "ShowWindow": ([w.HWND, c.c_int], w.BOOL),
            "SetPropW": ([w.HWND, w.LPCWSTR, w.HANDLE], w.BOOL),
            "GetPropW": ([w.HWND, w.LPCWSTR], w.HANDLE),
            "RemovePropW": ([w.HWND, w.LPCWSTR], w.HANDLE),
        }
        for name, (args, result) in specs.items():
            function = getattr(self.u, name)
            function.argtypes, function.restype = args, result
        suffix = "PtrW" if c.sizeof(c.c_void_p) == 8 else "W"
        self.get_long = getattr(self.u, "GetWindowLong" + suffix)
        self.get_long.argtypes, self.get_long.restype = [w.HWND, c.c_int], c.c_ssize_t
        self.set_long = getattr(self.u, "SetWindowLong" + suffix)
        self.set_long.argtypes, self.set_long.restype = [w.HWND, c.c_int, c.c_ssize_t], c.c_ssize_t

    def _check(self, result):
        if not result:
            raise c.WinError(c.get_last_error())
        return result

    def _set(self, hwnd: int, index: int, value: int):
        c.set_last_error(0)
        if self.set_long(hwnd, index, value) == 0 and c.get_last_error():
            raise c.WinError(c.get_last_error())

    def pid(self, hwnd: int) -> int:
        pid = w.DWORD()
        self.u.GetWindowThreadProcessId(hwnd, c.byref(pid))
        return pid.value

    def window_class(self, hwnd: int) -> str:
        text = c.create_unicode_buffer(256)
        self.u.GetClassNameW(hwnd, text, len(text))
        return text.value

    def enumerate(self, identity: Identity) -> list[int]:
        found = []

        @self.CALLBACK
        def visit(hwnd, _):
            if self.pid(hwnd) == identity.pid and self.u.IsWindowVisible(hwnd):
                rect = RECT()
                if self.u.GetWindowRect(hwnd, c.byref(rect)):
                    if rect.right - rect.left >= 200 and rect.bottom - rect.top >= 120:
                        if not self.get_long(hwnd, -16) & self.CHILD:
                            found.append(int(hwnd))
            return True

        self._check(self.u.EnumWindows(visit, 0))
        return found

    def snapshot(self, hwnd: int, identity: Identity) -> dict:
        placement = PLACEMENT(length=c.sizeof(PLACEMENT))
        self._check(self.u.GetWindowPlacement(hwnd, c.byref(placement)))
        rect = RECT()
        self._check(self.u.GetWindowRect(hwnd, c.byref(rect)))
        return {"hwnd": hwnd, "pid": identity.pid, "created": identity.created, "path": identity.path,
                "marker": secrets.randbits(30) + 1,
                "class": self.window_class(hwnd), "style": self.get_long(hwnd, -16),
                "exstyle": self.get_long(hwnd, -20), "rect": [rect.left, rect.top, rect.right, rect.bottom], "placement": {
                    "flags": placement.flags, "show": placement.showCmd,
                    "min": [placement.min.x, placement.min.y], "max": [placement.max.x, placement.max.y],
                    "rect": [placement.rect.left, placement.rect.top, placement.rect.right, placement.rect.bottom]}}

    def work_area(self, hwnd):
        info = MONITORINFO(size=c.sizeof(MONITORINFO))
        monitor = self.u.MonitorFromWindow(hwnd, 2)
        self._check(self.u.GetMonitorInfoW(monitor, c.byref(info)))
        return info.work

    def is_windowed(self, hwnd: int) -> bool:
        style, exstyle = self.get_long(hwnd, -16), self.get_long(hwnd, -20)
        return style & self.CAPTION == self.CAPTION and not exstyle & self.TOPMOST

    def windowed(self, hwnd: int):
        if self.u.IsIconic(hwnd):
            return  # Respect a user-minimized broadcast.
        if self.is_windowed(hwnd):
            return
        area = self.work_area(hwnd)
        width = max(200, min(1000, (area.right - area.left) * 3 // 4))
        height = max(160, min(720, (area.bottom - area.top) * 3 // 4))
        self._set(hwnd, -16, self.get_long(hwnd, -16) | self.FRAME)
        self._set(hwnd, -20, self.get_long(hwnd, -20) & ~self.TOPMOST)
        self._check(self.u.SetWindowPos(hwnd, -2, area.left + 35, area.top + 35, width, height, 0x30))
        if not self.is_windowed(hwnd):
            raise OSError("广播程序未接受窗口状态修改")

    def fullscreen(self, hwnd: int):
        area = self.work_area(hwnd)
        self._set(hwnd, -16, self.get_long(hwnd, -16) & ~self.FRAME)
        self._check(self.u.SetWindowPos(hwnd, -2, area.left, area.top,
                                       area.right - area.left, area.bottom - area.top, 0x30))

    def exists(self, snapshot: dict) -> bool:
        hwnd = snapshot["hwnd"]
        return (bool(self.u.IsWindow(hwnd)) and self.pid(hwnd) == snapshot["pid"]
                and self.window_class(hwnd) == snapshot["class"]
                and snapshot.get("marker") is not None
                and self.u.GetPropW(hwnd, self.OWNER_PROPERTY) == snapshot.get("marker"))

    def mark(self, snapshot: dict):
        existing = self.u.GetPropW(snapshot["hwnd"], self.OWNER_PROPERTY)
        if existing and existing != snapshot["marker"]:
            raise OSError("窗口属于不同会话，未修改该窗口")
        self._check(self.u.SetPropW(snapshot["hwnd"], self.OWNER_PROPERTY, snapshot["marker"]))

    def restore(self, snapshot: dict):
        hwnd, saved = snapshot["hwnd"], snapshot["placement"]
        visible = self.u.IsWindowVisible(hwnd)
        desired_style = snapshot["style"] if visible else snapshot["style"] & ~0x10000000
        self._set(hwnd, -16, desired_style)
        self._set(hwnd, -20, snapshot["exstyle"])
        placement = PLACEMENT(length=c.sizeof(PLACEMENT), flags=saved["flags"], showCmd=saved["show"] if visible else 0,
                              min=POINT(*saved["min"]), max=POINT(*saved["max"]), rect=RECT(*saved["rect"]))
        self._check(self.u.SetWindowPlacement(hwnd, c.byref(placement)))
        insert_after = -1 if snapshot["exstyle"] & self.TOPMOST else -2
        if saved["show"] not in (2, 3, 6, 7) and "rect" in snapshot:
            left, top, right, bottom = snapshot["rect"]
            self._check(self.u.SetWindowPos(hwnd, insert_after, left, top, right - left, bottom - top, 0x30))
        else:
            self._check(self.u.SetWindowPos(hwnd, insert_after, 0, 0, 0, 0, 0x33))
        if not visible:
            self.u.ShowWindow(hwnd, 0)  # Do not revive a broadcast the teacher already hid.
        if self.get_long(hwnd, -16) != desired_style or self.get_long(hwnd, -20) != snapshot["exstyle"]:
            raise OSError("窗口原状态未恢复，已保留恢复记录")
        if saved["show"] not in (2, 3, 6, 7) and "rect" in snapshot:
            current = RECT()
            self._check(self.u.GetWindowRect(hwnd, c.byref(current)))
            if [current.left, current.top, current.right, current.bottom] != snapshot["rect"]:
                raise OSError("窗口位置尚未恢复，已保留恢复记录")
        self.u.RemovePropW(hwnd, self.OWNER_PROPERTY)
