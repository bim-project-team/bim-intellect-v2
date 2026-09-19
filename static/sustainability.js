// Project-scoped Sustainability workspace. Carbon values are rendered from
// backend deterministic results; this file performs no carbon calculation.
//
// Cross-file dependency: the functions below read app.js top-level bindings
// (projectIdInput, selectedIfcFileIds, ifcProjects) and the i18n shim t().
// Those are classic-script global lexical bindings, not window properties, so
// they resolve at call time. This file is loaded before app.js, but it only
// *calls* into them after DOMContentLoaded, once app.js has initialized.

const sustainabilityProject = document.getElementById("sustainability-project");
const sustainabilityScopeMode = document.getElementById("sustainability-scope-mode");
const sustainabilityScopeFiles = document.getElementById("sustainability-scope-files");
const sustainabilityDerivedToggle = document.getElementById("sustainability-derived-toggle");
const sustainabilityRunBtn = document.getElementById("sustainability-run-btn");
const sustainabilityRefreshBtn = document.getElementById("sustainability-refresh-btn");
const sustainabilityStatus = document.getElementById("sustainability-status");
const sustainabilityKpis = document.getElementById("sustainability-kpis");
const sustainabilityBreakdownSelect = document.getElementById("sustainability-breakdown-select");
const sustainabilityBreakdown = document.getElementById("sustainability-breakdown");
const sustainabilityContributorsBody = document.getElementById("sustainability-contributors-body");
const sustainabilityQuality = document.getElementById("sustainability-quality");
const sustainabilityQualityBody = document.getElementById("sustainability-quality-body");
const sustainabilityAssessmentBody = document.getElementById("sustainability-assessment-body");
const sustainabilityReportButtons = document.querySelectorAll(".sustainability-report-btn");

function currentSustainabilityScope() {
  const projectId = projectIdInput ? projectIdInput.value.trim() : "";
  const mode = sustainabilityScopeMode ? sustainabilityScopeMode.value : "selected";
  const fileIds = mode === "all" ? null : Array.from(selectedIfcFileIds).sort();
  return {
    projectId: projectId,
    mode: mode,
    fileIds: fileIds,
    key: projectId + "|" + mode + "|" + (fileIds || []).join(","),
  };
}

function projectFilesForSustainabilityScope(scope) {
  const project = ifcProjects.find(function (item) {
    return item.project_id === scope.projectId;
  });
  const files = project ? (project.files || []) : [];
  return scope.mode === "all"
    ? files.filter(function (item) { return item.status === "ingested"; })
    : files.filter(function (item) { return (scope.fileIds || []).includes(item.file_id); });
}

function renderSustainabilityScope() {
  if (!sustainabilityProject) return;
  const scope = currentSustainabilityScope();
  sustainabilityProject.textContent = scope.projectId || "—";
  const files = projectFilesForSustainabilityScope(scope);
  const names = files.map(function (item) { return item.filename || item.file_id; });
  sustainabilityScopeFiles.textContent = names.length
    ? t("sustainability.scopeFiles", { n: names.length, names: names.join(", ") })
    : t(scope.mode === "all" ? "pipeline.noModels" : "sustainability.noSelectedFiles");
}

function setSustainabilityStatus(key, kind, detail) {
  if (!sustainabilityStatus) return;
  sustainabilityStatus.classList.remove("success", "error");
  if (kind) sustainabilityStatus.classList.add(kind);
  const label = sustainabilityStatus.querySelector("span:last-child");
  if (label) label.textContent = detail ? t(key) + ": " + detail : t(key);
}

function formatSustainabilityNumber(value, maximumFractionDigits) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  const locale = window.i18n && window.i18n.lang === "fa" ? "fa-IR" : "en-GB";
  return new Intl.NumberFormat(locale, {
    maximumFractionDigits: maximumFractionDigits === undefined ? 2 : maximumFractionDigits,
  }).format(Number(value));
}

function formatSustainabilityPercent(value) {
  if (value === null || value === undefined) return "—";
  const locale = window.i18n && window.i18n.lang === "fa" ? "fa-IR" : "en-GB";
  return new Intl.NumberFormat(locale, {
    style: "percent", maximumFractionDigits: 1,
  }).format(Number(value));
}

