# ASSETMCP

ASSETMCP is a Python Model Context Protocol server for safe game asset search, download, import, organization, and playable prototype scaffolding.

It is designed for coding agents that need a practical "Game Asset Forge" workflow: search multiple public sources, check license safety, download files into a managed local library, unpack asset packs safely, inspect images and 3D models, generate previews, track credits, and scaffold playable prototypes.

## Features

- Search Kenney, OpenGameArt, Quaternius, Poly Haven, ambientCG, Openverse, itch.io, Godot Asset Library, GitHub repositories, and local asset folders.
- Search several sources at once with normalized result records and non-fatal provider errors.
- Check licenses before download or import.
- Track downloaded/imported assets in `ASSET_MANIFEST.json`.
- Generate `CREDITS.md` for attribution-required assets.
- Download safe individual URLs or source-specific asset packs.
- Extract `.zip`, `.tar`, `.tar.gz`, `.tar.bz2`, `.tar.xz`, `.tgz`, and `.7z` archives.
- Inspect images for dimensions, transparency, dominant colors, content bounds, edge density, and rough shape.
- Inspect 3D assets for geometry counts, bounds, extents, GLTF metadata, animation/material counts, and rough shape.
- Use Blender, when installed, for deeper model hierarchy/bone/material inspection, real PNG renders, and simple generated idle GLB exports.
- Generate PNG contact sheets for image folders.
- Generate local HTML galleries for images and GLB/GLTF models.
- Create local browser viewers for GLB/GLTF files.
- Build JSON indexes of local asset folders.
- Search downloaded files by name, kind, and optional image dimensions.
- Slice sprite sheets and tilesets into individual PNG frames.
- Scaffold playable HTML Canvas, Phaser, Three.js, and Godot 4 prototypes.
- Audit projects for blocked licenses, missing files, missing credits, and suspicious executables.

## Requirements

- Python 3.11 or newer
- Git, if you want to clone or contribute
- An MCP client that supports stdio servers
- Optional: Blender 4.x/5.x for the `blender_*` tools and production-quality model screenshots

The project uses these main Python libraries:

- `mcp`
- `httpx`
- `beautifulsoup4`
- `lxml`
- `pillow`
- `trimesh`
- `pygltflib`
- `py7zr`

## Provider Result Format

All provider results are normalized to:

- `id`
- `title`
- `source_name`
- `source_url`
- `author`
- `license`
- `license_url`
- `attribution_required`
- `asset_type`
- `tags`
- `preview_image_url`
- `download_url`
- `file_formats`
- `confidence_score`
- `warnings`

See [docs/PROVIDERS.md](docs/PROVIDERS.md) for provider-specific notes.

## Quick Start

Clone the repository:

```powershell
git clone https://github.com/evonar543/ASSETMCP.git
cd ASSETMCP
```

