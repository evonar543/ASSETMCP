"""Provider helpers shared by source integrations."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from assetmcp.schemas import AssetResult
from assetmcp.services.license_checker import check_asset_license

USER_AGENT = "ASSETMCP/0.3 (+https://modelcontextprotocol.io; game asset forge)"


async def fetch_html(url: str) -> str:
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
        timeout=httpx.Timeout(30.0, connect=10.0),
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


async def fetch_json(url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=httpx.Timeout(30.0, connect=10.0),
    ) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response.json()


def slug(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    return re.sub(r"-+", "-", value).strip("-._") or "asset"


def soup(text: str) -> BeautifulSoup:
    return BeautifulSoup(text, "lxml")


def absolute(base: str, url: str | None) -> str | None:
    if not url:
        return None
    return urljoin(base, url)


def finalize(asset: AssetResult) -> AssetResult:
    """Attach license warnings/status to a provider result."""
    check = check_asset_license(asset)
    asset.warnings = sorted(set(asset.warnings + check.warnings + check.reasons))
    asset.extra["license_status"] = check.status
    asset.extra["license_allowed"] = check.allowed
    return asset


def file_format_from_url(url: str | None) -> list[str]:
    if not url:
        return []
    path = urlparse(url).path.lower()
    if "." not in path:
        return []
    return [path.rsplit(".", 1)[-1]]
