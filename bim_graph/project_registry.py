"""Thread-safe manifest for project-scoped IFC upload and ingestion state."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(os.getenv("BIM_PROJECT_STORAGE_DIR", "dataset/ifc/projects"))
REGISTRY_PATH = Path(os.getenv("BIM_PROJECT_REGISTRY", "dataset/ifc/project_registry.json"))
_LOCK = threading.RLock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_id(value: str, fallback: str = "default-project") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", (value or "").strip()).strip("-._")
    return cleaned[:80] or fallback


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _empty() -> dict[str, Any]:
    return {"schema_version": 1, "projects": {}}


def _read() -> dict[str, Any]:
    if not REGISTRY_PATH.exists():
        return _empty()
    try:
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _empty()


def _write(data: dict[str, Any]) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = REGISTRY_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, REGISTRY_PATH)


def register_uploaded_file(
    project_id: str,
    source_path: str | Path,
    original_filename: str,
    *,
    discipline: str = "unspecified",
) -> dict[str, Any]:
    project_id = safe_id(project_id)
    source_path = Path(source_path)
    digest = file_sha256(source_path)
    file_id = f"{safe_id(Path(original_filename).stem, 'model')}-{digest[:12]}"
    with _LOCK:
        data = _read()
        project = data["projects"].setdefault(project_id, {
            "project_id": project_id,
            "name": project_id,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "files": {},
        })
        existing = project["files"].get(file_id, {})
        record = {
            **existing,
            "file_id": file_id,
            "project_id": project_id,
            "filename": original_filename,
            "stored_path": str(source_path.resolve()),
            "sha256": digest,
            "size_bytes": source_path.stat().st_size,
            "discipline": discipline or "unspecified",
            "status": existing.get("status", "uploaded"),
            "processing_status": existing.get("processing_status", "ready_for_ingestion"),
            "uploaded_at": existing.get("uploaded_at", utc_now()),
            "error": None,
        }
        project["files"][file_id] = record
        project["updated_at"] = utc_now()
        _write(data)
        return deepcopy(record)


def update_file(project_id: str, file_id: str, **updates: Any) -> dict[str, Any]:
    with _LOCK:
        data = _read()
        project = data.get("projects", {}).get(project_id)
        if not project or file_id not in project.get("files", {}):
            raise KeyError(f"Unknown IFC file {project_id}/{file_id}")
        project["files"][file_id].update(updates)
        project["updated_at"] = utc_now()
        _write(data)
        return deepcopy(project["files"][file_id])


def remove_files(project_id: str, file_ids: list[str]) -> list[dict[str, Any]]:
    """Atomically remove selected file records from one project.

    Graph, scene, and physical-file cleanup are deliberately orchestrated by
    the API while holding the pipeline lock. This function owns only registry
    mutation and never touches sibling projects or unselected records.
    """
    wanted = set(file_ids)
    if not wanted:
        return []
    with _LOCK:
        data = _read()
        projects = data.get("projects", {})
        project = projects.get(project_id)
        if not project:
            return []
        files = project.get("files", {})
        removed = [deepcopy(record) for key, record in files.items() if key in wanted]
        for file_id in wanted:
            files.pop(file_id, None)
        if not files:
            projects.pop(project_id, None)
        elif removed:
            project["updated_at"] = utc_now()
        if removed:
            _write(data)
        return sorted(removed, key=lambda record: record.get("uploaded_at", ""))


def mark_all_files_not_in_graph() -> int:
    """Synchronize registry state after a deliberate full-graph reset.

    A global Neo4j wipe removes every project's models, not only the project in
    the current request. Leaving sibling registry records marked ``ingested``
    would make the UI and sustainability scope resolver claim those absent
    models were still available. The caller re-marks the newly loaded files as
    ingested after this atomic transition.
    """
    changed = 0
    with _LOCK:
        data = _read()
        now = utc_now()
        for project in data.get("projects", {}).values():
            project_changed = False
            for record in project.get("files", {}).values():
                if record.get("status") == "ingested" or record.get("processing_status") in {
                    "imported", "analyzed", "extracting",
                }:
                    record["status"] = "uploaded"
                    record["processing_status"] = "not_in_graph"
                    record["error"] = None
                    changed += 1
                    project_changed = True
            if project_changed:
                project["updated_at"] = now
        if changed:
            _write(data)
    return changed


def get_files(project_id: str, file_ids: list[str] | None = None) -> list[dict[str, Any]]:
    with _LOCK:
        project = _read().get("projects", {}).get(project_id, {})
        records = list(project.get("files", {}).values())
    if file_ids:
        wanted = set(file_ids)
        records = [record for record in records if record["file_id"] in wanted]
    return sorted(records, key=lambda record: record.get("uploaded_at", ""))


def list_projects(*, ingested_only: bool = False) -> list[dict[str, Any]]:
    with _LOCK:
        projects = deepcopy(_read().get("projects", {}))
    output = []
    for project in projects.values():
        files = list(project.pop("files", {}).values())
        if ingested_only:
            files = [record for record in files if record.get("status") == "ingested"]
        project["files"] = sorted(files, key=lambda record: record.get("uploaded_at", ""))
        project["file_count"] = len(files)
        if files or not ingested_only:
            output.append(project)
    return sorted(output, key=lambda project: project.get("updated_at", ""), reverse=True)


def list_unregistered_ifc_files() -> list[dict[str, Any]]:
    """Expose legacy disk files honestly without claiming they were imported."""
    with _LOCK:
        registered = {
            str(Path(record["stored_path"]).resolve())
            for project in _read().get("projects", {}).values()
            for record in project.get("files", {}).values()
        }
    root = PROJECT_ROOT.parent
    return [
        {
            "filename": path.name,
            "stored_path": str(path.resolve()),
            "status": "legacy_unregistered",
            "processing_status": "upload_again_to_register",
            "size_bytes": path.stat().st_size,
            "modified_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
        }
        for path in sorted(root.glob("*.ifc"))
        if str(path.resolve()) not in registered
    ]
