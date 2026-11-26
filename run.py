from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def probe():
    import frida
    from oseasy_auto import __version__
    from oseasy_auto.engine import discover
    from oseasy_auto.model import ASSETS, SampleGate
    from oseasy_auto.teacher import TeacherDiscovery, TeacherTarget
    gate = SampleGate()
    identities = discover()
    targets = [TeacherTarget(identity, match[0], match[1]) for identity in identities if (match := gate.identify(identity))]
    return {"version": __version__, "platform": platform.platform(), "frida": frida.__version__,
            "resource_check": all((ASSETS / name).is_file() for name in ("observer.js", "target.json")),
            "processes": [{"pid": identity.pid, "name": Path(identity.path).name, "sample_matches": gate.identify(identity) is not None}
                          for identity in identities], "teacher": TeacherDiscovery().refresh(targets),
            "mode": "read_only_no_attachment"}


def self_test():
    import frida
    from oseasy_auto.instrumentation import Observer
    from oseasy_auto.model import Identity
    import psutil
    args = [sys.executable]
    if not getattr(sys, "frozen", False):
        args.append(str(Path(__file__).resolve()))
    args.append("--test-worker")
    temporary = tempfile.TemporaryDirectory(prefix="oseasy-auto-selftest-")
    pid_file = Path(temporary.name) / "worker.pid"
    args.extend(["--worker-pid-file", str(pid_file)])
    worker = subprocess.Popen(args, creationflags=subprocess.CREATE_NO_WINDOW)
    observer = None
    try:
        deadline = time.monotonic() + 25
        while not pid_file.exists():
            if worker.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("自检进程未能准备就绪")
            time.sleep(.1)
        process = psutil.Process(int(pid_file.read_text(encoding="ascii")))
        identity = Identity(process.pid, process.create_time(), process.exe())
        messages = []
        observer = Observer(identity, Path(process.exe()).parent, "renderer", [], messages.append)
        capabilities = observer.capabilities
        if not capabilities.get("broadcast"):
            raise RuntimeError("CreateProcess observer not installed")
        errors = observer.close()
        observer = None
        if errors or worker.poll() is not None:
            raise RuntimeError(f"Cleanup failed: {errors}")
        return {"ok": True, "frida": frida.__version__, "own_worker_only": True,
                "capabilities": capabilities, "cleanup": "verified_worker_survived_detach"}
    finally:
        if observer:
            observer.close()
        if worker.poll() is None:
            try:
                children = psutil.Process(worker.pid).children(recursive=True)
                for child in reversed(children):
                    try:
                        child.terminate()
                    except psutil.NoSuchProcess:
                        pass
                psutil.wait_procs(children, timeout=5)
            except psutil.NoSuchProcess:
                pass
            if worker.poll() is None:
                worker.terminate()
            worker.wait(timeout=10)
        temporary.cleanup()


def main():
    parser = argparse.ArgumentParser(description="OsEasy-ToolBox — 原版界面自动增强")
    parser.add_argument("--demo", action="store_true", help="仅演示交互，不连接学生端")
    parser.add_argument("--autostart", action="store_true", help="开启当前会话自动模式")
    parser.add_argument("--probe", action="store_true", help="只读识别，不注入进程")
    parser.add_argument("--probe-file", type=Path)
    parser.add_argument("--self-test", action="store_true", help="只对本程序自建测试进程验证监听和清理")
    parser.add_argument("--self-test-file", type=Path)
    parser.add_argument("--test-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--worker-pid-file", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.test_worker:
        if args.worker_pid_file:
            args.worker_pid_file.write_text(str(os.getpid()), encoding="ascii")
        time.sleep(60)
        return
    if args.probe or args.probe_file or args.self_test or args.self_test_file:
        try:
            result = self_test() if args.self_test or args.self_test_file else probe()
        except Exception as error:
            result = {"ok": False, "error": str(error)}
        destination = args.probe_file or args.self_test_file
        if destination:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if sys.stdout:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        if result.get("ok") is False:
            raise SystemExit(1)
        return
    from src.REmainv3 import launch
    launch(preview=args.demo, autostart=args.autostart)


if __name__ == "__main__":
    main()
