"""Built-in source providers for ASSETMCP."""

from __future__ import annotations

from urllib.parse import quote_plus, urljoin, urlparse

from assetmcp.providers.core import absolute, fetch_html, fetch_json, file_format_from_url, finalize, slug, soup
from assetmcp.schemas import AssetResult


async def search_kenney(query: str, max_results: int = 8) -> list[AssetResult]:
    urls = ["https://kenney.nl/assets"] + [f"https://kenney.nl/assets/page:{page}" for page in range(2, 5)]
    results: list[AssetResult] = []
    seen: set[str] = set()
    needle = query.lower().strip()
    for page_url in urls:
        doc = soup(await fetch_html(page_url))
        for anchor in doc.find_all("a", href=True):
            href = urljoin("https://kenney.nl", anchor["href"])
            parts = [part for part in urlparse(href).path.split("/") if part]
            if len(parts) != 2 or parts[0] != "assets" or ":" in parts[1] or href in seen:
                continue
            seen.add(href)
            card = anchor.find_parent(["article", "div", "li"]) or anchor.parent
            title = " ".join(anchor.get_text(" ", strip=True).split()) or parts[1].replace("-", " ").title()
            text = " ".join(card.get_text(" ", strip=True).split()) if card else title
            if needle and needle not in f"{title} {text} {parts[1]}".lower():
                continue
            img = card.find("img") if card else None
            results.append(
                finalize(
                    AssetResult(
                        id=f"kenney:{parts[1]}",
                        title=title,
                        source_name="Kenney",
                        source_url=href,
                        author="Kenney",
                        license="CC0",
                        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
                        attribution_required=False,
                        asset_type="game-asset-pack",
                        tags=[tag for tag in [parts[1], "cc0"] if tag],
                        preview_image_url=absolute(href, img.get("src") if img else None),
                        confidence_score=0.9,
                    )
                )
            )
            if len(results) >= max_results:
                return results
    return results


async def search_opengameart(query: str, max_results: int = 8) -> list[AssetResult]:
    page_url = f"https://opengameart.org/art-search-advanced?keys={quote_plus(query)}"
    doc = soup(await fetch_html(page_url))
    results: list[AssetResult] = []
    seen: set[str] = set()
    for anchor in doc.find_all("a", href=True):
        href = urljoin("https://opengameart.org", anchor["href"])
        parts = [part for part in urlparse(href).path.split("/") if part]
        if len(parts) != 2 or parts[0] != "content" or href in seen:
            continue
        if parts[1] in {"faq", "about", "contact", "privacy-policy", "terms-of-use"}:
            continue
        seen.add(href)
        card = anchor.find_parent(["article", "div", "li"]) or anchor.parent
        text = " ".join(card.get_text(" ", strip=True).split()) if card else ""
        img = card.find("img") if card else None
        warnings = ["OpenGameArt license varies per asset page; details tool should verify before download."]
        results.append(
            finalize(
                AssetResult(
                    id=f"opengameart:{parts[1]}",
                    title=anchor.get_text(" ", strip=True) or parts[1].replace("-", " ").title(),
                    source_name="OpenGameArt",
                    source_url=href,
                    license=None,
                    asset_type="game-asset",
                    tags=[query],
                    preview_image_url=absolute(href, img.get("src") if img else None),
                    confidence_score=0.55,
                    warnings=warnings,
                    extra={"summary_text": text[:500]},
                )
            )
        )
        if len(results) >= max_results:
            break
    return results


async def search_ambientcg(query: str, max_results: int = 8) -> list[AssetResult]:
    data = await fetch_json(
        "https://ambientCG.com/api/v3/assets",
        params={
            "q": query,
            "limit": max_results,
            "sort": "popular",
            "include": "type,title,url,downloads,thumbnails,tags,dimensions,shortDescription",
        },
    )
    results: list[AssetResult] = []
    for item in data.get("assets", []):
        downloads = [dl for dl in item.get("downloads", []) if dl.get("url")]
        chosen = next((dl for dl in downloads if "1K" in str(dl.get("attributes")) and dl.get("extension") == "zip"), downloads[0] if downloads else {})
        thumbs = item.get("thumbnails") or []
        thumbnail = thumbs[0].get("url") if thumbs and isinstance(thumbs[0], dict) else None
        results.append(
            finalize(
                AssetResult(
                    id=f"ambientcg:{item.get('id')}",
                    title=item.get("title") or item.get("id") or "ambientCG asset",
                    source_name="ambientCG",
                    source_url=item.get("url") or f"https://ambientcg.com/a/{item.get('id')}",
                    author="ambientCG",
                    license="CC0",
                    license_url="https://creativecommons.org/publicdomain/zero/1.0/",
                    attribution_required=False,
                    asset_type=item.get("type"),
                    tags=list(item.get("tags") or []),
                    preview_image_url=thumbnail,
                    download_url=chosen.get("url"),
                    file_formats=[chosen.get("extension")] if chosen.get("extension") else [],
                    confidence_score=0.92,
                    extra={"dimensions": item.get("dimensions"), "downloads": downloads[:10]},
                )
            )
        )
    return results


