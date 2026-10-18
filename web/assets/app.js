/* ============================================================
   منصة رصد التقسيم العكسي والانعكاس — منطق الواجهة
   ============================================================ */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

const state = {
  rows: [], summary: {}, sources: {}, strategy: null, phases: {},
  selected: null, analysis: null, tf: '1D', osc: 'rsi',
  // اعرض الأسهم التي اجتازت فلترة التقسيم أولاً؛ لا تُخفِ حالات الاقتراب
  // لمجرد أنها لم تحقق الفلاتر الخمسة كاملة بعد.
  filter: 'rs', search: '', polling: null, charts: { price: null, osc: null },
  series: { price: {}, osc: {} }, bars: 0, autoScanStarted: false,
};

/* دلائل عرض المخطط (تتحكم بها الأزرار وأشرطة السحب الجانبية) */
const chartUI = { autoScale: true, showVolume: true, vPos: 500, vZoom: 540 };
let sliderLock = false;

/* ------------------------- أدوات ------------------------- */
const n = (x, d = 2) => (x === null || x === undefined || isNaN(x)) ? '—' : Number(x).toFixed(d);
const pct = (x, d = 1) => (x === null || x === undefined || isNaN(x)) ? '—' : (x >= 0 ? '+' : '') + Number(x).toFixed(d) + '%';
const money = (x, d = 3) => (x === null || x === undefined || isNaN(x)) ? '—' : '$' + Number(x).toFixed(d);
function big(x) {
  if (x === null || x === undefined || isNaN(x)) return '—';
  x = Number(x);
  if (Math.abs(x) >= 1e9) return (x / 1e9).toFixed(2) + 'B';
  if (Math.abs(x) >= 1e6) return (x / 1e6).toFixed(2) + 'M';
  if (Math.abs(x) >= 1e3) return (x / 1e3).toFixed(1) + 'K';
  return x.toFixed(0);
}
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const cls = (x) => (x === null || x === undefined) ? '' : (x >= 0 ? 'pos' : 'neg');
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

const SNAPSHOT = (typeof window !== 'undefined' && window.__SNAPSHOT__) || null;

async function api(path, opts = {}) {
  if (SNAPSHOT) {
    // نسخة ثابتة (Offline Snapshot): كل البيانات مضمّنة داخل الملف نفسه
    const [p, qs] = String(path).split('?');
    const q = new URLSearchParams(qs || '');
    const rows = (SNAPSHOT.results && SNAPSHOT.results.rows) || [];
    if (p === '/api/health') return SNAPSHOT.health || {};
    if (p === '/api/strategy') return SNAPSHOT.strategy || {};
    if (p === '/api/demo-metadata') return SNAPSHOT.demo || { scenarios: [] };
    if (p === '/api/scan/results') return SNAPSHOT.results || { rows: [], summary: {} };
    if (p === '/api/scan/status') return { running: false, progress: rows.length, total: rows.length, percent: 100, results_available: rows.length, mode: 'demo' };
    if (p === '/api/scan/start') return { started: false, snapshot: true };
    if (p === '/api/report/save') return { written: [], folder: 'نسخة ثابتة — التصدير غير متاح' };
    if (p.startsWith('/api/analysis/')) return (SNAPSHOT.analysis || {})[p.split('/').pop()] || {};
    if (p.startsWith('/api/chart/')) {
      const sym = p.split('/').pop(), tf = (q.get('tf') || '1D').toUpperCase();
      return ((SNAPSHOT.charts || {})[sym] || {})[tf] || {};
    }
    return {};
  }
  const r = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...opts });
  if (!r.ok) throw new Error((await r.text()).slice(0, 200));
  return r.headers.get('content-type')?.includes('json') ? r.json() : r.text();
}

function toast(msg, ms = 3600) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.remove('hidden');
  clearTimeout(t._t);
  t._t = setTimeout(() => t.classList.add('hidden'), ms);
}

/* ------------------------- التنقّل (تبويبات + سجل الرجوع) ------------------------- */
function showTab(name) {
  $$('.tab').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
  $$('.tab-panel').forEach(p => p.classList.toggle('active', p.id === 'tab-' + name));
  $('#topbarRight').classList.remove('open');
  if (name === 'analysis' && state.charts.price) {
    setTimeout(() => { resizeCharts(); fitCharts(); }, 60);
  }
}

function route() {
  const hash = decodeURIComponent(location.hash || '');
  let m = /^#\/sym\/([A-Za-z0-9.\-]+)/.exec(hash);
  if (m) { openAnalysis(m[1].toUpperCase()); return; }
  m = /^#\/tab\/(\w+)/.exec(hash);
  showTab(m ? m[1] : 'scanner');
}

function go(hash) { if (location.hash === hash) route(); else location.hash = hash; }

$$('.tab').forEach(btn => btn.onclick = () => go('#/tab/' + btn.dataset.tab));
$('#btnBack').onclick = () => {
  if (history.length > 1) history.back(); else go('#/tab/scanner');
};
window.addEventListener('hashchange', route);

$('#menuBtn').onclick = () => $('#topbarRight').classList.toggle('open');

$$('.chip').forEach(c => c.onclick = () => {
  $$('.chip').forEach(x => x.classList.remove('active'));
  c.classList.add('active');
  state.filter = c.dataset.filter;
  renderTable(); renderCards();
});
$('#searchBox').oninput = e => { state.search = e.target.value.trim().toUpperCase(); renderTable(); renderCards(); };

/* ------------------------- الساعة والحالة ------------------------- */
setInterval(() => {
  $('#clock').textContent = new Date().toLocaleString('ar-EG', {
    weekday: 'short', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  });
}, 1000);

async function loadHealth() {
  try {
    const h = await api('/api/health');
    state.sources = h.data;
    const live = h.data.mode_effective === 'live';
    const b = $('#modeBadge');
    // نحدد النص النهائي بعد تحميل النتائج (قد يظهر synthetic>0)
    b.className = 'badge ' + (live ? 'badge-live' : 'badge-demo');
    if (SNAPSHOT) {
      b.textContent = live ? 'نسخة ثابتة — 🟢 حقيقية' : 'نسخة ثابتة — بيانات تجريبية';
      b.className = 'badge ' + (live ? 'badge-live' : 'badge-demo');
      ['btnScan', 'btnReport', 'btnReportHtml', 'btnReportMd', 'btnJson'].forEach(id => {
        const el = document.getElementById(id);
        if (el) { el.disabled = true; el.title = 'غير متاح في النسخة الثابتة'; }
      });
    } else {
      b.textContent = live ? 'وضع البيانات: حي 🟢' : 'وضع البيانات: تجريبي 🔴';
    }
    if (!SNAPSHOT && h.scan && h.scan.running) {
      $('#progressWrap').classList.remove('hidden');
      pollStatus();
    } else if (h.results_cached > 0) {
      loadResults();
    } else if (!state.autoScanStarted && !SNAPSHOT) {
      state.autoScanStarted = true;            // أول تشغيل: ابدأ المسح تلقائياً
      $('#progressWrap').classList.remove('hidden');
      api('/api/scan/start?limit=80', { method: 'POST' }).catch(() => {});
      pollStatus();
    }
    renderSources();
  } catch (e) { toast('تعذر الاتصال بالمخدم: ' + e.message); }
}

/* ------------------------- المسح ------------------------- */
$('#btnScan').onclick = async () => {
  $('#btnScan').disabled = true;
  try {
    await api('/api/scan/start?limit=80', { method: 'POST' });
    $('#progressWrap').classList.remove('hidden');
    pollStatus();
  } catch (e) { toast('فشل بدء المسح: ' + e.message); $('#btnScan').disabled = false; }
};

function pollStatus() {
  clearInterval(state.polling);
  state.polling = setInterval(async () => {
    let s;
    try { s = await api('/api/scan/status'); } catch { return; }
    $('#progressFill').style.width = (s.percent || 0) + '%';
    $('#progressPct').textContent = (s.percent || 0) + '%';
    $('#progressText').innerHTML = s.running
      ? `<span class="spinner"></span> جارٍ تحليل ${esc(s.current || '')} — ${s.progress}/${s.total}`
      : `اكتمل المسح — ${s.progress}/${s.total} رمز`;
    if (!s.running) {
      clearInterval(state.polling);
      $('#btnScan').disabled = false;
      setTimeout(() => $('#progressWrap').classList.add('hidden'), 2500);
      await loadResults();
      toast('تم إكمال مسح السوق وتحديث قائمة الفرص ✓');
    }
  }, 600);
}

const sourceKeys = ['price_source', 'split_source', 'fundamentals_source', 'news_source'];
function sourceBadge(r) {
  const label = r.synthetic !== false ? '🔴 تجريبي' : sourceKeys.some(k => !r[k]) ? '🟠 ناقص' : '🟢 حقيقي';
  const names = ['الأسعار', 'التقسيم', 'الأساسيات', 'الأخبار'];
  return `<div class="small">${label}</div><div class="muted small">${sourceKeys.map((k,i) => names[i] + ': ' + esc(r[k] || 'غير متاح')).join(' · ')}</div>`;
}
async function loadResults() {
  const data = await api('/api/scan/results');
  state.rows = data.rows || [];
  state.summary = data.summary || {};
  state.sources = data.sources || state.sources;
  const synthetic = state.rows.some(r => r.synthetic !== false);
  const incomplete = state.rows.some(r => sourceKeys.some(k => !r[k]));
  const live = state.sources.mode_effective === 'live';
  $('#modeBadge').textContent = 'وضع البيانات: ' + (live && !synthetic && !incomplete ? '🟢 حقيقية' : live ? '🟠 مختلطة — مصادر ناقصة' : '🔴 تجريبية');
  let warning = document.getElementById('sourceWarning');
  if (!warning) { warning = document.createElement('div'); warning.id = 'sourceWarning'; document.querySelector('header').after(warning); }
  warning.style.cssText = 'background:#3a2a08;color:#ffd87a;padding:12px';
  warning.textContent = synthetic || incomplete ? '⚠ توجد بيانات تجريبية أو مصادر ناقصة. راجع مصادر كل صف قبل الاعتماد عليه.' : '';
  warning.hidden = !synthetic && !incomplete;
  renderCoverage();
  renderKpis(); renderTable(); renderCards(); renderPhases(); renderSources();
}

