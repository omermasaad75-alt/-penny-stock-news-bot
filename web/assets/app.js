/* ============================================================
   لوحة الاستراتيجية (قاع ثانٍ + قمة ÷ 2) — منطق الواجهة
   ============================================================ */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

const state = {
  rows: [], summary: {}, sources: {}, strategy: null, phases: {},
  selected: null, analysis: null, tf: '1D', osc: 'rsi',
  filter: 'all', search: '', polling: null, charts: { price: null, osc: null },
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

const sourceKeys = ['price_source', 'fundamentals_source', 'news_source'];
const STATUS_AR = { READY: 'جاهز للدخول', NEAR: 'قريب من الاكتمال', WATCH: 'مراقبة', EXCLUDED: 'مستبعد' };
const PATTERN_AR = { double_bottom: 'قاع ثانٍ (دعم ثابت)', liquidity_sweep: 'سحب سيولة تحت القاع ثم استعادة' };
const STEP_SHORT = {
  pattern: 'قاع ثانٍ / سحب سيولة', confirm: 'شمعة تأكيد', drop_zone: 'منطقة القمة ÷ 2',
  negative: 'هبوط 40%+ بعد القمة', price: 'السعر 1.50–2.00', resistance: 'مجال المقاومة 20%+',
};
/* نسبة المخاطرة/العائد للهدف (من خطة الصفقة) */
function rrOf(lv, k) {
  if (!lv || !lv.trigger || !lv.stop || !lv.targets || lv.targets[k] == null) return '—';
  const risk = lv.trigger - lv.stop;
  return risk > 0 ? n((lv.targets[k] - lv.trigger) / risk, 2) + ' : 1' : '—';
}
function sourceBadge(r) {
  const label = r.synthetic !== false ? '🔴 تجريبي' : sourceKeys.some(k => !r[k]) ? '🟠 ناقص' : '🟢 حقيقي';
  const names = ['الأسعار', 'الأساسيات', 'الأخبار'];
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
  const withPattern = rows.filter(r => r.pattern && r.pattern.found).length;
  const near = rows.filter(r => r.strategy_status === 'NEAR').length;
  const ready = rows.filter(r => r.strategy_status === 'READY').length;
  const watch = rows.filter(r => r.watchlist).length;
  const excluded = src.rejections || {};
  let noData = Object.keys(excluded).length;
  const partial = !!(cov.partial || disc.truncated);
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
    `مرشحون ${collected} · مفحوصون ${checked} · غير مفحوصين ${unchecked} · ` +
    `بنمط قاع/سحب ${withPattern}` +
    `</div>` +
    (partial
      ? `<div style="background:#3a2a08;color:#ffd87a;border:1px solid #8a6d1f;border-radius:10px;` +
        `padding:10px 12px;margin-bottom:8px">⚠ التغطية جزئية؛ لا تعني النتائج غياب حالات أخرى في السوق.</div>`
      : '') +
    `<div style="display:flex;flex-wrap:wrap;gap:7px">` +
    chip('لم يُفحص', unchecked, 'c-amber', 'ليست مستبعدة') +
    chip('رموز بأسباب استبعاد', noData, 'c-red') +
    chip('قريب من الاكتمال', near, 'c-amber') +
    chip('قائمة المتابعة', watch, 'c-amber') +
    chip('جاهز للدخول', ready, 'c-green') +
    `</div>` +
    `<div class="muted" style="margin-top:8px;font-size:11px">«لم يُفحص» = مرشحون لم تتحقق منهم بعد (مهلة أو سقف) وليست مستبعدة فنياً؛ ` +
    `«قريب من الاكتمال» = نمط قاع مكتمل بلا شمعة تأكيد بعد؛ «جاهز للدخول» = نقاط ≥ 75 مع إغلاق تأكيدي.</div>`;
}

function renderKpis() {
  const s = state.summary;
  const rows = state.rows || [];
  $('#kpiScanned').textContent = s.scanned ?? '—';
  $('#kpiPattern').textContent = rows.filter(r => r.pattern && r.pattern.found).length;
  $('#kpiNear').textContent = s.near ?? rows.filter(r => r.strategy_status === 'NEAR').length;
  $('#kpiWatch').textContent = s.watchlist ?? '—';
  $('#kpiReady').textContent = s.ready ?? '—';
  $('#kpiAvg').textContent = s.avg_score_watchlist ?? '—';
  $('#kpiTime').textContent = s.generated_at ? new Date(s.generated_at).toLocaleString('ar-EG', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—';
}

function rowMatchesFilter(r) {
  switch (state.filter) {
    case 'watch': return !!r.watchlist;
    case 'eligible': return !!r.eligible;
    case 'near': return r.strategy_status === 'NEAR';
    case 'ready': return r.strategy_status === 'READY';
    case 'pattern': return !!(r.pattern && r.pattern.found);
    case 'rejected': return r.strategy_status === 'EXCLUDED';
    default: return true;
  }
}

function seqCell(r) {
  const checks = r.lifecycle_checks || [];
  if (!checks.length) return `<span class="muted small">لا بيانات كافية</span>`;
  const done = checks.filter(c => c.ok).length;
  const dots = checks.map(c => `<i class="dot ${c.ok ? 'ok' : 'pending'}"></i>`).join('');
  const active = checks.find(c => !c.ok);
  let label, color;
  if (r.strategy_status === 'READY') { label = `جاهز للدخول (${done}/6)`; color = 'c-green'; }
  else if (r.pattern && r.pattern.broken) { label = 'النمط منكسر — تحت وقف القاع الثاني'; color = 'c-red'; }
  else { label = `${done}/6 — ناقص: ${STEP_SHORT[active?.key] || (active?.ar || '')}`; color = 'c-amber'; }
  return `<span title="${esc(STATUS_AR[r.strategy_status] || '')}" style="cursor:help;white-space:nowrap">
    <span class="seq-icons">${dots}</span>
    <div class="small ${color}">${esc(label)}</div></span>`;
}

function renderTable() {
  const body = $('#scanBody');
  let rows = state.rows.filter(rowMatchesFilter);
  if (state.search) {
    rows = rows.filter(r => r.symbol.includes(state.search) || (r.name || '').toUpperCase().includes(state.search));
  }
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="16" class="empty">لا توجد نتائج مطابقة — جرّب مسح السوق أو تغيير المُرشّح.</td></tr>`;
    $('#scanFooter').textContent = '';
    return;
  }
  body.innerHTML = rows.map((r, i) => {
    const ph = r.phase_info || {};
    const lv = r.levels || {}, t = lv.targets || {};
    const pat = r.pattern && r.pattern.found ? PATTERN_AR[r.pattern.type] || r.pattern.type : '—';
    const scoreColor = r.score >= 75 ? '#3fb950' : r.score >= 60 ? '#d29922' : '#8b96a5';
    const checks = r.lifecycle_checks || [];
    const done = checks.filter(c => c.ok).length;
    const conf = r.confirmation && r.confirmation.confirmed ? '<span class="c-green">✓ مؤكد</span>' : '<span class="muted">بانتظار</span>';
    const dz = lv.drop_zone != null ? `${money(lv.drop_zone_low)}–${money(lv.drop_zone_high)}` : '—';
    return `<tr data-sym="${r.symbol}" class="${r.eligible ? '' : 'phase-rejected'}">
      <td class="muted">${i + 1}</td>
      <td class="sym-cell">${esc(r.symbol)}${sourceBadge(r)}<div class="muted small" style="max-width:170px;overflow:hidden;text-overflow:ellipsis">${esc((r.name || '').slice(0, 26))}</div></td>
      <td><span class="phase-pill" style="background:${(ph.color || '#333')}22;color:${ph.color || '#ccc'};border:1px solid ${(ph.color || '#333')}66">${esc(STATUS_AR[r.strategy_status] || (ph.ar || '—'))}</span></td>
      <td class="score-cell" style="color:${scoreColor}">${n(r.score, 1)}<span class="muted small"> ${esc(r.grade || '')}</span></td>
      <td>${money(r.last_close)}<br><span class="${cls(r.change_pct)} small">${pct(r.change_pct)}</span></td>
      <td>${lv.pattern_low1 != null ? `${money(lv.pattern_low1)} / ${money(lv.pattern_low2)}` : '—'}</td>
      <td>${dz}</td>
      <td>${r.drop_zone && r.drop_zone.spike_high ? money(r.drop_zone.spike_high) : '—'}</td>
      <td>${done}/${checks.length || 6}</td>
      <td>${lv.trigger ? money(lv.trigger) : '—'}</td>
      <td>${lv.stop ? money(lv.stop) : '—'}</td>
      <td>${t.t1 ? money(t.t1) : '—'}</td>
      <td>${rrOf(lv, 't1')}</td>
      <td>${conf}</td>
      <td class="muted" style="max-width:190px;white-space:normal">${esc(pat)}</td>
      <td><button class="btn btn-mini" data-open="${r.symbol}">تحليل ›</button></td>
    </tr>`;
  }).join('');
  $$('#scanBody tr[data-sym]').forEach(tr => tr.onclick = () => go('#/sym/' + tr.dataset.sym));
  $('#scanFooter').textContent = `عرض ${rows.length} من ${state.rows.length} رمز — ` +
    `جاهز للدخول: ${state.summary.ready ?? 0} · قريب من الاكتمال: ${state.summary.near ?? 0} · ` +
    `قائمة المتابعة: ${state.summary.watchlist ?? 0}`;
  if (state.selected) {
    const tr = $(`#scanBody tr[data-sym="${state.selected}"]`);
    if (tr) tr.classList.add('selected');
  }
}

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
  let rows = state.rows.filter(rowMatchesFilter);
  if (state.search) rows = rows.filter(r => r.symbol.includes(state.search) || (r.name || '').toUpperCase().includes(state.search));
  if (!rows.length) { box.innerHTML = '<div class="empty">لا توجد نتائج مطابقة.</div>'; return; }
  box.innerHTML = rows.map(r => {
    const ph = r.phase_info || {}, lv = r.levels || {}, t = lv.targets || {};
    const checks = r.lifecycle_checks || [];
    const done = checks.filter(c => c.ok).length;
    const stage = r.strategy_status === 'READY' ? `جاهز للدخول — ${done}/6`
      : r.pattern && r.pattern.broken ? 'النمط منكسر'
      : `${done}/6 — ${STATUS_AR[r.strategy_status] || ''}`;
    return `<div class="mcard ${r.eligible ? '' : 'muted-card'}" data-sym="${r.symbol}">
      <div class="mcard-head">
        <div><b class="sym-cell">${esc(r.symbol)}</b>${sourceBadge(r)}
          <div class="muted small">${esc((r.name || '').slice(0, 30))}</div></div>
        <div class="mcard-price">${money(r.last_close)}
          <div class="${cls(r.change_pct)} small">${pct(r.change_pct)}</div></div>
        <span class="phase-pill" style="background:${(ph.color || '#333')}22;color:${ph.color || '#ccc'};border:1px solid ${(ph.color || '#333')}66">${esc(STATUS_AR[r.strategy_status] || '—')}</span>
        <span class="score-pill">${n(r.score, 1)}${r.grade ? ' ' + esc(r.grade) : ''}</span>
      </div>
      <div class="mcard-seq">${seqCell(r)}</div>
      <div class="mcard-spark">${sparkline(r.spark)}<span class="muted small">${esc(stage)}</span></div>
      <div class="mcard-grid">
        <div><span>القاعان</span><b>${lv.pattern_low1 != null ? money(lv.pattern_low1) + ' / ' + money(lv.pattern_low2) : '—'}</b></div>
        <div><span>منطقة القمة ÷ 2</span><b>${lv.drop_zone != null ? money(lv.drop_zone) : '—'}</b></div>
        <div><span>الدخول</span><b>${lv.trigger ? money(lv.trigger) : '—'}</b></div>
        <div><span>الوقف</span><b>${lv.stop ? money(lv.stop) : '—'}</b></div>
        <div><span>الهدف 1 (+25%)</span><b>${t.t1 ? money(t.t1) : '—'}</b></div>
        <div><span>مخاطرة/عائد</span><b>${rrOf(lv, 't1')}</b></div>
        <div><span>حجم المركز</span><b>${lv.position_size ?? '—'} سهم</b></div>
        <div><span>الفلوت</span><b>${big(r.float_shares)}</b></div>
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
  document.title = 'تحليل ' + sym + ' | لوحة الاستراتيجية';
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
    box.innerHTML = `<p class="muted">لا توجد معايير محسوبة لهذا الرمز.</p>`;
  } else {
    box.innerHTML = checks.map((c, i) => `
      <div class="step ${c.ok ? 'done' : 'pending'}">
        <div class="st-num">${c.ok ? '✓' : i + 1}</div>
        <div class="st-title">${esc(c.ar)}</div>
        <div class="st-std">${esc(STEP_SHORT[c.key] || '')}</div>
      </div>`).join('');
  }
  const st = $('#seqStatus');
  const status = a.strategy_status || 'EXCLUDED';
  const failed = !!(a.pattern && a.pattern.broken);
  st.className = 'seq-status ' + (failed ? 'fail' : status === 'READY' ? 'ok' : 'active');
  st.innerHTML = `<i class="dot ${failed ? 'fail' : status === 'READY' ? 'ok' : 'active'}"></i> ${esc(STATUS_AR[status] || '—')} · التقييم ${n(a.score, 1)}`;
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
  const status = a.strategy_status || 'EXCLUDED';
  badge.textContent = STATUS_AR[status] || (ph.ar || '—');
  const phaseColor = ph.color || '#333';
  badge.style.background = phaseColor + '22';
  badge.style.color = ph.color || '#ccc';
  badge.style.borderColor = phaseColor + '66';
  badge.className = 'badge';
  $('#anPhaseDesc').textContent = ph.desc || '';
  const g = $('#anGauge');
  const deg = Math.max(0, Math.min(360, (a.score || 0) * 3.6));
  const col = a.score >= 75 ? '#3fb950' : a.score >= 55 ? '#d29942' : a.score >= 35 ? '#8b96a5' : '#5b6472';
  g.style.background = `conic-gradient(${col} 0deg, ${col} ${deg}deg, #1d2530 ${deg}deg 360deg)`;
  $('#anScore').textContent = n(a.score, 0);
  $('#anGrade').textContent = a.grade || '—';

  const alerts = [];
  if (a.risk_flags?.length) alerts.push(['bad', 'خطر', a.risk_flags.map(esc).join('<br>')]);
  if (a.pattern && a.pattern.broken) alerts.push(['bad', 'منكسر', 'النمط منكسر: السعر تحت وقف القاع الثاني — يُلغى السيناريو حتى يتكوّن قاع جديد.']);
  if (a.rejections?.length) alerts.push(['warn', 'استبعاد', '<b>أسباب الاستبعاد:</b><br>' + a.rejections.map(esc).join('<br>')]);
  if (a.notes?.length) alerts.push(['info', 'ملاحظة', a.notes.map(esc).join('<br>')]);
  if (status === 'READY') alerts.unshift(['good', 'جاهز', 'الشروط مكتملة وشمعة التأكيد تحققت — راجع خطة الصفقة والوقف قبل الدخول.']);
  $('#anAlerts').innerHTML = alerts.map(([k, tag, t]) =>
    `<div class="alert alert-${k}"><i class="dot ${k === 'good' ? 'ok' : k === 'bad' ? 'fail' : k === 'warn' ? 'active' : 'info'}"></i>
      <div><b class="alert-tag">${tag}</b> ${t}</div></div>`).join('');

  renderStepper(a);
  renderLevels(a); renderChecklist(a); renderBreakdown(a); renderFundamentals(a);
  renderMetrics(a); renderPatterns(a); renderNews(a);

  $('#anWhy').innerHTML = (a.why_ar || []).map(w => `<li>${esc(w)}</li>`).join('');
  $('#anAction').innerHTML = `<b>خطة العمل:</b> ${esc(a.action_ar || '')}`;
  $('#anConfirmations').innerHTML = (a.confirmations || []).length
    ? a.confirmations.map(c => `<span class="c">✓ ${esc(c)}</span>`).join('')
    : '<span class="muted small">لا توجد تأكيدات بعد.</span>';
}

function renderLevels(a) {
  const lv = a.levels || {}, t = lv.targets || {};
  const heading = $('#anLevels')?.closest('.card')?.querySelector('h3');
  if (heading) heading.textContent = 'خطة التداول والمستويات';
  const box = (cls2, label, val, extra = '') =>
    `<div class="lvl lvl-${cls2}"><span>${label}</span><b>${val}</b>${extra ? `<div class="muted small">${extra}</div>` : ''}</div>`;
  const plan = a.trade_plan;
  const noPlan = !(plan && plan.valid);
  const notice = noPlan
    ? `<div class="gam-reason" style="grid-column:1/-1"><b>لا توجد خطة صالحة بعد:</b> ${a.pattern && a.pattern.found ? 'مخاطرة السهم غير صالحة أو النمط لم يكتمل.' : 'لم يتكوّن قاع ثانٍ أو سحب سيولة بعد.'}</div>` : '';
  const targets = ['t1', 't2', 't3', 't4'].map((k, i) => box('target', `الهدف ${i + 1} (+${25 * (i + 1)}%)`, money(t[k]),
    !noPlan && lv.trigger ? `مخاطرة/عائد ${n((t[k] - lv.trigger) / (lv.trigger - lv.stop), 2)}:1` : '')).join('');
  $('#anLevels').innerHTML = notice + [
    box('trigger', 'نقطة الدخول (إغلاق تأكيدي)', money(lv.trigger), lv.entry_zone ? `نطاق ${money(lv.entry_zone[0])} – ${money(lv.trigger)}` : ''),
    box('stop', 'وقف الخسارة (3% تحت القاع الثاني)', money(lv.stop), lv.stop_pct != null ? `${n(lv.stop_pct, 1)}% من الدخول` : ''),
    box('stop', 'القاع الثاني', money(lv.pattern_low2), lv.pattern_low1 != null ? `القاع الأول ${money(lv.pattern_low1)}` : ''),
    box('target', 'منطقة القمة ÷ 2', money(lv.drop_zone), lv.drop_zone_low != null ? `${money(lv.drop_zone_low)} – ${money(lv.drop_zone_high)}` : ''),
    targets,
    `<div class="lvl" style="grid-column:1/-1"><span>إدارة حجم المركز</span><b class="small" style="font-weight:400">${
      plan && plan.valid ? `حجم المركز ${lv.position_size ?? '—'} سهم · مخاطرة السهم ${money(lv.risk_per_share)} · مخاطرة ثابتة للصفقة` : 'غير متاح بدون خطة صالحة'}</b></div>`,
  ].join('');
}

function renderChecklist(a) {
  const p = a.pattern || {}, c = a.confirmation || {}, dz = a.drop_zone || {};
  const detail = {
    pattern: p.found ? `${PATTERN_AR[p.type] || p.type} — قاع1 ${money(p.low1)} · قاع2 ${money(p.low2)} · خط العنق ${money(p.neckline)}`
      : p.broken ? 'النمط منكسر تحت وقف القاع الثاني' : 'لم يتكوّن قاع ثانٍ أو سحب سيولة بعد',
    confirm: c.confirmed ? `إغلاق فوق القاع الثاني وشمعة خضراء تكسر قمة الشمعة السابقة (الدخول ${money(c.entry)})`
      : (p.found ? 'بانتظار إغلاق أخضر يكسر قمة الشمعة السابقة' : 'يتطلب نمطاً أولاً'),
    drop_zone: dz.zone ? `القمة ${money(dz.spike_high)} ÷ 2 = ${money(dz.zone)} (نطاق ${money(dz.zone_low)}–${money(dz.zone_high)})` : '—',
    negative: 'هبوط 40%+ من القمة إلى ما بعدها خلال 90 يوماً',
    price: `السعر ${money(a.last_close)} (المثالي 1.50–2.00)`,
    resistance: 'أعلى سعر في 90 يوماً يجب أن يكون 20%+ فوق السعر',
  };
  const seq = (a.lifecycle_checks || []).map(ch => [ch.ar, detail[ch.key] || '', !!ch.ok, ch.key]);
  const doneN = seq.filter(x => x[2]).length;
  $('#anChecklist').innerHTML = `
    <div class="seq-head">
      <b>معايير الاستراتيجية (${doneN}/${seq.length})</b>
      <span class="${a.strategy_status === 'READY' ? 'c-green' : a.pattern && a.pattern.broken ? 'c-red' : 'c-amber'}">${esc(STATUS_AR[a.strategy_status] || '')}</span>
    </div>` + seq.map(([t, val, ok]) => `
    <div class="check ${ok ? 'ok' : 'pending'}">
      <span class="icon"><i class="dot ${ok ? 'ok' : 'pending'}"></i></span>
      <div><b>${esc(t)}</b><br><span class="muted small">${esc(val)}</span></div>
    </div>`).join('');
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
  ];
  $('#anFundamentals').innerHTML =
    rows.concat(shorts).map(([k, v]) => `<div class="row"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join('');
}

function renderMetrics(a) {
  const h4 = a.h4 || {};
  const trendAr = { up: 'صاعد', down: 'هابط', neutral: 'عرضي' }[h4.trend] || '—';
  const p = a.pattern || {}, dz = a.drop_zone || {}, c = a.confirmation || {};
  const rows = [
    ['القمة (أعلى 60 شمعة)', money(dz.spike_high)],
    ['منطقة القمة ÷ 2', dz.zone ? `${money(dz.zone)} (${money(dz.zone_low)} – ${money(dz.zone_high)})` : '—'],
    ['القاع الأول', p.found || p.broken ? money(p.low1) : '—'],
    ['القاع الثاني', p.found || p.broken ? money(p.low2) : '—'],
    ['خط العنق (أعلى ارتداد)', p.neckline ? money(p.neckline) : '—'],
    ['نوع النمط', p.found ? (PATTERN_AR[p.type] || p.type) : '—'],
    ['شموع منذ القاع الثاني', p.bars_since_low2 ?? '—'],
    ['شمعة التأكيد', c.confirmed ? 'مؤكدة ✓' : (c.green === false ? 'حمراء' : 'بانتظار')],
    ['ATR%', n(a.atr_pct, 2) + '%'],
    ['EMA9 / EMA20', money(a.levels?.ema9) + ' / ' + money(a.levels?.ema20)],
    ['فريم 4 ساعات', h4.available ? `${trendAr} · RSI ${n(h4.rsi, 1)} · RVOL ${n(h4.rvol, 2)}x` : '—'],
    ['توافق الفريمين', h4.aligned ? 'متوافق ✅' : 'غير متوافق ⏳'],
    ['RSI (الحالي / أدنى 40 شمعة)', `${n(a.rsi_now, 1)} / ${n(a.rsi_trough, 1)}`],
    ['MACD Hist', n(a.macd_hist, 4) + (a.macd_turning ? ' ↑' : '')],
    ['RVOL (حجم نسبي)', n(a.rvol, 2) + 'x'],
    ['جلسات إغلاق فوق EMA9', (a.above_ema9_sessions ?? 0) + ' جلسة'],
    ['القناة الهابطة', a.channel_broken ? 'مخترقة ✅' : 'لم تُخترق'],
    ['قيعان صاعدة (آخر 25 شمعة)', String(a.higher_lows ?? 0)],
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
const COLORS = { up: '#2ea043', down: '#f85149', ema9: '#e3b341', ema20: '#58a6ff', ema50: '#a371f7',
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
  s.price.ema50 = state.charts.price.addLineSeries({ color: COLORS.ema50, lineWidth: 1.4, priceLineVisible: false, lastValueVisible: false });
  s.price.bbU = state.charts.price.addLineSeries({ color: 'rgba(107,118,132,.45)', lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false });
  s.price.bbL = state.charts.price.addLineSeries({ color: 'rgba(107,118,132,.45)', lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false });
  s.price.chU = state.charts.price.addLineSeries({ color: 'rgba(163,113,247,.5)', lineWidth: 1.2, lineStyle: 1, priceLineVisible: false, lastValueVisible: false });
  s.price.chL = state.charts.price.addLineSeries({ color: 'rgba(163,113,247,.5)', lineWidth: 1.2, lineStyle: 1, priceLineVisible: false, lastValueVisible: false });
  s.price.ema9.setData(p.overlays.ema9 || []);
  s.price.ema20.setData(p.overlays.ema20 || []);
  s.price.ema50.setData(p.overlays.ema50 || []);
  s.price.bbU.setData(p.overlays.bb_up || []);
  s.price.bbL.setData(p.overlays.bb_low || []);
  s.price.chU.setData(p.overlays.ch_up || []);
  s.price.chL.setData(p.overlays.ch_low || []);
  if (p.markers?.length) s.price.candles.setMarkers(p.markers);

  // مستويات الاستراتيجية: القاعان، خط العنق، منطقة القمة÷2، الدخول والوقف، والأهداف الأربعة
  const lv = p.levels || {}, t = lv.targets || {}, lc = p.lifecycle || {};
  const line = (price, color, title, style = 2, onAxis = true) => price && s.price.candles.createPriceLine({
    price: Number(price), color, lineWidth: 1, lineStyle: style, axisLabelVisible: onAxis, title,
  });
  line(lv.pattern_low1, '#58a6ff', 'القاع الأول', 1);
  line(lv.pattern_low2, '#3fb950', 'القاع الثاني', 0);
  line(lv.neckline, '#e3b341', 'خط العنق', 2);
  line(lv.drop_zone, '#a371f7', 'القمة ÷ 2', 2, false);
  line(lv.trigger, COLORS.trigger, 'دخول', 0);
  line(lv.stop, COLORS.stop, 'وقف', 0);
  ['t1', 't2', 't3', 't4'].forEach((k, i) => line(t[k], COLORS.target, `هدف +${25 * (i + 1)}%`, 3, false));

  const seqLegend = (p.lifecycle?.checks || []).map(c =>
    `<span><i class="dot ${c.ok ? 'ok' : 'active'}"></i> ${esc((STEP_SHORT[c.key] || c.ar))}</span>`).join('');
  $('#chartLegend').innerHTML = [
    ['EMA9', COLORS.ema9], ['EMA20', COLORS.ema20], ['EMA50', COLORS.ema50],
    ['القاع الأول', '#58a6ff'], ['القاع الثاني', '#3fb950'], ['القمة ÷ 2', '#a371f7'],
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
      <p>رموز لم تبلغ حد المراقبة (35) أو كُسر فيها وقف القاع الثاني.</p>
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
  const el = $('#helpContent');
  if (!el) return;
  el.innerHTML = `
  <p>هذه المنصة تنفّذ استراتيجية <b>«قاع ثانٍ + منطقة القمة ÷ 2»</b> على الفريم اليومي مع تأكيد على فريم 4 ساعات.</p>
  <h3>المعايير التسعة (التقييم من 100)</h3>
  <table>
    <tr><th>#</th><th>المعيار</th><th>القاعدة في النظام</th></tr>
    <tr><td>1</td><td>الشورت</td><td>أقل من 20 ألف سهم (غير متوفر = محايد)</td></tr>
    <tr><td>2</td><td>منطقة القمة ÷ 2</td><td>القمة (أعلى 60 شمعة) ÷ 2 ±15%؛ مثال: افتتح 3 ووصل 3.40 ⇒ المنطقة 1.70</td></tr>
    <tr><td>3</td><td>السعر</td><td>1.50–2.00 نقاط كاملة؛ دون 5$ نقاط جزئية؛ الفلتر الصلب 0.10–10$</td></tr>
    <tr><td>4</td><td>المتوسطات</td><td>السعر فوق MA20، وميل MA20 للأعلى، والسعر فوق MA30</td></tr>
    <tr><td>5</td><td>السلوك السلبي</td><td>هبوط 40%+ من القمة إلى أدنى قاع بعدها خلال 90 شمعة</td></tr>
    <tr><td>6</td><td>النمط</td><td>قاع أول ← ارتداد 15%+ ← قاع ثانٍ ضمن ±4% <b>أو</b> سحب سيولة تحت القاع الأول يعود فوقه</td></tr>
    <tr><td>7</td><td>التأكيد</td><td>إغلاق أخضر فوق القاع الثاني وفوق القاع الأول، وتكسر قمة الشمعة السابقة، وبعد القاع الثاني بـ 10 شموع كحد أقصى</td></tr>
    <tr><td>8</td><td>المقاومة</td><td>أعلى سعر في 90 يوماً على بعد 20%+ من السعر</td></tr>
    <tr><td>9</td><td>الأخبار</td><td>وجود خبر أو إعلان (غير متوفر = محايد)</td></tr>
  </table>
  <h3>الحالات</h3>
  <ul>
    <li><b>جاهز للدخول</b>: التقييم ≥ 75 مع تأكيد الإغلاق.</li>
    <li><b>قريب من الاكتمال</b>: التقييم ≥ 55 مع نمط مكتمل بلا تأكيد بعد.</li>
    <li><b>مراقبة</b>: التقييم ≥ 35.</li>
    <li><b>مستبعد</b>: أقل من ذلك، أو كسر السعر وقف القاع الثاني (النمط منكسر).</li>
  </ul>
  <h3>خطة الصفقة</h3>
  <ul>
    <li>الدخول عند إغلاق التأكيد؛ الوقف 3% تحت القاع الثاني.</li>
    <li>الأهداف +25% / +50% / +75% / +100% من الدخول.</li>
    <li>حجم المركز = مبلغ المخاطرة الثابت ÷ مخاطرة السهم (الدخول − الوقف)؛ الخطة تُعتبر غير صالحة إن كانت المخاطرة صفراً أو أقل من 1% من الدخول.</li>
  </ul>
  <h3>أدوات المخطط</h3>
  <ul>
    <li><b>سحب حر:</b> اسحب المخطط بإصبعك أو بالفأرة يميناً/يساراً (الزمن) وأعلى/أسفل (الأسعار).</li>
    <li><b>شريط السحب الجانبي:</b> ▲ / ▼ لتحريك المخطط رأسياً، وشريط التكبير الرأسي لضبط مدى الأسعار.</li>
    <li><b>لوحة المفاتيح:</b> ← → للسحب، ↑ ↓ للتحريك الرأسي، + / − للتكبير والتصغير.</li>
  </ul>`;
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
