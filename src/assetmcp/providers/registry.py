"""Provider registry and non-fatal provider fan-out."""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from assetmcp.schemas import AssetResult

ProviderSearch = Callable[[str, int], Awaitable[list[AssetResult]]]


async def search_providers(
    query: str,
    providers: dict[str, ProviderSearch],
    *,
    source_names: list[str] | None = None,
    max_results_per_source: int = 8,
) -> dict[str, object]:
    requested = {name.lower() for name in source_names} if source_names else None
    selected = {
        name: search
        for name, search in providers.items()
        if requested is None or name.lower() in requested
    }
    gathered = await asyncio.gather(
        *(search(query, max_results_per_source) for search in selected.values()),
        return_exceptions=True,
    )
    by_source: dict[str, object] = {}
    flat: list[dict] = []
    for source_name, result in zip(selected.keys(), gathered):
        if isinstance(result, Exception):
            by_source[source_name] = {"error": str(result), "results": []}
            continue
        records = [item.to_dict() for item in result]
        by_source[source_name] = {"results": records}
        flat.extend(records)
    return {"query": query, "sources": list(selected.keys()), "by_source": by_source, "results": flat}


PROVIDERS: dict[str, ProviderSearch] = {}
