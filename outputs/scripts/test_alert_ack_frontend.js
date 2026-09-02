/**
 * 前端预警确认逻辑回归测试（真实 HTTP + 真实前端函数）
 *
 * 复现用户场景：
 *   点击「确认」 -> 状态变已确认 -> 刷新页面（重新 fetch） -> 仍为已确认
 *
 * 做法：从 cockpit_frontend.html 中原样抽出 alert 相关函数，在最小 DOM 桩上执行，
 *       fetch 打到真实的后端服务，断言内存状态与后端状态一致。
 *
 * 用法（后端未启动时用 --spawn 自动拉起 8091 端口）:
 *   node outputs/scripts/test_alert_ack_frontend.js --spawn
 *
 * 注意：本测试会把预警置为「已确认」，重跑前需先复位（确认动作按审计要求不可撤销）:
 *   python outputs/scripts/reset_alert_acks.py
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { spawn } = require('child_process');
const { setTimeout: sleep } = require('timers/promises');

const ROOT = path.resolve(__dirname, '..');
const HTML = path.join(ROOT, 'cockpit_frontend.html');
const PORT = Number(process.env.TEST_PORT || 8091);
const BASE = `http://127.0.0.1:${PORT}`;
const SPAWN = process.argv.includes('--spawn');
const PY = process.env.TEST_PY || path.join(ROOT, '.venv', 'Scripts', 'python.exe');

let passed = 0, failed = 0;
function check(cond, msg) {
  if (cond) { passed++; console.log(`  [PASS] ${msg}`); }
  else { failed++; console.log(`  [FAIL] ${msg}`); }
}

/** 从 HTML 中按起止标记切出真实源码，保证测的是线上代码本身 */
function slice(src, startMark, endMark) {
  const i = src.indexOf(startMark);
  const j = src.indexOf(endMark, i);
  if (i < 0 || j < 0) throw new Error(`抽取失败: ${startMark} .. ${endMark}`);
  return src.slice(i, j);
}

function buildContext() {
  const els = {};
  const mkEl = (id) => ({
    id, innerHTML: '', textContent: '', disabled: false,
    dataset: {}, children: [], style: {},
    classList: { contains: () => false, add() {}, remove() {} },
    querySelectorAll: () => [], querySelector: () => null,
    addEventListener() {}, appendChild() {}, closest: () => null,
    clientWidth: 860, clientHeight: 120,
  });
  const document = {
    getElementById: (id) => (els[id] || (els[id] = mkEl(id))),
    querySelectorAll: () => [], querySelector: () => null,
    addEventListener() {},
  };
  const ctx = {
    document, console, fetch, setTimeout, clearTimeout, URL, JSON, Date, Math,
    location: { origin: BASE, host: `127.0.0.1:${PORT}`, protocol: 'http:' },
    confirm: () => true,
    alert: () => {},
  };
  ctx.globalThis = ctx;
  return { ctx, els };
}

const PRELUDE = `
var alerts = [];
var alFilter = 'all';
var API = ${JSON.stringify(BASE)};
function toast(msg){ console.log('   [toast] ' + msg); }
`;

async function waitServer(timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const r = await fetch(`${BASE}/api/health`);
      if (r.ok) return true;
    } catch (_) { /* retry */ }
    await sleep(500);
  }
  return false;
}

async function main() {
  const src = fs.readFileSync(HTML, 'utf8');

  const alertBlock = slice(src, 'function alertById', '// ==================== Page Rendering');
  const fetchBlock = slice(src, 'async function fetchAlertsData', '// ==================== WebSocket');

  const { ctx, els } = buildContext();
  vm.createContext(ctx);
  vm.runInContext(
    PRELUDE + alertBlock + '\n' + fetchBlock + '\n'
    + 'globalThis.__T = { alertById, ackLabel, renderAlerts, ackAlert, ackAllAlerts, '
    + 'ackHm, fetchAlertsData, getAlerts: () => alerts };',
    ctx,
  );
  const T = ctx.__T;

  console.log('[1] 拉取预警列表');
  await T.fetchAlertsData();
  let list = T.getAlerts();
  check(list.length > 0, `拉到 ${list.length} 条预警`);
  check(list.every((a) => a.id), '每条都拿到后端 id（此前无任何主键，是确认错对象的根因）');

  const active0 = list.filter((a) => a.act).length;
  if (active0 === 0) {
    console.error('\n当前没有活动中的预警，无法验证确认流程。请先复位：');
    console.error('  python outputs/scripts/reset_alert_acks.py\n');
    process.exit(1);
  }

  console.log('\n[2] 渲染 HTML：活动中应带确认按钮，已确认应显示确认人');
  T.renderAlerts();
  const html = els.alList.innerHTML;
  check(html.includes('data-id='), '列表项带 data-id（不再靠 DOM 序号反查）');
  check(html.includes('data-ack='), '活动中的预警渲染出确认按钮');
  check(els.alBadge.textContent === String(list.filter((a) => a.act).length),
    `角标计数 = 活动中条数（${els.alBadge.textContent}）`);
  check(els.ackAllCount.textContent === String(list.filter((a) => a.act).length),
    '「全部确认」按钮显示待确认条数');

  console.log('\n[3] 核心场景：确认一条 -> 模拟刷新 -> 重新拉取');
  const target = list.find((a) => a.act) || list[0];
  await T.ackAlert(target.id);
  const afterAck = T.alertById(target.id);
  check(afterAck.act === false, '点确认后前端状态立即变为非活动中');
  check(!!afterAck.ackBy, `记录了确认人：${afterAck.ackBy}`);

  // 模拟刷新页面：清空内存后重新拉取
  vm.runInContext('alerts = [];', ctx);
  await T.fetchAlertsData();
  const afterRefresh = T.alertById(target.id);
  check(!!afterRefresh, '刷新后仍能拉到该预警');
  check(afterRefresh.act === false, '★ 刷新后仍为非活动中（用户报的 bug 已修复）');
  check(afterRefresh.ackBy === afterAck.ackBy, `★ 刷新后确认人保留：${afterRefresh.ackBy}`);

  console.log('\n[4] 状态文案');
  check(T.ackLabel(afterRefresh).startsWith('✓ 已确认'), `文案为「${T.ackLabel(afterRefresh)}」`);
  check(T.ackLabel({ act: false, ackBy: null }) === '已恢复', '无确认人时兜底显示「已恢复」');

  console.log('\n[5] 全部确认');
  await T.ackAllAlerts();
  vm.runInContext('alerts = [];', ctx);
  await T.fetchAlertsData();
  const remain = T.getAlerts().filter((a) => a.act).length;
  check(remain === 0, `★ 刷新后活动中共 ${remain} 条（全部已持久化确认）`);

  console.log(`\n${'='.repeat(46)}\n通过 ${passed} 项，失败 ${failed} 项\n${'='.repeat(46)}`);
  return failed === 0 ? 0 : 1;
}

(async () => {
  let proc = null;
  if (SPAWN) {
    proc = spawn(PY, ['-m', 'uvicorn', 'lung_protection_cockpit.api:app',
      '--host', '127.0.0.1', '--port', String(PORT), '--log-level', 'warning'],
      { cwd: ROOT, stdio: 'ignore' });
    if (!(await waitServer())) { console.error('服务启动失败'); proc.kill(); process.exit(1); }
  }
  try {
    process.exit(await main());
  } catch (e) {
    console.error('测试异常:', e);
    process.exit(1);
  } finally {
    if (proc) proc.kill();
  }
})();
