// Matches every language the backend's language filter (collector/language_filter.py)
// can actually detect — suggesting a language it can't recognize would be misleading.
const LANGUAGE_SUGGESTIONS = [
  'English', 'Polish', 'German', 'French', 'Spanish', 'Italian', 'Portuguese', 'Dutch',
  'Russian', 'Ukrainian', 'Czech', 'Slovak', 'Romanian', 'Bulgarian', 'Croatian', 'Slovenian',
  'Hungarian', 'Greek', 'Swedish', 'Norwegian', 'Danish', 'Finnish', 'Estonian', 'Latvian',
  'Lithuanian', 'Turkish', 'Arabic', 'Hebrew', 'Persian', 'Hindi', 'Bengali', 'Urdu',
  'Chinese (Simplified)', 'Chinese (Traditional)', 'Japanese', 'Korean', 'Vietnamese',
  'Thai', 'Indonesian', 'Tagalog', 'Swahili', 'Afrikaans', 'Welsh', 'Catalan', 'Albanian',
  'Macedonian', 'Somali',
];
const LEVEL_OPTIONS = ['Native', 'C2', 'C1', 'B2', 'B1', 'A2', 'A1'];

const COUNTRY_SUGGESTIONS = [
  'Poland', 'Germany', 'United Kingdom', 'Netherlands', 'France', 'Spain', 'Portugal', 'Italy',
  'Ireland', 'Belgium', 'Switzerland', 'Austria', 'Sweden', 'Norway', 'Denmark', 'Finland',
  'Czechia', 'Slovakia', 'Hungary', 'Romania', 'Bulgaria', 'Croatia', 'Slovenia', 'Greece',
  'Estonia', 'Latvia', 'Lithuania', 'Ukraine', 'Serbia', 'Iceland', 'Luxembourg', 'Malta',
  'Cyprus', 'United States', 'Canada', 'Mexico', 'Brazil', 'Argentina', 'Chile', 'Colombia',
  'United Arab Emirates', 'Israel', 'Turkey', 'India', 'Singapore', 'Japan', 'South Korea',
  'Australia', 'New Zealand', 'South Africa', 'Egypt', 'Morocco',
];

const COUNTRY_GROUPS = {
  EU: [
    'Austria', 'Belgium', 'Bulgaria', 'Croatia', 'Cyprus', 'Czechia', 'Denmark', 'Estonia',
    'Finland', 'France', 'Germany', 'Greece', 'Hungary', 'Ireland', 'Italy', 'Latvia',
    'Lithuania', 'Luxembourg', 'Malta', 'Netherlands', 'Poland', 'Portugal', 'Romania',
    'Slovakia', 'Slovenia', 'Spain', 'Sweden',
  ],
  EUROZONE: [
    'Austria', 'Belgium', 'Croatia', 'Cyprus', 'Estonia', 'Finland', 'France', 'Germany',
    'Greece', 'Ireland', 'Italy', 'Latvia', 'Lithuania', 'Luxembourg', 'Malta', 'Netherlands',
    'Portugal', 'Slovakia', 'Slovenia', 'Spain',
  ],
  NATO: [
    'Albania', 'Belgium', 'Bulgaria', 'Canada', 'Croatia', 'Czechia', 'Denmark', 'Estonia',
    'Finland', 'France', 'Germany', 'Greece', 'Hungary', 'Iceland', 'Italy', 'Latvia',
    'Lithuania', 'Luxembourg', 'Montenegro', 'Netherlands', 'North Macedonia', 'Norway',
    'Poland', 'Portugal', 'Romania', 'Slovakia', 'Slovenia', 'Spain', 'Sweden', 'Turkey',
    'United Kingdom', 'United States',
  ],
};

// Adds every country in the named group that isn't already a chip — does not
// remove or track anything, so re-clicking (or clicking an overlapping group
// later) re-adds anything the user removed in the meantime. That's intentional:
// the button always means "make sure this whole group is present right now."
function addCountryGroup(groupName) {
  const existing = new Set(getChipValues('country-chips').map(v => v.toLowerCase()));
  (COUNTRY_GROUPS[groupName] || []).forEach(country => {
    if (!existing.has(country.toLowerCase())) addChipValue('country-chips', country);
  });
}

