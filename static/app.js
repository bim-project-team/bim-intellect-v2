// Chat layout logic
const chatTabs = document.querySelectorAll('.nav-btn');
const tabContents = document.querySelectorAll('.tab-content');

chatTabs.forEach(btn => {
  btn.addEventListener('click', () => {
    chatTabs.forEach(b => b.classList.remove('active'));
    tabContents.forEach(c => c.classList.remove('active'));
    
    btn.classList.add('active');
    const target = btn.getAttribute('data-tab');
    document.getElementById(`tab-${target}`).classList.add('active');
  });
});

// Load Storeys dynamically from API
async function loadStoreys() {
    try {
        const response = await fetch('/filters/storeys');
        const data = await response.json();
        
        const storeySelect = document.getElementById('storeyFilter');
        if (!storeySelect) return;
        
        storeySelect.innerHTML = '<option value="">All Storeys</option>';
        
        if (data.storeys) {
            data.storeys.forEach(storey => {
                const opt = document.createElement('option');
                opt.value = storey;
                opt.textContent = storey;
                storeySelect.appendChild(opt);
            });
        }
    } catch (error) {
        console.error("Failed to load storeys:", error);
    }
}

// Global filter state
let currentStoreyFilter = "";
let currentTypesFilter = [];

// Results rendering with filters
async function loadResults() {
  const tbody = document.getElementById("results-body");
  if (!tbody) return;
  
  tbody.innerHTML = '<tr><td colspan="6" class="empty">Loading...</td></tr>';
  
  try {
    const params = new URLSearchParams();
    if (currentStoreyFilter) params.append("storey", currentStoreyFilter);
    if (currentTypesFilter.length > 0) params.append("types", currentTypesFilter.join(","));
    
    const res = await fetch(`/api/results?${params.toString()}`);
    if (!res.ok) throw new Error("Fetch failed");
    const data = await res.json();
    
    tbody.innerHTML = "";
    
    if (!data.results || data.results.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty">No issues found matching filters.</td></tr>';
      return;
    }
    
    data.results.forEach(r => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${r.type_a || ""}</td>
        <td>${r.name_a || ""}</td>
        <td>${r.type_b || ""}</td>
        <td>${r.name_b || ""}</td>
        <td><span class="badge ${r.issue === 'CLASH' ? 'badge-clash' : 'badge-clearance'}">${r.issue || ""}</span></td>
        <td>${r.metric ? parseFloat(r.metric).toFixed(4) : ""}</td>
      `;
      tbody.appendChild(tr);
    });
  } catch (err) {
    console.error(err);
    tbody.innerHTML = '<tr><td colspan="6" class="error">Failed to load results.</td></tr>';
  }
}

// Handle Apply Filters button
const applyFiltersBtn = document.getElementById('applyFiltersBtn');
if (applyFiltersBtn) {
    applyFiltersBtn.addEventListener('click', () => {
        const storeySelect = document.getElementById('storeyFilter');
        currentStoreyFilter = storeySelect ? storeySelect.value : "";
        
        const typeSelect = document.getElementById('typeFilter');
        if (typeSelect) {
            currentTypesFilter = Array.from(typeSelect.selectedOptions).map(opt => opt.value);
        } else {
            currentTypesFilter = [];
        }
        
        loadResults();
    });
}

const refreshBtn = document.getElementById("refresh-results-btn");
if (refreshBtn) {
    refreshBtn.addEventListener("click", loadResults);
}

// Chat logic
const chatForm = document.getElementById('chat-form');
const chatInput = document.getElementById('chat-input');
const chatMessages = document.getElementById('chat-messages');

if (chatForm) {
    chatForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const text = chatInput.value.trim();
      if (!text) return;
      
      // Add user message
      addMessage(text, 'user');
      chatInput.value = '';
      
      // Show loading
      const loadingId = addLoading();
      
      try {
        const res = await fetch('/api/ask', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ question: text })
        });
        
        const data = await res.json();
        removeMessage(loadingId);
        
        if (data.error) {
          addMessage(`Error: ${data.error}`, 'assistant error');
        } else {
          addMessage(data.answer, 'assistant', data.sources);
        }
      } catch (err) {
        removeMessage(loadingId);
        addMessage(`Failed to connect to backend: ${err.message}`, 'assistant error');
      }
    });
}

function addMessage(text, role, sources = []) {
  const div = document.createElement('div');
  div.className = `chat-msg ${role}`;
  
  let html = `<div class="msg-bubble">${text}</div>`;
  
  if (sources && sources.length > 0) {
    html += `<div class="msg-sources">`;
    sources.forEach(s => {
      let label = s.source;
      if (s.clause_id) label += ` [${s.clause_id}]`;
      if (s.element_id) label += ` (ID: ${s.element_id.substring(0,6)}...)`;
      html += `<span class="source-tag">${label}</span>`;
    });
    html += `</div>`;
  }
  
  div.innerHTML = html;
  chatMessages.appendChild(div);
  chatMessages.scrollTop = chatMessages.scrollHeight;
  return div.id;
}

function addLoading() {
  const id = 'loading-' + Date.now();
  const div = document.createElement('div');
  div.id = id;
  div.className = `chat-msg assistant`;
  div.innerHTML = `<div class="msg-bubble typing"><span class="dot"></span><span class="dot"></span><span class="dot"></span></div>`;
  chatMessages.appendChild(div);
  chatMessages.scrollTop = chatMessages.scrollHeight;
  return id;
}

function removeMessage(id) {
  const el = document.getElementById(id);
  if (el) el.remove();
}

// Example questions
document.querySelectorAll('.example-q').forEach(btn => {
  btn.addEventListener('click', () => {
    chatInput.value = btn.getAttribute('data-q');
    chatForm.dispatchEvent(new Event('submit'));
  });
});

// Run initializers
document.addEventListener("DOMContentLoaded", () => {
    loadStoreys();
    loadResults();
});