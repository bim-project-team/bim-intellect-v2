// BIM-Intellect Frontend — Chat, Pipeline, Results, Sustainability, Corpus
//
// Interface strings come from static/i18n.js via t(). Request URLs, payload
// keys, and response field names are deliberately untouched by localisation —
// only what the user reads changes.

// Shorthand for the localiser. Guarded so the app still renders (in English)
// if i18n.js fails to load for any reason.
function t(key, vars) {
  return window.i18n ? window.i18n.t(key, vars) : key;
}

// Free text of unknown language (model answers, IFC element names, filenames)
// is emitted with dir="auto" so the browser resolves direction per value.
// Without this, a Persian element name inside an English interface — or an
// English name inside a Persian one — renders with its punctuation displaced.
function autoDir(html) {
  return `<span dir="auto">${html}</span>`;
}

// ------------------------------------------------------------------
// Reusable checkbox-dropdown multiselect widget
// ------------------------------------------------------------------
class MultiSelectDropdown {
  constructor(rootId, {
    emptyTextKey = "pipeline.noneFound",
    allOptionKey = null,
    selectAllByDefault = false,
  } = {}) {
    this.root = document.getElementById(rootId);
    if (!this.root) return;

    this.toggleBtn = this.root.querySelector(".multiselect-toggle");
    this.toggleLabel = this.root.querySelector(".multiselect-toggle-label");
    this.panel = this.root.querySelector(".multiselect-panel");
    this.optionsEl = this.root.querySelector(".multiselect-options");
    this.searchInput = this.root.querySelector(".multiselect-search");
    this.emptyTextKey = emptyTextKey;
    this.allOptionKey = allOptionKey;
    this.selectAllByDefault = selectAllByDefault;
    this.hasLoadedOptions = false;
    // Remembered so the label/placeholder can be re-rendered in the new
    // language without the caller having to re-supply it.
    this.placeholderKey = "pipeline.selectFileFirst";
    this.options = []; // [{value, label, count}]
    this.selected = new Set();

    this.toggleBtn.addEventListener("click", () => {
      if (this.toggleBtn.disabled) return;
      this.isOpen() ? this.close() : this.open();
    });

    this.root.querySelectorAll('[data-action="all"]').forEach((btn) =>
      btn.addEventListener("click", () => this.selectAll())
    );
    this.root.querySelectorAll('[data-action="none"]').forEach((btn) =>
      btn.addEventListener("click", () => this.clear())
    );

    if (this.searchInput) {
      this.searchInput.addEventListener("input", () => this._renderOptions());
      this.searchInput.addEventListener("click", (e) => e.stopPropagation());
    }

    document.addEventListener("click", (e) => {
      if (!this.root.contains(e.target)) this.close();
    });

    this.disable("pipeline.selectFileFirst");
  }

  isOpen() {
    return this.root.classList.contains("open");
  }

  open() {
    this.root.classList.add("open");
    this.panel.classList.remove("hidden");
    if (this.searchInput) {
      this.searchInput.value = "";
      this.searchInput.focus();
      this._renderOptions();
    }
  }

  close() {
    this.root.classList.remove("open");
    this.panel.classList.add("hidden");
  }

  disable(placeholderKey) {
    this.options = [];
    this.selected.clear();
    this.placeholderKey = placeholderKey || this.placeholderKey;
    this.toggleBtn.disabled = true;
    this.toggleLabel.textContent = t(this.placeholderKey);
    this.optionsEl.innerHTML = "";
    this.close();
  }

  /** Re-render the visible strings after a language change, preserving both
   *  the option list and the current selection. */
  relabel() {
    if (!this.root) return;
    if (this.toggleBtn.disabled) {
      this.toggleLabel.textContent = t(this.placeholderKey);
    } else {
      this._updateLabel();
    }
    this._renderOptions();
  }

  setOptions(options) {
    // options: [{value, label, count?}]
    // Preserve the current selection across re-renders (loadIfcProjects()
    // recomputes the combined storey/type lists whenever the set of selected
    // models changes; dropping the selection there would silently reset the
    // user's filters).
    const previous = new Set(this.selected);
    const wasAllSelected = this.options.length > 0 && this.selected.size === this.options.length;
    this.options = options || [];
    const shouldSelectAll = this.selectAllByDefault && (!this.hasLoadedOptions || wasAllSelected);
    this.selected = new Set(this.options
      .map((o) => o.value)
      .filter((value) => shouldSelectAll || previous.has(value)));
    this.hasLoadedOptions = true;
    this.toggleBtn.disabled = this.options.length === 0;
    this._renderOptions();
    this._updateLabel();
  }

  getSelected() {
    return Array.from(this.selected);
  }

  getFilterValue() {
    // No selection and explicitly selecting every option both mean "all".
    if (this.selected.size === 0 || this.selected.size === this.options.length) {
      return null;
    }
    return this.getSelected();
  }

  selectAll() {
    this.options.forEach((o) => this.selected.add(o.value));
    this._syncCheckboxes();
    this._updateLabel();
  }

  clear() {
    this.selected.clear();
    this._syncCheckboxes();
    this._updateLabel();
  }

  _renderOptions() {
    const filter = this.searchInput ? this.searchInput.value.trim().toLowerCase() : "";
    const visible = filter
      ? this.options.filter((o) => o.label.toLowerCase().includes(filter))
      : this.options;

    if (this.options.length === 0) {
      this.optionsEl.innerHTML = `<div class="multiselect-empty">${escapeHtml(t(this.emptyTextKey))}</div>`;
      return;
    }
    if (visible.length === 0) {
      this.optionsEl.innerHTML = `<div class="multiselect-empty">${escapeHtml(t("pipeline.noMatches"))}</div>`;
      return;
    }

    const allOptionHtml = this.allOptionKey
      ? `
          <label class="multiselect-option multiselect-all-option">
            <input type="checkbox" data-select-all ${this.selected.size === this.options.length ? "checked" : ""} />
            <span class="option-label">${escapeHtml(t(this.allOptionKey))}</span>
          </label>
        `
      : "";

    this.optionsEl.innerHTML = allOptionHtml + visible
      .map((o) => {
        const checked = this.selected.has(o.value) ? "checked" : "";
        const countHtml = o.count !== undefined ? `<span class="option-count">${Number(o.count)}</span>` : "";
        // Storey names come from the IFC file and can be in either script.
        return `
          <label class="multiselect-option">
            <input type="checkbox" value="${escapeHtml(String(o.value))}" ${checked} />
            <span class="option-label" dir="auto">${escapeHtml(String(o.label))}</span>
            ${countHtml}
          </label>
        `;
      })
      .join("");

    const allCheckbox = this.optionsEl.querySelector('input[data-select-all]');
    if (allCheckbox) {
      allCheckbox.addEventListener("change", () => {
        if (allCheckbox.checked) this.selectAll();
        else this.clear();
      });
    }

    this.optionsEl.querySelectorAll('input[type="checkbox"]:not([data-select-all])').forEach((cb) => {
      cb.addEventListener("change", () => {
        if (cb.checked) this.selected.add(cb.value);
        else this.selected.delete(cb.value);
        this._syncCheckboxes();
        this._updateLabel();
      });
    });
  }

  _syncCheckboxes() {
    const allCheckbox = this.optionsEl.querySelector('input[data-select-all]');
    if (allCheckbox) {
      allCheckbox.checked = this.options.length > 0 && this.selected.size === this.options.length;
    }
    this.optionsEl.querySelectorAll('input[type="checkbox"]:not([data-select-all])').forEach((cb) => {
      cb.checked = this.selected.has(cb.value);
    });
  }

  _updateLabel() {
    const n = this.selected.size;
    if (this.options.length === 0) {
      this.toggleLabel.textContent = t(this.emptyTextKey);
    } else if (this.allOptionKey && n === this.options.length) {
      this.toggleLabel.textContent = t(this.allOptionKey);
    } else if (n === 0) {
      this.toggleLabel.textContent = this.allOptionKey
        ? t("pipeline.nSelected", { n: 0 })
        : t("pipeline.allSelected");
    } else if (n <= 2) {
      const labels = this.options.filter((o) => this.selected.has(o.value)).map((o) => o.label);
      this.toggleLabel.textContent = labels.join(", ");
    } else {
      this.toggleLabel.textContent = t("pipeline.nSelected", { n: n });
    }
  }
}

// ------------------------------------------------------------------
// State
// ------------------------------------------------------------------
let activeTab = "clashes"; // for results sub-tabs
let activeNav = "chat";    // for main navigation
let selectedElementId = null;
let selectedPdfFiles = [];
let selectedDocumentIds = new Set();
let indexedDocuments = [];
let selectedIfcFileIds = new Set();
let selectedIfcDeleteIds = new Set();
let ifcProjects = [];
let sustainabilityState = {
  scopeKey: null,
  summary: null,
  elements: [],
  materials: [],
  findings: [],
  requestToken: 0,
};
let currentProjectIfcFiles = [];

// ------------------------------------------------------------------
// DOM refs
// ------------------------------------------------------------------
const navButtons = document.querySelectorAll(".nav-btn");
const tabContents = document.querySelectorAll(".tab-content");

const sidebar = document.getElementById("sidebar");
const sidebarScrim = document.getElementById("sidebar-scrim");
const sidebarToggle = document.getElementById("sidebar-toggle");
const sidebarClose = document.getElementById("sidebar-close");
const workspaceTitle = document.getElementById("workspace-title");
const workspaceSubtitle = document.getElementById("workspace-subtitle");
const contextRail = document.getElementById("context-rail");
const railToggle = document.getElementById("rail-toggle");
const railClose = document.getElementById("rail-close");
const modelToggleWrap = document.getElementById("model-toggle-wrap");

const chatMessages = document.getElementById("chat-messages");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const chatSend = document.getElementById("chat-send");
const chatMeta = document.getElementById("chat-meta");
const strongModelsToggle = document.getElementById("strong-models-toggle");
const conversationStorageKey = "bim-intellect-conversation-id";
let conversationId = sessionStorage.getItem(conversationStorageKey);

