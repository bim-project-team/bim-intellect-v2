"""Auditable JSON, CSV, and printable HTML sustainability reports."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from html import escape
from typing import Any

REPORT_SCHEMA_VERSION = "sustainability-report-v1"
REPORT_DISCLAIMER = (
    "Embodied-carbon values are deterministic estimates based only on the selected "
    "IFC evidence and supplied carbon factors. LEED-oriented findings are not "
    "certification, credit awards, or a complete LEED evaluation."
)


def _run_value(run: dict[str, Any], snake: str, camel: str, default=None):
    value = run.get(snake)
    return run.get(camel, default) if value is None else value


def _factor_citations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    citations: dict[tuple, dict[str, Any]] = {}
    for row in rows:
        if not row.get("factor_id"):
            continue
        key = (
            row.get("factor_record_id") or row.get("factor_id"),
            row.get("factor_dataset_hash"),
        )
        citations[key] = {
            "factor_record_id": row.get("factor_record_id"),
            "factor_id": row.get("factor_id"),
            "factor_value": row.get("factor_value"),
            "factor_unit": row.get("factor_unit"),
            "source": row.get("factor_source"),
            "source_version": row.get("factor_source_version"),
            "dataset_version": row.get("factor_dataset_version"),
            "dataset_hash": row.get("factor_dataset_hash"),
            "region": row.get("factor_region"),
            "year": row.get("factor_year"),
        }
    return sorted(
        citations.values(),
        key=lambda item: (str(item.get("source")), str(item.get("factor_id"))),
    )


def _selected_models(summary: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        file_id = str(row.get("source_file_id") or "")
        if not file_id:
            continue
        by_id.setdefault(file_id, {
            "source_file_id": file_id,
            "source_ifc_file": row.get("source_ifc_file"),
            "discipline": row.get("discipline"),
        })
    return [
        by_id.get(str(file_id), {
            "source_file_id": str(file_id),
            "source_ifc_file": None,
            "discipline": None,
        })
        for file_id in summary.get("file_ids", [])
    ]


def build_sustainability_report(
    summary: dict[str, Any],
    elements: list[dict[str, Any]],
    materials: list[dict[str, Any]],
    leed_findings: list[dict[str, Any]] | None = None,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Assemble one canonical report without performing any carbon arithmetic."""
    run = dict(summary.get("run") or {})
    top = sorted(
        (dict(row) for row in elements if row.get("carbon_kgco2e") is not None),
        key=lambda row: (-float(row["carbon_kgco2e"]), str(row.get("element_id", ""))),
    )[:25]
    quality_rows = [
        dict(row) for row in elements
        if row.get("calculation_status") != "calculated_explicit"
    ]
    statuses = dict(summary.get("calculation_status_counts") or {})
    quality = {
        "missing_material": statuses.get("missing_material", 0),
        "missing_quantity": statuses.get("missing_quantity", 0),
        "ambiguous_quantity": statuses.get("ambiguous_quantity", 0),
        "ambiguous_material_mapping": statuses.get("ambiguous_mapping", 0),
        "missing_carbon_factor": statuses.get("unmatched_material", 0),
        "unit_incompatibility": statuses.get("incompatible_unit", 0),
        "derived_or_estimated_quantity": statuses.get("calculated_estimate", 0),
        "items": quality_rows,
    }
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "project": {
            "project_id": summary.get("project_id"),
            "selected_ifc_models": _selected_models(summary, elements),
        },
        "analysis": {
            "run_id": run.get("id"),
            "analysis_date": _run_value(run, "completed_at", "completedAt"),
            "calculation_methodology": (
                summary.get("methodology_version")
                or _run_value(run, "methodology_version", "methodologyVersion")
            ),
            "extractor_version": _run_value(run, "extractor_version", "extractorVersion"),
            "carbon_factor_dataset_version": _run_value(
                run, "factor_dataset_version", "factorDatasetVersion"
            ),
            "carbon_factor_dataset_hash": _run_value(
                run, "factor_dataset_hash", "factorDatasetHash"
            ),
            "carbon_factor_region": _run_value(run, "factor_region", "factorRegion"),
            "geometry_derived_enabled": bool(
                _run_value(run, "allow_geometry_derived", "allowGeometryDerived", False)
            ),
        },
        "summary": {
            key: summary.get(key) for key in (
                "calculated_carbon_kgco2e",
                "total_evaluated_elements",
                "elements_with_calculated_carbon",
                "elements_not_evaluated",
                "elements_with_explicit_quantities",
                "elements_with_estimated_quantities",
                "explicit_quantity_coverage_ratio",
                "estimated_quantity_coverage_ratio",
                "element_coverage_ratio",
                "unmatched_materials",
                "ambiguous_mappings",
            )
        },
        "breakdowns": {
            "material": list(summary.get("by_material") or []),
            "ifc_type": list(summary.get("by_ifc_type") or []),
            "discipline": list(summary.get("by_discipline") or []),
            "source_ifc_file": list(summary.get("by_source_ifc_file") or []),
        },
        "materials": [dict(item) for item in materials],
        "top_contributors": top,
        "data_quality": quality,
        "carbon_factor_citations": _factor_citations(elements),
        "leed_oriented_findings": [dict(item) for item in (leed_findings or [])],
        "limitations": list(summary.get("limitations") or []) + [REPORT_DISCLAIMER],
    }