/* ------------------------- تغطية المسح وحدودها ------------------------- */
/* يفصل بوضوح بين: لم يُفحص · بيانات غير متاحة · لا تقسيم حديث مطابق ·
   تقسيم حديث مرصود لكنه غير مؤهل فنياً · قائمة المتابعة · جاهز فنياً. */
function renderCoverage() {
  const src = state.sources || {};
  const cov = src.scan_coverage || (src.coverage) || {};
  const disc = src.candidate_discovery || {};
  const rows = state.rows || [];
  const collected = cov.candidates_collected ?? disc.candidates_collected ?? 0;
  const checked = cov.candidates_checked ?? src.candidates_checked ?? 0;
  const unchecked = cov.candidates_unchecked ?? Math.max(collected - checked, 0);
  const rowsAfterSplit = rows.filter(r => r.rs_found).length;
  const rsExcluded = rows.filter(r => r.rs_found && !r.eligible && !r.gam_candidate);
  const gam = rows.filter(r => r.gam_candidate);
  const watch = rows.filter(r => r.watchlist && r.eligible);
  const ready = rows.filter(r => r.eligible && r.phase === 6 && r.lifecycle_ready);
  const excluded = src.rejections || {};
  let noData = 0, noSplit = 0;
  Object.values(excluded).forEach(reason => {
    if (String(reason || '').includes('لا تقسيم')) noSplit += 1; else noData += 1;
  });
  const partial = !!(cov.partial || disc.truncated);
  const totalScreener = disc.total_matching_screener ?? cov.total_matching_screener;
  let box = document.getElementById('coverageBar');
  if (!box) {
    box = document.createElement('div');
    box.id = 'coverageBar';
    (document.getElementById('sourceWarning') || document.querySelector('header')).after(box);
  }
  const chip = (label, count, color, note) =>
    `<span style="display:inline-flex;gap:6px;align-items:center;background:#0e1520;border:1px solid #16202f;` +
    `border-radius:10px;padding:5px 9px;font-size:12px"><span class="muted">${label}</span>` +
    `<b class="${color}" style="font-size:15px">${count}</b>${note ? `<span class="muted" style="font-size:10px">${note}</span>` : ''}</span>`;
  box.style.cssText = 'background:#0b111a;border:1px solid #1e2a3d;border-radius:12px;padding:12px;margin:10px 0;font-size:12.5px;color:#e6edf7';
  box.innerHTML =
    `<div style="font-weight:700;margin-bottom:8px">📊 تغطية المسح — ` +
    `مرشحون مجموعون ${collected} · مفحوصون ${checked} · غير مفحوصين ${unchecked} · ` +
    `صفوف بعد فلترة التقسيم ${rowsAfterSplit}` +
    (totalScreener != null ? ` · إجمالي مطابقات screener ${totalScreener}` : '') +
    `</div>` +
    (partial
      ? `<div style="background:#3a2a08;color:#ffd87a;border:1px solid #8a6d1f;border-radius:10px;` +
        `padding:10px 12px;margin-bottom:8px">⚠ التغطية جزئية؛ لا تعني النتائج غياب حالات أخرى في السوق خلال شهرين.</div>`
      : '') +
    `<div style="display:flex;flex-wrap:wrap;gap:7px">` +
    chip('لم يُفحص', unchecked, 'c-amber', 'ليست مستبعدة') +
    chip('بيانات غير متاحة', noData, 'c-red') +
    chip('لا تقسيم حديث مطابق', noSplit, 'muted') +
    chip('تقسيم حديث — مستبعد لسبب آخر', rsExcluded.length, 'c-amber') +
    chip('GAM (غير مؤهل)', gam.length, 'c-purple', 'مسار ثانوي فقط') +
    chip('قائمة المتابعة', watch.length, 'c-amber') +
    chip('جاهز فنياً', ready.length, 'c-green') +
    `</div>` +
    `<div class="muted" style="margin-top:8px;font-size:11px">«لم يُفحص» = مرشحون لم تتحقق منهم بعد (مهلة أو سقف) وليست مستبعدة فنياً؛ ` +
    `«بيانات غير متاحة» = تعذّر تحقق الأسعار أو الملف؛ «لا تقسيم حديث مطابق» = خارج نافذة 60 يوماً أو النسبة المطلوبة؛ ` +
    `«مستبعد لسبب آخر» = حدث حديث أخفق السعر/السيولة/الصعود/النموذج أو سبباً مانعاً آخر؛ ` +
    `«GAM» = رفض حصراً بسبب هبوط أقل من 30% و/أو RSI في المسح الحالي، مع سعر داخل نطاق الفلاتر 1–5$؛ ليس فرصة أو توصية.</div>`;
}

function renderKpis() {
  const s = state.summary;
  $('#kpiScanned').textContent = s.scanned ?? '—';
  $('#kpiRs').textContent = s.with_reverse_split ?? '—';
  $('#kpiGam').textContent = s.gam_candidates ?? 0;
  $('#kpiWatch').textContent = s.watchlist ?? '—';
  $('#kpiReady').textContent = s.ready ?? '—';
  $('#kpiAvg').textContent = s.avg_score_watchlist ?? '—';
  $('#kpiTime').textContent = s.generated_at ? new Date(s.generated_at).toLocaleString('ar-EG', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—';
}

function rowMatchesFilter(r) {
  switch (state.filter) {
    case 'watch': return !!r.watchlist && !!r.eligible;
    case 'eligible': return !!r.eligible;
    case 'gam': return !!r.gam_candidate && !r.eligible && !r.watchlist;
    case 'ready': return !!r.eligible && r.phase === 6 && !!r.lifecycle_ready;
    case 'rs': return !!r.rs_found;
    case 'rejected': return r.rs_found && !r.eligible;
    default: return true;
  }
}

/* خلية «التسلسل»: خطوات دورة الحياة + اسم الخطوة الناقصة صراحةً */
const STEP_SHORT = {
  support_hold: 'ثبات الدعم 3 جلسات', impulse: 'الانطلاقة ≈20%', resistance: 'أقرب مقاومة',
  pullback: 'العودة لاختبار الدعم', sweep: 'سحب السيولة/الاستعادة', reclaim: 'الاستعادة فوق الدعم',
};
function seqCell(r) {
  const checks = r.lifecycle_checks || [];
  if (!checks.length) return `<span class="muted small">${r.rs_found ? 'تسلسل الثبات لم يبدأ بعد' : 'لا تقسيم داخل النافذة'}</span>`;
  const done = checks.filter(c => c.ok).length;
  const dots = checks.map(c => `<i class="dot ${c.ok ? 'ok' : (c.state === 'active' ? 'active' : 'pending')}"></i>`).join('');
  const active = checks.find(c => !c.ok);
  let label, color;
  if (r.gam_candidate) { label = `GAM — غير مؤهل (${done}/6 بنيوياً)`; color = 'c-purple'; }
  else if (r.lifecycle_ready && r.eligible) { label = 'جاهز فنياً (6/6)'; color = 'c-green'; }
  else if (r.lifecycle_failed) { label = 'فشل — كسر الدعم دون استعادة'; color = 'c-red'; }
  else { label = `${done}/6 — ناقص: ${STEP_SHORT[active?.key] || (active?.ar || '')}`; color = 'c-amber'; }
  return `<span title="${esc(r.lifecycle_stage || '')}" style="cursor:help;white-space:nowrap">
    <span class="seq-icons">${dots}</span>
    <div class="small ${color}">${esc(label)}</div></span>`;
}

function renderTable() {
  const body = $('#scanBody');
  let rows = state.rows.filter(rowMatchesFilter).sort((a, b) => b.score - a.score || a.symbol.localeCompare(b.symbol));
  if (state.search) {
    rows = rows.filter(r => r.symbol.includes(state.search) || (r.name || '').toUpperCase().includes(state.search));
  }
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="18" class="empty">لا توجد نتائج مطابقة — جرّب مسح السوق أو تغيير المُرشّح.</td></tr>`;
    $('#scanFooter').textContent = '';
    return;
  }
  body.innerHTML = rows.map((r, i) => {
    const ph = r.phase_info || {};
    const lv = r.levels || {};
    const pat = (r.patterns_daily && r.patterns_daily[0]) ? r.patterns_daily[0].name_ar : '—';
    const scoreColor = r.score >= 75 ? '#3fb950' : r.score >= 60 ? '#d29922' : '#8b96a5';
    const rsTxt = r.rs_found ? `${r.rs_ratio_str}<br><span class="muted small">${r.rs_date || ''} · ${r.rs_age_days ?? '—'}ي</span>` : '<span class="muted">—</span>';
    const gamBadge = r.gam_candidate ? '<span class="gam-pill" title="مسار ثانوي؛ غير مؤهل وليس توصية">GAM · غير مؤهل</span>' : '';
    const rowClass = `${r.eligible ? '' : 'phase-rejected'}${r.gam_candidate ? ' gam-row' : ''}`.trim();
    return `<tr data-sym="${r.symbol}" class="${rowClass}">
      <td class="muted">${i + 1}</td>
      <td class="sym-cell">${esc(r.symbol)}${gamBadge}${sourceBadge(r)}<div class="muted small" style="max-width:170px;overflow:hidden;text-overflow:ellipsis">${esc((r.name || '').slice(0, 26))}</div></td>
      <td><span class="phase-pill" style="background:${(ph.color || '#333')}22;color:${ph.color || '#ccc'};border:1px solid ${(ph.color || '#333')}66">${esc((ph.ar || '—').replace('المرحلة ', 'م'))}</span>${gamBadge}</td>
      <td class="score-cell" style="color:${scoreColor}">${n(r.score, 1)}<span class="muted small"> ${esc(r.grade)}</span></td>
      <td>${money(r.last_close)}<br><span class="${cls(r.change_pct)} small">${pct(r.change_pct)}</span></td>
      <td>${rsTxt}</td>
      <td class="${cls(r.spike_pct)}">${pct(r.spike_pct)}</td>
      <td class="${cls(r.decline_pct)}">${pct(r.decline_pct)}</td>
      <td>${r.base_sessions ?? '—'}${r.base_broken ? ' <span class="c-red">⚠</span>' : ''}</td>
      <td>${n(r.rsi_trough, 0)} <span class="muted">→</span> <b>${n(r.rsi_now, 0)}</b>${r.rsi_recovered ? ' <span class="c-green">↑</span>' : ''}</td>
      <td>${big(r.float_shares)}</td>
      <td>${r.short_shares != null ? Number(r.short_shares).toLocaleString('en-US') : '—'}</td>
      <td>${n(r.rvol, 2)}x</td>
      <td>${lv.trigger ? money(lv.trigger) : '—'}</td>
      <td>${lv.rr_t2 ? n(lv.rr_t2, 2) + ' : 1' : '—'}</td>
      <td>${seqCell(r)}</td>
      <td class="muted" style="max-width:190px;white-space:normal">${esc(pat)}</td>
      <td><button class="btn btn-mini" data-open="${r.symbol}">تحليل ›</button></td>
    </tr>`;
  }).join('');
  $$('#scanBody tr[data-sym]').forEach(tr => tr.onclick = () => go('#/sym/' + tr.dataset.sym));
  $('#scanFooter').textContent = `عرض ${rows.length} من ${state.rows.length} رمز — ` +
    `فرص مؤهلة: ${state.summary.eligible ?? 0} · قائمة المتابعة: ${state.summary.watchlist ?? 0} · ` +
    `GAM غير مؤهل: ${state.summary.gam_candidates ?? 0}`;
  if (state.selected) {
    const tr = $(`#scanBody tr[data-sym="${state.selected}"]`);
    if (tr) tr.classList.add('selected');
  }
}



