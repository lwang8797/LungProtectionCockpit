# -*- coding: utf-8 -*-
"""
gen_frontend_v2.py - 以原型设计 UI 为基底，替换 JS 为真实 API + WebSocket

策略:
  1. 从 gen_proto_dark.py 提取 CSS / BODY / HTML_HEAD（完全不变）
  2. 追加少量 CSS（.alarm.ok 绿色正常态）
  3. 重写 JS：保留所有 SVG 绘图函数，替换模拟数据为 fetch API + WebSocket
  4. 输出 cockpit_frontend.html
"""
import os, sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.dirname(SCRIPT_DIR)

# ── 从 gen_proto_dark.py 提取 CSS / BODY / HTML_HEAD ──
gen_proto_path = os.path.join(SCRIPT_DIR, "gen_proto_dark.py")
with open(gen_proto_path, "r", encoding="utf-8") as f:
    code = f.read()

# 只 exec 字符串定义部分，不执行文件写入
marker = "\nout = HTML_HEAD"
idx = code.find(marker)
if idx > 0:
    code = code[:idx]

ns = {}
exec(code, ns)
HTML_HEAD = ns["HTML_HEAD"]
CSS = ns["CSS"]
BODY = ns["BODY"]

# ── HTML_HEAD 修正：简化标题 ──
HTML_HEAD = HTML_HEAD.replace(
    '<title>肺保护驾驶舱 · 产品原型（15寸触摸屏 WebUI · 深色版）</title>',
    '<title>肺保护驾驶舱</title>',
)

# ── 修正 BODY：去除界面需求解释用语 + 真实数据适配 ──

# Logo: 肺图标替换舱图标，删除副标题
LUNG_SVG = '<svg width="18" height="18" viewBox="0 0 20 20" fill="#fff"><rect x="9.2" y="1.5" width="1.6" height="5" rx="0.8"/><path d="M8.5 6.5C6 6.5 2.5 9 2.5 13c0 3.5 1.5 5.5 3.5 5.5s2.5-1.5 2.5-4.5V6.5z"/><path d="M11.5 6.5C14 6.5 17.5 9 17.5 13c0 3.5-1.5 5.5-3.5 5.5s-2.5-1.5-2.5-4.5V6.5z"/></svg>'
BODY = BODY.replace(
    '<div class="lg">舱</div><span>肺保护驾驶舱<span class="sub">Lung Cockpit · 15寸触摸 WebUI</span></span>',
    '<div class="lg">' + LUNG_SVG + '</div><span>肺保护驾驶舱</span>',
)

# Meta: 通气模式动态化，隐藏患者信息
BODY = BODY.replace(
    '<b>VC-SIMV</b><div class="sep"></div><span>07床 · 张××</span><div class="sep"></div><span>通气',
    '<b id="workmode">--</b><div class="sep"></div><span>通气',
)

# 通气参数快照：去除来源提示
BODY = BODY.replace(
    ' <span class="hint">来源：呼吸机 MongoDB</span>',
    '',
)

# 通气参数快照：模式动态化
BODY = BODY.replace(
    '<div class="v">VC-SIMV <small>420 mL</small></div>',
    '<div class="v"><span id="snapMode">--</span> <small>420 mL</small></div>',
)

# ΔP趋势：去除"阈值线上方自动高亮"
BODY = BODY.replace(
    'Pplat − PEEP｜阈值线上方自动高亮｜CUSUM 变化点标记',
    'Pplat − PEEP｜CUSUM 变化点标记',
)

# ΔP分钟趋势标题：去除灰区说明
BODY = BODY.replace(
    '<span class="hint">灰区 = 无数据（不插值）</span>',
    '',
)

# MP趋势：去除灰区说明
BODY = BODY.replace(
    'MP = 0.098×RR×VT×(PIP−0.5ΔP)（VCV）｜灰区 = 暂停评估（自主呼吸较强）',
    'MP = 0.098×RR×VT×(PIP−0.5ΔP)（VCV）',
)

# CSV导出：去除实现细节
BODY = BODY.replace(
    '演示原型：后端（Python + MongoDB）就绪后将生成 1 分钟粒度 CSV 导出',
    '导出当前窗口 1 分钟粒度 CSV',
)

# 状态栏：简化各提示行
BODY = BODY.replace(
    '数据源：呼吸机 MongoDB · metrics_1min（1min 粒度）',
    '数据源：呼吸机 · 1min 粒度',
)
BODY = BODY.replace(
    '计算引擎：Python（规划，本期为模拟数据驱动）',
    '计算引擎：Python · 实时计算',
)
BODY = BODY.replace(
    '肺保护驾驶舱 V1.2 · 15寸触摸 WebUI · 1366×768',
    '肺保护驾驶舱 V2.0',
)

# ── CSS 追加：真实数据模式下的正常态样式 ──
CSS_ADD = r"""
/* ── Real-data mode additions ── */
#topbar .alarm.ok{background:var(--ok);}
#topbar .alarm.ok .ic{color:var(--ok);}
#topbar .riskchip.ok{background:var(--ok);}
"""

