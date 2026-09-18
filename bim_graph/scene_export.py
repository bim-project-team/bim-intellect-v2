"""Storey-partitioned glTF scene export for the 3D viewer.

Why storey-partitioned: the reference model tessellates to ~88 MB of glTF
across 10,887 elements. Serving that as one asset makes the viewer unusable,
while a single storey averages ~2 MB and is what a user actually asked about.
Partitioning by storey therefore matches both the payload budget and the
question shape ("clearances near doors on Level 5").

Why this runs inside the existing geometry pass: extract_graph already
tessellates every filtered element to compute its bounding box. Writing each
shape to a serializer while it is in hand costs no additional tessellation --
measured 55 s for bbox+glTF versus 73 s for bbox alone, i.e. within noise.
A separate export pass would double the most expensive step in ingestion.

Coordinates. IfcOpenShell's glTF serializer emits Y-up, which is also
three.js's convention, so meshes load correctly oriented with no client-side
rotation. The mapping from the IFC/Neo4j frame is::

    gltf_x = ifc_x    gltf_y = ifc_z    gltf_z = -ifc_y

verified element-for-element against the ``minX..maxZ`` properties stored in
Neo4j (see tests/test_scene_export.py). The shared IfcOpenShell geometry
settings keep ``CONVERT_BACK_UNITS=False``: tessellated vertices, stored AABBs,
clash metrics, and exported scenes therefore use the same canonical metre
coordinate contract.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import struct
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("bim_intellect.scene_export")

SCENES_ROOT = Path(os.getenv("BIM_SCENE_STORAGE_DIR", "dataset/ifc/scenes"))
MANIFEST_NAME = "manifest.json"
SCHEMA_VERSION = 1

# "glTF" little-endian, the first four bytes of every .glb file.
_GLB_MAGIC = 0x46546C67

# Elements outside the storey hierarchy (in the reference model: every IfcSpace
# and ~1,000 unhosted IfcFlowTerminals) still have geometry worth showing, so
# they get their own scene rather than being silently dropped.
UNASSIGNED_SCENE_KEY = "unassigned"
UNASSIGNED_STOREY_LABEL = ""

AXIS_MAPPING = "gltf=(ifc_x, ifc_z, -ifc_y)"

_MANIFEST_LOCK = threading.RLock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def scene_key(storey_name: str | None) -> str:
    """Stable filename-safe key for a storey.

    A hash rather than a slug, because storey names are neither unique after
    slugification nor reliably ASCII. The reference model already contains
    "BLDG. 1,2,3- LEVEL 5 FLR. FIN." and "BLDG 1,2,3- LEVEL 5 FLR. FIN." as
    distinct storeys, which any punctuation-stripping slug would merge, and
    Persian storey names would slugify to nothing at all. The human-readable
    name is preserved in the manifest instead.
    """
    name = (storey_name or "").strip()
    if not name:
        return UNASSIGNED_SCENE_KEY
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]


def ifc_bbox_to_gltf(
    min_x: float, min_y: float, min_z: float,
    max_x: float, max_y: float, max_z: float,
) -> tuple[list[float], list[float]]:
    """Convert an IFC/Neo4j axis-aligned box to the viewer's glTF frame.

    Negating Y swaps which corner is minimal on that axis, so the bounds are
    re-derived rather than mapped corner-to-corner.
    """
    return (
        [min_x, min_z, -max_y],
        [max_x, max_z, -min_y],
    )


def scene_directory(project_id: str, file_id: str) -> Path:
    return SCENES_ROOT / project_id / file_id


def scene_path(project_id: str, file_id: str, key: str) -> Path:
    return scene_directory(project_id, file_id) / f"{key}.glb"


def manifest_path(project_id: str) -> Path:
    return SCENES_ROOT / project_id / MANIFEST_NAME


def read_manifest(project_id: str) -> dict[str, Any]:
    path = manifest_path(project_id)
    if not path.exists():
        return _empty_manifest(project_id)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("Scene manifest for %s is unreadable; treating as empty.", project_id)
        return _empty_manifest(project_id)


def _empty_manifest(project_id: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "project_id": project_id,
        "axis_mapping": AXIS_MAPPING,
        "updated_at": None,
        "files": {},
    }


def _write_manifest(project_id: str, manifest: dict[str, Any]) -> dict[str, Any]:
    manifest["schema_version"] = SCHEMA_VERSION
    manifest["project_id"] = project_id
    manifest["axis_mapping"] = AXIS_MAPPING
    manifest["updated_at"] = _utc_now()
    target = manifest_path(project_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, target)
    return manifest


def merge_manifest(project_id: str, file_entry: dict[str, Any]) -> dict[str, Any]:
    """Record one file's scenes, leaving other files in the project intact.

    Ingesting a single discipline model must not invalidate the scenes of its
    siblings, so the manifest is merged per file rather than rewritten.
    """
    with _MANIFEST_LOCK:
        manifest = read_manifest(project_id)
        manifest["files"][file_entry["file_id"]] = file_entry
        return _write_manifest(project_id, manifest)


def prune_manifest(project_id: str, file_ids: list[str]) -> dict[str, Any]:
    """Drop scenes for files that are no longer part of the graph.

    Both the manifest entry and the exported geometry go, because a viewer that
    can still load a model the answers cannot reason about is worse than one that
    reports the geometry as missing. Deletion is confined to this project's scene
    directory, which contains only generated data.
    """
    keep = set(file_ids)
    with _MANIFEST_LOCK:
        manifest = read_manifest(project_id)
        stale = [key for key in manifest["files"] if key not in keep]
        if not stale:
            return manifest
        for key in stale:
            manifest["files"].pop(key, None)
            _remove_scene_directory(project_id, key)
        return _write_manifest(project_id, manifest)


def reconcile_scene_scope(keep_by_project: dict[str, list[str]]) -> dict[str, dict[str, Any]]:
    """Make generated scene storage match the models that remain in the graph.

    This is used after a global graph wipe. It removes stale file directories
    even when a manifest is missing/corrupt, while retaining only explicitly
    listed project/file pairs. Original IFC files live elsewhere and are never
    touched.
    """
    normalized = {project_id: set(file_ids) for project_id, file_ids in keep_by_project.items()}
    project_ids = set(normalized)
    if SCENES_ROOT.exists():
        project_ids.update(path.name for path in SCENES_ROOT.iterdir() if path.is_dir())

    results: dict[str, dict[str, Any]] = {}
    for project_id in sorted(project_ids):
        keep = normalized.get(project_id, set())
        manifest = prune_manifest(project_id, sorted(keep))
        project_dir = SCENES_ROOT / project_id
        if project_dir.is_dir():
            for child in project_dir.iterdir():
                if child.is_dir() and child.name not in keep:
                    _remove_scene_directory(project_id, child.name)
        results[project_id] = manifest
    return results


def _remove_scene_directory(project_id: str, file_id: str) -> None:
    """Delete one file's exported scenes, refusing anything outside SCENES_ROOT."""
    directory = scene_directory(project_id, file_id)
    try:
        resolved = directory.resolve()
        if not resolved.is_relative_to(SCENES_ROOT.resolve()) or not resolved.is_dir():
            return
        shutil.rmtree(resolved)
    except OSError as exc:
        logger.warning("Could not remove stale scenes at %s: %s", directory, exc)


