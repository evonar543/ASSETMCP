"""Local file scanning helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

SUSPICIOUS_EXTS = {
    ".exe",
    ".dll",
    ".bat",
    ".cmd",
    ".ps1",
    ".msi",
    ".scr",
    ".vbs",
    ".js",
    ".jar",
    ".com",
}


def scan_suspicious_files(root: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in SUSPICIOUS_EXTS:
            findings.append(
                {
                    "path": str(path),
                    "extension": path.suffix.lower(),
                    "warning": "Suspicious executable/script file. Do not execute downloaded assets.",
                }
            )
    return findings