async def search_openverse(query: str, max_results: int = 8) -> list[AssetResult]:
    data = await fetch_json("https://api.openverse.org/v1/images/", params={"q": query, "page_size": max_results})
    results: list[AssetResult] = []
    for item in data.get("results", []):
        image_url = item.get("url")
        license_name = item.get("license")
        results.append(
            finalize(
                AssetResult(
                    id=f"openverse:{item.get('id')}",
                    title=item.get("title") or "Openverse image",
                    source_name="Openverse",
                    source_url=item.get("foreign_landing_url") or image_url or "",
                    author=item.get("creator"),
                    license=license_name,
                    license_url=item.get("license_url"),
                    attribution_required=bool(license_name and "by" in license_name.lower()),
                    asset_type="image",
                    tags=[tag.get("name") for tag in item.get("tags", []) if isinstance(tag, dict) and tag.get("name")][:10],
                    preview_image_url=item.get("thumbnail") or image_url,
                    download_url=image_url,
                    file_formats=file_format_from_url(image_url),
                    confidence_score=0.72,
                    extra={"provider": item.get("provider")},
                )
            )
        )
    return results


async def search_polyhaven(query: str, max_results: int = 8) -> list[AssetResult]:
    data = await fetch_json("https://api.polyhaven.com/assets", params={"t": "all"})
    needle = query.lower().strip()
    results: list[AssetResult] = []
    for asset_id, item in data.items():
        haystack = " ".join([item.get("name", ""), item.get("description", ""), " ".join(item.get("tags") or [])]).lower()
        if needle and needle not in haystack:
            continue
        asset_type = {0: "hdr", 1: "texture", 2: "model"}.get(item.get("type"), "asset")
        results.append(
            finalize(
                AssetResult(
                    id=f"polyhaven:{asset_id}",
                    title=item.get("name") or asset_id,
                    source_name="Poly Haven",
                    source_url=f"https://polyhaven.com/a/{asset_id}",
                    author=", ".join((item.get("authors") or {}).keys()) or "Poly Haven",
                    license="CC0",
                    license_url="https://creativecommons.org/publicdomain/zero/1.0/",
                    attribution_required=False,
                    asset_type=asset_type,
                    tags=list(item.get("tags") or []) + list(item.get("categories") or []),
                    preview_image_url=f"https://cdn.polyhaven.com/asset_img/primary/{asset_id}.png?height=256",
                    download_url=None,
                    file_formats=["zip", "hdr", "exr", "blend", "gltf"],
                    confidence_score=0.88,
                    warnings=["Use get_asset_details for exact Poly Haven file downloads."],
                    extra={"dimensions": item.get("dimensions"), "max_resolution": item.get("max_resolution")},
                )
            )
        )
        if len(results) >= max_results:
            break
    return results


async def search_quaternius(query: str, max_results: int = 8) -> list[AssetResult]:
    base = "https://quaternius.com/assets.html"
    doc = soup(await fetch_html(base))
    results: list[AssetResult] = []
    seen: set[str] = set()
    needle = query.lower().strip()
    for anchor in doc.find_all("a", href=True):
        href = urljoin(base, anchor["href"])
        if "quaternius.com" not in urlparse(href).netloc or href in seen:
            continue
        title = " ".join(anchor.get_text(" ", strip=True).split())
        if not title:
            img = anchor.find("img")
            title = (img.get("alt") if img else "") or href.rsplit("/", 1)[-1]
        if needle and needle not in f"{title} {href}".lower():
            continue
        if "assets" not in href.lower() and "packs" not in href.lower() and "download" not in href.lower():
            continue
        seen.add(href)
        img = anchor.find("img")
        results.append(
            finalize(
                AssetResult(
                    id=f"quaternius:{slug(title)}",
                    title=title,
                    source_name="Quaternius",
                    source_url=href,
                    author="Quaternius",
                    license="CC0",
                    license_url="https://creativecommons.org/publicdomain/zero/1.0/",
                    attribution_required=False,
                    asset_type="3d-model-pack",
                    tags=[query, "low-poly", "3d"],
                    preview_image_url=absolute(base, img.get("src") if img else None),
                    download_url=href if href.lower().endswith(".zip") else None,
                    file_formats=file_format_from_url(href),
                    confidence_score=0.76,
                    warnings=[] if href.lower().endswith(".zip") else ["Result may be an asset page; use discover_page_assets before downloading."],
                )
            )
        )
        if len(results) >= max_results:
            break
    return results


