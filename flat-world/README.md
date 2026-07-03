# Flat World Walker

A small Three.js prototype where you control a simple humanoid character and walk freely across a flat procedural world.

## Run

From the repository root:

```powershell
python -m http.server 8000
```

Open:

```text
http://localhost:8000/flat-world/
```

The page imports Three.js from a pinned CDN URL, so the browser needs network access on first load.

## Controls

- `WASD` or arrow keys: move
- `Shift`: sprint
- Mouse drag: orbit camera
- `R`: recenter camera

## Assets

- Kenney Blocky Characters, CC0: source model at `../assets/flat_world-character-kenney/Models/GLB format/character-a.glb`
- ASSETMCP-generated walk loop derived from the Kenney character: `./assets/character-a-game-walk.glb`
- Kenney Retro Textures Fantasy, CC0: grass/sand/dirt ground textures from `../assets/zombie_fps/PNG/`

## Notes

The world is generated in chunks around the player. It stays flat, but the biome, trees, rocks, and markers are deterministic from the world coordinates.
