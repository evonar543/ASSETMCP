"""Manifest and credits helpers for downloaded/imported assets."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from assetmcp.schemas import AssetResult, LicenseCheck, normalize_asset_result

MANIFEST_FILENAME = "ASSET_MANIFEST.json"
CREDITS_FILENAME = "CREDITS.md"


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "assets": []}
    return json.loads(path.read_text(encoding="utf-8"))


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def upsert_manifest_entry(
    manifest_path: Path,
    asset_like: AssetResult | dict,
    license_check: LicenseCheck,
    files: list[str],
    *,
    checksum_sha256: str | None = None,
) -> dict[str, Any]:
    asset = asset_like if isinstance(asset_like, AssetResult) else normalize_asset_result(asset_like)
    manifest = load_manifest(manifest_path)
    entry = {
        "id": asset.id,
        "title": asset.title,
        "source_name": asset.source_name,
        "source_url": asset.source_url,
        "author": asset.author,
        "license": asset.license,
        "license_url": asset.license_url,
        "attribution_required": license_check.attribution_required,
        "attribution_text": license_check.attribution_text,
        "license_status": license_check.status,
        "warnings": sorted(set(asset.warnings + license_check.warnings)),
        "files": files,
        "checksum_sha256": checksum_sha256,
        "added_at": datetime.now(UTC).isoformat(),
    }
    assets = [item for item in manifest.get("assets", []) if item.get("id") != asset.id]
    assets.append(entry)
    manifest["assets"] = sorted(assets, key=lambda item: item.get("title", ""))
    write_manifest(manifest_path, manifest)
    return entry


def generate_credits_text(manifest: dict[str, Any], title: str = "Asset Credits") -> str:
    lines = [f"# {title}", ""]
    credited = [
        item for item in manifest.get("assets", [])
        if item.get("attribution_required") or item.get("attribution_text")
    ]
    if not credited:
        lines.append("No attribution-required assets are currently tracked.")
        lines.append("")
        return "\n".join(lines)
    for item in credited:
        attribution = item.get("attribution_text")
        if not attribution:
            attribution = (
                f"{item.get('title', 'Untitled asset')} by {item.get('author') or 'unknown author'}; "
                f"license: {item.get('license') or 'unknown'}; source: {item.get('source_url') or 'unknown'}"
            )
        lines.append(f"- {attribution}")
    lines.append("")
    return "\n".join(lines)
