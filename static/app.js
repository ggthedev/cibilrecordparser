'use strict';

// =====================================================================
// CIBIL Viewer SPA
// Single-page client: fetches /api/data once per file selection, then
// renders sections client-side. /api/search is delegated to the backend
// (which reuses query_cibil._search verbatim).
// =====================================================================

const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

const SECTION_KEYS = {
  accounts: 'accounts',
  enquiries: 'enquiry_details',
  addresses: 'address_details',
  contacts: 'contact_details',
  emails: 'email_details',
  identification: 'identification_details',
};

const state = {
  data: null,
  file: null,
  files: [],
  section: 'summary',
  search: '',          // current search term (server-side)
  searchHits: null,    // last server response
  selected: null,      // {section, index} when drilled into a record
};

// ---------------------------------------------------------------------
// DOM helpers
// ---------------------------------------------------------------------
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

function el(tag, attrs, ...children) {
  const e = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === 'class') e.className = v;
      else if (k === 'html') e.innerHTML = v;
      else if (k === 'dataset') Object.assign(e.dataset, v);
      else if (k.startsWith('on') && typeof v === 'function') {
        e.addEventListener(k.slice(2).toLowerCase(), v);
      } else {
        e.setAttribute(k, v);
      }
    }
  }
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    e.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return e;
}

const human = (k) => String(k).replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
const safe = (v) => (v == null || v === '') ? '-' : String(v);
const isBlank = (v) => v == null || v === '' || v === '-';

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function highlight(text, term) {
  if (!term) return escapeHtml(text);
  // We escape the rendered text first, then build the regex from the escaped
  // term so the pattern matches the escaped form consistently (e.g. "&" in a
  // search term becomes "&amp;" in both haystack and needle).
  const escapedTerm = escapeHtml(term).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return escapeHtml(text).replace(
    new RegExp(escapedTerm, 'gi'),
    (m) => `<mark class="hit">${m}</mark>`,
  );
}

// ---------------------------------------------------------------------
// API client
// ---------------------------------------------------------------------
async function api(path) {
  const r = await fetch(path, { headers: { 'Accept': 'application/json' } });
  if (!r.ok) {
    const text = await r.text().catch(() => '');
    throw new Error(`${path} -> HTTP ${r.status}${text ? ': ' + text.slice(0, 200) : ''}`);
  }
  return r.json();
}

const fileQS = () => state.file ? `file=${encodeURIComponent(state.file)}` : '';

async function loadFiles() {
  state.files = await api('/api/files');
  const sel = $('#file-select');
  sel.replaceChildren();
  if (!state.files.length) {
    sel.appendChild(el('option', { value: '' }, '(no output JSON found)'));
    sel.disabled = true;
    return;
  }
  sel.disabled = false;
  for (const f of state.files) {
    const label = f.is_latest ? `${f.name}  · latest` : f.name;
    sel.appendChild(el('option', { value: f.name }, label));
  }
  const latest = state.files.find(f => f.is_latest) || state.files[0];
  state.file = latest.name;
  sel.value = state.file;
}

async function loadData() {
  const qs = fileQS();
  const resp = await api(`/api/data${qs ? '?' + qs : ''}`);
  state.data = resp.data;
  state.file = resp.file;
}

async function runSearch(term) {
  const params = new URLSearchParams();
  params.set('q', term);
  if (state.file) params.set('file', state.file);
  const resp = await api(`/api/search?${params.toString()}`);
  return resp.hits || [];
}