/* رسم مصغّر (Sparkline) لسلسلة الإغلاقات */
function sparkline(values, w = 132, h = 34) {
  const v = (values || []).filter(x => x !== null && x !== undefined);
  if (v.length < 3) return '';
  const min = Math.min(...v), max = Math.max(...v), span = (max - min) || 1;
  const step = w / (v.length - 1);
  const pts = v.map((x, i) => `${(i * step).toFixed(1)},${(h - ((x - min) / span) * (h - 4) - 2).toFixed(1)}`).join(' ');
  const up = v[v.length - 1] >= v[0];
  const col = up ? '#3fb950' : '#f85149';
  const area = `${pts} ${w},${h} 0,${h}`;
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" preserveAspectRatio="none">
    <polygon points="${area}" fill="${col}22"/>
    <polyline points="${pts}" fill="none" stroke="${col}" stroke-width="1.6" stroke-linejoin="round"/>
  </svg>`;
}

/* ------------------------- عرض الجوال: بطاقات الفرص ------------------------- */
function renderCards() {
  const box = $('#scanCards');
  if (!box) return;
  let rows = state.rows.filter(rowMatchesFilter).sort((a, b) => b.score - a.score || a.symbol.localeCompare(b.symbol));
  if (state.search) rows = rows.filter(r => r.symbol.includes(state.search) || (r.name || '').toUpperCase().includes(state.search));
  if (!rows.length) { box.innerHTML = '<div class="empty">لا توجد نتائج مطابقة.</div>'; return; }
  box.innerHTML = rows.map(r => {
    const ph = r.phase_info || {}, lv = r.levels || {};
    const checks = r.lifecycle_checks || [];
    const done = checks.filter(c => c.ok).length;
    const active = checks.find(c => !c.ok);
    const stage = r.gam_candidate ? `GAM — غير مؤهل (${done}/6 بنيوياً)`
      : r.lifecycle_ready && r.eligible ? 'جاهز فنياً — التسلسل مكتمل'
      : r.lifecycle_failed ? 'فشل التسلسل — كسر الدعم دون استعادة'
      : checks.length ? `ناقص: ${STEP_SHORT[active?.key] || ''} (${done}/6)` : '—';
    const gamBadge = r.gam_candidate ? '<span class="gam-pill">GAM · غير مؤهل</span>' : '';
    const gamReason = r.gam_candidate
      ? `<div class="gam-reason"><b>مسار ثانوي فقط:</b> ${(r.gam_reasons || []).map(esc).join(' · ')}<br>ليس فرصة مؤهلة ولا توصية.</div>` : '';
    return `<div class="mcard ${r.eligible ? '' : 'muted-card'}${r.gam_candidate ? ' gam-card' : ''}" data-sym="${r.symbol}">
      <div class="mcard-head">
        <div><b class="sym-cell">${esc(r.symbol)}</b>${gamBadge}${sourceBadge(r)}
          <div class="muted small">${esc((r.name || '').slice(0, 30))}</div></div>
        <div class="mcard-price">${money(r.last_close)}
          <div class="${cls(r.change_pct)} small">${pct(r.change_pct)}</div></div>
        <span class="phase-pill" style="background:${(ph.color || '#333')}22;color:${ph.color || '#ccc'};border:1px solid ${(ph.color || '#333')}66">${esc((ph.ar || '—').replace('المرحلة ', 'م'))}</span>
        <span class="score-pill">${n(r.score, 1)}${r.grade ? ' ' + esc(r.grade) : ''}</span>
      </div>
      <div class="mcard-seq">${seqCell(r)}</div>
      ${gamReason}
      <div class="mcard-spark">${sparkline(r.spark)}<span class="muted small">آخر 40 جلسة</span></div>
      <div class="mcard-grid">
        <div><span>التقسيم</span><b>${r.rs_found ? esc(r.rs_ratio_str) : '—'}</b></div>
        <div><span>صعود/هبوط</span><b>${pct(r.spike_pct)} / ${pct(r.decline_pct)}</b></div>
        <div><span>الثبات</span><b>${r.base_sessions ?? '—'} جلسة</b></div>
        <div><span>RSI</span><b>${n(r.rsi_now, 0)} (قاع ${n(r.rsi_trough, 0)})</b></div>
        <div><span>الفلوت</span><b>${big(r.float_shares)}</b></div>
        <div><span>الأسهم المكشوفة</span><b>${r.short_shares != null ? Number(r.short_shares).toLocaleString('en-US') : '—'}</b></div>
        <div><span>الاختراق</span><b>${lv.trigger ? money(lv.trigger) : '—'}</b></div>
        <div><span>مخاطرة/عائد</span><b>${lv.rr_t2 ? n(lv.rr_t2, 2) + ':1' : '—'}</b></div>
      </div>
      <button class="btn btn-mini">فتح بطاقة التحليل ›</button>
    </div>`;
  }).join('');
  $$('#scanCards .mcard').forEach(el => el.onclick = () => go('#/sym/' + el.dataset.sym));
}

/* ------------------------- فتح تحليل رمز ------------------------- */
let loadingSym = null;
async function openAnalysis(sym) {
  if (loadingSym === sym) return;
  loadingSym = sym;
  state.selected = sym;
  $$('#scanBody tr').forEach(tr => tr.classList.toggle('selected', tr.dataset.sym === sym));
  showTab('analysis');
  $('#analysisEmpty').classList.add('hidden');
  $('#analysisBody').classList.remove('hidden');
  $('#anSymbol').textContent = sym;
  document.title = 'تحليل ' + sym + ' | منصة رصد التقسيم العكسي';
  try {
    const a = await api('/api/analysis/' + sym);
    state.analysis = a;
    renderAnalysis(a);
    await loadChart(sym, state.tf);
  } catch (e) { toast('تعذر تحميل التحليل: ' + e.message); }
  finally { loadingSym = null; }
}

/* ------------------------- شريط دورة الحياة ------------------------- */
function renderStepper(a) {
  const checks = a.lifecycle_checks || [];
  const box = $('#anStepper');
  if (!checks.length) {
    box.innerHTML = `<p class="muted">${a.rs_found ? 'تقسيم عكسي مرصود؛ تسلسل الثبات لم يبدأ بعد.' : 'لا تقسيم عكسي داخل النافذة.'}</p>`;
  } else {
    box.innerHTML = checks.map((c, i) => `
      <div class="step ${c.state}">
        <div class="st-num">${c.ok ? '✓' : i + 1}</div>
        <div class="st-title">${esc(c.ar)}</div>
        <div class="st-std">${esc(c.std)}</div>
        <div class="st-det">${esc(c.detail)}</div>
      </div>`).join('');
  }
  const st = $('#seqStatus');
  const okAll = a.lifecycle_ready && a.eligible, failed = a.lifecycle_failed;
  st.className = 'seq-status ' + (failed ? 'fail' : okAll ? 'ok' : 'active');
  const statusText = a.gam_candidate
    ? `GAM — غير مؤهل؛ الحالة البنيوية: ${a.lifecycle_stage || '—'}`
    : (a.lifecycle_stage || '—');
  st.innerHTML = `<i class="dot ${failed ? 'fail' : okAll ? 'ok' : 'active'}"></i> ${esc(statusText)}`;
}

/* ------------------------- عرض التحليل ------------------------- */
function renderAnalysis(a) {
  const ph = a.phase_info || {};
  $('#anName').textContent = a.name || '';
  $('#anExchange').textContent = a.exchange || '—';
  $('#anSector').textContent = a.sector || a.industry || '—';
  $('#anPrice').textContent = money(a.last_close);
  const ch = $('#anChange');
  ch.textContent = pct(a.change_pct, 2);
  ch.className = 'chg ' + cls(a.change_pct);
  const badge = $('#anPhaseBadge');
  badge.textContent = a.gam_candidate ? 'GAM — غير مؤهل (مسار ثانوي)' : (ph.ar || '—');
  const phaseColor = a.gam_candidate ? '#a371f7' : (ph.color || '#333');
  badge.style.background = phaseColor + '22';
  badge.style.color = a.gam_candidate ? '#c9adff' : (ph.color || '#ccc');
  badge.style.borderColor = phaseColor + '66';
  badge.className = 'badge';
  $('#anPhaseDesc').textContent = a.gam_candidate
    ? 'مرشح قريب فقط؛ لا يخفّف الأهلية ولا يدخل قائمة المتابعة، وليس توصية.'
    : (ph.desc || '');
  const g = $('#anGauge');
  const deg = Math.max(0, Math.min(360, (a.score || 0) * 3.6));
  const col = a.score >= 85 ? '#3fb950' : a.score >= 75 ? '#7ee787' : a.score >= 62 ? '#d29942' : '#8b96a5';
  g.style.background = `conic-gradient(${col} 0deg, ${col} ${deg}deg, #1d2530 ${deg}deg 360deg)`;
  $('#anScore').textContent = n(a.score, 0);
  $('#anGrade').textContent = a.grade || '—';

  const alerts = [];
  if (a.risk_flags?.length) alerts.push(['bad', 'خطر', a.risk_flags.map(esc).join('<br>')]);
  if (a.lifecycle_failed) alerts.push(['bad', 'فشل', 'فشل تسلسل الانعكاس: كسر الدعم الرئيسي بعد الانطلاقة دون استعادة — يُلغى السيناريو حتى يتكوّن قاع جديد.']);
  if (a.rejections?.length) alerts.push(['warn', 'استبعاد', '<b>أسباب الاستبعاد:</b><br>' + a.rejections.map(esc).join('<br>')]);
  if (a.notes?.length) alerts.push(['info', 'ملاحظة', a.notes.map(esc).join('<br>')]);
  if (a.gam_candidate) alerts.unshift(['gam', 'GAM — غير مؤهل',
    '<b>مسار ثانوي فقط:</b> ' + (a.gam_reasons || []).map(esc).join(' · ') +
    '<br>ليس فرصة مؤهلة ولا في قائمة المتابعة وليس توصية. رفض RSI يخص بيانات المسح الحالي؛ لا حفظ تاريخي.']);
  if (!a.rejections?.length && a.watchlist && a.eligible) alerts.unshift(['good', 'متابعة', 'السهم مستوفٍ لشروط الاستراتيجية ومرصود في قائمة المتابعة.']);
  $('#anAlerts').innerHTML = alerts.map(([k, tag, t]) =>
    `<div class="alert alert-${k}"><i class="dot ${k === 'good' ? 'ok' : k === 'bad' ? 'fail' : k === 'warn' || k === 'gam' ? 'active' : 'info'}"></i>
      <div><b class="alert-tag">${tag}</b> ${t}</div></div>`).join('');

  renderStepper(a);
  renderLevels(a); renderChecklist(a); renderBreakdown(a); renderFundamentals(a);
  renderMetrics(a); renderPatterns(a); renderNews(a);

  $('#anWhy').innerHTML = (a.why_ar || []).map(w => `<li>${esc(w)}</li>`).join('');
  $('#anAction').innerHTML = `<b>${a.gam_candidate ? 'حالة GAM:' : 'خطة العمل:'}</b> ${esc(a.action_ar || '')}`;
  $('#anConfirmations').innerHTML = (a.confirmations || []).length
    ? a.confirmations.map(c => `<span class="c">✓ ${esc(c)}</span>`).join('')
    : '<span class="muted small">لا توجد تأكيدات فنية بعد — السهم ما زال في طور التكوين.</span>';
}

function renderLevels(a) {
  const lv = a.levels || {}, t = lv.targets || {};
  const heading = $('#anLevels')?.closest('.card')?.querySelector('h3');
  if (heading) heading.textContent = a.gam_candidate
    ? 'مستويات فنية مرجعية — ليست خطة تداول'
    : 'خطة التداول والمستويات';
  const box = (cls2, label, val, extra = '') =>
    `<div class="lvl lvl-${cls2}"><span>${label}</span><b>${val}</b>${extra ? `<div class="muted small">${extra}</div>` : ''}</div>`;
  const gamNotice = a.gam_candidate
    ? '<div class="gam-reason"><b>GAM غير مؤهل:</b> هذه مستويات وصفية آلية، وليست توصية أو خطة دخول.</div>' : '';
  $('#anLevels').innerHTML = gamNotice + [
    box('trigger', a.gam_candidate ? 'مستوى الاختراق المرجعي' : 'نقطة الاختراق (Trigger)', money(lv.trigger), lv.entry_zone ? `${a.gam_candidate ? 'نطاق مرجعي' : 'منطقة الدخول'} ${money(lv.entry_zone[0])} – ${money(lv.trigger)}` : ''),
    box('stop', a.gam_candidate ? 'مستوى الإبطال المرجعي' : 'وقف الخسارة', money(lv.stop),
        a.gam_candidate ? 'للوصف الفني فقط' : (lv.stop_pct != null ? `${n(lv.stop_pct, 1)}% من الدخول` : '')),
    box('stop', a.gam_candidate ? 'الإبطال الهيكلي' : 'وقف إلغاء السيناريو', money(lv.stop_structural),
        a.pullback_type === 'liquidity_sweep' && a.sweep_low ? `تحت قاع السحب ${money(a.sweep_low)}` : 'كسر الدعم الرئيسي = إلغاء'),
    box('target', a.gam_candidate ? 'ارتداد مرجعي 38.2%' : 'الهدف 1 (38.2%)', money(t.t1_382),
        !a.gam_candidate && lv.rr_t1 ? `مخاطرة/عائد ${n(lv.rr_t1, 2)}:1` : ''),
    box('target', a.gam_candidate ? 'ارتداد مرجعي 61.8%' : 'الهدف 2 (61.8%)', money(t.t2_618),
        !a.gam_candidate && lv.rr_t2 ? `مخاطرة/عائد ${n(lv.rr_t2, 2)}:1` : ''),
    box('target', a.gam_candidate ? 'القمة السابقة المرجعية' : 'الهدف 3 (القمة)', money(t.t3_full), 'استعادة كامل الموجة'),
  ].join('') + (a.gam_candidate ? '' : `<div class="lvl" style="grid-column:1/-1"><span>إدارة حجم المركز</span><b class="small" style="font-weight:400">${esc(lv.position_note_ar || '')}</b></div>`);
}

/* فلاتر الأهلية الخمسة فقط — تُعرض أولاً وبشكل صريح قبل أي إشارة مساعدة. */
function coreFilterRows(a) {
  if (Array.isArray(a.core_filters) && a.core_filters.length === 5) return a.core_filters;
  const p = state.strategy?.params || {};
  const minP = p.min_price ?? 1, maxP = p.max_price ?? 5;
  const priceOk = a.last_close != null && a.last_close >= minP && a.last_close <= maxP;
  return [
    { key: 'price', label_ar: 'السعر ضمن نطاق البيني',
      rule_ar: `${minP}$ ≤ آخر إغلاق ≤ ${maxP}$ (شامل)`,
      display: a.last_close != null ? `${money(a.last_close)}` : 'غير متاح', ok: priceOk,
      reason: priceOk ? '' : 'السعر خارج نطاق الفلتر' },
    { key: 'reverse_split', label_ar: 'تقسيم عكسي منفّذ حديثاً',
      rule_ar: `خلال ≤ ${p.rs_lookback_days || 60} يوماً`,
      display: a.rs_found ? `${a.rs_ratio_str} · ${a.rs_date} · عمر ${a.rs_age_days} يوم` : 'لا يوجد حدث',
      ok: !!a.rs_found, reason: a.rs_found ? '' : 'لا يوجد تقسيم عكسي داخل النافذة' },
    { key: 'decline', label_ar: 'هبوط كافٍ من قمة ما بعد التقسيم',
      rule_ar: `≥ ${p.min_drop_pct || 30}%`,
      display: a.decline_pct != null ? pct(a.decline_pct) : 'غير متاح',
      ok: a.decline_pct != null && Math.abs(a.decline_pct) >= (p.min_drop_pct || 30),
      reason: a.decline_pct != null && Math.abs(a.decline_pct) < (p.min_drop_pct || 30) ? 'الهبوط أقل من الحد' : '' },
    { key: 'rsi', label_ar: 'تشبع بيعي أثناء تكوّن القاع',
      rule_ar: `RSI < ${p.rsi_oversold || 30}`,
      display: a.rsi_trough != null ? `أدنى RSI ${n(a.rsi_trough, 1)}` : 'غير متاح',
      ok: a.rsi_trough != null && a.rsi_trough < (p.rsi_oversold || 30),
      reason: a.rsi_trough != null && a.rsi_trough >= (p.rsi_oversold || 30) ? 'RSI لم ينزل تحت الحد' : '' },
    { key: 'short_shares', label_ar: 'انخفاض الأسهم المكشوفة',
      rule_ar: `Short Shares < ${Number(p.max_short_shares || 20000).toLocaleString('en-US')} (حصرياً)`,
      display: a.short_shares != null ? `${Number(a.short_shares).toLocaleString('en-US')} سهم` : 'غير متاح',
      ok: a.short_shares != null && Number(a.short_shares) >= 0 && Number(a.short_shares) < Number(p.max_short_shares || 20000),
      reason: a.short_shares == null ? 'بيانات المراكز المكشوفة غير متاحة' : 'عدد الأسهم المكشوفة عند الحد أو أعلى' },
  ];
}

function renderChecklist(a) {
  const p = state.strategy?.params || {};
  const core = coreFilterRows(a);
  const coreDone = core.filter(f => f.ok).length;
  const coreAll = coreDone === core.length;
  const coreHead = `
    <div class="seq-head">
      <b>فلاتر الأهلية الإلزامية (${coreDone}/5) — وحدها تحدد الظهور في الجدول</b>
      <span class="${coreAll ? 'c-green' : 'c-red'}">${coreAll ? 'مؤهل' : 'غير مؤهل'}</span>
    </div>`;
  const coreHtml = core.map(f => `
    <div class="check ${f.ok ? 'ok' : 'fail'}">
      <span class="icon"><i class="dot ${f.ok ? 'ok' : 'fail'}"></i></span>
      <div><b>${esc(f.label_ar)}</b> <span class="muted small">(${esc(f.rule_ar)})</span><br>
      <span class="muted small">${esc(f.display || '—')}</span>
      ${!f.ok && f.reason ? `<br><span class="c-red small">${esc(f.reason)}</span>` : ''}
      </div>
    </div>`).join('');

  /* إشارات مساعدة: تُحسب في النقاط فقط ولا تضيف فلتر أهلية سادساً. */
  const optional = [
    ['صعود ما بعد التقسيم (وصفي)', `≥ ${p.min_spike_pct || 5}%`, a.spike_pct != null ? `${pct(a.spike_pct)} (${money(a.rs_open)} → ${money(a.spike_high)})` : '—', a.spike_pct != null && a.spike_pct >= (p.min_spike_pct || 5)],
    ['ثبات على القاع (وصفي)', `≥ ${p.min_base_sessions || 3} جلسات`, `${a.base_sessions} جلسة على ${money(a.bottom_price)}` + (a.base_range_pct != null ? ` (نطاق ${n(a.base_range_pct, 1)}%)` : ''), (a.base_sessions || 0) >= (p.min_base_sessions || 3)],
    ['جفاف الحجم داخل القاعدة', `< ${Math.round((p.max_volume_dryup_ratio || .9) * 100)}%`, a.volume_dryup != null ? `${Math.round(a.volume_dryup * 100)}% من متوسط ما قبل القاع` : '—', a.volume_dryup != null && a.volume_dryup < (p.max_volume_dryup_ratio || .9)],
    ['تحسّن RSI بعد التشبع', '≥ 35 أو +5', `القاع ${n(a.rsi_trough, 1)} → الحالي ${n(a.rsi_now, 1)}`, !!a.rsi_recovered],
    ['تأكيدات انعكاس مساعدة', '≥ 3 تأكيدات', `${(a.confirmations || []).length} تأكيد: ${(a.confirmations || []).join('، ') || '—'}`, (a.confirmations || []).length >= 3],
    ['الدعم صامد (تنبيه مخاطر)', '—', a.base_broken ? 'تم كسر الدعم الرئيسي ⚠️' : 'لم يُكسر', !a.base_broken],
  ];
  const optionalHtml = `
    <div class="check-head">إشارات فنية مساعدة — للنقاط والتوصيف فقط، ولا تستبعد أي سهم</div>` +
    optional.map(([t, std, val, ok]) => `
      <div class="check ${ok ? 'ok' : 'pending'}">
        <span class="icon"><i class="dot ${ok ? 'ok' : 'pending'}"></i></span>
        <div><b>${esc(t)}</b> <span class="muted small">(${esc(std)})</span><br>
        <span class="muted small">${esc(val)}</span></div>
      </div>`).join('');

  const seq = (a.lifecycle_checks || []).map(c => [c.ar, c.std, c.detail, c.ok, c.state]);
  const doneN = seq.filter(c => c[3]).length;
  const seqHead = seq.length ? `
    <div class="check-head">دورة الحياة الفنية — توصيف فقط، ولا تحدد الأهلية (${doneN}/${seq.length})</div>
    <div class="seq-head">
      <b>حالة التسلسل</b>
      <span class="${a.gam_candidate ? 'c-purple' : a.lifecycle_ready && a.eligible ? 'c-green' : a.lifecycle_failed ? 'c-red' : 'c-amber'}">${esc(a.gam_candidate ? 'GAM غير مؤهل — ' + (a.lifecycle_stage || '') : (a.lifecycle_stage || ''))}</span>
    </div>` : '';
  const seqHtml = seq.map(([t, std, val, ok, st]) => `
    <div class="check ${ok ? 'ok' : (st === 'active' ? 'active' : 'pending')}">
      <span class="icon"><i class="dot ${ok ? 'ok' : (st === 'active' ? 'active' : 'pending')}"></i></span>
      <div><b>${esc(t)}</b> <span class="muted small">(${esc(std)})</span><br>
      <span class="muted small">${esc(val)}</span></div>
    </div>`).join('');

  $('#anChecklist').innerHTML = coreHead + coreHtml + optionalHtml + seqHead + seqHtml;
}

function renderBreakdown(a) {
  const rows = a.breakdown || [];
  $('#anBreakdown').innerHTML = rows.map(b => `
    <div class="bd">
      <div class="bd-head"><span>${esc(b.ar)} <span class="muted small">(وزن ${b.weight})</span></span>
        <span><b>${n(b.score, 0)}</b> <span class="muted small">+${n(b.points, 1)}</span></span></div>
      <div class="bd-bar"><div class="bd-fill" style="width:${Math.max(2, b.score)}%"></div></div>
      <small>${esc(b.detail || '')}</small>
    </div>`).join('');
}

function renderFundamentals(a) {
  const rows = [
    ['اسم الشركة', a.name || '—'], ['البورصة', a.exchange || '—'], ['القطاع', a.sector || '—'],
    ['عدد الأسهم القائمة', big(a.shares_out)], ['الفلوت (الأسهم الحرة)', big(a.float_shares)],
    ['القيمة السوقية', a.market_cap ? '$' + big(a.market_cap) : '—'],
    ['متوسط الحجم اليومي', big(a.avg_volume)], ['متوسط قيمة التداول', a.dollar_volume ? '$' + big(a.dollar_volume) : '—'],
    ['نسبة الملكية الداخلية', a.insider_pct != null ? n(a.insider_pct, 1) + '%' : '—'],
    ['نسبة الملكية المؤسسية', a.institutional_pct != null ? n(a.institutional_pct, 1) + '%' : '—'],
  ];
  const shorts = [
    ['البيع على المكشوف من الفلوت', a.short_pct_float != null ? n(a.short_pct_float, 2) + '%' : '—'],
    ['أسهم مبيعة على المكشوف', big(a.short_shares)],
    ['أيام التغطية', n(a.days_to_cover, 2)],
    ['نسبة حجم البيع المكشوف اليومي', a.short_volume_ratio != null ? n(a.short_volume_ratio * 100, 1) + '%' : '—'],
    ['تغيّر المراكز عن التقرير السابق', a.short_change_pct != null ? pct(a.short_change_pct) : '—'],
    ['مصدر بيانات البيع المكشوف', a.short_source || '—'],
    ['تاريخ تقرير الأسهم المكشوفة', a.short_as_of || 'غير متاح'],
  ];
  $('#anFundamentals').innerHTML =
    rows.concat(shorts).map(([k, v]) => `<div class="row"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join('');
}

function renderMetrics(a) {
  const h4 = a.h4 || {};
  const trendAr = { up: 'صاعد', down: 'هابط', neutral: 'عرضي' }[h4.trend] || '—';
  const rows = [
    ['سعر الافتتاح يوم التقسيم', money(a.rs_open)],
    ['قمة ما بعد التقسيم', money(a.spike_high) + (a.spike_date ? ` (${a.spike_date})` : '')],
    ['الدعم الرئيسي (القاع)', money(a.support_level || a.bottom_price) + (a.bottom_date ? ` (${a.bottom_date})` : '')],
    ['نطاق القاعدة', a.base_range_pct != null ? n(a.base_range_pct, 1) + '%' : '—'],
    ['جلسات الثبات', a.base_sessions ?? '—'],
    ['جفاف الحجم', a.volume_dryup != null ? Math.round(a.volume_dryup * 100) + '%' : '—'],
    ['الانطلاقة من إغلاق الثبات', a.impulse_pct != null ? `${pct(a.impulse_pct)} (من ${money(a.base_close)} إلى ${money(a.impulse_high)} في ${a.impulse_date || '—'})` : '—'],
    ['أقرب مقاومة مختبرة', a.resistance_level != null ? `${money(a.resistance_level)} — توقف السعر عندها بفارق ${n(a.resistance_stall_pct, 1)}%` : '—'],
    ['العودة لاختبار الدعم', a.pullback_type === 'liquidity_sweep'
      ? `سحب سيولة إلى ${money(a.sweep_low)} (${n(a.sweep_depth_pct, 1)}% تحت الدعم) في ${a.pullback_date || '—'}`
      : a.pullback_type === 'support_retest' ? `اختبار ناجح عند ${money(a.pullback_low)} في ${a.pullback_date || '—'}`
      : a.pullback_type === 'breakdown' ? 'كسر الدعم دون استعادة (فشل)'
      : a.pullback_type ? 'موجة عائدة لم تصل لمنطقة الدعم بعد' : '—'],
    ['الاستعادة فوق الدعم', a.reclaim_sessions ? `${a.reclaim_sessions} إغلاق فوق ${money(a.reclaim_level)} (آخرها ${a.reclaim_date || '—'})` : '—'],
    ['حالة التسلسل', (a.gam_candidate ? 'GAM غير مؤهل — ' : a.lifecycle_ready && a.eligible ? 'جاهز فنياً — ' : a.lifecycle_failed ? 'فشل التسلسل — ' : 'قيد التكوين — ') + (a.lifecycle_stage || '')],
    ['تصنيف GAM', a.gam_candidate ? `نعم — غير مؤهل: ${(a.gam_reasons || []).join(' | ')}` : 'لا'],
    ['RSI (يومي)', `${n(a.rsi_now, 1)} (أدنى ${n(a.rsi_trough, 1)})`],
    ['MACD Hist', n(a.macd_hist, 4) + (a.macd_turning ? ' ↑' : '')],
    ['EMA9 / EMA20', money(a.levels?.ema9) + ' / ' + money(a.levels?.ema20)],
    ['إغلاقات فوق EMA9', (a.above_ema9_sessions ?? 0) + ' جلسة'],
    ['حجم نسبي RVOL', n(a.rvol, 2) + 'x'],
    ['ATR%', n(a.atr_pct, 2) + '%'],
    ['القناة الهابطة', (a.channel_slope_pct != null ? n(a.channel_slope_pct, 2) + '%/شمعة' : '—') + (a.channel_broken ? ' · مخترقة ✅' : '')],
    ['بنية القيعان', (a.higher_lows ?? 0) + ' قاع صاعد'],
    ['فريم 4 ساعات', h4.available ? `${trendAr} · RSI ${n(h4.rsi, 1)} · RVOL ${n(h4.rvol, 2)}x` : '—'],
    ['توافق الفريمين', h4.aligned ? 'متوافق ✅' : 'غير متوافق ⏳'],
    ['مصدر البيانات', a.synthetic ? '🔴 تجريبي' : 'حقيقي — راجع المصادر'],
    ...sourceKeys.map(k => [k, a[k] || 'غير متاح']),
  ];
  $('#anMetrics').innerHTML = rows.map(([k, v]) => `<div class="row"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join('');
}

function renderPatterns(a) {
  const all = [...(a.patterns_daily || []), ...(a.patterns_4h || [])];
  if (!all.length) { $('#anPatterns').innerHTML = '<p class="muted">لا توجد نماذج فنية مؤكدة حالياً.</p>'; return; }
  $('#anPatterns').innerHTML = all.map(p => `
    <div class="pattern">
      <div class="pattern-head">
        <b>${esc(p.name_ar)}</b>
        <span class="conf">${esc(p.tf)} · ثقة ${Math.round((p.confidence || 0) * 100)}% ·
          ${p.status === 'confirmed' ? '<span class="c-green">مؤكد</span>' : '<span class="c-amber">قيد التشكّل</span>'}</span>
      </div>
      <p>${esc(p.description_ar)}</p>
      <div class="conf-bar"><div class="conf-fill" style="width:${Math.round((p.confidence || 0) * 100)}%"></div></div>
    </div>`).join('');
}

function renderNews(a) {
  const cats = a.catalysts || [];
  const dil = (a.dilution || []).map(d => `
    <div class="news-item ${d.severity === 'high' ? 'neg' : 'neu'}">
      <i class="dot ${d.severity === 'high' ? 'fail' : 'pending'}" style="margin-top:6px"></i>
      <div><div class="meta">تخفيف · ${esc(d.date)} · خطورة ${esc(d.severity)} · ${esc(d.source)}</div>
      <div class="title">${esc(d.title)}</div></div>
    </div>`).join('');
  if (!cats.length && !dil) { $('#anNews').innerHTML = '<p class="muted">لا توجد أخبار أو محفزات مرصودة لهذا الرمز.</p>'; return; }
  const icon = { positive: '🟢', negative: '🔴', neutral: '⚪' };
  const kindAr = { offering: 'طرح أسهم', earnings: 'أرباح', compliance: 'امتثال', clinical: 'نتائج/تجارب',
    contract: 'عقود', other: 'أخرى', split: 'تقسيم', delay: 'تأخير إفصاح' };
  const today = new Date().toISOString().slice(0, 10);
  $('#anNews').innerHTML = dil + cats.map(c => `
    <div class="news-item ${c.sentiment === 'positive' ? 'pos' : c.sentiment === 'negative' ? 'neg' : 'neu'}">
      <i class="dot ${c.sentiment === 'positive' ? 'ok' : c.sentiment === 'negative' ? 'fail' : 'pending'}" style="margin-top:6px"></i>
      <div>
        <div class="meta">${esc(c.date)} · ${esc(kindAr[c.type] || c.type)} · أثر ${esc(c.impact)}
          ${c.date > today ? '· <b class="c-blue">محفّز قادم</b>' : ''} · ${esc(c.source || '')}</div>
        <div class="title">${c.url ? `<a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.title)}</a>` : esc(c.title)}</div>
      </div>
    </div>`).join('');
}

