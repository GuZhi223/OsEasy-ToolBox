"""Check the original Flet field/button binding without running its actions."""
from types import SimpleNamespace as NS

import pytest

from oseasy_auto.toolbox_integration import AutoFillPolicy


def test_autofill_keeps_manual_input_and_does_not_guess_candidates():
    policy = AutoFillPolicy()
    first = policy.apply("", {"state": "resolved", "address": "192.0.2.10", "source": "控制连接"})
    assert first.value == "192.0.2.10"
    candidate = policy.apply(first.value, {"state": "candidate", "address": "192.0.2.11"})
    assert candidate.value == "" and "待确认" in candidate.caption
    assert policy.apply("198.51.100.20", {"state": "resolved", "address": "192.0.2.30"}).value == "198.51.100.20"
    assert not policy.enabled
    policy.enable("198.51.100.20")
    assert policy.apply("198.51.100.20", {"state": "resolved", "address": "192.0.2.30"}).value == "192.0.2.30"


def test_autofill_rejects_multicast_even_in_resolved_payload():
    assert AutoFillPolicy().apply("", {"state": "resolved", "address": "229.1.36.200"}).value == ""


class Page:
    def __init__(self):
        self.overlay = []
        self.controls = []
        self.updates = 0

    def update(self):
        self.updates += 1

    def clean(self):
        self.controls.clear()

    def add(self, *controls):
        self.controls.extend(controls)


@pytest.fixture
def original_ui(tmp_path, monkeypatch):
    from src import remain
    from src import REmainv3 as original
    monkeypatch.setattr(remain.ToolBoxCfg, "config_file_path", str(tmp_path / "config.json"))
    monkeypatch.setattr(remain.ToolBoxCfg, "oseasypath_have_been_modified", False)
    monkeypatch.setattr(remain.ToolBoxCfg, "oseasypath", str(tmp_path) + "\\")
    monkeypatch.setattr(remain.ToolBoxCfg, "running_student_client_ver", 0)
    ui = original.Ui()
    ui.preview = True
    ui.main(Page())
    return ui, original


def test_original_ip_field_is_filled_and_original_generate_button_uses_it(original_ui, monkeypatch):
    ui, original = original_ui
    generated = []
    monkeypatch.setattr(original, "generate_yc_cmd_and_save", generated.append)
    ui.teacher_ip_received({"state": "resolved", "address": "192.0.2.44", "source": "控制连接"})
    assert ui.teachIp_input.value == "192.0.2.44"
    assert ui.teachIp_input in ui.gbCommandStuff.controls
    assert ui.auto_gennerate_cmd in ui.gbCommandStuff.controls
    assert len(ui.MyRail.destinations) == 7
    assert ui.gbCommandStuff.height == ui.page.window_height - 85
    ui.auto_gennerate_cmd.on_click(None)
    assert generated == ["192.0.2.44"]


def test_manual_override_and_resume_on_original_field(original_ui):
    ui, _ = original_ui
    ui.teachIp_input.value = "198.51.100.20"
    ui.teacher_ip_manual_change()
    ui.teacher_ip_received({"state": "resolved", "address": "192.0.2.45"})
    assert ui.teachIp_input.value == "198.51.100.20" and not ui.teacher_auto_switch.value
    ui.teacher_auto_switch.value = True
    ui.teacher_ip_auto_changed()
    ui.teacher_ip_received({"state": "resolved", "address": "192.0.2.45"})
    assert ui.teachIp_input.value == "192.0.2.45" and ui.teacher_auto_switch.value


def test_automatic_mode_is_connected_to_original_process_page(original_ui):
    ui, _ = original_ui
    calls = []
    ui.automation = NS(toggle_mode=lambda: calls.append("toggle") or True)
    ui.toggle_automatic_mode()
    assert calls == ["toggle"]
    assert ui.auto_mode_button in ui.funcTab_Stuff.controls
    assert ui.auto_mode_button.text == "停止自动模式并恢复"
    ui.automatic_event_received({"kind": "stopped", "title": "已停止并恢复本次会话", "detail": "clean"})
    assert ui.auto_mode_button.text == "一键开启自动模式"


def test_teacher_discovery_starts_without_enabling_auto_actions(tmp_path):
    import threading
    from oseasy_auto.model import Identity
    from oseasy_auto.toolbox_integration import ToolboxAutomation
    found = threading.Event()
    received = []
    state = {"state": "resolved", "address": "192.0.2.10", "source": "控制连接"}
    discovery = NS(state={}, refresh=lambda targets: state)
    identity = Identity(100, 12, str(tmp_path / "MmcStudent.exe"))
    gate = NS(identify=lambda current: (tmp_path, "student"))

    def teacher(current):
        received.append(current)
        found.set()

    def no_engine(*args, **kwargs):
        raise AssertionError("Passive IP discovery must not start native automation")

    bridge = ToolboxAutomation(teacher, lambda event: None, scan=lambda: [identity],
                               gate=gate, discovery=discovery, engine_factory=no_engine)
    try:
        bridge.start()
        assert found.wait(2)
        assert received[0]["address"] == "192.0.2.10" and bridge.engine is None
    finally:
        assert bridge.close()