// ---------------------------------------------------------------------
// header / sidebar setup
// ---------------------------------------------------------------------
function setHeader() {
  const d = state.data || {};
  $('#consumer').textContent = d.personal_details?.name || '';
  $('#report-date').textContent = d.report?.date ? `Report ${d.report.date}` : '';

  const score = d.cibil_score?.score;
  const badge = $('#score-badge');
  if (score) {
    badge.classList.remove('hidden');
    badge.classList.add('score-badge');
    $('#score-value').textContent = score;
    const n = parseInt(score, 10);
    badge.classList.remove('score-good', 'score-fair', 'score-poor', 'score-na');
    if (Number.isFinite(n)) {
      if (n >= 750) badge.classList.add('score-good');
      else if (n >= 700) badge.classList.add('score-fair');
      else badge.classList.add('score-poor');
    } else {
      badge.classList.add('score-na');
    }
  } else {
    badge.classList.add('hidden');
  }

  $('#source-info').textContent = state.file || '(no source)';

  // Sidebar counts.
  for (const [navKey, dataKey] of Object.entries(SECTION_KEYS)) {
    const node = document.querySelector(`[data-count="${navKey}"]`);
    if (!node) continue;
    const arr = d[dataKey] || [];
    node.textContent = arr.length;
  }
}

function setActiveNav() {
  $$('#sidebar .nav-btn').forEach(b => {
    if (b.dataset.section === state.section && !state.search) {
      b.classList.add('active');
    } else {
      b.classList.remove('active');
    }
  });
}

// ---------------------------------------------------------------------
// reusable building blocks
// ---------------------------------------------------------------------
function card(title, ...children) {
  return el('section', { class: 'card' },
    title ? el('h2', { class: 'card-title' }, title) : null,
    el('div', { class: 'card-body' }, ...children),
  );
}

function cardFlush(title, ...children) {
  return el('section', { class: 'card' },
    title ? el('h2', { class: 'card-title' }, title) : null,
    el('div', { class: 'card-body card-body--flush' }, ...children),
  );
}

function sectionHeader(title, subtitle) {
  return el('div', { class: 'section-header' },
    el('h2', { class: 'section-title' }, title),
    subtitle ? el('div', { class: 'section-subtitle' }, subtitle) : null,
  );
}

function kvTable(pairs) {
  return el('div', { class: 'kv' },
    pairs.map(([k, v]) => {
      const blank = isBlank(v);
      const valEl = el('div', { class: 'kv-val' + (blank ? ' muted' : '') }, blank ? '—' : String(v));
      return el('div', { class: 'kv-row' },
        el('div', { class: 'kv-key' }, k),
        valEl,
      );
    }),
  );
}

function statusPill(status) {
  const cls = status === 'open' ? 'pill pill-open'
            : status === 'closed' ? 'pill pill-closed'
            : 'pill pill-ghost';
  return el('span', { class: cls }, status || '-');
}

function overduePill(amount) {
  if (isBlank(amount)) return null;
  // Treat literal "₹0" / "0" as not overdue.
  const stripped = String(amount).replace(/[^0-9.]/g, '');
  const n = parseFloat(stripped);
  if (Number.isFinite(n) && n === 0) return null;
  return el('span', { class: 'pill pill-warn' }, `Overdue ${amount}`);
}