Create and install the local environment:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -e .
```

Run the MCP server over stdio:

```powershell
.\run_assetmcp.ps1
```

Or run it directly:

```powershell
.\.venv\Scripts\python.exe -m assetmcp.server
```

## Windows Installer Script

This repository includes a convenience installer:

```powershell
.\install.ps1
```

It creates `.venv`, installs dependencies, and installs ASSETMCP in editable mode.

## MCP Client Configuration

Use this shape in your MCP client config. Update the paths if you cloned the project somewhere else.

```json
{
  "mcpServers": {
    "ASSETMCP": {
      "command": "C:\\path\\to\\ASSETMCP\\.venv\\Scripts\\python.exe",
      "args": ["-m", "assetmcp.server"],
      "env": {
        "ASSETMCP_LIBRARY_DIR": "C:\\path\\to\\ASSETMCP\\assets",
        "ASSETMCP_PREVIEW_DIR": "C:\\path\\to\\ASSETMCP\\previews",
        "ASSETMCP_MAX_DOWNLOAD_MB": "512",
        "ASSETMCP_BLENDER_PATH": "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe"
      }
    }
  }
}
```

A ready-to-edit example is included at [assetmcp.mcp.example.json](assetmcp.mcp.example.json).

## Configuration

ASSETMCP uses these environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `ASSETMCP_LIBRARY_DIR` | `./assets` | Where downloads and extracted files are stored. |
| `ASSETMCP_PREVIEW_DIR` | `./previews` | Where generated contact sheets, galleries, and model viewers are stored. |
| `ASSETMCP_MAX_DOWNLOAD_MB` | `512` | Maximum size for a single download. |
| `ASSETMCP_BLENDER_PATH` | auto-detected | Absolute path to `blender.exe` or `blender` for Blender-backed tools. |
| `ASSETMCP_TRANSPORT` | `stdio` | MCP transport: `stdio`, `sse`, or `streamable-http`. |

## Optional Blender Setup

ASSETMCP works without Blender, but Blender unlocks higher-fidelity model reading and real screenshots:

```powershell
$env:ASSETMCP_BLENDER_PATH = "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
.\.venv\Scripts\python.exe -m assetmcp.server
```

The Blender-backed tools run Blender in background mode with generated scripts that only import local model files and write outputs inside the configured preview directory:

- `blender_info`: Check whether Blender is available.
- `blender_inspect_model`: Read scene objects, mesh stats, dimensions, hierarchy, materials, armatures, bones, animation clips, and semantic name hints such as head/torso/arm/leg.
- `blender_render_model`: Render a real PNG screenshot from `iso`, `front`, `back`, `left`, `right`, or `top`.
- `blender_create_idle_animation`: Export a GLB with a part-aware generated idle/walk loop for quick prototyping. It uses named body-part meshes such as arms, legs, torso, and head when they exist.

These tools are separate from live Blender MCP bridges. A live bridge can control an open Blender GUI and may execute arbitrary Blender Python, so only enable that bridge in clients and projects you trust.

## Tool Reference

### Discovery

- `search_assets`: Search normalized providers with license-safety metadata.
- `search_asset_sources`: Search several supported sources at once.
- `search_opengameart`: Search OpenGameArt asset pages.
- `search_kenney_assets`: Search Kenney CC0 asset packs.
- `search_ambientcg_assets`: Search ambientCG materials, atlases, HDRIs, images, and 3D models.
- `search_openverse_images`: Search Openverse image results.
- `search_itch_assets`: Search itch.io game asset pages.
- `get_asset_details`: Normalize an asset and return license decision plus discoverable downloads.
- `suggest_asset_plan`: Suggest asset searches and folders for a game idea.
- `discover_page_assets`: Scrape an arbitrary web page for image, model, audio, archive, and file links.

### Downloading

- `download_asset`: Download one URL into the asset library after license validation.
- `download_page_assets`: Discover a page and attempt downloads; unclear licenses are blocked.
- `download_kenney_asset`: Download the primary archive from a Kenney asset page.
- `download_ambientcg_asset`: Download an ambientCG archive by asset id and preferred format.
- `import_asset_to_project`: Copy a local asset into a game project and track it in manifest/credits.

### Local Library

- `library_info`: Show storage paths, limits, and supported formats.
- `list_library`: List downloaded files by kind.
- `find_local_assets`: Search downloaded files by path, filename, kind, and optional image size.
- `index_library`: Write a JSON index of downloaded files and optional metadata/hashes.
- `create_asset_manifest`: Create or refresh `ASSET_MANIFEST.json`.
- `generate_credits`: Generate `CREDITS.md`.
- `audit_project_assets`: Check manifest, credits, suspicious files, and blocked licenses.
- `find_replacement_assets`: Find safe CC0/public-domain replacements for blocked assets.

### Inspection and Preview

- `inspect_asset`: Inspect images, 3D models, archives, or generic files.
- `make_image_contact_sheet`: Build a PNG contact sheet for image browsing.
- `create_asset_gallery`: Build a local HTML gallery for images and GLB/GLTF models.
- `create_3d_viewer`: Build a local browser viewer for `.glb` or `.gltf`.
- `blender_info`: Check Blender availability for advanced model jobs.
- `blender_inspect_model`: Inspect model hierarchy, shapes, materials, bones, and animations using Blender.
- `blender_render_model`: Render a real PNG screenshot of a model using Blender.
- `blender_create_idle_animation`: Export a part-aware generated idle/walk animation as GLB.
- `slice_sprite_sheet`: Slice a sprite sheet or tileset into PNG frames.
- `extract_archive`: Extract supported archives safely inside the asset library.
- `convert_assets`: Convert image assets to PNG, WebP, JPG, or JPEG.

### Game Generation

- `create_game_project`: Create a playable prototype for HTML Canvas, Phaser, Three.js, or Godot 4.
- `generate_game_from_assets`: Scaffold a project and import selected local assets.

## Example Agent Workflows

Search several sources:

```text
Use search_assets with query "low poly trees" and sources ["kenney", "ambientcg", "polyhaven", "quaternius"].
```

Download and extract a Kenney pack:

```text
Use search_kenney_assets for "platformer", then pass the selected result URL to download_kenney_asset with auto_extract true.
```

Inspect downloaded sprites:

```text
Use find_local_assets for kind image, then inspect_asset on likely sprite files, then make_image_contact_sheet for the folder.
```

Slice a sprite sheet:

```text
Use slice_sprite_sheet with frame_width 16 and frame_height 16 on the selected PNG.
```

Create a playable prototype:

```text
Use create_game_project with engine "phaser", title "Dungeon Lantern", and project_path "./prototypes/dungeon-lantern".
```

Audit licenses:

```text
Use audit_project_assets on "./prototypes/dungeon-lantern", then use find_replacement_assets for any blocked assets.
```

More prompts are in [docs/EXAMPLE_PROMPTS.md](docs/EXAMPLE_PROMPTS.md).

## Source Notes

- Kenney asset pages mark game assets as CC0.
- ambientCG results come from the public `api/v3/assets` endpoint and are generally CC0.
- Poly Haven results come from the public API and are generally CC0.
- Quaternius assets are treated as CC0 where public page metadata supports it; use discovery/details for exact downloads.
- Godot Asset Library results may be code plugins. Audit before enabling them.
- GitHub repository licenses may not cover every contained asset file. Audit after download.
- Openverse returns license metadata from its upstream providers.
- itch.io search results link to asset pages. Check each page's license and download flow before using the files.
- OpenGameArt assets have per-page license metadata; check the result page for exact terms.

## Safety Model

ASSETMCP treats downloaded files and archives as untrusted.

- Downloads are streamed with a size limit.
- Archive extraction validates every member path before writing.
- Archive extraction blocks suspicious executables and scripts by default.
- Downloads and extracted files are kept inside the configured asset library.
- Preview files are kept inside the configured preview directory.
- Existing files are not overwritten; ASSETMCP creates numbered filenames when needed.
- Direct URL downloads without license metadata are blocked by default.
- ASSETMCP never bypasses logins, paywalls, DRM, or marketplace restrictions.

## Development

Install in editable mode:

```powershell
.\install.ps1
```

Run a syntax check:

```powershell
.\.venv\Scripts\python.exe -m compileall src
```

Run tests:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

Verify that the MCP server starts and lists tools:

```powershell
@'
import asyncio
from assetmcp.server import mcp

async def main():
    tools = await mcp.list_tools()
    print(len(tools))
    print([tool.name for tool in tools])

asyncio.run(main())
'@ | .\.venv\Scripts\python.exe -
```

## Repository Layout

```text
src/assetmcp/server.py   MCP server and tool implementations
src/assetmcp/providers/  provider integrations and search registry
src/assetmcp/services/   license, manifest, scanner, and scaffolding services
src/assetmcp/schemas.py  normalized asset schemas
src/assetmcp/__init__.py package version
tests/                   unit tests
requirements.txt         runtime dependencies
pyproject.toml           package metadata and console script
install.ps1              Windows setup helper
run_assetmcp.ps1         Windows stdio launcher
assets/                  ignored local asset library
previews/                ignored generated previews
```

## License

This project is released under the MIT License. See [LICENSE](LICENSE).