async def search_itch(query: str, max_results: int = 8) -> list[AssetResult]:
    search_url = f"https://itch.io/game-assets/free?q={quote_plus(query)}"
    doc = soup(await fetch_html(search_url))
    results: list[AssetResult] = []
    seen: set[str] = set()
    for anchor in doc.find_all("a", href=True):
        href = anchor["href"]
        parsed = urlparse(href)
        if not parsed.netloc.endswith("itch.io") or parsed.netloc == "itch.io" or href in seen:
            continue
        title = " ".join(anchor.get_text(" ", strip=True).split())
        if not title or title.lower() in {"gif", "image"}:
            continue
        seen.add(href)
        card = anchor.find_parent(["article", "div", "li"]) or anchor.parent
        img = card.find("img") if card else None
        results.append(
            finalize(
                AssetResult(
                    id=f"itch:{parsed.netloc}:{parsed.path.strip('/')}",
                    title=title,
                    source_name="itch.io",
                    source_url=href,
                    license=None,
                    asset_type="game-asset-page",
                    tags=[query],
                    preview_image_url=absolute(href, img.get("src") if img else None),
                    confidence_score=0.5,
                    warnings=["itch.io licenses and paid/free status vary per page; do not bypass download flows."],
                )
            )
        )
        if len(results) >= max_results:
            break
    return results


async def search_godot_asset_library(query: str, max_results: int = 8) -> list[AssetResult]:
    data = await fetch_json(
        "https://godotengine.org/asset-library/api/asset",
        params={"filter": query, "max_results": max_results},
    )
    results: list[AssetResult] = []
    for item in data.get("result", []):
        asset_id = str(item.get("asset_id") or item.get("id") or item.get("title"))
        license_name = item.get("cost") if item.get("cost") not in {"0", 0, None} else item.get("license")
        results.append(
            finalize(
                AssetResult(
                    id=f"godot:{asset_id}",
                    title=item.get("title") or "Godot asset",
                    source_name="Godot Asset Library",
                    source_url=item.get("browse_url") or f"https://godotengine.org/asset-library/asset/{asset_id}",
                    author=item.get("author"),
                    license=license_name,
                    license_url=None,
                    asset_type=item.get("category"),
                    tags=[query],
                    preview_image_url=item.get("icon_url"),
                    download_url=item.get("download_url"),
                    file_formats=file_format_from_url(item.get("download_url")),
                    confidence_score=0.66,
                    warnings=["Verify Godot plugin code before enabling it in a project."],
                    extra=item,
                )
            )
        )
    return results


async def search_github_repositories(query: str, max_results: int = 8) -> list[AssetResult]:
    data = await fetch_json(
        "https://api.github.com/search/repositories",
        params={"q": f"{query} game assets license:cc0 OR license:mit OR license:apache-2.0", "per_page": max_results},
    )
    results: list[AssetResult] = []
    for item in data.get("items", []):
        license_obj = item.get("license") or {}
        license_name = license_obj.get("spdx_id") or license_obj.get("name")
        full_name = item.get("full_name")
        results.append(
            finalize(
                AssetResult(
                    id=f"github:{full_name}",
                    title=full_name or item.get("name") or "GitHub repository",
                    source_name="GitHub",
                    source_url=item.get("html_url") or "",
                    author=(item.get("owner") or {}).get("login"),
                    license=license_name,
                    license_url=None,
                    asset_type="repository",
                    tags=item.get("topics") or [query],
                    preview_image_url=(item.get("owner") or {}).get("avatar_url"),
                    download_url=f"{item.get('html_url')}/archive/refs/heads/{item.get('default_branch', 'main')}.zip" if item.get("html_url") else None,
                    file_formats=["zip"],
                    confidence_score=0.58,
                    warnings=["Repository license may not cover every contained asset; audit after download."],
                    extra={"description": item.get("description"), "stars": item.get("stargazers_count")},
                )
            )
        )
    return results


PROVIDER_SEARCH = {
    "kenney": search_kenney,
    "opengameart": search_opengameart,
    "ambientcg": search_ambientcg,
    "openverse": search_openverse,
    "polyhaven": search_polyhaven,
    "quaternius": search_quaternius,
    "itch": search_itch,
    "godot": search_godot_asset_library,
    "github": search_github_repositories,
}