const viewerPanel = document.getElementById("viewer-panel");
const viewerToggle = document.getElementById("viewer-toggle");
const viewerClose = document.getElementById("viewer-close");
const viewerReset = document.getElementById("viewer-reset");
const viewerCanvas = document.getElementById("viewer-canvas");
const viewerPlaceholder = document.getElementById("viewer-placeholder");
const viewerFooter = document.getElementById("viewer-footer");
if (!conversationId) {
  conversationId = window.crypto && window.crypto.randomUUID
    ? window.crypto.randomUUID()
    : `chat-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  sessionStorage.setItem(conversationStorageKey, conversationId);
}

const log = document.getElementById("log");
const resultsBody = document.getElementById("results-body");
const resultsCount = document.getElementById("results-count");
const resultsTableScroll = document.querySelector(".results-table-scroll");
const resultsTopScroll = document.getElementById("results-top-scroll");
const resultsTopSpacer = document.getElementById("results-top-scroll-spacer");
const resultTabButtons = document.querySelectorAll("#tab-results .tab-btn");
const exportResultsBtn = document.getElementById("export-results-btn");
const resultsCardView = document.getElementById("results-card-view");
const resultsCards = document.getElementById("results-cards");
const resultsTableWrap = document.getElementById("results-table-wrap");
const resultsStatus = document.getElementById("results-status");
const loadMoreResultsBtn = document.getElementById("load-more-results-btn");
const viewCardsBtn = document.getElementById("view-cards-btn");
const viewTableBtn = document.getElementById("view-table-btn");
const resultsSearchInput = document.getElementById("results-search");
const resultsSortSelect = document.getElementById("results-sort");
const crossFileFilterCheckbox = document.getElementById("cross-file-filter");
const clearLogBtn = document.getElementById("clear-log-btn");
const ingestForm = document.getElementById("ingest-form");
const ingestSubmitBtn = document.getElementById("ingest-submit");

const ifcDropZone = document.getElementById("ifc-drop-zone");
const ifcFileInput = document.getElementById("ifc-file");
const ifcDropZoneFile = document.getElementById("ifc-drop-zone-file");
const projectIdInput = document.getElementById("project-id");
const ifcModelList = document.getElementById("ifc-model-list");

const ingestStoreyDropdown = new MultiSelectDropdown("ingestStoreyFilter", { emptyTextKey: "pipeline.noStoreys" });
const ingestTypeDropdown = new MultiSelectDropdown("ingestTypeFilter", { emptyTextKey: "pipeline.noTypes" });
const resultTypeDropdown = new MultiSelectDropdown("resultTypeFilter", {
  emptyTextKey: "pipeline.noTypes",
  allOptionKey: "results.allTypes",
  selectAllByDefault: true,
});

const dropZone = document.getElementById("drop-zone");
const pdfFileInput = document.getElementById("pdf-file");
const dropZoneFile = document.getElementById("drop-zone-file");
const uploadForm = document.getElementById("upload-form");
const uploadSubmit = document.getElementById("upload-submit");
const uploadStatus = document.getElementById("upload-status");
const uploadStatusText = document.getElementById("upload-status-text");

const corpusSelectAll = document.getElementById("corpus-select-all");
const deleteCorpusSelectedBtn = document.getElementById("delete-corpus-selected-btn");
const corpusInfo = document.getElementById("corpus-info");
const ifcDeleteSelectAll = document.getElementById("ifc-delete-select-all");
const ifcDeleteSelectedBtn = document.getElementById("ifc-delete-selected-btn");

// ------------------------------------------------------------------
// Navigation + drawers
// ------------------------------------------------------------------

// The workspace header mirrors the active section, so the title/subtitle keys
// live here rather than being duplicated in the markup.
const TAB_HEADERS = {
  chat: { title: "nav.chat", subtitle: "header.chat" },
  pipeline: { title: "nav.pipeline", subtitle: "header.pipeline" },
  results: { title: "nav.results", subtitle: "header.results" },
  sustainability: { title: "nav.sustainability", subtitle: "header.sustainability" },
  corpus: { title: "nav.corpus", subtitle: "header.corpus" },
};

function syncWorkspaceHeader() {
  const meta = TAB_HEADERS[activeNav] || TAB_HEADERS.chat;
  // data-i18n is rewritten too, so a later language change re-renders the
  // header for whichever tab is open rather than reverting to Chat.
  workspaceTitle.setAttribute("data-i18n", meta.title);
  workspaceTitle.textContent = t(meta.title);
  workspaceSubtitle.setAttribute("data-i18n", meta.subtitle);
  workspaceSubtitle.textContent = t(meta.subtitle);

  // The context rail, the 3D Visualization trigger, and the model toggle
  // belong to chat only. The viewer *panel* is also reachable from Results,
  // where each clash card offers "View in 3D" for its element pair.
  const onChat = activeNav === "chat";
  modelToggleWrap.classList.toggle("hidden", !onChat);
  railToggle.classList.toggle("hidden", !onChat);
  contextRail.classList.toggle("hidden", !onChat);
  viewerToggle.classList.toggle("hidden", !onChat);
  viewerPanel.classList.toggle("hidden", !(onChat || activeNav === "results"));
  // Leaving chat must also close the drawers, otherwise one would reappear
  // still-open when the user comes back.
  if (!onChat) {
    closeDrawer(contextRail, railToggle);
    closeDrawer(viewerPanel, viewerToggle);
  }
}

// --- Drawer plumbing ------------------------------------------------
// Both panels are overlays: the sidebar on the inline-start edge, the analysis
// context on the inline-end edge. They share one scrim, and only one may be
// open at a time so the scrim's click target is never ambiguous.

const DRAWERS = []; // populated below, after both elements are known

function isOpen(panel) {
  return panel.classList.contains("open");
}

function syncScrim() {
  sidebarScrim.classList.toggle("open", DRAWERS.some(([panel]) => isOpen(panel)));
}

function closeDrawer(panel, trigger) {
  panel.classList.remove("open");
  if (trigger) trigger.setAttribute("aria-expanded", "false");
  syncScrim();
}

function closeAllDrawers() {
  DRAWERS.forEach(([panel, trigger]) => closeDrawer(panel, trigger));
}

function openDrawer(panel, trigger) {
  // Close the other one first — two overlapping drawers plus one scrim would
  // leave the second unreachable by click-outside.
  DRAWERS.forEach(([other, otherTrigger]) => {
    if (other !== panel) closeDrawer(other, otherTrigger);
  });
  panel.classList.add("open");
  if (trigger) trigger.setAttribute("aria-expanded", "true");
  syncScrim();
}

function toggleDrawer(panel, trigger) {
  if (isOpen(panel)) closeDrawer(panel, trigger);
  else openDrawer(panel, trigger);
}

DRAWERS.push([sidebar, sidebarToggle], [contextRail, railToggle], [viewerPanel, viewerToggle]);

sidebarToggle.addEventListener("click", () => toggleDrawer(sidebar, sidebarToggle));
railToggle.addEventListener("click", () => toggleDrawer(contextRail, railToggle));

sidebarClose.addEventListener("click", () => closeDrawer(sidebar, sidebarToggle));
railClose.addEventListener("click", () => closeDrawer(contextRail, railToggle));

sidebarScrim.addEventListener("click", closeAllDrawers);

// Escape closes whichever drawer is open — expected of any overlay panel.
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeAllDrawers();
});

navButtons.forEach((btn) => {
  btn.addEventListener("click", () => {
    const target = btn.dataset.tab;
    if (!target) return;

    navButtons.forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");

    tabContents.forEach((tc) => tc.classList.remove("active"));
    const targetEl = document.getElementById("tab-" + target);
    if (targetEl) targetEl.classList.add("active");

    activeNav = target;
    // Order matters: syncWorkspaceHeader() may close the rail, and closing the
    // sidebar afterwards leaves the scrim in the correct state either way.
    syncWorkspaceHeader();
    closeDrawer(sidebar, sidebarToggle);

    // Auto-load corpus status when opening that tab
    if (target === "corpus") {
      loadCorpusStatus();
    }
    if (target === "pipeline") {
      loadIfcProjects();
    }
    if (target === "sustainability") {
      loadIfcProjects().finally(() => {
        renderSustainabilityScope();
        loadSustainability();
      });
    }

    // Refresh Results tab filter options (Neo4j-backed) each time it's opened,
    // in case an ingest happened since the last visit.
    if (target === "results") {
      loadStoreys();
      loadTypes();
      loadResults();
    }
  });
});

// ------------------------------------------------------------------
// 3D Visualization
// ------------------------------------------------------------------
// One viewer instance, re-targeted per answer. viewer.js is a module and
// therefore deferred, so it may publish window.bimViewer after this classic
// script has run: every entry point below tolerates its absence rather than
// assuming it is loaded.

// The project whose scenes are shown. Read from the Pipeline tab's field so the
// chat and the viewer always agree on which building is being discussed.
function viewerProjectId() {
  return (projectIdInput && projectIdInput.value.trim()) || "";
}

// A storey scene averages ~2MB and the worst in the reference model is 13MB, so
// an answer spanning many storeys must not fetch them all. Four keeps the
// download bounded while still showing an element and its neighbours in context.
const MAX_VIEWER_SCENES = 4;

function setViewerFooter(lines) {
  viewerFooter.innerHTML = (lines || [])
    .filter(Boolean)
    .map((line) => `<span class="viewer-scope" dir="auto">${escapeHtml(line)}</span>`)
    .join("");
}

/** Bounding boxes for camera framing and for the no-geometry box fallback. */
async function fetchElementBounds(projectId, sceneKeys, ifcTypes, fileIds) {
  const url = new URL("/api/model/elements", window.location.origin);
  url.searchParams.set("project_id", projectId);
  (sceneKeys || []).forEach((key) => url.searchParams.append("scene_key", key));
  (ifcTypes || []).forEach((type) => url.searchParams.append("ifc_type", type));
  (fileIds || []).forEach((fileId) => url.searchParams.append("file_id", fileId));
  const response = await fetch(url);
  if (!response.ok) return { elements: [], truncated: false };
  return response.json();
}

/** Bounds for specific elements, so a clash pair can be shown without
 *  fetching (and filtering) a whole storey or type population. */
async function fetchElementsByIds(projectId, guids, fileIds) {
  const url = new URL("/api/model/elements/by-guid", window.location.origin);
  url.searchParams.set("project_id", projectId);
  (guids || []).filter(Boolean).forEach((guid) => url.searchParams.append("guid", guid));
  (fileIds || []).filter(Boolean).forEach((fileId) => url.searchParams.append("file_id", fileId));
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

/** Scene descriptors from the ingest-time manifest, restricted to the given keys. */
async function fetchScenes(projectId, sceneKeys, fileIds) {  const wanted = new Set((sceneKeys || []).filter(Boolean));
  const wantedFiles = new Set((fileIds || []).filter(Boolean));
  if (!wanted.size) return [];
  const url = new URL("/api/model/manifest", window.location.origin);
  url.searchParams.set("project_id", projectId);
  const response = await fetch(url);
  if (!response.ok) return [];
  const manifest = await response.json();
  const scenes = [];
  Object.entries(manifest.files || {}).forEach(([manifestFileId, file]) => {
    const fileId = file.file_id || manifestFileId;
    if (wantedFiles.size && !wantedFiles.has(fileId)) return;
    (file.scenes || []).forEach((scene) => {
      if (!wanted.has(scene.scene_key)) return;
      scenes.push({
        url: `/api/model/scene/${encodeURIComponent(manifest.project_id)}`
          + `/${encodeURIComponent(fileId)}/${encodeURIComponent(scene.scene_key)}.glb`,
        storeyName: scene.storey_name || "",
        bytes: scene.bytes || 0,
      });
    });
  });
  // Smallest first, so the cap keeps the most scenes for the least bytes.
  return scenes.sort((a, b) => a.bytes - b.bytes).slice(0, MAX_VIEWER_SCENES);
}

/**
 * Point the viewer at one answer's evidence.
 *
 * `visualization` is the payload the backend attaches to every answer. `related`
 * marks the opt-in IFC-type view, which is orientation rather than evidence, so
 * the footer says which of the two the user is looking at.
 */
// Shared viewer chrome: attach the canvas, open the drawer, resolve the active
// project, and put the panel into its loading state. Returns the resolved
// projectId, or null if the viewer is unavailable (footer already updated).
function beginViewerSession(requestedProjectId) {
  if (!window.bimViewer) return null;
  window.bimViewer.attach(viewerCanvas);
  openDrawer(viewerPanel, viewerToggle);

  const projectId = requestedProjectId || viewerProjectId();
  if (!projectId) {
    setViewerFooter([t("viewer.unavailable")]);
    return null;
  }

  viewerPlaceholder.classList.remove("hidden");
  viewerPlaceholder.textContent = t("viewer.loading");
  setViewerFooter([]);
  return projectId;
}

// Shared failure state for both viewer entry points.
function showViewerError(error) {
  viewerPlaceholder.classList.remove("hidden");
  viewerPlaceholder.textContent = t("viewer.empty");
  setViewerFooter([`${t("common.error")}: ${error.message || error}`]);
}

async function showInViewer(visualization, { related = false } = {}) {
  const projectId = beginViewerSession(visualization.project_id);
  if (!projectId) return;

  const highlight = related ? [] : visualization.highlight || [];
  const relatedTypes = related ? visualization.related_types || [] : [];
  const requestedScenes = related ? [] : (visualization.scenes || []).slice(0, MAX_VIEWER_SCENES);
  const scopedFileIds = Array.isArray(visualization.file_ids)
    ? visualization.file_ids
    : Array.from(selectedIfcFileIds || []);

  try {
    // Bounds first: in the opt-in view the elements themselves determine which
    // storeys are worth loading, so the scene list cannot be known before this.
    const bounds = await fetchElementBounds(
      projectId, requestedScenes, relatedTypes, scopedFileIds,
    );
    const allBounds = bounds.elements || [];
    const sceneKeys = related
      ? [...new Set(allBounds.map((item) => item.scene_key).filter(Boolean))]
      : requestedScenes;
    const scenes = await fetchScenes(projectId, sceneKeys, scopedFileIds);

    // In the evidence view only the cited elements get boxes. In the fallback
    // (no meshes at all) every returned element is drawn, otherwise a single
    // highlighted box would float with nothing around it.
    const highlightKeys = new Set(
      highlight.map((item) => item.ifc_guid || item.element_id).filter(Boolean),
    );
    const highlightedBounds = related
      ? allBounds
      : allBounds.filter((item) => highlightKeys.has(item.ifc_guid) || highlightKeys.has(item.element_id));

    const result = await window.bimViewer.show({
      sceneUrls: scenes.map((scene) => scene.url),
      highlight: related ? allBounds : highlight,
      bounds: highlightedBounds.length ? highlightedBounds : allBounds,
    });
    // A newer answer took over the viewer while this was loading; its own call
    // owns the footer and placeholder now.
    if (result.superseded) return;

    const drawn = result.loaded ? result.matched : highlightedBounds.length;
    viewerPlaceholder.classList.toggle("hidden", result.loaded > 0 || allBounds.length > 0);
    viewerPlaceholder.textContent = t("viewer.empty");

    const lines = [];
    if (related) {
      lines.push(t("viewer.relatedNotice", { types: relatedTypes.join(", ") }));
    }
    lines.push(t("viewer.elements", { n: drawn }));
    const storeys = scenes.map((scene) => scene.storeyName).filter(Boolean);
    if (storeys.length) lines.push(t("viewer.scenes", { names: storeys.join(" · ") }));
    if (!result.loaded) lines.push(t("viewer.boxFallback"));
    else if (highlight.length && !result.matched) lines.push(t("viewer.noMatch"));
    if (sceneKeys.length > scenes.length) {
      lines.push(t("viewer.sceneLimit", { shown: scenes.length, total: sceneKeys.length }));
    }
    if (visualization.truncated || bounds.truncated) {
      lines.push(t("viewer.truncated", { n: drawn }));
    }
    setViewerFooter(lines);
  } catch (error) {
    showViewerError(error);
  }
}

/** Per-message affordance, so any earlier answer can be re-shown. */
function appendViewerButton(container, visualization) {
  if (!window.bimViewer) return;
  const related = !visualization.available && visualization.reason === "related_types";
  if (!visualization.available && !related) return;

  const button = document.createElement("button");
  button.type = "button";
  button.className = `chat-view-3d${related ? " speculative" : ""}`;
  // Kept on the element so a later language change can relabel it without
  // re-asking the backend (see the languagechange handler).
  button.dataset.viewerRelated = String(related);
  button.dataset.viewerTypes = (visualization.related_types || []).join(", ");
  button.textContent = viewerButtonLabel(related, button.dataset.viewerTypes);
  button.addEventListener("click", () => showInViewer(visualization, { related }));
  container.appendChild(button);
}

function viewerButtonLabel(related, types) {
  return related ? t("viewer.openRelated", { types }) : t("viewer.open");
}

viewerToggle.addEventListener("click", () => toggleDrawer(viewerPanel, viewerToggle));
viewerClose.addEventListener("click", () => closeDrawer(viewerPanel, viewerToggle));
viewerReset.addEventListener("click", () => window.bimViewer?.resetView());

// ------------------------------------------------------------------
// Chat
// ------------------------------------------------------------------

function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function formatTime() {
  // Persian locale renders its own numerals, matching the rest of the UI.
  const locale = window.i18n && window.i18n.lang === "fa" ? "fa-IR" : "en-GB";
  return new Date().toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });
}

function detectContentDirection(text) {
  // Direction follows the dominant script in this value, not the UI locale.
  // The shared detector ignores code, URLs, citations, and technical IDs.
  return window.ChatMarkdown
    ? window.ChatMarkdown.direction(text)
    : (/[\u0590-\u08FF\uFB1D-\uFDFF\uFE70-\uFEFC]/u.test(String(text || "")) ? "rtl" : "ltr");
}

// Kept as a small compatibility helper for existing UI checks/callers.
function containsRtlText(text) {
  return detectContentDirection(text) === "rtl";
}

function applyTextDirection(element, text) {
  const direction = detectContentDirection(text);
  const isRtl = direction === "rtl";
  element.dir = direction;
  element.classList.toggle("rtl", isRtl);
  element.classList.toggle("ltr", !isRtl);
}

function resizeChatInput() {
  chatInput.style.height = "auto";
  chatInput.style.height = `${Math.min(chatInput.scrollHeight, 160)}px`;
  chatInput.style.overflowY = chatInput.scrollHeight > 160 ? "auto" : "hidden";
}

function appendChatMessage(role, text, sources, visualization) {
  // Remove welcome screen on first real message
  const welcome = chatMessages.querySelector(".chat-welcome");
  if (welcome) welcome.remove();

  const msgDiv = document.createElement("div");
  msgDiv.className = `chat-msg ${role}`;

  const bubble = document.createElement("div");
  bubble.className = "chat-bubble";
  applyTextDirection(bubble, text);
  if (role === "assistant" && window.ChatMarkdown) {
    bubble.classList.add("markdown-body");
    bubble.innerHTML = window.ChatMarkdown.render(text);
  } else if (window.ChatMarkdown) {
    bubble.innerHTML = window.ChatMarkdown.renderPlain(text);
  } else {
    // User messages and defensive fallback remain plain text.
    bubble.textContent = text;
  }
  msgDiv.appendChild(bubble);

  const meta = document.createElement("div");
  meta.className = "chat-meta-line";
  meta.textContent = `${role === "user" ? t("chat.you") : t("chat.assistant")} — ${formatTime()}`;
  msgDiv.appendChild(meta);

  if (sources && sources.length > 0) {
    const tagsDiv = document.createElement("div");
    tagsDiv.className = "chat-sources";
    sources.forEach((src) => {
      const tag = document.createElement("span");
      tag.className = `chat-source-tag ${src.type || "regulation"}`;
      tag.setAttribute("role", "note");
      if (src.type === "regulation") {
        const hasClause = src.clause_id && !["unknown", "none", "null"].includes(String(src.clause_id).toLowerCase());
        tag.textContent = hasClause
          ? `${t("chat.clause")} ${src.clause_id}, ${t("chat.page")} ${src.page_number || "?"}`
          : `${src.standard_name || src.source || t("chat.regulation")} — ${t("chat.section")} ${src.section_id || "?"}, ${t("chat.page")} ${src.page_number || "?"}`;

        tag.title =
          `${t("chat.source")}: ${src.source || t("chat.regulation")} | ${t("chat.page")} ${src.page_number || "?"}`;
      } else if (src.type === "graph") {
        tag.textContent =
          `${src.ifc_type || t("chat.element")} ${src.name || src.element_id || ""}`;

        tag.title =
          `${t("chat.elementId")}: ${src.element_id || "?"}`;
      } else if (src.type === "sustainability") {
        tag.textContent = `${t("chat.sourceSustainability")} — ${src.id || ""}`;
        tag.title = `${src.project_id || ""} | ${(src.file_ids || []).join(", ")}`;
      } else if (src.type === "document_metadata") {
        tag.textContent = `${src.source || t("chat.regulation")} — ${src.total_pdf_pages || "?"} pages`;
        tag.title = `Physical PDF page count (${src.page_number_convention || "physical_pdf_page_1_based"})`;
      } else {
        tag.textContent = JSON.stringify(src);
      }
      tagsDiv.appendChild(tag);
    });
    msgDiv.appendChild(tagsDiv);
  }

  if (visualization) appendViewerButton(msgDiv, visualization);

  chatMessages.appendChild(msgDiv);
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

chatInput.addEventListener("input", () => {
  applyTextDirection(chatInput, chatInput.value);
  resizeChatInput();
});

chatInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    chatForm.requestSubmit();
  }
});

function showTypingIndicator() {
  const welcome = chatMessages.querySelector(".chat-welcome");
  if (welcome) welcome.remove();

  const indicator = document.createElement("div");
  indicator.className = "chat-msg assistant";
  indicator.id = "typing-indicator";

  const bubble = document.createElement("div");
  bubble.className = "chat-bubble";
  bubble.style.minWidth = "60px";

  const dots = document.createElement("div");
  dots.className = "typing-indicator";
  dots.innerHTML = "<span></span><span></span><span></span>";
  bubble.appendChild(dots);

  indicator.appendChild(bubble);
  chatMessages.appendChild(indicator);
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function hideTypingIndicator() {
  const el = document.getElementById("typing-indicator");
  if (el) el.remove();
}

async function sendChat(question) {
  if (!question.trim()) return;

  appendChatMessage("user", question);
  chatInput.value = "";
  applyTextDirection(chatInput, "");
  resizeChatInput();
  chatInput.disabled = true;
  chatSend.disabled = true;
  showTypingIndicator();

  try {
    const payload = {
      question: question.trim(),
      selected_element_id: selectedElementId,
      conversation_id: conversationId,
      use_strong_models: Boolean(strongModelsToggle && strongModelsToggle.checked),
    };
    const activeProjectId = projectIdInput && projectIdInput.value.trim();
    if (activeProjectId) payload.project_id = activeProjectId;
    if (selectedIfcFileIds && selectedIfcFileIds.size) {
      payload.file_ids = Array.from(selectedIfcFileIds);
    }

    console.log("Sending /api/ask:", payload);

    const response = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    const data = await response.json().catch(() => ({
      answer: `Error: ${response.status} — ${response.statusText}`,
      sources: [],
    }));

    if (!response.ok) {
      hideTypingIndicator();
      appendChatMessage("assistant", `Error ${response.status}: ${data.detail || data.answer || "Unknown error"}`);
      return;
    }

    hideTypingIndicator();
    if (data.conversation_id) {
      conversationId = data.conversation_id;
      sessionStorage.setItem(conversationStorageKey, conversationId);
    }
    const visualization = data.visualization || null;
    appendChatMessage("assistant", data.answer || t("chat.noAnswer"), data.sources, visualization);

    // Update sidebar
    updateChatMeta(data);

    // The toggle stays disabled until an answer has something to show, so it
    // never opens onto an empty canvas.
    const showable = Boolean(visualization && (visualization.available || visualization.related_types?.length));
    viewerToggle.disabled = !showable;
    // Auto-open only for actual element evidence. The opt-in type view is an
    // offer, so it waits for the button.
    if (visualization && visualization.available) {
      showInViewer(visualization);
    }
  } catch (err) {
    hideTypingIndicator();
    appendChatMessage("assistant", `${t("chat.networkError")}: ${err.message}`);
  } finally {
    chatInput.disabled = false;
    chatSend.disabled = false;
    chatInput.focus();
  }
}

function updateChatMeta(data) {
  const parts = [];
  if (data.used_vector) parts.push(t("chat.sourceRegulations"));
  if (data.used_graph) parts.push(t("chat.sourceGraph"));
  if (data.used_sustainability) parts.push(t("chat.sourceSustainability"));

  let html = `<p class="hint">${escapeHtml(t("chat.sourcesConsulted"))}:</p>`;
  if (parts.length === 0) {
    html += `<p class="hint">${escapeHtml(t("chat.sourcesNone"))}</p>`;
  } else {
    html += `<ul>`;
    parts.forEach((p) => {
      html += `<li>${escapeHtml(p)}</li>`;
    });
    html += `</ul>`;
  }

  if (data.sources && data.sources.length > 0) {
    html += `<p class="hint rail-section">${escapeHtml(t("chat.citations"))} (${data.sources.length}):</p>`;
    html += `<div class="rail-tags">`;
    data.sources.forEach((src) => {
      if (src.type === "regulation") {
        const hasClause = src.clause_id && !["unknown", "none", "null"].includes(String(src.clause_id).toLowerCase());
        const clause = escapeHtml(src.clause_id || "?");
        const page = escapeHtml(src.page_number || "?");
        const source = escapeHtml(src.source || t("chat.regulation"));
        const section = escapeHtml(src.section_id || "?");
        const label = hasClause
          ? `${escapeHtml(t("chat.clause"))} ${clause}, ${escapeHtml(t("chat.page"))} ${page}`
          : `${escapeHtml(src.standard_name || src.source || t("chat.regulation"))} — ${escapeHtml(t("chat.section"))} ${section}, ${escapeHtml(t("chat.page"))} ${page}`;

        html += `
          <span
            class="chat-source-tag regulation"
            title="${escapeHtml(t("chat.source"))}: ${source} | ${escapeHtml(t("chat.page"))} ${page}">
            ${label}
          </span>
        `;
      } else if (src.type === "graph") {
        const elementId = escapeHtml(src.element_id || "");
        const label = escapeHtml(src.ifc_type || src.name || t("chat.element"));

        html += `
          <span
            class="chat-source-tag graph"
            title="${escapeHtml(t("chat.elementId"))}: ${elementId}">
            ${label}
          </span>
        `;
      } else if (src.type === "sustainability") {
        const runId = escapeHtml(src.id || "");
        const projectId = escapeHtml(src.project_id || "");
        html += `
          <span class="chat-source-tag sustainability" title="${projectId}">
            ${escapeHtml(t("chat.sourceSustainability"))} — ${runId}
          </span>
        `;
      } else if (src.type === "document_metadata") {
        const source = escapeHtml(src.source || t("chat.regulation"));
        const total = escapeHtml(src.total_pdf_pages || "?");
        html += `
          <span class="chat-source-tag document-metadata" title="Physical PDF page count">
            ${source} — ${total} pages
          </span>
        `;
      }
    });
    html += `</div>`;
  }

  if (data.model_mode) {
    const mode = escapeHtml(data.model_mode);
    const routerModel = escapeHtml(data.router_model || "");
    const finalModel = escapeHtml(data.final_model || "");
    html += `<p class="hint rail-section">${escapeHtml(t("chat.modelMode"))}: <strong>${mode}</strong></p>`;
    // Model slugs are identifiers — keep them LTR in both locales.
    html += `<p class="hint" dir="ltr">${escapeHtml(t("chat.router"))}: ${routerModel}<br>${escapeHtml(t("chat.final"))}: ${finalModel}</p>`;
  }
  const debug = data.retrieval_debug || {};
  if (data.rewritten_query) {
    html += `<p class="hint rail-section">${escapeHtml(t("chat.interpretedQuery"))}:</p>`;
    html += `<p class="chat-debug-query" dir="auto">${escapeHtml(data.rewritten_query)}</p>`;
  }
  if (debug.final_context_chunks !== undefined) {
    html += `<p class="hint">${escapeHtml(t("chat.retrievalStats", {
      candidates: Number(debug.vector_candidates || 0),
      reranked: Number(debug.reranked_results || 0),
      chunks: Number(debug.final_context_chunks || 0),
    }))}</p>`;
  }

  chatMeta.innerHTML = html;
}

chatForm.addEventListener("submit", (e) => {
  e.preventDefault();
  sendChat(chatInput.value);
});

// Example question buttons. Each carries an English and a Persian phrasing so
// the sample sent to the backend matches the language the user is reading.
document.querySelectorAll(".example-q").forEach((btn) => {
  btn.addEventListener("click", () => {
    const fa = btn.dataset.qFa;
    const q = window.i18n && window.i18n.lang === "fa" && fa ? fa : btn.dataset.q;
    if (q) sendChat(q);
  });
});

// ------------------------------------------------------------------
// PDF Upload (drag & drop + click)
// ------------------------------------------------------------------

// Shared drag-and-drop chrome for both the PDF and IFC drop zones: suppress the
// browser's default file handling and toggle the .dragover affordance. The
// click / change / drop handlers differ per zone and stay at each call site.
function setupDropZoneDrag(zoneEl) {
  ["dragenter", "dragover", "dragleave", "drop"].forEach((eventName) => {
    zoneEl.addEventListener(eventName, (e) => {
      e.preventDefault();
      e.stopPropagation();
    });
  });
  ["dragenter", "dragover"].forEach((eventName) => {
    zoneEl.addEventListener(eventName, () => zoneEl.classList.add("dragover"));
  });
  ["dragleave", "drop"].forEach((eventName) => {
    zoneEl.addEventListener(eventName, () => zoneEl.classList.remove("dragover"));
  });
}

dropZone.addEventListener("click", () => pdfFileInput.click());

pdfFileInput.addEventListener("change", () => {
  selectedPdfFiles = Array.from(pdfFileInput.files || []).filter((file) => file.name.toLowerCase().endsWith(".pdf"));
  renderSelectedFileNames(dropZoneFile, selectedPdfFiles);
  dropZone.classList.toggle("has-file", selectedPdfFiles.length > 0);
});

setupDropZoneDrag(dropZone);

dropZone.addEventListener("drop", (e) => {
  const files = Array.from(e.dataTransfer.files || []);
  const valid = files.filter((file) => file.name.toLowerCase().endsWith(".pdf"));
  if (valid.length > 0 && valid.length === files.length) {
    selectedPdfFiles = valid;
    renderSelectedFileNames(dropZoneFile, selectedPdfFiles);
    dropZone.classList.add("has-file");
    const dt = new DataTransfer();
    valid.forEach((item) => dt.items.add(item));
    pdfFileInput.files = dt.files;
  } else {
    showUploadStatus(t("corpus.allMustBePdf"), true);
  }
});

function renderSelectedFileNames(target, files) {
  target.textContent = files.length
    ? t("corpus.selectedFiles", {
        n: files.length,
        names: files.map((file) => file.name).join(", "),
      })
    : "";
}

function showUploadStatus(text, isError) {
  uploadStatusText.textContent = text;
  uploadStatus.classList.remove("hidden", "success", "error");
  uploadStatus.classList.add(isError ? "error" : "success");
}

function formatApiError(detail, fallback = "Unknown error") {
  if (!detail) return fallback;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((item) => {
      if (typeof item === "string") return item;
      const location = Array.isArray(item.loc) ? item.loc.join(" → ") : "request";
      return `${location}: ${item.msg || JSON.stringify(item)}`;
    }).join("; ");
  }
  return detail.message || JSON.stringify(detail);
}

uploadForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const files = Array.from(pdfFileInput.files || []);
  if (!files.length) {
    showUploadStatus(t("corpus.selectPdf"), true);
    return;
  }

  uploadSubmit.disabled = true;
  showUploadStatus(t("corpus.processing", { n: files.length }));

  const formData = new FormData();
  if (files.length === 1) {
    // The scalar field works with older FastAPI/Pydantic combinations too.
    formData.append("file", files[0]);
  } else {
    files.forEach((item) => formData.append("files", item));
  }
  const docId = document.getElementById("doc-id").value.trim();
  const documentDomain = document.getElementById("document-domain").value;
  const standardName = document.getElementById("standard-name").value.trim();
  const standardVersion = document.getElementById("standard-version").value.trim();
  const uploadUrl = new URL("/api/rag/upload", window.location.origin);
  if (docId && files.length === 1) uploadUrl.searchParams.set("doc_id", docId);
  uploadUrl.searchParams.set("document_domain", documentDomain);
  if (standardName) uploadUrl.searchParams.set("standard_name", standardName);
  if (standardVersion) uploadUrl.searchParams.set("standard_version", standardVersion);

  try {
    const response = await fetch(uploadUrl, {
      method: "POST",
      body: formData,
    });
    const data = await response.json().catch(() => ({
      detail: `${response.status} ${response.statusText}`,
    }));

    if (!response.ok) {
      showUploadStatus(
        `${t("corpus.uploadFailed")}: ${formatApiError(data.detail, t("common.unknownError"))}`,
        true,
      );
      return;
    }

    const outcomes = data.files || [data];
    const summary = outcomes.map((item) => item.status === "indexed"
      ? `✓ ${t("corpus.chunksIndexed", { file: item.filename, n: item.chunks_stored })}`
      : `✗ ${t("corpus.fileFailed", { file: item.filename, error: item.error || t("corpus.failed") })}`
    ).join("\n");
    showUploadStatus(summary, Number(data.succeeded ?? 1) === 0);

    // Reset form
    pdfFileInput.value = "";
    selectedPdfFiles = [];
    dropZoneFile.textContent = "";
    dropZone.classList.remove("has-file");
    document.getElementById("doc-id").value = "";
    document.getElementById("document-domain").value = "regulation";
    document.getElementById("standard-name").value = "";
    document.getElementById("standard-version").value = "";

    // Refresh corpus status if visible
    if (activeNav === "corpus") loadCorpusStatus();
  } catch (err) {
    showUploadStatus(`${t("chat.networkError")}: ${err.message}`, true);
  } finally {
    uploadSubmit.disabled = false;
  }
});

// ------------------------------------------------------------------
// Corpus Status
// ------------------------------------------------------------------

// Sync a "select all" master checkbox (checked / indeterminate / disabled) and
// its dependent action button from the selected vs available item counts.
// Shared by the PDF-corpus and IFC-file delete controls.
function syncSelectAll(masterCheckbox, actionButton, selectedCount, availableCount) {
  if (masterCheckbox) {
    masterCheckbox.checked = availableCount > 0 && selectedCount === availableCount;
    masterCheckbox.indeterminate = selectedCount > 0 && selectedCount < availableCount;
    masterCheckbox.disabled = availableCount === 0;
  }
  if (actionButton) actionButton.disabled = selectedCount === 0;
}

function updateCorpusSelectionControls() {
  const available = new Set(indexedDocuments.map((doc) => String(doc.document_id)));
  selectedDocumentIds = new Set(
    Array.from(selectedDocumentIds).filter((documentId) => available.has(documentId))
  );
  syncSelectAll(corpusSelectAll, deleteCorpusSelectedBtn, selectedDocumentIds.size, available.size);
}

async function loadCorpusStatus() {
  corpusInfo.innerHTML = `<p class="hint">${escapeHtml(t("common.loading"))}</p>`;
  try {
    const response = await fetch("/api/rag/status");
    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
      corpusInfo.innerHTML = `<p class="hint">${escapeHtml(t("common.error"))}: ${escapeHtml(data.detail || response.statusText)}</p>`;
      return;
    }

    const count = data.document_count ?? "?";
    const collection = data.collection ?? "?";
    const dir = data.chroma_dir ?? "?";
    const documents = data.documents || [];
    indexedDocuments = documents;

    let html = `
      <div class="stat-row"><span>${escapeHtml(t("corpus.collection"))}</span><span dir="auto">${escapeHtml(collection)}</span></div>
      <div class="stat-row"><span>${escapeHtml(t("corpus.indexedChunks"))}</span><span class="num">${escapeHtml(count)}</span></div>
      <div class="stat-row"><span>${escapeHtml(t("corpus.documents"))}</span><span class="num">${documents.length}</span></div>
      <div class="stat-row"><span>${escapeHtml(t("corpus.storage"))}</span><span class="path">${escapeHtml(dir)}</span></div>
    `;

    if (documents.length > 0) {
      html += `<div class="stored-file-list">${documents.map((doc) => `
        <label class="stored-file-row selectable-file">
          <input type="checkbox" class="document-select" data-document-id="${escapeHtml(doc.document_id)}"
                 aria-label="${escapeHtml(t("corpus.selectDocument", { name: doc.filename }))}"
                 ${selectedDocumentIds.has(String(doc.document_id)) ? "checked" : ""} />
          <div class="stored-file-copy">
            <strong dir="auto">${escapeHtml(doc.filename)}</strong>
            <span class="stored-file-meta">${escapeHtml(doc.document_domain || "regulation")}${doc.standard_name ? ` · ${escapeHtml(doc.standard_name)} ${escapeHtml(doc.standard_version || "")}` : ""} · ${Number(doc.chunk_count || 0)} ${escapeHtml(t("corpus.chunks"))} · ${Number(doc.page_count || 0)} ${escapeHtml(t("corpus.pages"))}${doc.ingested_at ? ` · ${escapeHtml(doc.ingested_at)}` : ""}</span>
          </div>
          <span class="file-status status-indexed">${escapeHtml(t("corpus.indexed"))}</span>
        </label>`).join("")}</div>`;
    } else {
      html += `<p class="hint">${escapeHtml(t("corpus.noDocuments"))}</p>`;
    }

    corpusInfo.innerHTML = html;
    corpusInfo.querySelectorAll("input.document-select").forEach((input) => {
      input.addEventListener("change", () => {
        if (input.checked) selectedDocumentIds.add(input.dataset.documentId);
        else selectedDocumentIds.delete(input.dataset.documentId);
        updateCorpusSelectionControls();
      });
    });
    updateCorpusSelectionControls();
  } catch (err) {
    indexedDocuments = [];
    selectedDocumentIds.clear();
    updateCorpusSelectionControls();
    corpusInfo.innerHTML = `<p class="hint">${escapeHtml(t("corpus.loadFailed"))}: ${escapeHtml(err.message)}</p>`;
  }
}

// No manual Refresh button: loadCorpusStatus() runs when the tab opens,
// after every upload and deletion, on page load, and on language change.
corpusSelectAll.addEventListener("change", () => {
  selectedDocumentIds = corpusSelectAll.checked
    ? new Set(indexedDocuments.map((doc) => String(doc.document_id)))
    : new Set();
  corpusInfo.querySelectorAll("input.document-select").forEach((input) => {
    input.checked = selectedDocumentIds.has(input.dataset.documentId);
  });
  updateCorpusSelectionControls();
});

deleteCorpusSelectedBtn.addEventListener("click", async () => {
  const documentIds = Array.from(selectedDocumentIds);
  if (!documentIds.length || !confirm(t("corpus.confirmDelete", { n: documentIds.length }))) return;
  deleteCorpusSelectedBtn.disabled = true;
  try {
    const response = await fetch("/api/rag/documents/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ document_ids: documentIds }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || response.statusText);
    selectedDocumentIds.clear();
    await loadCorpusStatus();
    showUploadStatus(t("corpus.deleted", { n: data.deleted_documents || 0 }));
  } catch (err) {
    alert(`${t("corpus.deleteFailed")}: ${err.message || err}`);
    updateCorpusSelectionControls();
  }
});

// The backend clear-collection endpoint is kept for compatibility, but the UI
// no longer exposes a Clear All button: Select All + Delete Selected covers
// bulk deletion, so no frontend caller remains.

// ------------------------------------------------------------------
// Console / activity log (unchanged from original)
// ------------------------------------------------------------------

function timestamp() {
  const locale = window.i18n && window.i18n.lang === "fa" ? "fa-IR" : "en-GB";
  return new Date().toLocaleTimeString(locale);
}

function clearLog() {
  log.innerHTML = `<div class="log-line log-muted">${escapeHtml(t("pipeline.logIdle"))}</div>`;
}

function appendLog(label, status, detailLines) {
  const muted = log.querySelector(".log-muted");
  if (muted) muted.remove();

  const line = document.createElement("div");
  line.className = `log-line log-${status}`;

  const time = document.createElement("span");
  time.className = "log-time";
  time.textContent = `[${timestamp()}]`;

  const text = document.createElement("span");
  text.textContent = label;

  line.appendChild(time);
  line.appendChild(text);

  if (detailLines && detailLines.length) {
    const pre = document.createElement("pre");
    pre.textContent = detailLines.join("\n");
    line.appendChild(pre);
  }

  log.appendChild(line);
  log.scrollTop = log.scrollHeight;
}

function logInfo(label) {
  appendLog(label, "info", null);
}

function logSuccess(label, detailLines) {
  appendLog(label, "success", detailLines);
}

function logError(label, err) {
  appendLog(label, "error", [String(err)]);
}

clearLogBtn.addEventListener("click", clearLog);

// ------------------------------------------------------------------
// Readable summaries (unchanged)
// ------------------------------------------------------------------

function formatCountsByType(obj, indent = "    ") {
  return Object.entries(obj)
    .sort((a, b) => b[1] - a[1])
    .map(([type, n]) => `${indent}${String(n).padStart(6)}  ${type}`);
}

// The activity log is machine/diagnostic output: it stays in English and
// pinned LTR (see .console-output in style.css), the same way a terminal
// would. Only the operator-facing labels around it are localised.
function formatIngestSummary(data) {
  const lines = [];
  if (typeof data.nodes_attempted === "number") {
    lines.push(`Nodes written:         ${data.nodes_attempted}`);
  }
  if (typeof data.relationships_in_graph === "number") {
    lines.push(`Relationships in graph: ${data.relationships_in_graph} (of ${data.edges_attempted ?? "?"} attempted)`);
  }
  if (data.edges_skipped) {
    lines.push(`Edges skipped (dangling ids): ${data.edges_skipped}`);
  }
  if (data.reset !== undefined) {
    lines.push(`Graph reset before load: ${data.reset ? "yes" : "no"}`);
  }
  if (data.node_counts_by_type) {
    lines.push("Node counts by type:");
    lines.push(...formatCountsByType(data.node_counts_by_type));
  }
  if (data.relationship_counts_by_type) {
    lines.push("Relationship counts by type:");
    lines.push(...formatCountsByType(data.relationship_counts_by_type));
  }
  if (lines.length === 0) {
    lines.push(JSON.stringify(data, null, 2));
  }
  return lines;
}

function formatAnalyzeSummary(data) {
  const lines = [];
  if (typeof data.elements_checked === "number") {
    lines.push(`Elements checked: ${data.elements_checked}`);
  }
  if (typeof data.issues_detected === "number") {
    lines.push(`Issues detected:  ${data.issues_detected}`);
  }
  if (data.issues_by_type) {
    lines.push("Issues by type:");
    lines.push(...formatCountsByType(data.issues_by_type));
  }
  if (lines.length === 0) {
    lines.push(JSON.stringify(data, null, 2));
  }
  return lines;
}

// ------------------------------------------------------------------
// HTTP helpers
// ------------------------------------------------------------------

async function getJSON(path) {
  const response = await fetch(path);
  const data = await response.json().catch(() => []);
  if (!response.ok) {
    throw new Error(`${response.status}: ${JSON.stringify(data)}`);
  }
  return data;
}

// ------------------------------------------------------------------
// Pipeline: Ingest then Analyze
// ------------------------------------------------------------------

ingestForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const projectId = projectIdInput.value.trim();
  const fileIds = Array.from(selectedIfcFileIds);
  if (!projectId || !fileIds.length) {
    logError(t("log.pipelineFailed"), t("log.needProjectAndFiles"));
    return;
  }

  ingestSubmitBtn.disabled = true;

  try {
    const payload = {
      file_ids: fileIds,
      storeys: ingestStoreyDropdown.getFilterValue(),
      types: ingestTypeDropdown.getFilterValue(),
      reset_all: ingestForm.querySelector('input[name="reset"]').checked,
      run_clash_detection: true,
    };
    logInfo(t("log.federating", { n: fileIds.length }));
    const response = await fetch(`/api/ifc/projects/${encodeURIComponent(projectId)}/ingest`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    const ingestData = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(JSON.stringify(ingestData.detail || ingestData));
    logSuccess(t("log.ingestDone"), formatIngestSummary(ingestData.load_summary || ingestData));
    if (ingestData.analysis) logSuccess(t("log.clashDone"), formatAnalyzeSummary(ingestData.analysis));
    (ingestData.per_file || []).forEach((item) => {
      const detail = item.status === "processed" ? `${item.nodes} nodes, ${item.edges} edges` : item.error;
      appendLog(`${item.filename}: ${item.status}`, item.status === "processed" ? "success" : "error", [detail]);
    });
    await loadIfcProjects();

    // The graph just changed (new/updated storeys, types, clashes) - refresh
    // the Results tab's filter options before loading results.
    await loadStoreys();
    await loadTypes();
    await loadResults();
  } catch (err) {
    logError(t("log.pipelineFailed"), err.message || err);
  } finally {
    ingestSubmitBtn.disabled = false;
  }
});

// ------------------------------------------------------------------
// Results: clash review cards (primary) + wide table (optional)
// ------------------------------------------------------------------

let currentStoreyFilter = "";
let currentTypesFilter = [];
let resultsLoadToken = 0;
// The rows currently loaded from the API, in normalized shape; the CSV export,
// the languagechange re-render, and both views work from this list instead of
// re-fetching or scraping the DOM.
let lastResultsRows = [];
// Rows after the instant client filters (search / cross-file / sort).
// Recomputed on every render cycle.
let visibleRowsCache = [];
// Card-view pagination: render a bounded page and reveal more on demand, so a
// 1000-row project never inserts thousands of DOM nodes at once.
const RESULTS_PAGE_SIZE = 50;
let resultsRenderCount = RESULTS_PAGE_SIZE;

function appendCurrentResultScope(url) {
  const projectId = projectIdInput ? projectIdInput.value.trim() : "";
  if (projectId) url.searchParams.set("project_id", projectId);
  Array.from(selectedIfcFileIds || []).forEach((fileId) => {
    url.searchParams.append("file_id", fileId);
  });
  return url;
}

function currentResultScopeKey() {
  const projectId = projectIdInput ? projectIdInput.value.trim() : "";
  const fileIds = Array.from(selectedIfcFileIds || []).sort();
  return `${projectId}\u0000${fileIds.join("\u0000")}`;
}

function invalidateResults() {
  // Supersede any in-flight request so a slow response for the old project or
  // file selection cannot repaint stale rows after the scope changed.
  resultsLoadToken += 1;
  lastResultsRows = [];
  visibleRowsCache = [];
  resultsRenderCount = RESULTS_PAGE_SIZE;
  showResultsState("loading");
  if (exportResultsBtn) exportResultsBtn.disabled = true;
  renderTableRows();
  updateResultsTopScroll();
}

// Maps the results sub-tab id to its label key, so log lines name the tab in
// the reader's language instead of echoing the internal identifier.
const RESULT_TAB_KEYS = {
  clashes: "results.clashes",
  violations: "results.violations",
  issues: "results.all",
};

resultTabButtons.forEach((btn) => {
  btn.addEventListener("click", () => {
    resultTabButtons.forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    activeTab = btn.dataset.target;
    loadResults();
  });
});

async function loadResults() {
  const loadToken = ++resultsLoadToken;
  lastResultsRows = [];
  visibleRowsCache = [];
  resultsRenderCount = RESULTS_PAGE_SIZE;
  showResultsState("loading");
  renderTableRows();
  updateResultsTopScroll();
  try {
    // Call the matching backend route depending on which tab is active
    let endpoint = "api/issues";
    if (activeTab === "clashes") endpoint = "api/clashes";
    if (activeTab === "violations") endpoint = "api/violations";

    const url = new URL(`/${endpoint}`, window.location.origin);
    if (currentStoreyFilter) url.searchParams.set("storey", currentStoreyFilter);
    if (currentTypesFilter && currentTypesFilter.length > 0) {
      url.searchParams.set("types", currentTypesFilter.join(","));
    }
    appendCurrentResultScope(url);

    const rows = await getJSON(url.pathname + url.search);
    if (loadToken !== resultsLoadToken) return;
    lastResultsRows = normalizeResultRows(rows);
    renderAllResults();
    logInfo(t("log.loadedRows", { n: lastResultsRows.length, tab: t(RESULT_TAB_KEYS[activeTab] || "results.all") }));
  } catch (err) {
    if (loadToken !== resultsLoadToken) return;
    showResultsState("error", err);
    resultsBody.innerHTML = `<tr><td colspan="13" class="empty">${escapeHtml(resultsErrorText(err))}</td></tr>`;
    updateResultsMeta();
    updateResultsTopScroll();
    logError(t("log.loadFailed", { tab: t(RESULT_TAB_KEYS[activeTab] || "results.all") }), err.message || err);
  }
}

// Loading / empty / error share one strip so an infrastructure failure can
// never be misread as an engineering result ("no clashes found").
function showResultsState(state, error) {
  if (!resultsStatus) return;
  // Any full-area state replaces whatever cards were showing.
  if (resultsCards) resultsCards.innerHTML = "";
  if (loadMoreResultsBtn) loadMoreResultsBtn.classList.add("hidden");
  resultsStatus.classList.remove("hidden", "is-empty", "is-error");
  if (state === "loading") {
    resultsStatus.classList.add("is-loading");
    resultsStatus.innerHTML = `
      <div class="results-skeleton"></div>
      <div class="results-skeleton"></div>
      <div class="results-skeleton"></div>`;
    if (resultsCount) resultsCount.textContent = t("results.loading");
    return;
  }
  resultsStatus.classList.remove("is-loading");
  if (state === "error") {
    resultsStatus.classList.add("is-error");
    lastResultsError = resultsErrorText(error);
    resultsStatus.textContent = lastResultsError;
    if (resultsCount) resultsCount.textContent = "";
    return;
  }
  // empty
  resultsStatus.classList.add("is-empty");
  resultsStatus.textContent = t("results.noResults");
  if (resultsCount) resultsCount.textContent = "";
}

// Kept so a language change can re-translate the prefix while preserving the
// backend's own detail text.
let lastResultsError = null;

// getJSON embeds the status code and raw body in the thrown message; surface
// the API's own detail ("Building graph is unavailable.") rather than JSON.
function resultsErrorText(error) {
  const raw = String((error && error.message) || error || "");
  const match = raw.match(/^(\d+):\s*([\s\S]*)$/);
  if (match) {
    try {
      const data = JSON.parse(match[2]);
      if (data && data.detail) return `${t("results.loadError")}: ${data.detail}`;
    } catch (e) { /* body was not JSON — fall through */ }
    return `${t("results.loadError")}: ${match[2]}`;
  }
  return `${t("results.loadError")}: ${raw}`;
}

// Maps one API row onto the field set the UI renders, resolving the legacy
// `a_*`/`b_*` aliases once so the card view, table view, and CSV exporter all
// agree on the same values.
function normalizeResultRows(rows) {
  return (rows || []).map((r) => {
    const aId = r.element_a_id ?? r.a_id ?? "";
    const bId = r.element_b_id ?? r.b_id ?? "";
    const issue = r.issue ?? "";
    return {
      uid: `${aId}|${bId}|${issue}`,
      aType: r.element_a_type ?? r.a_type ?? "",
      aName: r.element_a_name ?? r.a_name ?? "",
      aId,
      aGuid: r.element_a_guid ?? r.a_guid ?? "",
      aStorey: r.element_a_storey ?? r.a_storey_name ?? "",
      aFile: r.element_a_source_file ?? r.a_source_ifc_file ?? t("results.legacySource"),
      aFileId: r.element_a_source_file_id ?? r.a_source_file_id ?? "",
      aDiscipline: r.element_a_discipline ?? r.a_discipline ?? t("pipeline.unspecified"),
      bType: r.element_b_type ?? r.b_type ?? "",
      bName: r.element_b_name ?? r.b_name ?? "",
      bId,
      bGuid: r.element_b_guid ?? r.b_guid ?? "",
      bStorey: r.element_b_storey ?? r.b_storey_name ?? "",
      bFile: r.element_b_source_file ?? r.b_source_ifc_file ?? t("results.legacySource"),
      bFileId: r.element_b_source_file_id ?? r.b_source_file_id ?? "",
      bDiscipline: r.element_b_discipline ?? r.b_discipline ?? t("pipeline.unspecified"),
      crossFile: r.cross_file === true || r.relation_scope === "CROSS-FILE",
      issue,
      metricValue: r.metric !== undefined && r.metric !== null ? Number(r.metric) : null,
      metric: r.metric !== undefined && r.metric !== null ? Number(r.metric).toFixed(4) : "",
      projectId: r.project_id ?? "",
      anomalyA: r.anomaly_score_a ?? null,
      anomalyB: r.anomaly_score_b ?? null,
      anomalyCombined: r.combined_anomaly_score ?? null,
    };
  });
}

// Hard clashes and clearance violations read at a glance as badges; anything
// else (future issue kinds) falls back to its raw code so nothing is hidden.
function issueBadgeHtml(issue) {
  const value = String(issue || "");
  if (!value) return "";
  if (value === "CLASH") {
    return `<span class="issue-badge issue-clash">${escapeHtml(t("results.issueClash"))}</span>`;
  }
  if (value === "CLEARANCE_VIOLATION") {
    return `<span class="issue-badge issue-clearance">${escapeHtml(t("results.issueClearance"))}</span>`;
  }
  return `<span class="issue-badge issue-other">${escapeHtml(value)}</span>`;
}

function relationChipHtml(row) {
  return row.crossFile
    ? `<span class="relation-chip relation-cross">${escapeHtml(t("results.crossFileLabel"))}</span>`
    : `<span class="relation-chip relation-intra">${escapeHtml(t("results.sameModelLabel"))}</span>`;
}

// Metric block: label reflects what the backend actually measures (AABB
// overlap volume for clashes, separation distance for clearances). Units stay
// "model units" — the backend does not assert a physical unit.
function metricBlockHtml(row) {
  if (row.metricValue === null) return "";
  const label = row.issue === "CLASH"
    ? t("results.overlap")
    : row.issue === "CLEARANCE_VIOLATION" ? t("results.clearanceMetric") : t("results.colMetric");
  const unit = row.issue === "CLASH" ? t("results.modelUnitsCubic") : t("results.modelUnits");
  return `
    <div class="card-metric" dir="ltr">
      <span class="card-metric-label">${escapeHtml(label)}</span>
      <strong>${escapeHtml(row.metric)}</strong>
      <span class="card-metric-unit">${escapeHtml(unit)}</span>
    </div>`;
}

function clashElementColumn(type, name, file, storey, discipline) {
  const parts = [`<span class="card-el-type" dir="auto">${escapeHtml(type)}</span>`];
  if (name) parts.push(`<span class="card-el-name" dir="auto">${escapeHtml(name)}</span>`);
  if (file) parts.push(`<span class="card-el-file" dir="auto">${escapeHtml(file)}</span>`);
  if (storey) parts.push(`<span class="card-el-storey" dir="auto">${escapeHtml(storey)}</span>`);
  if (discipline && String(discipline).toLowerCase() !== "unspecified") {
    parts.push(`<span class="card-el-discipline" dir="auto">${escapeHtml(discipline)}</span>`);
  }
  return parts.join("");
}

function clashDetailsRow(label, value, mono) {
  if (value === undefined || value === null || String(value) === "") return "";
  return `
    <div class="clash-detail-row">
      <span class="clash-detail-label">${escapeHtml(label)}</span>
      <span class="clash-detail-value${mono ? " mono" : ""}" dir="${mono ? "ltr" : "auto"}">${escapeHtml(String(value))}</span>
    </div>`;
}

function clashDetailsHtml(row) {
  const numeric = (value) => (value === null || value === undefined ? "" : Number(value).toFixed(6));
  return `
    <div class="clash-details" hidden>
      <div class="clash-details-group">
        <h4 data-i18n="results.colElementA">${escapeHtml(t("results.colElementA"))}</h4>
        ${clashDetailsRow(t("results.graphId"), row.aId, true)}
        ${clashDetailsRow(t("results.ifcGuid"), row.aGuid, true)}
        ${clashDetailsRow(t("results.sourceFile"), row.aFile, false)}
        ${clashDetailsRow(t("results.discipline"), row.aDiscipline, false)}
        ${clashDetailsRow(t("results.storey"), row.aStorey, false)}
      </div>
      <div class="clash-details-group">
        <h4 data-i18n="results.colElementB">${escapeHtml(t("results.colElementB"))}</h4>
        ${clashDetailsRow(t("results.graphId"), row.bId, true)}
        ${clashDetailsRow(t("results.ifcGuid"), row.bGuid, true)}
        ${clashDetailsRow(t("results.sourceFile"), row.bFile, false)}
        ${clashDetailsRow(t("results.discipline"), row.bDiscipline, false)}
        ${clashDetailsRow(t("results.storey"), row.bStorey, false)}
      </div>
      <div class="clash-details-group">
        <h4 data-i18n="results.colIssue">${escapeHtml(t("results.colIssue"))}</h4>
        ${clashDetailsRow(t("results.colIssue"), row.issue, true)}
        ${clashDetailsRow(t("results.colMetric"), row.metric, true)}
        ${clashDetailsRow(t("results.projectLabel"), row.projectId, false)}
        ${clashDetailsRow(t("results.relation"), row.crossFile ? "CROSS-FILE" : "INTRA-FILE", true)}
        ${clashDetailsRow(`${t("results.anomalyScore")} (A)`, numeric(row.anomalyA), true)}
        ${clashDetailsRow(`${t("results.anomalyScore")} (B)`, numeric(row.anomalyB), true)}
        ${clashDetailsRow(`${t("results.anomalyScore")} (Σ)`, numeric(row.anomalyCombined), true)}
      </div>
    </div>`;
}

function clashCardHtml(row) {
  const canView3d = Boolean(row.aGuid && row.bGuid);
  return `
    <article class="clash-card" data-uid="${escapeHtml(row.uid)}">
      <header class="clash-card-header">
        ${issueBadgeHtml(row.issue)}
        ${relationChipHtml(row)}
        ${metricBlockHtml(row)}
      </header>
      <div class="clash-card-elements">
        <div class="clash-el clash-el-a">
          <span class="clash-el-side">${escapeHtml(t("results.colElementA"))}</span>
          ${clashElementColumn(row.aType, row.aName, row.aFile, row.aStorey, row.aDiscipline)}
        </div>
        <div class="clash-vs" aria-hidden="true">↔</div>
        <div class="clash-el clash-el-b">
          <span class="clash-el-side">${escapeHtml(t("results.colElementB"))}</span>
          ${clashElementColumn(row.bType, row.bName, row.bFile, row.bStorey, row.bDiscipline)}
        </div>
      </div>
      ${clashDetailsHtml(row)}
      <footer class="clash-card-actions">
        <button type="button" class="clash-action" data-action="details" aria-expanded="false">${escapeHtml(t("results.viewDetails"))}</button>
        <button type="button" class="clash-action" data-action="copy">${escapeHtml(t("results.copyRefs"))}</button>
        ${canView3d ? `<button type="button" class="clash-action" data-action="view3d">${escapeHtml(t("viewer.open"))}</button>` : ""}
      </footer>
    </article>`;
}

// ------------------------------------------------------------------
// Client-side instant filters over the already loaded rows. Backend filters
// (storey, IFC type, project, file scope) still reload via Apply.
// ------------------------------------------------------------------

function matchesResultsSearch(row, query) {
  const haystack = [
    row.aName, row.aType, row.aGuid, row.aId, row.aFile, row.aDiscipline, row.aStorey,
    row.bName, row.bType, row.bGuid, row.bId, row.bFile, row.bDiscipline, row.bStorey,
  ].join("\n").toLowerCase();
  return haystack.includes(query);
}

function computeVisibleResults() {
  const query = (resultsSearchInput && resultsSearchInput.value || "").trim().toLowerCase();
  const crossOnly = Boolean(crossFileFilterCheckbox && crossFileFilterCheckbox.checked);
  const mode = resultsSortSelect && resultsSortSelect.value || "metric-desc";
  let rows = lastResultsRows;
  if (crossOnly) rows = rows.filter((r) => r.crossFile);
  if (query) rows = rows.filter((r) => matchesResultsSearch(r, query));
  const byMetric = (a, b) => {
    const av = a.metricValue === null ? -Infinity : a.metricValue;
    const bv = b.metricValue === null ? -Infinity : b.metricValue;
    return bv - av;
  };
  if (mode === "metric-asc") rows = [...rows].sort((a, b) => -byMetric(a, b));
  else if (mode === "metric-desc") rows = [...rows].sort(byMetric);
  else if (mode === "type") rows = [...rows].sort((a, b) => String(a.aType).localeCompare(String(b.aType)));
  else if (mode === "source-file") rows = [...rows].sort((a, b) => String(a.aFile).localeCompare(String(b.aFile)));
  return rows;
}

function renderAllResults() {
  visibleRowsCache = computeVisibleResults();
  renderResultsCards();
  renderTableRows();
  updateResultsMeta();
}

function renderResultsCards() {
  if (!resultsCards) return;
  if (lastResultsRows.length === 0) return; // loading / error state owns the area
  if (visibleRowsCache.length === 0) {
    showResultsState("empty");
    return;
  }
  resultsStatus.classList.add("hidden");
  const page = visibleRowsCache.slice(0, resultsRenderCount);
  resultsCards.innerHTML = page.map(clashCardHtml).join("");
  if (loadMoreResultsBtn) {
    loadMoreResultsBtn.classList.toggle("hidden", visibleRowsCache.length <= resultsRenderCount);
  }
}

function updateResultsMeta() {
  const total = visibleRowsCache.length;
  if (resultsCount) {
    resultsCount.textContent = lastResultsRows.length === 0
      ? ""
      : t("results.showingRange", {
          shown: Math.min(resultsRenderCount, total),
          total,
        });
  }
  if (exportResultsBtn) exportResultsBtn.disabled = total === 0;
}

function renderTableRows() {
  if (lastResultsRows.length === 0) {
    // data-i18n so the languagechange handler can re-render this row without
    // re-fetching (see the handler near the bottom of this file).
    resultsBody.innerHTML = `<tr><td colspan="13" class="empty" data-i18n="results.loading">${escapeHtml(t("results.loading"))}</td></tr>`;
    return;
  }
  if (visibleRowsCache.length === 0) {
    resultsBody.innerHTML = `<tr><td colspan="13" class="empty" data-i18n="results.noResults">${escapeHtml(t("results.noResults"))}</td></tr>`;
    return;
  }
  resultsBody.innerHTML = visibleRowsCache
    .map((r) => {
      const relationLabel = r.crossFile ? t("results.crossFile") : t("results.intraFile");
      return `
        <tr>
          <td class="element-summary">${autoDir(`<strong>${escapeHtml(r.aType)}</strong>`)}<br>${autoDir(escapeHtml(r.aName))}</td>
          <td><code class="provenance-id">${escapeHtml(r.aId)}</code></td>
          <td><code class="provenance-id">${escapeHtml(r.aGuid)}</code></td>
          <td class="source-file">${autoDir(escapeHtml(r.aFile))}</td>
          <td>${autoDir(escapeHtml(r.aDiscipline))}</td>
          <td class="element-summary">${autoDir(`<strong>${escapeHtml(r.bType)}</strong>`)}<br>${autoDir(escapeHtml(r.bName))}</td>
          <td><code class="provenance-id">${escapeHtml(r.bId)}</code></td>
          <td><code class="provenance-id">${escapeHtml(r.bGuid)}</code></td>
          <td class="source-file">${autoDir(escapeHtml(r.bFile))}</td>
          <td>${autoDir(escapeHtml(r.bDiscipline))}</td>
          <td><span class="file-status relation-scope ${r.crossFile ? "cross-file" : "intra-file"}">${escapeHtml(relationLabel)}</span></td>
          <td>${issueBadgeHtml(r.issue)}</td>
          <td class="num">${r.metric}</td>
        </tr>`;
    })
    .join("");
}

// ------------------------------------------------------------------
// Clash card actions: details, copy, and 3D pair view (delegated, so a
// re-render never needs to rebind listeners).
// ------------------------------------------------------------------

async function copyTextToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (e) {
    // Clipboard API needs a secure context; fall back for plain HTTP deploys.
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "absolute";
    area.style.insetInlineStart = "-9999px";
    document.body.appendChild(area);
    area.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (err) { ok = false; }
    area.remove();
    return ok;
  }
}

function clashCopyText(row) {
  const lines = [
    `${t("results.colElementA")}: ${row.aType} — ${row.aName}`,
    `${t("results.ifcGuid")}: ${row.aGuid}`,
    `${t("results.elementId")}: ${row.aId}`,
    `${t("results.sourceFile")}: ${row.aFile}`,
    `${t("results.colElementB")}: ${row.bType} — ${row.bName}`,
    `${t("results.ifcGuid")}: ${row.bGuid}`,
    `${t("results.elementId")}: ${row.bId}`,
    `${t("results.sourceFile")}: ${row.bFile}`,
    `${t("results.colIssue")}: ${row.issue}`,
    `${t("results.colMetric")}: ${row.metric}`,
    `${t("results.projectLabel")}: ${row.projectId}`,
    `${t("results.relation")}: ${row.crossFile ? "CROSS-FILE" : "INTRA-FILE"}`,
  ];
  return lines.filter((line) => !line.endsWith(": ")).join("\n");
}

/**
 * Point the existing viewer at one clash pair. Deterministic: the record
 * already names both elements, so no harvesting or model involvement —
 * just the pair's bounds, its storey scenes, and two highlights.
 */
async function showClashInViewer(row) {
  const projectId = beginViewerSession(row.projectId);
  if (!projectId) return;

  const highlight = [];
  if (row.aGuid) highlight.push({ ifc_guid: row.aGuid, element_id: row.aId, name: row.aName, ifc_type: row.aType });
  if (row.bGuid) highlight.push({ ifc_guid: row.bGuid, element_id: row.bId, name: row.bName, ifc_type: row.bType });
  const fileIds = [row.aFileId, row.bFileId].filter(Boolean);

  try {
    const data = await fetchElementsByIds(projectId, [row.aGuid, row.bGuid], fileIds);
    const allBounds = data.elements || [];
    const sceneKeys = [...new Set(allBounds.map((item) => item.scene_key).filter(Boolean))];
    const scenes = await fetchScenes(projectId, sceneKeys, fileIds);

    const result = await window.bimViewer.show({
      sceneUrls: scenes.map((scene) => scene.url),
      highlight,
      bounds: allBounds,
    });
    if (result.superseded) return;

    viewerPlaceholder.classList.toggle("hidden", result.loaded > 0 || allBounds.length > 0);
    viewerPlaceholder.textContent = t("viewer.empty");

    const lines = [t("viewer.elements", { n: result.matched })];
    const storeys = scenes.map((scene) => scene.storeyName).filter(Boolean);
    if (storeys.length) lines.push(t("viewer.scenes", { names: storeys.join(" · ") }));
    if (!result.loaded) lines.push(t("viewer.boxFallback"));
    else if (!result.matched) lines.push(t("viewer.noMatch"));
    setViewerFooter(lines);
  } catch (error) {
    showViewerError(error);
  }
}

resultsCards.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const card = button.closest(".clash-card");
  if (!card) return;
  const row = lastResultsRows.find((r) => r.uid === card.dataset.uid);
  if (!row) return;

  if (button.dataset.action === "details") {
    const panel = card.querySelector(".clash-details");
    const open = panel.toggleAttribute("hidden") === false;
    button.setAttribute("aria-expanded", String(open));
    button.textContent = open ? t("results.hideDetails") : t("results.viewDetails");
  } else if (button.dataset.action === "copy") {
    const ok = await copyTextToClipboard(clashCopyText(row));
    if (ok) {
      button.textContent = t("results.copied");
      setTimeout(() => { button.textContent = t("results.copyRefs"); }, 1200);
    }
  } else if (button.dataset.action === "view3d") {
    showClashInViewer(row);
  }
});

// Instant controls: filter/sort the loaded rows and restart pagination.
[
  [resultsSearchInput, "input"],
  [resultsSortSelect, "change"],
  [crossFileFilterCheckbox, "change"],
].forEach(([element, eventName]) => {
  if (!element) return;
  element.addEventListener(eventName, () => {
    resultsRenderCount = RESULTS_PAGE_SIZE;
    renderAllResults();
  });
});

if (loadMoreResultsBtn) {
  loadMoreResultsBtn.addEventListener("click", () => {
    resultsRenderCount += RESULTS_PAGE_SIZE;
    renderResultsCards();
    updateResultsMeta();
  });
}

// Card view is the default review experience; the wide table stays available.
function setResultsView(mode) {
  if (!viewCardsBtn || !viewTableBtn) return;
  viewCardsBtn.classList.toggle("active", mode === "cards");
  viewTableBtn.classList.toggle("active", mode === "table");
  viewCardsBtn.setAttribute("aria-pressed", String(mode === "cards"));
  viewTableBtn.setAttribute("aria-pressed", String(mode === "table"));
  resultsCardView.classList.toggle("hidden", mode !== "cards");
  resultsTableWrap.classList.toggle("hidden", mode !== "table");
  // Recompute the top-scrollbar size for the container that just appeared.
  updateResultsTopScroll();
}

viewCardsBtn.addEventListener("click", () => setResultsView("cards"));
viewTableBtn.addEventListener("click", () => setResultsView("table"));
setResultsView("cards");

// ------------------------------------------------------------------
// CSV export of the visible Results table
// ------------------------------------------------------------------
// Exports exactly the rows on screen — same tab, storey/type filters, project,
// and file scope. The UTF-8 BOM keeps Persian element names legible when the
// file opens in Excel; RFC 4180 quoting handles commas, quotes, and newlines
// inside element names.

function csvCell(value) {
  return `"${String(value ?? "").replace(/"/g, '""')}"`;
}

