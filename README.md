# ASSETMCP

ASSETMCP is a Python Model Context Protocol server for finding, downloading, extracting, previewing, and inspecting game assets.

It is designed for coding agents that need a practical asset workflow: search multiple public sources, download files into a managed local library, unpack asset packs safely, inspect images and 3D models, and generate previews that are easy to browse.

## Features

- Search OpenGameArt, Kenney, ambientCG, Openverse, and itch.io.
- Search several sources at once with normalized result records.
- Download individual URLs or source-specific asset packs.
- Extract `.zip`, `.tar`, `.tar.gz`, `.tar.bz2`, `.tar.xz`, `.tgz`, and `.7z` archives.
- Inspect images for dimensions, transparency, dominant colors, content bounds, edge density, and rough shape.
- Inspect 3D assets for geometry counts, bounds, extents, GLTF metadata, animation/material counts, and rough shape.
- Generate PNG contact sheets for image folders.
- Generate local HTML galleries for images and GLB/GLTF models.
- Create local browser viewers for GLB/GLTF files.
- Build JSON indexes of local asset folders.
- Search downloaded files by name, kind, and optional image dimensions.
- Slice sprite sheets and tilesets into individual PNG frames.

## Requirements

- Python 3.11 or newer
- Git, if you want to clone or contribute
- An MCP client that supports stdio servers

The project uses these main Python libraries:

- `mcp`
- `httpx`
- `beautifulsoup4`
- `lxml`
- `pillow`
- `trimesh`
- `pygltflib`
- `py7zr`

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
        "ASSETMCP_MAX_DOWNLOAD_MB": "512"
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
| `ASSETMCP_TRANSPORT` | `stdio` | MCP transport: `stdio`, `sse`, or `streamable-http`. |

## Tool Reference

### Discovery

- `search_asset_sources`: Search several supported sources at once.
- `search_opengameart`: Search OpenGameArt asset pages.
- `search_kenney_assets`: Search Kenney CC0 asset packs.
- `search_ambientcg_assets`: Search ambientCG materials, atlases, HDRIs, images, and 3D models.
- `search_openverse_images`: Search Openverse image results.
- `search_itch_assets`: Search itch.io game asset pages.
- `discover_page_assets`: Scrape an arbitrary web page for image, model, audio, archive, and file links.

### Downloading

- `download_asset`: Download one URL into the asset library.
- `download_page_assets`: Discover a page and download matching files.
- `download_kenney_asset`: Download the primary archive from a Kenney asset page.
- `download_ambientcg_asset`: Download an ambientCG archive by asset id and preferred format.

### Local Library

- `library_info`: Show storage paths, limits, and supported formats.
- `list_library`: List downloaded files by kind.
- `find_local_assets`: Search downloaded files by path, filename, kind, and optional image size.
- `index_library`: Write a JSON index of downloaded files and optional metadata/hashes.

### Inspection and Preview

- `inspect_asset`: Inspect images, 3D models, archives, or generic files.
- `make_image_contact_sheet`: Build a PNG contact sheet for image browsing.
- `create_asset_gallery`: Build a local HTML gallery for images and GLB/GLTF models.
- `create_3d_viewer`: Build a local browser viewer for `.glb` or `.gltf`.
- `slice_sprite_sheet`: Slice a sprite sheet or tileset into PNG frames.
- `extract_archive`: Extract supported archives safely inside the asset library.

## Example Agent Workflows

Search several sources:

```text
Use search_asset_sources with query "low poly trees" and sources ["kenney", "ambientcg", "opengameart"].
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

## Source Notes

- Kenney asset pages mark game assets as CC0.
- ambientCG results come from the public `api/v3/assets` endpoint and are generally CC0.
- Openverse returns license metadata from its upstream providers.
- itch.io search results link to asset pages. Check each page's license and download flow before using the files.
- OpenGameArt assets have per-page license metadata; check the result page for exact terms.

## Safety Model

ASSETMCP treats downloaded files and archives as untrusted.

- Downloads are streamed with a size limit.
- Archive extraction validates every member path before writing.
- Downloads and extracted files are kept inside the configured asset library.
- Preview files are kept inside the configured preview directory.
- Existing files are not overwritten; ASSETMCP creates numbered filenames when needed.

## Development

Install in editable mode:

```powershell
.\install.ps1
```

Run a syntax check:

```powershell
.\.venv\Scripts\python.exe -m compileall src
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
src/assetmcp/__init__.py package version
requirements.txt         runtime dependencies
pyproject.toml           package metadata and console script
install.ps1              Windows setup helper
run_assetmcp.ps1         Windows stdio launcher
assets/                  ignored local asset library
previews/                ignored generated previews
```

## License

This project is released under the MIT License. See [LICENSE](LICENSE).
