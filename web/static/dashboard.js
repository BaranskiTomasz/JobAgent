let ALL_JOBS = [];
let _jobsTruncated = false;
let currentStatus = 'new';
let agentSocket = null;

let _renderedCount = 0;
const BATCH_SIZE = 25;
let _lazyJobs = [];

let _selectMode = false;
let _selected = new Set();

let _badgeFilters = new Set();

let _availableSources = [];
let _sourcesMap = {};

let _breakdownCache = {};
let _dismissedLoaded = new Set();

function esc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// For interpolating into a single-quoted JS string literal inside an onclick="..." attribute:
// escape backslashes/quotes for JS first, then HTML-escape the result for the attribute.
function escJs(s) {
  return esc(String(s ?? '').replace(/\\/g, '\\\\').replace(/'/g, "\\'"));
}

function safeUrl(u) {
  return (typeof u === 'string' && u.startsWith('http')) ? u : '#';
}

function formatDate(d) {
  if (!d) return '';
  return new Date(d).toLocaleDateString('en-GB', { day: '2-digit', month: 'short' });
}

function showToast(msg, kind) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.toggle('error', kind === 'error');
  t.classList.add('show');
  // Errors get more time on screen than a routine confirmation — this is the
  // one place a failed pipeline run (silent otherwise, see _updateAgentIndicator)
  // actually surfaces to someone who isn't staring at the Run Agent modal.
  setTimeout(() => t.classList.remove('show'), kind === 'error' ? 6000 : 2500);
}

// ── Theme ──────────────────────────────────────────────────────────────────────

function toggleTheme() {
  const root = document.documentElement;
  const current = root.getAttribute('data-theme') ||
    (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  const next = current === 'dark' ? 'light' : 'dark';
  root.setAttribute('data-theme', next);
  localStorage.setItem('jobagent-theme', next);
}

(function initTheme() {
  const saved = localStorage.getItem('jobagent-theme');
  if (saved) document.documentElement.setAttribute('data-theme', saved);
})();

// ── Structured data helpers ────────────────────────────────────────────────────

function _parseStructured(j) {
  if (!j.structured_data) return null;
  try { return typeof j.structured_data === 'string' ? JSON.parse(j.structured_data) : j.structured_data; }
  catch { return null; }
}

function _parseBreakdown(j) {
  if (!j.score_breakdown) return null;
  try { return typeof j.score_breakdown === 'string' ? JSON.parse(j.score_breakdown) : j.score_breakdown; }
  catch { return null; }
}

function _workType(s) {
  if (!s) return null;
  if (s.hybrid) return 'hybrid';
  if (s.remote) return 'remote';
  if (s.remote === false && s.hybrid === false) return 'onsite';
  return null;
}

// Remote job locations are free text like "Warszawa, Poland (Remote)" or bare
// "Poland (Remote)" — grouping by the raw string would split the same country
// across every city that ever posted a remote role there. Derive an actual
// country label so "Warszawa, Poland (Remote)" and "Kraków, Poland (Remote)"
// both collapse to "Poland" and match a "Poland" filter.
const _US_STATE_CODES = new Set(['AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA','KS','KY','LA','ME','MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ','NM','NY','NC','ND','OH','OK','OR','PA','RI','SC','SD','TN','TX','UT','VT','VA','WA','WV','WI','WY','DC']);
const _CA_PROVINCE_CODES = new Set(['AB','BC','MB','NB','NL','NS','NT','NU','ON','PE','QC','SK','YT']);

// Only labels that are actually a country (or a well-known multi-country recruiting
// region) are allowed through — otherwise metro/region names with no comma-separated
// country ("Cracow Metropolitan Area", "Greater Sankt Polten") would be mistaken for one.
const _KNOWN_COUNTRIES = new Set([
  'Afghanistan','Albania','Algeria','Andorra','Angola','Argentina','Armenia','Australia','Austria','Azerbaijan',
  'Bahamas','Bahrain','Bangladesh','Barbados','Belarus','Belgium','Belize','Benin','Bhutan','Bolivia',
  'Bosnia and Herzegovina','Botswana','Brazil','Brunei','Bulgaria','Burkina Faso','Burundi',
  'Cambodia','Cameroon','Canada','Cape Verde','Central African Republic','Chad','Chile','China','Colombia','Comoros',
  'Costa Rica','Croatia','Cuba','Cyprus','Czech Republic','Czechia',
  'Democratic Republic of the Congo','Denmark','Djibouti','Dominica','Dominican Republic',
  'Ecuador','Egypt','El Salvador','Equatorial Guinea','Eritrea','Estonia','Eswatini','Ethiopia',
  'Fiji','Finland','France',
  'Gabon','Gambia','Georgia','Germany','Ghana','Greece','Grenada','Guatemala','Guinea','Guinea-Bissau','Guyana',
  'Haiti','Honduras','Hungary',
  'Iceland','India','Indonesia','Iran','Iraq','Ireland','Israel','Italy','Ivory Coast',
  'Jamaica','Japan','Jordan',
  'Kazakhstan','Kenya','Kiribati','Kosovo','Kuwait','Kyrgyzstan',
  'Laos','Latvia','Lebanon','Lesotho','Liberia','Libya','Liechtenstein','Lithuania','Luxembourg',
  'Madagascar','Malawi','Malaysia','Maldives','Mali','Malta','Mauritania','Mauritius','Mexico','Micronesia','Moldova','Monaco','Mongolia','Montenegro','Morocco','Mozambique','Myanmar',
  'Namibia','Nauru','Nepal','Netherlands','New Zealand','Nicaragua','Niger','Nigeria','North Korea','North Macedonia','Norway',
  'Oman',
  'Pakistan','Palau','Palestine','Panama','Papua New Guinea','Paraguay','Peru','Philippines','Poland','Portugal',
  'Qatar',
  'Republic of the Congo','Romania','Russia','Rwanda',
  'Saint Kitts and Nevis','Saint Lucia','Saint Vincent and the Grenadines','Samoa','San Marino','Sao Tome and Principe','Saudi Arabia','Senegal','Serbia','Seychelles','Sierra Leone','Singapore','Slovakia','Slovenia','Solomon Islands','Somalia','South Africa','South Korea','South Sudan','Spain','Sri Lanka','Sudan','Suriname','Sweden','Switzerland','Syria',
  'Taiwan','Tajikistan','Tanzania','Thailand','Timor-Leste','Togo','Tonga','Trinidad and Tobago','Tunisia','Turkey','Turkmenistan','Tuvalu',
  'Uganda','Ukraine','United Arab Emirates','United Kingdom','United States','Uruguay','Uzbekistan',
  'Vanuatu','Vatican City','Venezuela','Vietnam',
  'Yemen',
  'Zambia','Zimbabwe',
]);
const _KNOWN_REGIONS = new Set(['EMEA','NAMER','LATAM','APAC','DACH','European Union','European Economic Area','Benelux','Nordics','Scandinavia','Middle East']);

function _countryOf(location) {
  if (!location) return null;
  const s = String(location).replace(/\s*\(\s*remote\s*\)\s*$/i, '').replace(/\s*\/\s*remote\s*$/i, '').trim();
  if (!s) return null;
  const parts = s.split(',').map(p => p.trim()).filter(Boolean);
  if (!parts.length) return null;
  let candidate = parts[parts.length - 1];
  const code = candidate.toUpperCase();
  if (_US_STATE_CODES.has(code)) candidate = 'United States';
  else if (_CA_PROVINCE_CODES.has(code)) candidate = 'Canada';
  return (_KNOWN_COUNTRIES.has(candidate) || _KNOWN_REGIONS.has(candidate)) ? candidate : null;
}

// Whether a job is remote for Cities-vs-Countries grouping purposes is decided from the
// location text itself ("... (Remote)" / ".../Remote"), not from structured_data.remote —
// many jobs have no structured_data yet (extraction hasn't run or failed on them), and
// those would otherwise silently fall back into "Cities" even though the location text
// plainly says Remote.
function _isRemoteLocationText(location) {
  return /\(\s*remote\s*\)\s*$/i.test(location || '') || /\/\s*remote\s*$/i.test(location || '');
}

const _WORK_LABEL = { remote: 'Remote', hybrid: 'Hybrid', onsite: 'On-site' };
const _SENIORITY_VALUES = ['junior', 'mid', 'senior', 'lead', 'director'];
const _CTYPE_VALUES = ['startup', 'scaleup', 'enterprise', 'agency'];
const _PVO_VALUES = ['product', 'outsourcing', 'mixed'];
const _SUBSCORE_LABELS = { stack_fit: 'Stack', seniority_fit: 'Seniority', company_fit: 'Company', compensation_fit: 'Salary' };

function cap(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }

// ── Stats / summary band ───────────────────────────────────────────────────────

async function loadStats() {
  let r, s;
  try {
    r = await fetch('/api/stats');
    s = await r.json();
  } catch {
    showToast('Failed to load stats — is the server running?');
    return;
  }
  document.getElementById('f-new').textContent      = s.new;
  document.getElementById('f-reviewed').textContent = s.reviewed;
  document.getElementById('f-applied').textContent  = s.applied;
  document.getElementById('f-rejected').textContent = s.rejected;
  document.getElementById('f-auto').textContent     = s.auto_rejected;

  document.getElementById('t-new').textContent      = s.new;
  document.getElementById('t-reviewed').textContent = s.reviewed;
  document.getElementById('t-applied').textContent  = s.applied;
  document.getElementById('t-rejected').textContent = s.rejected;
  document.getElementById('t-auto').textContent      = s.auto_rejected;
  document.getElementById('t-all').textContent      = s.total;

  document.getElementById('s-avg-new').textContent = s.avg_score_new != null ? s.avg_score_new.toFixed(1) : '—';

  const u = s.usage || {};
  document.getElementById('cost-per100').textContent = u.cost_per_100_usd != null ? `$${u.cost_per_100_usd.toFixed(2)}` : '—';
  document.getElementById('cost-today').textContent  = u.today_cost_usd != null ? `$${u.today_cost_usd.toFixed(3)}` : '—';
  document.getElementById('cost-total').textContent  = u.total_cost_usd != null ? `$${u.total_cost_usd.toFixed(2)}` : '—';

  const lastRun = s.last_run
    ? new Date(s.last_run.replace(' ', 'T') + 'Z').toLocaleString('pl-PL', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
    : 'never';
  document.getElementById('last-run').textContent = 'last run ' + lastRun;

  const banner = document.getElementById('cv-banner');
  if (banner) banner.style.display = (!s.has_cv && s.total > 0) ? '' : 'none';

  checkMissingDescriptions();
  loadCalibSummary();
}

async function loadCalibSummary() {
  let d;
  try {
    const r = await fetch('/api/eval/report');
    d = await r.json();
  } catch {
    return;
  }
  document.getElementById('calib-p5').textContent  = d.precision_at_5  != null ? Math.round(d.precision_at_5 * 100) + '%' : '—';
  document.getElementById('calib-p10').textContent = d.precision_at_10 != null ? Math.round(d.precision_at_10 * 100) + '%' : '—';
  document.getElementById('calib-div-count').textContent = (d.divergence_cases || []).length;
}

async function checkMissingDescriptions() {
  const r = await fetch('/api/jobs/missing-descriptions');
  const d = await r.json();
  document.getElementById('backfill-count').textContent = d.count;
  const badge = document.getElementById('op-backfill-n');
  if (badge) { badge.style.display = d.count > 0 ? '' : 'none'; badge.textContent = d.count; }
}

// ── Preference profile / "what the agent learned" ─────────────────────────────

function _humanDim(dim) { return String(dim || '').replace(/_/g, ' '); }
function _humanValue(v) { return v ? String(v).replace(/_/g, ' ') : ''; }

const _SIGNAL_TAG_LABEL = { ACCEPT: 'Likes', REJECT: 'Avoids', INFER: 'Inferred', NEUTRAL: 'No signal' };
const _SIGNAL_TAG_CLASS = { ACCEPT: 'acc', REJECT: 'rej', INFER: 'inf', NEUTRAL: 'neu' };

function _signalMainText(s) {
  if (s.note) return s.note;
  const val = _humanValue(s.value);
  const dim = _humanDim(s.dim);
  if (s.type === 'NEUTRAL') return `No clear pattern yet on ${dim}.`;
  if (s.type === 'INFER') return `Likely prefers ${dim}${val ? ': ' + val : ''}, based on limited evidence.`;
  return `${s.type === 'ACCEPT' ? 'Favors' : 'Avoids'} ${dim}${val ? ': ' + val : ''}.`;
}

function _signalMetaText(s) {
  const parts = [_humanDim(s.dim) + (s.value ? ': ' + _humanValue(s.value) : '')];
  if (s.conf) parts.push(s.conf.toLowerCase() + ' confidence');
  if (s.n_match != null && s.n_total != null) parts.push(`${s.n_match}/${s.n_total} examples`);
  else if (s.n_total != null) parts.push(`from ${s.n_total} examples`);
  return parts.join(' · ');
}

function _renderSignalChips(signals) {
  const notable = (signals || []).filter(s => s.type === 'ACCEPT' || s.type === 'REJECT' || s.type === 'INFER').slice(0, 6);
  if (!notable.length) return '<span class="lc-empty">No strong signals yet — apply/reject a few more jobs, then refresh.</span>';
  return notable.map(s => {
    const cls = _SIGNAL_TAG_CLASS[s.type] || 'inf';
    return `<div class="sig"><span class="tag ${cls}">${_SIGNAL_TAG_LABEL[s.type] || s.type}</span><span class="txt">${esc(_signalMainText(s))}</span></div>`;
  }).join('');
}

function _renderSignalList(signals) {
  if (!signals || !signals.length) return '';
  return `<div class="pref-list">${signals.map(s => {
    const cls = _SIGNAL_TAG_CLASS[s.type] || 'inf';
    const neutralCls = s.type === 'NEUTRAL' ? ' neutral' : '';
    return `
      <div class="pref-row${neutralCls}">
        <span class="pref-tag ${cls}">${_SIGNAL_TAG_LABEL[s.type] || s.type}</span>
        <div class="pref-body">
          <div class="pref-main">${esc(_signalMainText(s))}</div>
          <div class="pref-meta">${esc(_signalMetaText(s))}</div>
        </div>
      </div>`;
  }).join('')}</div>`;
}

async function _loadLearnedCard() {
  const el = document.getElementById('lc-signals');
  try {
    const r = await fetch('/api/preferences');
    const d = await r.json();
    if (!d.profile) { el.innerHTML = '<span class="lc-empty">No profile yet — click refresh to distill one from your feedback history.</span>'; return; }
    el.innerHTML = _renderSignalChips(d.profile.signals || []);
    if (!d.profile.signals) el.innerHTML = '<span class="lc-empty">' + esc(d.profile.content.slice(0, 200)) + '…</span>';
  } catch {
    el.innerHTML = '<span class="lc-empty">Error loading profile.</span>';
  }
}

function openLearnedModal() {
  document.getElementById('learned-modal').classList.add('open');
  _loadLearnedFull();
}
function closeLearnedModal() { document.getElementById('learned-modal').classList.remove('open'); }

async function _loadLearnedFull() {
  const meta = document.getElementById('learned-meta');
  const full = document.getElementById('learned-full');
  try {
    const r = await fetch('/api/preferences');
    const d = await r.json();
    if (!d.profile) { meta.textContent = ''; full.textContent = 'No profile yet. Click "Refresh" to distill from your feedback history.'; return; }
    const p = d.profile;
    const updated = new Date(p.updated_at.replace(' ', 'T') + 'Z').toLocaleString('pl-PL', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
    meta.innerHTML = `<span>Applied: ${p.applied_count}</span><span>Rejected: ${p.rejected_count}</span><span>Updated: ${updated}</span>`;
    full.innerHTML = (p.signals && p.signals.length) ? _renderSignalList(p.signals) : esc(p.content);
  } catch {
    full.textContent = 'Error loading profile.';
  }
}

async function distillPreferences() {
  const btns = [document.getElementById('lc-refresh'), document.getElementById('btn-learned-refresh')].filter(Boolean);
  btns.forEach(b => { b.disabled = true; });
  document.getElementById('lc-signals').innerHTML = '<span class="lc-empty">Analyzing feedback history with Claude…</span>';
  try {
    const r = await fetch('/api/preferences/distill', { method: 'POST' });
    const d = await r.json();
    if (!r.ok || !d.ok) {
      showToast(d.reason || 'Distillation failed');
      return;
    }
    showToast('Preference profile updated');
    await _loadLearnedCard();
    if (document.getElementById('learned-modal').classList.contains('open')) await _loadLearnedFull();
  } catch (e) {
    showToast('Error: ' + e.message);
  } finally {
    btns.forEach(b => { b.disabled = false; });
  }
}

// ── Status filter (funnel + tabs share one source of truth) ───────────────────

function setStatusFilter(status) {
  currentStatus = status;
  document.querySelectorAll('.tab[data-status]').forEach(t => t.classList.toggle('active', t.dataset.status === status));
  document.querySelectorAll('.funnel .step[data-status]').forEach(s => s.classList.toggle('active', s.dataset.status === status));
  loadJobs();
}

document.querySelectorAll('.tab[data-status]').forEach(tab => {
  tab.addEventListener('click', () => setStatusFilter(tab.dataset.status));
});
document.querySelectorAll('.funnel .step[data-status]').forEach(step => {
  step.addEventListener('click', () => setStatusFilter(step.dataset.status));
});

// ── Sources / jobs loading ─────────────────────────────────────────────────────

async function _loadSources() {
  const r = await fetch('/api/sources');
  _availableSources = await r.json();
  _sourcesMap = Object.fromEntries(_availableSources.map(s => [s.id, s.name]));

  const sel = document.getElementById('source-filter');
  while (sel.options.length > 1) sel.remove(1);
  _availableSources.forEach(s => sel.add(new Option(s.name, s.id)));
}

async function loadJobs() {
  const search = document.getElementById('search').value;
  const source = document.getElementById('source-filter').value;
  const params = new URLSearchParams({ status: currentStatus });
  if (search) params.set('search', search);
  if (source) params.set('source', source);

  try {
    const r = await fetch('/api/jobs?' + params);
    ALL_JOBS = await r.json();
    _jobsTruncated = r.headers.get('X-Jobs-Truncated') === 'true';
  } catch {
    showToast('Failed to load jobs — is the server running?');
    return;
  }
  render();
}

// ── Card rendering ──────────────────────────────────────────────────────────────

function _renderBadges(j, s) {
  const on = f => _badgeFilters.has(f) ? ' on' : '';
  const tags = [];

  const work = _workType(s);
  if (work) tags.push(`<span class="b key${on('work=' + work)}" onclick="toggleBadgeFilter('work=${work}')"><span class="dotmark"></span>${_WORK_LABEL[work]}</span>`);

  if (s && s.seniority && s.seniority !== 'unknown')
    tags.push(`<span class="b${on('seniority=' + s.seniority)}" onclick="toggleBadgeFilter('${escJs('seniority=' + s.seniority)}')">${esc(cap(s.seniority))}</span>`);
  if (s && s.company_type && s.company_type !== 'unknown')
    tags.push(`<span class="b${on('ctype=' + s.company_type)}" onclick="toggleBadgeFilter('${escJs('ctype=' + s.company_type)}')">${esc(cap(s.company_type))}</span>`);
  if (s && s.product_vs_outsourcing && s.product_vs_outsourcing !== 'unknown')
    tags.push(`<span class="b${on('pvo=' + s.product_vs_outsourcing)}" onclick="toggleBadgeFilter('${escJs('pvo=' + s.product_vs_outsourcing)}')">${esc(cap(s.product_vs_outsourcing))}</span>`);
  (s && s.stack || []).slice(0, 6).forEach(t => {
    const key = 'stack=' + t.toLowerCase();
    tags.push(`<span class="b stack${on(key)}" onclick="toggleBadgeFilter('${escJs(key)}')">${esc(t)}</span>`);
  });

  return tags.length ? `<div class="badges">${tags.join('')}</div>` : '';
}

function _renderSecondOpinion(j) {
  if (!j.debate_note) return '';
  const flag = j.debate_flag || '';
  const cls = flag === 'dealbreaker_risk' ? 'dealbreaker' : (flag === 'underrated' ? 'underrated' : '');
  return `
    <div class="second ${cls}">
      <span class="glyph"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg></span>
      <div class="so-body">
        <div class="so-label">Second opinion <span class="so-flag">${esc(flag.replace(/_/g, ' '))}</span>
          <span class="info"><button type="button" class="info-btn">?</button>
            <span class="tip">A second AI model reviews the primary ranking's top picks looking for mismatches the first pass might have missed. <b>dealbreaker risk</b> demotes the job to the bottom of the shortlist; <b>overrated</b>/<b>underrated</b> are just a note — they don't change its rank.</span>
          </span>
        </div>
        <div class="so-note">${esc(j.debate_note)}</div>
      </div>
    </div>`;
}

function _scoreItemLi(jobId, type, idx, text) {
  const icon = type === 'pro'
    ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6L9 17l-5-5"/></svg>'
    : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18M6 6l12 12"/></svg>';
  const key = `${jobId}:${type}:${idx}`;
  return `
    <li class="score-item" id="si-${esc(key)}">
      ${icon}<span class="txt">${esc(text)}</span>
      <button type="button" class="dismiss-x" onclick="toggleDismissPop('${escJs(jobId)}','${type}',${idx})" title="Not relevant to me">×</button>
      <div class="dismiss-pop" id="dp-${esc(key)}">
        <input type="text" id="dp-input-${esc(key)}" placeholder="Why doesn't this matter to you?"
               onkeydown="if(event.key==='Enter') confirmDismiss('${escJs(jobId)}','${type}',${idx}); if(event.key==='Escape') toggleDismissPop('${escJs(jobId)}','${type}',${idx})">
        <button type="button" class="go" onclick="confirmDismiss('${escJs(jobId)}','${type}',${idx})">Save</button>
        <button type="button" class="cancel" onclick="toggleDismissPop('${escJs(jobId)}','${type}',${idx})">Cancel</button>
      </div>
    </li>`;
}

function _renderBreakdownSection(jobId, b) {
  if (!b || (!(b.pros || []).length && !(b.cons || []).length && !Object.keys(b.sub_scores || {}).length)) return '';
  _breakdownCache[jobId] = b;
  // Fresh DOM for this job means any earlier "already loaded" dismissed-state fetch
  // no longer applies to what's on screen now — force a re-fetch next time it's opened.
  _dismissedLoaded.delete(jobId);
  const subs = Object.entries(b.sub_scores || {}).map(([key, val]) => `
    <div class="ss">
      <span class="lab">${esc(_SUBSCORE_LABELS[key] || key)}</span>
      <div class="track"><div class="fill" style="width:${Math.max(0, Math.min(100, (val / 10) * 100))}%"></div></div>
      <span class="val">${esc(String(val))}</span>
    </div>`).join('');
  const pros = (b.pros || []).map((p, i) => _scoreItemLi(jobId, 'pro', i, p)).join('');
  const cons = (b.cons || []).map((c, i) => _scoreItemLi(jobId, 'con', i, c)).join('');

  return `
    <button type="button" class="disc-toggle" id="disc-bd-${esc(jobId)}" onclick="toggleBreakdown('${esc(jobId)}')">
      <svg class="di" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 3v18M4 8l5-5 5 5"/></svg>
      Why this score <svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 9l6 6 6-6"/></svg>
    </button>
    <div class="breakdown" id="bd-${esc(jobId)}">
      <div class="bd-grid">
        <div>
          <div class="bd-h">Sub-scores</div>
          <div class="subscores">${subs || '<span class="pc-empty">No sub-scores recorded.</span>'}</div>
        </div>
        <div class="proscons">
          <div>
            <div class="bd-h">Pros</div>
            <ul class="pc-list pros">${pros || '<span class="pc-empty">None noted.</span>'}</ul>
          </div>
          <div>
            <div class="bd-h">Cons</div>
            <ul class="pc-list cons">${cons || '<span class="pc-empty">None noted.</span>'}</ul>
          </div>
        </div>
      </div>
    </div>`;
}

function _renderCard(j) {
  const s = _parseStructured(j);
  const on = f => _badgeFilters.has(f) ? ' on' : '';
  const score = j.score != null ? j.score.toFixed(1) : '—';
  const meterPct = j.score != null ? Math.max(0, Math.min(100, (j.score / 10) * 100)) : 0;
  const rankBadge = j.listwise_rank != null ? `<div class="rank-badge">#${j.listwise_rank}</div>` : '';
  const wouldApplyBadge = j.would_apply === 1
    ? `<div class="would-apply-badge" title="${esc(j.would_apply_reason || '')}">
         <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6L9 17l-5-5"/></svg>
         Would apply
       </div>`
    : '';
  const srcName = _sourcesMap[j.source] || j.source || '';
  const work = _workType(s);
  const locFilterValue = _isRemoteLocationText(j.location) ? (_countryOf(j.location) || j.location || '') : (j.location || '');
  const locFilterKey = 'loc=' + locFilterValue;
  const locLabel = j.location || '—';
  const companyAppliedBadge = j.company_applied_count > 0
    ? `<span class="co-applied-badge" title="You've applied to ${j.company_applied_count} job(s) from this company before">
         <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6L9 17l-5-5"/></svg>
         Applied before
       </span>`
    : '';
  const companyCountBadge = j.company_total_count > 1
    ? `<span class="co-count-badge" title="${j.company_total_count} listings from this company in your pool">${j.company_total_count}×</span>`
    : '';

  const reason = j.rank_reason || j.score_reason || '';
  let rejNote = null;
  if (j.status === 'rejected' && j.rejection_reason) rejNote = j.rejection_reason;
  else if (j.status === 'auto_rejected' && j.score_reason) rejNote = j.score_reason;

  const breakdown = _renderBreakdownSection(j.id, _parseBreakdown(j));
  const descToggle = j.description
    ? `<button type="button" class="disc-toggle" id="disc-desc-${esc(j.id)}" onclick="toggleDesc('${esc(j.id)}')">
         <svg class="di" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 6h16M4 12h16M4 18h10"/></svg>
         Description <svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 9l6 6 6-6"/></svg>
       </button>
       <div class="desc-body" id="desc-${esc(j.id)}">${esc(j.description)}</div>`
    : '';

  return `
  <div class="job st-${esc(j.status)}${j.debate_flag === 'dealbreaker_risk' ? ' demoted' : ''}" id="card-${esc(j.id)}">
    <div class="stripe"></div>
    <div class="job-body">
      <div class="job-top">
        <div class="job-sel"><input type="checkbox" ${_selected.has(j.id) ? 'checked' : ''} onchange="toggleSelect('${esc(j.id)}', this.checked)"></div>
        <div class="job-head">
          <a class="job-title" href="${safeUrl(j.url)}" target="_blank" rel="noopener">${esc(j.title)}</a>
          <div class="job-meta">
            <button type="button" class="co${on('firm=' + j.company)}" onclick="toggleBadgeFilter('firm=${escJs(j.company || '')}')">${esc(j.company)}</button>
            ${companyCountBadge}
            ${companyAppliedBadge}
            <button type="button" class="loc${on(locFilterKey)}" onclick="toggleBadgeFilter('${escJs(locFilterKey)}')">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 10c0 6-9 12-9 12s-9-6-9-12a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>
              ${esc(locLabel)}
            </button>
            ${srcName ? `<button type="button" class="src-badge${on('source=' + j.source)}" onclick="toggleBadgeFilter('source=${escJs(j.source)}')">${esc(srcName)}</button>` : ''}
          </div>
        </div>
        <div class="score-block">
          ${wouldApplyBadge}
          ${rankBadge}
          <div class="score-meter">
            <div class="score-num">${score}<span class="of">/10</span></div>
            <div class="meter-track"><div class="meter-fill" style="width:${meterPct}%"></div></div>
          </div>
        </div>
      </div>
      ${_renderBadges(j, s)}
      ${reason ? `<div class="verdict">${esc(reason)}</div>` : ''}
      ${_renderSecondOpinion(j)}
      ${rejNote ? `<div class="rejected-note"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M15 9l-6 6M9 9l6 6"/></svg>${esc(rejNote)}</div>` : ''}
      ${(breakdown || descToggle) ? `<div class="disclosures">${breakdown}${descToggle}</div>` : ''}
      <div class="job-foot">
        <span class="status-tag ${esc(j.status)}">${esc(j.status.replace('_', ' '))}</span>
        <span class="job-date">${formatDate(j.created_at)}</span>
        <div class="act-group">
          <button type="button" class="act ${j.status === 'reviewed' ? 'on-reviewed' : ''}" onclick="setStatus('${esc(j.id)}', 'reviewed')">Reviewed</button>
          <button type="button" class="act ${j.status === 'applied' ? 'on-applied' : ''}" onclick="setStatus('${esc(j.id)}', 'applied')">Applied ✓</button>
          <button type="button" class="act reject" onclick="toggleRejectPop('${esc(j.id)}')">Reject ✕</button>
        </div>
      </div>
      <div class="reject-pop" id="reject-pop-${esc(j.id)}">
        <input type="text" id="reject-input-${esc(j.id)}" placeholder="Reason, e.g. rate too low, too junior…" value="${esc(j.rejection_reason || '')}"
               onkeydown="if(event.key==='Enter') confirmReject('${esc(j.id)}'); if(event.key==='Escape') toggleRejectPop('${esc(j.id)}')">
        <button type="button" class="go" onclick="confirmReject('${esc(j.id)}')">Reject</button>
        <button type="button" class="cancel" onclick="toggleRejectPop('${esc(j.id)}')">Cancel</button>
      </div>
    </div>
  </div>`;
}

// ── Lazy-loading render ─────────────────────────────────────────────────────────

function render() {
  const sort = document.getElementById('sort').value;
  _lazyJobs = [...ALL_JOBS];
  if (sort === 'rank') {
    _lazyJobs.sort((a, b) => {
      const aRanked = a.listwise_rank != null;
      const bRanked = b.listwise_rank != null;
      if (aRanked && bRanked) return a.listwise_rank - b.listwise_rank;
      if (aRanked !== bRanked) return aRanked ? -1 : 1;
      return (b.score ?? -1) - (a.score ?? -1);
    });
  }
  if (sort === 'score')   _lazyJobs.sort((a, b) => (b.score ?? -1) - (a.score ?? -1));
  if (sort === 'date')    _lazyJobs.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
  if (sort === 'company') _lazyJobs.sort((a, b) => (a.company || '').localeCompare(b.company || ''));

  if (_badgeFilters.size) _lazyJobs = _lazyJobs.filter(_matchesBadgeFilters);
  _updateFilterBar();
  _updateFilterBadges();

  const banner = document.getElementById('truncated-banner');
  if (_jobsTruncated) {
    banner.style.display = '';
    banner.textContent = `Showing the most recent ${ALL_JOBS.length.toLocaleString()} jobs for this view — narrow with a search, status, or source filter to see the rest.`;
  } else {
    banner.style.display = 'none';
  }

  document.getElementById('count').textContent = `${_lazyJobs.length} job${_lazyJobs.length !== 1 ? 's' : ''}`;
  _renderedCount = 0;

  const container = document.getElementById('jobs-container');
  container.classList.toggle('selecting', _selectMode);
  container.innerHTML = '';

  if (!_lazyJobs.length) {
    const search = document.getElementById('search').value.trim();
    const source = document.getElementById('source-filter').value;
    const hasActiveFilter = search || source || _badgeFilters.size;
    if (!ALL_JOBS.length && !hasActiveFilter && currentStatus === 'new') {
      container.innerHTML = '<div class="no-results">No jobs yet — click <strong>Run agent</strong> above to collect your first batch.</div>';
    } else if (hasActiveFilter) {
      container.innerHTML = '<div class="no-results">No jobs match your filters.</div>';
    } else {
      container.innerHTML = '<div class="no-results">No jobs found.</div>';
    }
    return;
  }

  _appendBatch(container, BATCH_SIZE);
  _renderPager();
}

function _appendBatch(container, count) {
  const batch = _lazyJobs.slice(_renderedCount, _renderedCount + count);
  _renderedCount += batch.length;

  const frag = document.createDocumentFragment();
  batch.forEach(j => {
    const tmp = document.createElement('div');
    tmp.innerHTML = _renderCard(j).trim();
    frag.appendChild(tmp.firstChild);
  });
  container.appendChild(frag);
}

function _pageNumbers(current, total) {
  const pages = new Set([1, total, current, current - 1, current + 1, current - 2, current + 2]);
  return [...pages].filter(p => p >= 1 && p <= total).sort((a, b) => a - b);
}

function _renderPager() {
  const container = document.getElementById('jobs-container');
  const old = document.getElementById('pager-wrap');
  if (old) old.remove();

  const total = _lazyJobs.length;
  const totalPages = Math.ceil(total / BATCH_SIZE);
  const currentPage = Math.ceil(_renderedCount / BATCH_SIZE);
  if (totalPages <= 1 && _renderedCount >= total) return;

  const wrap = document.createElement('div');
  wrap.id = 'pager-wrap';
  wrap.className = 'load-more-bar';

  let html = '';
  if (_renderedCount < total) {
    const remaining = total - _renderedCount;
    html += `<button type="button" class="load-more-btn" onclick="_loadMore()">Load more (${Math.min(BATCH_SIZE, remaining)} of ${remaining} left)</button>`;
  }

  if (totalPages > 1) {
    const nums = _pageNumbers(currentPage, totalPages);
    let pagerHtml = `<button type="button" class="nav" onclick="_goToPage(1)" ${currentPage <= 1 ? 'disabled' : ''}>&laquo;</button>`;
    let prev = 0;
    for (const p of nums) {
      if (p - prev > 1) pagerHtml += `<span class="ellipsis">…</span>`;
      pagerHtml += `<button type="button" class="${p === currentPage ? 'active' : ''}" onclick="_goToPage(${p})">${p}</button>`;
      prev = p;
    }
    pagerHtml += `<button type="button" class="nav" onclick="_goToPage(${totalPages})" ${currentPage >= totalPages ? 'disabled' : ''}>&raquo;</button>`;
    html += `<div class="pager">${pagerHtml}</div>`;
  }

  wrap.innerHTML = html;
  container.parentElement.appendChild(wrap);
}

function _loadMore() {
  const container = document.getElementById('jobs-container');
  _appendBatch(container, BATCH_SIZE);
  _renderPager();
}

function _goToPage(n) {
  const container = document.getElementById('jobs-container');
  const targetCount = Math.min(n * BATCH_SIZE, _lazyJobs.length);
  if (targetCount > _renderedCount) _appendBatch(container, targetCount - _renderedCount);
  _renderPager();
  const idx = (n - 1) * BATCH_SIZE;
  const target = container.children[idx];
  if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ── Disclosure toggles ─────────────────────────────────────────────────────────

async function toggleBreakdown(jobId) {
  const opening = !document.getElementById(`bd-${jobId}`).classList.contains('open');
  document.getElementById(`bd-${jobId}`).classList.toggle('open');
  document.getElementById(`disc-bd-${jobId}`).classList.toggle('open');
  if (opening && !_dismissedLoaded.has(jobId)) {
    _dismissedLoaded.add(jobId);
    try {
      const r = await fetch(`/api/jobs/${jobId}/dismissed-items`);
      const d = await r.json();
      const b = _breakdownCache[jobId] || {};
      (d.items || []).forEach(it => {
        const idx = (b[it.item_type + 's'] || []).indexOf(it.item_text);
        if (idx !== -1) _markItemDismissed(jobId, it.item_type, idx, it.reason);
      });
    } catch {}
  }
}

function toggleDesc(jobId) {
  document.getElementById(`desc-${jobId}`).classList.toggle('open');
  document.getElementById(`disc-desc-${jobId}`).classList.toggle('open');
}

// ── Dismiss a pro/con as not relevant ─────────────────────────────────────────

function toggleDismissPop(jobId, type, idx) {
  const key = `${jobId}:${type}:${idx}`;
  document.querySelectorAll('.dismiss-pop.open').forEach(el => { if (el.id !== `dp-${key}`) el.classList.remove('open'); });
  const pop = document.getElementById(`dp-${key}`);
  if (!pop) return;
  pop.classList.toggle('open');
  if (pop.classList.contains('open')) {
    const input = document.getElementById(`dp-input-${key}`);
    input && input.focus();
  }
}

async function confirmDismiss(jobId, type, idx) {
  const key = `${jobId}:${type}:${idx}`;
  const input = document.getElementById(`dp-input-${key}`);
  const reason = input ? input.value.trim() : '';
  if (!reason) { input && input.focus(); return; }

  const b = _breakdownCache[jobId];
  const itemText = b && (b[type + 's'] || [])[idx];
  if (!itemText) return;

  const r = await fetch(`/api/jobs/${jobId}/dismiss-item`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ item_type: type, item_text: itemText, reason }),
  });
  if (!r.ok) { showToast('Failed to save — please try again'); return; }
  _markItemDismissed(jobId, type, idx, reason);
  showToast('Noted — this will shape future scoring, not this job.');
}

function _markItemDismissed(jobId, type, idx, reason) {
  const key = `${jobId}:${type}:${idx}`;
  const li = document.getElementById(`si-${key}`);
  if (!li || li.classList.contains('dismissed')) return;
  li.classList.add('dismissed');
  const pop = document.getElementById(`dp-${key}`);
  if (pop) pop.remove();
  const btn = li.querySelector('.dismiss-x');
  if (btn) btn.remove();
  const note = document.createElement('span');
  note.className = 'dismissed-note';
  note.textContent = `Not relevant: ${reason}`;
  li.appendChild(note);
}

// ── Reject popover ──────────────────────────────────────────────────────────────

function toggleRejectPop(jobId) {
  document.querySelectorAll('.reject-pop.open').forEach(el => { if (el.id !== `reject-pop-${jobId}`) el.classList.remove('open'); });
  const pop = document.getElementById(`reject-pop-${jobId}`);
  pop.classList.toggle('open');
  if (pop.classList.contains('open')) {
    const input = document.getElementById(`reject-input-${jobId}`);
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  }
}

async function confirmReject(jobId) {
  const input = document.getElementById(`reject-input-${jobId}`);
  const reason = input ? input.value.trim() : '';
  await setStatus(jobId, 'rejected', reason);
}

// ── Status update ───────────────────────────────────────────────────────────────

async function setStatus(jobId, status, rejectionReason = null) {
  const body = { status };
  if (status === 'rejected' && rejectionReason !== null) body.rejection_reason = rejectionReason;
  const r = await fetch(`/api/jobs/${jobId}/status`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    showToast('Error saving status — please try again');
    return;
  }

  const job = ALL_JOBS.find(j => j.id === jobId);
  if (job) {
    job.status = status;
    if (status === 'rejected' && rejectionReason !== null) job.rejection_reason = rejectionReason;
  }

  const belongs = currentStatus === 'all' || status === currentStatus;

  if (!belongs) {
    const ai = ALL_JOBS.findIndex(j => j.id === jobId);
    if (ai !== -1) ALL_JOBS.splice(ai, 1);
    const li = _lazyJobs.findIndex(j => j.id === jobId);
    if (li !== -1) _lazyJobs.splice(li, 1);

    const card = document.getElementById(`card-${jobId}`);
    if (card) {
      card.style.transition = 'opacity 0.15s, transform 0.15s';
      card.style.opacity = '0';
      card.style.transform = 'translateX(12px)';
      setTimeout(() => { card.remove(); }, 160);
    }
    document.getElementById('count').textContent = `${_lazyJobs.length} job${_lazyJobs.length !== 1 ? 's' : ''}`;
  } else {
    const card = document.getElementById(`card-${jobId}`);
    if (card && job) {
      const tmp = document.createElement('div');
      tmp.innerHTML = _renderCard(job).trim();
      card.replaceWith(tmp.firstChild);
    }
  }

  loadStats();
  const labels = { reviewed: 'Marked as reviewed', applied: 'Applied ✓', rejected: 'Rejected' };
  showToast(labels[status] || 'Updated');
}

// ── Exact-score filter panel ────────────────────────────────────────────────────

function _scoreKey(score) {
  return score != null ? `score=${score.toFixed(1)}` : 'score=none';
}

function _buildScoreFilterPanel() {
  const panel = document.getElementById('scoref-panel');
  if (!panel) return;
  const counts = new Map();
  for (const j of ALL_JOBS) {
    const key = _scoreKey(j.score);
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  const keys = [...counts.keys()].sort((a, b) => {
    if (a === 'score=none') return 1;
    if (b === 'score=none') return -1;
    return parseFloat(b.slice(6)) - parseFloat(a.slice(6));
  });
  if (!keys.length) {
    panel.innerHTML = '<div class="scoref-hint">No jobs loaded.</div>';
    return;
  }
  const pills = keys.map(key => {
    const isNone = key === 'score=none';
    const label = isNone ? 'n/a' : parseFloat(key.slice(6)).toFixed(1);
    const active = _badgeFilters.has(key) ? ' on' : '';
    return `<span class="spill${active}" onclick="toggleBadgeFilter('${key}')">${label}<span class="n">${counts.get(key)}</span></span>`;
  }).join('');
  panel.innerHTML =
    `<div class="scoref-title">Filter by exact score</div>
     <p class="scoref-hint">Pick one or more values — jobs matching any of them are shown.</p>
     <div class="scoref-grid">${pills}</div>
     <button type="button" class="scoref-clear" onclick="_clearScoreFilters()">Clear score filter</button>`;
}

function _clearScoreFilters() {
  for (const f of [..._badgeFilters]) if (f.startsWith('score=')) _badgeFilters.delete(f);
  _buildScoreFilterPanel();
  render();
}

function toggleScoreFilterPanel(e) {
  e.stopPropagation();
  const panel = document.getElementById('scoref-panel');
  const opening = !panel.classList.contains('open');
  panel.classList.toggle('open', opening);
  if (opening) _buildScoreFilterPanel();
}

document.addEventListener('click', (e) => {
  const wrap = document.querySelector('.scoref');
  const panel = document.getElementById('scoref-panel');
  if (wrap && panel && !wrap.contains(e.target)) panel.classList.remove('open');
});

document.addEventListener('click', (e) => {
  document.querySelectorAll('details.nav-menu[open]').forEach(menu => {
    if (!menu.contains(e.target)) menu.removeAttribute('open');
  });
});
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  document.querySelectorAll('details.nav-menu[open]').forEach(menu => menu.removeAttribute('open'));
});

// ── Badge / dimension filters ───────────────────────────────────────────────────

function _matchesBadgeFilters(j) {
  const active = [..._badgeFilters];
  const scoreFilters = active.filter(f => f.startsWith('score='));
  const otherFilters = active.filter(f => !f.startsWith('score='));

  if (scoreFilters.length && !scoreFilters.includes(_scoreKey(j.score))) return false;
  if (!otherFilters.length) return true;

  const s = _parseStructured(j);
  const stackLower = (s && s.stack || []).map(t => t.toLowerCase());
  const work = _workType(s);
  const locValue = _isRemoteLocationText(j.location) ? (_countryOf(j.location) || j.location || '') : (j.location || '');

  for (const f of otherFilters) {
    if (f.startsWith('work=')      && work !== f.slice(5)) return false;
    if (f.startsWith('seniority=') && (!s || s.seniority !== f.slice(10))) return false;
    if (f.startsWith('ctype=')     && (!s || s.company_type !== f.slice(7))) return false;
    if (f.startsWith('pvo=')       && (!s || s.product_vs_outsourcing !== f.slice(4))) return false;
    if (f.startsWith('stack=')     && !stackLower.includes(f.slice(6))) return false;
    if (f.startsWith('source=')    && j.source !== f.slice(7)) return false;
    if (f.startsWith('firm=')      && (j.company || '') !== f.slice(5)) return false;
    if (f.startsWith('loc=')       && locValue !== f.slice(4)) return false;
  }
  return true;
}

function toggleBadgeFilter(f) {
  _badgeFilters.has(f) ? _badgeFilters.delete(f) : _badgeFilters.add(f);
  if (f.startsWith('score=')) _buildScoreFilterPanel();
  render();
}

function removeBadgeFilter(f) {
  _badgeFilters.delete(f);
  if (f.startsWith('score=')) _buildScoreFilterPanel();
  render();
}

function clearBadgeFilters() {
  _badgeFilters.clear();
  _buildScoreFilterPanel();
  render();
  if (document.getElementById('filters-modal').classList.contains('open')) _buildFiltersModal();
}

function _filterLabel(f) {
  if (f.startsWith('score=')) return f.slice(6) === 'none' ? 'score: not scored' : `score: ${f.slice(6)}`;
  if (f.startsWith('work='))      return f.slice(5);
  if (f.startsWith('seniority=')) return f.slice(10);
  if (f.startsWith('ctype='))     return f.slice(7);
  if (f.startsWith('pvo='))       return f.slice(4);
  if (f.startsWith('stack='))     return f.slice(6);
  if (f.startsWith('source='))    return _sourcesMap[f.slice(7)] || f.slice(7);
  if (f.startsWith('firm='))      return f.slice(5);
  if (f.startsWith('loc='))       return f.slice(4);
  return f;
}

function _updateFilterBar() {
  const bar = document.getElementById('filter-bar');
  const chips = document.getElementById('filter-chips');
  if (!bar || !chips) return;
  bar.classList.toggle('on', _badgeFilters.size > 0);
  chips.innerHTML = [..._badgeFilters].map(f =>
    `<span class="fchip">${esc(_filterLabel(f))}<button onclick="removeBadgeFilter('${escJs(f)}')" title="Remove">×</button></span>`
  ).join('');
}

function _updateFilterBadges() {
  const scoreN = [..._badgeFilters].filter(f => f.startsWith('score=')).length;
  const otherN = _badgeFilters.size - scoreN;
  const scoreBadge = document.getElementById('scoref-badge');
  const moreBadge  = document.getElementById('more-filters-badge');
  if (scoreBadge) { scoreBadge.textContent = scoreN; scoreBadge.classList.toggle('on', scoreN > 0); }
  if (moreBadge)  { moreBadge.textContent  = otherN; moreBadge.classList.toggle('on', otherN > 0); }
  document.getElementById('scoref-btn').classList.toggle('on', scoreN > 0);
  document.getElementById('more-filters-btn').classList.toggle('on', otherN > 0);
}

// ── All-filters modal ────────────────────────────────────────────────────────────

function _fchip(key, label, mono = false) {
  const on = _badgeFilters.has(key) ? ' on' : '';
  return `<button type="button" class="fchip-toggle${mono ? ' mono' : ''}${on}" onclick="toggleBadgeFilter('${escJs(key)}'); _buildFiltersModal()">${esc(label)}</button>`;
}

function _buildFiltersModal() {
  const body = document.getElementById('filters-body');
  if (!body) return;

  const cities = new Set();
  const countries = new Set();
  ALL_JOBS.forEach(j => {
    if (!j.location) return;
    if (_isRemoteLocationText(j.location)) {
      const country = _countryOf(j.location);
      if (country) countries.add(country);
    } else {
      cities.add(j.location);
    }
  });

  body.innerHTML = `
    <div class="fgroup">
      <div class="fgroup-h">Work type</div>
      <div class="fmodal-grid">${['remote', 'hybrid', 'onsite'].map(w => _fchip('work=' + w, _WORK_LABEL[w])).join('')}</div>
    </div>
    <div class="fgroup">
      <div class="fgroup-h">Seniority</div>
      <div class="fmodal-grid">${_SENIORITY_VALUES.map(v => _fchip('seniority=' + v, cap(v))).join('')}</div>
    </div>
    <div class="fgroup">
      <div class="fgroup-h">Company type</div>
      <div class="fmodal-grid">${_CTYPE_VALUES.map(v => _fchip('ctype=' + v, cap(v))).join('')}</div>
    </div>
    <div class="fgroup">
      <div class="fgroup-h">Product vs. outsourcing</div>
      <div class="fmodal-grid">${_PVO_VALUES.map(v => _fchip('pvo=' + v, cap(v))).join('')}</div>
    </div>
    <div class="fgroup">
      <div class="fgroup-h">Source</div>
      <div class="fmodal-grid">${_availableSources.map(src => _fchip('source=' + src.id, src.name)).join('')}</div>
    </div>
    <div class="fgroup">
      <div class="fgroup-h">Cities</div>
      <p class="fgroup-note">Only applies to hybrid and on-site postings.</p>
      <div class="fmodal-grid">${[...cities].sort().map(c => _fchip('loc=' + c, c)).join('') || '<span class="fgroup-note">None yet.</span>'}</div>
    </div>
    <div class="fgroup">
      <div class="fgroup-h">Countries</div>
      <p class="fgroup-note">Only applies to remote postings.</p>
      <div class="fmodal-grid">${[...countries].sort().map(c => _fchip('loc=' + c, c)).join('') || '<span class="fgroup-note">None yet.</span>'}</div>
    </div>
  `;
}

function openFiltersModal() {
  document.getElementById('filters-modal').classList.add('open');
  _buildFiltersModal();
}
function closeFiltersModal() { document.getElementById('filters-modal').classList.remove('open'); }

// ── Select mode & bulk actions ──────────────────────────────────────────────────

function toggleSelectMode() {
  _selectMode = !_selectMode;
  if (!_selectMode) _selected.clear();
  const btn = document.getElementById('select-toggle');
  if (btn) { btn.textContent = _selectMode ? 'Cancel' : 'Select'; btn.classList.toggle('on', _selectMode); }
  const bar = document.getElementById('bulk-bar');
  if (bar) bar.classList.toggle('on', _selectMode);
  render();
}

function toggleSelect(jobId, checked) {
  checked ? _selected.add(jobId) : _selected.delete(jobId);
  _updateBulkBar();
}

function selectAll() {
  _lazyJobs.forEach(j => _selected.add(j.id));
  document.querySelectorAll('#jobs-container .job-sel input').forEach(cb => { cb.checked = true; });
  _updateBulkBar();
}

function deselectAll() {
  _selected.clear();
  document.querySelectorAll('#jobs-container .job-sel input').forEach(cb => { cb.checked = false; });
  _updateBulkBar();
}

function _updateBulkBar() {
  const n = _selected.size;
  const el = document.getElementById('bulk-count');
  if (el) el.textContent = `${n} selected`;
  document.querySelectorAll('.bulk-act').forEach(btn => { btn.disabled = n === 0; });
}

async function bulkSetStatus(status, reason = '') {
  const ids = [..._selected];
  if (!ids.length) return;
  await Promise.all(ids.map(id =>
    fetch(`/api/jobs/${id}/status`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status, ...(reason ? { rejection_reason: reason } : {}) }),
    })
  ));
  ids.forEach(id => {
    const job = ALL_JOBS.find(j => j.id === id);
    if (job) { job.status = status; if (reason) job.rejection_reason = reason; }
  });
  _selected.clear();
  _updateBulkBar();
  render();
  loadStats();
}

function openBulkRejectModal() {
  if (!_selected.size) return;
  const msg = document.getElementById('bulk-reject-msg');
  if (msg) msg.textContent = `Rejecting ${_selected.size} job${_selected.size > 1 ? 's' : ''}.`;
  const input = document.getElementById('bulk-reject-reason');
  if (input) input.value = '';
  document.getElementById('bulk-reject-modal').classList.add('open');
  setTimeout(() => input && input.focus(), 50);
}
function closeBulkRejectModal() { document.getElementById('bulk-reject-modal').classList.remove('open'); }
function confirmBulkReject() {
  const reason = (document.getElementById('bulk-reject-reason')?.value || '').trim();
  closeBulkRejectModal();
  bulkSetStatus('rejected', reason);
}

// ── Actions modal ────────────────────────────────────────────────────────────────

function openActionsModal() { document.getElementById('actions-modal').classList.add('open'); }
function closeActionsModal() { document.getElementById('actions-modal').classList.remove('open'); }

// ── Eval / Calibration modal ────────────────────────────────────────────────────

async function openEvalModal() {
  document.getElementById('eval-modal').classList.add('open');
  document.getElementById('eval-body').innerHTML = '<p>Loading…</p>';

  let data;
  try {
    const r = await fetch('/api/eval/report');
    data = await r.json();
  } catch {
    document.getElementById('eval-body').innerHTML = '<p>Failed to load report.</p>';
    return;
  }

  const cases = data.divergence_cases || [];
  const rows = cases.map(c => `
    <tr>
      <td><span class="kind ${c.divergence_type === 'false_positive' ? 'fp' : 'fn'}"><span class="dt"></span>${c.divergence_type === 'false_positive' ? 'Overrated' : 'Underrated'}</span></td>
      <td>${esc(c.title)}</td>
      <td class="co-cell">${esc(c.company)}</td>
      <td class="rk">${c.listwise_rank != null ? `#${c.listwise_rank}` : '—'}</td>
    </tr>`).join('');

  const wa = data.would_apply || {};
  const waPrecisionPct = wa.precision != null ? Math.round(wa.precision * 100) : null;
  const waMeetsGate = waPrecisionPct != null && waPrecisionPct >= 90;

  document.getElementById('eval-body').innerHTML = `
    <div class="eval-metrics">
      <div class="eval-metric"><div class="ev">${data.precision_at_5 != null ? Math.round(data.precision_at_5 * 100) + '%' : '—'}</div><div class="el">PRECISION@5</div></div>
      <div class="eval-metric"><div class="ev">${data.precision_at_10 != null ? Math.round(data.precision_at_10 * 100) + '%' : '—'}</div><div class="el">PRECISION@10</div></div>
      <div class="eval-metric"><div class="ev">${data.total_ranked ?? '—'}</div><div class="el">RANKED</div></div>
      <div class="eval-metric"><div class="ev">${cases.length}</div><div class="el">DIVERGENCES</div></div>
    </div>
    <p class="calib-explain"><b>Precision@K</b> looks at your top-K AI-ranked jobs that you've actually <i>decided</i> on (applied, rejected, or auto-rejected) — not the top-K overall, since only a decided job can confirm whether the ranking was right. "Reviewed" doesn't count as a decision here: it means you looked but haven't committed either way, so it's left out of the count and the score entirely.
    <br><br><b>Example:</b> your 10 highest-ranked jobs include 8 you've decided on — 6 applied, 2 rejected — and 2 still sitting as new/reviewed. Precision@10 = 6/8 = <b>75%</b>. The 2 undecided ones simply aren't counted yet; they'll factor in once you act on them.
    <br><br>A <b>divergence case</b> flags a specific miss: a job ranked in the top 5 that you rejected (the model overrated it), or ranked #16+ that you applied to anyway (the model underrated it). These are fed back into future scoring as calibration examples, so the model doesn't repeat the same mistake.</p>

    <div class="wa-gate ${waMeetsGate ? 'met' : ''}">
      <div class="wa-gate-hd">
        <span class="wa-gate-title">Would-apply gate</span>
        <span class="wa-gate-pct">${waPrecisionPct != null ? waPrecisionPct + '%' : '—'} <span class="wa-gate-target">/ 90% target</span></span>
      </div>
      <div class="wa-gate-bar"><div class="wa-gate-fill" style="width:${waPrecisionPct != null ? Math.min(100, waPrecisionPct) : 0}%"></div></div>
      <div class="wa-gate-detail">${wa.flagged_total ?? 0} flagged &middot; ${wa.decided ?? 0} decided (${wa.applied ?? 0} applied, ${wa.rejected ?? 0} rejected) &middot; ${(wa.flagged_total ?? 0) - (wa.decided ?? 0)} still open</div>
      <p class="calib-explain">Of the jobs the agent flagged as <b>"would apply"</b> (score ≥ ${data.would_apply_score_floor ?? 7.0}, no dealbreaker risk), what fraction did you actually apply to once you decided? This is flag-and-validate only — nothing is ever sent automatically. Note this number likely runs a bit optimistic versus true autonomous accuracy: seeing the badge can itself nudge your decision, since it's no longer fully independent of the flag.</p>
    </div>

    <div class="eval-tbl-h">Divergence cases <span class="cnt">(${cases.length})</span></div>
    <div class="div-scroll">
      <table class="div-tbl">
        <thead><tr><th>Type</th><th>Title</th><th>Company</th><th>Rank</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="4" class="div-empty">No divergence cases yet — keep using applied/rejected!</td></tr>'}</tbody>
      </table>
    </div>`;
}
function closeEvalModal() { document.getElementById('eval-modal').classList.remove('open'); }

// ── Excluded search queries modal ───────────────────────────────────────────────

async function openExcludedQueriesModal() {
  document.getElementById('excluded-queries-modal').classList.add('open');
  await _loadExcludedQueries();
}
function closeExcludedQueriesModal() { document.getElementById('excluded-queries-modal').classList.remove('open'); }

async function _loadExcludedQueries() {
  const body = document.getElementById('excluded-queries-body');
  body.innerHTML = '<p>Loading…</p>';

  let rows;
  try {
    const r = await fetch('/api/search-queries/excluded');
    rows = await r.json();
  } catch {
    body.innerHTML = '<p>Failed to load.</p>';
    return;
  }

  if (!rows.length) {
    body.innerHTML = '<p class="calib-explain">No search queries have been auto-excluded yet.</p>';
    return;
  }

  const trs = rows.map(row => `
    <tr>
      <td>${esc(row.source)}</td>
      <td>${esc(row.search_query)}</td>
      <td>${esc(row.reason)}</td>
      <td><button class="btn-ghost" onclick="reinstateExcludedQuery(${row.id})">Re-enable</button></td>
    </tr>`).join('');

  body.innerHTML = `
    <p class="calib-explain">Dropped from future LinkedIn runs — collection there is slow, so queries that reliably waste time get pruned automatically. Re-enabling puts a query back into the next run.</p>
    <div class="div-scroll">
      <table class="div-tbl">
        <thead><tr><th>Source</th><th>Query</th><th>Reason</th><th></th></tr></thead>
        <tbody>${trs}</tbody>
      </table>
    </div>`;
}

async function reinstateExcludedQuery(id) {
  try {
    await fetch(`/api/search-queries/excluded/${id}/reinstate`, { method: 'POST' });
  } catch {
    showToast('Failed to re-enable query.');
    return;
  }
  await _loadExcludedQueries();
}

// ── Delete jobs modal ────────────────────────────────────────────────────────────

function openDeleteModal() {
  document.getElementById('delete-step-1').style.display = '';
  document.getElementById('delete-step-2').style.display = 'none';
  document.getElementById('delete-modal').classList.add('open');
  document.querySelectorAll('.del-status').forEach(c => c.onchange = updateDeleteCount);
  updateDeleteCount();
}
function closeDeleteModal() { document.getElementById('delete-modal').classList.remove('open'); }

function _deleteParams() {
  const statuses = [...document.querySelectorAll('.del-status:checked')].map(c => c.value);
  const from = document.getElementById('del-date-from').value;
  const to = document.getElementById('del-date-to').value;
  const p = new URLSearchParams();
  statuses.forEach(s => p.append('status', s));
  if (from) p.set('date_from', from);
  if (to) p.set('date_to', to);
  return { params: p, statuses, from, to };
}

async function updateDeleteCount() {
  const { params, statuses } = _deleteParams();
  if (!statuses.length) {
    document.getElementById('del-count').textContent = '0';
    document.getElementById('btn-delete-go').disabled = true;
    return;
  }
  document.getElementById('del-count').textContent = '…';
  const r = await fetch('/api/jobs/count?' + params);
  const d = await r.json();
  document.getElementById('del-count').textContent = d.count;
  document.getElementById('btn-delete-go').disabled = d.count === 0;
}

function deleteStep2() {
  const { statuses, from, to } = _deleteParams();
  const count = document.getElementById('del-count').textContent;
  const statusLabel = statuses.map(s => s.replace('_', '-')).join(', ');
  let msg = `Are you sure you want to delete <strong>${count} jobs</strong> with status: <strong>${statusLabel}</strong>`;
  if (from || to) msg += `, added ${from || '…'} — ${to || '…'}`;
  msg += '?';
  const trainingStatuses = ['applied', 'rejected'];
  if (statuses.some(s => trainingStatuses.includes(s))) {
    msg += '<br><br>⚠️ Applied/rejected jobs are used as training data for scoring. Deleting them will degrade future evaluation quality.';
  }
  document.getElementById('delete-confirm-msg').innerHTML = msg;
  document.getElementById('delete-step-1').style.display = 'none';
  document.getElementById('delete-step-2').style.display = '';
}

function deleteBack() {
  document.getElementById('delete-step-1').style.display = '';
  document.getElementById('delete-step-2').style.display = 'none';
}

async function executeDelete() {
  const { params } = _deleteParams();
  const r = await fetch('/api/jobs?' + params, { method: 'DELETE' });
  const d = await r.json();
  closeDeleteModal();
  loadStats();
  loadJobs();
  showToast(`Deleted ${d.deleted} job(s)`);
}

// ── Backfill modal ───────────────────────────────────────────────────────────────

function openBackfillModal() {
  document.getElementById('backfill-log').textContent = '';
  document.getElementById('backfill-progress').style.display = 'none';
  document.getElementById('progress-bar').style.width = '0%';
  document.getElementById('progress-label').textContent = '0 / 0';
  document.getElementById('backfill-modal').classList.add('open');
}
function closeBackfillModal() { document.getElementById('backfill-modal').classList.remove('open'); }

function startBackfill() {
  const log = document.getElementById('backfill-log');
  const btn = document.getElementById('btn-backfill-start');
  const prog = document.getElementById('backfill-progress');
  const bar = document.getElementById('progress-bar');
  const lbl = document.getElementById('progress-label');

  log.textContent = '';
  prog.style.display = '';
  btn.disabled = true;
  btn.textContent = 'Running…';

  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/ws/backfill`);

  ws.onmessage = e => {
    const line = e.data;
    const m = line.match(/^PROGRESS:(\d+)\/(\d+)/);
    if (m) {
      const cur = parseInt(m[1]), tot = parseInt(m[2]);
      const pct = tot > 0 ? Math.round(cur / tot * 100) : 0;
      bar.style.width = pct + '%';
      lbl.textContent = `${cur} / ${tot}  (${pct}%)`;
      return;
    }
    if (line.includes('__DONE__')) {
      btn.disabled = false;
      btn.textContent = 'Start';
      bar.style.width = '100%';
      checkMissingDescriptions();
      loadJobs();
      return;
    }
    log.textContent += line;
    log.scrollTop = log.scrollHeight;
  };

  ws.onerror = () => {
    log.textContent += '\nWebSocket error.\n';
    btn.disabled = false;
    btn.textContent = 'Start';
  };
}

// ── Generic activity runner (rank / rescore / reevaluate) via run-modal ────────

let _stopping = false;

function _runGeneric(wsPath, title) {
  document.getElementById('run-modal-title').textContent = title;
  document.getElementById('run-modal-params').style.display = 'none';
  document.getElementById('btn-start').style.display = 'none';
  document.getElementById('btn-generic-stop').style.display = '';
  document.getElementById('run-progress').style.display = 'none';
  document.getElementById('run-log').textContent = '';
  document.getElementById('run-modal').classList.add('open');

  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}${wsPath}`);
  const log = document.getElementById('run-log');
  let done = false;

  ws.onmessage = e => {
    if (e.data.includes('__DONE__')) {
      done = true;
      ws.close();
      document.getElementById('btn-generic-stop').style.display = 'none';
      loadStats();
      loadJobs();
      return;
    }
    log.textContent += e.data;
    log.scrollTop = log.scrollHeight;
  };
  ws.onerror = () => {
    if (done) return;
    log.textContent += '\nWebSocket error.\n';
    document.getElementById('btn-generic-stop').style.display = 'none';
  };
}

async function reevaluateRejected() {
  let count = 0, costPer100 = null;
  try {
    const r = await fetch('/api/stats');
    const s = await r.json();
    count = s.auto_rejected || 0;
    costPer100 = s.usage && s.usage.cost_per_100_usd;
  } catch {}

  if (count === 0) { showToast('No auto-rejected jobs to re-evaluate.'); return; }

  const estCost = costPer100 != null ? ` (est. $${(count / 100 * costPer100).toFixed(2)})` : '';
  const msg = document.getElementById('reevaluate-rejected-msg');
  if (msg) msg.textContent = `Re-evaluate ${count} auto-rejected job(s)? This re-runs the keyword filter and AI scoring on each one${estCost}.`;
  document.getElementById('reevaluate-rejected-modal').classList.add('open');
}
function closeReevaluateRejectedModal() { document.getElementById('reevaluate-rejected-modal').classList.remove('open'); }
function confirmReevaluateRejected() {
  closeReevaluateRejectedModal();
  _runGeneric('/ws/reevaluate-rejected', 'Re-evaluating auto-rejected jobs');
}
function rescoreNew()         { _runGeneric('/ws/rescore-new', 'Re-scoring new jobs'); }
function rankJobs()           { _runGeneric('/ws/rank', 'Ranking jobs (AI)'); }

function stopAgent() {
  _stopping = true;
  fetch('/api/agent/stop', { method: 'POST' }).catch(() => {});
}

// ── Run modal (main collector + full pipeline run) ──────────────────────────────

function _onSinceLastToggle() {
  const checked = document.getElementById('run-since-last').checked;
  document.getElementById('run-days').disabled = checked;
  document.getElementById('run-days-label').classList.toggle('disabled', checked);
  _onDaysInput();
}

// A large --days window means more postings clear the cutoff, which means more
// LinkedIn descriptions to stealth-fetch — the actual dominant cost, and one
// the "typically 1-3 hours" hint alone doesn't convey scales with this field.
function _onDaysInput() {
  const warn = document.getElementById('run-days-warn');
  if (document.getElementById('run-since-last').checked) { warn.style.display = 'none'; return; }
  const days = parseInt(document.getElementById('run-days').value) || 1;
  if (days > 3) {
    warn.textContent = `Searching ${days} days back means more to process than usual — this could run considerably longer than the typical 1-3 hours.`;
    warn.style.display = '';
  } else {
    warn.style.display = 'none';
  }
}

function _formatElapsed(ms) {
  const totalSec = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m ${s}s`;
}

let _logPollTimer = null;
let _runStartedAtMs = null;

function _clearLogPollTimer() {
  if (_logPollTimer) { clearInterval(_logPollTimer); _logPollTimer = null; }
}

function _renderLogAndProgress(lines, stage) {
  const log = document.getElementById('run-log');
  const text = (lines || []).join('\n');
  log.textContent = text;
  log.scrollTop = log.scrollHeight;

  if (stage) {
    // Authoritative — reported by the server itself (see _stage_progress in
    // runner.py), not derived by scanning log text: /api/agent/logs only
    // returns the last 200 lines, which can lose an early stage's "===" header
    // once a later stage has printed enough of its own output.
    const pct = Math.round(stage.index / stage.total * 100);
    document.getElementById('run-progress-bar').style.width = pct + '%';
    document.getElementById('run-progress-label').textContent =
      `Stage ${stage.index} / ${stage.total}: ${stage.label}`;
  }
  _updateElapsedLabel();
}

function _updateElapsedLabel() {
  const el = document.getElementById('run-progress-elapsed');
  if (el) el.textContent = _runStartedAtMs ? 'Elapsed: ' + _formatElapsed(Date.now() - _runStartedAtMs) : '';
}

// Shared by startAgent()'s live __DONE__ handler and _attachToActiveRun()'s
// poll-detected completion — a failed run (lastStatus 'error' or
// 'done_with_errors') used to render identically to a clean one: full bar,
// "Stage 6/6", no visual difference at all.
function _applyFinalStageVisual(lastStatus) {
  const bar = document.getElementById('run-progress-bar');
  const lbl = document.getElementById('run-progress-label');
  const failed = lastStatus && lastStatus !== 'done';
  bar.style.width = '100%';
  bar.classList.toggle('error', failed);
  lbl.textContent = failed
    ? `Stage ${_AGENT_STAGE_COUNT} / ${_AGENT_STAGE_COUNT} — finished with errors, see log above`
    : `Stage ${_AGENT_STAGE_COUNT} / ${_AGENT_STAGE_COUNT}`;
}

// Polls the log file directly (written continuously server-side regardless of
// any websocket's state) instead of a live push — the one live /ws/agent
// stream belongs to whichever tab called startAgent(); a tab that reopens
// this modal after closing it (or a fresh page load) has no socket to
// reattach to, so this is the mechanism that makes "closing the modal loses
// the log forever" not true anymore.
async function _pollActiveRunOnce() {
  try {
    const [logsResp, statusResp] = await Promise.all([fetch('/api/agent/logs'), fetch('/api/agent/status')]);
    const { lines } = await logsResp.json();
    const { running, stage, last_status } = await statusResp.json();
    _renderLogAndProgress(lines, stage);
    return { running, lastStatus: last_status };
  } catch (e) {
    return { running: true, lastStatus: null }; // a transient fetch blip shouldn't tear down the view
  }
}

async function _attachToActiveRun(startedAt) {
  document.getElementById('run-modal-params').style.display = 'none';
  document.getElementById('query-suggestions').style.display = 'none';
  document.getElementById('btn-start').style.display = 'none';
  document.getElementById('btn-generic-stop').style.display = '';
  document.getElementById('run-progress').style.display = '';
  document.getElementById('run-progress-bar').style.width = '0%';
  document.getElementById('run-progress-bar').classList.remove('error'); // clear a red bar left over from a previous failed run
  document.getElementById('run-progress-label').textContent = `Stage 0 / ${_AGENT_STAGE_COUNT}`;

  _runStartedAtMs = startedAt ? new Date(startedAt).getTime() : null;
  _clearLogPollTimer();
  // Guarantees _updateAgentIndicator sees a real running->not-running transition
  // below (and so actually toasts on failure) even if the background 5s poller
  // (pollAgentStatus) hasn't independently observed this run as active yet.
  _agentRunning = true;

  const first = await _pollActiveRunOnce();
  if (!first.running) {
    _runStartedAtMs = null;
    document.getElementById('btn-generic-stop').style.display = 'none';
    _applyFinalStageVisual(first.lastStatus);
    _updateAgentIndicator(false, first.lastStatus);
    loadStats();
    loadJobs();
    return;
  }
  _logPollTimer = setInterval(async () => {
    const { running, lastStatus } = await _pollActiveRunOnce();
    if (!running) {
      _clearLogPollTimer();
      _runStartedAtMs = null;
      document.getElementById('btn-generic-stop').style.display = 'none';
      _applyFinalStageVisual(lastStatus);
      _updateAgentIndicator(false, lastStatus);
      loadStats();
      loadJobs();
    }
  }, 4000);
}

async function openRunModal() {
  document.getElementById('run-modal-title').textContent = 'Run agent';
  document.getElementById('run-modal').classList.add('open');

  let running = false, startedAt = null;
  try {
    const r = await fetch('/api/agent/status');
    ({ running, started_at: startedAt } = await r.json());
  } catch (e) {}

  if (running) {
    await _attachToActiveRun(startedAt);
    return;
  }

  document.getElementById('run-modal-params').style.display = '';
  document.getElementById('btn-start').style.display = '';
  document.getElementById('btn-generic-stop').style.display = 'none';
  document.getElementById('run-progress').style.display = 'none';
  document.getElementById('run-log').textContent = '';
  _onDaysInput();
  await _loadQuerySuggestions();
}

// Fetched once per modal-open, before the run starts — never mid-run, so a run
// left unattended for hours never blocks on this (see the run-agent auto-cleanup
// discussion: suggestions are advisory, "none" is a first-class choice, and the
// user is definitionally present right now since they just opened this modal).
async function _loadQuerySuggestions() {
  const panel = document.getElementById('query-suggestions');
  const list = document.getElementById('qs-list');
  list.innerHTML = '';
  panel.style.display = 'none';
  try {
    const r = await fetch('/api/agent/query-suggestions');
    const { suggestions } = await r.json();
    if (!suggestions || !suggestions.length) return;
    for (const s of suggestions) {
      const label = document.createElement('label');
      label.className = 'qs-item';
      label.innerHTML = `
        <input type="checkbox" data-query="${s.search_query.replace(/"/g, '&quot;')}">
        <span class="st-tx"><span class="t">${s.search_query}</span><span class="d">${s.reason}</span></span>`;
      list.appendChild(label);
    }
    panel.style.display = '';
  } catch (e) {
    // Suggestions are a nice-to-have, not a gate — a failed fetch here should
    // never block starting a real run.
  }
}

async function _applyCheckedQuerySuggestions() {
  const checked = [...document.querySelectorAll('#qs-list input:checked')].map(el => el.dataset.query);
  if (!checked.length) return;
  try {
    await fetch('/api/agent/query-suggestions/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ queries: checked }),
    });
  } catch (e) {
    // Best-effort — a failed exclude here shouldn't block the run either;
    // the same queries will be suggested again next time.
  }
}

function closeRunModal() {
  document.getElementById('run-modal').classList.remove('open');
  // Only tears down this tab's view of the run — the pipeline itself is a
  // server-side process untouched by closing the socket or the poll timer.
  // stopAgent() (the only thing that actually ends the run) is a separate,
  // explicit button click.
  if (agentSocket) { agentSocket.close(); agentSocket = null; }
  _clearLogPollTimer();
  const btn = document.getElementById('btn-start');
  btn.textContent = 'Start';
  btn.onclick = startAgent;
}

let _agentRunning = false;

async function checkAgentStatus() {
  const r = await fetch('/api/agent/status');
  const s = await r.json();
  _updateAgentIndicator(s.running, s.last_status);
}

// The one place that reacts to a run actually finishing, regardless of *how*
// that was detected — the live websocket's __DONE__ (startAgent), the 4s
// reattach poll (_pollActiveRunOnce), or this function's own background 5s
// poll running with no modal open at all. A run that failed used to render
// identically to a clean one everywhere; lastStatus (runner.py's
// done/done_with_errors/error) is what makes that visible even to someone who
// wasn't watching when it happened.
function _updateAgentIndicator(running, lastStatus) {
  const pill = document.getElementById('running-pill');
  const runBtn = document.getElementById('run-btn');
  const wasRunning = _agentRunning;
  _agentRunning = running;

  pill.classList.toggle('on', running);
  runBtn.classList.toggle('running', running);
  runBtn.innerHTML = running
    ? '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12"/></svg> Stop'
    : '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg> Run agent';
  runBtn.onclick = running ? stopAgent : openRunModal;

  if (wasRunning && !running) {
    loadStats();
    loadJobs();
    if (lastStatus && lastStatus !== 'done') {
      const msg = lastStatus === 'error'
        ? 'Run failed — see the log for details.'
        : 'Run finished, but one or more stages failed — see the log for details.';
      showToast(msg, 'error');
    }
  }
}

function pollAgentStatus() {
  fetch('/api/agent/status')
    .then(r => r.json())
    .then(s => _updateAgentIndicator(s.running, s.last_status))
    .catch(() => {})
    .finally(() => setTimeout(pollAgentStatus, 5000));
}

// Fixed stage count for the main pipeline run (COLLECTOR + the 5 stages in
// runner.py's _post_collect_stages()) — used to drive a stage-level progress
// bar from the "=== STAGE ===" headers already streamed in the log, with no
// server-side protocol change needed.
const _AGENT_STAGE_COUNT = 6;

// One shared ticker for the elapsed-time label, rather than a per-flow
// setInterval — both startAgent()'s live websocket run and
// _attachToActiveRun()'s reattach-to-an-in-progress-run path set
// _runStartedAtMs and share this same display.
setInterval(_updateElapsedLabel, 1000);

async function startAgent() {
  await _applyCheckedQuerySuggestions();
  document.getElementById('query-suggestions').style.display = 'none';

  const sinceLast = document.getElementById('run-since-last').checked;
  const days = document.getElementById('run-days').value || 1;
  const log = document.getElementById('run-log');
  const btn = document.getElementById('btn-start');
  const prog = document.getElementById('run-progress');
  const bar = document.getElementById('run-progress-bar');
  const lbl = document.getElementById('run-progress-label');
  let stageIndex = 0;

  log.textContent = '';
  btn.textContent = 'Stop';
  btn.onclick = stopAgent;
  prog.style.display = '';
  bar.style.width = '0%';
  bar.classList.remove('error'); // clear a red bar left over from a previous failed run
  lbl.textContent = `Stage 0 / ${_AGENT_STAGE_COUNT}`;

  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  agentSocket = new WebSocket(`${proto}://${location.host}/ws/agent`);

  _runStartedAtMs = Date.now();

  agentSocket.onopen = () => {
    _agentRunning = true;
    _updateAgentIndicator(true);
    agentSocket.send(JSON.stringify(sinceLast ? { since_last_run: true } : { days: parseInt(days) }));
  };

  function _resetStartBtn() {
    btn.textContent = 'Start';
    btn.onclick = startAgent;
    _runStartedAtMs = null;
  }

  agentSocket.onmessage = e => {
    const doneMatch = e.data.match(/__DONE__:(\w+)/);
    if (doneMatch) {
      const finalStatus = doneMatch[1]; // 'done' | 'done_with_errors' | 'error'
      _applyFinalStageVisual(finalStatus);
      _resetStartBtn();
      _updateAgentIndicator(false, finalStatus);
      loadStats();
      loadJobs();
      return;
    }
    const stageMatch = e.data.match(/^=== (.+?) ===/m);
    if (stageMatch) {
      stageIndex++;
      const pct = Math.round(stageIndex / _AGENT_STAGE_COUNT * 100);
      bar.style.width = pct + '%';
      lbl.textContent = `Stage ${stageIndex} / ${_AGENT_STAGE_COUNT}: ${stageMatch[1]}`;
    }
    log.textContent += e.data;
    log.scrollTop = log.scrollHeight;
  };

  agentSocket.onerror = () => {
    if (!_stopping) log.textContent += '\nWebSocket error.\n';
    _resetStartBtn();
    _stopping = false;
  };

  agentSocket.onclose = () => { agentSocket = null; };
}

// ── Search / sort listeners ──────────────────────────────────────────────────────

let searchTimeout;
document.getElementById('search').addEventListener('input', () => {
  clearTimeout(searchTimeout);
  searchTimeout = setTimeout(loadJobs, 300);
});
document.getElementById('source-filter').addEventListener('change', loadJobs);
document.getElementById('sort').addEventListener('change', render);

// ── Init ──────────────────────────────────────────────────────────────────────

loadStats();
_loadLearnedCard();
_loadSources().then(loadJobs);
pollAgentStatus();