function exportResultsCsv() {
  if (!visibleRowsCache.length) return;
  const headers = [
    `${t("results.colElementA")} — ${t("results.csv.type")}`,
    `${t("results.colElementA")} — ${t("results.csv.name")}`,
    `${t("results.colElementA")} — ${t("results.elementId")}`,
    `${t("results.colElementA")} — ${t("results.ifcGuid")}`,
    `${t("results.colElementA")} — ${t("results.sourceFile")}`,
    `${t("results.colElementA")} — ${t("results.discipline")}`,
    `${t("results.colElementA")} — ${t("results.storey")}`,
    `${t("results.colElementB")} — ${t("results.csv.type")}`,
    `${t("results.colElementB")} — ${t("results.csv.name")}`,
    `${t("results.colElementB")} — ${t("results.elementId")}`,
    `${t("results.colElementB")} — ${t("results.ifcGuid")}`,
    `${t("results.colElementB")} — ${t("results.sourceFile")}`,
    `${t("results.colElementB")} — ${t("results.discipline")}`,
    `${t("results.colElementB")} — ${t("results.storey")}`,
    t("results.relation"),
    t("results.colIssue"),
    t("results.colMetric"),
    t("results.projectLabel"),
    `${t("results.anomalyScore")} (A)`,
    `${t("results.anomalyScore")} (B)`,
    `${t("results.anomalyScore")} (Σ)`,
  ];
  const lines = [headers.map(csvCell).join(",")];
  visibleRowsCache.forEach((r) => {
    lines.push([
      r.aType, r.aName, r.aId, r.aGuid, r.aFile, r.aDiscipline, r.aStorey,
      r.bType, r.bName, r.bId, r.bGuid, r.bFile, r.bDiscipline, r.bStorey,
      r.crossFile ? "CROSS-FILE" : "INTRA-FILE",
      r.issue,
      r.metricValue === null ? "" : r.metricValue,
      r.projectId,
      r.anomalyA ?? "",
      r.anomalyB ?? "",
      r.anomalyCombined ?? "",
    ].map(csvCell).join(","));
  });
  const blob = new Blob(["\uFEFF" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `clash-results-${activeTab}-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

if (exportResultsBtn) exportResultsBtn.addEventListener("click", exportResultsCsv);

// ------------------------------------------------------------------
// Synchronized top scrollbar for the Results table
// ------------------------------------------------------------------
// The table region keeps its native horizontal scrolling (and remains the
// keyboard-accessible path, tabindex=0 + aria-label). This mirrors a second,
// slim scrollbar above it, next to the header the user orients by. The spacer
// div is sized to the table's real scroll width — no duplicated table, no
// second header, no fixed widths — and a guard flag stops the two scroll
// events from feeding back into each other.

function updateResultsTopScroll() {
  if (!resultsTableScroll || !resultsTopScroll || !resultsTopSpacer) return;
  const table = resultsTableScroll.querySelector("table");
  if (!table) return;
  const overflowWidth = Math.max(table.scrollWidth, resultsTableScroll.scrollWidth);
  const overflows = overflowWidth > resultsTableScroll.clientWidth + 1;
  resultsTopScroll.classList.toggle("has-overflow", overflows);
  resultsTopSpacer.style.width = `${overflowWidth}px`;
  resultsTopScroll.scrollLeft = resultsTableScroll.scrollLeft;
}

let syncingResultsScroll = false;
function mirrorResultsScroll(from, to) {
  if (syncingResultsScroll) return;
  syncingResultsScroll = true;
  to.scrollLeft = from.scrollLeft;
  syncingResultsScroll = false;
}

if (resultsTopScroll && resultsTableScroll) {
  resultsTopScroll.addEventListener("scroll", () =>
    mirrorResultsScroll(resultsTopScroll, resultsTableScroll));
  resultsTableScroll.addEventListener("scroll", () =>
    mirrorResultsScroll(resultsTableScroll, resultsTopScroll));
  // Window resizes resize the container; data, filter, tab, and language
  // changes resize the table itself. Observing both covers every trigger
  // without each caller having to remember to update the spacer.
  const resultsScrollObserver = new ResizeObserver(updateResultsTopScroll);
  resultsScrollObserver.observe(resultsTableScroll);
  const resultsTableEl = resultsTableScroll.querySelector("table");
  if (resultsTableEl) resultsScrollObserver.observe(resultsTableEl);
}

// ------------------------------------------------------------------
// Init
// ------------------------------------------------------------------
// Deferred to DOMContentLoaded so i18n.js has published window.i18n and
// applied the saved language before any dynamic string is rendered.
// (Previously these ran at parse time, which is also why a stale element
// reference could throw before the rest of the script had been evaluated.)

// Fetch storeys/types from Neo4j when the page loads or the Results tab
// is opened (for the Results tab filters). Both are graph-backed, so they
// only reflect data after a successful /ingest, unlike the Pipeline tab's
// storey/type dropdowns (which read the source IFC file directly).
// Shared fetch for the Results-tab backend filters: scope the request to the
// current project/file selection and drop the response if the scope changed
// while it was in flight (race guard). The success body differs per filter.
async function fetchScopedFilterData(path, onData) {
    const scopeKey = currentResultScopeKey();
    try {
        const url = appendCurrentResultScope(new URL(path, window.location.origin));
        const response = await fetch(url.pathname + url.search);
        const data = await response.json();
        if (scopeKey !== currentResultScopeKey()) return;
        onData(data);
    } catch (error) {
        console.error(`Failed to load ${path}:`, error);
    }
}

async function loadStoreys() {
    await fetchScopedFilterData('/api/filters/storeys', (data) => {
        const resultStoreySelect = document.getElementById('storeyFilter');
        if (!resultStoreySelect) return;
        const previous = resultStoreySelect.value;
        const storeys = data.storeys || [];
        resultStoreySelect.innerHTML = '';
        const allOption = document.createElement('option');
        allOption.value = '';
        allOption.textContent = t('results.allStoreys');
        allOption.setAttribute('data-i18n', 'results.allStoreys');
        resultStoreySelect.appendChild(allOption);
        storeys.forEach((storey) => {
            const opt = document.createElement('option');
            opt.value = storey;
            opt.textContent = storey;
            resultStoreySelect.appendChild(opt);
        });
        if (previous && storeys.includes(previous)) {
            resultStoreySelect.value = previous;
        }
    });
}

async function loadTypes() {
    await fetchScopedFilterData('/api/filters/types', (data) => {
        resultTypeDropdown.setOptions(
            (data.types || []).map((type) => ({ value: type, label: type }))
        );
    });
}

// ------------------------------------------------------------------
// Project-scoped multi-IFC upload, selection and combined filter metadata.
// ------------------------------------------------------------------

function updateIfcSelectionControls() {
  const availableIds = new Set(currentProjectIfcFiles.map((item) => item.file_id));
  selectedIfcDeleteIds = new Set(Array.from(selectedIfcDeleteIds)
    .filter((fileId) => availableIds.has(fileId)));
  syncSelectAll(ifcDeleteSelectAll, ifcDeleteSelectedBtn, selectedIfcDeleteIds.size, availableIds.size);
}

function populateIngestFilterSelects(data) {
    const rescanBtn = document.getElementById('rescan-dataset-btn');

    const storeyOptions = (data.storeys || []).map(storey => ({ value: storey, label: storey }));
    ingestStoreyDropdown.setOptions(storeyOptions);

    const typeOptions = (data.types || []).map(({ type, count }) => ({ value: type, label: type, count }));
    ingestTypeDropdown.setOptions(typeOptions);

    if (rescanBtn) rescanBtn.disabled = false;

    if (data.errors && data.errors.length > 0) {
        data.errors.forEach(e => console.warn("IFC filters:", e));
    }
}

function resetIngestFilterSelects(placeholder) {
    const rescanBtn = document.getElementById('rescan-dataset-btn');

    ingestStoreyDropdown.disable(placeholder);
    ingestTypeDropdown.disable(placeholder);

    if (rescanBtn) rescanBtn.disabled = true;
}

async function uploadIfcFiles(files) {
    files = Array.from(files || []);
    if (!files.length || files.some((file) => !file.name.toLowerCase().endsWith(".ifc"))) {
        logError(t("log.ifcUploadFailed"), t("log.allMustBeIfc"));
        return;
    }
    const projectId = projectIdInput.value.trim();
    if (!projectId) {
        logError(t("log.ifcUploadFailed"), t("log.needProject"));
        return;
    }
    renderSelectedFileNames(ifcDropZoneFile, files);
    ifcDropZone.classList.add("has-file");
    resetIngestFilterSelects("pipeline.scanning");

    const formData = new FormData();
    if (files.length === 1) {
        formData.append("file", files[0]);
    } else {
        files.forEach((item) => formData.append("files", item));
    }
    formData.append("project_id", projectId);

    try {
        const response = await fetch("/api/ifc/upload", { method: "POST", body: formData });
        const data = await response.json().catch(() => ({}));

        if (!response.ok) {
            throw new Error(data.detail || `${response.status} ${response.statusText}`);
        }

        populateIngestFilterSelects(data);
        (data.files || []).forEach((item) => {
          const ok = item.status === "uploaded";
          appendLog(`${item.filename}: ${item.status}`, ok ? "success" : "error", [ok ? t("log.readyForIngestion") : item.error]);
          if (ok) selectedIfcFileIds.add(item.file_id);
        });
        await loadIfcProjects();
    } catch (err) {
        ifcDropZoneFile.textContent = t("log.uploadFailedShort", { n: files.length });
        ifcDropZone.classList.remove("has-file");
        resetIngestFilterSelects("pipeline.selectFilesFirst");
        logError(t("log.ifcUploadFailed"), err.message || err);
    }
}

async function loadIfcProjects() {
  try {
    const data = await getJSON("/api/ifc/projects");
    ifcProjects = data.projects || [];
    const projectId = projectIdInput.value.trim();
    const project = ifcProjects.find((item) => item.project_id === projectId);
    const files = project ? project.files : [];
    currentProjectIfcFiles = files;
    const availableIds = new Set(files.map((item) => item.file_id));
    selectedIfcFileIds = new Set(Array.from(selectedIfcFileIds).filter((id) => availableIds.has(id)));
    selectedIfcDeleteIds = new Set(Array.from(selectedIfcDeleteIds).filter((id) => availableIds.has(id)));
    if (!selectedIfcFileIds.size) files.filter((item) => item.status === "ingested").forEach((item) => selectedIfcFileIds.add(item.file_id));
    const updateCombinedFilters = () => {
      const selected = files.filter((item) => selectedIfcFileIds.has(item.file_id));
      const storeys = Array.from(new Set(selected.flatMap((item) => item.storeys || []))).sort();
      const counts = {};
      selected.flatMap((item) => item.types || []).forEach((item) => {
        counts[item.type] = (counts[item.type] || 0) + Number(item.count || 0);
      });
      populateIngestFilterSelects({storeys, types: Object.entries(counts).map(([type, count]) => ({type, count}))});
    };
    updateCombinedFilters();
    const registeredHtml = files.length ? files.map((item) => `
      <div class="stored-file-row selectable-file">
        <input type="checkbox" data-file-id="${escapeHtml(item.file_id)}"
               aria-label="${escapeHtml(t("pipeline.selectForAnalysis", { name: item.filename }))}"
               ${selectedIfcFileIds.has(item.file_id) ? "checked" : ""} />
        <div class="stored-file-copy">
          <strong dir="auto">${escapeHtml(item.filename)}</strong>
          <span class="stored-file-meta">${escapeHtml(item.discipline || t("pipeline.unspecified"))} · ${Number(item.node_count || 0)} ${escapeHtml(t("pipeline.nodes"))} · ${escapeHtml(item.ingested_at || item.uploaded_at || "")}</span>
        </div>
        <span class="file-status status-${escapeHtml(item.status)}">${escapeHtml(item.processing_status || item.status)}</span>
        <label class="selection-toggle deletion-toggle">
          <input type="checkbox" class="ifc-delete-select" data-delete-file-id="${escapeHtml(item.file_id)}"
                 aria-label="${escapeHtml(t("pipeline.selectForDeletion", { name: item.filename }))}"
                 ${selectedIfcDeleteIds.has(item.file_id) ? "checked" : ""} />
          <span>${escapeHtml(t("common.delete"))}</span>
        </label>
      </div>`).join("") : `<p class="hint">${escapeHtml(t("pipeline.noModels"))}</p>`;
    const legacyHtml = (data.legacy_unregistered_files || []).length ? `
      <p class="hint">${escapeHtml(t("pipeline.legacyFiles"))}</p>
      ${(data.legacy_unregistered_files || []).map((item) => `
        <div class="stored-file-row"><div class="stored-file-copy"><strong dir="auto">${escapeHtml(item.filename)}</strong></div>
        <span class="file-status">${escapeHtml(t("pipeline.notImported"))}</span></div>`).join("")}` : "";
    ifcModelList.innerHTML = registeredHtml + legacyHtml;
    ifcModelList.querySelectorAll("input[data-file-id]").forEach((input) => input.addEventListener("change", () => {
      if (input.checked) selectedIfcFileIds.add(input.dataset.fileId);
      else selectedIfcFileIds.delete(input.dataset.fileId);
      updateCombinedFilters();
      updateIfcSelectionControls();
      invalidateResults();
      if (activeNav === "results") {
        loadStoreys();
        loadTypes();
        loadResults();
      }
    }));
    ifcModelList.querySelectorAll("input[data-delete-file-id]").forEach((input) => input.addEventListener("change", () => {
      if (input.checked) selectedIfcDeleteIds.add(input.dataset.deleteFileId);
      else selectedIfcDeleteIds.delete(input.dataset.deleteFileId);
      updateIfcSelectionControls();
    }));
    updateIfcSelectionControls();
  } catch (err) {
    currentProjectIfcFiles = [];
    updateIfcSelectionControls();
    ifcModelList.innerHTML = `<p class="hint">${escapeHtml(t("pipeline.listFailed"))}: ${escapeHtml(err.message)}</p>`;
  }
}

ifcDeleteSelectAll.addEventListener("change", () => {
  selectedIfcDeleteIds = ifcDeleteSelectAll.checked
    ? new Set(currentProjectIfcFiles.map((item) => item.file_id))
    : new Set();
  ifcModelList.querySelectorAll("input[data-delete-file-id]").forEach((input) => {
    input.checked = selectedIfcDeleteIds.has(input.dataset.deleteFileId);
  });
  updateIfcSelectionControls();
});

ifcDeleteSelectedBtn.addEventListener("click", async () => {
  const projectId = projectIdInput.value.trim();
  const availableIds = new Set(currentProjectIfcFiles.map((item) => item.file_id));
  const fileIds = Array.from(selectedIfcDeleteIds).filter((fileId) => availableIds.has(fileId));
  if (!projectId || !fileIds.length) return;
  if (!confirm(t("pipeline.confirmDelete", { n: fileIds.length }))) return;

  ifcDeleteSelectedBtn.disabled = true;
  try {
    const response = await fetch(`/api/ifc/projects/${encodeURIComponent(projectId)}/files/delete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_ids: fileIds }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(formatApiError(data.detail, response.statusText));

    fileIds.forEach((fileId) => {
      selectedIfcFileIds.delete(fileId);
      selectedIfcDeleteIds.delete(fileId);
    });
    currentStoreyFilter = "";
    currentTypesFilter = [];
    invalidateResults();
    window.bimViewer?.clear();
    if (typeof clearSustainabilityResults === "function") {
      sustainabilityState.requestToken += 1;
      clearSustainabilityResults();
    }
    await loadIfcProjects();
    await Promise.all([loadStoreys(), loadTypes(), loadResults()]);
    if (typeof renderSustainabilityScope === "function") renderSustainabilityScope();
    logInfo(t("pipeline.deleted", { n: data.deleted_files || 0 }));
  } catch (err) {
    logError(t("pipeline.deleteFailed"), err.message || err);
    updateIfcSelectionControls();
  }
});

