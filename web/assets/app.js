/* ============================================================
   منصة رصد التقسيم العكسي والانعكاس — منطق الواجهة
   ============================================================ */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

const state = {
  rows: [], summary: {}, sources: {}, strategy: null, phases: {},
  selected: null, analysis: null, tf: '1D', osc: 'rsi',
  filter: 'all', search: '', polling: null, charts: { price: null, osc: null },
  series: { price: {}, osc: {} }, mapping: {},
};

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

async function api(path, opts = {}) {
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

/* ------------------------- التبويبات ------------------------- */
$$('.tab').forEach(btn => btn.onclick = () => {
  $$('.tab').forEach(b => b.classList.remove('active'));
  $$('.tab-panel').forEach(p => p.classList.remove('active'));
  btn.classList.add('active');
  $('#tab-' + btn.dataset.tab).classList.add('active');
  if (btn.dataset.tab === 'analysis' && state.charts.price) {
    setTimeout(() => Object.values(state.charts).forEach(c => c && c.timeScale().fitContent()), 60);
  }
});
$$('.chip').forEach(c => c.onclick = () => {
  $$('.chip').forEach(x => x.classList.remove('active'));
  c.classList.add('active');
  state.filter = c.dataset.filter;
  renderTable();
});
$('#searchBox').oninput = e => { state.search = e.target.value.trim().toUpperCase(); renderTable(); };

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
    b.textContent = 'وضع البيانات: ' + (live ? 'حي (سوق حقيقي)' : 'تجريبي (demo)');
    b.className = 'badge ' + (live ? 'badge-live' : 'badge-demo');
    if (h.scan && h.scan.running) {
      $('#progressWrap').classList.remove('hidden');
      pollStatus();
    } else if (h.results_cached > 0) {
      loadResults();
    } else if (!state.autoScanStarted) {
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

async function loadResults() {
  const data = await api('/api/scan/results');
  state.rows = data.rows || [];
  state.summary = data.summary || {};
  state.sources = data.sources || state.sources;
  renderKpis(); renderTable(); renderPhases(); renderSources();
}

function renderKpis() {
  const s = state.summary;
  $('#kpiScanned').textContent = s.scanned ?? '—';
  $('#kpiRs').textContent = s.with_reverse_split ?? '—';
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
    case 'ready': return r.phase >= 5 && r.eligible;
    case 'rs': return !!r.rs_found;
    case 'rejected': return r.rs_found && !r.eligible;
    default: return true;
  }
}

function renderTable() {
  const body = $('#scanBody');
  let rows = state.rows.filter(rowMatchesFilter);
  if (state.search) {
    rows = rows.filter(r => r.symbol.includes(state.search) || (r.name || '').toUpperCase().includes(state.search));
  }
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="17" class="empty">لا توجد نتائج مطابقة — جرّب مسح السوق أو تغيير المُرشّح.</td></tr>`;
    $('#scanFooter').textContent = '';
    return;
  }
  body.innerHTML = rows.map((r, i) => {
    const ph = r.phase_info || {};
    const lv = r.levels || {};
    const pat = (r.patterns_daily && r.patterns_daily[0]) ? r.patterns_daily[0].name_ar : '—';
    const scoreColor = r.score >= 75 ? '#3fb950' : r.score >= 60 ? '#d29922' : '#8b96a5';
    const rsTxt = r.rs_found ? `${r.rs_ratio_str}<br><span class="muted small">${r.rs_date || ''} · ${r.rs_age_days ?? '—'}ي</span>` : '<span class="muted">—</span>';
    return `<tr data-sym="${r.symbol}" class="${r.eligible ? '' : 'phase-rejected'}">
      <td class="muted">${i + 1}</td>
      <td class="sym-cell">${esc(r.symbol)}<div class="muted small" style="max-width:170px;overflow:hidden;text-overflow:ellipsis">${esc((r.name || '').slice(0, 26))}</div></td>
      <td><span class="phase-pill" style="background:${(ph.color || '#333')}22;color:${ph.color || '#ccc'};border:1px solid ${(ph.color || '#333')}66">${esc((ph.ar || '—').replace('المرحلة ', 'م'))}</span></td>
      <td class="score-cell" style="color:${scoreColor}">${n(r.score, 1)}<span class="muted small"> ${esc(r.grade)}</span></td>
      <td>${money(r.last_close)}<br><span class="${cls(r.change_pct)} small">${pct(r.change_pct)}</span></td>
      <td>${rsTxt}</td>
      <td class="${cls(r.spike_pct)}">${pct(r.spike_pct)}</td>
      <td class="${cls(r.decline_pct)}">${pct(r.decline_pct)}</td>
      <td>${r.base_sessions ?? '—'}${r.base_broken ? ' <span class="c-red">⚠</span>' : ''}</td>
      <td>${n(r.rsi_trough, 0)} <span class="muted">→</span> <b>${n(r.rsi_now, 0)}</b>${r.rsi_recovered ? ' <span class="c-green">↑</span>' : ''}</td>
      <td>${big(r.float_shares)}</td>
      <td>${r.short_pct_float != null ? n(r.short_pct_float, 1) + '%' : '—'}</td>
      <td>${n(r.rvol, 2)}x</td>
      <td>${lv.trigger ? money(lv.trigger) : '—'}</td>
      <td>${lv.rr_t2 ? n(lv.rr_t2, 2) + ' : 1' : '—'}</td>
      <td class="muted" style="max-width:190px;white-space:normal">${esc(pat)}</td>
      <td><button class="btn" data-open="${r.symbol}">تحليل</button></td>
    </tr>`;
  }).join('');
  $$('#scanBody tr[data-sym]').forEach(tr => tr.onclick = (e) => selectSymbol(tr.dataset.sym));
  $('#scanFooter').textContent = `عرض ${rows.length} من ${state.rows.length} رمز — ` +
    `قائمة المتابعة: ${state.summary.watchlist ?? 0} · فرص مؤهلة: ${state.summary.eligible ?? 0}`;
  if (state.selected) {
    const tr = $(`#scanBody tr[data-sym="${state.selected}"]`);
    if (tr) tr.classList.add('selected');
  }
}

