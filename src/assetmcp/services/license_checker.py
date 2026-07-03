"""Strict license checks for searched, downloaded, and imported assets."""

from __future__ import annotations

from assetmcp.schemas import AssetResult, LicenseCheck, normalize_asset_result

PUBLIC_DOMAIN_LICENSES = {
    "cc0",
    "cc0-1.0",
    "public domain",
    "publicdomain",
    "pdm",
    "unlicense",
}

ATTRIBUTION_LICENSE_HINTS = {
    "cc-by",
    "cc by",
    "creative commons attribution",
    "by-sa",
    "cc-by-sa",
    "cc by-sa",
}

OPEN_SOURCE_LICENSES = {
    "mit",
    "apache-2.0",
    "apache 2.0",
    "bsd",
    "bsd-2-clause",
    "bsd-3-clause",
    "zlib",
    "isc",
}

PAID_HINTS = {"paid", "commercial", "purchase", "subscription", "marketplace"}


def normalize_license_name(value: str | None) -> str | None:
    if not value:
        return None
    return " ".join(value.strip().lower().replace("_", "-").split())


def _attribution_text(asset: AssetResult) -> str | None:
    if not asset.title or not asset.author or not asset.source_url or not asset.license:
        return None
    license_part = asset.license
    if asset.license_url:
        license_part = f"{asset.license} ({asset.license_url})"
    return f"{asset.title} by {asset.author}, licensed under {license_part}. Source: {asset.source_url}"


def check_asset_license(asset_like: AssetResult | dict, *, local_user_provided: bool = False) -> LicenseCheck:
    """Return an allow/block decision for an asset's license metadata."""
    asset = asset_like if isinstance(asset_like, AssetResult) else normalize_asset_result(asset_like)
    license_name = normalize_license_name(asset.license)
    warnings = list(asset.warnings)
    reasons: list[str] = []

    if local_user_provided:
        return LicenseCheck(
            status="allowed_local_user_provided",
            allowed=True,
            license=asset.license or "local user-provided",
            attribution_required=False,
            warnings=warnings,
        )

    haystack = " ".join(
        item
        for item in [license_name or "", asset.source_url.lower(), asset.title.lower(), " ".join(asset.tags).lower()]
        if item
    )
    if any(hint in haystack for hint in PAID_HINTS):
        return LicenseCheck(
            status="blocked_paid_asset",
            allowed=False,
            license=asset.license,
            attribution_required=False,
            warnings=warnings,
            reasons=["Paid or marketplace-restricted assets are not downloaded automatically."],
        )

    if not license_name:
        return LicenseCheck(
            status="blocked_unclear_license",
            allowed=False,
            license=None,
            attribution_required=False,
            warnings=warnings + ["Missing license metadata."],
            reasons=["License metadata is missing."],
        )

    if any(name in license_name for name in PUBLIC_DOMAIN_LICENSES):
        return LicenseCheck(
            status="allowed_public_domain",
            allowed=True,
            license=asset.license,
            attribution_required=False,
            warnings=warnings,
        )

    if any(name in license_name for name in OPEN_SOURCE_LICENSES):
        return LicenseCheck(
            status="allowed_open_source",
            allowed=True,
            license=asset.license,
            attribution_required=True,
            attribution_text=_attribution_text(asset),
            warnings=warnings + ["Open-source code license detected; verify it covers asset files."],
        )

    if any(name in license_name for name in ATTRIBUTION_LICENSE_HINTS):
        attribution = _attribution_text(asset)
        if attribution:
            return LicenseCheck(
                status="allowed_with_attribution",
                allowed=True,
                license=asset.license,
                attribution_required=True,
                attribution_text=attribution,
                warnings=warnings,
            )
        return LicenseCheck(
            status="blocked_missing_attribution_data",
            allowed=False,
            license=asset.license,
            attribution_required=True,
            warnings=warnings,
            reasons=["Attribution license found, but title/author/source metadata is incomplete."],
        )

    return LicenseCheck(
        status="blocked_unclear_license",
        allowed=False,
        license=asset.license,
        attribution_required=False,
        warnings=warnings + [f"Unrecognized license: {asset.license}"],
        reasons=["License is not clearly public domain, attribution-compatible, or open-source."],
    )