ifcDropZone.addEventListener("click", () => ifcFileInput.click());

ifcFileInput.addEventListener("change", () => {
    if (ifcFileInput.files && ifcFileInput.files.length) uploadIfcFiles(ifcFileInput.files);
});

setupDropZoneDrag(ifcDropZone);

ifcDropZone.addEventListener("drop", (e) => {
    const files = Array.from(e.dataTransfer.files || []);
    if (files.length && files.every((item) => item.name.toLowerCase().endsWith(".ifc"))) {
        const dt = new DataTransfer();
        files.forEach((item) => dt.items.add(item));
        ifcFileInput.files = dt.files;
        uploadIfcFiles(files);
    } else {
        logError(t("log.ifcUploadFailed"), t("log.onlyIfc"));
    }
});

const rescanDatasetBtn = document.getElementById('rescan-dataset-btn');
if (rescanDatasetBtn) {
    rescanDatasetBtn.disabled = false;
    rescanDatasetBtn.addEventListener('click', loadIfcProjects);
}
projectIdInput.addEventListener("change", () => {
  selectedIfcFileIds.clear();
  selectedIfcDeleteIds.clear();
  currentStoreyFilter = "";
  currentTypesFilter = [];
  invalidateResults();
  loadIfcProjects().finally(() => {
    if (activeNav === "results") {
      loadStoreys();
      loadTypes();
      loadResults();
    }
  });
});