/* ------------------------- اختيار رمز ------------------------- */
async function selectSymbol(sym) {
  state.selected = sym;
  $$('#scanBody tr').forEach(tr => tr.classList.toggle('selected', tr.dataset.sym === sym));
  $$('.tab').forEach(b => b.classList.remove('active'));
  $$('.tab-panel').forEach(p => p.classList.remove('active'));
  $('.tab[data-tab="analysis"]').classList.add('active');
  $('#tab-analysis').classList.add('active');
  $('#analysisEmpty').classList.add('hidden');
  $('#analysisBody').classList.remove('hidden');
  $('#anSymbol').textContent = sym;
  try {
    const a = await api('/api/analysis/' + sym);
    state.analysis = a;
    renderAnalysis(a);
    await loadChart(sym, state.tf);
  } catch (e) { toast('تعذر تحميل التحليل: ' + e.message); }
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
  badge.textContent = ph.ar || '—';
  badge.style.background = (ph.color || '#333') + '22';
  badge.style.color = ph.color || '#ccc';
  badge.style.borderColor = (ph.color || '#333') + '66';
  badge.className = 'badge';
  $('#anPhaseDesc').textContent = ph.desc || '';
  const g = $('#anGauge');
  const deg = Math.max(0, Math.min(360, (a.score || 0) * 3.6));
  const col = a.score >= 85 ? '#3fb950' : a.score >= 75 ? '#7ee787' : a.score >= 62 ? '#d29922' : '#8b96a5';
  g.style.background = `conic-gradient(${col} 0deg, ${col} ${deg}deg, #1d2530 ${deg}deg 360deg)`;
  $('#anScore').textContent = n(a.score, 0);
  $('#anGrade').textContent = a.grade || '—';

  // التنبيهات
  const alerts = [];
  if (a.risk_flags?.length) alerts.push(['bad', '⛔', a.risk_flags.join('<br>')]);
  if (a.rejections?.length) alerts.push(['warn', '⚠️', '<b>أسباب الاستبعاد:</b><br>' + a.rejections.map(esc).join('<br>')]);
  if (a.notes?.length) alerts.push(['info', 'ℹ️', a.notes.map(esc).join('<br>')]);
  if (!a.rejections?.length && a.watchlist) alerts.unshift(['good', '✅', 'السهم مستوفٍ لشروط الاستراتيجية ومرصود في قائمة المتابعة.']);
  $('#anAlerts').innerHTML = alerts.map(([k, ic, t]) =>
    `<div class="alert alert-${k}"><span>${ic}</span><div>${t}</div></div>`).join('');

  renderLevels(a); renderChecklist(a); renderBreakdown(a); renderFundamentals(a);
  renderMetrics(a); renderPatterns(a); renderNews(a);

  $('#anWhy').innerHTML = (a.why_ar || []).map(w => `<li>${esc(w)}</li>`).join('');
  $('#anAction').innerHTML = `<b>خطة العمل:</b> ${esc(a.action_ar || '')}`;
  $('#anConfirmations').innerHTML = (a.confirmations || []).length
    ? a.confirmations.map(c => `<span class="c">✓ ${esc(c)}</span>`).join('')
    : '<span class="muted small">لا توجد تأكيدات فنية بعد — السهم ما زال في طور التكوين.</span>';
}

