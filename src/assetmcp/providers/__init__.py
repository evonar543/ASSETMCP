"""Asset source providers."""

from assetmcp.providers.builtin import PROVIDER_SEARCH
from assetmcp.providers.registry import search_providers

PROVIDERS = PROVIDER_SEARCH

__all__ = ["PROVIDERS", "search_providers"]
