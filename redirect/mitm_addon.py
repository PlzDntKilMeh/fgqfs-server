"""
mitmproxy addon - redirects FG:QfS traffic to the local private server
AND logs every request/response to a JSONL file for endpoint discovery.

Usage:
    mitmdump -s redirect/mitm_addon.py
    mitmdump -s redirect/mitm_addon.py --set log_file=captures/live/traffic.jsonl

Environment variables:
    FGQFS_HOST       private server host (default: 127.0.0.1)
    FGQFS_PORT       private server port (default: 6767)
    FGQFS_REDIRECT   set to "0" to capture live traffic without redirecting it
    FGQFS_LOG_ALL    set to "1" to log non-FG hosts too (default: FG hosts only)
    FGQFS_OFFLINE    set to "0" to allow unknown hosts through (default: block)

Log format: one JSON object per line, written to captures/live/traffic.jsonl
by default (or --set log_file=...)
Each object has:
    ts          unix timestamp (float)
    direction   "request" or "response"  [response includes matching request fields]
    host        original destination host
    method      HTTP method
    path        URL path
    status      HTTP status code (response only)
    rpc_names   list of RPC names from the 'rpc' header (tapservice calls)
    rpcs        parsed data[] array from tapservice POST body
    body_raw    first 4096 bytes of body as hex (for binary/unknown content)
    body_json   parsed JSON body if applicable
    response_json  parsed JSON response (response events)
    x_tc_digest   x-tc-digest header value (response only)
    redirected  true if we rewrote the destination
    cdn_path    original CDN path before rewrite (CDN calls only)
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
from pathlib import Path
from typing import Any

from mitmproxy import http, ctx

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PRIVATE_HOST = os.environ.get("FGQFS_HOST", "127.0.0.1")
PRIVATE_PORT = int(os.environ.get("FGQFS_PORT", "6767"))
REDIRECT_ENABLED = os.environ.get("FGQFS_REDIRECT", "1") != "0"
LOG_ALL      = os.environ.get("FGQFS_LOG_ALL", "0") == "1"

TAPSERVICE_HOST = "familyguy.tinyco.com"
CDN_HOSTS = {
    "cdn2.familyguy.tinyco.com",
    "cdn.familyguy.tinyco.com",
    "cdn1.familyguy.tinyco.com",
}
# Analytics / crash-reporting hosts redirected to our stub endpoints so the
# game doesn't block at startup waiting for them to time out.
ANALYTICS_HOSTS = {
    "c.tinyco.com",          # TinyCo event collector (/t/api/1/)
    "sentry.io",             # Sentry crash reporting
    "o431949.ingest.sentry.io",
    "o574997.ingest.sentry.io",
}
# Game asset hosts: textures, audio, asset bundles.
# Our server lazy-fetches and caches any missing files on first request.
ASSET_HOSTS = {
    "family-guy-qfs.jamcity-static.com",
    "family-guy-qfs-config.jamcity-static.com",
    # Akamai CDN hosts. The current APK references the "-a" hosts directly;
    # the others are defensive mirror variants.
    "staticfg-a.akamaihd.net",
    "staticfg-b.akamaihd.net",
    "staticfg.akamaihd.net",
    "configfg-a.akamaihd.net",
    "configfg-b.akamaihd.net",
    "configfg.akamaihd.net",
}
ALL_FG_HOSTS = {TAPSERVICE_HOST} | CDN_HOSTS | ANALYTICS_HOSTS | ASSET_HOSTS

# When True, any request NOT in ALL_FG_HOSTS gets an immediate 503 response
# instead of reaching the real internet: full offline mode.
OFFLINE_MODE = os.environ.get("FGQFS_OFFLINE", "1") == "1"

# Hosts that are always allowed through even in offline mode.
# Includes our own private server host so CDN catalog downloads sent directly
# to the local server are not blocked by the offline filter.
_OFFLINE_PASSTHROUGH: set[str] = {
    "localhost",
    "127.0.0.1",
    PRIVATE_HOST,
    os.environ.get("FGQFS_EXTERNAL_HOST", "127.0.0.1"),
}

_log_path: Path = Path("captures/live/traffic.jsonl")
_log_file = None


# ---------------------------------------------------------------------------
# Addon lifecycle
# ---------------------------------------------------------------------------

def load(loader):
    loader.add_option("log_file", str, "captures/live/traffic.jsonl",
                      "Path to JSONL traffic log file")


def configure(updated):
    global _log_path, _log_file
    if "log_file" in updated:
        new_path = Path(ctx.options.log_file)
        if new_path != _log_path:
            if _log_file:
                _log_file.close()
            _log_path = new_path
            _log_file = None
    _ensure_log_open()


def _ensure_log_open():
    global _log_file
    if _log_file is None or _log_file.closed:
        _log_path.parent.mkdir(parents=True, exist_ok=True)
        _log_file = _log_path.open("a", encoding="utf-8")
        ctx.log.info(f"[fgqfs] Traffic log: {_log_path.resolve()}")


def _write(record: dict) -> None:
    if _log_file and not _log_file.closed:
        _log_file.write(json.dumps(record, separators=(",", ":")) + "\n")
        _log_file.flush()


# ---------------------------------------------------------------------------
# Body parsers
# ---------------------------------------------------------------------------

def _try_json(raw: bytes) -> Any:
    try:
        return json.loads(raw)
    except Exception:
        return None


def _parse_tapservice_body(raw: bytes) -> dict[str, Any] | None:
    """
    Parse 'request=<urlencoded_json>&chksum=<md5>' and return the payload dict.
    Returns None on failure.
    """
    try:
        text = raw.decode("utf-8", errors="replace")
        parts = dict(p.split("=", 1) for p in text.split("&") if "=" in p)
        payload = json.loads(urllib.parse.unquote(parts.get("request", "{}")))
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Request hook
# ---------------------------------------------------------------------------

def request(flow: http.HTTPFlow) -> None:
    host = flow.request.pretty_host
    is_fg = host in ALL_FG_HOSTS

    # ── Offline block — unknown hosts never reach the internet ───────────────
    if OFFLINE_MODE and not is_fg and host not in _OFFLINE_PASSTHROUGH:
        ctx.log.info(f"[fgqfs] BLOCKED {host}{flow.request.path[:60]}")
        flow.response = http.Response.make(
            503,
            b'{"error":"offline"}',
            {"Content-Type": "application/json", "X-Fgqfs-Blocked": "1"},
        )
        _write({
            "ts": time.time(), "direction": "blocked",
            "host": host, "method": flow.request.method,
            "path": flow.request.path,
        })
        return

    if not is_fg and not LOG_ALL:
        return

    redirected = False
    cdn_path   = None

    # ── Rewrite destination ──────────────────────────────────────────────────
    if REDIRECT_ENABLED and host == TAPSERVICE_HOST:
        flow.request.host   = PRIVATE_HOST
        flow.request.port   = PRIVATE_PORT
        flow.request.scheme = "http"
        redirected = True

    elif REDIRECT_ENABLED and host in CDN_HOSTS:
        cdn_path = flow.request.path
        flow.request.host   = PRIVATE_HOST
        flow.request.port   = PRIVATE_PORT
        flow.request.scheme = "http"
        if not cdn_path.startswith("/cdn/"):
            flow.request.path = "/cdn" + cdn_path
        redirected = True

    elif REDIRECT_ENABLED and host in ANALYTICS_HOSTS:
        flow.request.host   = PRIVATE_HOST
        flow.request.port   = PRIVATE_PORT
        flow.request.scheme = "http"
        redirected = True

    elif REDIRECT_ENABLED and host in ASSET_HOSTS:
        flow.request.host   = PRIVATE_HOST
        flow.request.port   = PRIVATE_PORT
        flow.request.scheme = "http"
        redirected = True

    # ── Build log record ─────────────────────────────────────────────────────
    body_raw = flow.request.content or b""
    content_type = flow.request.headers.get("content-type", "")
    rpc_header = (flow.request.headers.get("rpc", "") or
                  flow.request.headers.get("x-rpc", ""))

    record: dict = {
        "ts":         time.time(),
        "direction":  "request",
        "host":       host,
        "method":     flow.request.method,
        "path":       flow.request.path,
        "rpc_names":  [r.strip() for r in rpc_header.split(",") if r.strip()],
        "redirected": redirected,
    }

    if cdn_path:
        record["cdn_path"] = cdn_path

    if "form" in content_type or host == TAPSERVICE_HOST:
        parsed_payload = _parse_tapservice_body(body_raw)
        parsed_rpcs = parsed_payload.get("data", []) if isinstance(parsed_payload, dict) else []
        if parsed_rpcs:
            record["rpcs"] = parsed_rpcs
            player_id = parsed_payload.get("player_id", "")
            if isinstance(player_id, str) and player_id.strip():
                record["player_id"] = player_id
        else:
            record["body_hex"] = body_raw[:4096].hex()
    elif body_raw:
        j = _try_json(body_raw)
        if j is not None:
            record["body_json"] = j
        else:
            record["body_hex"] = body_raw[:4096].hex()

    # Stash for response hook
    flow.metadata["fgqfs_req_record"] = record
    _write(record)

    # Console summary
    if host == TAPSERVICE_HOST:
        rpc_list = [c[0] for c in record.get("rpcs", []) if c]
        ctx.log.info(f"[fgqfs] → tapservice  rpcs={rpc_list}")
    elif host in CDN_HOSTS:
        ctx.log.info(f"[fgqfs] → cdn  {cdn_path}")
    elif LOG_ALL:
        ctx.log.info(f"[fgqfs] → {host}{flow.request.path[:80]}")


# ---------------------------------------------------------------------------
# Response hook
# ---------------------------------------------------------------------------

def response(flow: http.HTTPFlow) -> None:
    host = flow.metadata.get("fgqfs_req_record", {}).get("host", "")
    is_fg = host in ALL_FG_HOSTS

    if not is_fg and not LOG_ALL:
        return

    req_record = flow.metadata.get("fgqfs_req_record", {})
    body_raw   = flow.response.content or b""
    digest     = flow.response.headers.get("x-tc-digest", "")

    record: dict = {
        "ts":           time.time(),
        "direction":    "response",
        "host":         host,
        "method":       req_record.get("method", ""),
        "path":         req_record.get("path", ""),
        "status":       flow.response.status_code,
        "rpc_names":    req_record.get("rpc_names", []),
        "x_tc_digest":  digest,
    }

    if req_record.get("rpcs"):
        record["req_rpcs"] = req_record["rpcs"]

    j = _try_json(body_raw)
    if j is not None:
        record["response_json"] = j
        # Flag anything in the response we haven't seen before
        _flag_unknown_response_keys(record, j, req_record)
    else:
        record["body_hex"] = body_raw[:4096].hex()

    _write(record)

    status = flow.response.status_code
    if host == TAPSERVICE_HOST:
        ctx.log.info(f"[fgqfs] ← tapservice  HTTP {status}  digest={digest[:16]}...")
    elif host in CDN_HOSTS:
        hit = status == 200
        ctx.log.info(f"[fgqfs] ← cdn  HTTP {status}  {'HIT' if hit else 'MISS'}")


# ---------------------------------------------------------------------------
# Heuristic: flag response keys that don't match our known RPC schema
# ---------------------------------------------------------------------------

# Keys we know appear in standard RPC responses — anything outside these gets flagged
_KNOWN_RESPONSE_KEYS: set[str] = {
    "response", "success", "error", "_stub", "rpc",
    # auth
    "salt", "challenge", "new_salt", "user_id", "username",
    "signed_salt", "player_id", "human_id", "errmsg",
    # session / config
    "session", "device_flags", "server_time", "locale",
    "asset_base_url", "cks", "cksAESKeys", "cksIV", "features",
    "adHocConfigs", "bson_types", "bson_types_handling",
    "config_tags", "env", "useCDN",
    # push / messaging
    "push_preferences", "content_pack_revisions",
    # save
    "save_version", "saved_game_pbuf", "time_slept",
    "player_state", "local_save_ok",
    # txn
    "Currency",
}


def _flag_unknown_response_keys(record: dict, parsed: Any, req_record: dict) -> None:
    if not isinstance(parsed, dict):
        return
    # Dig into response[*] entries
    for entry in parsed.get("response", []):
        if not isinstance(entry, dict):
            continue
        unknown = set(entry.keys()) - _KNOWN_RESPONSE_KEYS
        if unknown:
            record["unknown_keys"] = sorted(unknown)
            ctx.log.warn(
                f"[fgqfs] UNKNOWN response keys in {req_record.get('rpc_names', '?')}: "
                f"{sorted(unknown)}"
            )
