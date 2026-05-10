/* app.js — AI Test Case Generator v2 Frontend
   Supports: Python / JavaScript · Single / Multi-file / ZIP upload
*/

const API_BASE = '';

// ── Element refs ──────────────────────────────────────────────────────────────
const uploadSection   = document.getElementById('upload-section');
const loadingSection  = document.getElementById('loading-section');
const resultsSection  = document.getElementById('results-section');
const errorBanner     = document.getElementById('error-banner');
const errorText       = document.getElementById('error-text');

// Language toggle
const btnPython     = document.getElementById('btn-python');
const btnJavascript = document.getElementById('btn-javascript');
const uploadIcon    = document.getElementById('upload-icon');
const uploadTitle   = document.getElementById('upload-title');
const uploadDesc    = document.getElementById('upload-desc');
const extHint       = document.getElementById('ext-hint');
const uploadNote    = document.getElementById('upload-note');

// Upload mode
const tabSingle = document.getElementById('tab-single');
const tabMulti  = document.getElementById('tab-multi');

// File inputs
const fileInput      = document.getElementById('file-input');
const fileInputMulti = document.getElementById('file-input-multi');
const dropZone       = document.getElementById('drop-zone');
const dropInner      = document.getElementById('drop-inner');
const fileSelected   = document.getElementById('file-selected');
const fileListEl     = document.getElementById('file-list');
const clearFilesBtn  = document.getElementById('clear-files');
const browseBtn      = document.getElementById('browse-btn');
const generateBtn    = document.getElementById('generate-btn');

// Pipeline steps
const steps = {
  parse:    document.getElementById('step-parse'),
  generate: document.getElementById('step-generate'),
  run:      document.getElementById('step-run'),
  coverage: document.getElementById('step-coverage'),
  iterate:  document.getElementById('step-iterate'),
};
const loadingStatus = document.getElementById('loading-status');

// Results
const newFileBtn    = document.getElementById('new-file-btn');
const logToggle     = document.getElementById('log-toggle');
const logChevron    = document.getElementById('log-chevron');
const logBody       = document.getElementById('log-body');

// ── State ─────────────────────────────────────────────────────────────────────
let currentLang    = 'python';   // 'python' | 'javascript'
let currentMode    = 'single';   // 'single' | 'multi'
let selectedFiles  = [];         // File objects
let stepTimer      = null;

// ── Language Toggle ───────────────────────────────────────────────────────────
[btnPython, btnJavascript].forEach(btn => {
  btn.addEventListener('click', () => {
    currentLang = btn.dataset.lang;
    btnPython.classList.toggle('active', currentLang === 'python');
    btnJavascript.classList.toggle('active', currentLang === 'javascript');

    if (currentLang === 'python') {
      uploadIcon.textContent = '🐍';
      uploadTitle.textContent = 'Upload Python File(s)';
      extHint.textContent = '.py';
      fileInput.accept = '.py';
      fileInputMulti.accept = '.py';
    } else {
      uploadIcon.textContent = '🟨';
      uploadTitle.textContent = 'Upload JavaScript File(s)';
      extHint.textContent = '.js';
      fileInput.accept = '.js,.mjs';
      fileInputMulti.accept = '.js,.mjs';
    }
    clearFiles();
  });
});

// ── Upload Mode Tabs ──────────────────────────────────────────────────────────
[tabSingle, tabMulti].forEach(tab => {
  tab.addEventListener('click', () => {
    currentMode = tab.dataset.mode;
    [tabSingle, tabMulti].forEach(t => t.classList.toggle('active', t === tab));
    clearFiles();

    if (currentMode === 'multi') {
      uploadNote.textContent = 'Select up to 10 source files · Dependency order auto-detected';
    } else {
      uploadNote.textContent = 'Max 500 KB per file · Python and JavaScript supported';
    }
  });
});

