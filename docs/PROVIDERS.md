# Asset Providers

ASSETMCP normalizes provider results into one shared shape:

```json
{
  "id": "provider:specific-id",
  "title": "Asset title",
  "source_name": "Provider",
  "source_url": "https://example.com/asset-page",
  "author": "Creator",
  "license": "CC0",
  "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
  "attribution_required": false,
  "asset_type": "texture",
  "tags": ["tag"],
  "preview_image_url": "https://example.com/preview.png",
  "download_url": "https://example.com/download.zip",
  "file_formats": ["zip"],
  "confidence_score": 0.9,
  "warnings": []
}
```

## Supported Providers

| Provider | Support Level | Notes |
| --- | --- | --- |
| Kenney | Search and CC0 download helper | Scrapes public catalog pages and downloads primary archive links from asset pages. |
| OpenGameArt | Search | Licenses vary per asset page. Downloads are blocked unless license metadata is supplied. |
| ambientCG | Search and CC0 download helper | Uses the public API and prefers small archives by default. |
| Openverse | Search and direct image downloads when license metadata passes checks | License metadata comes from Openverse. |
| Poly Haven | Search | Uses the public API. Exact downloads should be resolved through details/discovery. |
| Quaternius | Search/page discovery | Public pages are scraped conservatively. Some results need discovery before download. |
| itch.io | Search/page discovery | ASSETMCP does not bypass login, paywalls, or itch download flows. Licenses vary per page. |
| Godot Asset Library | Search | Treat plugins as code; audit before enabling. |
| GitHub repositories | Search | Repository licenses may not cover every asset file; always audit after download/import. |
| Local library | Search/import | Local user-provided assets can be imported when explicitly marked as such. |

## License Policy

ASSETMCP prefers CC0/public domain assets. Attribution licenses are allowed only when enough metadata exists to generate attribution text. Missing or unclear licenses are blocked.

Provider failures are non-fatal. `search_assets` returns successful provider results alongside structured provider errors.
