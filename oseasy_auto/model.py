from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


ASSETS = Path(__file__).parent / "assets"


@dataclass(frozen=True)
class Identity:
    pid: int
    created: float
    path: str

    @property
    def key(self) -> str:
        return f"{self.pid}:{self.created:.6f}"


@dataclass
class Event:
    kind: str
    title: str
    detail: str = ""
    level: str = "info"
    pid: int = 0
    at: float = 0

    def __post_init__(self):
        self.at = self.at or time.time()

    def data(self):
        return asdict(self)


def state_directory() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "OsEasyAuto"
    root.mkdir(parents=True, exist_ok=True)
    return root


def atomic_json(path: Path, data: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class SampleGate:
    """Accept content identities, never just executable names or a PDB label."""

    def __init__(self, profile: dict | None = None):
        self.profile = profile or json.loads((ASSETS / "target.json").read_text(encoding="utf-8"))
        self.cache: dict[str, tuple[tuple, bool]] = {}

    def accepts(self, path: Path, relative: str) -> bool:
        expected = self.profile["files"].get(relative)
        if expected is None:
            return False
        try:
            stat = path.stat()
            stamp = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino)
            key = str(path.resolve()) + "|" + relative
            cached = self.cache.get(key)
            if cached and cached[0] == stamp:
                return cached[1]
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            accepted = digest == expected["sha256"]
            self.cache[key] = (stamp, accepted)
            return accepted
        except OSError:
            return False

    def identify(self, identity: Identity) -> tuple[Path, str] | None:
        path = Path(identity.path).resolve()
        name = path.name.lower()
        if name == "mmcstudent.exe":
            relative, role, root = "MmcStudent.exe", "student", path.parent
        elif name in ("screenrender.exe", "screenrender_y.exe"):
            relative, role, root = "ScreenRender.exe", "renderer", path.parent
        elif name in ("devicecontrol_x64.exe", "devicecontrol_x86.exe"):
            arch = "x64" if name.endswith("x64.exe") else "x86"
            relative, role, root = f"{arch}/{path.name}", "device", path.parent.parent
        else:
            return None
        if not self.accepts(path, relative):
            return None
        # Bind helpers to the same sample installation, not merely copied names.
        if not self.accepts(root / "MmcStudent.exe", "MmcStudent.exe"):
            return None
        return root, role

    def trusted_modules(self, root: Path) -> dict[str, str]:
        paths = {str((root / relative).resolve()): expected["sha256"]
                 for relative, expected in self.profile["files"].items()
                 if self.accepts(root / relative, relative)}
        alias = root / "ScreenRender_Y.exe"
        if self.accepts(alias, "ScreenRender.exe"):
            paths[str(alias.resolve())] = self.profile["files"]["ScreenRender.exe"]["sha256"]
        return paths


class Journal:
    def __init__(self, path: Path):
        self.path = path
        self.entries: dict[str, dict] = {}
        if path.exists():
            document = json.loads(path.read_text(encoding="utf-8"))
            if document.get("schema") != 1 or not isinstance(document.get("windows"), dict):
                raise ValueError("恢复记录格式不正确")
            if not all(isinstance(key, str) and isinstance(value, dict) for key, value in document["windows"].items()):
                raise ValueError("恢复记录内容损坏，未进行任何窗口修改")
            self.entries = document["windows"]

    def save(self):
        atomic_json(self.path, {"schema": 1, "windows": self.entries})

    def remember(self, key: str, snapshot: dict):
        if key not in self.entries:
            self.entries[key] = snapshot
            self.save()  # Write before touching a window, including partial failures.

    def forget(self, key: str):
        if key in self.entries:
            del self.entries[key]
            self.save()