// Handle clicking "Apply Filters" on the Results table
const applyBtn = document.getElementById('applyFiltersBtn');
if (applyBtn) {
    applyBtn.addEventListener('click', () => {
        const storeySelect = document.getElementById('storeyFilter');
        currentStoreyFilter = storeySelect ? storeySelect.value : "";

        currentTypesFilter = resultTypeDropdown.getFilterValue() || [];

        loadResults();
    });
}

// Handle clicking "Clear" on the Results filters: resets backend filters and
// every instant client filter, then reloads.
const clearFiltersBtn = document.getElementById('clearFiltersBtn');
if (clearFiltersBtn) {
    clearFiltersBtn.addEventListener('click', () => {
        const storeySelect = document.getElementById('storeyFilter');
        if (storeySelect) storeySelect.value = "";

        resultTypeDropdown.selectAll();

        if (resultsSearchInput) resultsSearchInput.value = "";
        if (crossFileFilterCheckbox) crossFileFilterCheckbox.checked = false;
        if (resultsSortSelect) resultsSortSelect.value = "metric-desc";

        currentStoreyFilter = "";
        currentTypesFilter = [];

        loadResults();
    });
}

// ------------------------------------------------------------------
// Language changes
// ------------------------------------------------------------------
// i18n.js rewrites every element carrying data-i18n; anything this script
// rendered dynamically has to be re-rendered here. Re-fetching would be
// wasteful and would discard state, so each case re-renders from what is
// already in hand. Each concern lives in its own refresh function so the
// handler below reads as a table of contents.