// ---------------------------------------------------------------------
// SUMMARY
// ---------------------------------------------------------------------
function renderSummary() {
  const d = state.data;
  const score = d.cibil_score || {};
  const rep = d.report || {};
  const p = d.personal_details || {};
  const accts = d.accounts || [];
  const open = accts.filter(a => a.status === 'open').length;
  const closed = accts.filter(a => a.status === 'closed').length;

  const breakdown = {};
  for (const a of accts) {
    const key = `${a.status || '?'} / ${a.account_type || '?'}`;
    breakdown[key] = (breakdown[key] || 0) + 1;
  }
  const sortedBreakdown = Object.entries(breakdown).sort(([a], [b]) => a.localeCompare(b));

  // Quick-glance metrics across all accounts.
  let totalLimit = 0, totalBalance = 0, totalOverdue = 0;
  for (const a of accts) {
    const ad = a.account_details || {};
    totalLimit  += parseInr(ad.credit_limit);
    totalBalance += parseInr(ad.current_balance);
    totalOverdue += parseInr(ad.amount_overdue);
  }

  return el('div', { class: 'space-y-6' },
    sectionHeader('Summary', `Snapshot from ${state.file || '(no file)'}`),

    el('div', { class: 'grid grid-cols-1 md:grid-cols-3 gap-4' },
      kpi('CIBIL Score', score.score || '—', score.as_of_date ? `as of ${score.as_of_date}` : ''),
      kpi('Accounts', String(accts.length), `${open} open · ${closed} closed`),
      kpi('Enquiries', String((d.enquiry_details || []).length), 'lifetime'),
    ),

    el('div', { class: 'grid grid-cols-1 md:grid-cols-3 gap-4' },
      kpi('Total credit limit',   totalLimit ? formatInr(totalLimit) : '—', 'across open + closed'),
      kpi('Total current balance', totalBalance ? formatInr(totalBalance) : '—', ''),
      kpi('Total amount overdue', totalOverdue ? formatInr(totalOverdue) : '—',
          totalOverdue > 0 ? 'review the accounts tab' : 'good standing'),
    ),

    card('Identity',
      kvTable([
        ['Consumer', p.name],
        ['Date of Birth', p.date_of_birth],
        ['Report Date', rep.date],
        ['Control Number', rep.control_number],
      ]),
    ),

    cardFlush('Account breakdown by type',
      el('table', { class: 'table' },
        el('thead', null, el('tr', null,
          el('th', null, 'Status / Type'),
          el('th', { class: 'text-right' }, 'Count'),
        )),
        el('tbody', null,
          sortedBreakdown.map(([k, v]) => el('tr', null,
            el('td', null, k),
            el('td', { class: 'text-right tabular-nums' }, String(v)),
          )),
        ),
      ),
    ),
  );
}

function kpi(label, value, sub) {
  return el('section', { class: 'card' },
    el('div', { class: 'card-body' },
      el('div', { class: 'text-[11px] uppercase tracking-wider text-slate-500 font-semibold' }, label),
      el('div', { class: 'mt-1 text-2xl font-bold tabular-nums tracking-tight' }, value),
      sub ? el('div', { class: 'mt-0.5 text-xs text-slate-500' }, sub) : null,
    ),
  );
}

// Parse an Indian-formatted currency string ("₹2,22,500") to a number.
// Returns 0 for blanks/dashes/non-numerics.
function parseInr(v) {
  if (isBlank(v)) return 0;
  const stripped = String(v).replace(/[^0-9.]/g, '');
  const n = parseFloat(stripped);
  return Number.isFinite(n) ? n : 0;
}

// Format a number using Indian grouping with the rupee symbol.
function formatInr(n) {
  if (!Number.isFinite(n)) return '—';
  const x = Math.round(n);
  const s = String(x);
  // Indian grouping: last 3 digits, then groups of 2.
  if (s.length <= 3) return '₹' + s;
  const last3 = s.slice(-3);
  const rest = s.slice(0, -3);
  const grouped = rest.replace(/\B(?=(\d{2})+(?!\d))/g, ',');
  return '₹' + grouped + ',' + last3;
}

// ---------------------------------------------------------------------
// PERSONAL
// ---------------------------------------------------------------------
function renderPersonal() {
  const d = state.data;
  const p = d.personal_details || {};
  const emp = d.employment_details || {};
  return el('div', { class: 'space-y-6' },
    sectionHeader('Personal & Employment'),
    card('Personal Details',
      kvTable(Object.entries(p).map(([k, v]) => [human(k), v]))),
    card('Employment Details',
      kvTable(Object.entries(emp).map(([k, v]) => [human(k), v]))),
  );
}

// ---------------------------------------------------------------------
// ACCOUNTS (list + detail)
// ---------------------------------------------------------------------
function renderAccounts() {
  const accts = state.data.accounts || [];
  return el('div', { class: 'space-y-4' },
    sectionHeader('Accounts', `${accts.length} total · click any row for full details`),
    el('div', { class: 'space-y-2' },
      accts.map((a, i) => accountRow(a, i)),
    ),
  );
}

