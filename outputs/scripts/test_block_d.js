// 用真实趋势数据在 DOM 桩上跑 drawAreaChart / renderDpAdvice，验证不抛错且产出 SVG。
// 用法: node scripts/test_block_d.js <baseUrl>
const http = require('http');

const BASE = process.argv[2] || 'http://127.0.0.1:8091';

function get(path) {
  return new Promise((res, rej) => {
    http.get(BASE + path, r => {
      let b = ''; r.on('data', c => b += c); r.on('end', () => res(JSON.parse(b)));
    }).on('error', rej);
  });
}

// ---- minimal DOM stub ----
const SVGNS = 'http://www.w3.org/2000/svg';
function makeEl(tag) {
  return {
    tagName: tag, children: [], attrs: {}, innerHTML: '', textContent: '',
    clientWidth: 860,
    setAttribute(k, v) { this.attrs[k] = v; },
    getAttribute(k) { return this.attrs[k]; },
    appendChild(c) { this.children.push(c); return c; },
  };
}
const store = {};
function reg(id, h) { const e = makeEl('svg'); e.attrs.height = String(h); store[id] = e; return e; }
['dpChart','dpTip','mpChart','mpTip','dpEnergy','deTip','mpEnergy','meTip','dpStats','mpStats','dpSegs','mpSegs','dpCusum','dpAdvice','rmBig','rmTip'].forEach(id => reg(id, id.includes('Energy') ? 120 : 240));

global.document = {
  getElementById: id => store[id] || null,
  createElementNS: (ns, tag) => makeEl(tag),
  querySelectorAll: () => [],
  addEventListener: () => {},
};
global.window = { addEventListener: () => {} };
global.SVGNS = SVGNS;
global.console = console;

// ---- extracted helpers (mirrors cockpit_frontend.html) ----
function el(tag, attrs) { const e = makeEl(tag); for (const k in attrs) e.setAttribute(k, attrs[k]); return e; }
function txt(x, y, s, attrs) { const e = el('text', Object.assign({ x, y }, attrs || {})); e.textContent = s; return e; }

let data = [], N = 0;
const TH = { dp: 15, mp: 17 };

function drawAreaChart(svgId, tipId, win, key, validKey, unit, color, extraKey) {
  const svg = document.getElementById(svgId); if (!svg) return;
  svg.innerHTML = ''; svg.children = [];
  const W = svg.clientWidth || 860, H = +svg.getAttribute('height') || 120;
  const L = 52, R = 14, Tp = 8, B = 24, pw = W - L - R, ph = H - Tp - B;
  const arr = data.slice(Math.max(0, N - win));
  if (arr.length < 2) { svg.appendChild(txt(W / 2, H / 2, '数据不足', {})); return; }
  if (arr[0][key] == null) { svg.appendChild(txt(W / 2, H / 2, '数据不足', {})); return; }
  const base = arr[0][key], baseX = (extraKey && arr[0][extraKey] != null) ? arr[0][extraKey] : null;
  const inc = arr.map(d => (d[key] != null && d[validKey] !== false) ? (d[key] - base) : null);
  const vals = inc.filter(v => v != null);
  if (!vals.length) { svg.appendChild(txt(W / 2, H / 2, '数据不足', {})); return; }
  const hiV = Math.max.apply(null, vals);
  const loV = Math.min.apply(null, vals, 0);
  const span = (hiV - loV) || 1;
  const X = idx => L + idx / (arr.length - 1) * pw;
  const Y = v => Tp + ph - (v - loV) / span * ph;
  let pts = '';
  arr.forEach((d, idx) => { const v = inc[idx]; if (v == null) return; pts += (pts === '' ? '' : ' ') + X(idx).toFixed(1) + ',' + Y(v).toFixed(1); });
  if (pts) {
    svg.appendChild(el('path', { d: 'M' + pts.split(' ').join(' L'), fill: 'none', stroke: color, 'stroke-width': 2 }));
  }
  if (extraKey) {
    const eArr = []; let ok = false;
    arr.forEach((d, idx) => { if (d[extraKey] == null || baseX == null || d[validKey] === false) return; const v = d[extraKey] - baseX; if (v > 0.001) ok = true; eArr.push({ x: X(idx), y: Y(v) }); });
    if (ok && eArr.length > 1) svg.appendChild(el('path', { d: 'M' + eArr.map(p => p.x.toFixed(1) + ',' + p.y.toFixed(1)).join(' L'), stroke: '#EF4444' }));
  }
  return { hiV, loV, n: vals.length, children: svg.children.length };
}