// Dropdown toggle labels / option lists hold live selection state. The
// explicit storey "All" option is translated; IFC names stay intact.
function refreshDropdownLabels() {
  ingestStoreyDropdown.relabel();
  ingestTypeDropdown.relabel();
  resultTypeDropdown.relabel();

  const storeySelect = document.getElementById("storeyFilter");
  const allStoreysOption = storeySelect && storeySelect.querySelector('option[value=""]');
  if (allStoreysOption) allStoreysOption.textContent = t("results.allStoreys");
}

// Panels whose entire body is generated. Each is a cheap re-render of
// already-fetched data except the two that must hit the API again to rebuild
// their markup; both are idempotent GETs.
function refreshActivePanel() {
  if (activeNav === "corpus") loadCorpusStatus();
  if (activeNav === "pipeline") loadIfcProjects();
}

// The idle log line is the only translated content in the console.
function refreshLogLabel() {
  const idle = log.querySelector(".log-muted");
  if (idle) idle.textContent = t("pipeline.logIdle");
}

// The results table: re-render the placeholder row. Real rows contain element
// names from the model, which are not translated, so reloading them would cost
// a request for no benefit — but the card actions, badges, and count line are
// UI strings, so loaded rows are re-rendered from memory (no fetch). The status
// strip's empty message is a plain UI string; the error keeps the backend's
// detail text and re-translates only its prefix.
function refreshResultsLabels() {
  const placeholder = resultsBody.querySelector("td.empty");
  if (placeholder && placeholder.hasAttribute("data-i18n")) {
    placeholder.textContent = t(placeholder.getAttribute("data-i18n"));
  }
  if (resultsStatus && !resultsStatus.classList.contains("hidden")) {
    if (resultsStatus.classList.contains("is-empty")) {
      resultsStatus.textContent = t("results.noResults");
    } else if (resultsStatus.classList.contains("is-error")) {
      const detail = lastResultsError ? lastResultsError.split(": ").slice(1).join(": ") : "";
      resultsStatus.textContent = detail ? `${t("results.loadError")}: ${detail}` : t("results.loadError");
    }
  }
  if (lastResultsRows.length) {
    renderAllResults();
  } else {
    updateResultsMeta();
  }
}