function accountRow(a, i) {
  const ad = a.account_details || {};
  const lim = ad.credit_limit || ad.sanctioned_amount || '-';
  const bal = ad.current_balance || '-';

  return el('button', {
    class: 'list-item',
    onclick: () => { state.selected = { section: 'accounts', index: i }; render(); },
  },
    el('div', { class: 'flex items-center gap-3 flex-wrap' },
      statusPill(a.status),
      el('div', { class: 'font-semibold text-slate-900' }, a.member_name || '-'),
      el('div', { class: 'text-sm text-slate-500' }, a.account_type || '-'),
      overduePill(ad.amount_overdue),
      el('div', { class: 'flex-1' }),
      el('div', { class: 'font-mono text-xs text-slate-400' }, `#${a.account_number || '-'}`),
    ),
    el('div', { class: 'mt-1.5 grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-1 text-xs text-slate-500' },
      el('div', null, `Opened: `, el('span', { class: 'text-slate-700' }, ad.date_opened_disbursed || '-')),
      el('div', null, `Last reported: `, el('span', { class: 'text-slate-700' }, ad.date_reported_and_certified || '-')),
      el('div', null, `Limit: `, el('span', { class: 'text-slate-700 tabular-nums' }, lim)),
      el('div', null, `Balance: `, el('span', { class: 'text-slate-700 tabular-nums' }, bal)),
    ),
  );
}

function renderAccountDetail(i) {
  const a = state.data.accounts[i];
  if (!a) return el('div', { class: 'text-red-600' }, `No account at index ${i}`);
  const ad = a.account_details || {};
  const ps = a.payment_status || {};
  const ph = a.payment_history || [];

  return el('div', { class: 'space-y-5' },
    el('button', {
      class: 'back-btn',
      onclick: () => { state.selected = null; render(); },
    },
      el('span', { html: '&larr;' }),
      ' Back to accounts',
    ),

    el('section', { class: 'card' },
      el('div', { class: 'px-5 pt-4 pb-3 border-b border-slate-200' },
        el('div', { class: 'flex items-center gap-3 flex-wrap' },
          statusPill(a.status),
          el('h2', { class: 'text-xl font-semibold tracking-tight' }, a.member_name || '-'),
          el('span', { class: 'text-slate-500 text-sm' }, a.account_type || '-'),
          overduePill(ad.amount_overdue),
        ),
        el('div', { class: 'mt-2 flex flex-wrap items-baseline gap-x-6 gap-y-1 text-xs text-slate-500' },
          el('div', null, 'Account #', el('span', { class: 'ml-1 font-mono text-slate-700' }, a.account_number || '-')),
          el('div', null, 'Ownership: ', el('span', { class: 'text-slate-700' }, a.ownership || '-')),
        ),
      ),
      el('div', { class: 'card-body' },
        kvTable(Object.entries(ad).map(([k, v]) => [human(k), v])),
      ),
    ),

    Object.keys(ps).length ? card('Payment Status',
      kvTable(Object.entries(ps).map(([k, v]) => [human(k), v])),
    ) : null,

    paymentHistoryCard(ph),
  );
}

function paymentHistoryCard(ph) {
  const wrap = el('section', { class: 'card' });
  let showFull = false;

  const titleRow = el('div', { class: 'flex items-center justify-between px-5 py-3 border-b border-slate-200' },
    el('div', null,
      el('h2', { class: 'text-[11px] uppercase tracking-widest font-semibold text-slate-600' }, 'Payment History'),
      el('div', { class: 'text-xs text-slate-500 mt-0.5', id: 'ph-range' }, ''),
    ),
    el('label', { class: 'inline-flex items-center gap-2 text-xs text-slate-600 cursor-pointer select-none' },
      el('input', {
        type: 'checkbox',
        class: 'accent-slate-900',
        onchange: (e) => {
          showFull = e.target.checked;
          body.replaceChildren(...buildHistoryGrid(ph, showFull));
        },
      }),
      'show full history',
    ),
  );

  const body = el('div', { class: 'card-body card-body--flush p-3' });
  body.replaceChildren(...buildHistoryGrid(ph, showFull));
  wrap.append(titleRow, body);

  // Range hint
  const range = phRange(ph);
  if (range) titleRow.querySelector('#ph-range').textContent = range;
  return wrap;
}

