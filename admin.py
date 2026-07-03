from __future__ import annotations

import html
import json
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import config
import db
import live_save_tools

ROOT = Path(__file__).parent
FLASH_COOKIE = "fgqfs_admin_flash"
TEMPLATE = ROOT / "templates" / "admin.html"
SAVE_CARD_TEMPLATE = ROOT / "templates" / "admin_save_card.html"
REVISION_ROW_TEMPLATE = ROOT / "templates" / "admin_revision_row.html"

router = APIRouter()


def _fmt_ts(value: Any) -> str:
    try:
        ivalue = int(value)
    except (TypeError, ValueError):
        return "-"
    if ivalue <= 0:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ivalue))


def _new_slot_id() -> str:
    return uuid.uuid4().hex


def _notice_html(message: str, is_error: bool = False) -> str:
    if not message:
        return ""
    kind = "error" if is_error else "success"
    return f'<div class="notice {kind}" role="status">{html.escape(message)}</div>'


def _flash_redirect(message: str, is_error: bool = False, status_code: int = 303) -> RedirectResponse:
    response = RedirectResponse(url="/admin", status_code=status_code)
    response.set_cookie(
        FLASH_COOKIE,
        json.dumps({"message": message, "is_error": is_error}, separators=(",", ":")),
        max_age=90,
        httponly=True,
        samesite="lax",
    )
    return response


def _json_success(message: str, **extra: Any) -> JSONResponse:
    payload = {"message": message, "redirect": "/admin", **extra}
    response = JSONResponse(payload)
    response.set_cookie(
        FLASH_COOKIE,
        json.dumps({"message": message, "is_error": False}, separators=(",", ":")),
        max_age=90,
        httponly=True,
        samesite="lax",
    )
    return response


def _flash_from_request(request: Request) -> tuple[str, bool]:
    raw = request.cookies.get(FLASH_COOKIE)
    if not raw:
        return "", False
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return "", False
    return str(payload.get("message") or ""), bool(payload.get("is_error"))


def _template(path: Path, context: dict[str, str]) -> str:
    text = path.read_text(encoding="utf-8")
    for key, value in context.items():
        text = text.replace(f"{{{{{key}}}}}", value)
    return text


def _save_card(row: Any, shared_pid: str) -> str:
    player_id = str(row["player_id"])
    player_url = quote(player_id, safe="")
    revisions = db.list_save_revisions(player_id, limit=200)
    revision_count = int(row["revision_count"] or 0)
    revision_note = ""
    if revision_count > len(revisions):
        revision_note = f'<p class="muted">Showing latest {len(revisions)} of {revision_count} revisions.</p>'

    revision_rows = "".join(
        _template(
            REVISION_ROW_TEMPLATE,
            {
                "revision_id": str(int(rev["revision_id"])),
                "source": html.escape(rev["source"] or "-"),
                "save_bytes": str(int(rev["save_bytes"] or 0)),
                "created_at": _fmt_ts(rev["created_at"]),
                "note": html.escape(rev["note"] or "-"),
                "target_player_id": html.escape(shared_pid, quote=True),
            },
        )
        for rev in revisions
    ) or '<tr><td colspan="6">No revisions yet.</td></tr>'

    return _template(
        SAVE_CARD_TEMPLATE,
        {
            "player_id": html.escape(player_id),
            "player_id_url": player_url,
            "email": html.escape(row["username"] or "-"),
            "save_bytes": str(int(row["save_bytes"] or 0)),
            "revision_count": str(revision_count),
            "updated_at": _fmt_ts(row["updated_at"]),
            "last_seen": _fmt_ts(row["last_seen"]),
            "revision_note_html": revision_note,
            "revision_rows": revision_rows,
        },
    )


def _render_dashboard(notice: str = "", is_error: bool = False) -> str:
    shared_pid = config.SHARED_SAVE_PID or "0"
    save_rows = db.list_saves()
    save_cards = "".join(_save_card(row, shared_pid) for row in save_rows[:20])
    if not save_cards:
        save_cards = '<section class="save-card empty">No saves found.</section>'
    return _template(TEMPLATE, {
        "notice_html": _notice_html(notice, is_error),
        "shared_pid": html.escape(shared_pid),
        "shared_pid_url": quote(shared_pid, safe=""),
        "save_cards": save_cards,
    })


@router.get("/admin")
async def dashboard(request: Request) -> HTMLResponse:
    notice, is_error = _flash_from_request(request)
    response = HTMLResponse(_render_dashboard(notice=notice, is_error=is_error))
    if request.cookies.get(FLASH_COOKIE):
        response.delete_cookie(FLASH_COOKIE)
    return response