function renderLevels(a) {
  const lv = a.levels || {}, t = lv.targets || {};
  const box = (cls2, label, val, extra = '') =>
    `<div class="lvl ${cls2}"><span>${label}</span><b>${val}</b>${extra ? `<div class="muted small">${extra}</div>` : ''}</div>`;
  $('#anLevels').innerHTML = [
    box('trigger', 'نقطة الاختراق (Trigger)', money(lv.trigger), lv.entry_zone ? `منطقة الدخول ${money(lv.entry_zone[0])} – ${money(lv.trigger)}` : ''),
    box('stop', 'وقف الخسارة', money(lv.stop), lv.stop_pct != null ? `${n(lv.stop_pct, 1)}% من الدخول` : ''),
    box('stop', 'وقف إلغاء السيناريو', money(lv.stop_structural), 'كسر القاع = إلغاء'),
    box('target', 'الهدف 1 (38.2%)', money(t.t1_382), lv.rr_t1 ? `مخاطرة/عائد ${n(lv.rr_t1, 2)}:1` : ''),
    box('target', 'الهدف 2 (61.8%)', money(t.t2_618), lv.rr_t2 ? `مخاطرة/عائد ${n(lv.rr_t2, 2)}:1` : ''),
    box('target', 'الهدف 3 (القمة)', money(t.t3_full), 'استعادة كامل الموجة'),
  ].join('') + `<div class="lvl" style="grid-column:1/-1"><span>إدارة حجم المركز</span><b class="small" style="font-weight:400">${esc(lv.position_note_ar || '')}</b></div>`;
}