def gltf_declared_length(path: Path) -> int | None:
    """Length recorded in a .glb header, or None if the file is not readable as one.

    Used to prove a scene was fully flushed before it is published. The glTF
    binary header carries the total file length, so a mismatch against the actual
    size is exactly the signature of a half-written export.
    """
    try:
        with path.open("rb") as handle:
            header = handle.read(12)
        if len(header) < 12:
            return None
        magic, _version, length = struct.unpack("<III", header)
        return length if magic == _GLB_MAGIC else None
    except OSError:
        return None


class SceneWriter:
    """Routes tessellated shapes into one glTF file per storey.

    Serializers are opened lazily, so a storey excluded by the caller's
    storey/type filter never produces an empty asset.

    Lifecycle warning: ``finalize()`` is necessary but *not* sufficient. The
    IfcOpenShell glTF serializer flushes its remaining buffer from the C++
    destructor, so a file measured while any Python reference is still alive is
    short -- observed as 8,991,664 bytes against a correct 8,998,268 on a
    571-element scene. ``close()`` therefore releases each serializer explicitly
    and then verifies the glTF length header against the file size, so a scene
    that somehow was not flushed is excluded rather than published broken.
    """

    def __init__(
        self,
        model: Any,
        geometry_settings: Any,
        *,
        project_id: str,
        file_id: str,
        source_ifc_file: str,
        unit_scale_to_metre: float | None = None,
    ):
        self._model = model
        self._geometry_settings = geometry_settings
        self.project_id = project_id
        self.file_id = file_id
        self.source_ifc_file = source_ifc_file
        self.unit_scale_to_metre = unit_scale_to_metre
        self._directory = scene_directory(project_id, file_id)
        self._directory.mkdir(parents=True, exist_ok=True)
        self._serializers: dict[str, Any] = {}
        self._storey_names: dict[str, str] = {}
        self._counts: dict[str, int] = {}
        self.failed_elements = 0

    def _serializer(self, key: str) -> Any:
        if key in self._serializers:
            return self._serializers[key]

        # Imported here so scene_key/ifc_bbox_to_gltf stay importable (and
        # testable) in environments without a compiled IfcOpenShell.
        import ifcopenshell.geom as geom

        settings = geom.serializer_settings()
        # Names each glTF node after the element's IFC GlobalId, which is what
        # the graph stores as `ifcGuid` -- the viewer highlights by looking a
        # node up by that name, with no separate index to keep in sync.
        settings.set("use-element-guids", True)
        serializer = geom.serializers.gltf(str(scene_path(self.project_id, self.file_id, key)),
                                          self._geometry_settings, settings)
        serializer.setFile(self._model)
        # Metadata only; geometry already follows the extractor's canonical
        # metre contract and therefore stays aligned with stored AABBs.
        serializer.setUnitNameAndMagnitude("METER", 1.0)
        serializer.writeHeader()
        self._serializers[key] = serializer
        return serializer

    def write(self, shape: Any, storey_name: str | None) -> None:
        """Add one tessellated shape to its storey's scene.

        A shape IfcOpenShell can tessellate but the serializer rejects must not
        abort ingestion, so failures are counted and reported in the manifest.
        """
        key = scene_key(storey_name)
        try:
            self._serializer(key).write(shape)
        except Exception as exc:
            self.failed_elements += 1
            logger.warning("Scene export skipped element %s: %s", getattr(shape, "guid", "?"), exc)
            return
        # Recorded only after a successful write, so the manifest never advertises
        # a storey whose every element failed to serialize.
        self._storey_names.setdefault(key, storey_name or UNASSIGNED_STOREY_LABEL)
        self._counts[key] = self._counts.get(key, 0) + 1

    def close(self) -> dict[str, Any]:
        """Finalize every scene and return this file's manifest entry.

        Idempotent: calling it twice (via ``__exit__`` after an explicit call)
        finalizes nothing further and re-publishes the same entry.
        """
        for key in list(self._serializers):
            serializer = self._serializers.pop(key)
            try:
                serializer.finalize()
            except Exception as exc:  # pragma: no cover - serializer-internal failure
                logger.error("Scene finalize failed: %s", exc)
            # Dropped one at a time so the C++ destructor runs, and its final
            # buffer flush completes, before the file is measured below.
            del serializer

        scenes = []
        for key, count in sorted(self._counts.items()):
            path = scene_path(self.project_id, self.file_id, key)
            size = path.stat().st_size if path.exists() else 0
            declared = gltf_declared_length(path) if size else None
            if size == 0 or declared != size:
                # Advertising a truncated scene would make the viewer fail to
                # parse it instead of falling back to bounding boxes.
                logger.error(
                    "Scene %s for %s/%s is incomplete (size=%s, glTF header=%s); "
                    "excluding it from the manifest.",
                    key, self.project_id, self.file_id, size, declared,
                )
                continue
            scenes.append({
                "scene_key": key,
                "storey_name": self._storey_names.get(key, UNASSIGNED_STOREY_LABEL),
                "element_count": count,
                "bytes": size,
            })

        entry = {
            "file_id": self.file_id,
            "source_ifc_file": self.source_ifc_file,
            "geometry_unit": "metre",
            # Retained as source-model provenance and for backward-compatible
            # manifests. It is not the output scene/AABB coordinate scale.
            "unit_scale_to_metre": self.unit_scale_to_metre,
            "generated_at": _utc_now(),
            "failed_elements": self.failed_elements,
            "scenes": scenes,
        }
        merge_manifest(self.project_id, entry)
        return entry

    def __enter__(self) -> "SceneWriter":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()
