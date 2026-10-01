#!/usr/bin/env python3
"""Read-only textual Kconfig warning report; never decides what can be removed.

Source statements and dependency expressions are not evaluated. Help can describe
an old alternative or a mitigation, and BROKEN can be conditional. Keep this
report independent of the optimizer's reviewed pruning rules.
"""

import argparse
import json
import os
from pathlib import Path
import platform
import re
import sys


LABEL = re.compile(r"\b(?:legacy|deprecated|obsolete|dangerous|unsafe|unmaintained)\b", re.I)
HELP = re.compile(
    r"\b(?:deprecated|obsolete|unmaintained|dangerous|unsafe)\b"
    r"|will\s+be\s+removed|not\s+(?:for\s+use\s+in|suitable\s+for|in)\s+production"
    r"|(?:not\s+be\s+enabled|do\s+not\s+(?:use|enable)).{0,70}?production"
    r"|known\s+bugs|data\s+(?:loss|corruption)|filesystem\s+corruption"
    r"|will\s+be\s+overwritten|may\s+crash|really\s+bad\s+idea", re.I | re.S,
)
QUOTED = re.compile(r'''^(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')(.*)$''', re.S)
CONFIG = re.compile(r"(?:config|menuconfig)\s+([A-Za-z0-9_]+)\b")
BOUNDARY = re.compile(r"(?:menu|endmenu|choice|endchoice|source|rsource|osource|orsource|if|endif|comment)\b")


def prompt(text):
    match = QUOTED.match(text.lstrip())
    if not match:
        return None
    value = match[1] if match[1] is not None else match[2]
    return re.sub(r"\\(.)", r"\1", value)


def source_arch(value):
    if re.fullmatch(r"x86_64|i[3-6]86", value):
        return "x86"
    if value.startswith("ppc"):
        return "powerpc"
    if value.startswith("riscv"):
        return "riscv"
    return {"aarch64": "arm64", "s390x": "s390"}.get(value, value)


def kconfig_files(root, arch):
    seen = set()
    for directory, dirs, files in os.walk(root, followlinks=True):
        path = Path(directory)
        real = path.resolve()
        if real in seen:
            dirs[:] = []
            continue
        seen.add(real)
        relative = path.relative_to(root)
        if relative.as_posix() == "scripts/kconfig/tests":
            dirs[:] = []
            continue
        if relative.as_posix() == "arch" and arch != "all":
            dirs[:] = [name for name in dirs if name == arch]
        dirs.sort()
        for name in sorted(files):
            if name == "Kconfig" or name.startswith("Kconfig."):
                yield path / name


def blocks(path):
    current = None
    in_help = False
    help_indent = -1
    continuation = ""
    start_line = 0
    for number, raw in enumerate(path.read_text(errors="replace").splitlines(), 1):
        expanded = raw.expandtabs(8)
        text = expanded.strip()
        indent = len(expanded) - len(expanded.lstrip())
        if in_help and text:
            if help_indent < 0:
                help_indent = indent
            if indent < help_indent:
                in_help = False
        if in_help:
            if current:
                current["help"].append((number, raw))
            continue
        if not continuation:
            start_line = number
        if text.endswith("\\"):
            continuation += text[:-1] + " "
            continue
        text = continuation + text
        continuation = ""
        match = CONFIG.match(text)
        if match or BOUNDARY.match(text):
            if current:
                yield current
            current = None
        if match:
            current = {"symbol": match[1], "line": start_line, "type": None,
                       "prompt": None, "properties": [], "help": []}
        if current and text and not text.startswith("#"):
            current["properties"].append((start_line, text))
            kind = re.match(r"(bool|tristate|def_bool|def_tristate|int|hex|string)\b(.*)", text)
            if kind:
                current["type"] = kind[1].removeprefix("def_")
                label = None if kind[1].startswith("def_") else prompt(kind[2])
                if label is not None:
                    current["prompt"] = label
            elif text.startswith("prompt ") or text.startswith("prompt\t"):
                current["prompt"] = prompt(text[6:])
        if text in ("help", "---help---"):
            in_help = True
            help_indent = -1
    if current:
        yield current