function clearSustainabilityResults(messageKey) {
  const key = messageKey || "sustainability.noAnalysis";
  sustainabilityState.summary = null;
  sustainabilityState.elements = [];
  sustainabilityState.materials = [];
  sustainabilityState.findings = [];
  sustainabilityState.scopeKey = null;
  sustainabilityKpis.innerHTML = "";
  sustainabilityBreakdown.innerHTML = '<p class="hint">' + escapeHtml(t(key)) + "</p>";
  sustainabilityContributorsBody.innerHTML =
    '<tr><td colspan="8" class="empty">' + escapeHtml(t(key)) + "</td></tr>";
  sustainabilityQuality.innerHTML = "";
  sustainabilityQualityBody.innerHTML =
    '<tr><td colspan="5" class="empty">' + escapeHtml(t(key)) + "</td></tr>";
  sustainabilityAssessmentBody.innerHTML =
    '<tr><td colspan="5" class="empty">' + escapeHtml(t("sustainability.noFindings")) + "</td></tr>";
  sustainabilityReportButtons.forEach(function (button) { button.disabled = true; });
}

function sustainabilityQueryUrl(path, scope, runId) {
  const url = new URL(path, window.location.origin);
  url.searchParams.set("project_id", scope.projectId);
  (scope.fileIds || []).forEach(function (id) { url.searchParams.append("file_id", id); });
  if (runId) url.searchParams.set("run_id", runId);
  return url;
}

async function fetchSustainabilityJSON(url, options) {
  const response = await fetch(url, options);
  const data = await response.json().catch(function () { return {}; });
  if (!response.ok) {
    const error = new Error(formatApiError(data.detail, response.statusText));
    error.status = response.status;
    error.code = data.detail && data.detail.code;
    throw error;
  }
  return data;
}

function assertSustainabilityScope(summary, scope) {
  if (String(summary.project_id || "") !== scope.projectId) {
    throw new Error("Sustainability response project does not match the active project.");
  }
  if (scope.fileIds) {
    const requested = scope.fileIds.slice().sort().join("|");
    const returned = (summary.file_ids || []).slice().sort().join("|");
    if (requested !== returned) {
      throw new Error("Sustainability response IFC scope does not match the selected files.");
    }
  }
}

async function loadAllSustainabilityElements(scope, runId) {
  const items = [];
  let offset = 0;
  const limit = 1000;
  for (let page = 0; page < 100; page += 1) {
    const url = sustainabilityQueryUrl("/api/sustainability/elements", scope, runId);
    url.searchParams.set("limit", String(limit));
    url.searchParams.set("offset", String(offset));
    const data = await fetchSustainabilityJSON(url);
    items.push.apply(items, data.items || []);
    offset += (data.items || []).length;
    if (offset >= Number(data.total || 0) || !(data.items || []).length) break;
  }
  return items;
}

function renderSustainabilityKpis(summary) {
  const calculated = Number(summary.elements_with_calculated_carbon || 0);
  const carbon = calculated > 0
    ? formatSustainabilityNumber(summary.calculated_carbon_kgco2e, 3) + " kgCO2e"
    : "—";
  const items = [
    ["sustainability.estimatedCarbon", carbon, Number(summary.estimated_carbon_kgco2e || 0) > 0 ? t("sustainability.estimated") : ""],
    ["sustainability.elementsEvaluated", formatSustainabilityNumber(calculated, 0), ""],
    ["sustainability.elementsNotEvaluated", formatSustainabilityNumber(summary.elements_not_evaluated, 0), ""],
    ["sustainability.explicitCoverage", formatSustainabilityPercent(summary.explicit_quantity_coverage_ratio), ""],
    ["sustainability.estimatedCoverage", formatSustainabilityPercent(summary.estimated_quantity_coverage_ratio), ""],
    ["sustainability.unmatchedMaterials", formatSustainabilityNumber(summary.unmatched_materials, 0), ""],
  ];
  sustainabilityKpis.innerHTML = items.map(function (item) {
    return '<div class="sustainability-kpi">' +
      '<span class="sustainability-kpi-label">' + escapeHtml(t(item[0])) + "</span>" +
      '<strong class="sustainability-kpi-value">' + escapeHtml(item[1]) + "</strong>" +
      (item[2] ? "<small>" + escapeHtml(item[2]) + "</small>" : "") +
      "</div>";
  }).join("");
}

