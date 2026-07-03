"""ASSETMCP server implementation.

The server intentionally keeps all downloaded/extracted files inside a managed
asset library. That matters for MCP use: a model can ask to download or unpack
unknown remote content, so every filesystem write goes through path guards before
touching disk.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import math
import os
import re
import tarfile
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from shutil import copy2
from typing import Any
from urllib.parse import quote_plus, unquote, urljoin, urlparse

import filetype
import httpx
import numpy as np
import py7zr
import trimesh
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP
from PIL import Image, ImageDraw, ImageFilter, ImageOps
from pygltflib import GLTF2

from assetmcp.providers import PROVIDERS, search_providers
from assetmcp.schemas import AssetResult, normalize_asset_result
from assetmcp.services.file_scanner import scan_suspicious_files
from assetmcp.services.file_scanner import SUSPICIOUS_EXTS
from assetmcp.services.license_checker import check_asset_license
from assetmcp.services.manifest import (
    CREDITS_FILENAME,
    MANIFEST_FILENAME,
    generate_credits_text,
    load_manifest,
    upsert_manifest_entry,
    write_manifest,
)
from assetmcp.services.scaffolder import scaffold_game_project

logging.getLogger("httpx").setLevel(logging.WARNING)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LIBRARY_ROOT = Path(
    os.environ.get("ASSETMCP_LIBRARY_DIR", PROJECT_ROOT / "assets")
).resolve()
PREVIEW_ROOT = Path(
    os.environ.get("ASSETMCP_PREVIEW_DIR", PROJECT_ROOT / "previews")
).resolve()
MAX_DOWNLOAD_MB = int(os.environ.get("ASSETMCP_MAX_DOWNLOAD_MB", "512"))
USER_AGENT = (
    "ASSETMCP/0.3 (+https://modelcontextprotocol.io; game asset forge MCP server)"
)

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tga", ".tif", ".tiff"}
MODEL_EXTS = {".glb", ".gltf", ".obj", ".stl", ".ply", ".dae", ".3mf", ".off"}
AUDIO_EXTS = {".wav", ".ogg", ".mp3", ".flac", ".m4a"}
ARCHIVE_EXTS = {".zip", ".tar", ".tgz", ".tar.gz", ".tar.bz2", ".tar.xz", ".7z"}
TEXTURE_HINTS = ("texture", "spritesheet", "sprite", "tileset", "tile", "atlas")
KENNEY_CATEGORY_MAP = {
    "2d": "category:2D",
    "3d": "category:3D",
    "audio": "category:Audio",
    "textures": "category:Textures",
    "texture": "category:Textures",
    "ui": "tag:interface",
    "pixel": "tag:pixel",
}
OPENGAMEART_NON_ASSET_SLUGS = {
    "faq",
    "about",
    "contact",
    "content",
    "copyright-policy",
    "privacy-policy",
    "terms-of-use",
}


mcp = FastMCP(
    "ASSETMCP",
    log_level="ERROR",
    instructions=(
        "Game Asset Forge server. Search normalized providers with search_assets, "
        "check licenses before download/import, track downloaded assets in "
        "ASSET_MANIFEST.json and CREDITS.md, inspect and convert assets, and scaffold "
        "playable prototypes for HTML Canvas, Phaser, Three.js, and Godot 4."
    ),
)


@dataclass(frozen=True)
class AssetLink:
    title: str
    url: str
    kind: str
    extension: str
    source: str

    def as_dict(self) -> dict[str, str]:
        return {
            "title": self.title,
            "url": self.url,
            "kind": self.kind,
            "extension": self.extension,
            "source": self.source,
        }


def _ensure_roots() -> None:
    LIBRARY_ROOT.mkdir(parents=True, exist_ok=True)
    PREVIEW_ROOT.mkdir(parents=True, exist_ok=True)


def _slug(value: str, fallback: str = "asset") -> str:
    value = unquote(value).strip().lower()
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-._")
    return value or fallback


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _safe_output_path(folder: Path, filename: str) -> Path:
    """Return a collision-free output path that cannot escape the asset library."""
    folder = folder.resolve()
    if not _is_inside(folder, LIBRARY_ROOT) and folder != LIBRARY_ROOT:
        raise ValueError(f"Output folder must stay inside {LIBRARY_ROOT}")
    folder.mkdir(parents=True, exist_ok=True)

    clean = _slug(filename, "asset")
    target = (folder / clean).resolve()
    if not _is_inside(target, folder):
        raise ValueError("Unsafe output filename")

    if not target.exists():
        return target

    stem = target.stem
    suffix = target.suffix
    for index in range(2, 10000):
        candidate = (folder / f"{stem}-{index}{suffix}").resolve()
        if not candidate.exists():
            return candidate
    raise ValueError(f"Could not find a free filename for {filename}")


def _resolve_read_path(path: str) -> Path:
    """Resolve user-provided paths while limiting reads to project-owned folders."""
    raw = Path(path).expanduser()
    resolved = (LIBRARY_ROOT / raw).resolve() if not raw.is_absolute() else raw.resolve()
    allowed_roots = [LIBRARY_ROOT, PREVIEW_ROOT, PROJECT_ROOT]
    if not any(_is_inside(resolved, root) or resolved == root for root in allowed_roots):
        raise ValueError(
            f"Path must be inside the ASSETMCP workspace/library. Got: {resolved}"
        )
    return resolved


def _subfolder_path(subfolder: str | None) -> Path:
    """Resolve a library subfolder from an MCP argument."""
    if not subfolder:
        return LIBRARY_ROOT
    target = (LIBRARY_ROOT / _slug(subfolder, "assets")).resolve()
    if not _is_inside(target, LIBRARY_ROOT):
        raise ValueError("Subfolder must stay inside the asset library")
    return target


def _extension_from_url(url: str) -> str:
    path = unquote(urlparse(url).path).lower()
    if path.endswith(".tar.gz"):
        return ".tar.gz"
    if path.endswith(".tar.bz2"):
        return ".tar.bz2"
    if path.endswith(".tar.xz"):
        return ".tar.xz"
    return Path(path).suffix.lower()


def _kind_for_url(url: str, text: str = "") -> str:
    """Classify a URL or filename into the broad asset kinds used by the tools."""
    ext = _extension_from_url(url)
    haystack = f"{url} {text}".lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in MODEL_EXTS:
        return "model"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in ARCHIVE_EXTS:
        return "archive"
    if any(hint in haystack for hint in TEXTURE_HINTS):
        return "possible_texture"
    if ext:
        return "file"
    return "page"


def _is_opengameart_asset_page(url: str) -> bool:
    parsed = urlparse(url)
    if "opengameart.org" not in parsed.netloc:
        return False
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2 or parts[0] != "content":
        return False
    return parts[1].lower() not in OPENGAMEART_NON_ASSET_SLUGS


def _guess_filename(url: str, content_disposition: str | None = None) -> str:
    if content_disposition:
        match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', content_disposition)
        if match:
            return _slug(match.group(1), "asset")
    name = Path(unquote(urlparse(url).path)).name
    return _slug(name, "asset.bin")


def _file_summary(path: Path) -> dict[str, Any]:
    kind = filetype.guess(str(path))
    stat = path.stat()
    return {
        "path": str(path),
        "name": path.name,
        "extension": path.suffix.lower(),
        "size_bytes": stat.st_size,
        "mime": kind.mime if kind else None,
        "asset_kind": _kind_for_url(path.name),
    }


def _manifest_path(root: Path | None = None) -> Path:
    return (root or LIBRARY_ROOT) / MANIFEST_FILENAME


def _credits_path(root: Path | None = None) -> Path:
    return (root or LIBRARY_ROOT) / CREDITS_FILENAME


def _error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, **extra}}


def _ok(**payload: Any) -> dict[str, Any]:
    return {"ok": True, **payload}


def _asset_from_download_args(
    *,
    url: str,
    title: str | None = None,
    source_name: str | None = None,
    source_url: str | None = None,
    author: str | None = None,
    license: str | None = None,
    license_url: str | None = None,
    asset_type: str | None = None,
    tags: list[str] | None = None,
    local_user_provided: bool = False,
) -> AssetResult:
    return AssetResult(
        id=f"{source_name or 'direct'}:{title or Path(urlparse(url).path).name or url}",
        title=title or Path(unquote(urlparse(url).path)).name or "Downloaded asset",
        source_name=source_name or ("Local" if local_user_provided else "Direct URL"),
        source_url=source_url or url,
        author=author,
        license=license or ("local user-provided" if local_user_provided else None),
        license_url=license_url,
        attribution_required=None,
        asset_type=asset_type or _kind_for_url(url),
        tags=tags or [],
        download_url=url,
        file_formats=[_extension_from_url(url).lstrip(".")] if _extension_from_url(url) else [],
        confidence_score=0.5,
    )


async def _fetch_html(url: str) -> str:
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
        timeout=httpx.Timeout(30.0, connect=10.0),
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


async def _fetch_json(url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=httpx.Timeout(30.0, connect=10.0),
    ) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response.json()


async def _download(url: str, folder: Path, filename: str | None = None) -> dict[str, Any]:
    """Stream a remote file into the library with size and path checks."""
    _ensure_roots()
    max_bytes = MAX_DOWNLOAD_MB * 1024 * 1024
    hasher = hashlib.sha256()
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
        timeout=httpx.Timeout(120.0, connect=20.0),
    ) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            length = response.headers.get("content-length")
            if length and int(length) > max_bytes:
                raise ValueError(
                    f"Remote file is {int(length)} bytes, above limit {max_bytes} bytes"
                )

            chosen_name = filename or _guess_filename(
                str(response.url), response.headers.get("content-disposition")
            )
            target = _safe_output_path(folder, chosen_name)
            total = 0
            with target.open("wb") as handle:
                async for chunk in response.aiter_bytes(1024 * 256):
                    total += len(chunk)
                    if total > max_bytes:
                        handle.close()
                        target.unlink(missing_ok=True)
                        raise ValueError(
                            f"Download exceeded limit {max_bytes} bytes"
                        )
                    hasher.update(chunk)
                    handle.write(chunk)

    summary = _file_summary(target)
    summary["source_url"] = url
    summary["sha256"] = hasher.hexdigest()
    summary["saved"] = True
    return summary


def _safe_preview_path(filename: str, suffix: str) -> Path:
    output = (PREVIEW_ROOT / _slug(filename, f"preview{suffix}")).resolve()
    if output.suffix.lower() != suffix:
        output = output.with_suffix(suffix)
    if not _is_inside(output, PREVIEW_ROOT):
        raise ValueError("Preview output must stay inside preview folder")
    return output


def _hash_file(path: Path, algorithm: str = "sha256") -> str:
    hasher = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _trim_text(value: Any, limit: int = 500) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())[:limit]


def _source_result(
    source: str,
    title: str,
    url: str,
    *,
    license: str | None = None,
    thumbnail: str | None = None,
    asset_type: str | None = None,
    downloads: list[dict[str, Any]] | None = None,
    description: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize source-specific search results into one shape for agents."""
    record: dict[str, Any] = {
        "source": source,
        "title": _trim_text(title, 160) or url.rsplit("/", 1)[-1],
        "url": url,
        "asset_type": asset_type,
        "license": license,
        "thumbnail": thumbnail,
        "description": _trim_text(description),
        "downloads": downloads or [],
    }
    if extra:
        record.update(extra)
    return record