@router.post("/admin/api/activate-revision")
async def activate_revision(request: Request) -> JSONResponse:
    payload = await request.json()
    revision_id = int(payload.get("revision_id", 0))
    target_player_id = str(payload.get("target_player_id") or (config.SHARED_SAVE_PID or "0"))
    row = db.get_save_revision(revision_id)
    if not row:
        return JSONResponse({"error": f"Unknown revision_id={revision_id}"}, status_code=404)
    if not db.get_player(target_player_id):
        db.upsert_player(target_player_id, human_id=f"p{target_player_id[:8]}")
    db.restore_save_revision(
        target_player_id,
        revision_id,
        source="dashboard_activate",
        note=f"dashboard activate from revision {revision_id}",
    )
    return _json_success(f"Activated revision {revision_id} into player {target_player_id}")


@router.post("/admin/api/download-live-save")
async def download_live_save(request: Request) -> JSONResponse:
    payload = await request.json()
    email = str(payload.get("email", "")).strip()
    password = str(payload.get("password", ""))
    target_player_id = str(payload.get("target_player_id") or (config.SHARED_SAVE_PID or "0"))
    if not email or not password:
        return JSONResponse({"error": "Email and password are required"}, status_code=400)
    try:
        metadata = live_save_tools.download_live_save(email, password)
        proto_bytes = Path(metadata["latest_output_path"]).read_bytes()
        if not db.get_player(target_player_id):
            db.upsert_player(target_player_id, human_id=f"p{target_player_id[:8]}")
        db.put_save(
            target_player_id,
            proto_bytes,
            save_version="2.1.0.0",
            source="live_download",
            note=f"official server download for {email}",
            force_revision=True,
        )
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    return _json_success(
        f"Downloaded live save for {email} and activated it into {target_player_id}",
        output_path=metadata["output_path"],
        bytes=metadata["bytes"],
    )


@router.post("/admin/upload-save")
async def upload_save(request: Request) -> Response:
    try:
        form = await request.form()
    except Exception as exc:
        return _flash_redirect(f"Upload parsing failed: {exc}. Install python-multipart if needed.", is_error=True)

    upload = form.get("save_file")
    if not upload or not hasattr(upload, "filename"):
        return _flash_redirect("No save file uploaded.", is_error=True)

    try:
        proto_bytes = await upload.read()
    except Exception as exc:
        return _flash_redirect(f"Failed to read uploaded file: {exc}", is_error=True)
    if not proto_bytes:
        return _flash_redirect("Uploaded save file was empty.", is_error=True)

    target_mode = str(form.get("target_mode") or "shared")
    specific_player_id = str(form.get("specific_player_id") or "").strip()
    note = str(form.get("note") or "").strip()
    make_active = str(form.get("make_active") or "") == "1"
    shared_pid = config.SHARED_SAVE_PID or "0"

    if target_mode == "shared":
        target_player_id = shared_pid
    elif target_mode == "new":
        target_player_id = _new_slot_id()
    elif target_mode == "specific":
        if not specific_player_id:
            return _flash_redirect("Specific player_id is required for that target mode.", is_error=True)
        target_player_id = specific_player_id
    else:
        return _flash_redirect(f"Unknown target mode: {target_mode}", is_error=True)

    if not db.get_player(target_player_id):
        db.upsert_player(target_player_id, human_id=f"p{target_player_id[:8]}")
    db.put_save(
        target_player_id,
        proto_bytes,
        save_version="2.1.0.0",
        source="dashboard_upload",
        note=note or f"uploaded file {upload.filename}",
        force_revision=True,
    )

    message = f"Uploaded {upload.filename} into player {target_player_id}."
    if make_active and target_player_id != shared_pid:
        latest = db.get_latest_save_revision(target_player_id)
        if latest:
            if not db.get_player(shared_pid):
                db.upsert_player(shared_pid, human_id=f"p{shared_pid[:8]}")
            db.restore_save_revision(
                shared_pid,
                int(latest["revision_id"]),
                source="dashboard_upload_activate",
                note=f"activated upload from {target_player_id}",
            )
            message += f" Activated into gameplay slot {shared_pid}."
    elif make_active:
        message += f" Stored directly in active gameplay slot {shared_pid}."

    return _flash_redirect(message)


@router.get("/admin/download/current/{player_id}")
async def download_current(player_id: str) -> Response:
    row = db.get_save(player_id)
    if not row or not row["proto_bytes"]:
        return Response(status_code=404, content=f"No save for player_id={player_id}")
    return Response(
        content=bytes(row["proto_bytes"]),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="current_{player_id}.pbuf"'},
    )


@router.get("/admin/download/revision/{revision_id}")
async def download_revision(revision_id: int) -> Response:
    row = db.get_save_revision(revision_id)
    if not row:
        return Response(status_code=404, content=f"No revision {revision_id}")
    return Response(
        content=db.revision_proto_bytes(row),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="revision_{revision_id}_{row["player_id"]}.pbuf"'},
    )
