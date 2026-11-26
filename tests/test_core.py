import hashlib
import queue
from pathlib import Path

from oseasy_auto.engine import Engine
from oseasy_auto.model import Identity, Journal, SampleGate


def fixture_gate(tmp_path):
    files = {}
    for name, content in [("MmcStudent.exe", b"student fixture"), ("ScreenRender.exe", b"renderer fixture")]:
        (tmp_path / name).write_bytes(content)
        files[name] = {"sha256": hashlib.sha256(content).hexdigest()}
    return SampleGate({"files": files})


def test_gate_rejects_changed_file_and_accepts_exact_alias(tmp_path):
    gate = fixture_gate(tmp_path)
    identity = Identity(4, 1, str(tmp_path / "ScreenRender.exe"))
    assert gate.identify(identity)[1] == "renderer"
    alias = tmp_path / "ScreenRender_Y.exe"
    alias.write_bytes((tmp_path / "ScreenRender.exe").read_bytes())
    assert gate.identify(Identity(8, 2, str(alias)))[1] == "renderer"
    (tmp_path / "MmcStudent.exe").write_bytes(b"changed student")
    assert gate.identify(identity) is None


def test_journal_preserves_original_snapshot(tmp_path):
    journal = Journal(tmp_path / "restore.json")
    journal.remember("window", {"style": 1})
    journal.remember("window", {"style": 2})
    assert Journal(journal.path).entries == {"window": {"style": 1}}


class FakeWindows:
    def __init__(self):
        self.windowed_state = False
        self.changes = 0
        self.restored = []
        self.fail_restore = False
        self.visible = True

    def enumerate(self, identity):
        return [123] if self.visible else []

    def is_windowed(self, hwnd):
        return self.windowed_state

    def snapshot(self, hwnd, identity):
        return {"hwnd": hwnd, "pid": identity.pid, "created": identity.created, "path": identity.path, "style": 1}

    def windowed(self, hwnd):
        self.windowed_state = True
        self.changes += 1

    def mark(self, snapshot):
        pass

    def fullscreen(self, hwnd):
        self.windowed_state = False

    def exists(self, snapshot):
        return True

    def restore(self, snapshot):
        if self.fail_restore:
            raise OSError("fixture restoration failure")
        self.restored.append(snapshot)


class FakeObserver:
    closed = 0

    def __init__(self, *args):
        self.capabilities = {"broadcast": True}

    def close(self):
        self.closed += 1
        return []


def make_engine(tmp_path, *, alive=lambda identity: True, observer=FakeObserver):
    root = tmp_path / "installation"
    root.mkdir()
    gate = fixture_gate(root)
    identity = Identity(12, 100.0, str(root / "ScreenRender.exe"))
    windows = FakeWindows()
    engine = Engine(queue.Queue(), directory=tmp_path / "state", windows=windows, gate=gate,
                    observer_factory=observer, scan=lambda: [identity], alive=alive)
    return engine, windows, identity


def test_repeated_scan_attaches_once_and_windows_once(tmp_path):
    engine, windows, _ = make_engine(tmp_path)
    engine._scan_once()
    engine._scan_once()
    assert len(engine.attachments) == 1
    engine._windows_once()
    engine._windows_once()
    assert windows.changes == 1
    assert len(engine.journal.entries) == 1
    engine.restore_windows()
    assert len(windows.restored) == 1 and not engine.journal.entries


def test_pid_reuse_does_not_restore_into_new_process(tmp_path):
    live = [True]
    engine, windows, _ = make_engine(tmp_path, alive=lambda identity: live[0])
    engine._scan_once()
    engine._windows_once()
    live[0] = False
    engine.restore_windows()
    assert not windows.restored and not engine.journal.entries


def test_failed_restore_keeps_recovery_record(tmp_path):
    engine, windows, _ = make_engine(tmp_path)
    engine._scan_once()
    engine._windows_once()
    windows.fail_restore = True
    engine.restore_windows()
    assert engine.journal.entries
    assert Journal(engine.journal.path).entries == engine.journal.entries


