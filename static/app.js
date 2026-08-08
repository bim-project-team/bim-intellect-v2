// BIM-Intellect Frontend — Chat, Pipeline, Corpus, Results

// ------------------------------------------------------------------
// Reusable checkbox-dropdown multiselect widget
// ------------------------------------------------------------------
class MultiSelectDropdown {
  constructor(rootId, { emptyText = "None found" } = {}) {
    this.root = document.getElementById(rootId);
    if (!this.root) return;

    this.toggleBtn = this.root.querySelector(".multiselect-toggle");
    this.toggleLabel = this.root.querySelector(".multiselect-toggle-label");
    this.panel = this.root.querySelector(".multiselect-panel");
    this.optionsEl = this.root.querySelector(".multiselect-options");
    this.searchInput = this.root.querySelector(".multiselect-search");
    this.emptyText = emptyText;
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

    this.disable("Select an IFC file first");
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

  disable(placeholderText) {
    this.options = [];
    this.selected.clear();
    this.toggleBtn.disabled = true;
    this.toggleLabel.textContent = placeholderText;
    this.optionsEl.innerHTML = "";
    this.close();
  }

  setOptions(options) {
    // options: [{value, label, count?}]
    this.options = options || [];
    this.selected.clear();
    this.toggleBtn.disabled = this.options.length === 0;
    this._renderOptions();
    this._updateLabel();
  }

  getSelected() {
    return Array.from(this.selected);
  }

  _renderOptions() {
    const filter = this.searchInput ? this.searchInput.value.trim().toLowerCase() : "";
    const visible = filter
      ? this.options.filter((o) => o.label.toLowerCase().includes(filter))
      : this.options;

    if (this.options.length === 0) {
      this.optionsEl.innerHTML = `<div class="multiselect-empty">${this.emptyText}</div>`;
      return;
    }
    if (visible.length === 0) {
      this.optionsEl.innerHTML = `<div class="multiselect-empty">No matches</div>`;
      return;
    }

    this.optionsEl.innerHTML = visible
      .map((o) => {
        const checked = this.selected.has(o.value) ? "checked" : "";
        const countHtml = o.count !== undefined ? `<span class="option-count">${o.count}</span>` : "";
        return `
          <label class="multiselect-option">
            <input type="checkbox" value="${o.value}" ${checked} />
            <span class="option-label">${o.label}</span>
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
      this.toggleLabel.textContent = this.emptyText;
    } else if (n === 0) {
      this.toggleLabel.textContent = "All";
    } else if (n <= 2) {
      const labels = this.options.filter((o) => this.selected.has(o.value)).map((o) => o.label);
      this.toggleLabel.textContent = labels.join(", ");
    } else {
      this.toggleLabel.textContent = `${n} selected`;
    }
  }
}

// ------------------------------------------------------------------
// State
// ------------------------------------------------------------------
let activeTab = "clashes"; // for results sub-tabs
let activeNav = "chat";    // for main navigation
let selectedElementId = null;

// ------------------------------------------------------------------
// DOM refs
// ------------------------------------------------------------------
const navButtons = document.querySelectorAll(".nav-btn");
const tabContents = document.querySelectorAll(".tab-content");

const chatMessages = document.getElementById("chat-messages");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const chatSend = document.getElementById("chat-send");
const chatMeta = document.getElementById("chat-meta");

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
const ifcPathHidden = document.getElementById("ifc_path");

const ingestStoreyDropdown = new MultiSelectDropdown("ingestStoreyFilter", { emptyText: "No storeys found in this file" });
const ingestTypeDropdown = new MultiSelectDropdown("ingestTypeFilter", { emptyText: "No types found in this file" });

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
// Navigation (main tabs)
// ------------------------------------------------------------------
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

    // Auto-load corpus status when opening that tab
    if (target === "corpus") {
      loadCorpusStatus();
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
// Chat
// ------------------------------------------------------------------

function escapeHtml(str) {
  if (!str) return "";
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function formatTime() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function appendChatMessage(role, text, sources) {
  // Remove welcome screen on first real message
  const welcome = chatMessages.querySelector(".chat-welcome");
  if (welcome) welcome.remove();

  const msgDiv = document.createElement("div");
  msgDiv.className = `chat-msg ${role}`;

  const bubble = document.createElement("div");
  bubble.className = "chat-bubble";
  // Preserve line breaks in assistant responses
  bubble.innerHTML = escapeHtml(text).replace(/\n/g, "<br>");
  msgDiv.appendChild(bubble);

  const meta = document.createElement("div");
  meta.className = "chat-meta-line";
  meta.textContent = `${role === "user" ? "You" : "Assistant"} — ${formatTime()}`;
  msgDiv.appendChild(meta);

  if (sources && sources.length > 0) {
    const tagsDiv = document.createElement("div");
    tagsDiv.className = "chat-sources";
    sources.forEach((src) => {
      const tag = document.createElement("span");
      tag.className = `chat-source-tag ${src.type || "regulation"}`;
      if (src.type === "regulation") {
        tag.textContent =
          `Clause ${src.clause_id || "?"}, Page ${src.page_number || "?"}`;

        tag.title =
          `Source: ${src.source || "Regulation"} | Page ${src.page_number || "?"}`;
      } else if (src.type === "graph") {
        tag.textContent =
          `${src.ifc_type || "Element"} ${src.name || src.element_id || ""}`;

        tag.title =
          `Element ID: ${src.element_id || "?"}`;
      } else {
        tag.textContent = JSON.stringify(src);
      }
      tagsDiv.appendChild(tag);
    });
    msgDiv.appendChild(tagsDiv);
  }

  chatMessages.appendChild(msgDiv);
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

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
  chatInput.disabled = true;
  chatSend.disabled = true;
  showTypingIndicator();

  try {
    const payload = {
      question: question.trim(),
      selected_element_id: selectedElementId,
    };

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
    appendChatMessage("assistant", data.answer || "(no answer)", data.sources);

    // Update sidebar
    updateChatMeta(data);
  } catch (err) {
    hideTypingIndicator();
    appendChatMessage("assistant", `Network error: ${err.message}`);
  } finally {
    chatInput.disabled = false;
    chatSend.disabled = false;
    chatInput.focus();
  }
}

function updateChatMeta(data) {
  const parts = [];
  if (data.used_vector) parts.push("Regulations");
  if (data.used_graph) parts.push("Building Graph");

  let html = `<p class="hint">Sources consulted:</p>`;
  if (parts.length === 0) {
    html += `<p class="hint">None — no relevant data found.</p>`;
  } else {
    html += `<ul style="margin:0;padding-left:1.2rem;font-size:0.8rem;">`;
    parts.forEach((p) => {
      html += `<li>${p}</li>`;
    });
    html += `</ul>`;
  }

  if (data.sources && data.sources.length > 0) {
    html += `<p class="hint" style="margin-top:0.75rem;">Citations (${data.sources.length}):</p>`;
    html += `<div style="display:flex;flex-wrap:wrap;gap:0.3rem;">`;
    data.sources.forEach((src) => {
      if (src.type === "regulation") {
        const clause = escapeHtml(String(src.clause_id || "?"));
        const page = escapeHtml(String(src.page_number || "?"));
        const source = escapeHtml(String(src.source || "Regulation"));

        html += `
          <span
            class="chat-source-tag regulation"
            title="Source: ${source} | Page ${page}">
            Clause ${clause}, Page ${page}
          </span>
        `;
      } else if (src.type === "graph") {
        const elementId = escapeHtml(String(src.element_id || ""));
        const label = escapeHtml(
          String(src.ifc_type || src.name || "Element")
        );

        html += `
          <span
            class="chat-source-tag graph"
            title="Element ID: ${elementId}">
            ${label}
          </span>
        `;
      }
    });
    html += `</div>`;
  }

  chatMeta.innerHTML = html;
}

chatForm.addEventListener("submit", (e) => {
  e.preventDefault();
  sendChat(chatInput.value);
});

// Example question buttons
document.querySelectorAll(".example-q").forEach((btn) => {
  btn.addEventListener("click", () => {
    const q = btn.dataset.q;
    if (q) sendChat(q);
  });
});

// ------------------------------------------------------------------
// PDF Upload (drag & drop + click)
// ------------------------------------------------------------------

let selectedFile = null;

dropZone.addEventListener("click", () => pdfFileInput.click());

pdfFileInput.addEventListener("change", () => {
  if (pdfFileInput.files && pdfFileInput.files[0]) {
    selectedFile = pdfFileInput.files[0];
    dropZoneFile.textContent = selectedFile.name;
    dropZone.classList.add("has-file");
  }
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
  const files = e.dataTransfer.files;
  if (files && files[0] && files[0].name.toLowerCase().endsWith(".pdf")) {
    selectedFile = files[0];
    dropZoneFile.textContent = selectedFile.name;
    dropZone.classList.add("has-file");
    // Sync to hidden input so form submission works
    const dt = new DataTransfer();
    dt.items.add(selectedFile);
    pdfFileInput.files = dt.files;
  } else {
    showUploadStatus("Only PDF files are accepted.", true);
  }
});

function showUploadStatus(text, isError) {
  uploadStatusText.textContent = text;
  uploadStatus.classList.remove("hidden", "success", "error");
  uploadStatus.classList.add(isError ? "error" : "success");
}

function hideUploadStatus() {
  uploadStatus.classList.add("hidden");
}

uploadForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const file = pdfFileInput.files[0];
  if (!file) {
    showUploadStatus("Please select a PDF file first.", true);
    return;
  }

  uploadSubmit.disabled = true;
  showUploadStatus("Uploading and embedding... please wait.");

  const formData = new FormData();
  formData.append("file", file);
  const docId = document.getElementById("doc-id").value.trim();
  if (docId) formData.append("doc_id", docId);

  try {
    const response = await fetch("/api/rag/upload", {
      method: "POST",
      body: formData,
    });
    const data = await response.json().catch(() => ({
      detail: `${response.status} ${response.statusText}`,
    }));

    if (!response.ok) {
      showUploadStatus(`Upload failed: ${data.detail || "Unknown error"}`, true);
      return;
    }

    showUploadStatus(
      `Uploaded "${data.filename}" — ${data.chunks_extracted} chunk(s) extracted, ${data.chunks_stored} stored in ChromaDB.`,
      false
    );

    // Reset form
    pdfFileInput.value = "";
    selectedFile = null;
    dropZoneFile.textContent = "";
    dropZone.classList.remove("has-file");
    document.getElementById("doc-id").value = "";

    // Refresh corpus status if visible
    if (activeNav === "corpus") loadCorpusStatus();
  } catch (err) {
    showUploadStatus(`Network error: ${err.message}`, true);
  } finally {
    uploadSubmit.disabled = false;
  }
});

// ------------------------------------------------------------------
// Corpus Status
// ------------------------------------------------------------------

async function loadCorpusStatus() {
  corpusInfo.innerHTML = `<p class="hint">Loading...</p>`;
  try {
    const response = await fetch("/api/rag/status");
    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
      corpusInfo.innerHTML = `<p class="hint">Error: ${data.detail || response.statusText}</p>`;
      return;
    }

    const count = data.document_count ?? "?";
    const collection = data.collection ?? "?";
    const dir = data.chroma_dir ?? "?";
    const samples = data.sample_ids || [];

    let html = `
      <div class="stat-row"><span>Collection</span><span>${escapeHtml(collection)}</span></div>
      <div class="stat-row"><span>Documents</span><span>${count}</span></div>
      <div class="stat-row"><span>Storage</span><span>${escapeHtml(dir)}</span></div>
    `;

    if (samples.length > 0) {
      html += `<div class="stat-row" style="flex-direction:column;align-items:flex-start;gap:0.25rem;">
        <span>Sample IDs:</span>
        <span style="font-size:0.75rem;color:var(--fg-muted);">${samples.map(escapeHtml).join(", ")}</span>
      </div>`;
    }

    corpusInfo.innerHTML = html;
  } catch (err) {
    corpusInfo.innerHTML = `<p class="hint">Failed to load: ${err.message}</p>`;
  }
}

refreshCorpusBtn.addEventListener("click", loadCorpusStatus);

clearCorpusBtn.addEventListener("click", async () => {
  if (!confirm("Are you sure you want to delete the entire ChromaDB collection? This cannot be undone.")) {
    return;
  }
  clearCorpusBtn.disabled = true;
  try {
    const response = await fetch("/api/rag/clear", { method: "DELETE" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      alert(`Failed to clear: ${data.detail || response.statusText}`);
      return;
    }
    loadCorpusStatus();
  } catch (err) {
    alert(`Error: ${err.message}`);
  } finally {
    clearCorpusBtn.disabled = false;
  }
});

// ------------------------------------------------------------------
// Console / activity log (unchanged from original)
// ------------------------------------------------------------------

function timestamp() {
  return new Date().toLocaleTimeString();
}

function clearLog() {
  log.innerHTML = `<div class="log-line log-muted">Idle. Run the pipeline to see activity here.</div>`;
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
  const fd = new FormData(ingestForm);

  const ifcPath = fd.get("ifc_path");
  if (!ifcPath) {
    logError("Pipeline failed", "Select an IFC file first (drag & drop or click to browse).");
    return;
  }

  ingestSubmitBtn.disabled = true;

  try {
    const url = new URL("/api/ingest", window.location.origin);
    url.searchParams.set("ifc_path", ifcPath);

    // Grab multiple selections from the checkbox-dropdowns if they exist, otherwise fallback to old csv behavior
    if (ingestStoreyDropdown.root && ingestTypeDropdown.root) {
        const storeys = ingestStoreyDropdown.getSelected();
        const types = ingestTypeDropdown.getSelected();

        addRepeatedParams("storey", storeys, url.searchParams);
        addRepeatedParams("type", types, url.searchParams);
    } else {
        csvToRepeatedParams("storey", fd.get("storey"), url.searchParams);
        csvToRepeatedParams("type", fd.get("type"), url.searchParams);
    }

    url.searchParams.set("reset", ingestForm.querySelector('input[name="reset"]').checked);

    logInfo("Starting ingestion (extract IFC → load into Neo4j)...");
    const ingestData = await postJSON(url.pathname + url.search);
    logSuccess("Ingestion complete", formatIngestSummary(ingestData.load_summary || ingestData));

    logInfo("Starting clash & clearance detection...");
    const analyzeData = await postJSON("/api/analyze");
    logSuccess("Clash detection complete", formatAnalyzeSummary(analyzeData));

    // The graph just changed (new/updated storeys, types, clashes) - refresh
    // the Results tab's filter options before loading results.
    await loadStoreys();
    await loadTypes();
    await loadResults();
  } catch (err) {
    logError("Pipeline failed", err.message || err);
  } finally {
    ingestSubmitBtn.disabled = false;
  }
});

// ------------------------------------------------------------------
// Results table
// ------------------------------------------------------------------

let currentStoreyFilter = "";
let currentTypesFilter = [];

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
  resultsBody.innerHTML = `<tr><td colspan="6" class="empty">Loading...</td></tr>`;
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

    const rows = await getJSON(url.pathname + url.search);
    renderRows(rows);
    logInfo(`Loaded ${rows ? rows.length : 0} row(s) into "${activeTab}" results.`);
  } catch (err) {
    resultsBody.innerHTML = `<tr><td colspan="6" class="empty">Error: ${err}</td></tr>`;
    logError(`Failed to load "${activeTab}" results`, err.message || err);
  }
}

function renderRows(rows) {
  if (!rows || rows.length === 0) {
    resultsBody.innerHTML = `<tr><td colspan="6" class="empty">No results.</td></tr>`;
    return;
  }
  resultsBody.innerHTML = rows
    .map((r) => `
      <tr>
        <td>${r.a_type ?? ""}</td>
        <td>${r.a_name ?? ""}</td>
        <td>${r.b_type ?? ""}</td>
        <td>${r.b_name ?? ""}</td>
        <td>${r.issue ?? ""}</td>
        <td>${r.metric !== undefined && r.metric !== null ? Number(r.metric).toFixed(4) : ""}</td>
      </tr>
    `)
    .join("");
}

// ------------------------------------------------------------------
// Init
// ------------------------------------------------------------------
loadCorpusStatus();
loadResults();

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
            resultStoreySelect.innerHTML = '<option value="">All Storeys</option>';
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
// Pipeline form: upload an IFC file, then populate its storey/type
// filters. Nothing is scanned until the user picks a file — no more
// scanning the whole dataset/ifc/ directory up front.
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

async function uploadIfcFile(file) {
    if (!file.name.toLowerCase().endsWith(".ifc")) {
        logError("IFC upload failed", "Only .ifc files are accepted.");
        return;
    }

    ifcDropZoneFile.textContent = `${file.name} — uploading...`;
    ifcDropZone.classList.add("has-file");
    resetIngestFilterSelects("Scanning file...");

    const formData = new FormData();
    formData.append("file", file);

    try {
        const response = await fetch("/api/ifc/upload", { method: "POST", body: formData });
        const data = await response.json().catch(() => ({}));

        if (!response.ok) {
            throw new Error(data.detail || `${response.status} ${response.statusText}`);
        }

        ifcPathHidden.value = data.ifc_path;
        ifcDropZoneFile.textContent = data.filename;
        populateIngestFilterSelects(data);
        logSuccess("IFC file uploaded", [`${data.filename} — ${data.storeys.length} storey(s), ${data.types.length} type(s) found`]);
    } catch (err) {
        ifcPathHidden.value = "";
        ifcDropZoneFile.textContent = `${file.name} — upload failed`;
        ifcDropZone.classList.remove("has-file");
        resetIngestFilterSelects("Select an IFC file first");
        logError("IFC upload failed", err.message || err);
    }
}

async function rescanCurrentIfcFile() {
    const ifcPath = ifcPathHidden.value;
    if (!ifcPath) return;

    const rescanBtn = document.getElementById('rescan-dataset-btn');
    if (rescanBtn) rescanBtn.disabled = true;

    try {
        const url = `/api/filters/dataset?refresh=true&ifc_path=${encodeURIComponent(ifcPath)}`;
        const response = await fetch(url);
        const data = await response.json();
        populateIngestFilterSelects(data);
        if (data.errors && data.errors.length > 0) {
            logError("Rescan finished with issues", data.errors);
        } else {
            logSuccess("Rescanned IFC file", [`${data.storeys.length} storey(s), ${data.types.length} type(s) found`]);
        }
    } catch (err) {
        logError("Rescan failed", err.message || err);
    } finally {
        if (rescanBtn) rescanBtn.disabled = false;
    }
}

ifcDropZone.addEventListener("click", () => ifcFileInput.click());

ifcFileInput.addEventListener("change", () => {
    if (ifcFileInput.files && ifcFileInput.files[0]) {
        uploadIfcFile(ifcFileInput.files[0]);
    }
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
    const files = e.dataTransfer.files;
    if (files && files[0] && files[0].name.toLowerCase().endsWith(".ifc")) {
        const dt = new DataTransfer();
        dt.items.add(files[0]);
        ifcFileInput.files = dt.files;
        uploadIfcFile(files[0]);
    } else {
        logError("IFC upload failed", "Only .ifc files are accepted.");
    }
});

const rescanDatasetBtn = document.getElementById('rescan-dataset-btn');
if (rescanDatasetBtn) {
    rescanDatasetBtn.addEventListener('click', rescanCurrentIfcFile);
}

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

// Run this when the script loads
loadStoreys();  // Results tab storey filter (Neo4j-backed)
loadTypes();    // Results tab type filter (Neo4j-backed)
// Pipeline tab storey/type filters populate on IFC upload, not on page load — see uploadIfcFile().