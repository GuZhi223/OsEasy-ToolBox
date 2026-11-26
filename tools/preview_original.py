"""Open the original Flet UI in disabled preview mode and close its own window."""
import ctypes as c
import subprocess
import sys
import time
from ctypes import wintypes as w
from pathlib import Path

import psutil
from PIL import ImageGrab

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from oseasy_auto.model import Identity
from oseasy_auto.windows import WindowBackend

executable = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else None
arguments = [str(executable), "--demo"] if executable else [sys.executable, str(ROOT / "run.py"), "--demo"]
artifacts = ROOT / "artifacts"
artifacts.mkdir(exist_ok=True)
backend = WindowBackend()
backend.u.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, c.c_int]
backend.u.GetWindowTextW.restype = c.c_int
backend.u.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
backend.u.PostMessageW.restype = w.BOOL

with (artifacts / "original-preview.log").open("w", encoding="utf-8") as log:
    process = subprocess.Popen(arguments, cwd=ROOT, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        deadline = time.monotonic() + 35
        hwnd = None
        while hwnd is None and time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Original UI exited; see artifacts/original-preview.log")
            parent = psutil.Process(process.pid)
            for child in [parent, *parent.children(recursive=True)]:
                try:
                    identity = Identity(child.pid, child.create_time(), child.exe())
                    for window in backend.enumerate(identity):
                        title = c.create_unicode_buffer(256)
                        backend.u.GetWindowTextW(window, title, len(title))
                        if "OsEasy-ToolBox" in title.value:
                            hwnd = window
                            break
                except psutil.Error:
                    pass
            time.sleep(.15)
        if hwnd is None:
            raise RuntimeError("Original Flet window did not appear; see artifacts/original-preview.log")
        time.sleep(2)
        parent = psutil.Process(process.pid)
        children = parent.children(recursive=True)
        owner = next(child for child in children if child.pid == backend.pid(hwnd))
        identity = Identity(owner.pid, owner.create_time(), owner.exe())
        rect = backend.snapshot(hwnd, identity)["rect"]
        ImageGrab.grab(tuple(rect)).save(artifacts / "original-ui-preview.png")
        if not backend.u.PostMessageW(hwnd, 0x10, 0, 0):
            raise c.WinError(c.get_last_error())
        code = process.wait(timeout=15)
        if code:
            raise RuntimeError(f"Original preview exited with {code}")
        print("Original Flet UI opened and closed; screenshot: artifacts/original-ui-preview.png")
    finally:
        if process.poll() is None:
            try:
                for child in reversed(psutil.Process(process.pid).children(recursive=True)):
                    try:
                        child.terminate()
                    except psutil.NoSuchProcess:
                        pass
            except psutil.NoSuchProcess:
                pass
            process.terminate()
            process.wait(timeout=10)
