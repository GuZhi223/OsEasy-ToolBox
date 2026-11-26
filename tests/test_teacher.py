from pathlib import Path
from types import SimpleNamespace as NS
import socket

import psutil
import pytest

from oseasy_auto.model import Identity
from oseasy_auto.teacher import (Evidence, TeacherDiscovery, TeacherTarget, broadcast_fields,
                                 choose_teacher, control_port, unicast_ipv4)


@pytest.mark.parametrize("value", ["0.0.0.0", "229.1.36.200", "127.0.0.1", "255.255.255.255", "169.254.2.1", "0.1.2.3", "999.2.3.4", "1", 0])
def test_invalid_or_non_host_addresses_are_not_teacher(value):
    assert unicast_ipv4(value) is None


def test_broadcast_parser_handles_hash_and_json_without_guessing_fields():
    fields = broadcast_fields(['ScreenRender.exe', '{#teacher_ip#:#192.0.2.10#,#remote#:#229.1.1.1#,#local#:#192.0.2.5#}'])
    assert fields == [{"field": "teacher_ip", "value": "192.0.2.10"}, {"field": "remote", "value": "229.1.1.1"}]
    assert broadcast_fields('{"teacher_ip":"192.0.2.20","remote":"192.0.2.21"}') == [
        {"field": "teacher_ip", "value": "192.0.2.20"}, {"field": "remote", "value": "192.0.2.21"}]
    assert not broadcast_fields('{#teacher_ip#:0,#not_teacher_ip#:#192.0.2.30#}')


def test_conflicting_strong_sources_are_not_arbitrarily_selected():
    state = choose_teacher([Evidence("192.0.2.10", "控制连接", True), Evidence("192.0.2.20", "teacher_ip", True)])
    assert state["state"] == "ambiguous" and state["address"] == ""
    assert state["addresses"] == ["192.0.2.10", "192.0.2.20"]


def test_multicast_is_rejected_and_remote_unicast_is_only_candidate():
    assert choose_teacher([Evidence("229.1.36.200", "remote", False)])["state"] == "waiting"
    assert choose_teacher([Evidence("192.0.2.10", "remote", False)])["state"] == "candidate"
    state = choose_teacher([Evidence("192.0.2.10", "remote", False), Evidence("192.0.2.20", "控制连接", True)])
    assert state["state"] == "resolved" and state["address"] == "192.0.2.20"


def interfaces():
    return {"classroom": [NS(family=socket.AF_INET, address="192.0.2.5", broadcast="192.0.2.255")]}


def connection(remote, *, remote_port=9003, local_port=43000, status="ESTABLISHED"):
    return NS(laddr=("192.0.2.5", local_port), raddr=(remote, remote_port), status=status)


class FakeProcess:
    def __init__(self, identity, connections=(), args=()):
        self.identity, self.connections, self.args = identity, list(connections), list(args)
        self.created = identity.created

    def create_time(self):
        return self.created

    def is_running(self):
        return self.created == self.identity.created

    def exe(self):
        return self.identity.path

    def net_connections(self, kind):
        assert kind == "tcp4"
        return self.connections

    def cmdline(self):
        return self.args


def target(tmp_path, role="student"):
    return TeacherTarget(Identity(101, 12.0, str(tmp_path / ("MmcStudent.exe" if role == "student" else "ScreenRender.exe"))), tmp_path, role)


def test_current_control_connection_and_configured_port(tmp_path):
    current = target(tmp_path)
    (tmp_path / "skin").mkdir()
    (tmp_path / "skin/core.conf").write_text("/Password/NOT_READ_OR_EMITTED/\n/ConnectPort/9123/\n")
    process = FakeProcess(current.identity, [connection("192.0.2.10", remote_port=9123), connection("192.0.2.50", remote_port=443)])
    discovery = TeacherDiscovery(process_factory=lambda pid: process, interfaces=interfaces)
    state = discovery.refresh([current])
    assert state["address"] == "192.0.2.10" and state["state"] == "resolved"
    assert "9123" in state["source"] and "NOT_READ_OR_EMITTED" not in str(state)
    assert discovery.refresh([])["state"] == "waiting"


def test_incoming_control_connection_is_supported(tmp_path):
    current = target(tmp_path)
    process = FakeProcess(current.identity, [connection("192.0.2.10", remote_port=45000, local_port=9003)])
    state = TeacherDiscovery(process_factory=lambda pid: process, interfaces=interfaces).refresh([current])
    assert state["address"] == "192.0.2.10"


def test_local_ip_broadcast_and_unestablished_connections_are_excluded(tmp_path):
    current = target(tmp_path)
    process = FakeProcess(current.identity, [connection("192.0.2.5"), connection("192.0.2.255"),
                                            connection("192.0.2.10", status="SYN_SENT")])
    assert TeacherDiscovery(process_factory=lambda pid: process, interfaces=interfaces).refresh([current])["state"] == "waiting"


def test_running_broadcast_can_identify_teacher_before_new_events(tmp_path):
    current = target(tmp_path, "renderer")
    process = FakeProcess(current.identity, args=['ScreenRender.exe', '{#teacher_ip#:#192.0.2.10#,#remote#:#229.1.36.200#}'])
    state = TeacherDiscovery(process_factory=lambda pid: process, interfaces=interfaces).refresh([current])
    assert state["state"] == "resolved" and state["address"] == "192.0.2.10"


def test_native_broadcast_evidence_expires_and_is_bound_to_live_owner(tmp_path):
    current = target(tmp_path)
    process = FakeProcess(current.identity)
    clock = [100.0]
    discovery = TeacherDiscovery(process_factory=lambda pid: process, interfaces=interfaces, clock=lambda: clock[0])
    discovery.note_broadcast(current.identity.key, [{"field": "teacher_ip", "value": "192.0.2.10"}])
    assert discovery.refresh([current])["address"] == "192.0.2.10"
    clock[0] += 11
    assert discovery.refresh([current])["state"] == "waiting"
    discovery.note_broadcast(current.identity.key, [{"field": "teacher_ip", "value": "192.0.2.10"}])
    process.created += 1
    assert discovery.refresh([current])["state"] == "waiting"


def test_pid_reuse_during_connection_read_drops_result(tmp_path):
    current = target(tmp_path)
    process = FakeProcess(current.identity, [connection("192.0.2.10")])

    def read(kind):
        process.created += 1
        return process.connections

    process.net_connections = read
    assert TeacherDiscovery(process_factory=lambda pid: process, interfaces=interfaces).refresh([current])["state"] == "waiting"


def test_connection_permissions_do_not_break_auto_mode(tmp_path):
    current = target(tmp_path)
    process = FakeProcess(current.identity)

    def denied(kind):
        raise psutil.AccessDenied(current.identity.pid)

    process.net_connections = denied
    state = TeacherDiscovery(process_factory=lambda pid: process, interfaces=interfaces).refresh([current])
    assert state["state"] == "waiting" and "无法读取" in state["detail"]


def test_malformed_ports_are_not_guessed(tmp_path):
    assert control_port(tmp_path) == 9003
    (tmp_path / "skin").mkdir()
    config = tmp_path / "skin/core.conf"
    for text in ("/ConnectPort/0/", "/ConnectPort/70000/", "/ConnectPort/9003/\n/ConnectPort/9004/"):
        config.write_text(text)
        assert control_port(tmp_path) is None