// Viewer buttons on past messages: relabel from the state stored on each element
// rather than replaying the conversation. The viewer's own footer/placeholder
// text is left as-is when a scene is loaded: re-deriving it would need the
// answer's payload, and the footer is refreshed on the next show() anyway.
function refreshViewerLabels() {
  document.querySelectorAll(".chat-view-3d").forEach((button) => {
    button.textContent = viewerButtonLabel(
      button.dataset.viewerRelated === "true", button.dataset.viewerTypes || "",
    );
  });
  if (!viewerPlaceholder.classList.contains("hidden")) {
    viewerPlaceholder.textContent = t("viewer.empty");
  }
}

document.addEventListener("languagechange", () => {
  syncWorkspaceHeader();
  refreshDropdownLabels();
  refreshActivePanel();
  refreshLogLabel();
  refreshResultsLabels();
  refreshViewerLabels();
});

// ------------------------------------------------------------------
// Init
// ------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
  syncWorkspaceHeader();
  loadCorpusStatus();
  loadIfcProjects();
  loadStoreys();   // Results tab storey filter (Neo4j-backed)
  loadTypes();     // Results tab type filter (Neo4j-backed)
  loadResults();
});
// Pipeline tab storey/type filters populate on IFC upload, not on page load — see uploadIfcFile().