def report_to_json(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)


def report_to_csv(report: dict[str, Any]) -> str:
    output = io.StringIO(newline="")
    fields = [
        "section", "key", "value", "element_id", "ifc_guid", "ifc_type",
        "source_ifc_file", "discipline", "material", "quantity", "quantity_unit",
        "quantity_source", "factor_id", "factor", "factor_unit", "factor_source", "kgco2e",
        "assessment_status", "citation",
    ]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()

    def write(section: str, key: str = "", value: Any = "", **values):
        writer.writerow({"section": section, "key": key, "value": value, **values})

    write("report", "schema_version", report["schema_version"])
    write("report", "generated_at", report["generated_at"])
    write("project", "project_id", report["project"]["project_id"])
    for model in report["project"]["selected_ifc_models"]:
        write("selected_ifc_model", model["source_file_id"], model.get("source_ifc_file") or "")
    for key, value in report["analysis"].items():
        write("analysis", key, value)
    for key, value in report["summary"].items():
        write("summary", key, value)
    for dimension, items in report["breakdowns"].items():
        for item in items:
            write(
                f"breakdown:{dimension}", str(item.get("key", "")),
                item.get("carbon_kgco2e", ""),
            )
    for item in report["top_contributors"]:
        write(
            "top_contributor",
            element_id=item.get("element_id", ""),
            ifc_guid=item.get("ifc_guid", ""),
            ifc_type=item.get("ifc_type", ""),
            source_ifc_file=item.get("source_ifc_file", ""),
            discipline=item.get("discipline", ""),
            material=item.get("material_raw") or item.get("material_normalized") or "",
            quantity=item.get("normalized_quantity", ""),
            quantity_unit=item.get("normalized_quantity_unit", ""),
            quantity_source=item.get("quantity_source", ""),
            factor_id=item.get("factor_id", ""),
            factor=item.get("factor_value", ""),
            factor_unit=item.get("factor_unit", ""),
            factor_source=item.get("factor_source", ""),
            kgco2e=item.get("carbon_kgco2e", ""),
        )
    for item in report["carbon_factor_citations"]:
        write(
            "carbon_factor",
            key=item.get("factor_id", ""),
            value=item.get("dataset_version", ""),
            factor_id=item.get("factor_id", ""),
            factor=item.get("factor_value", ""),
            factor_unit=item.get("factor_unit", ""),
            factor_source=item.get("source", ""),
        )
    for key, value in report["data_quality"].items():
        if key != "items":
            write("data_quality", key, value)
    for finding in report["leed_oriented_findings"]:
        citations = "; ".join(
            f"{item.get('document_id') or item.get('source')} / "
            f"{item.get('section_id')} / p.{item.get('page_number')}"
            for item in finding.get("document_citations", [])
        )
        write(
            "leed_oriented_finding",
            key=finding.get("criterion", ""),
            value=finding.get("evidence", ""),
            assessment_status=finding.get("assessment_status", ""),
            citation=citations,
        )
    for limitation in report["limitations"]:
        write("limitation", value=limitation)
    return output.getvalue()


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    head = "".join(f"<th>{escape(str(item))}</th>" for item in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{escape('' if value is None else str(value))}</td>" for value in row) + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def report_to_html(report: dict[str, Any]) -> str:
    project = report["project"]
    analysis = report["analysis"]
    summary = report["summary"]
    breakdown_sections = []
    for dimension, items in report["breakdowns"].items():
        breakdown_sections.append(
            f"<h3>{escape(dimension.replace('_', ' ').title())}</h3>"
            + _table(
                ["Group", "kgCO2e", "Elements", "Coverage"],
                [[
                    item.get("key"), item.get("carbon_kgco2e"),
                    item.get("element_count"), item.get("element_coverage_ratio"),
                ] for item in items],
            )
        )
    contributor_table = _table(
        ["Element", "IFC type", "GUID", "Source", "Material", "Quantity", "Factor", "kgCO2e"],
        [[
            item.get("element_name") or item.get("element_id"), item.get("ifc_type"),
            item.get("ifc_guid"), item.get("source_ifc_file"),
            item.get("material_raw") or item.get("material_normalized"),
            f"{item.get('normalized_quantity')} {item.get('normalized_quantity_unit')}",
            f"{item.get('factor_value')} {item.get('factor_unit')}",
            item.get("carbon_kgco2e"),
        ] for item in report["top_contributors"]],
    )
    finding_table = _table(
        ["Criterion / question", "Status", "Evidence", "Document citation", "Missing project data"],
        [[
            item.get("criterion"), item.get("assessment_status"), item.get("evidence"),
            "; ".join(
                f"{source.get('document_id') or source.get('source')} — "
                f"{source.get('section_id')}, page {source.get('page_number')}"
                for source in item.get("document_citations", [])
            ),
            ", ".join(item.get("missing_project_data", [])),
        ] for item in report["leed_oriented_findings"]],
    )
    quality_rows = [
        [key.replace("_", " "), value]
        for key, value in report["data_quality"].items() if key != "items"
    ]
    limitation_items = "".join(
        f"<li>{escape(str(item))}</li>" for item in report["limitations"]
    )
    model_items = "".join(
        f"<li>{escape(str(item.get('source_ifc_file') or item.get('source_file_id')))}</li>"
        for item in project["selected_ifc_models"]
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Sustainability report</title>
<style>
body{{font:14px Arial,sans-serif;color:#181818;max-width:1180px;margin:32px auto;padding:0 20px}}
h1,h2,h3{{margin-top:1.4em}} .notice{{border:1px solid #999;padding:12px;background:#f5f5f5}}
table{{border-collapse:collapse;width:100%;margin:12px 0 24px}}th,td{{border:1px solid #bbb;padding:7px;text-align:start;vertical-align:top}}
th{{background:#eee}} code{{font-family:monospace}} @media print{{body{{margin:0;max-width:none}}}}
</style></head><body>
<h1>Sustainability and embodied-carbon report</h1>
<p class="notice">{escape(REPORT_DISCLAIMER)}</p>
<h2>Project and analysis</h2>
<p><strong>Project:</strong> {escape(str(project.get("project_id") or ""))}<br>
<strong>Run:</strong> <code>{escape(str(analysis.get("run_id") or ""))}</code><br>
<strong>Analysis date:</strong> {escape(str(analysis.get("analysis_date") or ""))}<br>
<strong>Methodology:</strong> {escape(str(analysis.get("calculation_methodology") or ""))}<br>
<strong>Factor dataset:</strong> {escape(str(analysis.get("carbon_factor_dataset_version") or ""))}
 / <code>{escape(str(analysis.get("carbon_factor_dataset_hash") or ""))}</code></p>
<ul>{model_items}</ul>
<h2>Project summary</h2>
{_table(["Metric", "Value"], [[key.replace("_", " "), value] for key, value in summary.items()])}
<h2>Carbon breakdown</h2>
{"".join(breakdown_sections)}
<h2>Top element/material contributions</h2>
{contributor_table}
<h2>Data quality</h2>
{_table(["Issue", "Count"], quality_rows)}
<h2>LEED-oriented findings</h2>
{finding_table}
<h2>Limitations</h2><ul>{limitation_items}</ul>
</body></html>"""