const CITY_SUGGESTIONS = [
  'Warsaw', 'Kraków', 'Wrocław', 'Gdańsk', 'Poznań', 'Łódź', 'Katowice', 'Szczecin',
  'Berlin', 'Munich', 'Hamburg', 'Frankfurt', 'Cologne', 'Stuttgart',
  'London', 'Manchester', 'Edinburgh', 'Bristol',
  'Amsterdam', 'Rotterdam', 'The Hague', 'Utrecht',
  'Paris', 'Lyon', 'Marseille', 'Toulouse',
  'Madrid', 'Barcelona', 'Valencia', 'Lisbon', 'Porto',
  'Milan', 'Rome', 'Turin',
  'Dublin', 'Cork',
  'Brussels', 'Antwerp',
  'Zurich', 'Geneva', 'Basel',
  'Vienna', 'Graz',
  'Stockholm', 'Gothenburg', 'Copenhagen', 'Oslo', 'Helsinki',
  'Prague', 'Brno', 'Bratislava', 'Budapest', 'Bucharest', 'Sofia', 'Zagreb', 'Ljubljana',
  'Athens', 'Vilnius', 'Riga', 'Tallinn', 'Kyiv',
  'New York', 'San Francisco', 'Austin', 'Boston', 'Seattle', 'Chicago', 'Toronto', 'Vancouver',
  'Dubai', 'Tel Aviv', 'Istanbul', 'Bangalore', 'Singapore', 'Tokyo', 'Seoul',
  'Sydney', 'Melbourne',
];

function toggleTheme() {
  const root = document.documentElement;
  const current = root.getAttribute('data-theme')
    || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  root.setAttribute('data-theme', current === 'dark' ? 'light' : 'dark');
}

document.addEventListener('click', (e) => {
  document.querySelectorAll('details.nav-menu[open]').forEach(menu => {
    if (!menu.contains(e.target)) menu.removeAttribute('open');
  });
});
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  document.querySelectorAll('details.nav-menu[open]').forEach(menu => menu.removeAttribute('open'));
});

function toggle(el) { el.classList.toggle('on'); }

function toggleWorkMode(el) {
  el.classList.toggle('on');
  updateWorkModePanels();
}

function updateWorkModePanels() {
  const remote = document.querySelector('#workmode-group [data-value="remote"]').classList.contains('on');
  const hybridOrOnsite = document.querySelector('#workmode-group [data-value="hybrid"]').classList.contains('on')
    || document.querySelector('#workmode-group [data-value="onsite"]').classList.contains('on');
  document.getElementById('remote-panel').style.display = remote ? '' : 'none';
  document.getElementById('hybrid-panel').style.display = hybridOrOnsite ? '' : 'none';
}

function makeRemovableChip(value) {
  const chip = document.createElement('span');
  chip.className = 'chip removable on';
  chip.dataset.value = value;
  chip.appendChild(document.createTextNode(value + ' '));
  const x = document.createElement('span');
  x.className = 'x';
  x.textContent = '✕';
  x.onclick = (e) => { e.stopPropagation(); chip.remove(); };
  chip.appendChild(x);
  return chip;
}

function addChipValue(containerId, value) {
  if (!value) return;
  document.getElementById(containerId).appendChild(makeRemovableChip(value));
}

function addChip(containerId, inputId) {
  const input = document.getElementById(inputId);
  const val = input.value.trim();
  if (!val) return;
  addChipValue(containerId, val);
  input.value = '';
  input.focus();
}

function getChipValues(containerId) {
  return Array.from(document.querySelectorAll(`#${containerId} .chip`)).map(c => c.dataset.value);
}

function getToggledValues(containerId) {
  return Array.from(document.querySelectorAll(`#${containerId} .chip.on`)).map(c => c.dataset.value);
}