function renderSustainabilityBreakdown() {
  const summary = sustainabilityState.summary;
  if (!summary) return;
  const rows = (summary[sustainabilityBreakdownSelect.value] || []).slice(0, 20);
  const calculatedRows = rows.filter(function (item) {
    return Number(item.carbon_kgco2e || 0) > 0;
  });
  const max = Math.max.apply(null, calculatedRows.map(function (item) {
    return Number(item.carbon_kgco2e);
  }).concat([0]));
  if (!calculatedRows.length) {
    sustainabilityBreakdown.innerHTML =
      '<p class="hint">' + escapeHtml(t("sustainability.noAnalysis")) + "</p>";
    return;
  }
  sustainabilityBreakdown.innerHTML = calculatedRows.map(function (item) {
    const width = max > 0 ? Math.max(1, (Number(item.carbon_kgco2e) / max) * 100) : 0;
    return '<div class="carbon-bar-row">' +
      '<span class="carbon-bar-label" dir="auto">' + escapeHtml(item.key || "unknown") + "</span>" +
      '<div class="carbon-bar-track" aria-hidden="true"><div class="carbon-bar-fill" style="width:' +
      width.toFixed(2) + '%"></div></div>' +
      '<span class="carbon-bar-value">' +
      escapeHtml(formatSustainabilityNumber(item.carbon_kgco2e, 3)) + " kgCO2e</span></div>";
  }).join("");
}

function renderSustainabilityContributors(elements) {
  const rows = elements.filter(function (item) {
    return item.carbon_kgco2e !== null && item.carbon_kgco2e !== undefined;
  }).sort(function (a, b) {
    return Number(b.carbon_kgco2e) - Number(a.carbon_kgco2e);
  }).slice(0, 25);
  if (!rows.length) {
    sustainabilityContributorsBody.innerHTML =
      '<tr><td colspan="8" class="empty">' + escapeHtml(t("sustainability.noAnalysis")) + "</td></tr>";
    return;
  }
  sustainabilityContributorsBody.innerHTML = rows.map(function (item) {
    const estimated = item.calculation_status === "calculated_estimate";
    const quantity = item.normalized_quantity === null || item.normalized_quantity === undefined
      ? "—" : formatSustainabilityNumber(item.normalized_quantity, 5) + " " +
        (item.normalized_quantity_unit || "");
    const factor = item.factor_value === null || item.factor_value === undefined
      ? "—" : formatSustainabilityNumber(item.factor_value, 5) + " " + (item.factor_unit || "");
    return "<tr><td>" + autoDir("<strong>" + escapeHtml(item.element_name || item.element_id || "—") + "</strong>") +
      "</td><td>" + escapeHtml(item.ifc_type || "—") +
      '</td><td><small class="guid">' + escapeHtml(item.ifc_guid || "—") +
      "</small></td><td>" + autoDir(escapeHtml(item.source_ifc_file || "—")) +
      "</td><td>" + autoDir(escapeHtml(item.material_raw || item.material_normalized || "—")) +
      '</td><td class="num">' + escapeHtml(quantity) + '<br><span class="quantity-badge ' +
      (estimated ? "estimated" : "") + '">' +
      escapeHtml(t(estimated ? "sustainability.estimated" : "sustainability.ifcQuantity")) +
      '</span></td><td class="num" title="' + escapeHtml(item.factor_source || "") + '">' +
      escapeHtml(factor) + '</td><td class="num">' +
      escapeHtml(formatSustainabilityNumber(item.carbon_kgco2e, 3)) + "</td></tr>";
  }).join("");
}

function renderSustainabilityQuality(summary, elements) {
  const statuses = summary.calculation_status_counts || {};
  const cards = [
    ["sustainability.missingMaterial", statuses.missing_material || 0],
    ["sustainability.missingQuantity", statuses.missing_quantity || 0],
    ["sustainability.ambiguousQuantity", statuses.ambiguous_quantity || 0],
    ["sustainability.ambiguousMapping", statuses.ambiguous_mapping || 0],
    ["sustainability.missingFactor", statuses.unmatched_material || 0],
    ["sustainability.unitIncompatibility", statuses.incompatible_unit || 0],
    ["sustainability.derivedQuantity", statuses.calculated_estimate || 0],
  ];
  sustainabilityQuality.innerHTML = cards.map(function (item) {
    return '<div class="quality-card ' + (Number(item[1]) > 0 ? "has-issues" : "") + '">' +
      '<span class="quality-card-label">' + escapeHtml(t(item[0])) + "</span>" +
      '<strong class="quality-card-value">' +
      escapeHtml(formatSustainabilityNumber(item[1], 0)) + "</strong></div>";
  }).join("");
  const rows = elements.filter(function (item) {
    return item.calculation_status !== "calculated_explicit";
  }).slice(0, 100);
  if (!rows.length) {
    sustainabilityQualityBody.innerHTML =
      '<tr><td colspan="5" class="empty">' + escapeHtml(t("results.noResults")) + "</td></tr>";
    return;
  }
  sustainabilityQualityBody.innerHTML = rows.map(function (item) {
    return "<tr><td>" + autoDir(escapeHtml(item.element_name || item.element_id || "—")) +
      '<br><small class="guid">' + escapeHtml(item.ifc_guid || "") + "</small></td><td>" +
      autoDir(escapeHtml(item.source_ifc_file || "—")) + "</td><td>" +
      autoDir(escapeHtml(item.material_raw || item.material_normalized || "—")) +
      '</td><td><span class="status-badge">' +
      escapeHtml(item.calculation_status || "unknown") + "</span></td><td>" +
      autoDir(escapeHtml(item.calculation_note || "—")) + "</td></tr>";
  }).join("");
}

