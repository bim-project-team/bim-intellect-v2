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
  constructor(rootId, { emptyTextKey = "pipeline.noneFound" } = {}) {
    this.root = document.getElementById(rootId);
    if (!this.root) return;

    this.toggleBtn = this.root.querySelector(".multiselect-toggle");
    this.toggleLabel = this.root.querySelector(".multiselect-toggle-label");
    this.panel = this.root.querySelector(".multiselect-panel");
    this.optionsEl = this.root.querySelector(".multiselect-options");
    this.searchInput = this.root.querySelector(".multiselect-search");
    this.emptyTextKey = emptyTextKey;
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
      btn.addEventListener("click", () => {
        this.options.forEach((o) => this.selected.add(o.value));
        this._syncCheckboxes();
        this._updateLabel();
      })
    );
    this.root.querySelectorAll('[data-action="none"]').forEach((btn) =>
      btn.addEventListener("click", () => {
        this.selected.clear();
        this._syncCheckboxes();
        this._updateLabel();
      })
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
    this.options = options || [];
    this.selected = new Set(
      this.options.map((o) => o.value).filter((value) => previous.has(value))
    );
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

    this.optionsEl.innerHTML = visible
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

    this.optionsEl.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
      cb.addEventListener("change", () => {
        if (cb.checked) this.selected.add(cb.value);
        else this.selected.delete(cb.value);
        this._updateLabel();
      });
    });
  }

  _syncCheckboxes() {
    this.optionsEl.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
      cb.checked = this.selected.has(cb.value);
    });
  }

  _updateLabel() {
    const n = this.selected.size;
    if (this.options.length === 0) {
      this.toggleLabel.textContent = t(this.emptyTextKey);
    } else if (n === 0) {
      this.toggleLabel.textContent = t("pipeline.allSelected");
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
let selectedIfcFileIds = new Set();
let ifcProjects = [];
let sustainabilityState = {
  scopeKey: null,
  summary: null,
  elements: [],
  materials: [],
  findings: [],
  requestToken: 0,
};

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
const resultTabButtons = document.querySelectorAll("#tab-results .tab-btn");
const refreshResultsBtn = document.getElementById("refresh-results-btn");
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

const dropZone = document.getElementById("drop-zone");
const pdfFileInput = document.getElementById("pdf-file");
const dropZoneFile = document.getElementById("drop-zone-file");
const uploadForm = document.getElementById("upload-form");
const uploadSubmit = document.getElementById("upload-submit");
const uploadStatus = document.getElementById("upload-status");
const uploadStatusText = document.getElementById("upload-status-text");

const refreshCorpusBtn = document.getElementById("refresh-corpus-btn");
const clearCorpusBtn = document.getElementById("clear-corpus-btn");
const corpusInfo = document.getElementById("corpus-info");

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

  // The context rail, the 3D map, and the model toggle belong to chat only.
  const onChat = activeNav === "chat";
  modelToggleWrap.classList.toggle("hidden", !onChat);
  railToggle.classList.toggle("hidden", !onChat);
  contextRail.classList.toggle("hidden", !onChat);
  viewerToggle.classList.toggle("hidden", !onChat);
  viewerPanel.classList.toggle("hidden", !onChat);
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
    }
  });
});

// ------------------------------------------------------------------
// 3D map
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
async function fetchElementBounds(projectId, sceneKeys, ifcTypes) {
  const url = new URL("/api/model/elements", window.location.origin);
  url.searchParams.set("project_id", projectId);
  (sceneKeys || []).forEach((key) => url.searchParams.append("scene_key", key));
  (ifcTypes || []).forEach((type) => url.searchParams.append("ifc_type", type));
  const response = await fetch(url);
  if (!response.ok) return { elements: [], truncated: false };
  return response.json();
}