function phRange(ph) {
  if (!ph || !ph.length) return '';
  const ymd = [];
  for (const e of ph) {
    const m = /^([A-Za-z]{3})\s+(\d{4})$/.exec(e.month || '');
    if (!m) continue;
    const mi = MONTHS.indexOf(m[1]);
    if (mi < 0) continue;
    ymd.push(parseInt(m[2], 10) * 12 + mi);
  }
  if (!ymd.length) return '';
  ymd.sort((a, b) => a - b);
  const lo = ymd[0], hi = ymd[ymd.length - 1];
  const fmt = (n) => `${MONTHS[n % 12]} ${Math.floor(n / 12)}`;
  return `${fmt(lo)}  →  ${fmt(hi)}  (${ymd.length} entries)`;
}

function buildHistoryGrid(ph, full) {
  if (!ph || !ph.length) {
    return [el('div', { class: 'text-slate-500 text-sm px-2 py-2' }, '(no payment history)')];
  }
  const grid = {};
  for (const e of ph) {
    const m = /^([A-Za-z]{3})\s+(\d{4})$/.exec(e.month || '');
    if (!m) continue;
    const yr = parseInt(m[2], 10), mn = m[1];
    (grid[yr] ||= {})[mn] = e.value || '';
  }
  let years = Object.keys(grid).map(Number).sort((a, b) => b - a);
  const truncated = !full && years.length > 3;
  if (truncated) years = years.slice(0, 3);

  const table = el('table', { class: 'table table-history' },
    el('thead', null, el('tr', null,
      el('th', null, 'Year'),
      MONTHS.map(m => el('th', null, m)),
    )),
    el('tbody', null,
      years.map(y => el('tr', null,
        el('td', { class: 'tabular-nums' }, String(y)),
        MONTHS.map(m => {
          const v = (grid[y] || {})[m] || '';
          return el('td', { class: `ph-cell ${classifyPHValue(v)}` }, v || '·');
        }),
      )),
    ),
  );

  const result = [table];
  if (truncated) {
    const total = Object.keys(grid).length;
    result.push(el('div', { class: 'mt-2 px-2 text-xs text-slate-500' },
      `Showing newest 3 years of ${total}. Toggle "show full history" above for the rest.`));
  }
  return result;
}

function classifyPHValue(v) {
  if (!v) return 'ph-empty';
  const s = String(v).trim();
  if (s === '' || s === '-' || s === 'XXX') return 'ph-empty';
  if (s === 'STD' || s === '000') return 'ph-ok';
  if (/^[A-Z]+$/.test(s)) return 'ph-flag';
  const n = parseInt(s, 10);
  if (Number.isFinite(n)) {
    if (n === 0) return 'ph-ok';
    if (n <= 30) return 'ph-warn1';
    if (n <= 90) return 'ph-warn2';
    return 'ph-bad';
  }
  return 'ph-flag';
}

// ---------------------------------------------------------------------
// other simple sections
// ---------------------------------------------------------------------
function renderEnquiries() {
  const items = state.data.enquiry_details || [];
  return el('div', { class: 'space-y-4' },
    sectionHeader('Enquiries', `${items.length} total`),
    el('div', { class: 'space-y-1.5' },
      items.map(e => el('div', { class: 'list-item-static' },
        el('div', { class: 'flex flex-wrap items-center gap-3' },
          el('div', { class: 'font-medium' }, e.member_name || '-'),
          el('div', { class: 'pill pill-ghost' }, e.enquiry_purpose || '-'),
          el('div', { class: 'flex-1' }),
          el('div', { class: 'text-xs text-slate-500 tabular-nums' }, e.date_of_enquiry || '-'),
        ),
      )),
    ),
  );
}