/* ------------------------- الرسوم البيانية ------------------------- */
const COLORS = { up: '#2ea043', down: '#f85149', ema9: '#e3b341', ema20: '#58a6ff', ema30: '#39c5cf', ema50: '#a371f7',
  rsi: '#58a6ff', macd: '#58a6ff', signal: '#e3b341', hist: '#3fb950', stoch: '#a371f7', trigger: '#58a6ff',
  stop: '#f85149', target: '#3fb950', muted: '#6b7684' };

function chartOptions(el, intraday) {
  return {
    width: el.clientWidth, height: el.clientHeight,
    layout: { background: { type: 'solid', color: '#0a0f16' }, textColor: '#93a1b3', fontSize: 11,
              fontFamily: '"Segoe UI",Tahoma,"Noto Kufi Arabic",sans-serif' },
    grid: { vertLines: { color: 'rgba(30,39,53,.75)' }, horzLines: { color: 'rgba(30,39,53,.75)' } },
    rightPriceScale: { borderColor: '#28334333', entireTextOnly: true, scaleMargins: { top: .08, bottom: .24 } },
    leftPriceScale: { visible: false },
    timeScale: {
      borderColor: '#28334333', timeVisible: intraday, secondsVisible: false, rightOffset: 6,
      barSpacing: 7, minBarSpacing: .8, lockVisibleTimeRangeOnResize: true,
    },
    crosshair: {
      mode: 0, vertLine: { color: '#388bfd', width: 1, style: 2, labelBackgroundColor: '#1f6feb' },
      horzLine: { color: '#388bfd', width: 1, style: 2, labelBackgroundColor: '#1f6feb' },
    },
    localization: { locale: 'en-GB', priceFormatter: p => '$' + Number(p).toFixed(p < 1 ? 3 : 2) },
    handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: true },
    handleScale: { axisPressedMouseMove: { time: true, price: true }, axisDoubleClickReset: true,
                   mouseWheel: true, pinch: true },
    kineticScroll: { touch: true, mouse: false },
  };
}