/** Scene descriptors from the ingest-time manifest, restricted to the given keys. */
async function fetchScenes(projectId, sceneKeys) {
  const wanted = new Set((sceneKeys || []).filter(Boolean));
  if (!wanted.size) return [];
  const url = new URL("/api/model/manifest", window.location.origin);
  url.searchParams.set("project_id", projectId);
  const response = await fetch(url);
  if (!response.ok) return [];
  const manifest = await response.json();
  const scenes = [];
  Object.values(manifest.files || {}).forEach((file) => {
    (file.scenes || []).forEach((scene) => {
      if (!wanted.has(scene.scene_key)) return;
      scenes.push({
        url: `/api/model/scene/${encodeURIComponent(manifest.project_id)}`
          + `/${encodeURIComponent(file.file_id)}/${encodeURIComponent(scene.scene_key)}.glb`,
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
async function showInViewer(visualization, { related = false } = {}) {
  if (!window.bimViewer) return;
  window.bimViewer.attach(viewerCanvas);
  openDrawer(viewerPanel, viewerToggle);

  const projectId = visualization.project_id || viewerProjectId();
  if (!projectId) {
    setViewerFooter([t("viewer.unavailable")]);
    return;
  }

  viewerPlaceholder.classList.remove("hidden");
  viewerPlaceholder.textContent = t("viewer.loading");
  setViewerFooter([]);

  const highlight = related ? [] : visualization.highlight || [];
  const relatedTypes = related ? visualization.related_types || [] : [];
  const requestedScenes = related ? [] : (visualization.scenes || []).slice(0, MAX_VIEWER_SCENES);

  try {
    // Bounds first: in the opt-in view the elements themselves determine which
    // storeys are worth loading, so the scene list cannot be known before this.
    const bounds = await fetchElementBounds(projectId, requestedScenes, relatedTypes);
    const allBounds = bounds.elements || [];
    const sceneKeys = related
      ? [...new Set(allBounds.map((item) => item.scene_key).filter(Boolean))]
      : requestedScenes;
    const scenes = await fetchScenes(projectId, sceneKeys);

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
    viewerPlaceholder.classList.remove("hidden");
    viewerPlaceholder.textContent = t("viewer.empty");
    setViewerFooter([`${t("common.error")}: ${error.message || error}`]);
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

function containsRtlText(text) {
  // Hebrew, Arabic, Arabic Supplement/Extended, and presentation forms.
  // Persian code points are covered by these Unicode blocks.
  return /[\u0590-\u08FF\uFB1D-\uFDFF\uFE70-\uFEFC]/u.test(String(text || ""));
}

function applyTextDirection(element, text) {
  const isRtl = containsRtlText(text);
  element.dir = isRtl ? "rtl" : "ltr";
  element.classList.toggle("rtl", isRtl);
  element.classList.toggle("ltr", !isRtl);
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
  // Preserve line breaks in assistant responses
  bubble.innerHTML = escapeHtml(text).replace(/\n/g, "<br>");
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

chatInput.addEventListener("input", () => applyTextDirection(chatInput, chatInput.value));

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

dropZone.addEventListener("click", () => pdfFileInput.click());

pdfFileInput.addEventListener("change", () => {
  selectedPdfFiles = Array.from(pdfFileInput.files || []).filter((file) => file.name.toLowerCase().endsWith(".pdf"));
  renderSelectedFileNames(dropZoneFile, selectedPdfFiles);
  dropZone.classList.toggle("has-file", selectedPdfFiles.length > 0);
});

["dragenter", "dragover", "dragleave", "drop"].forEach((eventName) => {
  dropZone.addEventListener(eventName, (e) => {
    e.preventDefault();
    e.stopPropagation();
  });
});

["dragenter", "dragover"].forEach((eventName) => {
  dropZone.addEventListener(eventName, () => dropZone.classList.add("dragover"));
});

["dragleave", "drop"].forEach((eventName) => {
  dropZone.addEventListener(eventName, () => dropZone.classList.remove("dragover"));
});

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

function hideUploadStatus() {
  uploadStatus.classList.add("hidden");
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

    let html = `
      <div class="stat-row"><span>${escapeHtml(t("corpus.collection"))}</span><span dir="auto">${escapeHtml(collection)}</span></div>
      <div class="stat-row"><span>${escapeHtml(t("corpus.indexedChunks"))}</span><span class="num">${escapeHtml(count)}</span></div>
      <div class="stat-row"><span>${escapeHtml(t("corpus.documents"))}</span><span class="num">${documents.length}</span></div>
      <div class="stat-row"><span>${escapeHtml(t("corpus.storage"))}</span><span class="path">${escapeHtml(dir)}</span></div>
    `;

    if (documents.length > 0) {
      html += `<div class="stored-file-list">${documents.map((doc) => `
        <div class="stored-file-row">
          <div class="stored-file-copy">
            <strong dir="auto">${escapeHtml(doc.filename)}</strong>
            <span class="stored-file-meta">${escapeHtml(doc.document_domain || "regulation")}${doc.standard_name ? ` · ${escapeHtml(doc.standard_name)} ${escapeHtml(doc.standard_version || "")}` : ""} · ${Number(doc.chunk_count || 0)} ${escapeHtml(t("corpus.chunks"))} · ${Number(doc.page_count || 0)} ${escapeHtml(t("corpus.pages"))}${doc.ingested_at ? ` · ${escapeHtml(doc.ingested_at)}` : ""}</span>
          </div>
          <span class="file-status status-indexed">${escapeHtml(t("corpus.indexed"))}</span>
        </div>`).join("")}</div>`;
    } else {
      html += `<p class="hint">${escapeHtml(t("corpus.noDocuments"))}</p>`;
    }

    corpusInfo.innerHTML = html;
  } catch (err) {
    corpusInfo.innerHTML = `<p class="hint">${escapeHtml(t("corpus.loadFailed"))}: ${escapeHtml(err.message)}</p>`;
  }
}

refreshCorpusBtn.addEventListener("click", loadCorpusStatus);

clearCorpusBtn.addEventListener("click", async () => {
  if (!confirm(t("corpus.confirmClear"))) {
    return;
  }
  clearCorpusBtn.disabled = true;
  try {
    const response = await fetch("/api/rag/clear", { method: "DELETE" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      alert(`${t("corpus.clearFailed")}: ${data.detail || response.statusText}`);
      return;
    }
    loadCorpusStatus();
  } catch (err) {
    alert(`${t("common.error")}: ${err.message}`);
  } finally {
    clearCorpusBtn.disabled = false;
  }
});

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

function addRepeatedParams(paramName, rawValue, searchParams) {
  if (!rawValue) return;

  if (Array.isArray(rawValue)) {
      rawValue.filter(Boolean).forEach((v) => searchParams.append(paramName, v));
      return;
  }

  rawValue
    .split(",")
    .map((v) => v.trim())
    .filter(Boolean)
    .forEach((v) => searchParams.append(paramName, v));
}

function csvToRepeatedParams(paramName, rawValue, searchParams) {
  if (!rawValue) return;
  rawValue
    .split(";")
    .map((v) => v.trim())
    .filter(Boolean)
    .forEach((v) => searchParams.append(paramName, v));
}

async function postJSON(path, params) {
  const url = new URL(path, window.location.origin);
  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      if (value === undefined || value === null || value === "") return;
      if (Array.isArray(value)) {
          value.forEach(v => url.searchParams.append(key, v));
      } else {
          url.searchParams.set(key, value);
      }
    });
  }
  const response = await fetch(url, { method: "POST" });
  const data = await response.json().catch(() => ({ detail: "Non-JSON response" }));
  if (!response.ok) {
    throw new Error(`${response.status}: ${JSON.stringify(data)}`);
  }
  return data;
}

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
// Results table
// ------------------------------------------------------------------

let currentStoreyFilter = "";
let currentTypesFilter = [];

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

refreshResultsBtn.addEventListener("click", loadResults);

async function loadResults() {
  resultsBody.innerHTML = `<tr><td colspan="6" class="empty" data-i18n="results.loading">${escapeHtml(t("results.loading"))}</td></tr>`;
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
    if (projectIdInput && projectIdInput.value.trim()) {
      url.searchParams.set("project_id", projectIdInput.value.trim());
    }

    const rows = await getJSON(url.pathname + url.search);
    renderRows(rows);
    logInfo(t("log.loadedRows", { n: rows ? rows.length : 0, tab: t(RESULT_TAB_KEYS[activeTab] || "results.all") }));
  } catch (err) {
    resultsBody.innerHTML = `<tr><td colspan="6" class="empty">${escapeHtml(t("common.error"))}: ${escapeHtml(err.message || err)}</td></tr>`;
    logError(t("log.loadFailed", { tab: t(RESULT_TAB_KEYS[activeTab] || "results.all") }), err.message || err);
  }
}

function renderRows(rows) {
  if (!rows || rows.length === 0) {
    // data-i18n so the languagechange handler can re-render this row without
    // re-fetching (see the handler near the bottom of this file).
    resultsBody.innerHTML = `<tr><td colspan="6" class="empty" data-i18n="results.noResults">${escapeHtml(t("results.noResults"))}</td></tr>`;
    return;
  }
  resultsBody.innerHTML = rows
    .map((r) => `
      <tr>
        <td>${autoDir(`<strong>${escapeHtml(r.a_type)}</strong>`)}<br>${autoDir(escapeHtml(r.a_name))}<br><small class="guid">${escapeHtml(r.a_guid ?? r.a_id ?? "")}</small></td>
        <td>${autoDir(escapeHtml(r.a_source_ifc_file ?? t("results.legacySource")))}</td>
        <td>${autoDir(`<strong>${escapeHtml(r.b_type)}</strong>`)}<br>${autoDir(escapeHtml(r.b_name))}<br><small class="guid">${escapeHtml(r.b_guid ?? r.b_id ?? "")}</small></td>
        <td>${autoDir(escapeHtml(r.b_source_ifc_file ?? t("results.legacySource")))}${r.cross_file ? `<br><span class="file-status">${escapeHtml(t("results.crossFile"))}</span>` : ""}</td>
        <td>${escapeHtml(r.issue ?? "")}</td>
        <td class="num">${r.metric !== undefined && r.metric !== null ? Number(r.metric).toFixed(4) : ""}</td>
      </tr>
    `)
    .join("");
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
async function loadStoreys() {
    try {
        const response = await fetch('/api/filters/storeys');
        const data = await response.json();

        const resultStoreySelect = document.getElementById('storeyFilter');
        if (resultStoreySelect) {
            const previous = resultStoreySelect.value;
            resultStoreySelect.innerHTML = '';
            const allOption = document.createElement('option');
            allOption.value = '';
            allOption.textContent = t('results.allStoreys');
            allOption.setAttribute('data-i18n', 'results.allStoreys');
            resultStoreySelect.appendChild(allOption);
            (data.storeys || []).forEach(storey => {
                const opt = document.createElement('option');
                opt.value = storey;
                opt.textContent = storey;
                resultStoreySelect.appendChild(opt);
            });
            if (previous && (data.storeys || []).includes(previous)) {
                resultStoreySelect.value = previous;
            }
        }
    } catch (error) {
        console.error("Failed to load storeys:", error);
    }
}

async function loadTypes() {
    try {
        const response = await fetch('/api/filters/types');
        const data = await response.json();

        const resultTypeSelect = document.getElementById('typeFilter');
        if (resultTypeSelect) {
            const previouslySelected = new Set(
                Array.from(resultTypeSelect.selectedOptions).map((opt) => opt.value)
            );
            resultTypeSelect.innerHTML = '';
            (data.types || []).forEach(type => {
                const opt = document.createElement('option');
                opt.value = type;
                opt.textContent = type;
                if (previouslySelected.has(type)) opt.selected = true;
                resultTypeSelect.appendChild(opt);
            });
        }
    } catch (error) {
        console.error("Failed to load types:", error);
    }
}

// ------------------------------------------------------------------
// Project-scoped multi-IFC upload, selection and combined filter metadata.
// ------------------------------------------------------------------

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
    const availableIds = new Set(files.map((item) => item.file_id));
    selectedIfcFileIds = new Set(Array.from(selectedIfcFileIds).filter((id) => availableIds.has(id)));
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
      <label class="stored-file-row selectable-file">
        <input type="checkbox" data-file-id="${escapeHtml(item.file_id)}" ${selectedIfcFileIds.has(item.file_id) ? "checked" : ""} />
        <div class="stored-file-copy">
          <strong dir="auto">${escapeHtml(item.filename)}</strong>
          <span class="stored-file-meta">${escapeHtml(item.discipline || t("pipeline.unspecified"))} · ${Number(item.node_count || 0)} ${escapeHtml(t("pipeline.nodes"))} · ${escapeHtml(item.ingested_at || item.uploaded_at || "")}</span>
        </div>
        <span class="file-status status-${escapeHtml(item.status)}">${escapeHtml(item.processing_status || item.status)}</span>
      </label>`).join("") : `<p class="hint">${escapeHtml(t("pipeline.noModels"))}</p>`;
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
    }));
  } catch (err) {
    ifcModelList.innerHTML = `<p class="hint">${escapeHtml(t("pipeline.listFailed"))}: ${escapeHtml(err.message)}</p>`;
  }
}

ifcDropZone.addEventListener("click", () => ifcFileInput.click());

ifcFileInput.addEventListener("change", () => {
    if (ifcFileInput.files && ifcFileInput.files.length) uploadIfcFiles(ifcFileInput.files);
});

["dragenter", "dragover", "dragleave", "drop"].forEach((eventName) => {
    ifcDropZone.addEventListener(eventName, (e) => {
        e.preventDefault();
        e.stopPropagation();
    });
});

["dragenter", "dragover"].forEach((eventName) => {
    ifcDropZone.addEventListener(eventName, () => ifcDropZone.classList.add("dragover"));
});

["dragleave", "drop"].forEach((eventName) => {
    ifcDropZone.addEventListener(eventName, () => ifcDropZone.classList.remove("dragover"));
});

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
  loadIfcProjects();
});

// Handle clicking "Apply Filters" on the Results table
const applyBtn = document.getElementById('applyFiltersBtn');
if (applyBtn) {
    applyBtn.addEventListener('click', () => {
        const storeySelect = document.getElementById('storeyFilter');
        currentStoreyFilter = storeySelect ? storeySelect.value : "";

        const typeSelect = document.getElementById('typeFilter');
        currentTypesFilter = typeSelect
            ? Array.from(typeSelect.selectedOptions).map(opt => opt.value)
            : [];

        loadResults();
    });
}

// Handle clicking "Clear" on the Results table filters
const clearFiltersBtn = document.getElementById('clearFiltersBtn');
if (clearFiltersBtn) {
    clearFiltersBtn.addEventListener('click', () => {
        const storeySelect = document.getElementById('storeyFilter');
        if (storeySelect) storeySelect.value = "";

        const typeSelect = document.getElementById('typeFilter');
        if (typeSelect) Array.from(typeSelect.options).forEach(opt => (opt.selected = false));

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
// already in hand.
document.addEventListener("languagechange", () => {
  syncWorkspaceHeader();

  // Dropdown toggle labels / option lists hold live selection state.
  ingestStoreyDropdown.relabel();
  ingestTypeDropdown.relabel();

  // "All storeys" is the only translated <option>; the rest are storey names.
  const storeySelect = document.getElementById("storeyFilter");
  const allOption = storeySelect && storeySelect.querySelector('option[value=""]');
  if (allOption) allOption.textContent = t("results.allStoreys");

  // Panels whose entire body is generated. Each of these is a cheap re-render
  // of already-fetched data except the two that must hit the API again to
  // rebuild their markup; both are idempotent GETs.
  if (activeNav === "corpus") loadCorpusStatus();
  if (activeNav === "pipeline") loadIfcProjects();

  // The idle log line is the only translated content in the console.
  const idle = log.querySelector(".log-muted");
  if (idle) idle.textContent = t("pipeline.logIdle");

  // The results table: re-render only the placeholder row. Real rows contain
  // element names from the model, which are not translated, so reloading them
  // would cost a request for no benefit.
  const placeholder = resultsBody.querySelector("td.empty");
  if (placeholder && placeholder.hasAttribute("data-i18n")) {
    placeholder.textContent = t(placeholder.getAttribute("data-i18n"));
  }

  // Viewer buttons on past messages: relabel from the state stored on each
  // element rather than replaying the conversation.
  document.querySelectorAll(".chat-view-3d").forEach((button) => {
    button.textContent = viewerButtonLabel(
      button.dataset.viewerRelated === "true", button.dataset.viewerTypes || "",
    );
  });
  // The viewer's own footer/placeholder text. Left as-is when a scene is
  // loaded: re-deriving it would need the answer's payload, and the footer is
  // refreshed on the next show() anyway.
  if (!viewerPlaceholder.classList.contains("hidden")) {
    viewerPlaceholder.textContent = t("viewer.empty");
  }
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