// ── Browse Button ─────────────────────────────────────────────────────────────
browseBtn.addEventListener('click', (e) => {
  e.stopPropagation();
  if (currentMode === 'multi') fileInputMulti.click();
  else fileInput.click();
});

// ── File Input Change ─────────────────────────────────────────────────────────
fileInput.addEventListener('change', () => {
  if (fileInput.files.length) setFiles([...fileInput.files]);
});
fileInputMulti.addEventListener('change', () => {
  if (fileInputMulti.files.length) setFiles([...fileInputMulti.files]);
});

// ── Drag & Drop ───────────────────────────────────────────────────────────────
dropZone.addEventListener('click', () => {
  if (!selectedFiles.length) browseBtn.click();
});
dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
dropZone.addEventListener('drop', e => {
  e.preventDefault();
  dropZone.classList.remove('drag-over');
  const files = [...e.dataTransfer.files];
  if (files.length) setFiles(files);
});

// ── Set / Clear Files ─────────────────────────────────────────────────────────
function setFiles(files) {
  const ext = currentLang === 'python' ? ['.py'] : ['.js', '.mjs'];

  const valid = files.filter(f => {
    const suffix = '.' + f.name.split('.').pop().toLowerCase();
    return ext.includes(suffix);
  });

  if (!valid.length) {
    showError(`No valid ${ext.join('/')} files found in selection.`);
    return;
  }

  selectedFiles = valid.slice(0, 10);

  // Render file list
  fileListEl.innerHTML = '';
  selectedFiles.forEach(f => {
    const item = document.createElement('div');
    item.className = 'file-item';
    const icon = currentLang === 'python' ? '🐍' : '🟨';
    item.innerHTML = `
      <span class="file-item-icon">${icon}</span>
      <span class="file-item-name">${escHtml(f.name)}</span>
      <span class="file-item-size">${(f.size / 1024).toFixed(1)} KB</span>
    `;
    fileListEl.appendChild(item);
  });

  dropInner.hidden = true;
  fileSelected.hidden = false;
  generateBtn.disabled = false;
}

function clearFiles() {
  selectedFiles = [];
  fileInput.value = '';
  fileInputMulti.value = '';
  fileListEl.innerHTML = '';
  dropInner.hidden = false;
  fileSelected.hidden = true;
  generateBtn.disabled = true;
}

clearFilesBtn.addEventListener('click', e => { e.stopPropagation(); clearFiles(); });

// ── Generate ──────────────────────────────────────────────────────────────────
generateBtn.addEventListener('click', runPipeline);

async function runPipeline() {
  if (!selectedFiles.length) return;

  showLoading();
  startStepAnimation();

  try {
    const formData = new FormData();
    selectedFiles.forEach(f => formData.append('files', f));
    formData.append('language', currentLang);
    const resp = await fetch(`${API_BASE}/generate`, { method: 'POST', body: formData });

    const data = await resp.json();

    if (!resp.ok) {
      showError(data.detail || `Server error ${resp.status}`);
      showUpload();
      return;
    }

    completeAllSteps();
    renderResults(data);
    showResults();

  } catch (err) {
    showError(`Network error: ${err.message}`);
    showUpload();
  }
}

// ── Step Animation ────────────────────────────────────────────────────────────
function startStepAnimation() {
  const order  = ['parse', 'generate', 'run', 'coverage', 'iterate'];
  const labels = [
    'Parsing source files…',
    `Generating ${currentLang === 'javascript' ? 'Jest' : 'pytest'} tests…`,
    `Running tests with ${currentLang === 'javascript' ? 'Jest' : 'pytest'}…`,
    'Measuring code coverage…',
    'Iterating to improve results…',
  ];
  const delays = [2000, 9000, 5000, 5000, 10000];
  let idx = 0;

  function advance() {
    if (idx > 0) markDone(order[idx - 1]);
    if (idx < order.length) {
      markActive(order[idx]);
      loadingStatus.textContent = labels[idx];
      stepTimer = setTimeout(advance, delays[idx]);
      idx++;
    }
  }
  advance();
}