function ensureCharts(intraday) {
  const priceEl = $('#priceChart'), oscEl = $('#oscChart');
  if (state.charts.price) { state.charts.price.remove(); state.charts.price = null; }
  if (state.charts.osc) { state.charts.osc.remove(); state.charts.osc = null; }
  state.charts.price = LightweightCharts.createChart(priceEl, chartOptions(priceEl, intraday));
  const oscOpts = chartOptions(oscEl, intraday);
  state.charts.osc = LightweightCharts.createChart(oscEl, {
    ...oscOpts,
    localization: { locale: 'en-GB', priceFormatter: v => Number(v).toFixed(0) },   // RSI/MACD بأرقام لا دولارات
    rightPriceScale: { borderColor: '#28334333', entireTextOnly: true, scaleMargins: { top: .18, bottom: .12 } },
    crosshair: { ...oscOpts.crosshair },
  });
  const p = state.charts.price, o = state.charts.osc;
  p.timeScale().subscribeVisibleLogicalRangeChange(r => {
    if (!r) return;
    o.timeScale().setVisibleLogicalRange(r);
    syncHSlider(r);
  });
  o.timeScale().subscribeVisibleLogicalRangeChange(r => { if (r && !sliderLock) p.timeScale().setVisibleLogicalRange(r); });
  state.series = { price: {}, osc: {} };
}

