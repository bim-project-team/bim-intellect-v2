"""Tests for glTF scene export used by the 3D viewer.

The pure helpers (scene keys, the axis transform, manifest bookkeeping) are
tested without IfcOpenShell so they run anywhere. The end-to-end export test
requires the reference IFC file and skips when it is absent.

The axis-mapping expectations here are not derived from documentation; they were
measured by exporting real elements and comparing the resulting glTF node bounds
against the same elements' minX..maxZ properties. If IfcOpenShell ever changes
its glTF orientation, this test is what catches it.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from bim_graph import scene_export
from bim_graph.scene_export import (
    UNASSIGNED_SCENE_KEY, ifc_bbox_to_gltf, merge_manifest, prune_manifest,
    read_manifest, scene_key, scene_path,
)

REFERENCE_IFC = Path("bim-intellect-sources/210_King_Merged.ifc")

# Two storeys from the reference model that differ only in punctuation. Any
# slug-based key would merge them and silently serve the wrong geometry.
NEAR_COLLIDING_STOREYS = (
    "BLDG. 1,2,3- LEVEL 5 FLR. FIN.",
    "BLDG 1,2,3- LEVEL 5 FLR. FIN.",
)


@pytest.fixture()
def scenes_root(tmp_path, monkeypatch):
    """Redirect scene storage so tests never touch the real dataset directory."""
    root = tmp_path / "scenes"
    monkeypatch.setattr(scene_export, "SCENES_ROOT", root)
    return root


def test_scene_key_is_stable_and_filename_safe():
    key = scene_key("BLDG. 1,2,3- LEVEL 5 FLR. FIN.")
    assert key == scene_key("BLDG. 1,2,3- LEVEL 5 FLR. FIN.")
    assert key.isalnum() and len(key) == 12


def test_scene_key_separates_storeys_that_differ_only_in_punctuation():
    first, second = (scene_key(name) for name in NEAR_COLLIDING_STOREYS)
    assert first != second


def test_scene_key_handles_non_ascii_storey_names():
    """A Persian storey name must still produce a usable, distinct key."""
    key = scene_key("طبقه پنجم")
    assert key.isalnum() and len(key) == 12
    assert key != scene_key("طبقه ششم")
    assert key != UNASSIGNED_SCENE_KEY


def test_missing_storey_maps_to_the_unassigned_scene():
    # 1,392 elements in the reference model (every IfcSpace, plus unhosted flow
    # terminals) have no storey. They still have geometry worth showing.
    for value in (None, "", "   "):
        assert scene_key(value) == UNASSIGNED_SCENE_KEY


def test_axis_transform_matches_measured_gltf_output():
    """Pinned to values measured from real exported geometry."""
    minimum, maximum = ifc_bbox_to_gltf(-101.52, -66.21, 53.75, 99.94, 69.88, 80.34)
    assert minimum == pytest.approx([-101.52, 53.75, -69.88])
    assert maximum == pytest.approx([99.94, 80.34, 66.21])


def test_axis_transform_keeps_bounds_ordered_after_negating_y():
    """Negating Y swaps which corner is minimal, so bounds must be re-derived."""
    minimum, maximum = ifc_bbox_to_gltf(0.0, 2.0, 0.0, 1.0, 5.0, 3.0)
    assert minimum == pytest.approx([0.0, 0.0, -5.0])
    assert maximum == pytest.approx([1.0, 3.0, -2.0])
    assert all(low <= high for low, high in zip(minimum, maximum))


def _entry(file_id: str, *, storey: str = "Level 1", key: str | None = None) -> dict:
    return {
        "file_id": file_id,
        "source_ifc_file": f"{file_id}.ifc",
        "unit_scale_to_metre": 0.3048,
        "generated_at": "2026-01-01T00:00:00+00:00",
        "failed_elements": 0,
        "scenes": [{
            "scene_key": key or scene_key(storey),
            "storey_name": storey,
            "element_count": 12,
            "bytes": 2048,
        }],
    }


def test_manifest_merge_preserves_sibling_files(scenes_root):
    """Ingesting one discipline must not invalidate another's scenes."""
    merge_manifest("proj", _entry("arch"))
    manifest = merge_manifest("proj", _entry("mep", storey="Level 2"))
    assert sorted(manifest["files"]) == ["arch", "mep"]
    assert manifest["files"]["arch"]["scenes"][0]["storey_name"] == "Level 1"


def test_manifest_merge_replaces_the_same_file(scenes_root):
    merge_manifest("proj", _entry("arch", storey="Level 1"))
    manifest = merge_manifest("proj", _entry("arch", storey="Level 9"))
    assert len(manifest["files"]) == 1
    assert manifest["files"]["arch"]["scenes"][0]["storey_name"] == "Level 9"


def test_manifest_prune_drops_files_no_longer_in_the_graph(scenes_root):
    """A file removed from the graph must stop being offered as viewable."""
    merge_manifest("proj", _entry("arch"))
    merge_manifest("proj", _entry("mep", storey="Level 2"))
    manifest = prune_manifest("proj", ["arch"])
    assert list(manifest["files"]) == ["arch"]