function renderChecklist(a) {
  const p = state.strategy?.params || {};
  const items = [
    ['تقسيم عكسي 1:2 – 1:60 خلال شهرين', `≤ ${p.rs_lookback_days || 60} يوماً`, a.rs_found ? `${a.rs_ratio_str} بتاريخ ${a.rs_date} (${a.rs_age_days} يوم)` : 'لا يوجد حدث', !!a.rs_found],
    ['صعود يوم التقسيم', `≥ ${p.min_spike_pct || 5}%`, a.spike_pct != null ? `${pct(a.spike_pct)} (${money(a.rs_open)} → ${money(a.spike_high)})` : '—', a.spike_pct != null && a.spike_pct >= (p.min_spike_pct || 5)],
    ['موجة هبوط قوية', `≥ ${p.min_drop_pct || 30}%`, a.decline_pct != null ? pct(a.decline_pct) : '—', a.decline_pct != null && Math.abs(a.decline_pct) >= (p.min_drop_pct || 30)],
    ['تشبع بيعي RSI', `< ${p.rsi_oversold || 30}`, `القاع ${n(a.rsi_trough, 1)} → الحالي ${n(a.rsi_now, 1)}`, a.rsi_trough != null && a.rsi_trough < (p.rsi_oversold || 30)],
    ['ثبات على القاع', `≥ ${p.min_base_sessions || 3} جلسات`, `${a.base_sessions} جلسة على ${money(a.bottom_price)}` + (a.base_range_pct != null ? ` (نطاق ${n(a.base_range_pct, 1)}%)` : ''), (a.base_sessions || 0) >= (p.min_base_sessions || 3)],
    ['جفاف الحجم داخل القاعدة', `< ${Math.round((p.max_volume_dryup_ratio || .9) * 100)}%`, a.volume_dryup != null ? `${Math.round(a.volume_dryup * 100)}% من متوسط ما قبل القاع` : '—', a.volume_dryup != null && a.volume_dryup < (p.max_volume_dryup_ratio || .9)],
    ['تأكيد الانعكاس', '≥ 3 تأكيدات', `${(a.confirmations || []).length} تأكيد: ${(a.confirmations || []).join('، ') || '—'}`, (a.confirmations || []).length >= 3],
    ['القاع صامد (لم يُكسر)', '—', a.base_broken ? 'تم كسر القاع ⚠️' : 'لم يُكسر', !a.base_broken],
  ];
  $('#anChecklist').innerHTML = items.map(([t, std, val, ok]) => `
    <div class="check ${ok ? 'ok' : (val === '—' ? 'pending' : 'pending')}">
      <span class="icon">${ok ? '✅' : '⏳'}</span>
      <div><b>${esc(t)}</b> <span class="muted small">(${esc(std)})</span><br>
      <span class="muted small">${esc(val)}</span></div>
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
  const rows = [
    ['سعر الافتتاح يوم التقسيم', money(a.rs_open)],
    ['قمة ما بعد التقسيم', money(a.spike_high) + (a.spike_date ? ` (${a.spike_date})` : '')],
    ['القاع المرجعي', money(a.bottom_price) + (a.bottom_date ? ` (${a.bottom_date})` : '')],
    ['نطاق القاعدة', a.base_range_pct != null ? n(a.base_range_pct, 1) + '%' : '—'],
    ['جلسات الثبات', a.base_sessions ?? '—'],
    ['جفاف الحجم', a.volume_dryup != null ? Math.round(a.volume_dryup * 100) + '%' : '—'],
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
    ['مصدر البيانات', a.data_source === 'live' ? 'سوق حقيقي' : 'تجريبي'],
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
  if (!cats.length) { $('#anNews').innerHTML = '<p class="muted">لا توجد أخبار أو محفزات مرصودة لهذا الرمز.</p>'; return; }
  const icon = { positive: '🟢', negative: '🔴', neutral: '⚪' };
  const kindAr = { offering: 'طرح أسهم', earnings: 'أرباح', compliance: 'امتثال', clinical: 'نتائج/تجارب',
    contract: 'عقود', other: 'أخرى', split: 'تقسيم', delay: 'تأخير إفصاح' };
  const dil = (a.dilution || []).map(d => `
    <div class="news-item ${d.severity === 'high' ? 'neg' : 'neu'}">
      <span class="sent">${d.severity === 'high' ? '🔻' : '📄'}</span>
      <div><div class="meta">تخفيف · ${esc(d.date)} · خطورة ${esc(d.severity)} · ${esc(d.source)}</div>
      <div class="title">${esc(d.title)}</div></div>
    </div>`).join('');
  const today = new Date().toISOString().slice(0, 10);
  $('#anNews').innerHTML = dil + cats.map(c => `
    <div class="news-item ${c.sentiment === 'positive' ? 'pos' : c.sentiment === 'negative' ? 'neg' : 'neu'}">
      <span class="sent">${icon[c.sentiment] || '⚪'}</span>
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
    layout: { background: { color: 'transparent' }, textColor: '#8b96a5', fontSize: 11 },
    grid: { vertLines: { color: '#1a212b' }, horzLines: { color: '#1a212b' } },
    rightPriceScale: { borderColor: '#242c38', scaleMargins: { top: .08, bottom: .22 } },
    timeScale: { borderColor: '#242c38', timeVisible: intraday, secondsVisible: false, rightOffset: 5 },
    crosshair: { mode: 0, vertLine: { color: '#388bfd', labelBackgroundColor: '#1f6feb' },
      horzLine: { color: '#388bfd', labelBackgroundColor: '#1f6feb' } },
    handleScroll: true, handleScale: true,
  };
}