async function loadChart(sym, tf) {
  const payload = await api(`/api/chart/${sym}?tf=${tf}`);
  if (!payload.candles || !payload.candles.length) { toast('لا توجد بيانات رسم لهذا الرمز'); return; }
  renderChart(payload);
}

function renderChart(p) {
  state._lastPayload = p;
  const intraday = p.tf === '4H';
  ensureCharts(intraday);
  const s = state.series;
  state.bars = (p.candles || []).length;
  s.price.candles = state.charts.price.addCandlestickSeries({
    upColor: COLORS.up, downColor: COLORS.down, borderUpColor: COLORS.up, borderDownColor: COLORS.down,
    wickUpColor: COLORS.up, wickDownColor: COLORS.down, priceLineVisible: true,
  });
  s.price.candles.setData(p.candles);
  s.price.vol = state.charts.price.addHistogramSeries({
    priceFormat: { type: 'volume' }, priceScaleId: 'vol', color: 'rgba(56,139,253,.35)',
    visible: chartUI.showVolume,
  });
  state.charts.price.priceScale('vol').applyOptions({ scaleMargins: { top: .86, bottom: 0 } });
  s.price.vol.setData(p.volume || []);
  s.price.ema9 = state.charts.price.addLineSeries({ color: COLORS.ema9, lineWidth: 1.4, priceLineVisible: false, lastValueVisible: false });
  s.price.ema20 = state.charts.price.addLineSeries({ color: COLORS.ema20, lineWidth: 1.4, priceLineVisible: false, lastValueVisible: false });
  s.price.ema30 = state.charts.price.addLineSeries({ color: COLORS.ema30, lineWidth: 1.4, priceLineVisible: false, lastValueVisible: false });
  s.price.ema50 = state.charts.price.addLineSeries({ color: COLORS.ema50, lineWidth: 1.4, priceLineVisible: false, lastValueVisible: false });
  s.price.bbU = state.charts.price.addLineSeries({ color: 'rgba(107,118,132,.45)', lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false });
  s.price.bbL = state.charts.price.addLineSeries({ color: 'rgba(107,118,132,.45)', lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false });
  s.price.chU = state.charts.price.addLineSeries({ color: 'rgba(163,113,247,.5)', lineWidth: 1.2, lineStyle: 1, priceLineVisible: false, lastValueVisible: false });
  s.price.chL = state.charts.price.addLineSeries({ color: 'rgba(163,113,247,.5)', lineWidth: 1.2, lineStyle: 1, priceLineVisible: false, lastValueVisible: false });
  s.price.ema9.setData(p.overlays.ema9 || []);
  s.price.ema20.setData(p.overlays.ema20 || []);
  s.price.ema30.setData(p.overlays.ema30 || []);
  s.price.ema50.setData(p.overlays.ema50 || []);
  s.price.bbU.setData(p.overlays.bb_up || []);
  s.price.bbL.setData(p.overlays.bb_low || []);
  s.price.chU.setData(p.overlays.ch_up || []);
  s.price.chL.setData(p.overlays.ch_low || []);
  if (p.markers?.length) s.price.candles.setMarkers(p.markers);

  // مستويات الدعم/المقاومة/الخطة
  const lv = p.levels || {}, t = lv.targets || {}, lc = p.lifecycle || {};
  const line = (price, color, title, style = 2, onAxis = true) => price && s.price.candles.createPriceLine({
    price: Number(price), color, lineWidth: 1, lineStyle: style, axisLabelVisible: onAxis, title,
  });
  // شروط دورة الحياة مرسومة على المخطط (بلا ازدحام: الأساسيات دائماً، والبقية حسب الحاجة)
  line(lc.support, '#f0883e', 'الدعم الرئيسي', 0);
  if (lc.resistance_level) line(lc.resistance_level, '#e3b341', 'أقرب مقاومة', 2);
  if (lc.sweep_low) line(lc.sweep_low, '#f85149', 'قاع السحب', 1);
  if (!lc.ready) {
    const tol = ((state.strategy?.params?.support_retest_tolerance_pct ?? 2) / 100);
    if (lc.support) {
      line(lc.support * (1 + tol), '#f0883e66', '', 1);
      line(lc.support * (1 - tol), '#f0883e66', '', 1);
    }
    if (lc.impulse_target) line(lc.impulse_target, '#a371f7', 'هدف الانطلاقة +20%', 1);
  }
  line(lv.trigger, COLORS.trigger, 'اختراق', 0);
  line(lv.stop, COLORS.stop, 'وقف', 0);
  line(t.t1_382, COLORS.target, 'هدف1', 3, false);
  line(t.t2_618, COLORS.target, 'هدف2', 3, false);

  const seqLegend = (p.lifecycle?.checks || []).map(c =>
    `<span><i class="dot ${c.ok ? 'ok' : 'active'}"></i> ${esc((STEP_SHORT[c.key] || c.ar))}</span>`).join('');
  $('#chartLegend').innerHTML = [
    ['EMA9', COLORS.ema9], ['EMA20', COLORS.ema20], ['EMA30', COLORS.ema30], ['EMA50', COLORS.ema50],
    ['الدعم', '#f0883e'], ['المقاومة', '#e3b341'], ['هدف الانطلاقة +20%', '#a371f7'],
    ['قاع السحب', '#f85149'],
  ].map(([k, c]) => `<span><i style="background:${c}"></i>${k}</span>`).join('') +
    (seqLegend ? `<span class="seq-legend">${seqLegend}</span>` : '') +
    `<span>الشموع: ${p.meta?.bars || 0}</span><span>الإقفال: ${money(p.meta?.last_close)}</span>` +
    (p.meta?.rvol ? `<span>RVOL: ${n(p.meta.rvol, 2)}x</span>` : '');

  applyPriceScale();
  renderOscillator(p);
  fitCharts();
}

