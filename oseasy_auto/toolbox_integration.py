"""Automation and passive IP discovery connected to the original Flet toolbox."""
from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import dataclass

from .engine import Engine, discover
from .model import Event, SampleGate
from .teacher import TeacherDiscovery, TeacherTarget, unicast_ipv4


@dataclass
class FieldUpdate:
    value: str
    enabled: bool
    caption: str


class AutoFillPolicy:
    def __init__(self):
        self.enabled = True
        self.owned_value = ""

    def manual_input(self):
        self.enabled = False

    def enable(self, current=""):
        self.enabled = True
        self.owned_value = current or ""

    def apply(self, current: str | None, state: dict) -> FieldUpdate:
        current = current or ""
        if self.enabled and current not in ("", self.owned_value):
            self.enabled = False
        if not self.enabled:
            return FieldUpdate(current, False, "保留手动输入；开启自动填写后可重新识别")
        address = unicast_ipv4(state.get("address")) if state.get("state") == "resolved" else None
        if address:
            self.owned_value = address
            return FieldUpdate(address, True, f"已自动填写 {address}\n来源：{state.get('source', '当前连接')}")
        self.owned_value = ""
        if state.get("state") == "ambiguous":
            caption = "教师地址存在冲突，未自动填写：" + " / ".join(state.get("addresses", [])[:3])
        elif state.get("state") == "candidate":
            caption = f"广播来源候选 {state.get('address', '')}，教师身份待确认，未自动填写"
        else:
            caption = state.get("detail") or "等待教师连接或广播，暂未获得可靠地址"
        return FieldUpdate("", True, caption)


class ToolboxAutomation:
    def __init__(self, on_teacher, on_event, *, directory=None, scan=discover,
                 gate=None, discovery=None, engine_factory=Engine):
        self.on_teacher, self.on_event = on_teacher, on_event
        self.directory = directory
        self.scan, self.gate = scan, gate or SampleGate()
        self.discovery = discovery or TeacherDiscovery()
        self.engine_factory = engine_factory
        self.queue = queue.Queue()
        self.engine = None
        self.current_teacher = self.discovery.state
        self.closed = threading.Event()
        self.refresh_requested = threading.Event()
        self.lock = threading.Lock()
        self.worker = None
        self.last_teacher = ""
        self.clean = True

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        self.closed.clear()
        self.worker = threading.Thread(target=self._loop, name="toolbox-automation", daemon=True)
        self.worker.start()

    def refresh(self):
        self.last_teacher = ""
        self.refresh_requested.set()
        if self.engine:
            self.engine.teacher_checked_at = -999.0

    def toggle_mode(self) -> bool:
        with self.lock:
            if self.engine and self.engine.thread and self.engine.thread.is_alive():
                self.engine.stop()
                return False
            self.engine = self.engine_factory(self.queue, directory=self.directory)
            self.clean = True
            self.engine.start()
            return True

    def _teacher(self, state):
        self.current_teacher = state
        signature = json.dumps(state, sort_keys=True)
        if signature != self.last_teacher:
            self.last_teacher = signature
            self.on_teacher(state)

    def _event(self, event):
        if event["kind"] == "status":
            state = json.loads(event["detail"])
            self._teacher(state.get("teacher", self.current_teacher))
        elif event["kind"] == "stopped":
            self.clean = event["detail"] == "clean"
            self.refresh_requested.set()
        self.on_event(event)

    def _loop(self):
        last_check = -999.0
        while not self.closed.is_set():
            try:
                try:
                    self._event(self.queue.get(timeout=.2))
                except queue.Empty:
                    pass
                active = self.engine and self.engine.thread and self.engine.thread.is_alive()
                force = self.refresh_requested.is_set()
                self.refresh_requested.clear()
                if not active and (force or time.monotonic() - last_check >= 2):
                    last_check = time.monotonic()
                    targets = [TeacherTarget(identity, match[0], match[1]) for identity in self.scan()
                               if (match := self.gate.identify(identity))]
                    self._teacher(self.discovery.refresh(targets))
                elif active and force:
                    self._teacher(self.engine.teacher.state)
            except Exception as error:
                if not self.closed.is_set():
                    try:
                        self.on_event(Event("warning", "自动识别暂未完成", str(error)[:160], "warning").data())
                    except Exception:
                        self.closed.set()
                        if self.engine:
                            self.engine.stop()
                    self.closed.wait(2)

    def close(self, timeout=15) -> bool:
        self.closed.set()
        if self.engine:
            self.engine.stop()
            if self.engine.thread:
                self.engine.thread.join(timeout=timeout)
                if self.engine.thread.is_alive():
                    return False
        if self.worker and threading.current_thread() is not self.worker:
            self.worker.join(timeout=3)
        while not self.queue.empty():
            event = self.queue.get_nowait()
            if event["kind"] == "stopped":
                self.clean = event["detail"] == "clean"
        return self.clean