function renderDpAdvice(win, el2) {
  const arr = data.slice(Math.max(0, N - win)).filter(d => d.dpValid);
  if (!arr.length) { return { empty: true }; }
  const last = data[N - 1] || {};
  const tatH = (last.cumDp != null && arr[0].cumDp != null) ? (last.cumDp - arr[0].cumDp) : null;
  const overH = (last.cumDpOver != null && arr[0].cumDpOver != null) ? (last.cumDpOver - arr[0].cumDpOver) : null;
  const peak = Math.max.apply(null, arr.map(d => d.dp));
  return { tatH, overH, peak, lines: 4 };
}

// ---- build data from real API ----
(async () => {
  const [dpRes, mpRes] = await Promise.all([get('/api/dp/trend?hours=24&points=1440'), get('/api/mp/trend?hours=24&points=1440')]);
  const dpS = dpRes.series || [], mpS = mpRes.series || [];
  const len = Math.max(dpS.length, mpS.length);
  data = []; let cumE = 0, cumDp = 0, cumDpOver = 0;
  for (let i = 0; i < len; i++) {
    const dV = dpS[i] ? dpS[i].value : null, mV = mpS[i] ? mpS[i].value : null;
    const dt = dpS[i] ? dpS[i].dt : (mpS[i] ? mpS[i].dt : '');
    let t = ''; if (dt) { const d = new Date(dt); t = ('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2); }
    const dOk = dV !== null && dV !== undefined, mOk = mV !== null && mV !== undefined;
    if (mOk) cumE += mV;
    if (dOk) { cumDp += dV; if (dV >= TH.dp) cumDpOver += (dV - TH.dp); }
    data.push({ i, t, dt, dp: dOk ? dV : 0, mp: mOk ? mV : 0, dpValid: dOk, mpValid: mOk,
      cum: cumE / 1000, cumDp: cumDp / 60, cumDpOver: cumDpOver / 60 });
  }
  N = data.length;

  const fails = [];
  const ck = (c, l, e) => { console.log((c ? '  [OK] ' : '  [FAIL] ') + l + (e !== undefined ? '  ' + JSON.stringify(e) : '')); if (!c) fails.push(l); };

  console.log('数据点 N = ' + N);
  ck(N > 0, '有趋势数据点');
  ck(data.every(d => d.cumDp != null && isFinite(d.cumDp)), 'cumDp 全为有限数');
  ck(data.every(d => d.cumDpOver != null && isFinite(d.cumDpOver)), 'cumDpOver 全为有限数');
  ck(data.every((d, i) => i === 0 || d.cumDp >= data[i-1].cumDp - 1e-9), 'cumDp 单调不减');
  ck(data.every((d, i) => i === 0 || d.cumDpOver >= data[i-1].cumDpOver - 1e-9), 'cumDpOver 单调不减');

  console.log('\n-- ΔP 累积曲线 (cumDp + 超阈虚线) --');
  const r1 = drawAreaChart('dpEnergy', 'deTip', 1440, 'cumDp', 'dpValid', 'cmH₂O·h', '#3B82F6', 'cumDpOver');
  console.log('  ', JSON.stringify(r1));
  ck(r1 && r1.n > 0, 'dpEnergy 有有效点');
  ck(r1 && r1.children >= 1, 'dpEnergy 产出图形元素', r1 && r1.children);

  console.log('\n-- MP 累积曲线 --');
  const r2 = drawAreaChart('mpEnergy', 'meTip', 1440, 'cum', 'mpValid', 'kJ', '#A855F7');
  console.log('  ', JSON.stringify(r2));
  ck(r2 && r2.n > 0, 'mpEnergy 有有效点');

  console.log('\n-- ΔP 解读建议 --');
  const a = renderDpAdvice(1440, null);
  console.log('  ', JSON.stringify(a));
  ck(a && !a.empty, '建议生成成功');
  ck(a && a.peak != null && isFinite(a.peak), '峰值有限');

  console.log('\n-- 空数据健壮性 --');
  const save = data; data = []; N = 0;
  let threw = false;
  try { drawAreaChart('dpEnergy', 'deTip', 1440, 'cumDp', 'dpValid', 'cmH₂O·h', '#3B82F6', 'cumDpOver'); } catch (e) { threw = true; }
  ck(!threw, '空数据不抛错');
  data = save; N = save.length;

  console.log('\n===== 块D：' + (fails.length ? '失败 ' + fails.length + ' 项 ' + JSON.stringify(fails) : '全部通过') + ' =====');
  process.exit(fails.length ? 1 : 0);
})().catch(e => { console.error('测试异常:', e); process.exit(1); });
