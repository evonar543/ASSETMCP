"""Shared data shapes for ASSETMCP providers and tools."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class AssetResult:
    """Normalized asset search result returned by all providers."""

    id: str
    title: str
    source_name: str
    source_url: str
    author: str | None = None
    license: str | None = None
    license_url: str | None = None
    attribution_required: bool | None = None
    asset_type: str | None = None
    tags: list[str] = field(default_factory=list)
    preview_image_url: str | None = None
    download_url: str | None = None
    file_formats: list[str] = field(default_factory=list)
    confidence_score: float = 0.5
    warnings: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LicenseCheck:
    """Decision from the strict license checker."""

    status: str
    allowed: bool
    license: str | None
    attribution_required: bool
    attribution_text: str | None = None
    warnings: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_asset_result(raw: dict[str, Any]) -> AssetResult:
    """Coerce legacy or provider-specific dictionaries into AssetResult."""
    source_name = raw.get("source_name") or raw.get("source") or raw.get("provider") or "unknown"
    source_url = raw.get("source_url") or raw.get("url") or raw.get("foreign_landing_url") or ""
    title = raw.get("title") or raw.get("name") or source_url.rsplit("/", 1)[-1] or "Untitled asset"
    asset_id = raw.get("id") or f"{source_name}:{title}:{source_url}"
    download_url = raw.get("download_url")
    downloads = raw.get("downloads") or []
    if not download_url and downloads:
        first = downloads[0] if isinstance(downloads[0], dict) else {}
        download_url = first.get("url")
    file_formats = raw.get("file_formats") or []
    if not file_formats and downloads:
        file_formats = [
            item.get("extension", "").lstrip(".")
            for item in downloads
            if isinstance(item, dict) and item.get("extension")
        ]
    license_name = raw.get("license") or raw.get("license_name")
    attribution_required = raw.get("attribution_required")
    if attribution_required is None and license_name:
        attribution_required = "by" in str(license_name).lower() and "cc0" not in str(license_name).lower()
    return AssetResult(
        id=str(asset_id),
        title=str(title),
        source_name=str(source_name),
        source_url=str(source_url),
        author=raw.get("author") or raw.get("creator"),
        license=license_name,
        license_url=raw.get("license_url"),
        attribution_required=attribution_required,
        asset_type=raw.get("asset_type") or raw.get("kind") or raw.get("type"),
        tags=list(raw.get("tags") or []),
        preview_image_url=raw.get("preview_image_url") or raw.get("thumbnail"),
        download_url=download_url,
        file_formats=list(dict.fromkeys(str(item).lstrip(".") for item in file_formats if item)),
        confidence_score=float(raw.get("confidence_score", raw.get("confidence", 0.5)) or 0.5),
        warnings=list(raw.get("warnings") or []),
        extra=dict(raw.get("extra") or {}),
    )