function ensureCharts(intraday) {
  const priceEl = $('#priceChart'), oscEl = $('#oscChart');
  if (state.charts.price) { state.charts.price.remove(); state.charts.price = null; }
  if (state.charts.osc) { state.charts.osc.remove(); state.charts.osc = null; }
  state.charts.price = LightweightCharts.createChart(priceEl, chartOptions(priceEl, intraday));
  state.charts.osc = LightweightCharts.createChart(oscEl, {
    ...chartOptions(oscEl, intraday),
    rightPriceScale: { borderColor: '#242c38', scaleMargins: { top: .18, bottom: .12 } },
    timeScale: { ...chartOptions(oscEl, intraday).timeScale, visible: true },
  });
  const p = state.charts.price, o = state.charts.osc;
  p.timeScale().subscribeVisibleLogicalRangeChange(r => { if (r) o.timeScale().setVisibleLogicalRange(r); });
  o.timeScale().subscribeVisibleLogicalRangeChange(r => { if (r) p.timeScale().setVisibleLogicalRange(r); });
  state.series = { price: {}, osc: {} };
}

async function loadChart(sym, tf) {
  const payload = await api(`/api/chart/${sym}?tf=${tf}`);
  if (!payload.candles || !payload.candles.length) { toast('لا توجد بيانات رسم لهذا الرمز'); return; }
  renderChart(payload);
}

function renderChart(p) {
  const intraday = p.tf === '4H';
  ensureCharts(intraday);
  const s = state.series;
  s.price.candles = state.charts.price.addCandlestickSeries({
    upColor: COLORS.up, downColor: COLORS.down, borderUpColor: COLORS.up, borderDownColor: COLORS.down,
    wickUpColor: COLORS.up, wickDownColor: COLORS.down, priceLineVisible: true,
  });
  s.price.candles.setData(p.candles);
  s.price.vol = state.charts.price.addHistogramSeries({
    priceFormat: { type: 'volume' }, priceScaleId: 'vol', color: '#388bfd55',
  });
  state.charts.price.priceScale('vol').applyOptions({ scaleMargins: { top: .86, bottom: 0 } });
  s.price.vol.setData(p.volume || []);
  s.price.ema9 = state.charts.price.addLineSeries({ color: COLORS.ema9, lineWidth: 1.4, priceLineVisible: false, lastValueVisible: false });
  s.price.ema20 = state.charts.price.addLineSeries({ color: COLORS.ema20, lineWidth: 1.4, priceLineVisible: false, lastValueVisible: false });
  s.price.ema50 = state.charts.price.addLineSeries({ color: COLORS.ema50, lineWidth: 1.4, priceLineVisible: false, lastValueVisible: false });
  s.price.bbU = state.charts.price.addLineSeries({ color: '#6b768466', lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false });
  s.price.bbL = state.charts.price.addLineSeries({ color: '#6b768466', lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false });
  s.price.chU = state.charts.price.addLineSeries({ color: '#a371f766', lineWidth: 1.2, lineStyle: 1, priceLineVisible: false, lastValueVisible: false });
  s.price.chL = state.charts.price.addLineSeries({ color: '#a371f766', lineWidth: 1.2, lineStyle: 1, priceLineVisible: false, lastValueVisible: false });
  s.price.ema9.setData(p.overlays.ema9 || []);
  s.price.ema20.setData(p.overlays.ema20 || []);
  s.price.ema50.setData(p.overlays.ema50 || []);
  s.price.bbU.setData(p.overlays.bb_up || []);
  s.price.bbL.setData(p.overlays.bb_low || []);
  s.price.chU.setData(p.overlays.ch_up || []);
  s.price.chL.setData(p.overlays.ch_low || []);
  if (p.markers?.length) s.price.candles.setMarkers(p.markers);

  // مستويات الخطة
  const lv = p.levels || {}, t = lv.targets || {};
  const line = (price, color, title, style = 2) => price && s.price.candles.createPriceLine({
    price: Number(price), color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title,
  });
  line(lv.trigger, COLORS.trigger, 'اختراق');
  line(lv.stop, COLORS.stop, 'وقف');
  line(t.t1_382, COLORS.target, 'هدف1', 3);
  line(t.t2_618, COLORS.target, 'هدف2', 3);
  line(t.t3_full, '#7ee787', 'هدف3', 3);

  $('#chartLegend').innerHTML = ['EMA9 ' + COLORS.ema9, 'EMA20 ' + COLORS.ema20, 'EMA50 ' + COLORS.ema50]
    .map(x => { const [k, c] = x.split(' '); return `<span><i style="background:${c}"></i>${k}</span>`; })
    .join('') + `<span>عدد الشموع: ${p.meta?.bars || 0}</span><span>الإقفال: ${money(p.meta?.last_close)}</span>` +
    (p.meta?.rvol ? `<span>RVOL: ${n(p.meta.rvol, 2)}x</span>` : '');

  renderOscillator(p);
  state.charts.price.timeScale().fitContent();
  state.charts.osc.timeScale().fitContent();
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
    ch.priceScale('right').applyOptions({ autoScale: false });
    o.main.createPriceLine({ price: 70, color: '#f8514966', lineWidth: 1, lineStyle: 2, title: '70' });
    o.main.createPriceLine({ price: 30, color: '#3fb95066', lineWidth: 1, lineStyle: 2, title: '30' });
    ch.applyOptions({ rightPriceScale: { scaleMargins: { top: .12, bottom: .12 } } });
  } else if (kind === 'macd') {
    o.hist = ch.addHistogramSeries({ priceLineVisible: false, color: '#3fb95088' });
    o.hist.setData((osc.macd_hist || []).map(d => ({ ...d, color: d.value >= 0 ? '#3fb95088' : '#f8514988' })));
    o.main = ch.addLineSeries({ color: COLORS.macd, lineWidth: 1.4, priceLineVisible: false });
    o.main.setData(osc.macd || []);
    o.signal = ch.addLineSeries({ color: COLORS.signal, lineWidth: 1.2, priceLineVisible: false });
    o.signal.setData(osc.macd_signal || []);
  } else if (kind === 'stoch') {
    o.main = ch.addLineSeries({ color: COLORS.stoch, lineWidth: 1.5, priceLineVisible: false });
    o.main.setData(osc.stoch_k || []);
    o.signal = ch.addLineSeries({ color: COLORS.ema9, lineWidth: 1.2, priceLineVisible: false });
    o.signal.setData(osc.stoch_d || []);
    o.main.createPriceLine({ price: 80, color: '#f8514966', lineWidth: 1, lineStyle: 2 });
    o.main.createPriceLine({ price: 20, color: '#3fb95066', lineWidth: 1, lineStyle: 2 });
  } else if (kind === 'rvol') {
    const data = (osc.rvol || []).map(d => ({ ...d, color: d.value >= 1.5 ? '#3fb950aa' : '#388bfd55' }));
    o.hist = ch.addHistogramSeries({ priceLineVisible: false });
    o.hist.setData(data);
    o.main = ch.addLineSeries({ color: COLORS.ema9, lineWidth: 1, priceLineVisible: false });
    o.main.setData((osc.rvol || []).map(d => ({ time: d.time, value: 1.5 })));
  }
}

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
  if (state.selected) loadChart(state.selected, state.tf);
});

