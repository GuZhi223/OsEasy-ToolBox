from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import psutil

from .instrumentation import Observer
from .model import Event, Identity, Journal, SampleGate, state_directory
from .teacher import TeacherDiscovery, TeacherTarget, choose_teacher


NAMES = {"mmcstudent.exe", "screenrender.exe", "screenrender_y.exe", "devicecontrol_x64.exe", "devicecontrol_x86.exe"}


def live_identity(identity: Identity) -> bool:
    try:
        process = psutil.Process(identity.pid)
        return process.create_time() == identity.created and Path(process.exe()).resolve() == Path(identity.path).resolve()
    except (psutil.Error, OSError):
        return False


def discover() -> list[Identity]:
    found = []
    for process in psutil.process_iter(["pid", "name", "exe", "create_time"]):
        info = process.info
        if (info["name"] or "").lower() in NAMES and info["exe"] and info["create_time"]:
            found.append(Identity(info["pid"], info["create_time"], info["exe"]))
    return found


@dataclass
class Attachment:
    identity: Identity
    root: Path
    role: str
    observer: Observer
    token: str


class Engine:
    """One serialized owner for attachments, window mutations and restoration."""

    def __init__(self, output: queue.Queue, *, directory: Path | None = None, windows=None,
                 gate=None, observer_factory=Observer, scan=discover, alive=live_identity):
        self.output = output
        self.directory = directory or state_directory()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.journal = Journal(self.directory / "recovery.json")
        self.windows = windows
        self.gate = gate or SampleGate()
        self.make_observer, self.scan, self.alive = observer_factory, scan, alive
        self.commands = queue.Queue()
        self.messages = queue.Queue()
        self.attachments: dict[str, Attachment] = {}
        self.retry: dict[str, tuple[int, float]] = {}
        self.reported: set[str] = set()
        self.fullscreen_windows: set[int] = set()
        self.stopping = threading.Event()
        self.thread: threading.Thread | None = None
        self.running = False
        self.owner = None
        self.last_status = None
        self.last_events: dict[tuple, float] = {}
        self.teacher = TeacherDiscovery()
        self.teacher_targets: list[TeacherTarget] = []
        self.teacher_checked_at = -999.0
        self.teacher_signature = ""

    def emit(self, kind, title, detail="", level="info", pid=0, *, dedup=0):
        key = (kind, title, detail, pid)
        now = time.monotonic()
        if dedup and now - self.last_events.get(key, -999) < dedup:
            return
        self.last_events[key] = now
        if len(self.last_events) > 2000:
            self.last_events = {key: at for key, at in self.last_events.items() if now - at < 60}
        event = Event(kind, title, detail, level, pid)
        self.output.put(event.data())
        if kind == "status":
            return
        try:
            path = self.directory / "events.jsonl"
            if path.exists() and path.stat().st_size > 2 * 1024 * 1024:
                path.replace(self.directory / "events.previous.jsonl")
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event.data(), ensure_ascii=False) + "\n")
        except OSError:
            self.output.put(Event("warning", "记录文件暂时无法写入", "事件仍显示在界面中", "warning").data())

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        from .windows import ControllerOwner
        self.owner = ControllerOwner()
        # Load a fresh journal only after obtaining ownership of this session.
        try:
            self.journal = Journal(self.directory / "recovery.json")
        except Exception:
            self.owner.close()
            self.owner = None
            raise
        self.stopping.clear()
        self.last_status = None
        self.thread = threading.Thread(target=self._run, name="oseasy-controller", daemon=True)
        try:
            self.thread.start()
        except Exception:
            self.owner.close()
            self.owner = None
            raise

    def stop(self):
        self.stopping.set()

    def toggle_fullscreen(self):
        self.commands.put("toggle_fullscreen")

    def _status(self):
        student = sum(a.role == "student" for a in self.attachments.values())
        renderers = sum(a.role == "renderer" for a in self.attachments.values())
        capabilities = {name: any(a.observer.capabilities.get(name) for a in self.attachments.values())
                        for name in ("broadcast", "keyboard", "network", "usb")}
        document = {"running": True, "students": student, "renderers": renderers,
                    "windows": len(self.journal.entries), "capabilities": capabilities,
                    "waiting": not self.attachments, "partial": True, "teacher": self.teacher.state}
        signature = json.dumps(document, sort_keys=True)
        if signature != self.last_status:
            self.last_status = signature
            self.emit("status", "正在等待学生端" if not self.attachments else "自动模式 · 部分能力就绪", signature)

    def _on_message(self, key: str, token: str, data: dict):
        self.messages.put((key, token, data))

    def _drain_messages(self):
        while True:
            try:
                key, token, data = self.messages.get_nowait()
            except queue.Empty:
                break
            attachment = self.attachments.get(key)
            if not attachment or attachment.token != token:
                continue
            kind, pid = data.get("kind"), attachment.identity.pid
            if kind in ("ready", "capabilities"):
                attachment.observer.capabilities.update(data.get("capabilities", {}))
            elif kind == "detached":
                self.emit("disconnected", "进程监听已断开", "等待重新识别并连接", "warning", pid)
                self.attachments.pop(key)
                self.retry[key] = (0, time.monotonic() + 2)
                attachment.observer.close()
            elif kind == "broadcast_request":
                success = data.get("launched")
                if success:
                    self.teacher.note_broadcast(key, data.get("addresses"))
                    self.teacher_checked_at = -999.0
                title = "广播启动请求已调整" if data.get("changed") else "观察到广播启动请求"
                self.emit("broadcast_request", title,
                          "正在等待窗口核验" if success else "原学生端报告启动失败",
                          "info" if success else "warning", pid, dedup=2)
            elif kind in ("keyboard_denied", "network_denied", "usb_denied"):
                names = {"keyboard_denied": "拒绝了新的快捷键限制请求", "network_denied": "拒绝了新的网络限制请求",
                         "usb_denied": "拒绝了新的 USB 限制请求"}
                self.emit(kind, names[kind], "本次调用已返回失败；已有驱动状态未重置", "success", pid, dedup=2)
            elif kind == "program_request":
                self.emit(kind, "观察到学生端程序启动请求", data.get("image", "")[:100], pid=pid, dedup=3)
            elif kind == "hook_error":
                self.emit("error", "部分监听接口未就绪", str(data.get("error", "未知错误"))[:180], "warning", pid, dedup=10)

    def _scan_once(self):
        identities = self.scan()
        self.teacher_targets = []
        keys = {identity.key for identity in identities}
        for key, attached in list(self.attachments.items()):
            if key not in keys or not self.alive(attached.identity):
                attached.observer.close()
                self.attachments.pop(key)
                self.emit("process_exit", "学生端组件已退出", attached.role, pid=attached.identity.pid)
        for identity in identities:
            checked = self.gate.identify(identity)
            if checked is None:
                if identity.key not in self.reported:
                    self.reported.add(identity.key)
                    self.emit("unmatched", "发现了其他版本或已替换的组件", Path(identity.path).name + "：未通过样本校验", "warning", identity.pid)
                continue
            self.teacher_targets.append(TeacherTarget(identity, checked[0], checked[1]))
            if identity.key in self.attachments:
                continue
            attempts, after = self.retry.get(identity.key, (0, 0))
            if attempts >= 3 or time.monotonic() < after:
                continue
            root, role = checked
            try:
                if not self.alive(identity):
                    continue
                token = uuid.uuid4().hex
                observer = self.make_observer(identity, root, role, self.gate.trusted_modules(root),
                                              lambda data, key=identity.key, token=token: self._on_message(key, token, data))
                if not self.alive(identity):
                    observer.close()
                    continue
                self.attachments[identity.key] = Attachment(identity, root, role, observer, token)
                self.retry.pop(identity.key, None)
                self.emit("attached", "已连接学生端组件", f"{Path(identity.path).name} · 本次会话监听", "success", identity.pid)
            except Exception as error:
                self.retry[identity.key] = (attempts + 1, time.monotonic() + (2, 8, 30)[attempts])
                self.emit("error", "组件监听暂未连接", str(error)[:180], "warning", identity.pid)
        # Bound bookkeeping when processes restart repeatedly.
        self.retry = {key: value for key, value in self.retry.items() if key in keys}
        self.reported.intersection_update(keys)

    def _teacher_once(self):
        now = time.monotonic()
        if now - self.teacher_checked_at < 2:
            return
        self.teacher_checked_at = now
        try:
            state = self.teacher.refresh(self.teacher_targets)
            signature = json.dumps(state, sort_keys=True)
            if signature != self.teacher_signature:
                self.teacher_signature = signature
                titles = {"resolved": "已自动识别教师机 IP", "candidate": "识别到教师机候选 IP",
                          "ambiguous": "教师机 IP 存在多个候选", "waiting": "正在等待教师机地址"}
                detail = " / ".join(state["addresses"]) + (" · " + state["source"] if state["source"] else "")
                self.emit("teacher_ip", titles[state["state"]], detail or state["detail"],
                          "warning" if state["state"] == "ambiguous" else "info")
        except Exception as error:
            self.teacher.state = choose_teacher([], unavailable=True)
            self.emit("warning", "教师机地址暂未识别", str(error)[:150], "warning", dedup=30)

    def _window_identity(self, snapshot: dict) -> Identity:
        return Identity(snapshot["pid"], snapshot["created"], snapshot["path"])

    def restore_windows(self):
        for key, snapshot in list(self.journal.entries.items()):
            try:
                identity = self._window_identity(snapshot)
                if not self.alive(identity) or not self.windows.exists(snapshot):
                    self.journal.forget(key)
                    continue
                checked = self.gate.identify(identity)
                if not checked or checked[1] != "renderer":
                    raise OSError("窗口所属程序未通过样本校验，保留恢复记录")
                self.windows.restore(snapshot)
                self.journal.forget(key)
                self.emit("restored", "广播窗口原状态已恢复", pid=identity.pid)
            except Exception as error:
                self.emit("error", "有窗口尚未恢复", str(error)[:180], "warning", snapshot.get("pid", 0))

    def _windows_once(self):
        # HWNDs can be reused inside the same process. Retire stale lifetimes first.
        for key, snapshot in list(self.journal.entries.items()):
            if not self.alive(self._window_identity(snapshot)) or not self.windows.exists(snapshot):
                self.fullscreen_windows.discard(snapshot["hwnd"])
                self.journal.forget(key)
        for attached in self.attachments.values():
            if attached.role != "renderer" or not self.alive(attached.identity):
                continue
            visible = self.windows.enumerate(attached.identity)
            for snapshot in self.journal.entries.values():
                if snapshot["pid"] == attached.identity.pid and snapshot["hwnd"] not in visible:
                    self.fullscreen_windows.discard(snapshot["hwnd"])
            for hwnd in visible:
                if hwnd in self.fullscreen_windows:
                    continue
                key = f"{attached.identity.key}:{hwnd}"
                if self.windows.is_windowed(hwnd):
                    continue
                try:
                    snapshot = self.windows.snapshot(hwnd, attached.identity)
                    self.journal.remember(key, snapshot)
                    self.windows.mark(self.journal.entries[key])
                    # Recheck the process immediately before a cross-process window action.
                    if not self.alive(attached.identity):
                        continue
                    self.windows.windowed(hwnd)
                    self.emit("windowed", "广播已切换为窗口", "已核验窗口边框和置顶状态", "success", attached.identity.pid, dedup=3)
                except Exception as error:
                    self.emit("error", "广播窗口处理未完成", str(error)[:180], "warning", attached.identity.pid, dedup=10)

    def _toggle(self):
        for attached in self.attachments.values():
            if attached.role != "renderer" or not self.alive(attached.identity):
                continue
            for hwnd in self.windows.enumerate(attached.identity):
                key = f"{attached.identity.key}:{hwnd}"
                self.journal.remember(key, self.windows.snapshot(hwnd, attached.identity))
                self.windows.mark(self.journal.entries[key])
                if hwnd in self.fullscreen_windows:
                    self.fullscreen_windows.remove(hwnd)
                    self.windows.windowed(hwnd)
                    self.emit("windowed", "已返回窗口广播", pid=attached.identity.pid)
                else:
                    self.windows.fullscreen(hwnd)
                    self.fullscreen_windows.add(hwnd)
                    self.emit("fullscreen", "广播临时全屏", "再次点击可返回窗口", pid=attached.identity.pid)
                return
        self.emit("notice", "当前没有可切换的广播窗口")

    def _run(self):
        self.running = True
        clean = False
        try:
            if self.windows is None:
                from .windows import WindowBackend
                self.windows = WindowBackend()
            if self.journal.entries:
                self.emit("recovery", "正在恢复上次会话的窗口")
                self.restore_windows()
                if self.journal.entries:
                    raise OSError("仍有窗口未恢复，请查看事件记录")
            self.emit("starting", "自动模式已启动", "持续观察本机事件；黑屏和已有驱动限制尚未适配")
            while not self.stopping.is_set():
                self._scan_once()
                self._drain_messages()
                self._teacher_once()
                self._windows_once()
                try:
                    if self.commands.get_nowait() == "toggle_fullscreen":
                        self._toggle()
                except queue.Empty:
                    pass
                self._status()
                self.stopping.wait(0.65)
        except Exception as error:
            self.emit("error", "自动模式已暂停", str(error)[:200], "error")
        finally:
            self.emit("restoring", "正在停止监听并恢复窗口")
            hook_errors = []
            for attached in self.attachments.values():
                hook_errors.extend(attached.observer.close())
            self.attachments.clear()
            if self.windows is not None:
                self.restore_windows()
            self.fullscreen_windows.clear()
            clean = not self.journal.entries and not hook_errors
            if hook_errors:
                self.emit("error", "部分监听清理未确认", "; ".join(hook_errors)[:200], "warning")
            self.running = False
            self.output.put(Event("stopped", "已停止并恢复本次会话" if clean else "已停止 · 部分恢复未确认",
                                  "clean" if clean else "partial", "info" if clean else "warning").data())
            if self.owner:
                self.owner.close()
                self.owner = None
