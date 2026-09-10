# -*- coding: utf-8 -*-
"""在单次进程内启动 uvicorn 并跑完所有接口断言（venv 无 httpx，用 stdlib urllib）。"""
import json, os, subprocess, sys, time, urllib.request, urllib.error

PY = os.path.join('.venv', 'Scripts', 'python.exe')
PORT = 8091
BASE = 'http://127.0.0.1:%d' % PORT
env = dict(os.environ, COCKPIT_PORT=str(PORT))

proc = subprocess.Popen(
    [PY, '-m', 'uvicorn', 'lung_protection_cockpit.api:app',
     '--host', '127.0.0.1', '--port', str(PORT), '--log-level', 'warning'],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
)

def get(path, timeout=30):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))

fails = []
def check(cond, label, extra=''):
    print(('  [OK] ' if cond else '  [FAIL] ') + label + (('  ' + str(extra)) if extra else ''))
    if not cond:
        fails.append(label)

try:
    # 等待就绪
    ready = False
    for _ in range(40):
        time.sleep(1)
        try:
            get('/api/health', timeout=3); ready = True; break
        except Exception:
            pass
    check(ready, '后端启动就绪')
    if not ready:
        print(proc.stdout.read().decode('utf-8', 'replace')[:3000]); sys.exit(1)

    # 路由注册
    spec = get('/openapi.json')
    paths = set(spec.get('paths', {}).keys())
    for p in ['/api/overview','/api/analysis','/api/dp/trend','/api/mp/trend',
              '/api/risk-map','/api/alerts','/api/metrics/1min','/api/exposure-summary']:
        check(p in paths, '路由已注册 %s' % p)

    # 新接口 6 组合
    print('\n-- /api/exposure-summary 全档位 --')
    for param in ('dp','mp'):
        for h in (1,6,24):
            d = get('/api/exposure-summary?param=%s&hours=%d' % (param, h))
            L = d.get('limits') or {}; m = d.get('metrics') or {}; c = d.get('conclusion') or {}
            n = len(d.get('series') or [])
            print('  %s/%dh: stratum=%s crs=%s dcr=%s n=%d thr=%s L3=%s L4=%s TAT=%s AUC=%s PTA=%s peak=%s -> L%s %s'
                  % (param,h,d.get('stratum'),d.get('compliance_mean'),d.get('dcr'),n,
                     L.get('instant_thr'),L.get('tat_l3_h'),L.get('tat_l4_h'),
                     m.get('tat_h'),m.get('auc'),m.get('pta'),m.get('peak'),
                     c.get('level'),c.get('label')))
            check(d.get('param')==param, '%s/%dh param 回显' % (param,h))
            check(c.get('level') in (0,1,2,3), '%s/%dh 结论等级合法' % (param,h), c.get('level'))
            check(bool(c.get('hint')) and bool(c.get('advice')) and bool(c.get('basis')),
                  '%s/%dh 结论三要素齐全' % (param,h))
            # 阈值口径校验
            if d.get('stratum') in ('high','low'):
                if param=='dp':
                    check(L.get('instant_thr')==15.0, 'ΔP 瞬时阈值恒为 15', L.get('instant_thr'))
                    check((L.get('tat_l3_h'),L.get('tat_l4_h'))==(2.0,6.0), 'ΔP L3/L4 = 2/6h')
                else:
                    exp = (18.0,2.0,6.0) if d['stratum']=='high' else (20.0,12.0,24.0)
                    got = (L.get('instant_thr'),L.get('tat_l3_h'),L.get('tat_l4_h'))
                    check(got==exp, 'MP %s 层阈值 %s' % (d['stratum'], exp), got)
            # NaN 清洗
            raw = json.dumps(d)
            check('NaN' not in raw and 'Infinity' not in raw, '%s/%dh 响应无 NaN/Inf' % (param,h))

    # 非法 param
    try:
        get('/api/exposure-summary?param=xx&hours=6')
        check(False, '非法 param 应 422')
    except urllib.error.HTTPError as e:
        check(e.code==422, '非法 param 返回 422', e.code)

    # 老接口回归
    print('\n-- 既有接口回归 --')
    ov = get('/api/overview')
    check(isinstance(ov, dict) and 'dp' in ov and 'mp' in ov, '/api/overview 结构')
    check('NaN' not in json.dumps(ov), '/api/overview 无 NaN')
    an = get('/api/analysis?hours=24')
    check('grade' in an and 'slope' in an and 'cusum' in an, '/api/analysis 结构')
    check('NaN' not in json.dumps(an), '/api/analysis 无 NaN')
    al = get('/api/alerts')
    check(isinstance(al, dict) and 'alerts' in al and 'active_count' in al, '/api/alerts 结构')
    check('NaN' not in json.dumps(al), '/api/alerts 无 NaN')
    rm = get('/api/risk-map?hours=24')
    check(isinstance(rm, dict), '/api/risk-map 结构')
    check('NaN' not in json.dumps(rm), '/api/risk-map 无 NaN')
    for k in ('dp','mp'):
        tr = get('/api/%s/trend?hours=6' % k)
        check('NaN' not in json.dumps(tr), '/api/%s/trend 无 NaN' % k)

finally:
    proc.terminate()
    try: proc.wait(timeout=8)
    except Exception: proc.kill()

print('\n===== 结果：%s =====' % ('全部通过' if not fails else ('失败 %d 项: %s' % (len(fails), fails))))
sys.exit(1 if fails else 0)
