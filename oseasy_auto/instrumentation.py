from __future__ import annotations

import hashlib
import json
from pathlib import Path

import frida

from .model import ASSETS, Identity


class Observer:
    def __init__(self, identity: Identity, root: Path, role: str, modules: dict[str, str] | list[str], callback):
        self.session = None
        self.script = None
        self.identity = identity
        self.capabilities = {}
        if not isinstance(modules, dict):
            modules = {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in modules}
        configuration = {"role": role, "trustedModules": list(modules),
                         "trustedHashes": {path.replace("/", "\\").lower(): digest for path, digest in modules.items()},
                         "renderers": [path for path in modules if Path(path).name.lower() in ("screenrender.exe", "screenrender_y.exe")]}
        source = (ASSETS / "observer.js").read_text(encoding="utf-8")
        source = source.replace("__CONFIG__", json.dumps(configuration, ensure_ascii=True), 1)
        try:
            self.session = frida.get_local_device().attach(identity.pid)
            self.session.on("detached", lambda reason, *_: callback({"kind": "detached", "reason": reason}))
            self.script = self.session.create_script(source, name="oseasy-auto")
            self.script.on("message", lambda message, data: self._message(message, callback))
            self.script.load()
            self.capabilities = self.script.exports_sync.status()["capabilities"]
        except Exception:
            self.close()
            raise

    def _message(self, message, callback):
        if message.get("type") == "send" and isinstance(message.get("payload"), dict):
            callback(message["payload"])
        elif message.get("type") == "error":
            callback({"kind": "hook_error", "error": message.get("description", "脚本异常")})

    def close(self) -> list[str]:
        errors = []
        if self.script:
            try:
                self.script.exports_sync.dispose()
            except (frida.InvalidOperationError, frida.TransportError):
                pass  # A terminated process has no remaining in-process hooks.
            except Exception as error:
                errors.append(str(error))
            try:
                self.script.unload()
            except frida.InvalidOperationError:
                pass
            except Exception as error:
                errors.append(str(error))
            self.script = None
        if self.session:
            try:
                self.session.detach()
            except frida.InvalidOperationError:
                pass
            except Exception as error:
                errors.append(str(error))
            self.session = None
        return errors