function renderAddresses() {
  const items = state.data.address_details || [];
  return el('div', { class: 'space-y-4' },
    sectionHeader('Addresses', `${items.length} total`),
    el('div', { class: 'space-y-3' },
      items.map(a => el('section', { class: 'card' },
        el('div', { class: 'card-body' },
          el('div', { class: 'flex items-baseline gap-3 mb-2 flex-wrap' },
            el('span', { class: 'pill pill-info' }, a.category || '-'),
            !isBlank(a.residence_code) ? el('span', { class: 'pill pill-ghost' }, a.residence_code) : null,
            el('div', { class: 'flex-1' }),
            el('div', { class: 'text-xs text-slate-500' },
              `Reported ${a.date_reported || '-'}`),
          ),
          el('div', { class: 'text-sm text-slate-800 leading-relaxed' }, a.address || '-'),
        ),
      )),
    ),
  );
}

function renderContacts() {
  const items = state.data.contact_details || [];
  return el('div', { class: 'space-y-4' },
    sectionHeader('Contacts', `${items.length} total`),
    el('div', { class: 'space-y-1.5' },
      items.map(c => el('div', { class: 'list-item-static' },
        el('div', { class: 'flex flex-wrap items-center gap-3' },
          el('span', { class: 'pill pill-ghost' }, c.telephone_number_type || '-'),
          el('span', { class: 'font-mono text-sm text-slate-800' }, c.telephone_number || '-'),
          el('span', { class: 'text-xs text-slate-500' }, `ext ${c.telephone_extension || '-'}`),
        ),
      )),
    ),
  );
}

function renderEmails() {
  const items = state.data.email_details || [];
  return el('div', { class: 'space-y-4' },
    sectionHeader('Emails', `${items.length} total`),
    el('div', { class: 'space-y-1.5' },
      items.map(em => el('div', { class: 'list-item-static font-mono text-sm text-slate-800' }, em)),
    ),
  );
}

function renderIdentification() {
  const items = state.data.identification_details || [];
  return el('div', { class: 'space-y-4' },
    sectionHeader('Identification', `${items.length} total`),
    el('div', { class: 'space-y-3' },
      items.map(idn => el('section', { class: 'card' },
        el('div', { class: 'card-body' },
          el('div', { class: 'flex items-center gap-3 mb-2 flex-wrap' },
            el('span', { class: 'pill pill-info' }, idn.identification_type || '-'),
            el('span', { class: 'font-mono text-sm font-semibold' }, idn.id_number || '-'),
          ),
          el('div', { class: 'grid grid-cols-2 gap-2 text-xs text-slate-500' },
            el('div', null, 'Issued: ', el('span', { class: 'text-slate-700' }, idn.issue_date || '-')),
            el('div', null, 'Expires: ', el('span', { class: 'text-slate-700' }, idn.expiry_date || '-')),
          ),
        ),
      )),
    ),
  );
}

const RENDERERS = {
  summary: renderSummary,
  personal: renderPersonal,
  accounts: renderAccounts,
  enquiries: renderEnquiries,
  addresses: renderAddresses,
  contacts: renderContacts,
  emails: renderEmails,
  identification: renderIdentification,
};

// ---------------------------------------------------------------------
// SEARCH RESULTS
// ---------------------------------------------------------------------
function renderSearchResults(hits) {
  const grouped = {};
  for (const h of hits) (grouped[h.section] ||= []).push(h);
  const order = ['accounts', 'enquiries', 'addresses', 'contacts', 'emails', 'identification'];

  if (!hits.length) {
    return el('div', { class: 'space-y-4' },
      sectionHeader('Search', `No matches for "${state.search}"`),
      el('div', { class: 'text-sm text-slate-500' },
        'Try a different keyword. The search scans member names, account numbers, account types, ',
        'enquiry purposes, addresses, phone numbers, emails, and identification numbers.'),
    );
  }

  return el('div', { class: 'space-y-5' },
    sectionHeader('Search Results',
      `${hits.length} match${hits.length !== 1 ? 'es' : ''} for "${state.search}"`),
    order.flatMap(sec => {
      const items = grouped[sec];
      if (!items?.length) return [];
      return [el('section', { class: 'card' },
        el('h3', { class: 'card-title flex items-center justify-between' },
          el('span', null, sec),
          el('span', { class: 'text-slate-500 normal-case tracking-normal text-xs font-normal' },
            `${items.length} hit${items.length !== 1 ? 's' : ''}`),
        ),
        el('div', { class: 'card-body card-body--flush divide-y divide-slate-100' },
          items.map(h => searchHitRow(h)),
        ),
      )];
    }),
  );
}