# ── 新 JS：保留所有绘图函数，替换数据源 ──
NEW_JS = r'''
// ==================== Stage Fit ====================
function fitStage(){
  var s = Math.min(window.innerWidth/1366, window.innerHeight/768);
  var el = document.getElementById('stage');
  el.style.transform = 'translate(-50%,-50%) scale('+s+')';
}
window.addEventListener('resize', fitStage); fitStage();

// ==================== Global State ====================
var data = [];        // merged dp+mp time series
var N = 0;            // data.length
var changePts = [];   // CUSUM change points
var alerts = [];      // alert list
var TH = { dp:15.0, mp:17.0 };
var curWin = {dp:1440, mp:1440};
var ws = null;
var ovData = null;
var alFilter = 'all';
var API = location.origin;

// ==================== Utility Functions ====================
function stats(arr, key, validKey, T){
  var auc=0, ta=0, validN=0, sum=0, max=-1e9;
  arr.forEach(function(d){ if(!d[validKey]) return; validN++; var v=d[key]; sum+=v; if(v>max)max=v; if(v>T){ auc+=(v-T); ta+=1; } });
  return { auc: auc/60, ta: ta, ratio: validN? ta/validN : 0, mean: validN? sum/validN : 0, max: max===-1e9?0:max };
}
function fmtDur(min){ var h=Math.floor(min/60), m=Math.round(min%60); return (h?h+'h ':'')+(m||!h? m+'m':''); }
function riskOf(dp, mp){
  if((dp>=20||mp>=25)) return 3;
  if(dp>=TH.dp||mp>=TH.mp) return 2;
  if(dp>=TH.dp-2||mp>=TH.mp-2) return 1;
  return 0;
}
var RISK_C=['#22C55E','#EAB308','#F97316','#EF4444'];

// ==================== SVG Helpers ====================
var SVGNS='http://www.w3.org/2000/svg';
function el(tag,attrs){ var e=document.createElementNS(SVGNS,tag); for(var k in attrs) e.setAttribute(k,attrs[k]); return e; }
function txt(x,y,s,attrs){ var e=el('text',Object.assign({x:x,y:y},attrs||{})); e.textContent=s; return e; }

// ==================== Drawing: Trend Chart ====================
function drawTrend(svgId, tipId, win, key, validKey, T, yMin, yMax, unit, color, showCp){
  var svg=document.getElementById(svgId); if(!svg) return; svg.innerHTML='';
  var W=svg.clientWidth||860, H=+svg.getAttribute('height');
  var L=46,R=10,Tp=8,B=40, pw=W-L-R, ph=H-Tp-B;
  var arr=data.slice(N-win);
  if(arr.length===0){ svg.appendChild(txt(W/2,H/2,'暂无数据',{'font-size':14,fill:'#7C8694','text-anchor':'middle'})); return; }
  function X(idx){ return L+idx/(win-1)*pw; }
  function Y(v){ return Tp+(yMax-Math.min(Math.max(v,yMin),yMax))/(yMax-yMin)*ph; }
  var yT=Y(T);
  for(var v=yMin; v<=yMax; v+=(yMax-yMin)/4){
    var gy=Y(v);
    svg.appendChild(el('line',{x1:L,y1:gy,x2:W-R,y2:gy,stroke:'rgba(255,255,255,.05)','stroke-width':1}));
    svg.appendChild(txt(L-6,gy+4,v.toFixed(0),{'font-size':10,fill:'#7C8694','text-anchor':'end'}));
  }
  var step = win===1440?180: win===360?60:10;
  for(var i=0;i<win;i+=step){
    var d=arr[i]; if(!d) continue;
    svg.appendChild(txt(X(i),H-24,d.t,{'font-size':10,fill:'#7C8694','text-anchor':'middle'}));
    svg.appendChild(el('line',{x1:X(i),y1:Tp+ph,x2:X(i),y2:Tp+ph+4,stroke:'rgba(255,255,255,.12)'}));
  }
  svg.appendChild(el('line',{x1:L,y1:yT,x2:W-R,y2:yT,stroke:'#EF4444','stroke-width':1.6,'stroke-dasharray':'7 5'}));
  svg.appendChild(txt(W-R,yT-6,'阈值 '+T.toFixed(1)+' '+unit,{'font-size':10.5,fill:'#EF4444','text-anchor':'end','font-weight':'bold'}));
  var i2=0;
  while(i2<win){
    var d=arr[i2];
    if(d && !d[validKey]){
      var j=i2; while(j<win && arr[j] && !arr[j][validKey]) j++;
      svg.appendChild(el('rect',{x:X(i2),y:Tp,width:X(j-1)-X(i2)+ (j<win? 3:0),height:ph,fill:'rgba(255,255,255,.04)','stroke':'rgba(255,255,255,.1)','stroke-dasharray':'3 3'}));
      var mid=(X(i2)+X(j-1))/2;
      var lbl = d.mpSt==='MP_SUSPENDED' ? '暂停评估' : '无数据';
      if(X(j-1)-X(i2)>26) svg.appendChild(txt(mid,Tp+ph/2,lbl,{'font-size':10,fill:'#7C8694','text-anchor':'middle'}));
      i2=j;
    } else i2++;
  }
  var segs=[], cur=[];
  arr.forEach(function(d,idx){ if(d[validKey]) cur.push([idx,d]); else { if(cur.length)segs.push(cur); cur=[]; } });
  if(cur.length) segs.push(cur);
  segs.forEach(function(sg){
    var pts = sg.map(function(p){ return X(p[0]).toFixed(1)+','+Y(p[1][key]).toFixed(1); }).join(' ');
    svg.appendChild(el('polyline',{points:pts,fill:'none',stroke:color,'stroke-width':2,'stroke-linejoin':'round'}));
    for(var q=0;q<sg.length-1;q++){
      var a=sg[q], b=sg[q+1];
      if(a[1][key]>T && b[1][key]>T){
        svg.appendChild(el('polygon',{points:X(a[0])+','+yT+' '+X(a[0])+','+Y(a[1][key])+' '+X(b[0])+','+Y(b[1][key])+' '+X(b[0])+','+yT, fill:'#EF4444','opacity':.22}));
      }
    }
  });
  if(showCp){ changePts.forEach(function(cp){ if(cp.i > N-win && cp.i < N){
    var li=N-1-cp.i, x=X(li);
    if(x<L||x>W-R) return;
    svg.appendChild(el('line',{x1:x,y1:Tp,x2:x,y2:Tp+ph,stroke:'#3B82F6','stroke-width':1.4,'stroke-dasharray':'3 4'}));
    svg.appendChild(el('circle',{cx:x,cy:Y(data[cp.i][key]),r:5,fill:'#0E1218',stroke:'#3B82F6','stroke-width':2.4}));
    svg.appendChild(el('path',{d:'M'+(x-5)+' '+(Y(data[cp.i][key])-11)+'L'+(x+5)+' '+(Y(data[cp.i][key])-11)+'L'+x+' '+(Y(data[cp.i][key])-3)+'Z',fill:'#3B82F6'}));
  }});}
  svg.appendChild(txt(L+2,Tp+11,unit,{'font-size':10,fill:'#7C8694'}));

  var tip=document.getElementById(tipId);
  if(tip){
  svg.onmousemove=function(ev){
    var r=svg.getBoundingClientRect();
    var mx=(ev.clientX-r.left)*(W/r.width);
    var idx=Math.round((mx-L)/pw*(win-1)); idx=Math.max(0,Math.min(win-1,idx));
    var d=arr[idx]; if(!d) return;
    tip.style.display='block';
    tip.innerHTML='<b>'+d.t+'</b>  '+key.toUpperCase()+'：'+(d[validKey]? d[key].toFixed(1):'—')+' '+unit+(d.mpSt==='MP_SUSPENDED'?'（暂停评估）':(!d[validKey]?'（无数据）':''))+(d.dpSt==='UNRELIABLE_DP'?'（平台压不可靠）':'');
    tip.style.left=Math.min((X(idx)/W)*100,72)+'%';
    tip.style.top='6px';
  };
  svg.onmouseleave=function(){ tip.style.display='none'; };
  }
}

// ==================== Drawing: Gauge ====================
function gaugeLevel(v, th, hi){
  if(v>=hi-3) return 3;
  if(v>=th) return 2;
  if(v>=th-2) return 1;
  return 0;
}
function drawGauge(svgId, val, th, lo, hi, unit){
  var svg=document.getElementById(svgId); if(!svg) return; svg.innerHTML='';
  var W=svg.clientWidth||300, H=+svg.getAttribute('height');
  var cx=W/2, cy=H-38, r=Math.min(W/2-20, cy-12), sw=Math.max(14, r*0.18);
  function ang(v){ return Math.PI*(1-(Math.max(lo,Math.min(hi,v))-lo)/(hi-lo)); }
  function pt(v,rad){ var a=ang(v); return [cx+rad*Math.cos(a), cy-rad*Math.sin(a)]; }
  function arcSeg(v1,v2,rad){
    var p1=pt(v1,rad), p2=pt(v2,rad);
    return 'M'+p1[0].toFixed(1)+' '+p1[1].toFixed(1)+' A'+rad+' '+rad+' 0 0 1 '+p2[0].toFixed(1)+' '+p2[1].toFixed(1);
  }
  var bands=[[lo,th-2,'#22C55E'],[th-2,th,'#EAB308'],[th,hi-3,'#F97316'],[hi-3,hi,'#EF4444']];
  bands.forEach(function(b){ svg.appendChild(el('path',{d:arcSeg(b[0],b[1],r),fill:'none',stroke:b[2],'stroke-width':sw,opacity:.9})); });
  svg.appendChild(el('path',{d:arcSeg(lo,hi,r-sw/2-5),fill:'none',stroke:'rgba(255,255,255,.08)','stroke-width':1.5}));
  var t1=pt(th,r+sw/2+3), t2=pt(th,r-sw/2-5);
  svg.appendChild(el('line',{x1:t1[0],y1:t1[1],x2:t2[0],y2:t2[1],stroke:'#F1F4F8','stroke-width':2.5}));
  var anchor = t1[0]>=cx?'start':'end';
  svg.appendChild(txt(t1[0]+(t1[0]>=cx?5:-5), t1[1]-5,'阈值 '+th.toFixed(0),{'font-size':10,fill:'#B7C0CC','text-anchor':anchor}));
  var lv=gaugeLevel(val,th,hi), nc=RISK_C[lv];
  var m1=pt(val,r-sw/2-4), m2=pt(val,r+sw/2+2);
  svg.appendChild(el('line',{x1:m1[0],y1:m1[1],x2:m2[0],y2:m2[1],stroke:nc,'stroke-width':9,'stroke-linecap':'round',opacity:.25}));
  svg.appendChild(el('line',{x1:m1[0],y1:m1[1],x2:m2[0],y2:m2[1],stroke:nc,'stroke-width':4,'stroke-linecap':'round'}));
  svg.appendChild(txt(cx, cy-26, val.toFixed(1), {'font-size':40,'font-weight':800,fill:nc,'text-anchor':'middle'}));
  svg.appendChild(txt(cx, cy-9, unit, {'font-size':11.5,fill:'#7C8694','text-anchor':'middle'}));
  svg.appendChild(txt(cx-r-sw/2, cy+16, lo, {'font-size':9.5,fill:'#7C8694','text-anchor':'middle'}));
  svg.appendChild(txt(cx+r+sw/2, cy+16, hi, {'font-size':9.5,fill:'#7C8694','text-anchor':'middle'}));
}

// ==================== Drawing: Risk Band ====================
function drawRiskBand(){
  var svg=document.getElementById('riskBand'); if(!svg) return; svg.innerHTML='';
  var W=svg.clientWidth||1200,H=60,L=8,R=8,bh=28,bt=4;
  var per=20;
  var totalSegs=Math.ceil(N/per);
  if(totalSegs===0){ svg.appendChild(txt(W/2,H/2,'暂无数据',{'font-size':12,fill:'#7C8694','text-anchor':'middle'})); return; }
  for(var b=0;b<totalSegs;b++){
    var seg=data.slice(b*per,(b+1)*per);
    var valid=seg.filter(function(d){return d.dpValid;});
    var lvl=0; var hasData=valid.length>0;
    if(hasData){ seg.forEach(function(d){ if(d.dpValid){ var l=riskOf(d.dp,d.mp); if(l>lvl)lvl=l;} }); }
    var x=L+b/totalSegs*(W-L-R);
    var color = hasData? RISK_C[lvl] : '#252C38';
    var op = hasData? 1 : .7;
    svg.appendChild(el('rect',{x:x,y:bt,width:(W-L-R)/totalSegs-1.2,height:bh,rx:2.5,fill:color,opacity:op}));
  }
  var px=L+(N-1)/N*(W-L-R);
  svg.appendChild(el('line',{x1:px,y1:bt-3,x2:px,y2:bt+bh+3,stroke:'#F1F4F8','stroke-width':2}));
  svg.appendChild(el('path',{d:'M'+(px-5)+' '+(bt-3)+'L'+(px+5)+' '+(bt-3)+'L'+px+' '+bt+'Z',fill:'#F1F4F8'}));
  // Time labels - safe for N < 1440
  for(var h=0;h<=24;h+=3){
    var hx=L+h/24*(W-L-R);
    var idx=N-1-(24-h)*60;
    var tStr='--';
    if(idx>=0 && idx<N && data[idx]) tStr=data[idx].t;
    else if(idx<0 && N>0) tStr=data[0].t;
    svg.appendChild(txt(Math.min(hx,W-20),H-4,tStr,{'font-size':9.5,fill:'#7C8694','text-anchor':'middle'}));
  }
}

// ==================== Drawing: Risk Map ====================
function drawRiskMap(svgId, tipId, W, H, showQuad){
  var svg=document.getElementById(svgId); if(!svg) return; svg.innerHTML='';
  if(N===0){ svg.appendChild(txt(W/2,H/2,'暂无数据',{'font-size':14,fill:'#7C8694','text-anchor':'middle'})); return; }
  var L=52,R=14,Tp=12,B=40,pw=W-L-R,ph=H-Tp-B;
  var dpMax=25, mpMax=35;
  function X(dp){ return L+dp/dpMax*pw; } function Y(mp){ return Tp+(mpMax-mp)/mpMax*ph; }
  var xT=X(TH.dp), yT=Y(TH.mp);
  if(showQuad){
    svg.appendChild(el('rect',{x:xT,y:Tp,width:W-R-xT,height:yT-Tp,fill:'#EF4444','opacity':.06}));
    svg.appendChild(el('rect',{x:L,y:Tp,width:xT-L,height:yT-Tp,fill:'#F97316','opacity':.06}));
    svg.appendChild(el('rect',{x:xT,y:yT,width:W-R-xT,height:Tp+ph-yT,fill:'#EAB308','opacity':.07}));
  }
  for(var d=0; d<=dpMax; d+=5){ svg.appendChild(el('line',{x1:X(d),y1:Tp,x2:X(d),y2:Tp+ph,stroke:'rgba(255,255,255,.06)'})); svg.appendChild(txt(X(d),H-22,d,{'font-size':10,fill:'#7C8694','text-anchor':'middle'})); }
  for(var m=0; m<=mpMax; m+=7){ svg.appendChild(el('line',{x1:L,y1:Y(m),x2:W-R,y2:Y(m),stroke:'rgba(255,255,255,.06)'})); svg.appendChild(txt(L-6,Y(m)+4,m,{'font-size':10,fill:'#7C8694','text-anchor':'end'})); }
  svg.appendChild(el('line',{x1:xT,y1:Tp,x2:xT,y2:Tp+ph,stroke:'#EF4444','stroke-width':1.5,'stroke-dasharray':'6 5'}));
  svg.appendChild(el('line',{x1:L,y1:yT,x2:W-R,y2:yT,stroke:'#EF4444','stroke-width':1.5,'stroke-dasharray':'6 5'}));
  if(showQuad){
    svg.appendChild(txt(W-R,yT-5,'MP 阈值 '+TH.mp.toFixed(0),{'font-size':10,fill:'#EF4444','text-anchor':'end','font-weight':'bold'}));
    svg.appendChild(txt(W-R,Tp+ph+34,'ΔP 阈值 '+TH.dp.toFixed(0),{'font-size':10,fill:'#EF4444','text-anchor':'end','font-weight':'bold'}));
    svg.appendChild(txt(X(2),Y(33),'左上 · 低应力高能量',{'font-size':10,fill:'#F97316','font-weight':'bold'}));
    svg.appendChild(txt(W-R-2,Y(33),'右上 · 双高风险',{'font-size':10,fill:'#EF4444','font-weight':'bold','text-anchor':'end'}));
    svg.appendChild(txt(X(2),Y(1.5),'左下 · 安全区',{'font-size':10,fill:'#22C55E','font-weight':'bold'}));
    svg.appendChild(txt(W-R-2,Y(1.5),'右下 · 高应力',{'font-size':10,fill:'#EAB308','font-weight':'bold','text-anchor':'end'}));
  }
  svg.appendChild(txt(L+2,Tp+11,'MP (J/min)',{'font-size':10,fill:'#7C8694'}));
  svg.appendChild(txt(W-R,Tp+ph+34,'ΔP (cmH2O)',{'font-size':10,fill:'#7C8694','text-anchor':'start','x':L-40}));
  var traj=data.slice(Math.max(0,N-60)).filter(function(d){return d.dpValid&&d.mpValid;});
  var tip=tipId && document.getElementById(tipId);
  traj.forEach(function(d,k){
    var op=0.15+0.85*(k/traj.length), r=2.5+2.5*(k/traj.length);
    var c=el('circle',{cx:X(Math.min(d.dp,dpMax)),cy:Y(Math.min(d.mp,mpMax)),r:r,fill:RISK_C[riskOf(d.dp,d.mp)],opacity:op,cursor:'pointer'});
    if(tip){ c.addEventListener('mousemove',function(){ tip.style.display='block'; tip.innerHTML='<b>'+d.t+'</b>  ΔP '+d.dp.toFixed(1)+' cmH2O · MP '+d.mp.toFixed(1)+' J/min'; tip.style.left='55%'; tip.style.top='4px'; }); c.addEventListener('mouseleave',function(){ tip.style.display='none'; }); }
    svg.appendChild(c);
  });
  var last=data[N-1];
  if(last && last.dpValid && last.mpValid){
    var cx=X(Math.min(last.dp,dpMax)), cy=Y(Math.min(last.mp,mpMax));
    var ring=el('circle',{cx:cx,cy:cy,r:9,fill:'none',stroke:RISK_C[riskOf(last.dp,last.mp)],'stroke-width':2.4});
    svg.appendChild(ring);
    ring.appendChild(el('animate',{attributeName:'r',values:'9;15;9',dur:'1.6s',repeatCount:'indefinite'}));
    ring.appendChild(el('animate',{attributeName:'opacity',values:'1;.15;1',dur:'1.6s',repeatCount:'indefinite'}));
    svg.appendChild(el('circle',{cx:cx,cy:cy,r:5.5,fill:RISK_C[riskOf(last.dp,last.mp)],stroke:'#0E1218','stroke-width':2}));
    svg.appendChild(txt(cx,cy-14,'当前',{'font-size':10.5,fill:'#F1F4F8','font-weight':'bold','text-anchor':'middle'}));
  }
}

// ==================== Stat Boxes & Segment List ====================
function statBoxes(containerId, s, unit, aucUnit, cumKj){
  var h='';
  h+='<div class="statbox"><div class="k">均值（当前窗口）</div><div class="v">'+s.mean.toFixed(1)+' <small>'+unit+'</small></div></div>';
  h+='<div class="statbox"><div class="k">超阈值曲线下面积</div><div class="v">'+s.auc.toFixed(2)+' <small>'+aucUnit+'</small></div></div>';
  h+='<div class="statbox"><div class="k">超阈值时间 / 占比</div><div class="v">'+fmtDur(s.ta)+' <small>'+(s.ratio*100).toFixed(1)+'%</small></div></div>';
  if(cumKj!==undefined) h+='<div class="statbox"><div class="k">累积机械能（24h）</div><div class="v">'+cumKj.toFixed(1)+' <small>kJ</small></div></div>';
  else h+='<div class="statbox"><div class="k">峰值</div><div class="v">'+s.max.toFixed(1)+' <small>'+unit+'</small></div></div>';
  document.getElementById(containerId).innerHTML=h;
}
function segList(containerId, win, key, validKey, T, unit, color){
  var arr=data.slice(Math.max(0,N-win)); var segs=[]; var cur=null;
  arr.forEach(function(d,idx){
    if(d[validKey] && d[key]>T){ if(!cur) cur={s:idx,e:idx,peak:d[key]}; else { cur.e=idx; if(d[key]>cur.peak)cur.peak=d[key]; } }
    else if(cur){ segs.push(cur); cur=null; }
  });
  if(cur) segs.push(cur);
  var h='';
  if(!segs.length) h='<div style="font-size:12px;color:var(--t-3);padding:6px 2px;">当前窗口内无超阈值时段</div>';
  segs.forEach(function(sg){
    var dur=(sg.e-sg.s+1);
    h+='<div class="evli"><div class="bar" style="background:'+color+'"></div><div><div><b>'+arr[sg.s].t+' - '+(arr[sg.e].t)+'</b>（'+fmtDur(dur)+'）</div><div style="color:var(--t-3);font-size:11px;">峰值 '+sg.peak.toFixed(1)+' '+unit+' | 超出 '+(sg.peak-T).toFixed(1)+'</div></div></div>';
  });
  document.getElementById(containerId).innerHTML=h;
}

// ==================== Alert Rendering ====================
function renderAlerts(){
  var list=document.getElementById('alList'); var h='';
  var filtered=alerts.filter(function(a){ return alFilter==='all'||(alFilter==='act'?a.act:!a.act); });
  if(!filtered.length){ list.innerHTML='<div class="card" style="text-align:center;color:var(--t-3);">当前无预警事件（保护性通气）</div>'; document.getElementById('alBadge').textContent='0'; return; }
  filtered.forEach(function(a){
    h+='<div class="al-item">'
      +'<span class="lv l'+a.lv+'">'+a.lvN+'</span>'
      +'<div><div class="msg">'+a.msg+'</div><div class="meta">'+a.meta+(a.time?' · '+a.time:'')+(a.ack?' · '+a.ack:'')+'</div></div>'
      +'<div class="st">'+(a.act? '<span style="color:var(--dan);font-weight:bold;">● 活动中</span><button class="btn ghost-warn" onclick="ackAlert(this)">确认</button>' : '<span style="color:var(--t-3)">已恢复</span>')+'</div></div>';
  });
  list.innerHTML=h;
  document.getElementById('alBadge').textContent=alerts.filter(function(a){return a.act;}).length;
}
function ackAlert(btn){
  var item=btn.closest('.al-item');
  var idx=[].indexOf.call(document.getElementById('alList').children,item);
  var acts=alerts.filter(function(a){return alFilter==='all'||(alFilter==='act'?a.act:!a.act);});
  if(acts[idx]){ acts[idx].act=false; acts[idx].ack='操作员 '+('0'+new Date().getHours()).slice(-2)+':'+('0'+new Date().getMinutes()).slice(-2)+' 确认'; }
  renderAlerts(); toast('预警已确认（操作留痕）');
}
document.getElementById('alFilter').addEventListener('click',function(e){
  if(e.target.classList.contains('btn')){
    [].forEach.call(this.querySelectorAll('.btn'),function(b){b.classList.remove('on');});
    e.target.classList.add('on'); alFilter=e.target.dataset.f; renderAlerts();
  }
});

// ==================== Page Rendering ====================
function renderDp(win){
  if(N===0){ document.getElementById('dpStats').innerHTML=''; document.getElementById('dpSegs').innerHTML=''; document.getElementById('dpCusum').innerHTML=''; return; }
  drawTrend('dpChart','dpTip',win,'dp','dpValid',TH.dp,8,22,'cmH₂O','#3B82F6',true); statBoxes('dpStats',stats(data.slice(Math.max(0,N-win)),'dp','dpValid',TH.dp),'cmH₂O','cmH₂O·h'); segList('dpSegs',win,'dp','dpValid',TH.dp,'cmH₂O','#EF4444');
  var vis=changePts.filter(function(c){return c.i>N-win && c.i<N;});
  document.getElementById('dpCusum').innerHTML = vis.length? vis.map(function(c){return '· '+c.txt;}).join('<br>') : '<span style="color:var(--t-3)">当前窗口无变化点</span>';
}
function renderMp(win){
  if(N===0){ document.getElementById('mpStats').innerHTML=''; document.getElementById('mpSegs').innerHTML=''; return; }
  drawTrend('mpChart','mpTip',win,'mp','mpValid',TH.mp,8,26,'J/min','#A855F7',false); statBoxes('mpStats',stats(data.slice(Math.max(0,N-win)),'mp','mpValid',TH.mp),'J/min','J', N>0? data[N-1].cum:0); segList('mpSegs',win,'mp','mpValid',TH.mp,'J/min','#EF4444');
  var svg=document.getElementById('mpEnergy'); if(!svg) return; svg.innerHTML='';
  var W=svg.clientWidth||860,H=120,L=46,R=10,Tp=6,B=26,pw=W-L-R,ph=H-Tp-B;
  var arr=data.slice(Math.max(0,N-win));
  if(arr.length<2){ svg.appendChild(txt(W/2,H/2,'数据不足',{'font-size':12,fill:'#7C8694','text-anchor':'middle'})); return; }
  var lo=arr[0].cum, hi=arr[arr.length-1].cum;
  var pts=arr.map(function(d,idx){ return (L+idx/(arr.length-1)*pw).toFixed(1)+','+(Tp+ph-(d.cum-lo)/((hi-lo)||1)*ph).toFixed(1); });
  var step=win===1440?180:win===360?60:10;
  for(var i=0;i<arr.length;i+=step){ svg.appendChild(txt(L+i/(arr.length-1)*pw,H-8,arr[i].t,{'font-size':9.5,fill:'#7C8694','text-anchor':'middle'})); }
  svg.appendChild(el('polygon',{points:L+','+(Tp+ph)+' '+pts.join(' ')+' '+(W-R)+','+(Tp+ph),fill:'#A855F7','opacity':.14}));
  svg.appendChild(el('polyline',{points:pts.join(' '),fill:'none',stroke:'#A855F7','stroke-width':2}));
  svg.appendChild(txt(L+2,Tp+11,'kJ',{'font-size':9.5,fill:'#7C8694'}));
  svg.appendChild(txt(W-R,Tp+11,hi.toFixed(1)+' kJ',{'font-size':10,fill:'#A855F7','text-anchor':'end','font-weight':'bold'}));
}
function renderAll(){
  var dpVal = (ovData && ovData.dp && ovData.dp.current!=null) ? ovData.dp.current : (N>0 ? data[N-1].dp : 10);
  var mpVal = (ovData && ovData.mp && ovData.mp.current!=null) ? ovData.mp.current : (N>0 ? data[N-1].mp : 5);
  drawGauge('dpGauge', dpVal, TH.dp, 10, 22, 'cmH₂O');
  drawGauge('mpGauge', mpVal, TH.mp, 8, 28, 'J/min');
  drawRiskBand();
  drawRiskMap('rmMini',null,420,190,false);
  drawRiskMap('rmBig','rmTip',860,520,true);
  renderDp(curWin.dp); renderMp(curWin.mp);
  renderAlerts();
}
function gotoPage(pg){
  document.querySelectorAll('.page').forEach(function(p){p.classList.remove('on');});
  document.getElementById('pg-'+pg).classList.add('on');
  document.querySelectorAll('.navitem').forEach(function(n){ n.classList.toggle('on', n.dataset.page===pg); });
  if(pg==='dp') renderDp(curWin.dp);
  if(pg==='mp') renderMp(curWin.mp);
  if(pg==='rm') drawRiskMap('rmBig','rmTip',860,520,true);
  if(pg==='ov'){
    var dpVal = (ovData && ovData.dp && ovData.dp.current!=null) ? ovData.dp.current : (N>0 ? data[N-1].dp : 10);
    var mpVal = (ovData && ovData.mp && ovData.mp.current!=null) ? ovData.mp.current : (N>0 ? data[N-1].mp : 5);
    drawGauge('dpGauge', dpVal, TH.dp, 10, 22, 'cmH₂O'); drawGauge('mpGauge', mpVal, TH.mp, 8, 28, 'J/min'); drawRiskBand(); drawRiskMap('rmMini',null,420,190,false);
  }
}

// ==================== Navigation ====================
document.querySelectorAll('.navitem').forEach(function(n){ n.addEventListener('click',function(){ gotoPage(n.dataset.page); }); });
document.getElementById('dpWin').addEventListener('click',function(e){ if(e.target.dataset.w){ [].forEach.call(this.querySelectorAll('.btn'),function(b){ b.classList.remove('on');}); e.target.classList.add('on'); curWin.dp=+e.target.dataset.w; renderDp(curWin.dp);} });
document.getElementById('mpWin').addEventListener('click',function(e){ if(e.target.dataset.w){ [].forEach.call(this.querySelectorAll('.btn'),function(b){ b.classList.remove('on');}); e.target.classList.add('on'); curWin.mp=+e.target.dataset.w; renderMp(curWin.mp);} });

// ==================== Threshold Settings ====================
var sldDp=document.getElementById('sldDp'), sldMp=document.getElementById('sldMp');
sldDp.oninput=function(){ document.getElementById('sldDpV').textContent=(+this.value).toFixed(1); };
sldMp.oninput=function(){ document.getElementById('sldMpV').textContent=(+this.value).toFixed(1); };
function resetTh(){ sldDp.value=15; sldMp.value=17; document.getElementById('sldDpV').textContent='15.0'; document.getElementById('sldMpV').textContent='17.0'; toast('已恢复默认值（未保存）'); }
function askSaveTh(){
  document.getElementById('modalTxt').innerHTML='ΔP 阈值：<b>'+TH.dp.toFixed(1)+' -> '+(+sldDp.value).toFixed(1)+' cmH₂O</b><br>MP 阈值：<b>'+TH.mp.toFixed(1)+' -> '+(+sldMp.value).toFixed(1)+' J/min</b><br><br>阈值变更将影响风险分级与预警判定，并记入审计日志。';
  document.getElementById('modal').classList.add('on');
}
document.getElementById('modalOk').onclick=function(){
  TH.dp=+sldDp.value; TH.mp=+sldMp.value;
  var tr=document.getElementById('auditBody');
  var now=new Date();
  tr.innerHTML='<tr><td>'+now.getFullYear()+'-'+('0'+(now.getMonth()+1)).slice(-2)+'-'+('0'+now.getDate()).slice(-2)+' '+('0'+now.getHours()).slice(-2)+':'+('0'+now.getMinutes()).slice(-2)+'</td><td>操作员</td><td>ΔP / MP 阈值</td><td>15.0 / 17.0</td><td><b>'+TH.dp.toFixed(1)+' / '+TH.mp.toFixed(1)+'</b></td><td>个体化调整</td></tr>'+tr.innerHTML;
  closeModal(); renderAll(); toast('阈值已更新，风险分级与图表已重新计算');
};
function closeModal(){ document.getElementById('modal').classList.remove('on'); }
document.getElementById('modal').addEventListener('click',function(e){ if(e.target===this) closeModal(); });

// ==================== Toast ====================
var toastTimer=null;
function toast(msg){
  var t=document.getElementById('toast'); t.textContent=msg; t.style.display='block';
  clearTimeout(toastTimer); toastTimer=setTimeout(function(){ t.style.display='none'; },2600);
}

// ==================== Audit Init ====================
document.getElementById('auditBody').innerHTML=
 '<tr><td>系统默认</td><td>系统</td><td>ΔP / MP 阈值</td><td>-</td><td>15.0 / 17.0</td><td>循证默认（Amato 2015 / Chest 2025）</td></tr>';

// ==================== Clock ====================
setInterval(function(){
  var d=new Date();
  document.getElementById('clk').textContent=('0'+d.getHours()).slice(-2)+':'+('0'+d.getMinutes()).slice(-2)+':'+('0'+d.getSeconds()).slice(-2);
},1000);

// ==================== API: Fetch Trends ====================
async function fetchTrends(){
  try {
    var p1 = fetch(API+'/api/dp/trend?hours=24&points=1440').then(function(r){return r.json();});
    var p2 = fetch(API+'/api/mp/trend?hours=24&points=1440').then(function(r){return r.json();});
    var results = await Promise.all([p1, p2]);
    var dpRes = results[0], mpRes = results[1];
    var dpSeries = dpRes.series || [];
    var mpSeries = mpRes.series || [];
    var len = Math.max(dpSeries.length, mpSeries.length);
    data = [];
    var cumE = 0;
    for(var i=0; i<len; i++){
      var dpVal = dpSeries[i] ? dpSeries[i].value : null;
      var mpVal = mpSeries[i] ? mpSeries[i].value : null;
      var dt = dpSeries[i] ? dpSeries[i].dt : (mpSeries[i] ? mpSeries[i].dt : '');
      var t = '';
      if(dt){ var d = new Date(dt); t = ('0'+d.getHours()).slice(-2)+':'+('0'+d.getMinutes()).slice(-2); }
      var dpValid = dpVal !== null && dpVal !== undefined;
      var mpValid = mpVal !== null && mpVal !== undefined;
      if(mpValid) cumE += mpVal;
      data.push({
        i: i, t: t,
        dp: dpValid ? dpVal : 0,
        mp: mpValid ? mpVal : 0,
        dpValid: dpValid, mpValid: mpValid,
        dpSt: dpValid ? 'VALID' : 'INVALID',
        mpSt: mpValid ? 'VALID' : 'MP_SUSPENDED',
        cum: cumE / 1000
      });
    }
    N = data.length;
    if(dpSeries[0] && dpSeries[0].threshold) TH.dp = dpSeries[0].threshold;
    if(mpSeries[0] && mpSeries[0].threshold) TH.mp = mpSeries[0].threshold;
    // Simple change point detection
    changePts = [];
    for(var ci=10; ci<N; ci++){
      if(data[ci].dpValid && data[ci-1].dpValid){
        var diff = Math.abs(data[ci].dp - data[ci-1].dp);
        if(diff > 3){
          changePts.push({i: ci, txt: 'ΔP 变化 '+data[ci-1].dp.toFixed(1)+'->'+data[ci].dp.toFixed(1)+' cmH₂O（'+data[ci].t+'）'});
        }
      }
    }
    if(changePts.length > 5) changePts = changePts.slice(0, 5);
    console.log('Trends loaded: '+N+' points, '+changePts.length+' change points');
  } catch(e) {
    console.error('fetchTrends error:', e);
  }
}

// ==================== API: Fetch Overview ====================
async function fetchOverview(){
  try {
    var r = await fetch(API+'/api/overview');
    ovData = await r.json();
    updateOverviewUI(ovData);
  } catch(e) { console.error('fetchOverview error:', e); }
}

function updateOverviewUI(d){
  if(!d || d.error) return;
  // Rating band
  var band = document.querySelector('.rating-band');
  if(band){
    var lvClass = Math.min((d.risk_level||1) - 1, 3);  // 后端1~4 → CSS lv-0~lv-3
    band.className = 'rating-band lv-' + lvClass;
    var lvEl = band.querySelector('.lv');
    if(lvEl) lvEl.textContent = 'L' + (d.risk_level||0);
    var ttlEl = band.querySelector('.ttl');
    if(ttlEl) ttlEl.textContent = d.risk_label || '正常';
    var subEl = band.querySelector('.sub');
    if(subEl){
      var subText = '';
      if((d.risk_level||0) <= 1) subText = '保护性通气，ΔP 与 MP 均在安全范围';
      else if((d.risk_level||0) === 2) subText = '关注：接近阈值上限，建议评估参数';
      else subText = '存在超阈风险，建议尽快调整通气参数';
      subEl.textContent = subText;
    }
    var pillsEl = band.querySelector('.pills');
    if(pillsEl){
      var overCnt = 0;
      if(d.dp && d.dp.over_pct > 0) overCnt++;
      if(d.mp && d.mp.over_pct > 0) overCnt++;
      pillsEl.innerHTML = '<span>越限 <b>'+overCnt+'</b></span><span>安全 <b>'+(2-overCnt)+'</b></span>';
    }
  }
  // Alarm bar
  var alarmBar = document.querySelector('.alarm');
  if(alarmBar){
    var alarmText = alarmBar.querySelector('.grow');
    if((d.risk_level||0) >= 2){
      alarmBar.classList.remove('ok');
      var msg = '';
      if(d.dp && d.dp.current >= d.dp.threshold) msg += 'ΔP 超限 ';
      if(d.mp && d.mp.current >= d.mp.threshold) msg += 'MP 超限 ';
      msg += '· ΔP '+(d.dp? d.dp.current:0).toFixed(1)+'/'+(d.dp? d.dp.threshold:15)+' · MP '+(d.mp? d.mp.current:0).toFixed(2)+'/'+(d.mp? d.mp.threshold:17);
      if(alarmText) alarmText.textContent = msg;
    } else {
      alarmBar.classList.add('ok');
      if(alarmText) alarmText.textContent = '系统正常 · 无活动预警';
    }
  }
  // Riskchip
  var chip = document.querySelector('.riskchip');
  if(chip){
    var chipSpans = chip.querySelectorAll('span');
    if(chipSpans[1]) chipSpans[1].textContent = d.risk_label || '正常';
    if((d.risk_level||0) >= 2){ chip.classList.remove('ok'); chip.style.background='var(--dan)'; }
    else { chip.classList.add('ok'); chip.style.background='var(--ok)'; }
  }
  // Vent duration
  var ventEl = document.getElementById('ventdur');
  if(ventEl && d.cumulative) ventEl.textContent = fmtDur(d.cumulative.vent_duration_min || 0);
  // Work mode
  var wmEl = document.getElementById('workmode');
  if(wmEl && d.work_mode) wmEl.textContent = d.work_mode;
  var snapMode = document.getElementById('snapMode');
  if(snapMode && d.work_mode) snapMode.textContent = d.work_mode;
  // Gauges
  var dpVal = (d.dp && d.dp.current!=null) ? d.dp.current : 0;
  var mpVal = (d.mp && d.mp.current!=null) ? d.mp.current : 0;
  drawGauge('dpGauge', dpVal, TH.dp, 10, 22, 'cmH₂O');
  drawGauge('mpGauge', mpVal, TH.mp, 8, 28, 'J/min');
  // Gauge messages and badges
  var dpBadge = document.getElementById('dpCardBadge');
  var dpMsg = document.getElementById('dpGaugeMsg');
  if(dpBadge && dpMsg){
    if(dpVal >= TH.dp){ dpBadge.textContent='偏 高'; dpBadge.className='badge b-warn'; dpMsg.textContent='已超阈值'; dpMsg.style.color='var(--warn)'; }
    else if(dpVal >= TH.dp - 2){ dpBadge.textContent='关 注'; dpBadge.className='badge b-watch'; dpMsg.textContent='接近阈值'; dpMsg.style.color='var(--watch)'; }
    else { dpBadge.textContent='正 常'; dpBadge.className='badge b-ok'; dpMsg.textContent='安全范围'; dpMsg.style.color='var(--ok)'; }
  }
  var mpBadge = document.getElementById('mpCardBadge');
  var mpMsg = document.getElementById('mpGaugeMsg');
  if(mpBadge && mpMsg){
    if(mpVal >= TH.mp){ mpBadge.textContent='偏 高'; mpBadge.className='badge b-warn'; mpMsg.textContent='已超阈值'; mpMsg.style.color='var(--warn)'; }
    else if(mpVal >= TH.mp - 3){ mpBadge.textContent='关 注'; mpBadge.className='badge b-watch'; mpMsg.textContent='接近阈值'; mpMsg.style.color='var(--watch)'; }
    else { mpBadge.textContent='正 常'; mpBadge.className='badge b-ok'; mpMsg.textContent='安全范围'; mpMsg.style.color='var(--ok)'; }
  }
  // Risk card border
  var dpCard = document.querySelector('.risk-grid .risk-card:nth-child(1)');
  if(dpCard){ var dplv=gaugeLevel(dpVal, TH.dp, 22); dpCard.className='risk-card lv-'+Math.min(dplv,3); }
  var mpCard = document.querySelector('.risk-grid .risk-card:nth-child(2)');
  if(mpCard){ var mplv=gaugeLevel(mpVal, TH.mp, 28); mpCard.className='risk-card lv-'+Math.min(mplv,3); }
  // Status bar
  var statusEls = document.querySelectorAll('#statusbar span');
  if(statusEls[2]) statusEls[2].textContent = '计算引擎：Python · 实时计算';
  // Update last data point from overview
  if(N > 0 && d.dp && d.mp){
    data[N-1].dp = d.dp.current || data[N-1].dp;
    data[N-1].mp = d.mp.current || data[N-1].mp;
    data[N-1].dpValid = d.dp.current != null;
    data[N-1].mpValid = d.mp.current != null;
  }
}

// ==================== API: Fetch Alerts ====================
async function fetchAlertsData(){
  try {
    var r = await fetch(API+'/api/alerts?hours=168');
    var d = await r.json();
    alerts = (d.alerts || []).map(function(a){
      var dt = a.ts ? new Date(a.ts) : new Date();
      var hh = ('0'+dt.getHours()).slice(-2), mm = ('0'+dt.getMinutes()).slice(-2);
      var timeStr = hh+':'+mm+' - '+(a.active===false ? '已恢复' : '进行中');
      return {
        lv: Math.min(a.risk_level||2, 3),
        lvN: a.risk_label || ('L'+(a.risk_level||2)),
        msg: a.message || a.risk_label || '风险提示',
        meta: a.detail || '',
        time: timeStr,
        act: a.active !== false,
        ack: a.acknowledged_by || null
      };
    });
    renderAlerts();
  } catch(e) { console.error('fetchAlerts error:', e); }
}

// ==================== WebSocket ====================
function connectWS(){
  var wsUrl = (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws';
  try { ws = new WebSocket(wsUrl); } catch(e) { setTimeout(connectWS, 3000); return; }
  ws.onopen = function(){ console.log('WebSocket connected'); };
  ws.onmessage = function(evt){
    try {
      var msg = JSON.parse(evt.data);
      if(msg.type === 'overview' && msg.data){
        ovData = msg.data;
        updateOverviewUI(msg.data);
      }
    } catch(e) { console.error('ws parse error:', e); }
  };
  ws.onclose = function(){ console.log('WebSocket disconnected, reconnecting...'); setTimeout(connectWS, 3000); };
  ws.onerror = function(){ if(ws) ws.close(); };
}

// ==================== Init ====================
async function init(){
  await fetchOverview();
  await fetchTrends();
  await fetchAlertsData();
  renderAll();
  connectWS();
  setInterval(fetchOverview, 10000);
}

requestAnimationFrame(function(){ setTimeout(init, 60); });
window.addEventListener('resize', function(){ setTimeout(renderAll, 120); });
'''

# ── 组装输出 ──
html = HTML_HEAD + CSS + CSS_ADD + '</style>\n</head>\n' + BODY + '<script>\n' + NEW_JS + '\n</script>\n</body>\n</html>'

out_path = os.path.join(OUTPUT_DIR, 'cockpit_frontend.html')
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(html)
print(f'OK {len(html)} bytes -> {out_path}')