new ResizeObserver(() => {
  if (!state.charts.price) return;
  const pEl = $('#priceChart'), oEl = $('#oscChart');
  state.charts.price.applyOptions({ width: pEl.clientWidth, height: pEl.clientHeight });
  state.charts.osc.applyOptions({ width: oEl.clientWidth, height: oEl.clientHeight });
}).observe($('#tab-analysis'));

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
  $('#phasesList').innerHTML = [
    `<div class="phase-card" style="--pc:#5b6472"><h4>مستبعد (لم يستوفِ الشروط)</h4>
      <p>رموز لم تُحقق شرط التقسيم العكسي أو الهبوط أو التشبع أو الثبات.</p>
      <div class="cnt">${counts['0'] || 0}</div></div>`
  ].concat(list.map(([k, v]) => `
    <div class="phase-card" style="--pc:${v.color}">
      <h4>${esc(v.ar)}</h4><p>${esc(v.desc)}</p><div class="cnt">${counts[k] || 0}</div>
    </div>`)).join('');
}

function renderWeights() {
  const w = state.strategy?.params?.weights || {};
  const keys = state.strategy?.scoring_keys || {};
  $('#weightsList').innerHTML = Object.entries(w).map(([k, v]) => `
    <div class="w-row"><span>${esc(keys[k] || k)}</span><b>${v}</b>
      <div class="w-bar"><div class="w-fill" style="width:${v * 5}%"></div></div></div>`).join('');
}