function searchHitRow(h) {
  return el('button', {
    class: 'block w-full text-left px-4 py-2.5 hover:bg-slate-50 transition-colors',
    onclick: () => jumpToHit(h),
  },
    el('div', { class: 'flex items-center gap-3 text-sm flex-wrap' },
      h.summary.map((s, idx) => el('span', {
        class: idx === 0 ? 'font-medium text-slate-800' : 'text-slate-600',
        html: highlight(String(s), state.search),
      })),
    ),
  );
}

function jumpToHit(h) {
  state.search = '';
  state.searchHits = null;
  $('#search').value = '';
  state.section = h.section;
  state.selected = (h.section === 'accounts') ? { section: h.section, index: h.index } : null;
  render();
}

// ---------------------------------------------------------------------
// top-level render
// ---------------------------------------------------------------------
async function render() {
  const main = $('#content');
  setActiveNav();

  if (!state.data) {
    main.replaceChildren(el('div', { class: 'text-sm text-slate-500' }, 'Loading…'));
    return;
  }

  if (state.search) {
    main.replaceChildren(el('div', { class: 'text-sm text-slate-500' }, 'Searching…'));
    try {
      const hits = await runSearch(state.search);
      state.searchHits = hits;
      // Avoid clobbering if the user kept typing.
      if ($('#search').value.trim() !== state.search) return;
      main.replaceChildren(renderSearchResults(hits));
    } catch (err) {
      main.replaceChildren(el('div', { class: 'text-sm text-red-600' }, String(err)));
    }
    return;
  }

  let view;
  if (state.section === 'accounts' && state.selected?.section === 'accounts') {
    view = renderAccountDetail(state.selected.index);
  } else {
    const fn = RENDERERS[state.section] || RENDERERS.summary;
    view = fn();
  }
  main.replaceChildren(view);
  main.scrollTop = 0;
}

// ---------------------------------------------------------------------
// bootstrap
// ---------------------------------------------------------------------
function wireEvents() {
  $$('#sidebar .nav-btn').forEach(b => {
    b.addEventListener('click', () => {
      state.section = b.dataset.section;
      state.selected = null;
      state.search = '';
      $('#search').value = '';
      render();
    });
  });

  let searchTimer;
  $('#search').addEventListener('input', (e) => {
    clearTimeout(searchTimer);
    const v = e.target.value.trim();
    searchTimer = setTimeout(() => {
      state.search = v;
      render();
    }, 180);
  });

  $('#search').addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      e.target.value = '';
      state.search = '';
      render();
    }
  });

  // Cmd/Ctrl+K focuses search.
  document.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
      e.preventDefault();
      $('#search').focus();
      $('#search').select();
    }
  });

  $('#file-select').addEventListener('change', async (e) => {
    state.file = e.target.value;
    state.selected = null;
    state.search = '';
    $('#search').value = '';
    try {
      await loadData();
      setHeader();
      render();
    } catch (err) {
      $('#content').replaceChildren(el('div', { class: 'text-sm text-red-600' }, String(err)));
    }
  });
}

async function init() {
  wireEvents();
  try {
    await loadFiles();
    if (!state.file) {
      $('#content').replaceChildren(el('div', { class: 'space-y-2' },
        el('div', { class: 'text-base font-medium' }, 'No verified output JSON found.'),
        el('div', { class: 'text-sm text-slate-600' },
          'Run ',
          el('code', { class: 'px-1.5 py-0.5 bg-slate-100 rounded text-xs' }, 'make'),
          ' from the project root to parse ',
          el('code', { class: 'px-1.5 py-0.5 bg-slate-100 rounded text-xs' }, 'src/*.mhtml'),
          ', then refresh.',
        ),
      ));
      return;
    }
    await loadData();
    setHeader();
    render();
  } catch (err) {
    $('#content').replaceChildren(el('div', { class: 'text-sm text-red-600' }, String(err)));
  }
}

init();