function setToggled(containerId, values) {
  const set = new Set(values || []);
  document.querySelectorAll(`#${containerId} .chip`).forEach(c => {
    c.classList.toggle('on', set.has(c.dataset.value));
  });
}

function makeLangRow(language, level) {
  const row = document.createElement('div');
  row.className = 'lang-row';

  const langInput = document.createElement('input');
  langInput.type = 'text';
  langInput.className = 'lang-input';
  langInput.setAttribute('list', 'language-suggestions');
  langInput.placeholder = 'Language, e.g. English';
  langInput.value = language ? language.replace(/\b\w/g, c => c.toUpperCase()) : '';

  const levelSelect = document.createElement('select');
  levelSelect.className = 'level-select';
  LEVEL_OPTIONS.forEach(opt => {
    const o = document.createElement('option');
    o.value = opt;
    o.textContent = opt;
    if (opt === (level || 'B2')) o.selected = true;
    levelSelect.appendChild(o);
  });

  const removeBtn = document.createElement('button');
  removeBtn.className = 'btn-add';
  removeBtn.type = 'button';
  removeBtn.textContent = 'Remove';
  removeBtn.onclick = () => row.remove();

  row.appendChild(langInput);
  row.appendChild(levelSelect);
  row.appendChild(removeBtn);
  return row;
}

function addLangRow(language, level) {
  document.getElementById('lang-rows').appendChild(makeLangRow(language, level));
}

function getLanguages() {
  return Array.from(document.querySelectorAll('#lang-rows .lang-row'))
    .map(row => ({
      language: row.querySelector('.lang-input').value.trim().toLowerCase(),
      level: row.querySelector('.level-select').value,
    }))
    .filter(l => l.language);
}

function renderDetectedChips(parsed) {
  const panel = document.getElementById('detected-panel');
  const container = document.getElementById('detected-chips');
  container.innerHTML = '';
  const values = [];
  if (parsed.seniority) values.push(parsed.seniority);
  if (parsed.years_experience) values.push(`${parsed.years_experience} years experience`);
  (parsed.stack || []).forEach(t => values.push(t));
  if (!values.length) return;
  values.forEach(v => {
    const span = document.createElement('span');
    span.className = 'chip-static';
    span.textContent = v;
    container.appendChild(span);
  });
  panel.style.display = '';
}

async function replaceCV(file) {
  const formData = new FormData();
  formData.append('file', file);
  const status = document.getElementById('save-status');
  status.textContent = 'Uploading CV…';
  status.classList.remove('is-error');
  try {
    const res = await fetch('/api/cv/upload', { method: 'POST', body: formData });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Upload failed');
    document.getElementById('cv-filename').textContent = file.name;
    renderDetectedChips(data.parsed || {});
    status.textContent = 'CV replaced.';
  } catch (err) {
    status.textContent = err.message;
    status.classList.add('is-error');
  }
}