def _parse_kenney_asset_page(url: str, html_text: str) -> dict[str, Any]:
    """Extract title, license hints, previews, and archive links from a Kenney page."""
    soup = BeautifulSoup(html_text, "lxml")
    title_node = soup.find("h1")
    title = _trim_text(title_node.get_text(" ", strip=True) if title_node else "", 160)
    text = _trim_text(soup.get_text(" ", strip=True), 1200)
    links: list[dict[str, Any]] = []
    for anchor in soup.find_all("a", href=True):
        href = urljoin(url, anchor["href"])
        label = _trim_text(anchor.get_text(" ", strip=True), 120)
        if ".zip" in href.lower() or "download" in label.lower():
            links.append(
                {
                    "title": label or Path(urlparse(href).path).name,
                    "url": href,
                    "kind": _kind_for_url(href, label),
                    "extension": _extension_from_url(href),
                }
            )
    img = soup.find("img")
    thumbnail = urljoin(url, img["src"]) if img and img.get("src") else None
    license_name = "CC0" if "CC0" in text or "Creative Commons CC0" in text else None
    return _source_result(
        "Kenney",
        title or url.rsplit("/", 1)[-1],
        url,
        license=license_name,
        thumbnail=thumbnail,
        downloads=links,
        description=text,
    )

def _extract_zip(archive: Path, destination: Path, *, allow_suspicious: bool = False) -> list[Path]:
    """Extract a zip after validating every member path."""
    extracted: list[Path] = []
    with zipfile.ZipFile(archive) as zf:
        for member in zf.infolist():
            target = (destination / member.filename).resolve()
            if not _is_inside(target, destination):
                raise ValueError(f"Unsafe zip member path: {member.filename}")
            if not allow_suspicious and _is_suspicious_member(member.filename):
                raise ValueError(f"Suspicious executable/script member blocked: {member.filename}")
        zf.extractall(destination)
        for member in zf.infolist():
            if not member.is_dir():
                extracted.append((destination / member.filename).resolve())
    return extracted


def _extract_tar(archive: Path, destination: Path, *, allow_suspicious: bool = False) -> list[Path]:
    """Extract a tar archive after validating every member path."""
    extracted: list[Path] = []
    with tarfile.open(archive) as tf:
        for member in tf.getmembers():
            target = (destination / member.name).resolve()
            if not _is_inside(target, destination):
                raise ValueError(f"Unsafe tar member path: {member.name}")
            if not allow_suspicious and _is_suspicious_member(member.name):
                raise ValueError(f"Suspicious executable/script member blocked: {member.name}")
        tf.extractall(destination)
        for member in tf.getmembers():
            if member.isfile():
                extracted.append((destination / member.name).resolve())
    return extracted


def _extract_7z(archive: Path, destination: Path, *, allow_suspicious: bool = False) -> list[Path]:
    """Extract a 7z archive after validating every member path."""
    with py7zr.SevenZipFile(archive, mode="r") as zf:
        names = zf.getnames()
        for name in names:
            target = (destination / name).resolve()
            if not _is_inside(target, destination):
                raise ValueError(f"Unsafe 7z member path: {name}")
            if not allow_suspicious and _is_suspicious_member(name):
                raise ValueError(f"Suspicious executable/script member blocked: {name}")
        zf.extractall(destination)
    return [p for p in destination.rglob("*") if p.is_file()]


