from __future__ import annotations

import csv
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main import app
from sustainability.calculator import calculate_elements, summarize_results
from sustainability.factors import CarbonFactorDatasetError, CarbonFactorRepository
from sustainability.ifc_extractor import (
    _flatten_material_definition,
    derive_bound_quantities,
    extract_explicit_quantities,
    extract_material_uses,
    validate_explicit_quantity_plausibility,
)
from sustainability.models import CarbonFactor, ElementEvidence, FactorMatch, MaterialUse, QuantityEvidence
from sustainability.repository import SustainabilityRepository, cleanup_project_sustainability
from sustainability.service import SustainabilityService
from sustainability.units import (
    UnitNormalizationError,
    dimensions_compatible,
    normalize_ifc_value,
    normalize_text_quantity,
)
from sustainability.normalization import material_category, normalize_material_name


class FakeEntity:
    _next_id = 1

    def __init__(self, ifc_type, **values):
        self.ifc_type = ifc_type
        self._id = FakeEntity._next_id
        FakeEntity._next_id += 1
        for key, value in values.items():
            setattr(self, key, value)

    def is_a(self, requested=None):
        if requested is None:
            return self.ifc_type
        inheritance = {
            "IfcQuantityVolume": {"IfcPhysicalSimpleQuantity"},
            "IfcQuantityArea": {"IfcPhysicalSimpleQuantity"},
            "IfcQuantityLength": {"IfcPhysicalSimpleQuantity"},
            "IfcQuantityWeight": {"IfcPhysicalSimpleQuantity"},
        }
        return requested == self.ifc_type or requested in inheritance.get(self.ifc_type, set())

    def id(self):
        return self._id


def si_unit(name, prefix=None):
    return FakeEntity("IfcSIUnit", Name=name, Prefix=prefix)


