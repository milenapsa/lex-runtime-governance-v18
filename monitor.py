import json
import os
import time
import urllib.request
from datetime import datetime, timezone

BASE = os.getenv("LEX_MONITOR_BASE", "http://homosapiens-lex-runtime-governance-v18:8080").rstrip("/")
INTERVAL = max(60, int(os.getenv("LEX_MONITOR_INTERVAL", "300")))
EXPECTED_UNIQUE = int(os.getenv("LEX_MONITOR_EXPECTED_UNIQUE", "34"))
EXPECTED_ACTIVE = int(os.getenv("LEX_MONITOR_EXPECTED_ACTIVE", "20"))
EXPECTED_BLOCKED = int(os.getenv("LEX_MONITOR_EXPECTED_BLOCKED", "3"))
VERSION = "0.19.0-monitor"

def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def get(path, timeout=45):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as response:
        return response.status, json.load(response)

def post(query, timeout=120):
    body=json.dumps({"query":query,"limit":5},ensure_ascii=False).encode()
    req=urllib.request.Request(BASE+"/v1/search",data=body,headers={"Content-Type":"application/json"},method="POST")
    with urllib.request.urlopen(req,timeout=timeout) as response:
        return response.status,json.load(response)

def cycle():
    checks={}
    status,h=get("/health"); checks["health"]=status==200 and h.get("version")=="0.18.0-governance"
    status,r=get("/v1/sources/registry"); s=r.get("summary",{})
    checks["registry"]=(status==200 and s.get("registered_unique")==EXPECTED_UNIQUE and s.get("active_search")==EXPECTED_ACTIVE and s.get("blocked")==EXPECTED_BLOCKED)
    by={x.get("id"):x for x in r.get("sources",[])}
    checks["blocked_truth"]=all(by.get(i,{}).get("active_search") is False for i in ("stj_ckan","stf_corte_aberta","lexml_sru"))
    checks["tjpe_sources"]=all(by.get(i,{}).get("active_search") is True for i in ("tjpe_sumulas_tribunal","tjpe_enunciados_administrativos","tjpe_sumulas_teu"))
    status,rt=get("/v1/runtime/status"); checks["policies"]=status==200 and rt.get("policies",{}).get("synthetic_results_allowed") is False
    status,q=post("Lei 13.709/2018"); checks["search"]=status==200 and q.get("integrity",{}).get("synthetic")==0 and "senado_legislacao" in q.get("sources_used",[])
    payload={"ts":now(),"service":"lex-runtime-monitor","version":VERSION,"status":"ok" if all(checks.values()) else "degraded","expected":{"unique":EXPECTED_UNIQUE,"active":EXPECTED_ACTIVE,"blocked":EXPECTED_BLOCKED},"checks":checks,"trace_id":q.get("trace_id")}
    print(json.dumps(payload,ensure_ascii=False,separators=(",",":")),flush=True)

while True:
    try:
        cycle()
    except Exception as exc:
        print(json.dumps({"ts":now(),"service":"lex-runtime-monitor","version":VERSION,"status":"error","error_type":type(exc).__name__},separators=(",",":")),flush=True)
    time.sleep(INTERVAL)
