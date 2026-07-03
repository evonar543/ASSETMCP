"""Blender-backed model inspection, rendering, and simple animation helpers.

These helpers run Blender in background mode with a generated Python script. The
script is intentionally narrow: it imports a local model, reads scene data or
produces a deterministic output file, then prints one JSON payload for ASSETMCP
to parse.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


JSON_PREFIX = "ASSETMCP_JSON:"
DEFAULT_TIMEOUT_SECONDS = 240
COMMON_WINDOWS_BLENDER_PATHS = (
    r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 4.4\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 4.3\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
)
SUPPORTED_IMPORT_EXTENSIONS = {".glb", ".gltf", ".fbx", ".obj", ".stl", ".ply"}


def find_blender_executable(explicit_path: str | None = None) -> str | None:
    """Return the first usable Blender executable path."""
    candidates = [
        explicit_path,
        os.environ.get("ASSETMCP_BLENDER_PATH"),
        shutil.which("blender"),
        *COMMON_WINDOWS_BLENDER_PATHS,
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.exists() and path.is_file():
            return str(path)
    return None


def blender_status(explicit_path: str | None = None) -> dict[str, Any]:
    """Report whether Blender is available for ASSETMCP background jobs."""
    executable = find_blender_executable(explicit_path)
    if not executable:
        return {
            "available": False,
            "executable": None,
            "supported_import_extensions": sorted(SUPPORTED_IMPORT_EXTENSIONS),
        }
    return {
        "available": True,
        "executable": executable,
        "supported_import_extensions": sorted(SUPPORTED_IMPORT_EXTENSIONS),
    }


def run_blender_script(
    script: str,
    *,
    blender_path: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Run a generated Blender Python script and parse its JSON sentinel line."""
    executable = find_blender_executable(blender_path)
    if not executable:
        raise RuntimeError(
            "Blender executable was not found. Set ASSETMCP_BLENDER_PATH or add blender to PATH."
        )

    with tempfile.TemporaryDirectory(prefix="assetmcp-blender-") as temp_dir:
        script_path = Path(temp_dir) / "job.py"
        script_path.write_text(script, encoding="utf-8")
        completed = subprocess.run(
            [executable, "--background", "--factory-startup", "--python", str(script_path)],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    payload = _extract_json_payload(stdout)
    if completed.returncode != 0:
        message = stderr.strip() or stdout.strip() or f"Blender exited with {completed.returncode}"
        raise RuntimeError(message[-4000:])
    if payload is None:
        message = stderr.strip() or stdout.strip() or "Blender did not return an ASSETMCP JSON payload."
        raise RuntimeError(message[-4000:])
    payload["blender_stdout_tail"] = stdout[-2000:]
    if stderr:
        payload["blender_stderr_tail"] = stderr[-2000:]
    return payload


def inspect_model(model_path: Path, *, blender_path: str | None = None) -> dict[str, Any]:
    """Return object hierarchy, mesh stats, materials, armatures, bones, and animations."""
    _ensure_supported_model(model_path)
    script = _script_prelude(model_path) + r"""
import math

def dims_from_bounds(obj):
    try:
        points = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
        mins = [min(point[i] for point in points) for i in range(3)]
        maxs = [max(point[i] for point in points) for i in range(3)]
        return [round(maxs[i] - mins[i], 5) for i in range(3)], [mins, maxs]
    except Exception:
        return [0.0, 0.0, 0.0], None

def shape_from_dims(dims):
    positives = [abs(value) for value in dims if abs(value) > 0.00001]
    if not positives:
        return "empty-or-point-like"
    longest = max(positives)
    shortest = min(positives)
    if shortest == 0:
        return "flat-or-degenerate"
    ratio = longest / shortest
    if ratio < 1.35:
        return "compact"
    if dims[2] == max(dims):
        return "tall-upright"
    if dims[2] == min(dims):
        return "flat"
    return "elongated"

def semantic_hint(name):
    lower = name.lower()
    hints = {
        "head": ("head", "face", "hair", "eye"),
        "torso": ("torso", "body", "chest", "spine", "hips", "pelvis"),
        "arm": ("arm", "shoulder", "elbow", "hand", "finger"),
        "leg": ("leg", "thigh", "knee", "foot", "toe"),
        "weapon": ("sword", "gun", "axe", "bow", "weapon"),
        "wheel": ("wheel", "tire"),
        "wing": ("wing",),
        "tail": ("tail",),
    }
    return [label for label, needles in hints.items() if any(needle in lower for needle in needles)]

import_model(MODEL_PATH)

objects = []
mesh_count = 0
vertex_count = 0
face_count = 0
for obj in bpy.context.scene.objects:
    dims, bounds = dims_from_bounds(obj) if obj.type in {"MESH", "ARMATURE", "EMPTY"} else ([0, 0, 0], None)
    entry = {
        "name": obj.name,
        "type": obj.type,
        "parent": obj.parent.name if obj.parent else None,
        "children": [child.name for child in obj.children],
        "location": [round(value, 5) for value in obj.location],
        "rotation_euler": [round(value, 5) for value in obj.rotation_euler],
        "dimensions": dims,
        "shape": shape_from_dims(dims),
        "semantic_hints": semantic_hint(obj.name),
    }
    if bounds:
        entry["bounds"] = bounds
    if obj.type == "MESH" and obj.data:
        mesh_count += 1
        vertex_count += len(obj.data.vertices)
        face_count += len(obj.data.polygons)
        entry["mesh"] = {
            "vertices": len(obj.data.vertices),
            "faces": len(obj.data.polygons),
            "materials": [slot.material.name for slot in obj.material_slots if slot.material],
        }
    objects.append(entry)

armatures = []
for armature in [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]:
    bones = []
    for bone in armature.data.bones:
        bones.append(
            {
                "name": bone.name,
                "parent": bone.parent.name if bone.parent else None,
                "children": [child.name for child in bone.children],
                "semantic_hints": semantic_hint(bone.name),
            }
        )
    armatures.append({"name": armature.name, "bone_count": len(bones), "bones": bones[:400]})

scene_min, scene_max = scene_bounds()
scene_dims = [round(scene_max[i] - scene_min[i], 5) for i in range(3)] if scene_min else [0, 0, 0]
materials = []
for material in bpy.data.materials:
    materials.append({"name": material.name, "use_nodes": bool(material.use_nodes)})

actions = []
for action in bpy.data.actions:
    fcurves = getattr(action, "fcurves", None)
    actions.append(
        {
            "name": action.name,
            "frame_range": [round(value, 3) for value in action.frame_range],
            "fcurve_count": len(fcurves) if fcurves is not None else None,
        }
    )

emit(
    {
        "model_path": str(MODEL_PATH),
        "scene": {
            "object_count": len(objects),
            "mesh_count": mesh_count,
            "vertex_count": vertex_count,
            "face_count": face_count,
            "dimensions": scene_dims,
            "shape": shape_from_dims(scene_dims),
        },
        "objects": objects,
        "materials": materials,
        "armatures": armatures,
        "animations": actions,
    }
)
"""
    return run_blender_script(script, blender_path=blender_path)


def render_model(
    model_path: Path,
    output_path: Path,
    *,
    view: str = "iso",
    resolution: int = 1024,
    blender_path: str | None = None,
) -> dict[str, Any]:
    """Render a PNG screenshot of a model from a named camera angle."""
    _ensure_supported_model(model_path)
    if output_path.suffix.lower() != ".png":
        output_path = output_path.with_suffix(".png")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    resolution = max(256, min(int(resolution), 4096))
    script = (
        _script_prelude(model_path)
        + f"\nOUTPUT_PATH = Path({json.dumps(str(output_path))})\nVIEW = {json.dumps(view)}\nRESOLUTION = {resolution}\n"
        + r"""
import math

import_model(MODEL_PATH)
scene_min, scene_max = scene_bounds()
if scene_min is None:
    raise RuntimeError("Imported scene has no renderable bounds.")

center = (scene_min + scene_max) * 0.5
dims = scene_max - scene_min
radius = max(dims.length * 0.65, 1.0)

directions = {
    "front": Vector((0, -1, 0.28)),
    "back": Vector((0, 1, 0.28)),
    "left": Vector((-1, 0, 0.28)),
    "right": Vector((1, 0, 0.28)),
    "top": Vector((0, 0, 1)),
    "iso": Vector((1.7, -2.1, 1.25)),
}
direction = directions.get(VIEW.lower(), directions["iso"]).normalized()

camera_data = bpy.data.cameras.new("ASSETMCP_Camera")
camera = bpy.data.objects.new("ASSETMCP_Camera", camera_data)
bpy.context.collection.objects.link(camera)
camera.location = center + direction * (radius * 2.8)
camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
camera.data.lens = 55
camera.data.clip_end = max(radius * 20, 100)
bpy.context.scene.camera = camera

light_data = bpy.data.lights.new("ASSETMCP_Key_Light", "AREA")
light = bpy.data.objects.new("ASSETMCP_Key_Light", light_data)
bpy.context.collection.objects.link(light)
light.location = center + Vector((-2.5, -3.0, 4.0)).normalized() * (radius * 3.5)
light.rotation_euler = (center - light.location).to_track_quat("-Z", "Y").to_euler()
light.data.energy = 500
light.data.size = max(radius * 1.8, 1.0)

bpy.context.scene.render.resolution_x = RESOLUTION
bpy.context.scene.render.resolution_y = RESOLUTION
bpy.context.scene.render.film_transparent = False
bpy.context.scene.world.color = (0.04, 0.045, 0.05)
try:
    bpy.context.scene.render.engine = "BLENDER_EEVEE_NEXT"
except Exception:
    bpy.context.scene.render.engine = "BLENDER_WORKBENCH"
bpy.context.scene.eevee.taa_render_samples = 64 if hasattr(bpy.context.scene, "eevee") else 16
bpy.context.scene.render.filepath = str(OUTPUT_PATH)
bpy.ops.render.render(write_still=True)

emit(
    {
        "model_path": str(MODEL_PATH),
        "render_path": str(OUTPUT_PATH),
        "view": VIEW,
        "resolution": RESOLUTION,
        "scene_dimensions": [round(value, 5) for value in dims],
    }
)
"""
    )
    return run_blender_script(script, blender_path=blender_path)


def create_idle_animation(
    model_path: Path,
    output_path: Path,
    *,
    blender_path: str | None = None,
) -> dict[str, Any]:
    """Export a GLB with a generated idle/walk animation.

    The animation first looks for semantic mesh names such as arm-left,
    leg-right, torso, and head. Many free low-poly assets are not rigged, but
    they do have separated body-part meshes with useful origins; animating those
    parts produces a much better result than moving the whole model as one root.
    """
    _ensure_supported_model(model_path)
    if output_path.suffix.lower() != ".glb":
        output_path = output_path.with_suffix(".glb")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    script = (
        _script_prelude(model_path)
        + f"\nOUTPUT_PATH = Path({json.dumps(str(output_path))})\n"
        + r"""
import math

import_model(MODEL_PATH)
bpy.context.scene.frame_start = 1
bpy.context.scene.frame_end = 48

animated_objects = []
animated_parts = []

def find_part(*needles):
    for obj in bpy.context.scene.objects:
        lower = obj.name.lower()
        if obj.type in {"MESH", "EMPTY", "ARMATURE"} and all(needle in lower for needle in needles):
            return obj
    return None

def key_rotation(obj, axis, values):
    if not obj:
        return
    start = obj.rotation_euler.copy()
    for frame, angle in values:
        bpy.context.scene.frame_set(frame)
        obj.rotation_euler = start.copy()
        setattr(obj.rotation_euler, axis, getattr(obj.rotation_euler, axis) + angle)
        obj.keyframe_insert(data_path="rotation_euler", frame=frame)
    animated_parts.append(obj.name)

def key_root(obj):
    if not obj:
        return
    start_location = obj.location.copy()
    start_rotation = obj.rotation_euler.copy()
    for frame, z_offset, lean in ((1, 0.0, 0.0), (12, 0.06, 0.035), (24, 0.0, 0.0), (36, 0.06, -0.035), (48, 0.0, 0.0)):
        bpy.context.scene.frame_set(frame)
        obj.location = start_location + Vector((0, 0, z_offset))
        obj.rotation_euler = start_rotation.copy()
        obj.rotation_euler.y += lean
        obj.keyframe_insert(data_path="location", frame=frame)
        obj.keyframe_insert(data_path="rotation_euler", frame=frame)
    animated_objects.append(obj.name)

root = find_part("root") or find_part("character") or next(
    (obj for obj in bpy.context.scene.objects if obj.parent is None and obj.type in {"MESH", "ARMATURE", "EMPTY"}),
    None,
)
key_root(root)

frames = (1, 12, 24, 36, 48)
semantic_swings = (
    (find_part("arm", "left"), "x", (0.48, -0.45, 0.48, -0.45, 0.48)),
    (find_part("arm", "right"), "x", (-0.48, 0.45, -0.48, 0.45, -0.48)),
    (find_part("leg", "left"), "x", (-0.34, 0.31, -0.34, 0.31, -0.34)),
    (find_part("leg", "right"), "x", (0.34, -0.31, 0.34, -0.31, 0.34)),
    (find_part("torso") or find_part("body"), "z", (0.0, -0.055, 0.0, 0.055, 0.0)),
    (find_part("head"), "z", (0.0, 0.045, 0.0, -0.045, 0.0)),
)
for obj, axis, angles in semantic_swings:
    key_rotation(obj, axis, tuple(zip(frames, angles)))

head = find_part("head")
if head:
    key_rotation(head, "x", ((1, 0.0), (12, -0.035), (24, 0.0), (36, 0.035), (48, 0.0)))

animated_bones = []
for armature in [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]:
    bpy.context.view_layer.objects.active = armature
    for pose_bone in armature.pose.bones:
        lower = pose_bone.name.lower()
        if not any(token in lower for token in ("arm", "hand", "leg", "thigh", "foot")):
            continue
        pose_bone.rotation_mode = "XYZ"
        direction = -1.0 if any(token in lower for token in ("left", ".l", "_l")) else 1.0
        for frame, angle in ((1, 0.0), (12, 0.16 * direction), (24, 0.0), (36, -0.16 * direction), (48, 0.0)):
            bpy.context.scene.frame_set(frame)
            pose_bone.rotation_euler.x = angle
            pose_bone.keyframe_insert(data_path="rotation_euler", frame=frame)
        animated_bones.append(f"{armature.name}:{pose_bone.name}")

for action in bpy.data.actions:
    action.name = "ASSETMCP_generated_part_aware_idle"
    for fcurve in getattr(action, "fcurves", []):
        for point in fcurve.keyframe_points:
            point.interpolation = "BEZIER"

bpy.ops.export_scene.gltf(
    filepath=str(OUTPUT_PATH),
    export_format="GLB",
    export_animations=True,
    export_apply=False,
)

emit(
    {
        "source_model_path": str(MODEL_PATH),
        "animated_model_path": str(OUTPUT_PATH),
        "frame_start": 1,
        "frame_end": 48,
        "animated_objects": animated_objects,
        "animated_parts": animated_parts,
        "animated_bones": animated_bones[:200],
    }
)
"""
    )
    return run_blender_script(script, blender_path=blender_path)


def _extract_json_payload(output: str) -> dict[str, Any] | None:
    for line in reversed(output.splitlines()):
        if line.startswith(JSON_PREFIX):
            return json.loads(line[len(JSON_PREFIX) :])
    return None


def _ensure_supported_model(model_path: Path) -> None:
    if model_path.suffix.lower() not in SUPPORTED_IMPORT_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_IMPORT_EXTENSIONS))
        raise ValueError(f"Blender tools support {supported}; got {model_path.suffix or 'no extension'}")
    if not model_path.exists():
        raise FileNotFoundError(str(model_path))


