# -*- coding: utf-8 -*-
"""Read-only probe: confirm freshness mechanisms. No writes."""
import sys, os
sys.path.insert(0, r"C:\Users\lwang\OneDrive\LungProtection\outputs")
os.environ.setdefault("COCKPIT_DEVICE_ID", "1787816609")
from lung_protection_cockpit.collector import get_db, get_latest_raw_batch, get_current_work_mode
from lung_protection_cockpit.config import COLL_1MIN, COLL_RAW
import math
from datetime import datetime, timezone

db = get_db()
dev = "1787816609"

# 1) latest raw batch + live validity (mirror compute_live_current)
raw = get_latest_raw_batch(db, dev)
print("=== latest raw batch ===")
if raw:
    ts = int(raw[0]["timeStamp"])
    from lung_protection_cockpit.calculator import enrich_rows
    row = {}
    from lung_protection_cockpit.collector import PARAM_MAP, to_float
    for d in raw:
        pname = PARAM_MAP.get(d.get("paramId"))
        if pname: row[pname] = to_float(d.get("value"))
    row["ts"] = ts
    enrich_rows([row])
    dp = row.get("dP", float("nan")); mp = row.get("MP", float("nan"))
    valid = not (math.isnan(dp) or dp <= 0 or math.isnan(mp))
    age = int(datetime.now(timezone.utc).timestamp() - ts/1000.0)
    print(f"  ts={ts} ({datetime.fromtimestamp(ts/1000,tz=timezone.utc).isoformat()}) age={age}s")
    print(f"  has dP={not math.isnan(dp)} dp={dp}, has MP={not math.isnan(mp)} mp={mp}")
    print(f"  -> LIVE valid = {valid}")
    print(f"  batch paramIds present: {sorted(set(d.get('paramId') for d in raw))}")
else:
    print("  (no raw batch)")

# 2) metrics_1min lag vs latest raw
print("=== metrics_1min vs raw ===")
latest_raw = db[COLL_RAW].find_one({"deviceId": dev}, sort=[("timeStamp", -1)])
latest_1min = db[COLL_1MIN].find_one({"deviceId": dev}, sort=[("minute", -1)])
if latest_raw and latest_1min:
    rt = int(latest_raw["timeStamp"]); mt = int(latest_1min["minute"])
    print(f"  latest raw ts      = {rt}")
    print(f"  latest 1min minute = {mt} ({(mt//60000)*60000})")
    print(f"  gap (raw - 1min)   = {(rt-mt)/1000.0:.1f}s  -> ~{(rt-mt)//60000} min behind")
    print(f"  1min is_ventilating={latest_1min.get('is_ventilating')}, dp_mean={latest_1min.get('dp_mean')}, mp_mean={latest_1min.get('mp_mean')}")

# 3) work_mode
print("=== work_mode ===")
wm = get_current_work_mode(db, dev)
print(f"  current work_mode = {wm}")