def write_factors(path: Path, rows: list[dict]) -> Path:
    fields = [
        "factor_id", "material_id", "material_name", "aliases", "category",
        "factor_value", "factor_unit", "region", "source", "source_version",
        "year", "dataset_version", "notes", "enabled",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


def factor_row(**updates):
    value = {
        "factor_id": "verified-concrete-v1",
        "material_id": "concrete",
        "material_name": "Concrete",
        "aliases": "Concrete - Cast in Situ|Cast-In-Place Concrete",
        "category": "concrete",
        "factor_value": "100",
        "factor_unit": "kgCO2e/m3",
        "region": "test-region",
        "source": "Test fixture source",
        "source_version": "1",
        "year": "2025",
        "dataset_version": "test-v1",
        "notes": "Test-only factor; not environmental data",
        "enabled": "true",
    }
    value.update(updates)
    return value


def element(materials=None, quantities=None, *, element_id="p::f::g", file_id="f"):
    return ElementEvidence(
        element_id=element_id, ifc_guid="g", ifc_type="IfcWall", name="Wall",
        storey_name="L1", project_id="p", source_file_id=file_id,
        source_ifc_file=f"{file_id}.ifc", discipline="architecture",
        materials=list(materials or []), quantities=list(quantities or []),
    )


def material(name="Concrete - Cast in Situ", *, component="material", thickness=None, fraction=None, suffix="1"):
    normalized = normalize_material_name(name)
    return MaterialUse(
        id=f"use-{suffix}", raw_name=name, normalized_name=normalized,
        category=material_category(normalized), association_type="IfcRelAssociatesMaterial",
        component_type=component, layer_thickness_m=thickness, fraction=fraction,
    )


def quantity(value=2.5, *, source="ifc_explicit", kind="volume", unit="m3", suffix="1"):
    return QuantityEvidence(
        id=f"quantity-{suffix}", quantity_value=value, quantity_type=kind,
        quantity_unit=unit, quantity_source=source, normalized_value=value,
        normalized_unit=unit, quantity_name="NetVolume" if kind == "volume" else "Weight",
        quantity_set_name="BaseQuantities", quality="authored" if source == "ifc_explicit" else "low",
    )


def test_material_normalization_keeps_deterministic_lookup_key_and_category():
    raw = "  Concrete - Cast_in Situ  "
    assert normalize_material_name(raw) == "concrete cast in situ"
    assert material_category(normalize_material_name(raw)) == "concrete"
    assert raw == "  Concrete - Cast_in Situ  "  # caller's authored value is untouched


def test_layer_profile_and_constituent_material_structures_are_flattened():
    concrete = FakeEntity("IfcMaterial", Name="Concrete")
    steel = FakeEntity("IfcMaterial", Name="Steel")
    relation = FakeEntity("IfcRelAssociatesMaterial")
    layer_set = FakeEntity("IfcMaterialLayerSet", MaterialLayers=[
        FakeEntity("IfcMaterialLayer", Material=concrete, LayerThickness=200),
        FakeEntity("IfcMaterialLayer", Material=steel, LayerThickness=10),
    ])
    usage = FakeEntity("IfcMaterialLayerSetUsage", ForLayerSet=layer_set)
    layers = _flatten_material_definition(
        usage, element_id="e", association=relation, association_level="occurrence",
        length_scale_to_m=0.001,
    )
    profile_set = FakeEntity("IfcMaterialProfileSet", MaterialProfiles=[
        FakeEntity("IfcMaterialProfile", Material=steel),
    ])
    profiles = _flatten_material_definition(
        profile_set, element_id="e", association=relation, association_level="occurrence",
        length_scale_to_m=1,
    )
    constituent_set = FakeEntity("IfcMaterialConstituentSet", MaterialConstituents=[
        FakeEntity("IfcMaterialConstituent", Material=concrete, Fraction=0.7),
        FakeEntity("IfcMaterialConstituent", Material=steel, Fraction=0.3),
    ])
    constituents = _flatten_material_definition(
        constituent_set, element_id="e", association=relation, association_level="occurrence",
        length_scale_to_m=1,
    )
    material_list = _flatten_material_definition(
        FakeEntity("IfcMaterialList", Materials=[concrete, steel]),
        element_id="e", association=relation, association_level="occurrence",
        length_scale_to_m=1,
    )
    assert [(item.raw_name, item.layer_thickness_m) for item in layers] == [
        ("Concrete", 0.2), ("Steel", 0.01),
    ]
    assert profiles[0].component_type == "profile" and profiles[0].raw_name == "Steel"
    assert [item.fraction for item in constituents] == [0.7, 0.3]
    assert [item.component_type for item in material_list] == ["list_item", "list_item"]


def test_occurrence_material_overrides_inherited_type(monkeypatch):
    occurrence_material = FakeEntity("IfcMaterial", Name="Concrete")
    type_material = FakeEntity("IfcMaterial", Name="Steel")
    occurrence = FakeEntity("IfcElement", HasAssociations=[
        FakeEntity("IfcRelAssociatesMaterial", RelatingMaterial=occurrence_material),
    ])
    type_object = FakeEntity("IfcWallType", HasAssociations=[
        FakeEntity("IfcRelAssociatesMaterial", RelatingMaterial=type_material),
    ])
    monkeypatch.setattr("sustainability.ifc_extractor.element_util.get_type", lambda _e: type_object)
    uses = extract_material_uses(occurrence, "e", 1.0)
    assert [item.raw_name for item in uses] == ["Concrete"]
    assert uses[0].association_level == "occurrence"


def test_explicit_ifc_quantities_preserve_raw_and_normalized_units(monkeypatch):
    volume = FakeEntity(
        "IfcQuantityVolume", Name="NetVolume", VolumeValue=35.3146667,
        Unit=FakeEntity("IfcConversionBasedUnit", Name="CUBIC_FOOT", ConversionFactor=FakeEntity(
            "IfcMeasureWithUnit", ValueComponent=0.028316846592,
            UnitComponent=si_unit("CUBIC_METRE"),
        )),
    )
    qset = FakeEntity("IfcElementQuantity", Name="BaseQuantities", Quantities=[volume])
    owner = FakeEntity("IfcElement", IsDefinedBy=[
        FakeEntity("IfcRelDefinesByProperties", RelatingPropertyDefinition=qset),
    ])
    monkeypatch.setattr("sustainability.ifc_extractor.element_util.get_type", lambda _e: None)
    extracted = extract_explicit_quantities(owner, "e", object())
    assert len(extracted) == 1
    assert extracted[0].quantity_source == "ifc_explicit"
    assert extracted[0].quantity_value == pytest.approx(35.3146667)
    assert extracted[0].normalized_value == pytest.approx(1.0, rel=1e-6)
    assert extracted[0].normalized_unit == "m3"


def test_derived_bounds_are_si_estimates_only_when_explicit_dimension_is_missing():
    graph = {
        "element_id": "e", "ifc_type": "IfcWall",
        "min_x": 0, "min_y": 0, "min_z": 0,
        "max_x": 10, "max_y": 1, "max_z": 3,
    }
    # The extractor's CONVERT_BACK_UNITS=False contract means graph bounds are metres.
    derived = derive_bound_quantities(graph, length_scale_to_m=1.0, existing_types={"area"})
    assert {item.quantity_type for item in derived} == {"volume"}
    assert derived[0].quantity_source == "geometry_derived"
    assert derived[0].quality == "low"
    assert derived[0].normalized_value == pytest.approx(30.0)


def test_implausible_declared_ifc_quantity_is_retained_but_not_calculable():
    explicit = quantity(1_000_000_000, kind="volume")
    validate_explicit_quantity_plausibility([explicit], {
        "min_x": 0, "min_y": 0, "min_z": 0,
        "max_x": 10, "max_y": 1, "max_z": 3,
    }, length_scale_to_m=1.0)
    assert explicit.quantity_source == "ifc_explicit"
    assert explicit.quantity_value == 1_000_000_000
    assert "implausible" in explicit.error


def test_unit_normalization_and_dimension_mismatch_are_explicit():
    assert normalize_text_quantity(1, "ft3", "volume") == pytest.approx((0.028316846592, "m3"))
    value, unit, _ = normalize_ifc_value(1000, si_unit("GRAM"), "mass")
    assert value == pytest.approx(1.0) and unit == "kg"
    assert dimensions_compatible("mass", "kgCO2e/kg") is True
    assert dimensions_compatible("volume", "kgCO2e/kg") is False
    with pytest.raises(UnitNormalizationError):
        normalize_text_quantity(1, "bananas", "mass")


def test_factor_alias_matching_unmatched_and_ambiguous(tmp_path):
    path = write_factors(tmp_path / "factors.csv", [factor_row()])
    repository = CarbonFactorRepository(path)
    assert repository.match("Concrete - Cast in Situ", region="test-region").status == "matched"
    assert repository.match("Mystery material").status == "unmatched"

    ambiguous_path = write_factors(tmp_path / "ambiguous.csv", [
        factor_row(),
        factor_row(factor_id="other-concrete", factor_value="110"),
    ])
    assert CarbonFactorRepository(ambiguous_path).match("Concrete").status == "ambiguous"


def test_factor_repository_rejects_missing_provenance(tmp_path):
    path = write_factors(tmp_path / "bad.csv", [factor_row(source="")])
    with pytest.raises(CarbonFactorDatasetError, match="provenance"):
        CarbonFactorRepository(path)
    nonfinite = write_factors(tmp_path / "nonfinite.csv", [factor_row(factor_value="nan")])
    with pytest.raises(CarbonFactorDatasetError, match="value/unit/year"):
        CarbonFactorRepository(nonfinite)


def test_carbon_calculation_is_deterministic_and_reproducible(tmp_path):
    factors = CarbonFactorRepository(write_factors(tmp_path / "factors.csv", [factor_row()]))
    results = calculate_elements(
        [element([material()], [quantity(2.5)])], factors,
        run_id="run", region="test-region", dataset_version="test-v1",
    )
    result = results[0]
    assert result.calculation_status == "calculated_explicit"
    assert result.carbon_kgco2e == 250.0
    assert result.factor_id == "verified-concrete-v1"
    assert result.factor_source == "Test fixture source"
    assert result.quantity_source == "ifc_explicit"
    assert result.quantity_evidence_id == "quantity-1"
    assert result.allocation_share == 1.0
    assert result.factor_dataset_version == "test-v1"
    assert result.factor_dataset_hash == factors.dataset_hash
    assert result.factor_record_id != result.factor_id


def test_calculator_refuses_incompatible_factor_dimension():
    bad_factor = CarbonFactor(
        factor_id="bad", material_id="concrete", material_name="Concrete", aliases=(),
        category="concrete", factor_value=100, factor_unit="kgCO2e/kg",
        quantity_type="volume", region="test", source="test", source_version="1",
        year=2025, dataset_version="test",
    )

    class BadRepository:
        def match(self, *_args, **_kwargs):
            return FactorMatch("matched", bad_factor)

    result = calculate_elements(
        [element([material()], [quantity()])], BadRepository(), run_id="run",
    )[0]
    assert result.calculation_status == "incompatible_unit"
    assert result.carbon_kgco2e is None


def test_layer_allocation_is_estimated_and_missing_data_is_not_zero(tmp_path):
    factors = CarbonFactorRepository(write_factors(tmp_path / "factors.csv", [
        factor_row(), factor_row(
            factor_id="steel-volume", material_id="steel", material_name="Steel", aliases="",
            category="steel", factor_value="200",
        ),
    ]))
    layers = [
        material(component="layer", thickness=0.2, suffix="c"),
        material("Steel", component="layer", thickness=0.1, suffix="s"),
    ]
    calculated = calculate_elements([element(layers, [quantity(3)])], factors, run_id="r")
    assert {item.calculation_status for item in calculated} == {"calculated_estimate"}
    assert {item.quantity_source for item in calculated} == {"ifc_explicit"}
    assert sum(item.allocation_share for item in calculated) == pytest.approx(1.0)
    assert sum(item.carbon_kgco2e for item in calculated) == pytest.approx(400.0)

    missing = calculate_elements([element([], [])], factors, run_id="missing")[0]
    assert missing.calculation_status == "missing_material"
    assert missing.carbon_kgco2e is None

    ambiguous = calculate_elements([
        element([material()], [quantity(2.5), quantity(3.0, suffix="2")]),
    ], factors, run_id="ambiguous")[0]
    assert ambiguous.calculation_status == "ambiguous_quantity"
    assert ambiguous.carbon_kgco2e is None


def test_multi_file_and_project_aggregation_preserves_groups(tmp_path):
    factors = CarbonFactorRepository(write_factors(tmp_path / "factors.csv", [factor_row()]))
    results = calculate_elements([
        element([material(suffix="a")], [quantity(1, suffix="a")], element_id="p::a::1", file_id="a"),
        element([material(suffix="b")], [quantity(2, source="geometry_derived", suffix="b")], element_id="p::b::2", file_id="b"),
    ], factors, run_id="run")
    summary = summarize_results(results, {"project_id": "p", "file_ids": ["a", "b"]})
    assert summary["project_id"] == "p"
    assert summary["calculated_carbon_kgco2e"] == 300
    assert summary["explicit_carbon_kgco2e"] == 100
    assert summary["estimated_carbon_kgco2e"] == 200
    assert summary["by_project"][0]["key"] == "p"
    assert {item["key"] for item in summary["by_source_ifc_file"]} == {"a.ifc", "b.ifc"}

    partial = summarize_results([
        *results,
        calculate_elements([element([], [], element_id="p::a::missing", file_id="a")], factors, run_id="run")[0],
    ])
    file_a = next(item for item in partial["by_source_ifc_file"] if item["key"] == "a.ifc")
    assert file_a["element_count"] == 2
    assert file_a["calculated_element_count"] == 1
    assert file_a["element_coverage_ratio"] == pytest.approx(0.5)
    assert file_a["calculation_status_counts"]["missing_material"] == 1


class FakeNeo4jClient:
    calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def run(self, query, parameters=None):
        self.calls.append((query, parameters))
        return []

    def run_batched(self, query, rows, batch_size):
        self.calls.append((query, list(rows)))


def test_persistence_is_additive_and_reingestion_cleanup_is_project_scoped(tmp_path):
    FakeNeo4jClient.calls = []
    repository = SustainabilityRepository(client_factory=FakeNeo4jClient)
    factors = CarbonFactorRepository(write_factors(tmp_path / "factors.csv", [factor_row()]))
    evidence = [element([material()], [quantity()])]
    results = calculate_elements(evidence, factors, run_id="run-1")
    run = {
        "id": "run-1", "scope_key": "scope", "project_id": "p", "file_ids": ["f"],
        "status": "completed", "isCurrent": True, "factorDatasetHash": factors.dataset_hash,
    }
    repository.persist(run, evidence, results, factors.factors)
    cleanup_project_sustainability(FakeNeo4jClient(), "p")
    text = "\n".join(query for query, _ in FakeNeo4jClient.calls)
    assert "SustainabilityResult" in text and "MaterialUse" in text
    assert "USES_QUANTITY" in text
    factor_batches = [
        rows for query, rows in FakeNeo4jClient.calls
        if "MERGE (factor:CarbonFactor {id: row.id})" in query
    ]
    assert factor_batches[0][0]["id"] != factor_batches[0][0]["factor_id"]
    cleanup_calls = [params for query, params in FakeNeo4jClient.calls if "DETACH DELETE n" in query]
    assert cleanup_calls and all(item == {"project_id": "p"} for item in cleanup_calls)
    assert "CLASHES_WITH" not in text


def test_project_scoped_service_aggregates_selected_files_without_cross_project_leakage(monkeypatch, tmp_path):
    records = [
        {
            "project_id": "p", "file_id": "a", "filename": "a.ifc", "stored_path": "a.ifc",
            "discipline": "architecture", "status": "ingested",
            "coordinate_system": {"unit_scale_to_metre": 1.0, "project_guids": ["shared"],
                                  "site_guids": [], "sites": [], "contexts": [{}], "map_conversions": []},
        },
        {
            "project_id": "p", "file_id": "b", "filename": "b.ifc", "stored_path": "b.ifc",
            "discipline": "structure", "status": "ingested",
            "coordinate_system": {"unit_scale_to_metre": 1.0, "project_guids": ["shared"],
                                  "site_guids": [], "sites": [], "contexts": [{}], "map_conversions": []},
        },
    ]
    monkeypatch.setattr("sustainability.service.get_files", lambda project_id: records if project_id == "p" else [])
    monkeypatch.setattr("sustainability.service.update_file", lambda *args, **kwargs: None)

    class Repository:
        persisted = []

        def fetch_elements(self, project_id, file_ids):
            assert project_id == "p" and file_ids == ["a", "b"]
            return [
                {"element_id": "p::a::1", "ifc_guid": "1", "ifc_type": "IfcWall", "name": "A",
                 "storey_name": "L1", "source_file_id": "a"},
                {"element_id": "p::b::2", "ifc_guid": "2", "ifc_type": "IfcWall", "name": "B",
                 "storey_name": "L1", "source_file_id": "b"},
            ]

        def persist(self, run, evidence, results, factors):
            self.persisted.append((run, evidence, results, factors))

    def extractor(_path, graph_elements, record, **_kwargs):
        return [element(
            [material(suffix=record["file_id"])],
            [quantity(1 if record["file_id"] == "a" else 2, suffix=record["file_id"])],
            element_id=graph_elements[0]["element_id"], file_id=record["file_id"],
        )]

    factor_path = write_factors(tmp_path / "factors.csv", [factor_row()])
    repository = Repository()
    service = SustainabilityService(repository=repository, factor_path=factor_path, file_extractor=extractor)
    first = service.analyze("p", ["b", "a"], factor_dataset_version="test-v1", region="test-region")
    second = service.analyze("p", ["a", "b"], factor_dataset_version="test-v1", region="test-region")
    assert first["calculated_carbon_kgco2e"] == 300
    assert first["total_evaluated_elements"] == 2
    assert first["file_ids"] == ["a", "b"]
    assert first["project_id"] == "p"
    assert len(repository.persisted) == 2
    assert repository.persisted[0][0]["scope_key"] == repository.persisted[1][0]["scope_key"]
    assert repository.persisted[0][0]["id"] != repository.persisted[1][0]["id"]


def test_project_scope_rejects_files_from_another_project(monkeypatch, tmp_path):
    monkeypatch.setattr("sustainability.service.get_files", lambda _project_id: [])
    service = SustainabilityService(repository=object(), factor_path=tmp_path / "unused.csv")
    with pytest.raises(ValueError, match="Unknown file IDs"):
        service.resolve_scope("other-project", ["foreign-file"])


def test_sustainability_api_is_registered_and_template_factor_file_is_non_authoritative(monkeypatch):
    paths = app.openapi()["paths"]
    assert "/api/sustainability/analyze" in paths
    assert "/api/sustainability/summary" in paths
    response = TestClient(app).get("/api/sustainability/factors")
    assert response.status_code == 200
    assert response.json()["factor_count"] == 0
    assert response.json()["authoritative"] is False


def test_sustainability_api_forwards_exact_project_file_scope(monkeypatch):
    import api.sustainability_routes as sustainability_routes

    class Service:
        def analyze(self, project_id, file_ids, **options):
            return {"project_id": project_id, "file_ids": file_ids, "options": options}

        def summary(self, project_id, file_ids, run_id=None):
            return {"project_id": project_id, "file_ids": file_ids, "run_id": run_id}

    monkeypatch.setattr(sustainability_routes, "_SERVICE", Service())
    client = TestClient(app)
    analysis = client.post("/api/sustainability/analyze", json={
        "project_id": "p", "file_ids": ["a", "b"], "allow_geometry_derived": True,
    })
    summary = client.get("/api/sustainability/summary?project_id=p&file_id=a&file_id=b&run_id=r")
    assert analysis.status_code == 200
    assert analysis.json()["file_ids"] == ["a", "b"]
    assert analysis.json()["options"]["allow_geometry_derived"] is True
    assert summary.json() == {"project_id": "p", "file_ids": ["a", "b"], "run_id": "r"}
