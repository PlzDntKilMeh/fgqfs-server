"""
Save RPCs: getGameStatePB, saveV3, getRemotePlayerStateV2.
"""
from __future__ import annotations

import logging

import config as _cfg
import db
from wire import decode_save_blob, encode_save_blob, inner_blob_digest

log = logging.getLogger("handlers.save")

CURRENT_SAVE_VERSION = "2.1.0.0"


def _canonical_player_id(player_id: str) -> str:
    if _cfg.USE_SHARED_SAVE and _cfg.SHARED_SAVE_PID:
        if player_id != _cfg.SHARED_SAVE_PID:
            log.info("shared-save: serving account %r to requester %r",
                     _cfg.SHARED_SAVE_PID, player_id)
        return _cfg.SHARED_SAVE_PID
    return player_id


def handle_get_game_state_pb(player_id: str, token: str = "") -> dict:
    player_id = _canonical_player_id(player_id)
    row = db.get_save(player_id)
    if row and row["proto_bytes"]:
        proto_bytes = bytes(row["proto_bytes"])
        save_version = row["save_version"] or CURRENT_SAVE_VERSION
        blob = encode_save_blob(proto_bytes, add_prefix=False)
        log.info("getGameStatePB player=%s proto=%d bytes", player_id, len(proto_bytes))
        return {
            "success": True,
            "save_version": save_version,
            "saved_game_pbuf": blob,
            "time_slept": 0,
            "server_time": _cfg.current_server_time(),
        }

    log.info("getGameStatePB player=%s no save local_save_ok=True", player_id)
    return {
        "success": True,
        "save_version": CURRENT_SAVE_VERSION,
        "local_save_ok": True,
        "time_slept": 0,
        "server_time": _cfg.current_server_time(),
    }


def handle_save_v3(player_id: str, args: list) -> dict:
    if len(args) < 4:
        log.warning("saveV3 player=%s bad arg count %d", player_id, len(args))
        return {"success": False, "error": "bad_args"}

    blob_field = args[0]
    save_version = str(args[1]).strip()
    inner_dig = str(args[3]) if len(args) > 3 else ""

    player_id = _canonical_player_id(player_id)

    try:
        raw_proto = decode_save_blob(blob_field)
    except Exception as e:
        log.error("saveV3 player=%s blob decode failed: %s", player_id, e)
        return {"success": False, "error": "decode_failed"}

    expected_dig = inner_blob_digest(raw_proto)
    if inner_dig and expected_dig.lower() != inner_dig.lower():
        log.warning("saveV3 player=%s inner digest mismatch got=%s expected=%s",
                    player_id, inner_dig, expected_dig)

    db.upsert_player(player_id)
    revision_id = db.put_save(
        player_id,
        raw_proto,
        save_version or CURRENT_SAVE_VERSION,
        source="saveV3",
        note="runtime save",
    )
    log.info("saveV3 player=%s stored %d bytes version=%s",
             player_id, len(raw_proto), save_version)
    if revision_id:
        log.info("saveV3 player=%s revision=%s", player_id, revision_id)
    return {"success": True}


def handle_get_remote_player_state(player_id: str, target_player_id: str = "",
                                   player_index: int = 0) -> dict:
    pid = target_player_id or player_id
    row = db.get_save(pid)
    if row and row["proto_bytes"]:
        proto_bytes = bytes(row["proto_bytes"])
        save_version = row["save_version"]
        blob = encode_save_blob(proto_bytes, add_prefix=False)
    else:
        return {"success": False, "error": "player_not_found"}

    return {
        "success": True,
        "player_state": blob,
        "save_version": save_version,
    }
