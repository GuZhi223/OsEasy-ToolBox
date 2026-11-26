"""Infer teacher IPv4 addresses from the pinned local processes, without scans."""
from __future__ import annotations

import ipaddress
import re
import socket
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

from .model import Identity


PARAMETER = re.compile(
    r'(?<![\w])(?P<key_quote>["#]?)(?P<key>teacher_ip|remote)(?P=key_quote)\s*:\s*'
    r'(?P<value_quote>["#]?)(?P<value>\d{1,3}(?:\.\d{1,3}){3})(?P=value_quote)(?=[\s,}]|$)',
    re.IGNORECASE,
)


def unicast_ipv4(value, excluded=()) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        address = ipaddress.ip_address(value.strip())
        if isinstance(address, ipaddress.IPv6Address):
            address = address.ipv4_mapped
        if address is None or any((address.is_multicast, address.is_loopback, address.is_link_local,
                                   address.is_unspecified, address.is_reserved)) or address.packed[0] == 0:
            return None
        normalized = str(address)
        return None if normalized in excluded else normalized
    except ValueError:
        return None


def broadcast_fields(arguments: str | list[str]) -> list[dict[str, str]]:
    text = arguments if isinstance(arguments, str) else " ".join(arguments)
    return [{"field": match["key"].lower(), "value": match["value"]}
            for match in PARAMETER.finditer(text)][:8]


@dataclass(frozen=True)
class TeacherTarget:
    identity: Identity
    root: Path
    role: str


@dataclass(frozen=True)
class Evidence:
    address: str
    source: str
    strong: bool


def choose_teacher(evidence: list[Evidence], excluded=(), *, unavailable=False) -> dict:
    valid = [Evidence(address, item.source, item.strong) for item in evidence
             if (address := unicast_ipv4(item.address, excluded))]
    strong = [item for item in valid if item.strong]
    selected = strong or valid
    addresses = sorted({item.address for item in selected}, key=ipaddress.IPv4Address)
    sources = sorted({item.source for item in selected})
    if not addresses:
        detail = "暂时无法读取部分连接信息，等待可用的广播参数。" if unavailable else "等待教师控制连接或包含教师地址的广播。"
        return {"state": "waiting", "address": "", "addresses": [], "source": "", "detail": detail}
    if len(addresses) > 1:
        return {"state": "ambiguous", "address": "", "addresses": addresses,
                "source": " / ".join(sources), "detail": "存在多个地址，尚未确定唯一教师机；不会自动任选一个。"}
    return {"state": "resolved" if strong else "candidate", "address": addresses[0], "addresses": addresses,
            "source": " / ".join(sources),
            "detail": "来自当前连接或明确的教师地址字段。" if strong else "仅识别到单播广播来源，教师身份仍需核对。"}


def control_port(root: Path) -> int | None:
    try:
        path = root / "skin" / "core.conf"
        if path.stat().st_size > 64 * 1024:
            return None
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except FileNotFoundError:
        return 9003  # Pinned sample's bundled default, not a LAN scan range.
    except OSError:
        return None
    values = re.findall(r"^/ConnectPort/(\d+)/\s*$", text, flags=re.MULTILINE)
    if not values:
        return None
    try:
        ports = {int(value) for value in values}
    except ValueError:
        return None
    return ports.pop() if len(ports) == 1 and all(0 < value <= 65535 for value in ports) else None


class TeacherDiscovery:
    def __init__(self, *, process_factory=psutil.Process, interfaces=psutil.net_if_addrs, clock=time.monotonic):
        self.process_factory, self.interfaces, self.clock = process_factory, interfaces, clock
        self.native: dict[str, tuple[float, list[dict]]] = {}
        self.state = choose_teacher([])

    def note_broadcast(self, owner: str, fields):
        if isinstance(fields, list):
            clean = [item for item in fields[:8] if isinstance(item, dict)
                     and item.get("field") in ("teacher_ip", "remote") and isinstance(item.get("value"), str)]
            self.native[owner] = (self.clock(), clean)

    def _same_process(self, process, identity):
        return (process.is_running() and process.create_time() == identity.created
                and Path(process.exe()).resolve() == Path(identity.path).resolve())

    @staticmethod
    def _field_evidence(fields, source):
        return [Evidence(item["value"], source + (" · teacher_ip" if item["field"] == "teacher_ip" else " · remote"),
                         item["field"] == "teacher_ip") for item in fields]

    def refresh(self, targets: list[TeacherTarget]) -> dict:
        excluded = set()
        evidence = []
        unavailable = False
        try:
            for addresses in self.interfaces().values():
                for address in addresses:
                    if address.family == socket.AF_INET:
                        excluded.add(address.address)
                        if address.broadcast:
                            excluded.add(address.broadcast)
        except (psutil.Error, OSError):
            unavailable = True
        active = set()
        for target in targets:
            identity = target.identity
            try:
                process = self.process_factory(identity.pid)
                if not self._same_process(process, identity):
                    continue
                observations = []
                if target.role == "student":
                    port = control_port(target.root)
                    try:
                        connections = process.net_connections(kind="tcp4") if port else []
                        for connection in connections:
                            if connection.laddr:
                                excluded.add(connection.laddr[0])
                            if connection.status != psutil.CONN_ESTABLISHED or not connection.raddr:
                                continue
                            if connection.raddr[1] == port or (connection.laddr and connection.laddr[1] == port):
                                observations.append(Evidence(connection.raddr[0], f"学生端 TCP 控制连接（{port}）", True))
                    except (psutil.Error, OSError):
                        unavailable = True
                elif target.role == "renderer":
                    try:
                        fields = broadcast_fields(process.cmdline())
                        observations.extend(self._field_evidence(fields, "当前广播参数"))
                    except (psutil.Error, OSError):
                        unavailable = True
                # A PID can disappear or be reused while net_connections is read.
                if not self._same_process(process, identity):
                    continue
                active.add(identity.key)
                evidence.extend(observations)
            except (psutil.Error, OSError):
                unavailable = True
        now = self.clock()
        self.native = {owner: record for owner, record in self.native.items()
                       if owner in active and now - record[0] < 10}
        for _, fields in self.native.values():
            evidence.extend(self._field_evidence(fields, "刚收到的广播启动参数"))
        self.state = choose_teacher(evidence, excluded, unavailable=unavailable)
        return self.state
