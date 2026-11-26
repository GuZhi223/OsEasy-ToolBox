"""Exercise real Windows APIs on an owned fixture; never launch the sample."""
import shutil
import subprocess
import time
from pathlib import Path

import psutil
import pytest

from oseasy_auto.instrumentation import Observer
from oseasy_auto.model import Identity
from oseasy_auto.windows import WindowBackend


@pytest.fixture
def target(tmp_path):
    compiler = shutil.which("gcc")
    if not compiler:
        pytest.skip("gcc is needed only to build the owned test fixture")
    executable = tmp_path / "fixture.exe"
    source = Path(__file__).parent / "native" / "target.c"
    subprocess.run([compiler, "-municode", "-static", "-O0", str(source), "-o", str(executable), "-luser32"], check=True, capture_output=True)
    child = tmp_path / "ScreenRender.exe"
    shutil.copy2(executable, child)
    process = subprocess.Popen([str(executable)], creationflags=subprocess.CREATE_NO_WINDOW)
    identity = Identity(process.pid, psutil.Process(process.pid).create_time(), str(executable))
    try:
        time.sleep(.4)
        yield identity, child, tmp_path
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_real_window_roundtrip(target):
    identity, _, _ = target
    windows = WindowBackend()
    hwnd = windows.enumerate(identity)[0]
    original = windows.snapshot(hwnd, identity)
    windows.mark(original)
    assert windows.exists(original)
    assert not windows.is_windowed(hwnd)
    try:
        windows.windowed(hwnd)
        assert windows.is_windowed(hwnd)
    finally:
        windows.restore(original)
    restored = windows.snapshot(hwnd, identity)
    assert restored["style"] == original["style"]
    assert restored["exstyle"] == original["exstyle"]
    assert restored["placement"]["rect"] == original["placement"]["rect"]
    assert not windows.exists(original)


def test_window_lifetime_marker_prevents_stale_restore(target):
    identity, _, _ = target
    windows = WindowBackend()
    hwnd = windows.enumerate(identity)[0]
    first, second = windows.snapshot(hwnd, identity), windows.snapshot(hwnd, identity)
    windows.mark(first)
    assert windows.exists(first) and not windows.exists(second)
    with pytest.raises(OSError):
        windows.mark(second)
    windows.restore(first)


def test_restoration_does_not_redisplay_ended_broadcast(target):
    identity, _, _ = target
    windows = WindowBackend()
    hwnd = windows.enumerate(identity)[0]
    original = windows.snapshot(hwnd, identity)
    windows.mark(original)
    windows.windowed(hwnd)
    windows.u.ShowWindow(hwnd, 0)
    windows.restore(original)
    assert not windows.u.IsWindowVisible(hwnd)


def test_hook_changes_only_renderer_and_unloads(target):
    import frida
    identity, child, directory = target
    device = frida.get_local_device()
    control = device.attach(identity.pid)
    helper = control.create_script("""
        const target = Process.enumerateModules().find(m => m.name.toLowerCase() === 'fixture.exe');
        const spawn = new NativeFunction(target.getExportByName('RunSpawn'), 'int', ['pointer', 'pointer']);
        const ioctl = new NativeFunction(target.getExportByName('RunUnrelatedIoctl'), 'uint', []);
        rpc.exports = {
            spawn(image, out) { return spawn(Memory.allocUtf16String(image), Memory.allocUtf16String(out)); },
            ioctl() { return ioctl(); }
        };
    """)
    script_errors = []
    helper.on("message", lambda message, data: script_errors.append(message) if message.get("type") == "error" else None)
    helper.load()
    observer = None
    try:
        assert not script_errors, script_errors
        baseline_error = helper.exports_sync.ioctl()
        events = []
        observer = Observer(identity, directory, "student", [str(child)], events.append)
        assert observer.capabilities["keyboard"]
        changed = directory / "adjusted.txt"
        assert helper.exports_sync.spawn(str(child), str(changed)) == 1
        text = changed.read_text()
        assert "#fullscreen#:0" in text and "#notfullscreen#:1" in text
        assert helper.exports_sync.ioctl() == baseline_error
        assert observer.close() == []
        observer = None
        restored = directory / "restored.txt"
        helper.exports_sync.spawn(str(child), str(restored))
        assert "#fullscreen#:1" in restored.read_text()
        assert any(event["kind"] == "broadcast_request" and event["changed"] for event in events)
        broadcast = next(event for event in events if event["kind"] == "broadcast_request")
        assert broadcast["addresses"] == [{"field": "teacher_ip", "value": "192.0.2.10"},
                                          {"field": "remote", "value": "229.1.36.200"}]
        assert not any(event["kind"] == "hook_error" for event in events), events
    finally:
        if observer:
            observer.close()
        helper.unload()
        control.detach()


