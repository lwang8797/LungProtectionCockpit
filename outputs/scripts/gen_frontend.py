# -*- coding: utf-8 -*-
"""
gen_frontend.py - 生成肺保护驾驶舱前端 HTML（M5: 对接真实 REST + WebSocket API）

生成文件: outputs/cockpit_frontend.html
特性:
  - 深色主题，15寸触摸屏 WebUI（1366×768）
  - REST API 取数 (/api/overview, /api/dp/trend, /api/mp/trend, /api/risk-map, /api/alerts)
  - WebSocket 实时推送 (/ws) → 总览页仪表盘自动刷新
  - 6 个页面: 总览/ΔP趋势/MP趋势/二维风险图/预警中心/设置
  - 纯原生 JS + SVG，无外部依赖
"""

import os

OUTPUT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML_PATH = os.path.join(OUTPUT_DIR, "cockpit_frontend.html")

HTML = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>肺保护驾驶舱</title>
<style>
/* ── 全局重置 ── */
*{margin:0;padding:0;box-sizing:border-box}
html,body{height:100%;overflow:hidden}
body{font-family:"Segoe UI","Microsoft YaHei",sans-serif;background:#07090D;color:#E8ECF1;font-size:14px}

/* ── 色板 ── */
:root{
  --bg0:#07090D;--bg1:#0E1218;--bg2:#151A22;--bg3:#1C2330;
  --tx0:#E8ECF1;--tx1:#A0AAB8;--tx2:#6B7684;
  --grn:#22C55E;--ylw:#EAB308;--org:#F97316;--red:#EF4444;--pur:#A855F7;
  --blu:#3B82F6;--cy:#06B6D4;
}

/* ── 布局 ── */
#app{display:flex;flex-direction:column;height:100vh}

/* 顶栏 */
.topbar{height:52px;background:linear-gradient(90deg,#0E1218,#151A22);border-bottom:1px solid #1C2330;display:flex;align-items:center;padding:0 20px;gap:16px;flex-shrink:0}
.topbar .logo{font-size:18px;font-weight:700;color:#06B6D4;letter-spacing:1px}
.topbar .dev{font-size:12px;color:#6B7684;border-left:1px solid #2A3242;padding-left:12px}
.topbar .spacer{flex:1}
.ws-dot{width:8px;height:8px;border-radius:50%;background:#EF4444;transition:.3s}
.ws-dot.on{background:#22C55E}
.ws-label{font-size:11px;color:#6B7684}
.topbar .clock{font-size:13px;color:#A0AAB8;font-variant-numeric:tabular-nums}

/* 导航 */
.nav{height:44px;background:#0E1218;border-bottom:1px solid #1C2330;display:flex;padding:0 16px;flex-shrink:0}
.nav-item{padding:0 18px;height:100%;display:flex;align-items:center;cursor:pointer;color:#6B7684;font-size:13px;border-bottom:2px solid transparent;transition:.2s;user-select:none}
.nav-item:hover{color:#A0AAB8}
.nav-item.active{color:#06B6D4;border-bottom-color:#06B6D4}

/* 内容区 */
.content{flex:1;overflow-y:auto;padding:16px 20px}
.page{display:none;height:100%}
.page.active{display:block}

/* 卡片 */
.card{background:linear-gradient(180deg,#0E1218,#151A22);border:1px solid #1C2330;border-radius:12px;padding:16px;margin-bottom:14px}
.card-title{font-size:12px;color:#6B7684;margin-bottom:10px;text-transform:uppercase;letter-spacing:.5px}

/* 评级横幅 */
.rating-band{display:flex;align-items:center;justify-content:space-between;padding:14px 20px;border-radius:10px;margin-bottom:16px}
.rating-band .lvl{font-size:28px;font-weight:800;letter-spacing:1px}
.rating-band .sub{font-size:13px;opacity:.8}
.rating-band.lv1{background:linear-gradient(90deg,rgba(34,197,94,.12),rgba(34,197,94,.03));border:1px solid rgba(34,197,94,.3)}
.rating-band.lv1 .lvl{color:#22C55E}
.rating-band.lv2{background:linear-gradient(90deg,rgba(234,179,8,.12),rgba(234,179,8,.03));border:1px solid rgba(234,179,8,.3)}
.rating-band.lv2 .lvl{color:#EAB308}
.rating-band.lv3{background:linear-gradient(90deg,rgba(249,115,22,.12),rgba(249,115,22,.03));border:1px solid rgba(249,115,22,.3)}
.rating-band.lv3 .lvl{color:#F97316}
.rating-band.lv4{background:linear-gradient(90deg,rgba(239,68,68,.15),rgba(239,68,68,.04));border:1px solid rgba(239,68,68,.4)}
.rating-band.lv4 .lvl{color:#EF4444}

/* 仪表盘网格 */
.gauge-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}
.gauge-card{background:linear-gradient(180deg,#0E1218,#151A22);border:1px solid #1C2330;border-radius:12px;padding:16px;text-align:center}
.gauge-card .gauge-svg{width:100%;max-width:280px;height:140px;margin:0 auto}
.gauge-card .gauge-num{font-size:36px;font-weight:800;font-variant-numeric:tabular-nums;line-height:1}
.gauge-card .gauge-unit{font-size:14px;color:#6B7684;margin-top:2px}
.gauge-card .gauge-hint{font-size:12px;color:#A0AAB8;margin-top:8px}
.gauge-card .gauge-stats{display:flex;justify-content:space-around;margin-top:10px;padding-top:10px;border-top:1px solid #1C2330}
.gauge-card .gauge-stat{text-align:center}
.gauge-card .gauge-stat .v{font-size:16px;font-weight:700;font-variant-numeric:tabular-nums}
.gauge-card .gauge-stat .l{font-size:10px;color:#6B7684;margin-top:2px}

/* 综合卡片行 */
.summary-row{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px}
.summary-card{background:linear-gradient(180deg,#0E1218,#151A22);border:1px solid #1C2330;border-radius:12px;padding:14px;text-align:center}
.summary-card .s-val{font-size:24px;font-weight:800;font-variant-numeric:tabular-nums;color:#06B6D4}
.summary-card .s-label{font-size:11px;color:#6B7684;margin-top:4px}
.summary-card .s-sub{font-size:10px;color:#6B7684;margin-top:2px}

/* 趋势图 */
.chart-wrap{position:relative;width:100%}
.chart-svg{width:100%;height:340px}
.chart-stats{display:flex;gap:24px;padding:8px 0}
.chart-stat .v{font-size:18px;font-weight:700;font-variant-numeric:tabular-nums}
.chart-stat .l{font-size:11px;color:#6B7684}
.window-sel{display:flex;gap:6px;float:right}
.window-sel button{background:#1C2330;border:1px solid #2A3242;color:#6B7684;padding:4px 12px;border-radius:6px;cursor:pointer;font-size:11px}
.window-sel button.active{background:#06B6D4;color:#07090D;border-color:#06B6D4;font-weight:600}

/* 风险图 */
.risk-svg{width:100%;height:400px}

/* 预警列表 */
.alert-empty{text-align:center;padding:40px;color:#6B7684}
.alert-list{display:flex;flex-direction:column;gap:8px}
.alert-item{display:flex;align-items:center;gap:12px;padding:10px 14px;background:#0E1218;border:1px solid #1C2330;border-radius:8px}
.alert-item .badge{width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;flex-shrink:0}
.alert-item .badge.lv2{background:rgba(234,179,8,.15);color:#EAB308}
.alert-item .badge.lv3{background:rgba(249,115,22,.15);color:#F97316}
.alert-item .badge.lv4{background:rgba(239,68,68,.15);color:#EF4444}
.alert-item .msg{flex:1;font-size:13px;color:#E8ECF1}
.alert-item .ts{font-size:11px;color:#6B7684;font-variant-numeric:tabular-nums}

/* 设置 */
.settings-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;max-width:700px}
.setting-row{display:flex;justify-content:space-between;align-items:center;padding:10px 0;border-bottom:1px solid #1C2330}
.setting-row .l{color:#A0AAB8;font-size:13px}
.setting-row .v{font-weight:600;font-variant-numeric:tabular-nums}

/* SVG text */
svg text{font-family:"Segoe UI","Microsoft YaHei",sans-serif}

/* 加载 */
.loading{display:flex;align-items:center;justify-content:center;height:200px;color:#6B7684}
</style>
</head>
<body>
<div id="app">
  <!-- 顶栏 -->
  <div class="topbar">
    <span class="logo">🫁 肺保护驾驶舱</span>
    <span class="dev" id="devInfo">--</span>
    <span class="spacer"></span>
    <span class="ws-dot" id="wsDot"></span>
    <span class="ws-label" id="wsLabel">未连接</span>
    <span class="clock" id="clock">--:--:--</span>
  </div>

  <!-- 导航 -->
  <div class="nav">
    <div class="nav-item active" data-page="ov" onclick="gotoPage('ov')">总览</div>
    <div class="nav-item" data-page="dp" onclick="gotoPage('dp')">ΔP 趋势</div>
    <div class="nav-item" data-page="mp" onclick="gotoPage('mp')">MP 趋势</div>
    <div class="nav-item" data-page="rm" onclick="gotoPage('rm')">二维风险图</div>
    <div class="nav-item" data-page="al" onclick="gotoPage('al')">预警中心</div>
    <div class="nav-item" data-page="st" onclick="gotoPage('st')">设置</div>
  </div>

  <!-- 内容区 -->
  <div class="content">

    <!-- ── 总览 ── -->
    <div class="page active" id="page-ov">
      <div class="rating-band lv1" id="ratingBand">
        <div>
          <span class="lvl" id="riskLvl">L1 正常</span>
        </div>
        <div class="sub" id="riskSub">保护性通气，ΔP 与 MP 均在安全范围</div>
      </div>

      <div class="gauge-grid">
        <!-- ΔP 仪表盘 -->
        <div class="gauge-card">
          <div class="card-title">ΔP 驱动压</div>
          <svg class="gauge-svg" id="dpGauge" viewBox="0 0 280 140"></svg>
          <div class="gauge-num" id="dpNum" style="color:#E8ECF1">--</div>
          <div class="gauge-unit">cmH₂O</div>
          <div class="gauge-hint" id="dpHint">等待数据...</div>
          <div class="gauge-stats">
            <div class="gauge-stat"><div class="v" id="dpMax">--</div><div class="l">最大</div></div>
            <div class="gauge-stat"><div class="v" id="dpMean">--</div><div class="l">均值</div></div>
            <div class="gauge-stat"><div class="v" id="dpOver">--</div><div class="l">超阈%</div></div>
          </div>
        </div>
        <!-- MP 仪表盘 -->
        <div class="gauge-card">
          <div class="card-title">MP 机械功率</div>
          <svg class="gauge-svg" id="mpGauge" viewBox="0 0 280 140"></svg>
          <div class="gauge-num" id="mpNum" style="color:#E8ECF1">--</div>
          <div class="gauge-unit">J/min</div>
          <div class="gauge-hint" id="mpHint">等待数据...</div>
          <div class="gauge-stats">
            <div class="gauge-stat"><div class="v" id="mpMax">--</div><div class="l">最大</div></div>
            <div class="gauge-stat"><div class="v" id="mpMean">--</div><div class="l">均值</div></div>
            <div class="gauge-stat"><div class="v" id="mpOver">--</div><div class="l">超阈%</div></div>
          </div>
        </div>
      </div>

      <div class="summary-row">
        <div class="summary-card">
          <div class="s-val" id="sumEnergy">--</div>
          <div class="s-label">累积能量</div>
          <div class="s-sub">Joule</div>
        </div>
        <div class="summary-card">
          <div class="s-val" id="sumVent">--</div>
          <div class="s-label">通气时长</div>
          <div class="s-sub">minutes</div>
        </div>
        <div class="summary-card">
          <div class="s-val" id="sumSource">--</div>
          <div class="s-label">数据来源</div>
          <div class="s-sub">source</div>
        </div>
      </div>
    </div>

    <!-- ── ΔP 趋势 ── -->
    <div class="page" id="page-dp">
      <div class="card">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
          <div class="card-title" style="margin:0">ΔP 驱动压趋势</div>
          <div class="window-sel" id="dpWindow">
            <button onclick="loadTrend('dp',2)" class="active">2h</button>
            <button onclick="loadTrend('dp',6)">6h</button>
            <button onclick="loadTrend('dp',24)">24h</button>
          </div>
        </div>
        <div class="chart-stats">
          <div class="chart-stat"><span class="v" id="dpTrendCur">--</span><span class="l">当前</span></div>
          <div class="chart-stat"><span class="v" id="dpTrendMax">--</span><span class="l">最大</span></div>
          <div class="chart-stat"><span class="v" id="dpTrendMean">--</span><span class="l">均值</span></div>
        </div>
        <div class="chart-wrap"><svg class="chart-svg" id="dpChart" viewBox="0 0 800 340"></svg></div>
      </div>
    </div>

    <!-- ── MP 趋势 ── -->
    <div class="page" id="page-mp">
      <div class="card">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
          <div class="card-title" style="margin:0">MP 机械功率趋势</div>
          <div class="window-sel" id="mpWindow">
            <button onclick="loadTrend('mp',2)" class="active">2h</button>
            <button onclick="loadTrend('mp',6)">6h</button>
            <button onclick="loadTrend('mp',24)">24h</button>
          </div>
        </div>
        <div class="chart-stats">
          <div class="chart-stat"><span class="v" id="mpTrendCur">--</span><span class="l">当前</span></div>
          <div class="chart-stat"><span class="v" id="mpTrendMax">--</span><span class="l">最大</span></div>
          <div class="chart-stat"><span class="v" id="mpTrendMean">--</span><span class="l">均值</span></div>
        </div>
        <div class="chart-wrap"><svg class="chart-svg" id="mpChart" viewBox="0 0 800 340"></svg></div>
      </div>
    </div>

    <!-- ── 二维风险图 ── -->
    <div class="page" id="page-rm">
      <div class="card">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
          <div class="card-title" style="margin:0">ΔP - MP 二维风险图</div>
          <div class="window-sel">
            <button onclick="loadRiskMap(2)" class="active">2h</button>
            <button onclick="loadRiskMap(6)">6h</button>
            <button onclick="loadRiskMap(24)">24h</button>
          </div>
        </div>
        <div class="chart-wrap"><svg class="risk-svg" id="riskChart" viewBox="0 0 800 400"></svg></div>
      </div>
    </div>

    <!-- ── 预警中心 ── -->
    <div class="page" id="page-al">
      <div class="card">
        <div class="card-title">预警事件</div>
        <div id="alertList" class="alert-list">
          <div class="alert-empty">加载中...</div>
        </div>
      </div>
    </div>

    <!-- ── 设置 ── -->
    <div class="page" id="page-st">
      <div class="card">
        <div class="card-title">系统设置</div>
        <div class="settings-grid">
          <div>
            <div class="setting-row"><span class="l">ΔP 阈值</span><span class="v" style="color:#22C55E">15.0 cmH₂O</span></div>
            <div class="setting-row"><span class="l">MP 阈值</span><span class="v" style="color:#22C55E">17.0 J/min</span></div>
            <div class="setting-row"><span class="l">采样间隔</span><span class="v">4 秒</span></div>
            <div class="setting-row"><span class="l">聚合粒度</span><span class="v">1 分钟</span></div>
          </div>
          <div>
            <div class="setting-row"><span class="l">设备 ID</span><span class="v" id="setDev">--</span></div>
            <div class="setting-row"><span class="l">MongoDB</span><span class="v" id="setMongo">--</span></div>
            <div class="setting-row"><span class="l">数据时间范围</span><span class="v" id="setRange" style="font-size:11px">--</span></div>
            <div class="setting-row"><span class="l">WebSocket</span><span class="v" id="setWs">--</span></div>
          </div>
        </div>
      </div>
    </div>

  </div>
</div>

<script>
// ═══════════════════════════════════════════
// 全局状态
// ═══════════════════════════════════════════
const API = location.origin;
let ws = null;
let curHours = 2;

// 风险颜色映射
const RISK_COLORS = {
  green:'#22C55E', yellow:'#EAB308', orange:'#F97316', red:'#EF4444', purple:'#A855F7'
};

// ═══════════════════════════════════════════
// 时钟
// ═══════════════════════════════════════════
function tickClock(){
  const d = new Date();
  const p = n => String(n).padStart(2,'0');
  document.getElementById('clock').textContent = p(d.getHours())+':'+p(d.getMinutes())+':'+p(d.getSeconds());
}
setInterval(tickClock, 1000); tickClock();

// ═══════════════════════════════════════════
// 导航
// ═══════════════════════════════════════════
function gotoPage(id){
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n=>n.classList.remove('active'));
  document.getElementById('page-'+id).classList.add('active');
  document.querySelector('.nav-item[data-page="'+id+'"]').classList.add('active');
  // 懒加载
  if(id==='dp') loadTrend('dp', curHours);
  if(id==='mp') loadTrend('mp', curHours);
  if(id==='rm') loadRiskMap(curHours);
  if(id==='al') loadAlerts();
  if(id==='st') loadSettings();
}

// ═══════════════════════════════════════════
// 仪表盘 SVG 绘制
// ═══════════════════════════════════════════
function drawGauge(svgId, val, threshold, lo, hi, unit){
  const svg = document.getElementById(svgId);
  if(!svg) return;
  const W=280, H=140, cx=W/2, cy=H-10, R=110, r=82;
  const startAng=Math.PI, endAng=0; // 半圆从左到右
  // 角度计算：值映射到 [startAng, endAng]
  const t = Math.max(0, Math.min(1, (val-lo)/(hi-lo)));
  const valAng = startAng + t*(endAng-startAng);
  // 阈值角度
  const thT = (threshold-lo)/(hi-lo);
  const thAng = startAng + thT*(endAng-startAng);

  // 弧坐标辅助
  const pt = (ang, rad) => [cx + rad*Math.cos(ang), cy - rad*Math.sin(ang)];
  // arc path
  const arcPath = (a0, a1, rad) => {
    const [x0,y0] = pt(a0, rad);
    const [x1,y1] = pt(a1, rad);
    const large = Math.abs(a1-a0) > Math.PI ? 1 : 0;
    const sweep = a1 < a0 ? 1 : 0;
    return `M ${x0} ${y0} A ${rad} ${rad} 0 ${large} ${sweep} ${x1} ${y1}`;
  };

  let html = '';
  // 背景弧（灰）
  html += `<path d="${arcPath(startAng, endAng, R)}" fill="none" stroke="#1C2330" stroke-width="14" stroke-linecap="round"/>`;
  // 分区：绿(0~阈值前2单位) 黄(阈值前2) 橙(阈值~阈值*1.3) 红(>阈值*1.3)
  const preAlert = threshold - 2; // 阈值前2单位
  const dangerMul = threshold * 1.3;
  const segs = [
    {from:lo, to:Math.max(lo,preAlert), color:RISK_COLORS.green},
    {from:Math.max(lo,preAlert), to:threshold, color:RISK_COLORS.yellow},
    {from:threshold, to:Math.min(hi,dangerMul), color:RISK_COLORS.orange},
    {from:Math.min(hi,dangerMul), to:hi, color:RISK_COLORS.red},
  ];
  segs.forEach(s=>{
    if(s.to <= s.from) return;
    const a0 = startAng + ((s.from-lo)/(hi-lo))*(endAng-startAng);
    const a1 = startAng + ((s.to-lo)/(hi-lo))*(endAng-startAng);
    const [x0,y0] = pt(a0, R);
    const [x1,y1] = pt(a1, R);
    const large = Math.abs(a1-a0) > Math.PI ? 1 : 0;
    html += `<path d="M ${x0} ${y0} A ${R} ${R} 0 ${large} 0 ${x1} ${y1}" fill="none" stroke="${s.color}" stroke-width="14" opacity="0.35"/>`;
  });

  // 值弧（高亮）
  const [vx0,vy0] = pt(startAng, R);
  const [vx1,vy1] = pt(valAng, R);
  const vLarge = Math.abs(valAng-startAng) > Math.PI ? 1 : 0;
  let valColor = RISK_COLORS.green;
  if(val >= threshold*1.3) valColor = RISK_COLORS.red;
  else if(val >= threshold) valColor = RISK_COLORS.orange;
  else if(val >= preAlert) valColor = RISK_COLORS.yellow;
  html += `<path d="M ${vx0} ${vy0} A ${R} ${R} 0 ${vLarge} 0 ${vx1} ${vy1}" fill="none" stroke="${valColor}" stroke-width="14" stroke-linecap="round"/>`;

  // 阈值刻度线
  const [tx,ty] = pt(thAng, R+8);
  const [tx2,ty2] = pt(thAng, R-8);
  html += `<line x1="${tx}" y1="${ty}" x2="${tx2}" y2="${ty2}" stroke="#E8ECF1" stroke-width="2"/>`;
  html += `<text x="${tx2}" y="${ty2-4}" fill="#6B7684" font-size="9" text-anchor="middle">${threshold}</text>`;

  // 指针
  const [px,py] = pt(valAng, R-22);
  html += `<line x1="${cx}" y1="${cy}" x2="${px}" y2="${py}" stroke="${valColor}" stroke-width="3" stroke-linecap="round"/>`;
  html += `<circle cx="${cx}" cy="${cy}" r="5" fill="${valColor}"/>`;

  svg.innerHTML = html;
}

// ═══════════════════════════════════════════
// 总览数据渲染
// ═══════════════════════════════════════════
function renderOverview(d){
  if(!d || d.error) return;

  // 评级横幅
  const band = document.getElementById('ratingBand');
  band.className = 'rating-band lv' + d.risk_level;
  document.getElementById('riskLvl').textContent = d.risk_label;
  let subText = '保护性通气，ΔP 与 MP 均在安全范围';
  if(d.risk_level >= 3) subText = '⚠ 存在超阈风险，建议调整参数';
  else if(d.risk_level === 2) subText = '关注：接近阈值上限';
  document.getElementById('riskSub').textContent = subText;

  // ΔP 仪表盘
  const dpCur = d.dp.current ?? 0;
  drawGauge('dpGauge', dpCur, d.dp.threshold, 0, 25, 'cmH₂O');
  document.getElementById('dpNum').textContent = dpCur !== null ? dpCur.toFixed(1) : '--';
  document.getElementById('dpHint').textContent = dpCur >= d.dp.threshold ? '⚠ 超阈值！' : (dpCur >= d.dp.threshold-2 ? '接近阈值，关注' : '安全范围');
  document.getElementById('dpMax').textContent = d.dp.max?.toFixed(1) ?? '--';
  document.getElementById('dpMean').textContent = d.dp.mean?.toFixed(1) ?? '--';
  document.getElementById('dpOver').textContent = (d.dp.over_pct ?? 0).toFixed(0) + '%';

  // MP 仪表盘
  const mpCur = d.mp.current ?? 0;
  drawGauge('mpGauge', mpCur, d.mp.threshold, 0, 30, 'J/min');
  document.getElementById('mpNum').textContent = mpCur !== null ? mpCur.toFixed(1) : '--';
  document.getElementById('mpHint').textContent = mpCur >= d.mp.threshold ? '⚠ 超阈值！' : (mpCur >= d.mp.threshold-3 ? '接近阈值，关注' : '安全范围');
  document.getElementById('mpMax').textContent = d.mp.max?.toFixed(2) ?? '--';
  document.getElementById('mpMean').textContent = d.mp.mean?.toFixed(2) ?? '--';
  document.getElementById('mpOver').textContent = (d.mp.over_pct ?? 0).toFixed(0) + '%';

  // 累积
  document.getElementById('sumEnergy').textContent = (d.cumulative?.energy_j ?? 0).toFixed(1);
  document.getElementById('sumVent').textContent = (d.cumulative?.vent_duration_min ?? 0).toFixed(0);
  document.getElementById('sumSource').textContent = d.source === 'metrics_1min' ? '聚合表' : '实时';
  document.getElementById('devInfo').textContent = d.device;
}

// ═══════════════════════════════════════════
// REST: 总览
// ═══════════════════════════════════════════
async function fetchOverview(){
  try{
    const r = await fetch(API + '/api/overview');
    const d = await r.json();
    renderOverview(d);
  }catch(e){ console.error('overview', e); }
}

// ═══════════════════════════════════════════
// 趋势图绘制
// ═══════════════════════════════════════════
function drawTrendChart(svgId, series, threshold, color){
  const svg = document.getElementById(svgId);
  if(!svg || !series || series.length === 0) return;

  const W=800, H=340, padL=50, padR=20, padT=20, padB=40;
  const cw = W - padL - padR, ch = H - padT - padB;

  // 值范围
  const vals = series.map(s => s.value).filter(v => v !== null && v !== undefined);
  if(vals.length === 0){ svg.innerHTML = '<text x="400" y="170" fill="#6B7684" text-anchor="middle">无数据</text>'; return; }
  let vMin = Math.min(...vals, 0), vMax = Math.max(...vals, threshold * 1.2);
  if(vMax === vMin) vMax = vMin + 1;
  vMax = Math.ceil(vMax / 5) * 5;

  // 时间范围
  const tsMin = series[0].ts, tsMax = series[series.length-1].ts;
  if(tsMax === tsMin){ svg.innerHTML = '<text x="400" y="170" fill="#6B7684" text-anchor="middle">数据点不足</text>'; return; }

  const xScale = ts => padL + ((ts - tsMin) / (tsMax - tsMin)) * cw;
  const yScale = v => padT + ch - ((v - vMin) / (vMax - vMin)) * ch;

  let html = '';

  // 网格线
  for(let i=0; i<=5; i++){
    const y = padT + (ch/5)*i;
    const val = vMax - (vMax-vMin)*(i/5);
    html += `<line x1="${padL}" y1="${y}" x2="${W-padR}" y2="${y}" stroke="#1C2330" stroke-width="1"/>`;
    html += `<text x="${padL-8}" y="${y+4}" fill="#6B7684" font-size="10" text-anchor="end">${val.toFixed(1)}</text>`;
  }

  // 阈值线
  const yTh = yScale(threshold);
  html += `<line x1="${padL}" y1="${yTh}" x2="${W-padR}" y2="${yTh}" stroke="#EF4444" stroke-width="1.5" stroke-dasharray="6 4"/>`;
  html += `<text x="${W-padR-4}" y="${yTh-4}" fill="#EF4444" font-size="10" text-anchor="end">阈值 ${threshold}</text>`;

  // 数据线
  let pathD = '', areaD = '';
  let firstX = padL, lastX = padL;
  series.forEach((s, i) => {
    if(s.value === null || s.value === undefined) return;
    const x = xScale(s.ts), y = yScale(s.value);
    if(!pathD){ pathD = `M ${x} ${y}`; firstX = x; }
    else pathD += ` L ${x} ${y}`;
    lastX = x;
  });
  // 填充区域
  const yBase = padT + ch;
  areaD = pathD + ` L ${lastX} ${yBase} L ${firstX} ${yBase} Z`;

  html += `<path d="${areaD}" fill="${color}" opacity="0.08"/>`;
  html += `<path d="${pathD}" fill="none" stroke="${color}" stroke-width="2"/>`;

  // X 轴时间标签
  for(let i=0; i<5; i++){
    const t = tsMin + (tsMax-tsMin)*(i/4);
    const x = xScale(t);
    const d = new Date(t);
    const lbl = String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');
    html += `<text x="${x}" y="${H-padB+18}" fill="#6B7684" font-size="10" text-anchor="middle">${lbl}</text>`;
  }

  // 边框
  html += `<rect x="${padL}" y="${padT}" width="${cw}" height="${ch}" fill="none" stroke="#1C2330" stroke-width="1"/>`;

  svg.innerHTML = html;
}

// ═══════════════════════════════════════════
// REST: 趋势
// ═══════════════════════════════════════════
async function loadTrend(type, hours){
  curHours = hours;
  // 更新按钮状态
  const btns = document.querySelectorAll('#'+type+'Window button');
  btns.forEach(b => b.classList.toggle('active', parseInt(b.textContent) === hours));

  const endpoint = type === 'dp' ? '/api/dp/trend' : '/api/mp/trend';
  const threshold = type === 'dp' ? 15 : 17;
  const color = type === 'dp' ? '#06B6D4' : '#A855F7';
  const svgId = type === 'dp' ? 'dpChart' : 'mpChart';

  try{
    document.getElementById(svgId).innerHTML = '<text x="400" y="170" fill="#6B7684" text-anchor="middle">加载中...</text>';
    const r = await fetch(API + endpoint + '?hours=' + hours + '&points=300');
    const d = await r.json();

    drawTrendChart(svgId, d.series, threshold, color);

    // 统计
    const vals = d.series.map(s=>s.value).filter(v=>v!=null);
    if(vals.length){
      document.getElementById(type+'TrendCur').textContent = vals[vals.length-1].toFixed(type==='dp'?1:2);
      document.getElementById(type+'TrendMax').textContent = Math.max(...vals).toFixed(type==='dp'?1:2);
      document.getElementById(type+'TrendMean').textContent = (vals.reduce((a,b)=>a+b,0)/vals.length).toFixed(type==='dp'?1:2);
    }
  }catch(e){ console.error('trend', e); }
}

// ═══════════════════════════════════════════
// 风险图绘制
// ═══════════════════════════════════════════
function drawRiskMap(series, dpTh, mpTh){
  const svg = document.getElementById('riskChart');
  if(!svg) return;
  const W=800, H=400, padL=50, padR=20, padT=20, padB=40;
  const cw=W-padL-padR, ch=H-padT-padB;

  let xMax = Math.max(dpTh*1.8, ...series.map(p=>p.dp||0)) * 1.1;
  let yMax = Math.max(mpTh*1.8, ...series.map(p=>p.mp||0)) * 1.1;
  xMax = Math.ceil(xMax/5)*5; yMax = Math.ceil(yMax/5)*5;

  const xs = v => padL + (v/xMax)*cw;
  const ys = v => padT + ch - (v/yMax)*ch;

  let html = '';

  // 四象限背景
  const xTh = xs(dpTh), yTh = ys(mpTh);
  // 左下（安全-绿）
  html += `<rect x="${padL}" y="${yTh}" width="${xTh-padL}" height="${padT+ch-yTh}" fill="rgba(34,197,94,0.04)"/>`;
  // 右下（ΔP超-橙）
  html += `<rect x="${xTh}" y="${yTh}" width="${padL+cw-xTh}" height="${padT+ch-yTh}" fill="rgba(249,115,22,0.04)"/>`;
  // 左上（MP超-黄）
  html += `<rect x="${padL}" y="${padT}" width="${xTh-padL}" height="${yTh-padT}" fill="rgba(234,179,8,0.04)"/>`;
  // 右上（双超-红）
  html += `<rect x="${xTh}" y="${padT}" width="${padL+cw-xTh}" height="${yTh-padT}" fill="rgba(239,68,68,0.06)"/>`;

  // 网格
  for(let i=0;i<=5;i++){
    const y=padT+(ch/5)*i, v=yMax-(yMax/5)*i;
    html += `<line x1="${padL}" y1="${y}" x2="${W-padR}" y2="${y}" stroke="#1C2330" stroke-width="1"/>`;
    html += `<text x="${padL-8}" y="${y+4}" fill="#6B7684" font-size="10" text-anchor="end">${v.toFixed(0)}</text>`;
  }
  for(let i=0;i<=5;i++){
    const x=padL+(cw/5)*i, v=(xMax/5)*i;
    html += `<line x1="${x}" y1="${padT}" x2="${x}" y2="${padT+ch}" stroke="#1C2330" stroke-width="1"/>`;
    html += `<text x="${x}" y="${padT+ch+18}" fill="#6B7684" font-size="10" text-anchor="middle">${v.toFixed(0)}</text>`;
  }

  // 阈值线
  html += `<line x1="${xTh}" y1="${padT}" x2="${xTh}" y2="${padT+ch}" stroke="#EF4444" stroke-width="1.5" stroke-dasharray="6 4"/>`;
  html += `<line x1="${padL}" y1="${yTh}" x2="${W-padR}" y2="${yTh}" stroke="#EF4444" stroke-width="1.5" stroke-dasharray="6 4"/>`;
  html += `<text x="${xTh+4}" y="${padT+12}" fill="#EF4444" font-size="10">ΔP阈值=${dpTh}</text>`;
  html += `<text x="${W-padR-4}" y="${yTh-4}" fill="#EF4444" font-size="10" text-anchor="end">MP阈值=${mpTh}</text>`;

  // 散点
  series.forEach(p => {
    if(p.dp == null || p.mp == null) return;
    const x = xs(p.dp), y = ys(p.mp);
    let c = RISK_COLORS.green;
    if(p.dp >= dpTh && p.mp >= mpTh) c = RISK_COLORS.red;
    else if(p.dp >= dpTh || p.mp >= mpTh) c = RISK_COLORS.orange;
    else if(p.dp >= dpTh-2 || p.mp >= mpTh-3) c = RISK_COLORS.yellow;
    html += `<circle cx="${x}" cy="${y}" r="3" fill="${c}" opacity="0.6"/>`;
  });

  // 轴标签
  html += `<text x="${padL+cw/2}" y="${H-4}" fill="#A0AAB8" font-size="12" text-anchor="middle">ΔP (cmH₂O)</text>`;
  html += `<text x="14" y="${padT+ch/2}" fill="#A0AAB8" font-size="12" text-anchor="middle" transform="rotate(-90 14 ${padT+ch/2})">MP (J/min)</text>`;

  svg.innerHTML = html;
}

async function loadRiskMap(hours){
  curHours = hours;
  try{
    const r = await fetch(API + '/api/risk-map?hours=' + hours + '&points=500');
    const d = await r.json();
    drawRiskMap(d.series, d.thresholds.dp, d.thresholds.mp);
  }catch(e){ console.error('riskmap', e); }
}

// ═══════════════════════════════════════════
// 预警列表
// ═══════════════════════════════════════════
async function loadAlerts(){
  try{
    const r = await fetch(API + '/api/alerts?hours=168');
    const d = await r.json();
    const el = document.getElementById('alertList');
    if(d.count === 0){
      el.innerHTML = '<div class="alert-empty">✓ 当前无预警事件（保护性通气，风险等级 L1）</div>';
      return;
    }
    el.innerHTML = d.alerts.map(a => {
      const d = new Date(a.ts);
      const ts = `${d.getMonth()+1}/${d.getDate()} ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
      return `<div class="alert-item">
        <div class="badge lv${a.risk_level}">L${a.risk_level}</div>
        <div class="msg">${a.message || a.risk_label}</div>
        <div class="ts">${ts}</div>
      </div>`;
    }).join('');
  }catch(e){ console.error('alerts', e); }
}

// ═══════════════════════════════════════════
// 设置页
// ═══════════════════════════════════════════
async function loadSettings(){
  try{
    const r = await fetch(API + '/api/health');
    const d = await r.json();
    document.getElementById('setDev').textContent = d.device;
    document.getElementById('setMongo').textContent = d.mongo?.replace('mongodb://','') || '--';
    document.getElementById('setRange').textContent = d.data_range ? (d.data_range.oldest?.slice(0,10)+' ~ '+d.data_range.newest?.slice(0,10)) : '--';
    document.getElementById('setWs').textContent = (d.ws_connected || 0) + ' 连接';
  }catch(e){ console.error('settings', e); }
}

// ═══════════════════════════════════════════
// WebSocket 连接
// ═══════════════════════════════════════════
function connectWS(){
  const wsUrl = (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws';
  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    document.getElementById('wsDot').classList.add('on');
    document.getElementById('wsLabel').textContent = '已连接';
  };

  ws.onmessage = (evt) => {
    try{
      const msg = JSON.parse(evt.data);
      if(msg.type === 'overview'){
        renderOverview(msg.data);
      }
    }catch(e){ console.error('ws parse', e); }
  };

  ws.onclose = () => {
    document.getElementById('wsDot').classList.remove('on');
    document.getElementById('wsLabel').textContent = '断开，重连中...';
    setTimeout(connectWS, 3000);
  };

  ws.onerror = () => { ws.close(); };
}

// ═══════════════════════════════════════════
// 初始化
// ═══════════════════════════════════════════
async function init(){
  await fetchOverview();
  connectWS();
  // 定时刷新总览（即使WS断开也有保底）
  setInterval(fetchOverview, 10000);
}
init();
</script>
</body>
</html>
'''

if __name__ == "__main__":
    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(HTML)
    print(f"前端 HTML 已生成: {HTML_PATH}")
    print(f"文件大小: {len(HTML)} bytes")
    print(f"页面: 总览/ΔP趋势/MP趋势/二维风险图/预警中心/设置")
    print(f"API: REST + WebSocket (/ws)")
