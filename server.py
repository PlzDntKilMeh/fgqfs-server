#!/usr/bin/env python3
"""
FG:QfS private server main entry point.
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from admin import router as admin_router
import cdn
import config
import db
import wire
from handlers import auth as auth_handlers
from handlers import save as save_handlers
from handlers import session as session_handlers

_ROOT = Path(__file__).parent
_LOG_FILE = _ROOT / "server.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(_LOG_FILE, mode="a", encoding="utf-8"),
    ],
)
log = logging.getLogger("server")

app = FastAPI(title="FG:QfS Private Server", version="0.1.0")
app.mount("/admin/static", StaticFiles(directory=str(_ROOT / "static")), name="admin_static")
app.include_router(admin_router)
_verify_chksum = True
_unknown_rpc_log: Path = _ROOT / "unknown_rpcs.jsonl"
_unknown_log_file = None
_cdn_access_log = _ROOT / "cdn_access.log"
_certs_dir = _ROOT / "mitmproxy"


def _serve_cert_file(filename: str) -> Response:
    path = _certs_dir / Path(filename).name
    if not path.is_file():
        return Response(status_code=404, content=f"Cert not found: {filename}")
    media_type = "application/octet-stream"
    if path.suffix.lower() in {".cer", ".pem"}:
        media_type = "application/x-x509-ca-cert"
    elif path.suffix.lower() == ".p12":
        media_type = "application/x-pkcs12"
    return FileResponse(str(path), media_type=media_type, filename=path.name)


def _serve_default_cert() -> Response:
    return _serve_cert_file("mitmproxy-ca-cert.cer")


def _log_unknown_rpc(rpc_name: str, args: list, envelope: dict) -> None:
    global _unknown_log_file
    if _unknown_log_file is None:
        _unknown_log_file = _unknown_rpc_log.open("a", encoding="utf-8")
    record = {
        "ts": time.time(),
        "rpc": rpc_name,
        "player_id": envelope.get("player_id", ""),
        "args": args,
        "envelope_keys": list(envelope.keys()),
    }
    _unknown_log_file.write(json.dumps(record, separators=(",", ":")) + "\n")
    _unknown_log_file.flush()


def dispatch_rpc(rpc_name: str, args: list, envelope: dict) -> Any:
    player_id = envelope.get("player_id", "")

    if rpc_name == "preparePasswordAuthentication":
        return auth_handlers.handle_prepare_password_auth(args[0] if args else "")
    if rpc_name == "authenticateUsername":
        username = args[0] if len(args) > 0 else ""
        proof = args[1] if len(args) > 1 else ""
        challenge_hex = args[2] if len(args) > 2 else ""
        return auth_handlers.handle_authenticate_username(username, proof, challenge_hex)
    if rpc_name == "getSalt":
        return auth_handlers.handle_get_salt()
    if rpc_name == "getOrCreatePlayerId":
        return auth_handlers.handle_get_or_create_player_id(args[0] if args else {})
    if rpc_name == "login":
        login_obj = args[0] if args else {}
        sk = login_obj.get("sk", "") if isinstance(login_obj, dict) else ""
        fbt = login_obj.get("fbt", "") if isinstance(login_obj, dict) else ""
        return session_handlers.handle_login(player_id, sk=sk, fbt=fbt)
    if rpc_name == "config":
        locale_obj = args[0] if args else {}
        locale = locale_obj.get("locale", "en_US") if isinstance(locale_obj, dict) else "en_US"
        return session_handlers.handle_config(player_id, locale=locale)
    if rpc_name == "getTransactionSummary":
        return session_handlers.handle_get_transaction_summary(player_id)
    if rpc_name == "getPushPreferences":
        return session_handlers.handle_get_push_preferences(player_id)
    if rpc_name == "getClientMessageQueue":
        return session_handlers.handle_get_client_message_queue(player_id, client_id=str(args[0] if args else ""))
    if rpc_name == "logout":
        return session_handlers.handle_logout(player_id)
    if rpc_name == "saveSocialData":
        return session_handlers.handle_save_social_data(player_id)
    if rpc_name == "getContentPackRevisions":
        return session_handlers.handle_get_content_pack_revisions()
    if rpc_name == "getGameStatePB":
        return save_handlers.handle_get_game_state_pb(player_id, token=str(args[0] if args else ""))
    if rpc_name == "saveV3":
        return save_handlers.handle_save_v3(player_id, args)
    if rpc_name == "getRemotePlayerStateV2":
        req_obj = args[0] if args else {}
        target = req_obj.get("player_id", player_id) if isinstance(req_obj, dict) else player_id
        idx = req_obj.get("player_index", 0) if isinstance(req_obj, dict) else 0
        return save_handlers.handle_get_remote_player_state(player_id, target_player_id=target, player_index=idx)

    log.warning("Unknown RPC: %s  args=%s", rpc_name, args)
    _log_unknown_rpc(rpc_name, args, envelope)
    return {"success": True, "_stub": True, "rpc": rpc_name}


@app.post("/tapservice/api/")
async def tapservice(request: Request) -> Response:
    body_str = (await request.body()).decode("utf-8", errors="replace")
    try:
        payload, chksum, raw_json = wire.decode_request_body(body_str)
    except Exception as e:
        log.error("Failed to decode request body: %s | body=%s", e, body_str[:200])
        return Response(content='{"error":"bad_request"}', status_code=400, media_type="application/json")

    if _verify_chksum and not wire.verify_request_chksum(raw_json, chksum):
        log.warning("Checksum mismatch chksum=%s", chksum)

    rpc_header = request.headers.get("rpc", request.headers.get("x-rpc", ""))
    data_list = payload.get("data", [])
    log.info("POST /tapservice/api/ player=%s rpcs=%s calls=%d",
             payload.get("player_id", "?"), rpc_header, len(data_list))

    results = []
    for call in data_list:
        if not isinstance(call, list) or not call:
            results.append({"success": False, "error": "bad_call"})
            continue
        try:
            results.append(dispatch_rpc(call[0], call[1:], payload))
        except Exception as e:
            log.exception("Handler error for RPC %s: %s", call[0], e)
            results.append({"success": False, "error": "internal_error", "rpc": call[0]})

    raw_body, digest = wire.build_response(results)
    return Response(content=raw_body, media_type="application/json", headers={"x-tc-digest": digest})


def _serve_catalog(path: Path, filename: str) -> Response:
    return FileResponse(str(path), media_type=cdn.guess_content_type(path))


@app.get("/cdn/{filename:path}")
async def cdn_file(filename: str) -> Response:
    path = cdn.find_cached(filename)
    ts = __import__("time").strftime("%H:%M:%S")
    if path:
        log.info("CDN HIT : %s", filename)
        with _cdn_access_log.open("a") as f:
            f.write(f"{ts} HIT  {filename}\n")
        return _serve_catalog(path, filename)

    log.warning("CDN MISS: %s", filename)
    with _cdn_access_log.open("a") as f:
        f.write(f"{ts} MISS {filename}\n")
    return Response(status_code=404, content=f"Not cached: {filename}")


@app.get("/cdn/")
async def cdn_index() -> JSONResponse:
    files = cdn.list_cached_files()
    return JSONResponse({"cached_files": len(files), "files": files[:200]})


@app.post("/analytics/")
@app.post("/analytics/{path:path}")
async def analytics_stub(request: Request) -> Response:
    return Response(content='{"ok":true}', media_type="application/json")


@app.post("/t/api/{path:path}")
async def tinyco_analytics_stub(request: Request) -> Response:
    return Response(content='{"ok":true}', media_type="application/json")


@app.post("/api/{project_id}/{endpoint}")
async def sentry_stub(project_id: str, endpoint: str, request: Request) -> Response:
    if endpoint in ("envelope", "store"):
        return Response(content='{"id":"0"}', media_type="application/json")
    return Response(status_code=404)


@app.get("/")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "server": "fgqfs-private", "version": "0.1.0"})


@app.get("/certs/")
async def cert_index() -> JSONResponse:
    files = []
    if _certs_dir.is_dir():
        files = [p.name for p in sorted(_certs_dir.iterdir()) if p.is_file()]
    return JSONResponse({"install_url": "/cert", "cert_files": files})


@app.get("/cert")
@app.get("/cert.cer")
async def default_cert_file() -> Response:
    return _serve_default_cert()


@app.get("/certs/{filename}")
async def cert_file(filename: str) -> Response:
    return _serve_cert_file(filename)


@app.get("/debug/db")
async def debug_db() -> JSONResponse:
    conn = db._conn()
    players = conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
    saves = conn.execute("SELECT COUNT(*) FROM saves").fetchone()[0]
    return JSONResponse({"players": players, "saves": saves})


@app.get("/{filename:path}")
async def cdn_root_file(filename: str, request: Request) -> Response:
    if not filename or filename.startswith("tapservice"):
        return Response(status_code=404)
    if filename == "certs" or filename == "certs/":
        files = []
        if _certs_dir.is_dir():
            files = [p.name for p in sorted(_certs_dir.iterdir()) if p.is_file()]
        return JSONResponse({"install_url": "/cert", "cert_files": files})
    if filename in {"cert", "cert.cer"}:
        return _serve_default_cert()
    if filename.startswith("certs/"):
        return _serve_cert_file(filename.split("/", 1)[1])
    ts = __import__("time").strftime("%H:%M:%S")
    host = request.headers.get("host", "?")

    path = cdn.find_cached(filename)
    if path:
        log.info("CDN ROOT HIT : %s  (host=%s)", filename, host)
        with _cdn_access_log.open("a") as f:
            f.write(f"{ts} ROOT-HIT  {host}/{filename}\n")
        return _serve_catalog(path, filename)

    path = cdn.find_asset_cached(filename)
    if path:
        log.info("ASSET HIT : %s  (host=%s)", filename, host)
        with _cdn_access_log.open("a") as f:
            f.write(f"{ts} ASSET-HIT  {host}/{filename}\n")
        return FileResponse(str(path), media_type=cdn.guess_content_type(path))

    if config.LAZY_FETCH:
        path = cdn.fetch_and_cache_asset(filename)
        if path:
            log.warning("ASSET LAZY-FETCH: %s  (host=%s)", filename, host)
            with _cdn_access_log.open("a") as f:
                f.write(f"{ts} LAZY-FETCH {host}/{filename}\n")
            return FileResponse(str(path), media_type=cdn.guess_content_type(path))

    log.warning("CDN ROOT MISS: %s  (host=%s)", filename, host)
    with _cdn_access_log.open("a") as f:
        f.write(f"{ts} ROOT-MISS {host}/{filename}\n")
    return Response(status_code=404, content=f"Not cached: {filename}")


@app.on_event("startup")
async def on_startup() -> None:
    db.init_db()
    log.info("DB initialised at %s", config.DB_PATH)
    log.info("CDN cache dir: %s exists=%s", config.CDN_CACHE_DIR, config.CDN_CACHE_DIR.is_dir())
    log.info("LAZY_FETCH=%s", config.LAZY_FETCH)
    log.info("Cert dir: %s exists=%s", _certs_dir, _certs_dir.is_dir())
    log.info("server_time=%d", config.current_server_time())


def main() -> None:
    global _verify_chksum
    parser = argparse.ArgumentParser(description="FG:QfS Private Server")
    parser.add_argument("--host", default=config.HOST)
    parser.add_argument("--port", type=int, default=config.PORT)
    parser.add_argument("--no-checksum-verify", action="store_true")
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    if args.no_checksum_verify:
        _verify_chksum = False
        log.warning("Checksum verification DISABLED")

    log.info("Starting FG:QfS private server on %s:%d", args.host, args.port)
    uvicorn.run("server:app", host=args.host, port=args.port, reload=args.reload, log_level="info")


if __name__ == "__main__":
    main()