function sustainabilityDocumentCitation(source) {
  const clause = String(source.clause_id || "").toLowerCase();
  if (clause && !["unknown", "none", "null"].includes(clause)) {
    return "[Clause " + source.clause_id + ", Page " + source.page_number + "]";
  }
  return "[Document " + (source.document_id || source.source) + ", Section " +
    source.section_id + ", Page " + source.page_number + "]";
}

function renderSustainabilityAssessments(findings) {
  if (!findings.length) {
    sustainabilityAssessmentBody.innerHTML =
      '<tr><td colspan="5" class="empty">' +
      escapeHtml(t("sustainability.noFindings")) + "</td></tr>";
    return;
  }
  sustainabilityAssessmentBody.innerHTML = findings.map(function (item) {
    const citations = (item.document_citations || []).map(
      sustainabilityDocumentCitation
    ).join("\n") || "—";
    const status = item.assessment_status || "insufficient_evidence";
    return "<tr><td>" + autoDir(escapeHtml(item.criterion || "—")) +
      '</td><td><span class="status-badge status-' + escapeHtml(status) + '">' +
      escapeHtml(t("sustainability.status." + status)) + "</span></td>" +
      '<td class="evidence-cell">' +
      autoDir(escapeHtml(item.evidence || item.assessment_explanation || "—")) +
      '</td><td><small class="guid">' + escapeHtml(citations) + "</small></td><td>" +
      autoDir(escapeHtml((item.missing_project_data || []).join(", ") || "—")) +
      "</td></tr>";
  }).join("");
}

function renderSustainability(summary, elements, materials, findings, scopeKey) {
  sustainabilityState.scopeKey = scopeKey;
  sustainabilityState.summary = summary;
  sustainabilityState.elements = elements;
  sustainabilityState.materials = materials;
  sustainabilityState.findings = findings;
  renderSustainabilityKpis(summary);
  renderSustainabilityBreakdown();
  renderSustainabilityContributors(elements);
  renderSustainabilityQuality(summary, elements);
  renderSustainabilityAssessments(findings);
  sustainabilityReportButtons.forEach(function (button) { button.disabled = false; });
  const partial = Number(summary.elements_not_evaluated || 0) > 0 ||
    Number(summary.estimated_quantity_results || 0) > 0 ||
    Number(summary.unmatched_materials || 0) > 0 ||
    Number(summary.ambiguous_mappings || 0) > 0;
  setSustainabilityStatus(
    partial ? "sustainability.partial" : "sustainability.success", "success"
  );
}

async function loadSustainability() {
  const scope = currentSustainabilityScope();
  renderSustainabilityScope();
  clearSustainabilityResults();
  if (!scope.projectId) {
    setSustainabilityStatus("sustainability.noProject", "error");
    return;
  }
  if (scope.mode === "selected" && !(scope.fileIds || []).length) {
    setSustainabilityStatus("sustainability.noSelectedFiles", "error");
    return;
  }
  const token = ++sustainabilityState.requestToken;
  setSustainabilityStatus("sustainability.loading");
  sustainabilityRunBtn.disabled = true;
  sustainabilityRefreshBtn.disabled = true;
  try {
    const summary = await fetchSustainabilityJSON(
      sustainabilityQueryUrl("/api/sustainability/summary", scope)
    );
    assertSustainabilityScope(summary, scope);
    const runId = summary.run && summary.run.id;
    const resolvedScope = {
      projectId: scope.projectId,
      mode: scope.mode,
      fileIds: (summary.file_ids || []).slice().sort(),
      key: scope.key,
    };
    const findingsUrl = sustainabilityQueryUrl(
      "/api/sustainability/assessment-findings", resolvedScope, runId
    );
    findingsUrl.searchParams.set("conversation_id", conversationId);
    const loaded = await Promise.all([
      loadAllSustainabilityElements(resolvedScope, runId),
      fetchSustainabilityJSON(
        sustainabilityQueryUrl("/api/sustainability/materials", resolvedScope, runId)
      ).then(function (data) { return data.items || []; }),
      fetchSustainabilityJSON(findingsUrl).then(function (data) { return data.items || []; }),
    ]);
    if (token !== sustainabilityState.requestToken ||
        scope.key !== currentSustainabilityScope().key) return;
    renderSustainability(summary, loaded[0], loaded[1], loaded[2], scope.key);
  } catch (error) {
    if (token !== sustainabilityState.requestToken) return;
    clearSustainabilityResults();
    if (error.status === 404 && error.code === "analysis_not_found") {
      setSustainabilityStatus("sustainability.empty");
    } else {
      setSustainabilityStatus("sustainability.backendError", "error", error.message);
    }
  } finally {
    if (token === sustainabilityState.requestToken) {
      sustainabilityRunBtn.disabled = false;
      sustainabilityRefreshBtn.disabled = false;
    }
  }
}