function renderOscillator(p) {
  const o = state.series.osc;
  const ch = state.charts.osc;
  const osc = p.oscillators || {};
  const kind = state.osc;
  if (kind === 'rsi' || kind === 'mfi') {
    const data = kind === 'rsi' ? osc.rsi : osc.mfi;
    o.main = ch.addLineSeries({ color: COLORS.rsi, lineWidth: 1.6, priceLineVisible: false });
    o.main.setData(data || []);
    o.main.createPriceLine({ price: 70, color: 'rgba(248,81,73,.4)', lineWidth: 1, lineStyle: 2, title: 'تشبع 70', axisLabelVisible: false });
    o.main.createPriceLine({ price: 30, color: 'rgba(63,185,80,.45)', lineWidth: 1, lineStyle: 2, title: 'تشبع 30', axisLabelVisible: false });
    ch.applyOptions({ rightPriceScale: { autoScale: true, scaleMargins: { top: .12, bottom: .12 } } });
  } else if (kind === 'macd') {
    o.hist = ch.addHistogramSeries({ priceLineVisible: false, color: 'rgba(63,185,80,.5)' });
    o.hist.setData((osc.macd_hist || []).map(d => ({ ...d, color: d.value >= 0 ? 'rgba(63,185,80,.55)' : 'rgba(248,81,73,.55)' })));
    o.main = ch.addLineSeries({ color: COLORS.macd, lineWidth: 1.4, priceLineVisible: false });
    o.main.setData(osc.macd || []);
    o.signal = ch.addLineSeries({ color: COLORS.signal, lineWidth: 1.2, priceLineVisible: false });
    o.signal.setData(osc.macd_signal || []);
  } else if (kind === 'stoch') {
    o.main = ch.addLineSeries({ color: COLORS.stoch, lineWidth: 1.5, priceLineVisible: false });
    o.main.setData(osc.stoch_k || []);
    o.signal = ch.addLineSeries({ color: COLORS.ema9, lineWidth: 1.2, priceLineVisible: false });
    o.signal.setData(osc.stoch_d || []);
    o.main.createPriceLine({ price: 80, color: 'rgba(248,81,73,.4)', lineWidth: 1, lineStyle: 2, axisLabelVisible: false });
    o.main.createPriceLine({ price: 20, color: 'rgba(63,185,80,.45)', lineWidth: 1, lineStyle: 2, axisLabelVisible: false });
  } else if (kind === 'rvol') {
    const data = (osc.rvol || []).map(d => ({ ...d, color: d.value >= 1.5 ? 'rgba(63,185,80,.6)' : 'rgba(56,139,253,.35)' }));
    o.hist = ch.addHistogramSeries({ priceLineVisible: false });
    o.hist.setData(data);
    o.main = ch.addLineSeries({ color: COLORS.ema9, lineWidth: 1, priceLineVisible: false });
    o.main.setData((osc.rvol || []).map(d => ({ time: d.time, value: 1.5 })));
  }
}

/* --------- أدوات التحكم بالمخطط: تحجيم + سحب رأسي/أفقي --------- */
function applyPriceScale() {
  if (!state.charts.price) return;
  const s = state.charts.price.priceScale('right');
  const zoomScale = 2.2 - 1.7 * (chartUI.vZoom / 1000);      // 2.2 = مصغّر جداً … 0.5 = مكبّر
  const base = chartUI.showVolume ? { top: .08, bottom: .24 } : { top: .07, bottom: .07 };
  let top = base.top * zoomScale, bottom = base.bottom * zoomScale;
  const t = (chartUI.vPos - 500) / 500;                       // اتجاه السحب الرأسي
  const shift = .42 * zoomScale;
  if (t >= 0) top += shift * t; else bottom += shift * (-t);
  if (top + bottom > .93) { const k = .93 / (top + bottom); top *= k; bottom *= k; }
  s.applyOptions({ autoScale: chartUI.autoScale, scaleMargins: { top, bottom } });
}

function fitCharts() {
  if (!state.charts.price) return;
  sliderLock = true;
  state.charts.price.timeScale().fitContent();
  state.charts.osc.timeScale().fitContent();
  setTimeout(() => { sliderLock = false; syncHSlider(); }, 40);
}

function zoomTime(factor) {
  if (!state.charts.price) return;
  const ts = state.charts.price.timeScale();
  const r = ts.getVisibleLogicalRange();
  if (!r) return;
  const c = (r.from + r.to) / 2, half = Math.max(3, (r.to - r.from) * factor / 2);
  sliderLock = true;
  ts.setVisibleLogicalRange({ from: c - half, to: c + half });
  setTimeout(() => { sliderLock = false; syncHSlider(); }, 30);
}

function scrollTime(fraction) {                 // 0 = أقدم شمعة … 1 = الأحدث
  if (!state.charts.price || !state.bars) return;
  const ts = state.charts.price.timeScale();
  const r = ts.getVisibleLogicalRange();
  if (!r) return;
  const width = Math.max(6, r.to - r.from);
  const span = Math.max(1, state.bars - width);
  const from = clamp(fraction * span, 0, Math.max(0, state.bars - width + 2));
  sliderLock = true;
  ts.setVisibleLogicalRange({ from, to: from + width });
  setTimeout(() => { sliderLock = false; }, 30);
}

function syncHSlider(r) {
  if (sliderLock || !state.bars) return;
  r = r || state.charts.price?.timeScale().getVisibleLogicalRange();
  if (!r) return;
  const width = Math.max(6, r.to - r.from);
  const span = Math.max(1, state.bars - width);
  $('#hSlider').value = clamp(Math.round(1000 * r.from / span), 0, 1000);
}