async function savePreferences() {
  const btn = document.getElementById('save-btn');
  const status = document.getElementById('save-status');
  btn.disabled = true;
  status.classList.remove('is-error');
  status.textContent = 'Saving…';

  const salaryMin = document.getElementById('salary-min').value;
  const seniorityLevels = getToggledValues('seniority-group');
  const companyTypes = getToggledValues('company-group');

  const fields = {
    work_mode: getToggledValues('workmode-group'),
    work_country: document.getElementById('work-country').value.trim(),
    employer_countries: getChipValues('country-chips'),
    hybrid_cities: getChipValues('city-chips'),
    salary_min: salaryMin ? parseInt(salaryMin, 10) : null,
    salary_currency: document.getElementById('salary-currency').value,
    show_jobs_without_salary: document.getElementById('show-no-salary').checked ? 1 : 0,
    seniority_levels: seniorityLevels,
    required_seniority_levels: document.getElementById('seniority-hard').checked ? seniorityLevels : [],
    role_types: getToggledValues('role-group'),
    preferred_company_types: companyTypes,
    required_company_types: document.getElementById('company-hard').checked ? companyTypes : [],
    extra_tech: getChipValues('tech-chips'),
    avoided_tech: getChipValues('avoid-chips'),
    languages: getLanguages(),
    open_notes: document.getElementById('notes').value.trim(),
  };

  try {
    const res = await fetch('/api/candidate-preferences', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(fields),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Save failed');
    status.textContent = 'Saved — returning to your dashboard…';
    setTimeout(() => { window.location.href = '/'; }, 700);
  } catch (err) {
    btn.disabled = false;
    status.textContent = err.message;
    status.classList.add('is-error');
  }
}

function populateDatalist(datalistId, values) {
  const datalist = document.getElementById(datalistId);
  values.forEach(v => {
    const option = document.createElement('option');
    option.value = v;
    datalist.appendChild(option);
  });
}

async function init() {
  populateDatalist('country-suggestions', COUNTRY_SUGGESTIONS);
  populateDatalist('city-suggestions', CITY_SUGGESTIONS);
  populateDatalist('language-suggestions', LANGUAGE_SUGGESTIONS);

  Object.entries(COUNTRY_GROUPS).forEach(([name, countries]) => {
    const tip = document.getElementById(`tip-${name}`);
    if (tip) tip.textContent = countries.join(', ');
  });

  document.getElementById('cv-replace-file').addEventListener('change', (e) => {
    if (e.target.files[0]) replaceCV(e.target.files[0]);
  });

  const [cv, prefs] = await Promise.all([
    fetch('/api/cv/active').then(r => r.json()).catch(() => null),
    fetch('/api/candidate-preferences').then(r => r.json()).catch(() => ({})),
  ]);

  if (cv) {
    document.getElementById('cv-filename').textContent = cv.filename;
    renderDetectedChips(cv.parsed || {});
  }

  const hasPrefs = prefs && Object.keys(prefs).length > 0;

  setToggled('workmode-group', hasPrefs ? prefs.work_mode : ['remote']);
  const detectedCountry = cv && cv.parsed ? (cv.parsed.location || '') : '';
  const legacyWorkCountry = hasPrefs && (prefs.remote_countries || []).length === 1 ? prefs.remote_countries[0] : '';
  document.getElementById('work-country').value = hasPrefs
    ? (prefs.work_country || legacyWorkCountry || detectedCountry)
    : detectedCountry;
  (hasPrefs ? (prefs.employer_countries || []) : []).forEach(c => addChipValue('country-chips', c));
  (hasPrefs ? prefs.hybrid_cities : []).forEach(c => addChipValue('city-chips', c));
  updateWorkModePanels();

  if (hasPrefs) {
    if (prefs.salary_min != null) document.getElementById('salary-min').value = prefs.salary_min;
    if (prefs.salary_currency) document.getElementById('salary-currency').value = prefs.salary_currency;
    document.getElementById('show-no-salary').checked = !!prefs.show_jobs_without_salary;
  }

  setToggled('seniority-group', hasPrefs ? prefs.seniority_levels : (cv && cv.parsed && cv.parsed.seniority ? [cv.parsed.seniority.toLowerCase()] : []));
  document.getElementById('seniority-hard').checked = !!(hasPrefs && (prefs.required_seniority_levels || []).length);
  setToggled('role-group', hasPrefs ? prefs.role_types : []);
  setToggled('company-group', hasPrefs ? prefs.preferred_company_types : []);
  document.getElementById('company-hard').checked = !!(hasPrefs && (prefs.required_company_types || []).length);

  const tech = hasPrefs ? (prefs.extra_tech || []) : (cv && cv.parsed ? (cv.parsed.stack || []) : []);
  tech.forEach(t => addChipValue('tech-chips', t));
  (hasPrefs ? (prefs.avoided_tech || []) : []).forEach(t => addChipValue('avoid-chips', t));

  const languages = hasPrefs ? (prefs.languages || []) : [];
  if (languages.length) {
    languages.forEach(l => addLangRow(l.language, l.level));
  } else {
    addLangRow('english', 'B2');
  }

  if (hasPrefs && prefs.open_notes) document.getElementById('notes').value = prefs.open_notes;
}

init();