async function runSustainabilityAnalysis() {
  const scope = currentSustainabilityScope();
  if (!scope.projectId) {
    setSustainabilityStatus("sustainability.noProject", "error");
    return;
  }
  if (scope.mode === "selected" && !(scope.fileIds || []).length) {
    setSustainabilityStatus("sustainability.noSelectedFiles", "error");
    return;
  }
  const token = ++sustainabilityState.requestToken;
  clearSustainabilityResults();
  setSustainabilityStatus("sustainability.running");
  sustainabilityRunBtn.disabled = true;
  sustainabilityRefreshBtn.disabled = true;
  try {
    const response = await fetch("/api/sustainability/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project_id: scope.projectId,
        file_ids: scope.fileIds,
        allow_geometry_derived: Boolean(sustainabilityDerivedToggle.checked),
      }),
    });
    const data = await response.json().catch(function () { return {}; });
    if (!response.ok) {
      const error = new Error(formatApiError(data.detail, response.statusText));
      error.status = response.status;
      error.code = data.detail && data.detail.code;
      throw error;
    }
    assertSustainabilityScope(data, scope);
    if (token !== sustainabilityState.requestToken) return;
    await loadSustainability();
  } catch (error) {
    if (token !== sustainabilityState.requestToken) return;
    setSustainabilityStatus("sustainability.backendError", "error", error.message);
  } finally {
    if (token === sustainabilityState.requestToken) {
      sustainabilityRunBtn.disabled = false;
      sustainabilityRefreshBtn.disabled = false;
    }
  }
}

async function downloadSustainabilityReport(format) {
  const summary = sustainabilityState.summary;
  if (!summary) return;
  const scope = {
    projectId: String(summary.project_id),
    fileIds: (summary.file_ids || []).slice().sort(),
  };
  const runId = summary.run && summary.run.id;
  const url = sustainabilityQueryUrl("/api/sustainability/report", scope, runId);
  url.searchParams.set("conversation_id", conversationId);
  url.searchParams.set("format", format);
  try {
    const response = await fetch(url);
    if (!response.ok) {
      const data = await response.json().catch(function () { return {}; });
      throw new Error(formatApiError(data.detail, response.statusText));
    }
    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    const disposition = response.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="([^"]+)"/);
    link.href = objectUrl;
    link.download = match ? match[1] :
      scope.projectId + "-sustainability-report." + format;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(objectUrl);
  } catch (error) {
    setSustainabilityStatus("sustainability.reportFailed", "error", error.message);
  }
}

sustainabilityRunBtn.addEventListener("click", runSustainabilityAnalysis);
sustainabilityRefreshBtn.addEventListener("click", loadSustainability);
sustainabilityScopeMode.addEventListener("change", function () {
  sustainabilityState.requestToken += 1;
  renderSustainabilityScope();
  loadSustainability();
});
sustainabilityBreakdownSelect.addEventListener("change", renderSustainabilityBreakdown);
sustainabilityReportButtons.forEach(function (button) {
  button.addEventListener("click", function () {
    downloadSustainabilityReport(button.dataset.format);
  });
});

document.addEventListener("languagechange", function () {
  if (activeNav === "sustainability") {
    renderSustainabilityScope();
    if (sustainabilityState.summary) {
      renderSustainability(
        sustainabilityState.summary,
        sustainabilityState.elements,
        sustainabilityState.materials,
        sustainabilityState.findings,
        sustainabilityState.scopeKey
      );
    }
  }
});

document.addEventListener("DOMContentLoaded", function () {
  renderSustainabilityScope();
});
