
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.getenv("PORT", "8080"))
UPSTREAM = os.getenv("LEX_UPSTREAM", "http://homosapiens-lex-tjba-curated-v16:8080").rstrip("/")
VERSION = "0.18.0-governance"
REGISTRY_VERSION = "2026-07-12.v1"
UA = "Lex-HomoSapiens-Governance/0.18"

SEARCH_PATHS = {
    "/v1/search",
    "/v1/search/global",
    "/v1/search/legislacao",
    "/v1/search/datasets",
}
SOURCE_PATHS = {"/v1/sources", "/v1/sources/registry"}

ROLE_PRIORITY = {
    "active_search": 0,
    "filter": 1,
    "reference": 2,
    "manual_portal": 3,
    "pending_secret": 4,
    "blocked": 5,
    "connectivity_only": 6,
    "unknown": 7,
}

def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def request_json(path: str, method: str = "GET", payload=None):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(UPSTREAM + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            body = {"error": "upstream_http_error", "status": exc.code}
        return exc.code, body

def classify(source: dict, active_ids: set[str]) -> tuple[str, bool, str]:
    source_id = str(source.get("id") or "")
    status = str(source.get("status") or "unknown")
    if source_id in active_ids:
        return "active_search", True, "online"
    if status == "online_filter":
        return "filter", False, "online"
    if status == "online_reference":
        return "reference", False, "online"
    if status == "manual_official_portal":
        return "manual_portal", False, "manual"
    if status == "needs_secret":
        return "pending_secret", False, "not_configured"
    if status in {
        "blocked_by_upstream_security_check",
        "dns_unreachable_from_runtime",
        "tls_validation_failed_from_runtime",
        "blocked",
        "unavailable",
    }:
        return "blocked", False, status
    if status == "online":
        return "connectivity_only", False, "online"
    return "unknown", False, status

def merge_source(current: dict, incoming: dict) -> dict:
    merged = dict(current)
    for key, value in incoming.items():
        if key not in merged or merged[key] in (None, "", [], {}):
            merged[key] = value
    # incoming URL is preferred when current lacks it.
    if incoming.get("url") and not current.get("url"):
        merged["url"] = incoming["url"]
    return merged

def normalized_registry() -> tuple[dict, dict]:
    health_status, health = request_json("/health")
    source_status, source_payload = request_json("/v1/sources")
    if health_status != 200 or source_status != 200:
        raise RuntimeError("upstream_registry_unavailable")

    active_ids = set(health.get("real_sources_online") or [])
    deduped: dict[str, dict] = {}
    duplicates = 0

    for raw in source_payload.get("sources") or []:
        source_id = str(raw.get("id") or "").strip()
        if not source_id:
            continue
        if source_id in deduped:
            duplicates += 1
            deduped[source_id] = merge_source(deduped[l£urce_id], raw)
        else:
            deduped[source_id] = dict(raw)

    normalized = []
    counts = {role: 0 for role in ROLE_PRIORITY}
    for source_id, source in deduped.items():
        role, queryable, connectivity = classify(source, active_ids)
        item = dict(source)
        item["operational_role"] = role
        item["active_search"] = role == "active_search"
        item["queryable_automatically"] = queryable
        item["connectivity_status"] = connectivity
        item["registry_version"] = REGISTRY_VERSION
        normalized.append(item)
        counts[role] += 1

    normalized.sort(key=lambda item: (ROLE_PRIORITY.get(item["operational_role"], 99), item["id"]))
    summary = {
        "registered_unique": len(normalized),
        "duplicates_removed": duplicates,
        "active_search": counts["active_search"],
        "filters": counts["filter"],
        "references": counts["reference"],
        "manual_portals": counts["manual_portal"],
        "pending_secret": counts["pending_secret"],
        "blocked": counts["blocked"],
        "connectivity_only": counts["connectivity_only"],
        "unknown": counts["unknown"],
    }
    payload = {
        "status": "ok",
        "service": "lex-source-governance",
        "version": VERSION,
        "upstream_version": source_payload.get("version") or health.get("version"),
        "registry_version": REGISTRY_VERSION,
        "generated_at": now_iso(),
        "summary": summary,
        "sources": normalized,
        "human_review_required": True,
        "no_invention_policy": True,
    }
    return payload, health

class Handler(BaseHTTPRequestHandler):
    server_version = "LexGovernance/0.18"

    def send_json(self, status: int, payload: dict):
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Lex-Registry-Version", REGISTRY_VERSION)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def read_body(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > 64_000:
            raise ValueError("payload_too_large")
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            if path in SOURCE_PATHS:
                payload, _ = normalized_registry()
                return self.send_json(200, payload)

            if path in {"/health", "/v1/health"}:
                registry, upstream = normalized_registry()
                payload = {
                    "status": "ok",
                    "service": "lex-search-governance",
                    "version": VERSION,
                    "upstream_version": upstream.get("version"),
                    "generated_at": now_iso(),
                    "registry_version": REGISTRY_VERSION,
                    "registry_summary": registry["summary"],
                    "real_sources_online": [
                        source["id"] for source in registry["sources"] if source["active_search"]
                    ],
                    "human_review_required": True,
                    "no_invention_policy": True,
                }
                return self.send_json(200, payload)

            if path in {"/ready", "/v1/readiness"}:
                registry, upstream = normalized_registry()
                ready = upstream.get("status") == "ok" and registry["summary"]["active_search"] > 0
                return self.send_json(
                    200 if ready else 503,
                    {
                        "status": "ready" if ready else "not_ready",
                        "version": VERSION,
                        "upstream_version": upstream.get("version"),
                        "registry_version": REGISTRY_VERSION,
                        "active_search_sources": registry["summary"]["active_search"],
                        "generated_at": now_iso(),
                    },
                )

            if path == "/v1/runtime/status":
                registry, upstream = normalized_registry()
                return self.send_json(
                    200,
                    {
                        "status": "ok",
                        "service": "lex-runtime-status",
                        "version": VERSION,
                        "upstream_version": upstream.get("version"),
                        "registry_version": REGISTRY_VERSION,
                        "summary": registry["summary"],
                        "policies": {
                            "human_review_required": True,
                            "no_invention": True,
                            "synthetic_results_allowed": False,
                        },
                        "generated_at": now_iso(),
                    },
                )

            status, body = request_json(path)
            return self.send_json((status), body)
        except Exception as exc:
            return self.send_json(
                503,
                {
                    "status": "degraded",
                    "error": "governance_runtime_error",
                    "detail": type(exc).__name__,
                    "generated_at": now_iso(),
                },
            )

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path not in SEARCH_PATHS:
            return self.send_json(404, {"error": "not_found"})
        try:
            payload = self.read_body()
            query = str(payload.get("query") or payload.get("q") or "").strip()
            if not query:
                return self.send_json(422, {"error": "query_required"})
            status, body = request_json(path if path != "/v1/search" else "/v1/search", "POST", payload)
            if isinstance(body, dict):
                body["runtime_governance_version"] = VERSION
                body["registry_version"] = REGISTRY_VERSION
            return self.send_json(status, body)
        except ValueError as exc:
            return self.send_json(413, {"error": str(exc)})
        except Exception as exc:
            return self.send_json(
                503,
                {
                    "status": "degraded",
                    "error": "governance_runtime_error",
                    "detail": type(exc).__name__,
                },
            )

    def log_message(self, fmt, *args):
        pass

ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