def _script_prelude(model_path: Path) -> str:
    """Return Blender Python shared by every generated job script."""
    return f"""
import json
from pathlib import Path

import bpy
from mathutils import Vector

MODEL_PATH = Path({json.dumps(str(model_path))})
JSON_PREFIX = {json.dumps(JSON_PREFIX)}

def emit(payload):
    print(JSON_PREFIX + json.dumps(payload, sort_keys=True))

def clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()

def import_model(path):
    clear_scene()
    suffix = path.suffix.lower()
    if suffix in {{".glb", ".gltf"}}:
        bpy.ops.import_scene.gltf(filepath=str(path))
    elif suffix == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(path))
    elif suffix == ".obj":
        if hasattr(bpy.ops.wm, "obj_import"):
            bpy.ops.wm.obj_import(filepath=str(path))
        else:
            bpy.ops.import_scene.obj(filepath=str(path))
    elif suffix == ".stl":
        if hasattr(bpy.ops.wm, "stl_import"):
            bpy.ops.wm.stl_import(filepath=str(path))
        else:
            bpy.ops.import_mesh.stl(filepath=str(path))
    elif suffix == ".ply":
        if hasattr(bpy.ops.wm, "ply_import"):
            bpy.ops.wm.ply_import(filepath=str(path))
        else:
            bpy.ops.import_mesh.ply(filepath=str(path))
    else:
        raise RuntimeError(f"Unsupported model extension: {{suffix}}")

def scene_bounds():
    points = []
    for obj in bpy.context.scene.objects:
        if obj.type not in {{"MESH", "ARMATURE", "EMPTY"}}:
            continue
        if obj.type == "MESH" and obj.bound_box:
            points.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
        elif obj.type == "ARMATURE":
            points.append(obj.matrix_world.translation)
    if not points:
        return None, None
    mins = Vector((min(point.x for point in points), min(point.y for point in points), min(point.z for point in points)))
    maxs = Vector((max(point.x for point in points), max(point.y for point in points), max(point.z for point in points)))
    return mins, maxs
"""