def _dominant_colors(image: Image.Image, count: int = 6) -> list[dict[str, Any]]:
    rgba = image.convert("RGBA")
    thumb = rgba.resize((96, 96), Image.Resampling.LANCZOS)
    pixels = np.asarray(thumb)
    opaque = pixels[pixels[:, :, 3] > 24]
    if opaque.size == 0:
        opaque = pixels.reshape(-1, 4)
    rgb = opaque[:, :3]
    bucketed = (rgb // 32) * 32
    totals = Counter(map(tuple, bucketed.tolist()))
    total = sum(totals.values()) or 1
    colors: list[dict[str, Any]] = []
    for (r, g, b), n in totals.most_common(count):
        colors.append(
            {
                "hex": f"#{int(r):02x}{int(g):02x}{int(b):02x}",
                "approx_percent": round(n / total * 100, 2),
            }
        )
    return colors


def _inspect_image(path: Path) -> dict[str, Any]:
    """Return image metadata plus lightweight visual-awareness signals."""
    with Image.open(path) as img:
        width, height = img.size
        rgba = img.convert("RGBA")
        alpha = rgba.getchannel("A")
        bbox = alpha.getbbox()
        alpha_values = np.asarray(alpha)
        transparent_percent = float(np.mean(alpha_values < 24) * 100)

        gray = ImageOps.grayscale(rgba)
        edges = gray.filter(ImageFilter.FIND_EDGES)
        edge_values = np.asarray(edges)
        edge_density = float(np.mean(edge_values > 32) * 100)

        if bbox:
            left, top, right, bottom = bbox
            content_w = right - left
            content_h = bottom - top
            occupancy = (content_w * content_h) / (width * height) * 100
        else:
            left = top = right = bottom = content_w = content_h = 0
            occupancy = 0.0

        aspect = width / height if height else 0
        content_aspect = content_w / content_h if content_h else 0
        if content_aspect > 1.35:
            silhouette = "wide"
        elif content_aspect < 0.74 and content_aspect > 0:
            silhouette = "tall"
        elif occupancy < 35:
            silhouette = "small-centered-or-sparse"
        else:
            silhouette = "roughly-square"

        return {
            **_file_summary(path),
            "width": width,
            "height": height,
            "mode": img.mode,
            "aspect_ratio": round(aspect, 4),
            "dominant_colors": _dominant_colors(rgba),
            "transparency_percent": round(transparent_percent, 2),
            "content_bbox": {
                "left": left,
                "top": top,
                "right": right,
                "bottom": bottom,
                "width": content_w,
                "height": content_h,
            },
            "content_occupancy_percent": round(occupancy, 2),
            "edge_density_percent": round(edge_density, 2),
            "shape_read": silhouette,
        }


def _inspect_model(path: Path) -> dict[str, Any]:
    """Return geometry and GLTF metadata for local 3D model files."""
    result: dict[str, Any] = {**_file_summary(path), "model": {}}
    try:
        loaded = trimesh.load(path, force="scene", process=False)
        if isinstance(loaded, trimesh.Scene):
            geometries = list(loaded.geometry.values())
            bounds = loaded.bounds.tolist() if loaded.bounds is not None else None
            extents = (
                (loaded.bounds[1] - loaded.bounds[0]).tolist()
                if loaded.bounds is not None
                else None
            )
        else:
            geometries = [loaded]
            bounds = loaded.bounds.tolist() if loaded.bounds is not None else None
            extents = loaded.extents.tolist() if loaded.extents is not None else None

        vertices = int(sum(getattr(g, "vertices", np.array([])).shape[0] for g in geometries))
        faces = int(sum(getattr(g, "faces", np.array([])).shape[0] for g in geometries))
        result["model"].update(
            {
                "geometry_count": len(geometries),
                "vertices": vertices,
                "faces": faces,
                "bounds": bounds,
                "extents": extents,
                "approx_shape": _model_shape(extents),
            }
        )
    except Exception as exc:
        result["model"]["trimesh_error"] = str(exc)

    if path.suffix.lower() in {".gltf", ".glb"}:
        try:
            gltf = GLTF2().load(str(path))
            result["gltf"] = {
                "scenes": len(gltf.scenes or []),
                "nodes": len(gltf.nodes or []),
                "meshes": len(gltf.meshes or []),
                "materials": len(gltf.materials or []),
                "textures": len(gltf.textures or []),
                "images": len(gltf.images or []),
                "animations": len(gltf.animations or []),
            }
        except Exception as exc:
            result["gltf"] = {"error": str(exc)}

    return result


def _model_shape(extents: list[float] | None) -> str | None:
    if not extents or len(extents) != 3:
        return None
    x, y, z = [abs(float(v)) for v in extents]
    longest = max(x, y, z)
    shortest = min(v for v in (x, y, z) if v > 0) if any(v > 0 for v in (x, y, z)) else 0
    if shortest == 0:
        return "flat-or-degenerate"
    ratio = longest / shortest
    if ratio < 1.35:
        return "compact"
    if y == longest:
        return "tall"
    if z == shortest:
        return "flat"
    return "elongated"


def _archive_manifest(path: Path, limit: int = 200) -> dict[str, Any]:
    suffix = _extension_from_url(path.name)
    names: list[str] = []
    if suffix == ".zip":
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
    elif suffix in {".tar", ".tgz", ".tar.gz", ".tar.bz2", ".tar.xz"}:
        with tarfile.open(path) as tf:
            names = tf.getnames()
    elif suffix == ".7z":
        with py7zr.SevenZipFile(path, mode="r") as zf:
            names = zf.getnames()
    counts = Counter(_kind_for_url(name) for name in names)
    return {
        **_file_summary(path),
        "entries_total": len(names),
        "entry_kind_counts": dict(counts),
        "entries_sample": names[:limit],
    }


def _is_suspicious_member(name: str) -> bool:
    return Path(name).suffix.lower() in SUSPICIOUS_EXTS


def _discover_links(page_url: str, html_text: str) -> list[AssetLink]:
    """Find likely asset links on arbitrary HTML pages."""
    soup = BeautifulSoup(html_text, "lxml")
    links: list[AssetLink] = []
    seen: set[str] = set()

    for tag in soup.find_all(["a", "img", "source"]):
        attr = "href" if tag.name == "a" else "src"
        raw = tag.get(attr)
        if not raw:
            continue
        url = urljoin(page_url, raw)
        if url in seen or url.startswith("javascript:") or url.startswith("mailto:"):
            continue
        seen.add(url)
        title = " ".join(tag.get_text(" ", strip=True).split())
        if not title:
            title = tag.get("alt") or tag.get("title") or Path(urlparse(url).path).name
        kind = _kind_for_url(url, title)
        if kind == "page" and "/content/" not in url:
            continue
        links.append(
            AssetLink(
                title=title[:160],
                url=url,
                kind=kind,
                extension=_extension_from_url(url),
                source=page_url,
            )
        )
    return links


@mcp.tool()
def library_info() -> dict[str, Any]:
    """Return ASSETMCP storage locations and active limits."""
    _ensure_roots()
    return {
        "name": "ASSETMCP",
        "library_root": str(LIBRARY_ROOT),
        "preview_root": str(PREVIEW_ROOT),
        "max_download_mb": MAX_DOWNLOAD_MB,
        "supported_image_extensions": sorted(IMAGE_EXTS),
        "supported_model_extensions": sorted(MODEL_EXTS),
        "supported_archive_extensions": sorted(ARCHIVE_EXTS),
        "providers": sorted(PROVIDERS.keys()),
        "manifest_filename": MANIFEST_FILENAME,
        "credits_filename": CREDITS_FILENAME,
    }


@mcp.tool()
async def search_assets(
    query: str,
    sources: list[str] | None = None,
    max_results_per_source: int = 8,
    license_policy: str = "safe",
) -> dict[str, Any]:
    """Search normalized asset providers with license-safety metadata."""
    requested = {item.lower() for item in sources} if sources else None
    provider_sources = [item for item in sources or [] if item.lower() != "local"] if sources else None
    payload = await search_providers(
        query,
        PROVIDERS,
        source_names=provider_sources,
        max_results_per_source=max_results_per_source,
    )
    if requested is None or "local" in requested:
        local = find_local_assets(query=query, max_results=max_results_per_source)
        local_results = []
        for item in local.get("results", []):
            asset = AssetResult(
                id=f"local:{item['path']}",
                title=item["name"],
                source_name="Local",
                source_url=item["path"],
                author="local user",
                license="local user-provided",
                attribution_required=False,
                asset_type=item.get("asset_kind"),
                tags=[item.get("extension", "").lstrip(".")],
                preview_image_url=item["path"] if item.get("asset_kind") == "image" else None,
                download_url=None,
                file_formats=[item.get("extension", "").lstrip(".")],
                confidence_score=0.95,
                extra={"license_status": "allowed_local_user_provided", "license_allowed": True},
            )
            local_results.append(asset.to_dict())
        payload["by_source"]["local"] = {"results": local_results}
        payload["sources"].append("local")
        payload["results"].extend(local_results)
    if license_policy == "safe":
        payload["results"] = [
            item for item in payload["results"]
            if item.get("extra", {}).get("license_allowed")
        ]
    payload["license_policy"] = license_policy
    return payload


@mcp.tool()
async def get_asset_details(asset: dict[str, Any] | None = None, asset_url: str | None = None) -> dict[str, Any]:
    """Return normalized details, license decision, and discoverable downloads for an asset."""
    if not asset and not asset_url:
        return _error("missing_asset", "Provide either an asset record or asset_url.")
    normalized = normalize_asset_result(asset or {"url": asset_url, "source": "Direct URL"})
    license_check = check_asset_license(normalized)
    discover: dict[str, Any] | None = None
    if normalized.source_url:
        try:
            discover = await discover_page_assets(normalized.source_url, max_results=50)
        except Exception as exc:
            discover = {"error": str(exc)}
    return _ok(
        asset=normalized.to_dict(),
        license_check=license_check.to_dict(),
        discovered_assets=discover,
    )


@mcp.tool()
def suggest_asset_plan(
    game_description: str,
    style: str = "cc0",
    target_engine: str = "html",
) -> dict[str, Any]:
    """Suggest practical asset searches and project folders for a game idea."""
    terms = []
    lowered = game_description.lower()
    if "top" in lowered or "roguelike" in lowered:
        terms.extend(["top-down tileset", "pixel character", "dungeon props"])
    if "horror" in lowered:
        terms.extend(["dark ambience audio", "horror props", "stone floor texture"])
    if "3d" in lowered or target_engine.lower() in {"three", "godot"}:
        terms.extend(["low poly environment", "low poly character", "cc0 sound effects"])
    if not terms:
        terms = ["player sprite", "enemy sprite", "environment tileset", "ui sound effects"]
    return _ok(
        game_description=game_description,
        target_engine=target_engine,
        preferred_license=style,
        searches=[{"query": term, "sources": ["kenney", "ambientcg", "opengameart", "polyhaven"]} for term in dict.fromkeys(terms)],
        folders=["assets/images", "assets/audio", "assets/models", "assets/ui"],
        warnings=["Review non-CC0 licenses before download/import."],
    )


@mcp.tool()
def create_asset_manifest(
    project_path: str | None = None,
    include_library_index: bool = True,
) -> dict[str, Any]:
    """Create or refresh ASSET_MANIFEST.json for a project or the asset library."""
    root = Path(project_path).expanduser().resolve() if project_path else LIBRARY_ROOT
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = _manifest_path(root)
    manifest = load_manifest(manifest_path)
    if include_library_index:
        files = []
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.name not in {MANIFEST_FILENAME, CREDITS_FILENAME}:
                files.append(
                    {
                        "path": str(path.relative_to(root)),
                        "size_bytes": path.stat().st_size,
                        "asset_kind": _kind_for_url(path.name),
                        "sha256": _hash_file(path),
                    }
                )
        manifest["scanned_files"] = files
    write_manifest(manifest_path, manifest)
    return _ok(manifest_path=str(manifest_path), asset_count=len(manifest.get("assets", [])))


@mcp.tool()
def generate_credits(project_path: str | None = None) -> dict[str, Any]:
    """Generate CREDITS.md from ASSET_MANIFEST.json."""
    root = Path(project_path).expanduser().resolve() if project_path else LIBRARY_ROOT
    manifest_path = _manifest_path(root)
    credits_path = _credits_path(root)
    manifest = load_manifest(manifest_path)
    text = generate_credits_text(manifest)
    credits_path.parent.mkdir(parents=True, exist_ok=True)
    credits_path.write_text(text, encoding="utf-8")
    return _ok(credits_path=str(credits_path), credited_assets=text.count("\n- "))


@mcp.tool()
def audit_project_assets(project_path: str | None = None) -> dict[str, Any]:
    """Audit manifests, credits, suspicious files, and unclear-license entries."""
    root = Path(project_path).expanduser().resolve() if project_path else LIBRARY_ROOT
    manifest_path = _manifest_path(root)
    manifest = load_manifest(manifest_path)
    unclear = [
        item for item in manifest.get("assets", [])
        if str(item.get("license_status", "")).startswith("blocked")
    ]
    missing_files = []
    for item in manifest.get("assets", []):
        for file_path in item.get("files", []):
            path = Path(file_path)
            if not path.is_absolute():
                path = root / path
            if not path.exists():
                missing_files.append({"asset_id": item.get("id"), "file": file_path})
    suspicious = scan_suspicious_files(root) if root.exists() else []
    return _ok(
        project_path=str(root),
        manifest_path=str(manifest_path),
        manifest_exists=manifest_path.exists(),
        credits_exists=_credits_path(root).exists(),
        blocked_or_unclear_license_count=len(unclear),
        blocked_or_unclear_assets=unclear,
        missing_files=missing_files,
        suspicious_files=suspicious,
        passed=not unclear and not missing_files and not suspicious,
    )


@mcp.tool()
async def find_replacement_assets(
    query: str,
    blocked_asset: dict[str, Any] | None = None,
    max_results: int = 8,
) -> dict[str, Any]:
    """Find CC0/public-domain replacement candidates for a blocked or unclear asset."""
    search_query = query or (blocked_asset or {}).get("title") or "game asset"
    results = await search_assets(
        search_query,
        sources=["kenney", "ambientcg", "polyhaven", "quaternius"],
        max_results_per_source=max_results,
        license_policy="safe",
    )
    return _ok(blocked_asset=blocked_asset, replacements=results["results"])


@mcp.tool()
def import_asset_to_project(
    asset_path: str,
    project_path: str,
    asset: dict[str, Any] | None = None,
    target_subfolder: str = "assets/imported",
    local_user_provided: bool = False,
) -> dict[str, Any]:
    """Copy a local asset into a project and track it in manifest/credits."""
    source = _resolve_read_path(asset_path)
    if not source.exists() or not source.is_file():
        return _error("missing_file", f"Asset file not found: {source}")
    project = Path(project_path).expanduser().resolve()
    destination_dir = project / target_subfolder
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / source.name
    if destination.exists():
        destination = destination_dir / f"{source.stem}-{hashlib.sha1(str(source).encode()).hexdigest()[:8]}{source.suffix}"
    copy2(source, destination)
    normalized = normalize_asset_result(asset or {
        "id": f"local:{source.name}",
        "title": source.name,
        "source": "Local",
        "url": str(source),
        "license": "local user-provided" if local_user_provided else None,
    })
    license_check = check_asset_license(normalized, local_user_provided=local_user_provided)
    if not license_check.allowed:
        destination.unlink(missing_ok=True)
        return _error("blocked_license", "Asset import blocked by license policy.", license_check=license_check.to_dict())
    entry = upsert_manifest_entry(
        _manifest_path(project),
        normalized,
        license_check,
        [str(destination.relative_to(project))],
        checksum_sha256=_hash_file(destination),
    )
    credits = generate_credits(project_path=str(project))
    return _ok(imported_path=str(destination), manifest_entry=entry, credits=credits)


@mcp.tool()
def create_game_project(
    project_path: str,
    engine: str = "html",
    title: str = "ASSETMCP Prototype",
) -> dict[str, Any]:
    """Create a playable prototype for HTML Canvas, Phaser, Three.js, or Godot 4."""
    project = Path(project_path).expanduser().resolve()
    return _ok(**scaffold_game_project(project, engine, title))


@mcp.tool()
def generate_game_from_assets(
    project_path: str,
    engine: str,
    title: str,
    asset_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Scaffold a game project and import selected local assets."""
    scaffold = scaffold_game_project(Path(project_path).expanduser().resolve(), engine, title)
    imported = []
    for asset_path in asset_paths or []:
        imported.append(
            import_asset_to_project(
                asset_path,
                scaffold["project_path"],
                local_user_provided=True,
            )
        )
    return _ok(scaffold=scaffold, imported=imported)


@mcp.tool()
def convert_assets(
    paths: list[str],
    output_format: str = "png",
    output_folder: str | None = None,
) -> dict[str, Any]:
    """Convert image assets to png, webp, jpg, or jpeg. Other assets return warnings."""
    folder = _subfolder_path(output_folder or f"converted-{output_format}")
    converted: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    for item in paths:
        path = _resolve_read_path(item)
        if path.suffix.lower() not in IMAGE_EXTS:
            warnings.append({"path": str(path), "warning": "Only image conversion is currently supported."})
            continue
        out = _safe_output_path(folder, f"{path.stem}.{output_format.lower().lstrip('.')}")
        with Image.open(path) as img:
            image = img.convert("RGB") if output_format.lower() in {"jpg", "jpeg"} else img.convert("RGBA")
            image.save(out)
        converted.append({"source": str(path), "output": str(out), "sha256": _hash_file(out)})
    return _ok(converted=converted, warnings=warnings)


@mcp.tool()
async def search_opengameart(
    query: str,
    max_results: int = 20,
    art_type: str | None = None,
) -> dict[str, Any]:
    """Search OpenGameArt and return asset pages with thumbnails when available."""
    type_map = {
        "2d": "9",
        "3d": "10",
        "audio": "12",
        "texture": "14",
        "concept": "15",
        "font": "16",
    }
    url = f"https://opengameart.org/art-search-advanced?keys={quote_plus(query)}"
    if art_type and art_type.lower() in type_map:
        url += f"&field_art_type_tid%5B%5D={type_map[art_type.lower()]}"

    body = await _fetch_html(url)
    soup = BeautifulSoup(body, "lxml")
    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if "/content/" not in href:
            continue
        page_url = urljoin("https://opengameart.org", href)
        if not _is_opengameart_asset_page(page_url):
            continue
        if page_url in seen:
            continue
        seen.add(page_url)
        card = anchor.find_parent(["article", "div", "li"]) or anchor.parent
        text = " ".join(card.get_text(" ", strip=True).split()) if card else ""
        img = card.find("img") if card else None
        thumb = urljoin(page_url, img["src"]) if img and img.get("src") else None
        results.append(
            {
                "title": anchor.get_text(" ", strip=True)[:160] or page_url.rsplit("/", 1)[-1],
                "url": page_url,
                "thumbnail": thumb,
                "summary_text": text[:500],
            }
        )
        if len(results) >= max_results:
            break

    return {"query": query, "source": "OpenGameArt", "search_url": url, "results": results}


@mcp.tool()
async def search_openverse_images(
    query: str,
    max_results: int = 20,
    license_filter: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """Search Openverse for openly licensed image assets and references."""
    params: dict[str, Any] = {"q": query, "page_size": max(1, min(max_results, 50))}
    if license_filter:
        params["license"] = license_filter
    if source:
        params["source"] = source
    data = await _fetch_json("https://api.openverse.org/v1/images/", params=params)
    results: list[dict[str, Any]] = []
    for item in data.get("results", [])[:max_results]:
        image_url = item.get("url")
        landing_url = item.get("foreign_landing_url") or image_url
        if not image_url:
            continue
        results.append(
            _source_result(
                "Openverse",
                item.get("title") or image_url.rsplit("/", 1)[-1],
                landing_url,
                license=item.get("license_url") or item.get("license"),
                thumbnail=item.get("thumbnail") or image_url,
                asset_type="image",
                downloads=[
                    {
                        "title": "source image",
                        "url": image_url,
                        "kind": "image",
                        "extension": _extension_from_url(image_url),
                    }
                ],
                description=item.get("creator") or item.get("provider"),
                extra={
                    "creator": item.get("creator"),
                    "creator_url": item.get("creator_url"),
                    "provider": item.get("provider"),
                },
            )
        )
    return {
        "query": query,
        "source": "Openverse",
        "result_count": data.get("result_count"),
        "results": results,
    }


@mcp.tool()
async def search_ambientcg_assets(
    query: str,
    max_results: int = 20,
    asset_type: str | None = None,
    sort: str = "popular",
) -> dict[str, Any]:
    """Search ambientCG for CC0 PBR materials, atlases, HDRIs, images, and 3D models."""
    include = "type,title,url,downloads,thumbnails,tags,dimensions,shortDescription"
    params = {
        "q": query,
        "limit": max(1, min(max_results, 100)),
        "sort": sort,
        "include": include,
    }
    if asset_type:
        params["type"] = asset_type
    data = await _fetch_json("https://ambientCG.com/api/v3/assets", params=params)
    results: list[dict[str, Any]] = []
    for item in data.get("assets", [])[:max_results]:
        downloads = [
            {
                "title": dl.get("attributes") or dl.get("url", "").rsplit("=", 1)[-1],
                "url": dl.get("url"),
                "kind": "archive" if dl.get("extension") == "zip" else "file",
                "extension": f".{dl.get('extension')}" if dl.get("extension") else "",
                "size_bytes": dl.get("size"),
            }
            for dl in item.get("downloads", [])
            if dl.get("url")
        ]
        thumbs = item.get("thumbnails") or []
        thumbnail = None
        if isinstance(thumbs, list) and thumbs:
            thumbnail = thumbs[0].get("url") if isinstance(thumbs[0], dict) else str(thumbs[0])
        results.append(
            _source_result(
                "ambientCG",
                item.get("title") or item.get("id") or "",
                item.get("url") or f"https://ambientcg.com/a/{item.get('id')}",
                license="CC0",
                thumbnail=thumbnail,
                asset_type=item.get("type"),
                downloads=downloads,
                description=item.get("shortDescription"),
                extra={
                    "id": item.get("id"),
                    "tags": item.get("tags"),
                    "dimensions": item.get("dimensions"),
                },
            )
        )
    return {
        "query": query,
        "source": "ambientCG",
        "total_results": data.get("totalResults"),
        "results": results,
    }


@mcp.tool()
async def download_ambientcg_asset(
    asset_id: str,
    attributes_preference: str = "1K-JPG",
    subfolder: str | None = None,
    auto_extract: bool = False,
) -> dict[str, Any]:
    """Download one ambientCG asset archive by id, preferring a size/format such as 1K-JPG."""
    data = await _fetch_json(
        "https://ambientCG.com/api/v3/assets",
        params={
            "id": asset_id,
            "limit": 1,
            "include": "title,downloads,type,url",
        },
    )
    assets = data.get("assets", [])
    if not assets:
        raise ValueError(f"ambientCG asset not found: {asset_id}")
    asset = assets[0]
    downloads = [dl for dl in asset.get("downloads", []) if dl.get("url")]
    if not downloads:
        raise ValueError(f"ambientCG asset has no downloads: {asset_id}")
    preference = attributes_preference.lower()
    matching = [dl for dl in downloads if preference in str(dl.get("attributes", "")).lower()]
    chosen = min(matching or downloads, key=lambda dl: dl.get("size") or 10**18)
    folder = _subfolder_path(subfolder or f"ambientcg-{_slug(asset_id)}")
    download_result = await download_asset(
        chosen["url"],
        subfolder=str(folder.relative_to(LIBRARY_ROOT)),
        title=asset.get("title") or asset_id,
        source_name="ambientCG",
        source_url=asset.get("url") or f"https://ambientcg.com/a/{asset_id}",
        author="ambientCG",
        license="CC0",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
    )
    if not download_result.get("ok"):
        return download_result
    downloaded = download_result["downloaded"]
    result: dict[str, Any] = {
        "asset_id": asset_id,
        "title": asset.get("title"),
        "chosen_download": chosen,
        "downloaded": downloaded,
        "manifest_entry": download_result.get("manifest_entry"),
    }
    if auto_extract and downloaded["extension"] in ARCHIVE_EXTS:
        result["extracted"] = extract_archive(downloaded["path"], str(folder.relative_to(LIBRARY_ROOT)))
    return result


@mcp.tool()
async def search_kenney_assets(
    query: str = "",
    max_results: int = 20,
    category: str | None = None,
    max_pages: int = 5,
) -> dict[str, Any]:
    """Search Kenney's CC0 asset catalog by crawling catalog pages."""
    suffix = KENNEY_CATEGORY_MAP.get(category.lower(), "") if category else ""
    base = f"https://kenney.nl/assets/{suffix}" if suffix else "https://kenney.nl/assets"
    urls = [base]
    if not suffix:
        urls.extend(f"https://kenney.nl/assets/page:{page}" for page in range(2, max(2, max_pages + 1)))
    needle = query.lower().strip()
    seen: set[str] = set()
    results: list[dict[str, Any]] = []

    for page_url in urls:
        body = await _fetch_html(page_url)
        soup = BeautifulSoup(body, "lxml")
        for anchor in soup.find_all("a", href=True):
            href = urljoin("https://kenney.nl", anchor["href"])
            parsed = urlparse(href)
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) != 2 or parts[0] != "assets":
                continue
            slug = parts[1]
            if ":" in slug or slug in seen:
                continue
            seen.add(slug)
            card = anchor.find_parent(["article", "div", "li"]) or anchor.parent
            title = _trim_text(anchor.get_text(" ", strip=True), 160)
            text = _trim_text(card.get_text(" ", strip=True) if card else title, 500)
            if not title:
                title = slug.replace("-", " ").title()
            haystack = f"{title} {text} {slug}".lower()
            if needle and needle not in haystack:
                continue
            img = card.find("img") if card else None
            thumbnail = urljoin(href, img["src"]) if img and img.get("src") else None
            results.append(
                _source_result(
                    "Kenney",
                    title,
                    href,
                    license="CC0",
                    thumbnail=thumbnail,
                    asset_type=category,
                    description=text,
                )
            )
            if len(results) >= max_results:
                return {
                    "query": query,
                    "source": "Kenney",
                    "catalog_urls": urls,
                    "results": results,
                }
    return {"query": query, "source": "Kenney", "catalog_urls": urls, "results": results}


@mcp.tool()
async def download_kenney_asset(
    asset_url: str,
    subfolder: str | None = None,
    auto_extract: bool = False,
) -> dict[str, Any]:
    """Download the primary archive from a Kenney asset page."""
    body = await _fetch_html(asset_url)
    metadata = _parse_kenney_asset_page(asset_url, body)
    archives = [dl for dl in metadata["downloads"] if dl.get("kind") == "archive"]
    if not archives:
        raise ValueError(f"No downloadable archive found on Kenney page: {asset_url}")
    chosen = archives[0]
    folder = _subfolder_path(subfolder or f"kenney-{_slug(metadata['title'])}")
    download_result = await download_asset(
        chosen["url"],
        subfolder=str(folder.relative_to(LIBRARY_ROOT)),
        title=metadata["title"],
        source_name="Kenney",
        source_url=asset_url,
        author="Kenney",
        license=metadata.get("license") or "CC0",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
    )
    if not download_result.get("ok"):
        return download_result
    downloaded = download_result["downloaded"]
    result: dict[str, Any] = {
        "metadata": metadata,
        "chosen_download": chosen,
        "downloaded": downloaded,
        "manifest_entry": download_result.get("manifest_entry"),
    }
    if auto_extract:
        result["extracted"] = extract_archive(downloaded["path"], str(folder.relative_to(LIBRARY_ROOT)))
    return result


@mcp.tool()
async def search_itch_assets(
    query: str,
    max_results: int = 20,
    free_only: bool = True,
) -> dict[str, Any]:
    """Search itch.io game asset pages. Downloads usually require the asset page flow."""
    url = "https://itch.io/game-assets/free" if free_only else "https://itch.io/game-assets"
    body = await _fetch_html(f"{url}?q={quote_plus(query)}")
    soup = BeautifulSoup(body, "lxml")
    seen: set[str] = set()
    results: list[dict[str, Any]] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        parsed = urlparse(href)
        if not parsed.netloc.endswith("itch.io"):
            continue
        if parsed.netloc == "itch.io" or href in seen:
            continue
        seen.add(href)
        title = _trim_text(anchor.get_text(" ", strip=True), 160)
        if not title or title.lower() in {"gif", "image"}:
            continue
        card = anchor.find_parent(["article", "div", "li"]) or anchor.parent
        text = _trim_text(card.get_text(" ", strip=True) if card else title, 500)
        img = card.find("img") if card else None
        thumbnail = urljoin(href, img["src"]) if img and img.get("src") else None
        results.append(
            _source_result(
                "itch.io",
                title,
                href,
                license="See asset page",
                thumbnail=thumbnail,
                asset_type="game-assets",
                description=text,
            )
        )
        if len(results) >= max_results:
            break
    return {"query": query, "source": "itch.io", "search_url": f"{url}?q={quote_plus(query)}", "results": results}


@mcp.tool()
async def search_asset_sources(
    query: str,
    sources: list[str] | None = None,
    max_results_per_source: int = 8,
) -> dict[str, Any]:
    """Compatibility wrapper around search_assets."""
    return await search_assets(
        query,
        sources=sources,
        max_results_per_source=max_results_per_source,
        license_policy="all",
    )


@mcp.tool()
async def discover_page_assets(url: str, max_results: int = 100) -> dict[str, Any]:
    """Find downloadable images, models, audio, archives, and asset pages linked from any web page."""
    body = await _fetch_html(url)
    links = _discover_links(url, body)
    downloadable = [
        item for item in links if item.kind in {"image", "model", "audio", "archive", "file", "possible_texture"}
    ]
    pages = [item for item in links if item.kind == "page"]
    return {
        "url": url,
        "downloadable_count": len(downloadable),
        "page_count": len(pages),
        "downloadables": [item.as_dict() for item in downloadable[:max_results]],
        "pages": [item.as_dict() for item in pages[:max_results]],
    }


@mcp.tool()
async def download_asset(
    url: str,
    subfolder: str | None = None,
    filename: str | None = None,
    title: str | None = None,
    source_name: str | None = None,
    source_url: str | None = None,
    author: str | None = None,
    license: str | None = None,
    license_url: str | None = None,
    local_user_provided: bool = False,
) -> dict[str, Any]:
    """Download a URL into the asset library after strict license validation."""
    asset = _asset_from_download_args(
        url=url,
        title=title,
        source_name=source_name,
        source_url=source_url,
        author=author,
        license=license,
        license_url=license_url,
        local_user_provided=local_user_provided,
    )
    license_check = check_asset_license(asset, local_user_provided=local_user_provided)
    if not license_check.allowed:
        return _error(
            "blocked_license",
            "Download blocked by license policy.",
            asset=asset.to_dict(),
            license_check=license_check.to_dict(),
        )
    folder = _subfolder_path(subfolder)
    try:
        downloaded = await _download(url, folder, filename)
        entry = upsert_manifest_entry(
            _manifest_path(),
            asset,
            license_check,
            [downloaded["path"]],
            checksum_sha256=downloaded.get("sha256"),
        )
        credits = generate_credits()
        suspicious = scan_suspicious_files(Path(downloaded["path"]).parent)
        return _ok(
            downloaded=downloaded,
            manifest_entry=entry,
            credits=credits,
            suspicious_files=suspicious,
        )
    except Exception as exc:
        return _error("download_failed", str(exc), url=url)


@mcp.tool()
async def download_page_assets(
    page_url: str,
    kinds: list[str] | None = None,
    subfolder: str | None = None,
    max_files: int = 10,
) -> dict[str, Any]:
    """Discover a page and download matching asset links only when license metadata is supplied."""
    wanted = set(kinds or ["archive", "image", "model"])
    body = await _fetch_html(page_url)
    links = [item for item in _discover_links(page_url, body) if item.kind in wanted]
    folder = _subfolder_path(subfolder or _slug(urlparse(page_url).path.rsplit("/", 1)[-1], "page-assets"))
    downloaded: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for item in links[:max_files]:
        result = await download_asset(
            item.url,
            subfolder=str(folder.relative_to(LIBRARY_ROOT)),
            title=item.title,
            source_name="Discovered Page",
            source_url=page_url,
        )
        if result.get("ok"):
            downloaded.append(result["downloaded"])
        else:
            errors.append({"url": item.url, "error": result.get("error", {}).get("message", "blocked")})
    return {
        "page_url": page_url,
        "matched": len(links),
        "downloaded": downloaded,
        "errors": errors,
        "folder": str(folder),
    }


@mcp.tool()
def extract_archive(
    archive_path: str,
    destination_folder: str | None = None,
    allow_suspicious: bool = False,
) -> dict[str, Any]:
    """Extract archives inside the asset library, blocking executable/script members by default."""
    _ensure_roots()
    archive = _resolve_read_path(archive_path)
    if not archive.exists():
        raise FileNotFoundError(str(archive))
    suffix = _extension_from_url(archive.name)
    destination = _subfolder_path(destination_folder or archive.stem)
    destination.mkdir(parents=True, exist_ok=True)

    if suffix == ".zip":
        files = _extract_zip(archive, destination, allow_suspicious=allow_suspicious)
    elif suffix in {".tar", ".tgz", ".tar.gz", ".tar.bz2", ".tar.xz"}:
        files = _extract_tar(archive, destination, allow_suspicious=allow_suspicious)
    elif suffix == ".7z":
        files = _extract_7z(archive, destination, allow_suspicious=allow_suspicious)
    else:
        raise ValueError(f"Unsupported archive type: {suffix or archive.suffix}")

    kind_counts = Counter(_kind_for_url(path.name) for path in files)
    return {
        "archive": str(archive),
        "destination": str(destination),
        "files_extracted": len(files),
        "kind_counts": dict(kind_counts),
        "files_sample": [str(path) for path in files[:200]],
    }


@mcp.tool()
def list_library(
    subfolder: str | None = None,
    kinds: list[str] | None = None,
    max_results: int = 200,
) -> dict[str, Any]:
    """List files in the asset library with basic type categorization."""
    _ensure_roots()
    root = _subfolder_path(subfolder) if subfolder else LIBRARY_ROOT
    wanted = set(kinds) if kinds else None
    files: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        summary = _file_summary(path)
        if wanted and summary["asset_kind"] not in wanted:
            continue
        files.append(summary)
        if len(files) >= max_results:
            break
    counts = Counter(item["asset_kind"] for item in files)
    return {"root": str(root), "count": len(files), "kind_counts": dict(counts), "files": files}


@mcp.tool()
def inspect_asset(path: str) -> dict[str, Any]:
    """Inspect an asset file, including image silhouette/color awareness and 3D model geometry."""
    target = _resolve_read_path(path)
    if not target.exists():
        raise FileNotFoundError(str(target))
    ext = _extension_from_url(target.name)
    if ext in IMAGE_EXTS:
        return _inspect_image(target)
    if ext in MODEL_EXTS:
        return _inspect_model(target)
    if ext in ARCHIVE_EXTS:
        return _archive_manifest(target)
    return _file_summary(target)


@mcp.tool()
def make_image_contact_sheet(
    folder: str | None = None,
    paths: list[str] | None = None,
    output_name: str | None = None,
    max_images: int = 80,
    thumb_size: int = 160,
) -> dict[str, Any]:
    """Create a PNG contact sheet for quickly viewing many downloaded images."""
    _ensure_roots()
    image_paths: list[Path] = []
    if paths:
        image_paths = [_resolve_read_path(item) for item in paths]
    else:
        root = _subfolder_path(folder) if folder else LIBRARY_ROOT
        image_paths = [p for p in sorted(root.rglob("*")) if p.suffix.lower() in IMAGE_EXTS]

    image_paths = [p for p in image_paths if p.exists() and p.suffix.lower() in IMAGE_EXTS][:max_images]
    if not image_paths:
        raise ValueError("No images found for contact sheet")

    columns = max(1, min(5, math.ceil(math.sqrt(len(image_paths)))))
    label_h = 34
    cell_w = thumb_size
    cell_h = thumb_size + label_h
    rows = math.ceil(len(image_paths) / columns)
    sheet = Image.new("RGB", (columns * cell_w, rows * cell_h), "#202124")
    draw = ImageDraw.Draw(sheet)

    for index, path in enumerate(image_paths):
        col = index % columns
        row = index // columns
        x = col * cell_w
        y = row * cell_h
        with Image.open(path) as img:
            img = ImageOps.contain(img.convert("RGBA"), (thumb_size - 16, thumb_size - 16))
            checker = Image.new("RGBA", (thumb_size - 16, thumb_size - 16), "#333333")
            px = x + 8 + ((thumb_size - 16) - img.width) // 2
            py = y + 8 + ((thumb_size - 16) - img.height) // 2
            sheet.paste(checker.convert("RGB"), (x + 8, y + 8))
            sheet.paste(img.convert("RGB"), (px, py), img)
        label = path.name[:28]
        draw.text((x + 8, y + thumb_size + 5), label, fill="#f1f3f4")

    output = (PREVIEW_ROOT / _slug(output_name or "contact-sheet.png", "contact-sheet.png")).resolve()
    if output.suffix.lower() != ".png":
        output = output.with_suffix(".png")
    if not _is_inside(output, PREVIEW_ROOT):
        raise ValueError("Preview output must stay inside preview folder")
    sheet.save(output)
    return {
        "output_path": str(output),
        "image_count": len(image_paths),
        "images": [str(p) for p in image_paths],
    }


@mcp.tool()
def create_3d_viewer(path: str, output_name: str | None = None) -> dict[str, Any]:
    """Create a local HTML viewer for GLB/GLTF models using the browser's WebGL renderer."""
    _ensure_roots()
    target = _resolve_read_path(path)
    if target.suffix.lower() not in {".glb", ".gltf"}:
        raise ValueError("create_3d_viewer currently supports .glb and .gltf files")
    rel_model = os.path.relpath(target, PREVIEW_ROOT).replace("\\", "/")
    output = (PREVIEW_ROOT / _slug(output_name or f"{target.stem}-viewer.html", "model-viewer.html")).resolve()
    if output.suffix.lower() != ".html":
        output = output.with_suffix(".html")
    if not _is_inside(output, PREVIEW_ROOT):
        raise ValueError("Viewer output must stay inside preview folder")

    title = html.escape(target.name)
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <script type="module" src="https://ajax.googleapis.com/ajax/libs/model-viewer/4.0.0/model-viewer.min.js"></script>
  <style>
    html, body {{ margin: 0; height: 100%; background: #111; color: #f3f3f3; font-family: Arial, sans-serif; }}
    model-viewer {{ width: 100vw; height: 100vh; background: radial-gradient(circle at 50% 45%, #333, #111 60%); }}
    .label {{ position: fixed; left: 16px; bottom: 16px; padding: 8px 10px; background: rgba(0,0,0,.55); border-radius: 6px; }}
  </style>
</head>
<body>
  <model-viewer src="{rel_model}" camera-controls auto-rotate shadow-intensity="1" exposure="1"></model-viewer>
  <div class="label">{title}</div>
</body>
</html>
"""
    output.write_text(document, encoding="utf-8")
    return {"viewer_path": str(output), "model_path": str(target)}


@mcp.tool()
def index_library(
    subfolder: str | None = None,
    inspect_files: bool = False,
    include_hashes: bool = False,
    max_files: int = 1000,
    output_name: str | None = None,
) -> dict[str, Any]:
    """Create a JSON index of the local asset library for later searching and review."""
    _ensure_roots()
    root = _subfolder_path(subfolder) if subfolder else LIBRARY_ROOT
    records: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            record = inspect_asset(str(path)) if inspect_files else _file_summary(path)
            record["relative_path"] = str(path.relative_to(LIBRARY_ROOT))
            if include_hashes:
                record["sha256"] = _hash_file(path)
            records.append(record)
        except Exception as exc:
            records.append({"path": str(path), "error": str(exc)})
        if len(records) >= max_files:
            break
    counts = Counter(item.get("asset_kind", "unknown") for item in records)
    output = _safe_preview_path(output_name or "asset-index.json", ".json")
    payload = {
        "library_root": str(LIBRARY_ROOT),
        "indexed_root": str(root),
        "count": len(records),
        "kind_counts": dict(counts),
        "files": records,
    }
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {**payload, "output_path": str(output)}


@mcp.tool()
def find_local_assets(
    query: str = "",
    kinds: list[str] | None = None,
    subfolder: str | None = None,
    max_results: int = 100,
    min_width: int | None = None,
    min_height: int | None = None,
) -> dict[str, Any]:
    """Search the local library by filename/path/kind, with optional image dimension filters."""
    _ensure_roots()
    root = _subfolder_path(subfolder) if subfolder else LIBRARY_ROOT
    wanted = {kind.lower() for kind in kinds} if kinds else None
    needle = query.lower().strip()
    results: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        summary = _file_summary(path)
        if wanted and summary["asset_kind"].lower() not in wanted:
            continue
        haystack = f"{path.name} {path.parent} {summary['asset_kind']}".lower()
        if needle and needle not in haystack:
            continue
        if (min_width or min_height) and path.suffix.lower() in IMAGE_EXTS:
            try:
                with Image.open(path) as img:
                    width, height = img.size
                if min_width and width < min_width:
                    continue
                if min_height and height < min_height:
                    continue
                summary["width"] = width
                summary["height"] = height
            except Exception:
                continue
        results.append(summary)
        if len(results) >= max_results:
            break
    return {"query": query, "root": str(root), "count": len(results), "results": results}


@mcp.tool()
def create_asset_gallery(
    folder: str | None = None,
    output_name: str | None = None,
    max_files: int = 300,
) -> dict[str, Any]:
    """Create a local HTML gallery for images and GLB/GLTF models in the asset library."""
    _ensure_roots()
    root = _subfolder_path(folder) if folder else LIBRARY_ROOT
    files = [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.suffix.lower() in (IMAGE_EXTS | {".glb", ".gltf"})
    ][:max_files]
    output = _safe_preview_path(output_name or "asset-gallery.html", ".html")
    items: list[str] = []
    for path in files:
        rel = os.path.relpath(path, output.parent).replace("\\", "/")
        name = html.escape(str(path.relative_to(LIBRARY_ROOT)))
        if path.suffix.lower() in IMAGE_EXTS:
            media = f'<img src="{rel}" alt="{name}" loading="lazy">'
        else:
            media = f'<model-viewer src="{rel}" camera-controls auto-rotate></model-viewer>'
        items.append(f'<figure>{media}<figcaption>{name}</figcaption></figure>')
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ASSETMCP Gallery</title>
  <script type="module" src="https://ajax.googleapis.com/ajax/libs/model-viewer/4.0.0/model-viewer.min.js"></script>
  <style>
    body {{ margin: 0; background: #181a1b; color: #f1f3f4; font-family: Arial, sans-serif; }}
    header {{ position: sticky; top: 0; padding: 12px 16px; background: #111; border-bottom: 1px solid #333; }}
    main {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 12px; padding: 12px; }}
    figure {{ margin: 0; background: #242728; border: 1px solid #363a3c; border-radius: 6px; overflow: hidden; }}
    img, model-viewer {{ display: block; width: 100%; height: 180px; object-fit: contain; background: #101112; }}
    figcaption {{ padding: 8px; font-size: 12px; overflow-wrap: anywhere; color: #d4d7d9; }}
  </style>
</head>
<body>
  <header>ASSETMCP Gallery: {html.escape(str(root))} ({len(files)} files)</header>
  <main>
    {''.join(items)}
  </main>
</body>
</html>
"""
    output.write_text(document, encoding="utf-8")
    return {"gallery_path": str(output), "file_count": len(files), "files": [str(p) for p in files]}


@mcp.tool()
def slice_sprite_sheet(
    path: str,
    frame_width: int,
    frame_height: int,
    output_folder: str | None = None,
    margin: int = 0,
    spacing: int = 0,
    skip_empty: bool = True,
) -> dict[str, Any]:
    """Slice a sprite sheet or tileset into individual PNG frames."""
    _ensure_roots()
    target = _resolve_read_path(path)
    if target.suffix.lower() not in IMAGE_EXTS:
        raise ValueError("slice_sprite_sheet requires an image file")
    if frame_width <= 0 or frame_height <= 0:
        raise ValueError("frame_width and frame_height must be positive")
    folder = _subfolder_path(output_folder or f"{target.stem}-frames")
    folder.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    skipped = 0
    with Image.open(target) as img:
        rgba = img.convert("RGBA")
        index = 0
        y = margin
        while y + frame_height <= rgba.height:
            x = margin
            while x + frame_width <= rgba.width:
                frame = rgba.crop((x, y, x + frame_width, y + frame_height))
                if skip_empty and frame.getchannel("A").getbbox() is None:
                    skipped += 1
                else:
                    out = _safe_output_path(folder, f"{target.stem}-{index:04d}.png")
                    frame.save(out)
                    saved.append(str(out))
                index += 1
                x += frame_width + spacing
            y += frame_height + spacing
    return {
        "source": str(target),
        "output_folder": str(folder),
        "frame_width": frame_width,
        "frame_height": frame_height,
        "frames_saved": len(saved),
        "frames_skipped": skipped,
        "files": saved,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the ASSETMCP server.")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default=os.environ.get("ASSETMCP_TRANSPORT", "stdio"),
    )
    parser.add_argument("--library", help="Override ASSETMCP asset library folder.")
    parser.add_argument("--preview-dir", help="Override ASSETMCP preview output folder.")
    args = parser.parse_args()

    global LIBRARY_ROOT, PREVIEW_ROOT
    if args.library:
        LIBRARY_ROOT = Path(args.library).expanduser().resolve()
    if args.preview_dir:
        PREVIEW_ROOT = Path(args.preview_dir).expanduser().resolve()
    _ensure_roots()
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