function markActive(k) { steps[k].classList.add('active'); steps[k].classList.remove('done'); }
function markDone(k)   { steps[k].classList.remove('active'); steps[k].classList.add('done'); }
function completeAllSteps() {
  clearTimeout(stepTimer);
  Object.keys(steps).forEach(k => markDone(k));
  loadingStatus.textContent = 'Pipeline complete!';
}

// ── Render Results ────────────────────────────────────────────────────────────
function renderResults(data) {
  const lang = data.language || currentLang;

  // Summary
  document.getElementById('res-filename').textContent = data.file_name || '—';
  document.getElementById('res-generated').textContent = data.tests_generated ?? '—';
  document.getElementById('res-passed').textContent   = data.tests_passed ?? '—';
  document.getElementById('res-failed').textContent   = data.tests_failed ?? '—';
  document.getElementById('res-iterations').textContent = data.iterations_taken ?? '—';
  document.getElementById('res-time').textContent =
    data.time_taken_seconds != null ? `${data.time_taken_seconds.toFixed(1)}s` : '—';

  // Badges
  const badgeRow = document.getElementById('res-badges');
  const allFiles = data.all_files || [data.file_name];
  badgeRow.innerHTML = `
    <span class="lang-badge ${lang}">${lang === 'python' ? '🐍 Python' : '🟨 JavaScript'}</span>
    ${allFiles.length > 1 ? `<span class="multi-badge">📂 ${allFiles.length} files</span>` : ''}
  `;

  // Multi-file list card
  const fileListCard = document.getElementById('file-list-card');
  if (allFiles.length > 1) {
    fileListCard.hidden = false;
    const listEl = document.getElementById('uploaded-file-list');
    listEl.innerHTML = allFiles.map((f, i) => `
      <div class="dep-file-item">
        <span class="dep-order">${i + 1}</span>
        <span>${escHtml(f)}</span>
      </div>
    `).join('');
  } else {
    fileListCard.hidden = true;
  }

  // ── Dependency Classification Card ───────────────────────────────────────────
  const depCard        = document.getElementById('dep-classification-card');
  const depBody        = document.getElementById('dep-classification-body');
  const standaloneFiles = data.standalone_files || [];
  const dependentFiles  = data.dependent_files  || [];

  if (allFiles.length > 1) {
    depCard.hidden = false;
    let depHtml = '';

    if (dependentFiles.length > 0) {
      depHtml += `
        <div class="dep-group">
          <div class="dep-group-label dep-linked">🔗 Dependent Files — tested with shared context</div>
          ${dependentFiles.map(f => `
            <div class="dep-file-item">
              <span class="dep-order dep-order-check">✓</span>
              <span>${escHtml(f)}</span>
            </div>
          `).join('')}
        </div>`;
    }

    if (standaloneFiles.length > 0) {
      depHtml += `
        <div class="dep-group">
          <div class="dep-group-label dep-standalone">⚠️ Standalone Files — no dependency on other uploaded files</div>
          ${standaloneFiles.map(f => `
            <div class="dep-file-item standalone-item">
              <span class="dep-order dep-order-arrow">→</span>
              <span>${escHtml(f)}</span>
              <span class="standalone-hint">tested independently</span>
            </div>
          `).join('')}
          <div class="standalone-note">
            💡 These files share no imports with the other uploaded files and were tested in isolation.
            For best results, upload standalone files separately using <strong>Single File</strong> mode.
          </div>
        </div>`;
    }

    depBody.innerHTML = depHtml || '<p style="color:var(--muted);font-size:0.85rem">All files processed.</p>';
  } else {
    depCard.hidden = true;
  }

  // Coverage bar
  const pct = data.final_coverage ?? 0;
  const bar  = document.getElementById('coverage-bar');
  const lbl  = document.getElementById('coverage-label');
  bar.style.width = `${Math.min(pct, 100)}%`;
  lbl.textContent = `${pct.toFixed(1)}%`;
  bar.className = 'coverage-bar ' + (pct >= 80 ? 'high' : pct >= 60 ? 'medium' : 'low');
  document.getElementById('coverage-note').textContent =
    pct >= 80 ? '✓ Coverage target met (≥ 80%)' : 'Coverage below 80% threshold';

  // Run command hint
  document.getElementById('run-cmd').textContent =
    lang === 'javascript' ? 'npx jest' : 'pytest';

  // Download link
  const dlLink = document.getElementById('download-link');
  if (data.download_url) {
    dlLink.href = data.download_url;
    dlLink.style.opacity = '';
    dlLink.style.pointerEvents = '';
    dlLink.textContent = `⬇ Download ${data.download_url.split('/').pop()}`;
  } else {
    dlLink.textContent = 'No test file available';
    dlLink.style.opacity = '0.4';
    dlLink.style.pointerEvents = 'none';
  }

  // Test results table
  const tbody = document.getElementById('results-tbody');
  tbody.innerHTML = '';
  const results = data.test_results || [];
  if (!results.length) {
    tbody.innerHTML = '<tr><td colspan="3" style="color:var(--muted);padding:1rem">No test results recorded.</td></tr>';
  } else {
    results.forEach(r => {
      const tr = document.createElement('tr');
      const cls = r.status === 'PASSED' ? 'badge-pass' : 'badge-fail';
      const err = r.error
        ? `<span class="error-cell">${escHtml(r.error)}</span>`
        : '<span style="color:var(--muted)">—</span>';
      tr.innerHTML = `
        <td style="font-family:monospace;font-size:0.82rem">${escHtml(r.name)}</td>
        <td><span class="badge ${cls}">${r.status}</span></td>
        <td>${err}</td>
      `;
      tbody.appendChild(tr);
    });
  }

  // Iteration log
  const logEl = document.getElementById('iteration-log');
  logEl.innerHTML = '';
  const log = data.iteration_log || [];
  if (!log.length) {
    logEl.innerHTML = '<p class="log-empty">No iterations needed — tests passed on first run.</p>';
  } else {
    log.forEach(entry => {
      const div = document.createElement('div');
      div.className = 'log-entry';
      div.innerHTML = `
        <div class="log-number">${entry.iteration}</div>
        <div class="log-content">
          <div class="log-event event-${entry.event}">${entry.event.replaceAll('_', ' ')}</div>
          <div class="log-detail">${escHtml(String(entry.detail || ''))}</div>
          <div class="log-action">→ ${escHtml(String(entry.action || ''))}</div>
        </div>
      `;
      logEl.appendChild(div);
    });
  }

  if (data.error) showError(data.error);
}

// ── Collapsible Log ───────────────────────────────────────────────────────────
logToggle.addEventListener('click', () => {
  logBody.classList.toggle('collapsed');
  logChevron.classList.toggle('open');
});

// ── Reset ─────────────────────────────────────────────────────────────────────
newFileBtn.addEventListener('click', () => {
  clearFiles();
  Object.keys(steps).forEach(k => steps[k].classList.remove('active', 'done'));
  showUpload();
});

// ── Visibility ────────────────────────────────────────────────────────────────
function showUpload()  { uploadSection.hidden = false; loadingSection.hidden = true;  resultsSection.hidden = true;  }
function showLoading() { uploadSection.hidden = true;  loadingSection.hidden = false; resultsSection.hidden = true;  hideError(); }
function showResults() { uploadSection.hidden = true;  loadingSection.hidden = true;  resultsSection.hidden = false; }

// ── Error ─────────────────────────────────────────────────────────────────────
function showError(msg) {
  errorText.textContent = msg;
  errorBanner.classList.add('visible');
  setTimeout(() => { errorBanner.classList.remove('visible'); }, 9000);
}
function hideError() { errorBanner.classList.remove('visible'); }

// ── Utility ───────────────────────────────────────────────────────────────────
function escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
