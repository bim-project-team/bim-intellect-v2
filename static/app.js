// BIM-Intellect Frontend — Chat, Pipeline, Corpus, Results

// ------------------------------------------------------------------
// State
// ------------------------------------------------------------------
let activeTab = "clashes"; // for results sub-tabs
let activeNav = "chat";    // for main navigation

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
      tag.className = `chat-source-tag ${src.type}`;
      if (src.type === "regulation") {
        tag.textContent = `Clause ${src.clause_id || "?"}`;
        tag.title = `Source: ${src.source || "Regulation"}`;
      } else if (src.type === "graph") {
        tag.textContent = `${src.ifc_type || "Element"} ${src.name || src.element_id || ""}`;
        tag.title = `Element ID: ${src.element_id || "?"}`;
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
    const response = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
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
        html += `<span class="chat-source-tag regulation">Clause ${escapeHtml(src.clause_id || "?")}</span>`;
      } else if (src.type === "graph") {
        html += `<span class="chat-source-tag graph" title="${escapeHtml(src.element_id || "")}">${escapeHtml(src.ifc_type || "Element")}</span>`;
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
  ingestSubmitBtn.disabled = true;

  try {
    const url = new URL("/api/ingest", window.location.origin);
    url.searchParams.set("ifc_path", fd.get("ifc_path"));
    
    // Grab multiple selections from the selects if they exist, otherwise fallback to old csv behavior
    const storeySelect = document.getElementById('ingestStoreyFilter');
    const typeSelect = document.getElementById('ingestTypeFilter');
    
    if (storeySelect && typeSelect) {
        const storeys = Array.from(storeySelect.selectedOptions).map(o => o.value);
        const types = Array.from(typeSelect.selectedOptions).map(o => o.value);
        
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
    const params = new URLSearchParams();
    
    // Call the matching backend route depending on which tab is active
    let endpoint = "api/issues";
    if (activeTab === "clashes") endpoint = "api/clashes";
    if (activeTab === "violations") endpoint = "api/violations";
    
    const urlStr = `/${endpoint}`;
    const rows = await getJSON(urlStr);
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

// Fetch storeys from Neo4j when the page loads (for results table only)
async function loadStoreys() {
    try {
        const response = await fetch('/api/filters/storeys');
        const data = await response.json();
        
        const resultStoreySelect = document.getElementById('storeyFilter');
        const ingestStoreySelect = document.getElementById('ingestStoreyFilter');
        
        if (resultStoreySelect) resultStoreySelect.innerHTML = '<option value="">All Storeys</option>';
        if (ingestStoreySelect) ingestStoreySelect.innerHTML = '<option value="">All Storeys (Leave empty)</option>';
        
        if (data.storeys) {
            data.storeys.forEach(storey => {
                if (resultStoreySelect) {
                    const opt1 = document.createElement('option');
                    opt1.value = storey;
                    opt1.textContent = storey;
                    resultStoreySelect.appendChild(opt1);
                }
                
                if (ingestStoreySelect) {
                    const opt2 = document.createElement('option');
                    opt2.value = storey;
                    opt2.textContent = storey;
                    ingestStoreySelect.appendChild(opt2);
                }
            });
        }
    } catch (error) {
        console.error("Failed to load storeys:", error);
    }
}

// Handle clicking "Apply Filters" on the Results table
const applyBtn = document.getElementById('applyFiltersBtn');
if (applyBtn) {
    applyBtn.addEventListener('click', () => {
        const storeySelect = document.getElementById('storeyFilter');
        currentStoreyFilter = storeySelect ? storeySelect.value : "";
        
        const typeSelect = document.getElementById('typeFilter');
        if (typeSelect) {
            currentTypesFilter = Array.from(typeSelect.selectedOptions).map(opt => opt.value);
        }
        
        loadResults();
    });
}

// Run this when the script loads
loadStoreys();