function renderSources() {
  const d = state.sources || {};
  const rows = [
    ['وضع البيانات المُعدّ', d.mode_setting || '—'],
    ['الوضع الفعّال الآن', d.mode_effective === 'live' ? 'سوق حقيقي (live)' : 'تجريبي (demo)'],
    ['مكتبة yfinance مثبّتة', d.has_yfinance ? 'نعم' : 'لا'],
  ].concat((d.providers || []).map(p => [p.name, (p.ok ? '✔ ' : '✖ ') + (p.detail || (p.ok ? 'يعمل' : 'غير مفعّل'))]));
  const el = $('#sourcesTable');
  if (el) el.innerHTML = rows.map(([k, v]) => `<div class="row"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join('');
}

async function renderDemo() {
  try {
    const d = await api('/api/demo-metadata');
    $('#demoBody').innerHTML = d.scenarios.map(s => `<tr>
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
  $('#helpContent').innerHTML = `
  <p>هذه المنصة تنفّذ استراتيجية <b>«التقسيم العكسي + التشبع البيعي + الانعكاس»</b>،
  وتعمل على الفريم اليومي وفريم 4 ساعات مع نماذج التحليل الفني.</p>
  <h3>شروط الاستراتيجية (كما في ورقة الشروط)</h3>
  <table>
    <tr><th>#</th><th>الشرط</th><th>المعيار في النظام</th></tr>
    <tr><td>1</td><td>تقسيم عكسي حديث</td><td>نسبة 1:${p.rs_min_ratio || 2} حتى 1:${p.rs_max_ratio || 60} خلال آخر ${p.rs_lookback_days || 60} يوماً</td></tr>
    <tr><td>2</td><td>صعود يوم التقسيم</td><td>≥ ${p.min_spike_pct || 5}% (المثالي ${p.ideal_spike_pct || 15}%)</td></tr>
    <tr><td>3</td><td>موجة هبوط قوية</td><td>≥ ${p.min_drop_pct || 30}% (جيد ${p.good_drop_pct || 40}% / قوي ${p.strong_drop_pct || 50}%)</td></tr>
    <tr><td>4</td><td>تشبع بيعي</td><td>RSI &lt; ${p.rsi_oversold || 30} (عميق &lt; ${p.rsi_deep_oversold || 25})</td></tr>
    <tr><td>5</td><td>قاع وثبات</td><td>≥ ${p.min_base_sessions || 3} جلسات داخل ±${p.base_tolerance_pct || 9}%</td></tr>
    <tr><td>6</td><td>تحسّن RSI</td><td>RSI ≥ ${p.rsi_recovery_min || 35} أو +${p.rsi_recovery_delta || 5} عن قاعه</td></tr>
    <tr><td>7</td><td>تأكيد الانعكاس</td><td>إغلاقان فوق EMA9 + تحسّن MACD + اختراق القناة الهابطة + RVOL ≥ ${p.rvol_threshold || 1.5}x</td></tr>
  </table>
  <h3>التسلسل العملي</h3>
  <p>1) رصد التقسيم → 2) متابعة الهبوط والتشبع → 3) تكوّن القاع → 4) ثبات 3-5 جلسات (دخول قائمة المتابعة)
  → 5) تحسّن RSI → 6) انعكاس مؤكد (جاهز فنياً) → 7) بعد الاختراق (مرحلة التمدد: لا مطاردة).</p>
  <h3>قواعد إدارة المخاطر</h3>
  <ul>
    <li>لا شراء أثناء الهبوط (“لا نشتري سكيناً ساقطة”) — الدخول بعد ثبات القاع وتأكيد الاختراق.</li>
    <li>الوقف التداولي 8-20% من نقطة الدخول، ووقف إلغاء السيناريو تحت القاع المرجعي.</li>
    <li>أي كسر للقاع المرجعي يلغي السيناريو بالكامل حتى لو بدت المؤشرات متشبعة.</li>
    <li>الطرح الجديد (424B5/S-1/S-3) أو مذكرات شراء فعّالة = خطر تخفيف يخفض التقييم تلقائياً.</li>
    <li>الفلوت الصغير + البيع المكشوف العالي = وقود انفجار، لكن تضخّم السعر مفاجئ وعنيف.</li>
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
  setInterval(loadHealth, 60000);
})();
