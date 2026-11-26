"""Read sample files without loading DLLs or executing sample programs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import pefile


FOCUS_NAMES = {
    "mmcstudent.exe", "student.exe", "teacher.exe", "mmpc.exe",
    "screenrender.exe", "screensender.exe", "studentlogic.dll", "mainlogic.dll",
    "multiclient.exe", "blackslient.exe", "lockkeyboard.dll", "loaddriver.exe",
    "lisshelper.exe", "lissnetinfosniffer.exe", "oenetlimit.dll", "easyusbctrl.dll",
    "devicecontrol_x64.exe", "devicecontrol_x86.exe", "netlimitinterface.dll",
    "mmcwrapper.dll", "mmcimplbase.dll", "vkctrl.dll", "mtdrpt.sys", "mtpcpt.sys",
    "antihelper.dll", "student_screen_helper.exe", "studentscreenhelper.exe",
}
CONFIG_KEYS = {
    "IsTeacherSide", "ConnectPort", "MultiCastPort", "ScreenUdpVerityPort",
    "MmpcBindPort", "MultiClientBindPort", "DeviceControlBindPort", "LissHelperBindPort",
    "ChannleScanPort", "LockedAfterNetBroken", "StuAutoStart", "MmpcProtect",
}
VERSION_RE = re.compile(r"(?<![\d.])(?:[vV])?10\.\d+\.\d+\.\d+(?![\d.])")
RENDER_KEYS = {
    "decoderName", "fullscreen", "local", "port", "remote", "teacher_ip", "verityPort",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def decode(value: bytes | str) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def strings(data: bytes):
    for match in re.finditer(rb"[\x20-\x7e]{4,}", data):
        yield match.start(), "ascii", match.group().decode("ascii")
    for match in re.finditer(rb"(?:[\x20-\x7e]\x00){4,}", data):
        yield match.start(), "utf-16le", match.group().decode("utf-16le")


def inspect_pe(path: Path, root: Path) -> dict:
    data = path.read_bytes()
    output = {"path": path.relative_to(root).as_posix(), "sha256": hashlib.sha256(data).hexdigest()}
    pe = pefile.PE(data=data, fast_load=True)
    try:
        directories = ["IMAGE_DIRECTORY_ENTRY_IMPORT", "IMAGE_DIRECTORY_ENTRY_EXPORT",
                       "IMAGE_DIRECTORY_ENTRY_RESOURCE", "IMAGE_DIRECTORY_ENTRY_DEBUG"]
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY[name] for name in directories])
        output["machine"] = hex(pe.FILE_HEADER.Machine)
        output["architecture"] = {0x14C: "x86", 0x8664: "x86_64", 0xAA64: "arm64"}.get(pe.FILE_HEADER.Machine, "unknown")
        output["is_dll"] = bool(pe.FILE_HEADER.Characteristics & 0x2000)
        output["is_managed"] = bool(pe.OPTIONAL_HEADER.DATA_DIRECTORY[14].VirtualAddress)
        output["coff_timestamp"] = pe.FILE_HEADER.TimeDateStamp
        output["imports"] = [decode(item.dll) for item in getattr(pe, "DIRECTORY_ENTRY_IMPORT", [])]
        export_dir = getattr(pe, "DIRECTORY_ENTRY_EXPORT", None)
        output["exports"] = [decode(symbol.name) if symbol.name else f"ordinal:{symbol.ordinal}"
                             for symbol in export_dir.symbols] if export_dir else []
        output["version_strings"] = {}
        for file_info_group in getattr(pe, "FileInfo", []):
            for file_info in file_info_group:
                for table in getattr(file_info, "StringTable", []):
                    for key, value in table.entries.items():
                        if decode(key) in {"FileVersion", "ProductVersion", "ProductName", "CompanyName", "OriginalFilename", "FileDescription"}:
                            output["version_strings"][decode(key)] = decode(value)
        output["codeview_pdb_records"] = []
        for entry in getattr(pe, "DIRECTORY_ENTRY_DEBUG", []):
            if entry.struct.Type != 2:
                continue
            start = entry.struct.PointerToRawData
            end = start + entry.struct.SizeOfData
            payload = data[start:end]
            prefix_size = {b"RSDS": 24, b"NB10": 16}.get(payload[:4])
            if prefix_size is not None:
                pdb_path = payload[prefix_size:].split(b"\x00", 1)[0].decode("utf-8", errors="replace")
                output["codeview_pdb_records"].append({"path": pdb_path, "start_file_offset": start,
                                                       "end_file_offset_exclusive": end})
        output["version_literals"] = []
        output["renderer_key_literals"] = []
        for offset, encoding, text in strings(data):
            for match in VERSION_RE.finditer(text):
                match_offset = offset + match.start() * (2 if encoding == "utf-16le" else 1)
                source = "codeview_pdb_path" if any(
                    record["start_file_offset"] <= match_offset < record["end_file_offset_exclusive"]
                    for record in output["codeview_pdb_records"]
                ) else "unattributed_binary_string"
                output["version_literals"].append({"value": match.group(), "string_file_offset": offset,
                                                    "match_file_offset": match_offset,
                                                    "encoding": encoding, "source": source})
            if path.name.lower() == "screenrender.exe" and text in RENDER_KEYS:
                output["renderer_key_literals"].append({"value": text, "file_offset": offset, "encoding": encoding})
    finally:
        pe.close()
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sample_root", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.sample_root.resolve(strict=True)
    if not root.is_dir():
        parser.error("sample_root must be a directory")
    files = sorted(path for path in root.rglob("*") if path.is_file())
    focus_files = [path for path in files if path.name.lower() in FOCUS_NAMES]
    binaries = []
    errors = []
    for path in focus_files:
        try:
            binaries.append(inspect_pe(path, root))
        except (pefile.PEFormatError, OSError) as error:
            errors.append({"path": path.relative_to(root).as_posix(), "error": str(error)})
    inventory = [{"path": path.relative_to(root).as_posix(), "size": path.stat().st_size,
                  "sha256": sha256(path)} for path in files]
    config_path = root / "skin" / "core.conf"
    config = {}
    for line_number, line in enumerate(config_path.read_text(encoding="utf-8-sig", errors="replace").splitlines(), 1):
        match = re.fullmatch(r"/([^/]+)/([^/]*)/", line)
        if match and match[1] in CONFIG_KEYS:
            config[match[1]] = {"value": match[2], "line": line_number}
    legacy_names = ["Student.exe", "MultiClient.exe", "BlackSlient.exe", "LockKeyboard.dll", "LoadDriver.exe",
                    "LISSNetInfoSniffer.exe", "OeNetLimitSetup.exe", "OeNetLimit.sys", "OeNetLimit.inf",
                    "oenetlimitx64.cat", "easyusbflt.sys", "ScreenRender_Helper.exe"]
    presence = {name: [path.relative_to(root).as_posix() for path in files if path.name.lower() == name.lower()]
                for name in legacy_names}
    profile = {
        "schema_version": 1,
        "evidence_type": "offline_static_file_inspection",
        "sample_root": str(root),
        "archive": {"path": str(args.archive), "size": args.archive.stat().st_size, "sha256": sha256(args.archive)} if args.archive else None,
        "file_count": len(files),
        "total_file_bytes": sum(item["size"] for item in inventory),
        "extensions": dict(Counter(path.suffix.lower() for path in files)),
        "binaries": binaries,
        "legacy_component_presence": presence,
        "bundled_default_config": {"path": "skin/core.conf", "values": config},
        "inventory": inventory,
        "errors": errors,
        "limitations": [
            "No sample program or DLL was executed or loaded.",
            "Version literals are raw strings, not proof of an installed or running version.",
            "Export names do not establish parameter types, calling conventions, or runtime effects.",
            "Bundled configuration does not establish current registry, services, host OS, or classroom policy.",
            "A directory copy can omit files outside the directory, including installed system drivers.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"file_count": len(files), "focus_binary_count": len(binaries), "errors": errors,
                      "output": str(args.output),
                      "versions": {item["path"]: sorted({literal["value"] for literal in item["version_literals"]})
                                   for item in binaries if item["version_literals"]}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