def test_manifest_prune_also_deletes_the_stale_geometry(scenes_root):
    """Orphaned scenes would let the viewer show a model the answers cannot use."""
    for file_id in ("arch", "mep"):
        merge_manifest("proj", _entry(file_id))
        path = scene_path("proj", file_id, scene_key("Level 1"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"glTF-stub")

    prune_manifest("proj", ["arch"])
    assert scene_path("proj", "arch", scene_key("Level 1")).exists()
    assert not scene_path("proj", "mep", scene_key("Level 1")).exists()
    assert not (scenes_root / "proj" / "mep").exists()


def test_manifest_prune_is_a_no_op_when_nothing_is_stale(scenes_root):
    merge_manifest("proj", _entry("arch"))
    path = scene_path("proj", "arch", scene_key("Level 1"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"glTF-stub")
    prune_manifest("proj", ["arch", "mep"])
    assert path.exists()


def test_manifest_is_written_atomically_and_reads_back(scenes_root):
    merge_manifest("proj", _entry("arch"))
    on_disk = json.loads((scenes_root / "proj" / "manifest.json").read_text(encoding="utf-8"))
    assert on_disk == read_manifest("proj")
    assert on_disk["axis_mapping"] == scene_export.AXIS_MAPPING
    assert not list((scenes_root / "proj").glob("*.tmp"))


def test_unreadable_manifest_is_treated_as_empty(scenes_root):
    """A corrupt manifest must not break the viewer or the ingest pipeline."""
    target = scenes_root / "proj" / "manifest.json"
    target.parent.mkdir(parents=True)
    target.write_text("{not json", encoding="utf-8")
    manifest = read_manifest("proj")
    assert manifest["files"] == {}
    assert manifest["project_id"] == "proj"


def test_missing_manifest_reads_as_empty(scenes_root):
    assert read_manifest("never-ingested")["files"] == {}


def test_scene_path_is_project_and_file_scoped(scenes_root):
    path = scene_path("proj", "arch", "abc123")
    assert path == scenes_root / "proj" / "arch" / "abc123.glb"


def test_gltf_length_check_rejects_a_truncated_or_foreign_file(tmp_path):
    """The published size must be provable, not assumed.

    The IfcOpenShell glTF serializer flushes its last buffer from a C++
    destructor, so a scene measured too early is short. The glTF header records
    the total file length, which makes that state detectable.
    """
    from bim_graph.scene_export import gltf_declared_length

    good = tmp_path / "good.glb"
    good.write_bytes(struct.pack("<III", 0x46546C67, 2, 40) + b"x" * 28)
    assert gltf_declared_length(good) == 40

    truncated = tmp_path / "short.glb"
    truncated.write_bytes(struct.pack("<III", 0x46546C67, 2, 999) + b"x" * 28)
    assert gltf_declared_length(truncated) != truncated.stat().st_size

    assert gltf_declared_length(tmp_path / "absent.glb") is None
    not_gltf = tmp_path / "other.glb"
    not_gltf.write_bytes(b"not a glb file at all........")
    assert gltf_declared_length(not_gltf) is None


@pytest.mark.skipif(not REFERENCE_IFC.exists(), reason="reference IFC file not available")
def test_export_writes_one_scene_per_storey_with_guid_named_nodes(scenes_root):
    """End-to-end: real IFC in, per-storey glTF out, nodes named by IFC GlobalId.

    The GlobalId naming is what lets the viewer highlight an element by the same
    identifier the graph stores, so it is asserted rather than assumed.
    """
    import ifcopenshell
    import ifcopenshell.util.element as element_util

    from extract_graph import extract, geom_settings

    model = ifcopenshell.open(str(REFERENCE_IFC))
    storeys = [
        name for name in NEAR_COLLIDING_STOREYS
        if any(
            (container := element_util.get_container(item)) is not None and container.Name == name
            for item in model.by_type("IfcWall")
        )
    ]
    if not storeys:
        pytest.skip("Reference model does not contain the expected storeys.")

    writer = scene_export.SceneWriter(
        model, geom_settings,
        project_id="test-project", file_id="reference",
        source_ifc_file=REFERENCE_IFC.name, unit_scale_to_metre=0.3048,
    )
    try:
        node_rows, _ = extract(
            model, storey_filter=storeys, type_filter=["IfcWall", "IfcSlab"],
            project_id="test-project", source_ifc_file=REFERENCE_IFC.name,
            source_file_id="reference", scene_writer=writer,
        )
    finally:
        entry = writer.close()

    assert entry["scenes"], "Expected at least one exported scene."
    assert entry["failed_elements"] == 0
    assert {scene["storey_name"] for scene in entry["scenes"]} <= set(storeys)

    exported_guids = set()
    for scene in entry["scenes"]:
        path = scene_path("test-project", "reference", scene["scene_key"])
        # The published size must equal both the file on disk and the length the
        # glTF header declares, proving the serializer finished flushing.
        assert scene["bytes"] == path.stat().st_size
        assert scene["bytes"] == scene_export.gltf_declared_length(path)
        with path.open("rb") as handle:
            magic, version, _length = struct.unpack("<III", handle.read(12))
            assert magic == 0x46546C67 and version == 2
            chunk_length, _ = struct.unpack("<II", handle.read(8))
            gltf = json.loads(handle.read(chunk_length))
        assert len(gltf["nodes"]) == scene["element_count"]
        exported_guids.update(node["name"] for node in gltf["nodes"] if "name" in node)

    graph_guids = {row["ifc_guid"] for row in node_rows if row["min_x"] != ""}
    assert exported_guids <= graph_guids, "Every exported node must name a graph element."
