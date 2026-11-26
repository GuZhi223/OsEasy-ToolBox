from __future__ import annotations

import ctypes
import json
import queue
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from . import __version__
from .engine import Engine
from .model import Event


BG = "#f4f4ef"
SURFACE = "#ffffff"
FG = "#202922"
MUTED = "#6d766f"
ACCENT = "#285c47"


def is_admin():
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


def elevate():
    args = [] if getattr(sys, "frozen", False) else [str(Path(sys.argv[0]).resolve())]
    args.append("--autostart")
    shell = ctypes.windll.shell32.ShellExecuteW
    shell.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_int]
    shell.restype = ctypes.c_ssize_t
    result = shell(None, "runas", sys.executable, subprocess.list2cmdline(args), str(Path.cwd()), 1)
    if result <= 32:
        raise OSError("管理员启动未完成；尚未连接学生端")


class Desktop:
    def __init__(self, root: tk.Tk, demo=False):
        self.root, self.demo = root, demo
        self.events = queue.Queue()
        self.engine = None
        self.active = False
        self.closing = False
        self.closed = False
        self.tray = None
        self.demo_jobs = []
        self.counter = 0
        root.title("OsEasy Auto · 自动模式预览")
        root.geometry(f"710x{min(800, max(650, root.winfo_screenheight() - 90))}")
        root.minsize(590, 620)
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", self.close)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=FG,
                        rowheight=37, borderwidth=0, font=("Microsoft YaHei UI", 10))
        style.configure("Treeview.Heading", background=BG, foreground=MUTED, borderwidth=0,
                        font=("Microsoft YaHei UI", 9))
        style.map("Treeview", background=[("selected", "#e1eae3")], foreground=[("selected", FG)])
        wrapper = tk.Frame(root, bg=BG, padx=30, pady=24)
        wrapper.pack(fill="both", expand=True)
        header = tk.Frame(wrapper, bg=BG)
        header.pack(fill="x")
        self.label(header, "OsEasy Auto", size=21, weight="bold").pack(side="left")
        self.label(header, "交互演示" if demo else f"预览版 {__version__}", color=MUTED, size=10).pack(side="right", pady=6)
        self.label(wrapper, "一次开启，持续处理本机事件", color=MUTED, size=11).pack(anchor="w", pady=(5, 22))
        card = tk.Frame(wrapper, bg=SURFACE, padx=24, pady=21, highlightbackground="#e2e5dd", highlightthickness=1)
        card.pack(fill="x")
        self.status = self.label(card, "尚未开启", size=19, bg=SURFACE, weight="bold")
        self.status.pack(anchor="w")
        self.detail = self.label(card, "识别你提供的样本；等待开启自动模式。", size=10, bg=SURFACE, color=MUTED)
        self.detail.configure(wraplength=540, justify="left")
        self.detail.pack(anchor="w", pady=(7, 16))
        self.main = tk.Button(card, text="开启自动模式", command=self.toggle, bg=ACCENT, fg="white",
                              activebackground="#1e4938", activeforeground="white", relief="flat", bd=0,
                              padx=20, pady=11, font=("Microsoft YaHei UI", 11, "bold"))
        self.main.pack(fill="x")
        teacher = tk.Frame(wrapper, bg=BG, pady=12)
        teacher.pack(fill="x")
        teacher_line = tk.Frame(teacher, bg=BG)
        teacher_line.pack(fill="x")
        self.label(teacher_line, "教师机 IP", size=10).pack(side="left")
        self.teacher_address = ""
        self.teacher_value = self.label(teacher_line, "等待识别", size=11, weight="bold")
        self.teacher_value.pack(side="left", padx=(14, 8))
        self.copy_teacher = tk.Button(teacher_line, text="复制 IP", command=self.copy_teacher_ip, relief="flat",
                                      bg=BG, fg=ACCENT, font=("Microsoft YaHei UI", 9), state="disabled")
        self.copy_teacher.pack(side="right")
        self.teacher_source = self.label(teacher, "开启后自动从当前控制连接和广播参数识别", size=9, color=MUTED)
        self.teacher_source.configure(anchor="w", justify="left", wraplength=620)
        self.teacher_source.pack(fill="x", pady=(3, 0))
        self.capabilities = {}
        capabilities = tk.Frame(wrapper, bg=BG, pady=3)
        capabilities.pack(fill="x")
        for title, key in [("广播窗口化", "broadcast"), ("新快捷键限制请求", "keyboard"),
                           ("新网络 / USB 请求", "drivers"), ("黑屏 / 已有驱动限制", "pending")]:
            line = tk.Frame(capabilities, bg=BG)
            line.pack(fill="x", pady=3)
            self.label(line, title, size=10).pack(side="left")
            value = self.label(line, "待适配" if key == "pending" else "等待连接", color=MUTED, size=10)
            value.pack(side="right")
            self.capabilities[key] = value
        actions = tk.Frame(wrapper, bg=BG)
        actions.pack(fill="x", pady=(3, 10))
        self.label(actions, "事件记录", size=11, weight="bold").pack(side="left")
        self.fullscreen = tk.Button(actions, text="临时全屏 / 返回窗口", command=self.toggle_fullscreen,
                                    relief="flat", bg=BG, fg=ACCENT, font=("Microsoft YaHei UI", 9), state="disabled")
        self.fullscreen.pack(side="right")
        self.tree = ttk.Treeview(wrapper, columns=("time", "event"), show="headings", height=7, selectmode="browse")
        self.tree.heading("time", text="时间", anchor="w")
        self.tree.heading("event", text="本机事件 / 处理结果", anchor="w")
        self.tree.column("time", width=78, minwidth=75, stretch=False)
        self.tree.column("event", width=490, minwidth=300)
        self.tree.bind("<<TreeviewSelect>>", self.show_event)
        self.details_by_id = {}
        self.selected_detail = self.label(wrapper, "开启后，成功处理和需要注意的事件都会显示在这里。", color=MUTED, size=9)
        self.selected_detail.configure(wraplength=620, justify="left")
        footer = tk.Frame(wrapper, bg=BG)
        footer.pack(side="bottom", fill="x", pady=(7, 0))
        self.label(footer, "本次会话生效 · 关闭时恢复本次修改", color=MUTED, size=9).pack(side="left")
        tk.Button(footer, text="收起到托盘", command=self.hide, bg=BG, fg=ACCENT, relief="flat",
                  font=("Microsoft YaHei UI", 9)).pack(side="right")
        self.selected_detail.pack(side="bottom", anchor="w", fill="x", pady=(11, 5))
        self.tree.pack(fill="both", expand=True)
        root.bind("<Configure>", self._resize)
        self.root.after(120, self.pump)

    def _resize(self, event):
        if event.widget is self.root:
            self.detail.configure(wraplength=max(300, event.width - 120))
            self.selected_detail.configure(wraplength=max(300, event.width - 70))
            self.teacher_source.configure(wraplength=max(300, event.width - 70))

    def label(self, parent, text, *, size=11, weight="normal", color=FG, bg=BG):
        return tk.Label(parent, text=text, font=("Microsoft YaHei UI", size, weight), fg=color, bg=bg)

    def add(self, event):
        if event["kind"] == "status":
            state = json.loads(event["detail"])
            self.status.configure(text=event["title"])
            self.detail.configure(text="等待已运行的学生端；不会自行启动样本程序。" if state["waiting"] else
                                  f"已连接 {state['students']} 个学生端、{state['renderers']} 个广播组件。已有锁定和黑屏仍需适配。")
            caps = state["capabilities"]
            for key in ("broadcast", "keyboard"):
                self.capabilities[key].configure(text="监听已接入" if caps.get(key) else "等待连接")
            self.capabilities["drivers"].configure(text=f"网络 {'已接入' if caps.get('network') else '待加载'} / USB {'已接入' if caps.get('usb') else '待加载'}")
            self.fullscreen.configure(state="normal" if state["renderers"] else "disabled")
            self.show_teacher(state.get("teacher", {}))
            return
        if event["kind"] == "stopped":
            self.active = False
            self.status.configure(text=event["title"])
            self.detail.configure(text="监听已停止。" if event["detail"] == "clean" else "请检查未恢复项目；原状态记录已保留。")
            self.main.configure(text="开启自动模式", state="normal")
            self.fullscreen.configure(state="disabled")
            self.teacher_address = ""
            self.teacher_value.configure(text="已停止识别")
            self.teacher_source.configure(text="下次开启后重新核对当前连接")
            self.copy_teacher.configure(state="disabled")
            for key in ("broadcast", "keyboard", "drivers"):
                self.capabilities[key].configure(text="已停止")
            if self.closing and event["detail"] == "clean":
                self.destroy()
                return
            self.closing = False
        elif event["kind"] == "restoring":
            self.status.configure(text="正在恢复本次会话")
        self.counter += 1
        iid = str(self.counter)
        self.tree.insert("", 0, iid=iid, values=(time.strftime("%H:%M:%S", time.localtime(event["at"])), event["title"]))
        self.details_by_id[iid] = event
        rows = self.tree.get_children()
        for row in rows[300:]:
            self.tree.delete(row)
            self.details_by_id.pop(row, None)
        self.selected_detail.configure(text=event["detail"] or event["title"])

    def show_event(self, *_):
        selected = self.tree.selection()
        if selected:
            event = self.details_by_id.get(selected[0], {})
            self.selected_detail.configure(text=event.get("detail") or event.get("title", ""))

    def show_teacher(self, teacher):
        state = teacher.get("state", "waiting")
        self.teacher_address = teacher.get("address", "") if state in ("resolved", "candidate") else ""
        value = self.teacher_address or ("多个地址待确认" if state == "ambiguous" else "等待识别")
        if state == "candidate":
            value += "（候选）"
        self.teacher_value.configure(text=value)
        source = teacher.get("source") or teacher.get("detail") or "等待教师控制连接或广播"
        if state == "ambiguous":
            addresses = teacher.get("addresses", [])
            source = " / ".join(addresses[:3]) + (f" 等 {len(addresses)} 个地址" if len(addresses) > 3 else "") + " · 地址存在冲突"
        self.teacher_source.configure(text=source)
        self.copy_teacher.configure(state="normal" if self.teacher_address else "disabled")

    def copy_teacher_ip(self):
        if self.teacher_address:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.teacher_address)
            self.add(Event("notice", "教师机 IP 已复制", self.teacher_address).data())

    def toggle(self):
        if self.active:
            self.main.configure(state="disabled", text="正在停止并恢复…")
            if self.demo:
                for job in self.demo_jobs:
                    self.root.after_cancel(job)
                self.demo_jobs.clear()
                self.add(Event("stopped", "演示已停止", "clean").data())
            else:
                self.engine.stop()
            return
        if self.demo:
            self.start_demo()
            return
        if not is_admin():
            try:
                elevate()
                self.destroy()
            except Exception as error:
                self.add(Event("error", "管理员启动未完成", str(error), "warning").data())
            return
        try:
            self.engine = Engine(self.events)
            self.active = True
            self.main.configure(text="停止并恢复")
            self.status.configure(text="正在准备自动模式")
            self.engine.start()
        except Exception as error:
            self.active = False
            self.add(Event("error", "无法准备自动模式", str(error), "error").data())

    def start_demo(self):
        self.active = True
        self.main.configure(text="停止演示")
        self.status.configure(text="交互演示 · 自动模式")
        self.detail.configure(text="仅演示交互；没有连接学生端，也没有改变系统状态。")
        self.fullscreen.configure(state="normal")
        self.show_teacher({"state": "resolved", "address": "192.0.2.10", "source": "模拟控制连接 · 示例地址"})
        for key in ("broadcast", "keyboard", "drivers"):
            self.capabilities[key].configure(text="模拟事件")
        samples = [("starting", "演示：自动模式开启", "所有记录均为模拟事件"),
                   ("broadcast_request", "演示：收到全屏广播请求", "广播请求进入自动处理队列"),
                   ("windowed", "演示：广播已窗口化", "可继续观看，并切换临时全屏"),
                   ("keyboard_denied", "演示：拒绝新的快捷键限制请求", "现有驱动状态未重置")]
        for index, (kind, title, detail) in enumerate(samples):
            self.demo_jobs.append(self.root.after(index * 650, lambda k=kind, t=title, d=detail: self.add(Event(k, t, d).data())))

    def toggle_fullscreen(self):
        if self.demo:
            self.add(Event("demo", "演示：切换广播显示方式", "这不会操作真实窗口").data())
        elif self.engine:
            self.engine.toggle_fullscreen()

    def pump(self):
        for _ in range(200):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event.get("kind") == "show_ui":
                self.root.deiconify()
                self.root.lift()
            elif event.get("kind") == "close_ui":
                self.close()
            else:
                self.add(event)
            if self.closed:
                return
        if not self.closed:
            self.root.after(120, self.pump)

    def hide(self):
        if self.tray is None:
            try:
                import pystray
                from PIL import Image, ImageDraw
                image = Image.new("RGBA", (64, 64), ACCENT)
                draw = ImageDraw.Draw(image)
                draw.rounded_rectangle((13, 13, 51, 51), radius=10, outline="white", width=4)
                menu = pystray.Menu(pystray.MenuItem("显示窗口", lambda *_: self.events.put({"kind": "show_ui"}), default=True),
                                    pystray.MenuItem("停止并退出", lambda *_: self.events.put({"kind": "close_ui"})))
                self.tray = pystray.Icon("OsEasyAuto", image, "OsEasy Auto", menu)
                self.tray.run_detached()
            except Exception as error:
                self.tray = None
                self.add(Event("error", "暂时无法收起到托盘", str(error), "warning").data())
                return
        self.root.withdraw()

    def close(self):
        if self.active:
            self.closing = True
            self.toggle()
        else:
            self.destroy()

    def destroy(self):
        if self.closed:
            return
        self.closed = True
        if self.tray:
            self.tray.stop()
        self.root.destroy()


def run(demo=False, autostart=False):
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (OSError, AttributeError):
        pass
    root = tk.Tk()
    app = Desktop(root, demo=demo)
    if autostart:
        root.after(300, app.toggle)
    root.mainloop()