def config_values(path):
    if path is None:
        return {}
    text = path.read_text()
    values = dict(re.findall(r"^CONFIG_([A-Za-z0-9_]+)=(.*)$", text, re.M))
    values.update({sym: "n" for sym in re.findall(r"^# CONFIG_([A-Za-z0-9_]+) is not set$", text, re.M)})
    return values


def audit(root, config=None, arch=None):
    arch = source_arch(arch or platform.machine())
    values = config_values(config)
    entries = []
    file_count = 0
    for path in kconfig_files(root, arch):
        file_count += 1
        for block in blocks(path):
            evidence = []
            for number, text in block["properties"]:
                if re.match(r"depends\s+on\b", text) and re.search(r"\bBROKEN\b", text.split("#", 1)[0]):
                    evidence.append({"category": "conditional-broken-reference", "line": number, "text": text})
                if re.match(r"(?:bool|tristate|prompt)\s", text):
                    label = prompt(text.split(maxsplit=1)[1])
                    if label and LABEL.search(label):
                        evidence.append({"category": "prompt-label", "line": number, "text": label})
            # Search joined help as warnings can wrap across several lines.
            help_text = "\n".join(text for _, text in block["help"])
            reported_lines = set()
            for match in HELP.finditer(help_text):
                index = help_text[:match.start()].count("\n")
                if index in reported_lines:
                    continue
                reported_lines.add(index)
                end_index = help_text[:match.end()].count("\n")
                number = block["help"][index][0]
                snippet = " ".join(text.strip() for _, text in block["help"][max(0, index-1):end_index+2])
                evidence.append({"category": "help-reference", "line": number, "text": snippet})
            if evidence:
                entries.append({"symbol": block["symbol"], "type": block["type"],
                                "prompt": block["prompt"], "file": str(path.relative_to(root)),
                                "line": block["line"], "value": values.get(block["symbol"], "absent" if config else "unknown"),
                                "evidence": evidence, "disposition": "review-context"})
    entries.sort(key=lambda entry: (entry["value"] not in ("y", "m"), entry["symbol"], entry["file"]))
    return {"root": str(root), "arch": arch, "config": str(config) if config else None,
            "scope": "Textual references only; source/depends/select are not evaluated. No automatic removal decisions.",
            "files": file_count, "entries": entries}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel-srcdir", required=True, type=Path)
    parser.add_argument("--config-file", type=Path, help="relative paths are relative to the kernel tree")
    parser.add_argument("--arch", default=os.environ.get("ARCH", platform.machine()), help="target architecture, or all")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        parser.error("Python 3.11 or later is required")
    root = args.kernel_srcdir.resolve()
    if not (root / "Kconfig").is_file():
        parser.error(f"missing Kconfig in {root}")
    config = args.config_file
    if config is not None:
        config = config if config.is_absolute() else root / config
        if not config.is_file():
            parser.error(f"config is not a regular file: {config}")
    elif (root / ".config").is_file():
        config = root / ".config"
    try:
        report = audit(root, config, args.arch)
    except OSError as exc:
        parser.error(str(exc))
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"Kconfig audit: {root} (arch={report['arch']}, {report['files']} files)")
        print(report["scope"])
        print(f"Configuration: {config or 'not supplied; activation unknown'}")
        print("Help may describe a replacement or mitigation; BROKEN may apply only to other configurations.")
        print(f"References to review: {len(report['entries'])}")
        for entry in report["entries"]:
            print(f"\nCONFIG_{entry['symbol']}={entry['value']} [{entry['type']}] — {entry['prompt'] or '(no prompt)'}")
            for item in entry["evidence"]:
                print(f"  {entry['file']}:{item['line']} [{item['category']}] {item['text']}")


if __name__ == "__main__":
    main()