def test_unknown_binary_never_reaches_observer(tmp_path):
    calls = []
    engine, _, identity = make_engine(tmp_path, observer=lambda *args: calls.append(args))
    Path(identity.path).write_bytes(b"different renderer")
    engine._scan_once()
    assert not calls and not engine.attachments


def test_fullscreen_toggle_retains_first_snapshot(tmp_path):
    engine, windows, _ = make_engine(tmp_path)
    engine._scan_once()
    engine._windows_once()
    original = next(iter(engine.journal.entries.values())).copy()
    engine._toggle()
    engine._windows_once()
    assert not windows.windowed_state
    engine._toggle()
    assert windows.windowed_state
    assert next(iter(engine.journal.entries.values())) == original


def test_start_is_idempotent_and_other_controller_cannot_restore(tmp_path):
    import pytest
    engine, windows, _ = make_engine(tmp_path)
    engine.scan = lambda: []
    other = Engine(queue.Queue(), directory=tmp_path / "other-state", windows=FakeWindows(), scan=lambda: [])
    try:
        engine.start()
        first_thread = engine.thread
        engine.start()
        assert engine.thread is first_thread
        with pytest.raises(OSError, match="已有自动模式"):
            other.start()
        assert not other.windows.restored
    finally:
        engine.stop()
        if engine.thread:
            engine.thread.join(timeout=5)
    assert not engine.thread.is_alive() and not engine.journal.entries


def test_old_detach_event_cannot_remove_new_connection(tmp_path):
    engine, _, identity = make_engine(tmp_path)
    engine._scan_once()
    current = engine.attachments[identity.key]
    engine._on_message(identity.key, "old-observer-token", {"kind": "detached"})
    engine._drain_messages()
    assert engine.attachments[identity.key] is current


def test_next_broadcast_returns_to_default_window_mode(tmp_path):
    engine, windows, _ = make_engine(tmp_path)
    engine._scan_once()
    engine._windows_once()
    engine._toggle()
    assert engine.fullscreen_windows
    windows.visible = False
    engine._windows_once()
    assert not engine.fullscreen_windows
    windows.visible = True
    engine._windows_once()
    assert windows.windowed_state


def test_teacher_ip_flows_from_valid_broadcast_event_into_status(tmp_path):
    import json
    from types import SimpleNamespace
    from oseasy_auto.teacher import TeacherDiscovery
    engine, _, identity = make_engine(tmp_path)
    process = SimpleNamespace(is_running=lambda: True, create_time=lambda: identity.created, exe=lambda: identity.path, cmdline=lambda: [])
    engine.teacher = TeacherDiscovery(process_factory=lambda pid: process, interfaces=lambda: {})
    engine._scan_once()
    token = engine.attachments[identity.key].token
    data = {"kind": "broadcast_request", "launched": True,
            "addresses": [{"field": "teacher_ip", "value": "192.0.2.10"}]}
    engine._on_message(identity.key, "old-connection", data)
    engine._drain_messages()
    engine._teacher_once()
    assert engine.teacher.state["state"] == "waiting"
    engine._on_message(identity.key, token, data)
    engine._drain_messages()
    engine._teacher_once()
    engine._status()
    events = []
    while not engine.output.empty():
        events.append(engine.output.get_nowait())
    state = json.loads(next(event["detail"] for event in events if event["kind"] == "status"))
    assert state["teacher"]["address"] == "192.0.2.10"
    assert state["teacher"]["state"] == "resolved"


def test_failed_broadcast_does_not_supply_teacher_address(tmp_path):
    from types import SimpleNamespace
    from oseasy_auto.teacher import TeacherDiscovery
    engine, _, identity = make_engine(tmp_path)
    process = SimpleNamespace(is_running=lambda: True, create_time=lambda: identity.created, exe=lambda: identity.path, cmdline=lambda: [])
    engine.teacher = TeacherDiscovery(process_factory=lambda pid: process, interfaces=lambda: {})
    engine._scan_once()
    engine._on_message(identity.key, engine.attachments[identity.key].token,
                       {"kind": "broadcast_request", "launched": False,
                        "addresses": [{"field": "teacher_ip", "value": "192.0.2.10"}]})
    engine._drain_messages()
    engine._teacher_once()
    assert engine.teacher.state["state"] == "waiting"