function bindChartControls() {
  $('#ctZoomIn').onclick = () => zoomTime(.7);
  $('#ctZoomOut').onclick = () => zoomTime(1.45);
  $('#ctFit').onclick = () => { fitCharts(); toast('تم عرض جميع الشموع', 1600); };
  $('#ctReset').onclick = () => {
    chartUI.autoScale = true; chartUI.showVolume = true; chartUI.vPos = 500; chartUI.vZoom = 540;
    $('#vSlider').value = 500; $('#zSlider').value = 540;
    $('#ctAutoScale').classList.add('active'); $('#ctVolume').classList.add('active');
    state.series.price.vol?.applyOptions({ visible: true });
    applyPriceScale(); fitCharts();
  };
  $('#ctAutoScale').onclick = () => {
    chartUI.autoScale = !chartUI.autoScale;
    $('#ctAutoScale').classList.toggle('active', chartUI.autoScale);
    applyPriceScale();
    toast(chartUI.autoScale ? 'تحجيم تلقائي لعمود السعر' : 'تحجيم يدوي — اسحب محور السعر أو استخدم الشريط الجانبي', 2600);
  };
  $('#ctVolume').onclick = () => {
    chartUI.showVolume = !chartUI.showVolume;
    $('#ctVolume').classList.toggle('active', chartUI.showVolume);
    state.series.price.vol?.applyOptions({ visible: chartUI.showVolume });
    applyPriceScale();
  };
  $('#ctUp').onclick = () => { chartUI.vPos = clamp(chartUI.vPos - 70, 0, 1000); $('#vSlider').value = chartUI.vPos; applyPriceScale(); };
  $('#ctDown').onclick = () => { chartUI.vPos = clamp(chartUI.vPos + 70, 0, 1000); $('#vSlider').value = chartUI.vPos; applyPriceScale(); };
  $('#ctVZoomIn').onclick = () => { chartUI.vZoom = clamp(chartUI.vZoom + 70, 0, 1000); $('#zSlider').value = chartUI.vZoom; applyPriceScale(); };
  $('#ctVZoomOut').onclick = () => { chartUI.vZoom = clamp(chartUI.vZoom - 70, 0, 1000); $('#zSlider').value = chartUI.vZoom; applyPriceScale(); };
  $('#vSlider').oninput = e => { chartUI.vPos = +e.target.value; applyPriceScale(); };
  $('#zSlider').oninput = e => { chartUI.vZoom = +e.target.value; applyPriceScale(); };
  $('#hSlider').oninput = e => scrollTime(+e.target.value / 1000);

  // أسهم لوحة المفاتيح: تحريك وسحب المخطط
  document.addEventListener('keydown', e => {
    if (!state.charts.price || !$('#tab-analysis').classList.contains('active')) return;
    if ($('#analysisBody').classList.contains('hidden')) return;
    if (['INPUT', 'TEXTAREA'].includes(e.target.tagName || '')) return;
    const r = state.charts.price.timeScale().getVisibleLogicalRange();
    if (!r) return;
    const width = Math.max(6, r.to - r.from);
    const span = Math.max(1, state.bars - width);
    const step = Math.max(1, Math.round(width * .12));
    switch (e.key) {
      case 'ArrowRight': scrollTime((r.from + step) / span); break;   // إلى الأحدث
      case 'ArrowLeft': scrollTime((r.from - step) / span); break;    // إلى الأقدم
      case 'ArrowUp': $('#ctUp').click(); break;
      case 'ArrowDown': $('#ctDown').click(); break;
      case '+': case '=': zoomTime(.8); break;
      case '-': zoomTime(1.25); break;
      default: return;
    }
    e.preventDefault();
  });
}

bindChartControls();

$$('.tf').forEach(b => b.onclick = () => {
  $$('.tf').forEach(x => x.classList.remove('active'));
  b.classList.add('active');
  state.tf = b.dataset.tf;
  if (state.selected) loadChart(state.selected, state.tf);
});
$$('.osc').forEach(b => b.onclick = () => {
  $$('.osc').forEach(x => x.classList.remove('active'));
  b.classList.add('active');
  state.osc = b.dataset.osc;
  if (state.selected && state._lastPayload) {       // إعادة رسم سريعة بلا طلب جديد
    ensureCharts(state.tf === '4H');
    renderChart(state._lastPayload);
  } else if (state.selected) loadChart(state.selected, state.tf);
});

function resizeCharts() {
  if (!state.charts.price) return;
  const pEl = $('#priceChart'), oEl = $('#oscChart');
  state.charts.price.applyOptions({ width: pEl.clientWidth, height: pEl.clientHeight });
  state.charts.osc.applyOptions({ width: oEl.clientWidth, height: oEl.clientHeight });
}
new ResizeObserver(() => resizeCharts()).observe($('#tab-analysis'));
window.addEventListener('orientationchange', () => setTimeout(resizeCharts, 260));

/* ------------------------- التقارير ------------------------- */
$('#btnReportHtml').onclick = () => state.selected && window.open('/api/report/' + state.selected + '?fmt=html', '_blank');
$('#btnReportMd').onclick = () => state.selected && window.open('/api/report/' + state.selected + '?fmt=md', '_blank');
$('#btnJson').onclick = () => state.selected && window.open('/api/report/' + state.selected + '?fmt=json', '_blank');
$('#btnReport').onclick = async () => {
  try {
    const r = await api('/api/report/save', { method: 'POST' });
    toast(`تم توليد ${r.written.length} تقريراً في المجلد ${r.folder} ✓`, 6000);
    window.open('/reports/watchlist_' + new Date().toISOString().slice(0, 10).replace(/-/g, '') + '.html', '_blank');
  } catch (e) { toast('تعذر توليد التقارير: ' + e.message); }
};

/* ------------------------- المراحل والمصادر ------------------------- */
function renderPhases() {
  const counts = state.summary.phase_counts || {};
  const list = Object.entries(state.phases).filter(([k]) => k !== '0');
  const el = $('#phasesList');
  if (!el) return;
  el.innerHTML = [
    `<div class="phase-card" style="--pc:#5b6472"><h4>مستبعد (لم يستوفِ الشروط)</h4>
      <p>رموز لم تُحقق شرط التقسيم العكسي أو الهبوط أو التشبع أو الثبات، أو فشل تسلسل الانعكاس.</p>
      <div class="cnt">${counts['0'] || 0}</div></div>`
  ].concat(list.map(([k, v]) => `
    <div class="phase-card" style="--pc:${v.color}">
      <h4>${esc(v.ar)}</h4><p>${esc(v.desc)}</p><div class="cnt">${counts[k] || 0}</div>
    </div>`)).join('');
}

function renderWeights() {
  const w = state.strategy?.params?.weights || {};
  const keys = state.strategy?.scoring_keys || {};
  const el = $('#weightsList');
  if (!el) return;
  el.innerHTML = Object.entries(w).map(([k, v]) => `
    <div class="w-row"><span>${esc(keys[k] || k)}</span><b>${v}</b>
      <div class="w-bar"><div class="w-fill" style="width:${v * 5}%"></div></div></div>`).join('');
}

function renderSources() {
  const d = state.sources || {};
  const rows = [
    ['وضع البيانات المُعدّ', d.mode_setting || '—'],
    ['الوضع الفعّال الآن', d.mode_effective === 'live' ? 'سوق حقيقي (live)' : 'تجريبي (demo)'],
    ['مكتبة yfinance مثبّتة', d.has_yfinance ? 'نعم' : 'لا'],
  ].concat((d.providers || []).map(p => [p.name, (p.ok ? 'يعمل — ' : 'غير مفعّل — ') + (p.detail || '')]));
  const el = $('#sourcesTable');
  if (el) el.innerHTML = rows.map(([k, v]) => `<div class="row"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join('');
}

async function renderDemo() {
  try {
    const d = await api('/api/demo-metadata');
    const el = $('#demoBody');
    if (!el) return;
    el.innerHTML = d.scenarios.map(s => `<tr>
      <td class="sym-cell">${esc(s.symbol)}</td><td>${esc(s.name)}</td>
      <td>${esc(s.rs_ratio)}<div class="muted small">قبل ${Math.abs(s.rs_offset_sessions)} جلسة</div></td>
      <td class="pos">${pct(s.spike_pct)}</td><td class="neg">-${n(s.drop_pct, 0)}%</td>
      <td>${big(s.float)}</td><td>${n(s.si_pct, 1)}%</td>
      <td class="small">${esc(s.note)}</td></tr>`).join('');
  } catch (e) { /* ignore */ }
}

/* ------------------------- المساعدة ------------------------- */
$('#btnHelp').onclick = () => $('#helpModal').classList.remove('hidden');
$('#helpClose').onclick = () => $('#helpModal').classList.add('hidden');
$('#helpModal').onclick = e => { if (e.target.id === 'helpModal') $('#helpModal').classList.add('hidden'); };

async function renderHelp() {
  const p = state.strategy?.params || {};
  const el = $('#helpContent');
  if (!el) return;
  el.innerHTML = `
    <h3>الشروط الإلزامية الخمسة</h3>
    <ol><li>السعر بين ${p.min_price} و${p.max_price} دولار، شامل الحدين.</li>
    <li>تقسيم عكسي منفّذ خلال ${p.rs_lookback_days} يوماً تقويمياً؛ الإعلانات المستقبلية لا تكفي.</li>
    <li>هبوط لا يقل عن ${p.min_drop_pct}% من أعلى قمة بعد التقسيم إلى أدنى قاع لاحق.</li>
    <li>RSI اليومي (${p.rsi_period}) وصل إلى أقل من ${p.rsi_oversold} خلال تكوّن القاع.</li>
    <li>Short Shares أقل حصراً من ${p.max_short_shares?.toLocaleString('en-US')} سهم. البيانات المفقودة لا تعني صفراً.</li></ol>
    <h3>النقاط الفنية: من صفر إلى 100</h3>
    <p>جفاف الحجم 25، أفضل نموذج من W أو الوتد الهابط أو تباعد RSI الصاعد 35، اختبار الدعم وسحب السيولة 25، وتفاعل EMA20/30/50 مقدار 15 نقطة.</p>
    <p>النماذج بدائل وليست شروط AND. غيابها يمنح صفراً في حصتها، دون استبعاد أو إعادة توزيع وزنها.
    جميع الأسهم المؤهلة تظهر افتراضياً، حتى بدرجة صفر، بترتيب تنازلي للنقاط. مراحل دورة الحياة وصف إضافي لا شرط ظهور.</p>
    <p>حجم البيع المكشوف اليومي ونسبته لا يعوّضان عدد المراكز المكشوفة. بيانات Short Shares دورية وقد تتأخر؛ راجع تاريخها ومصدرها.
    لا حد إضافي للقيمة السوقية أو الفلوت أو السيولة. النماذج تقديرية والنقاط ليست احتمال ربح أو توصية استثمارية.</p>
  `;
}

/* ------------------------- الإقلاع ------------------------- */
(async function boot() {
  try {
    state.strategy = await api('/api/strategy');
    state.phases = state.strategy.phases || {};
    renderWeights(); renderPhases(); renderHelp();
  } catch (e) { /* ignore */ }
  await loadHealth();
  renderDemo();
  route();
  setInterval(loadHealth, 60000);
})();