def test_exact_keyboard_boundary_without_real_driver(target):
    """Redirect the fixture's device open to NUL; no real driver is opened."""
    import frida
    identity, _, directory = target
    control = frida.attach(identity.pid)
    helper = control.create_script(r"""
        const target = Process.enumerateModules().find(m => m.name.toLowerCase() === 'fixture.exe');
        const kernel = Process.enumerateModules().find(m => m.name.toLowerCase() === 'kernel32.dll');
        const address = kernel.getExportByName('CreateFileW');
        const original = new NativeFunction(address, 'pointer', ['pointer','uint','uint','pointer','uint','uint','pointer']);
        const nul = Memory.allocUtf16String('NUL');
        const redirect = new NativeCallback(function(p,a,b,c,d,e,f) {
            if (p.readUtf16String().toLowerCase().endsWith('oekbdfilter')) return original(nul,a,b,c,d,e,f);
            return original(p,a,b,c,d,e,f);
        }, 'pointer', ['pointer','uint','uint','pointer','uint','uint','pointer']);
        Interceptor.replace(address, redirect);
        const request = new NativeFunction(target.getExportByName('RunKeyboardRequest'), 'uint', ['uint']);
        rpc.exports = {request(keys) { return request(keys); }};
    """)
    helper.load()
    observer = None
    try:
        baseline = helper.exports_sync.request(3)
        assert baseline != 5
        events = []
        observer = Observer(identity, directory, "student", [], events.append)
        assert helper.exports_sync.request(3) == 5
        assert helper.exports_sync.request(0) == baseline
        assert helper.exports_sync.request(9) == baseline
        assert any(event["kind"] == "keyboard_denied" for event in events)
        assert not observer.close()
        observer = None
        assert helper.exports_sync.request(3) == baseline
    finally:
        if observer:
            observer.close()
        helper.unload()
        control.detach()


def test_dll_guards_apply_on_load_and_revert(target):
    import frida
    identity, _, directory = target
    source = Path(__file__).parent / "native" / "driver_stub.c"
    modules = [directory / "OeNetlimit.dll", directory / "easyusbctrl.dll"]
    for module in modules:
        subprocess.run([shutil.which("gcc"), "-shared", "-O0", str(source), "-o", str(module)], check=True, capture_output=True)
    control = frida.attach(identity.pid)
    helper = control.create_script("""
        const target = Process.enumerateModules().find(m => m.name.toLowerCase() === 'fixture.exe');
        const run = new NativeFunction(target.getExportByName('RunDll'), 'int', ['pointer','int']);
        rpc.exports = {run(path, usb) { return run(Memory.allocUtf16String(path), usb); }};
    """)
    helper.load()
    observer = None
    try:
        events = []
        observer = Observer(identity, directory, "student", [str(module) for module in modules], events.append)
        assert helper.exports_sync.run(str(modules[0]), 0) == 0
        assert helper.exports_sync.run(str(modules[1]), 1) == 500
        assert any(event["kind"] == "network_denied" for event in events)
        assert any(event["kind"] == "usb_denied" for event in events)
        assert not any(event["kind"] == "hook_error" for event in events), events
        assert not observer.close()
        observer = None
        assert helper.exports_sync.run(str(modules[0]), 0) == 101
        assert helper.exports_sync.run(str(modules[1]), 1) == 1
    finally:
        if observer:
            observer.close()
        helper.unload()
        control.detach()


def test_64bit_controller_attaches_to_32bit_process(target):
    identity, child, directory = target
    csc = Path(r"C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe")
    if not csc.exists():
        pytest.skip(".NET Framework x86 compiler is needed only for the cross-bitness test")
    actor = directory / "actor32.exe"
    source = Path(__file__).parent / "native" / "Actor32.cs"
    subprocess.run([str(csc), "/nologo", "/target:winexe", "/platform:x86", "/out:" + str(actor), str(source)], check=True, capture_output=True)
    trigger, output, ready = [directory / name for name in ("trigger", "output.txt", "ready.txt")]
    process = subprocess.Popen([str(actor), str(child), str(trigger), str(output), str(ready)])
    observer = None

    def wait_for(path):
        deadline = time.monotonic() + 10
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        assert path.exists()

    try:
        wait_for(ready)
        assert ready.read_text() == "32"
        current = psutil.Process(process.pid)
        identity = Identity(process.pid, current.create_time(), str(actor))
        events = []
        observer = Observer(identity, directory, "student", [str(child)], events.append)
        trigger.touch()
        wait_for(output)
        assert "#fullscreen#:0" in output.read_text()
        assert "#notfullscreen#:1" in output.read_text()
        assert not observer.close()
        observer = None
        output.unlink()
        trigger.touch()
        wait_for(output)
        assert "#fullscreen#:1" in output.read_text()
        assert not any(event["kind"] == "hook_error" for event in events), events
    finally:
        if observer:
            observer.close()
        process.terminate()
        process.wait(timeout=10)
