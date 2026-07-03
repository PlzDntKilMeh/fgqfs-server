from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import fg_login
from wire import decode_save_blob

CAPTURE_DIR = Path(__file__).parent / "captures" / "live" / "saves"


def _safe_label(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip().lower())
    return cleaned.strip("._-") or "unknown"


def extract_game_state_blob(game_state_entry: dict[str, Any]) -> bytes:
    blob = game_state_entry.get("saved_game_pbuf")
    if not isinstance(blob, str) or not blob:
        raise ValueError("Live server response did not include saved_game_pbuf")
    return decode_save_blob(blob)


def download_live_save(email: str, password: str, install_id: int | None = None,
                       output_dir: Path | None = None) -> dict[str, Any]:
    creds = fg_login.full_login(
        email=email,
        password=password,
        install_id=install_id or fg_login._rand_int32(),
        include_game_state=True,
        include_config=False,
        debug=False,
    )
    game_state = creds.get("game_state")
    if not isinstance(game_state, dict):
        raise ValueError("Live server login returned no game_state payload")

    proto_bytes = extract_game_state_blob(game_state)
    out_dir = output_dir or CAPTURE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    label = _safe_label(email)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"live_server_{label}_{stamp}.pbuf"
    latest_path = out_dir / f"live_server_{label}_latest.pbuf"
    meta_path = out_dir / f"live_server_{label}_latest.json"
    out_path.write_bytes(proto_bytes)
    latest_path.write_bytes(proto_bytes)
    metadata = {
        "email": email,
        "player_id": creds.get("player_id", ""),
        "human_id": creds.get("human_id", ""),
        "session": creds.get("session", ""),
        "bytes": len(proto_bytes),
        "downloaded_at": stamp,
        "output_path": str(out_path),
        "latest_output_path": str(latest_path),
    }
    meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata
