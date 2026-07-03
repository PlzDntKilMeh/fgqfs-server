#!/usr/bin/env python3
"""Export the latest live save from a mitmproxy JSONL traffic log."""
from __future__ import annotations

import argparse
import base64
import json
import re
import zlib
from pathlib import Path
from typing import Any


def _safe_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return cleaned.strip("._-") or "unknown"


def _decode_blob(blob: str) -> bytes:
    b64 = blob[2:] if blob.startswith("p:") else blob
    compressed = base64.b64decode(b64.replace("\\n", "").strip())
    return zlib.decompress(compressed)


def _pick_player_id(record: dict[str, Any], fallback: str = "") -> str:
    player_id = record.get("player_id")
    if isinstance(player_id, str) and player_id.strip():
        return player_id
    return fallback


def _request_save_candidate(record: dict[str, Any], current_id: str) -> dict[str, Any] | None:
    rpcs = record.get("rpcs")
    if not isinstance(rpcs, list):
        return None
    player_id = _pick_player_id(record, current_id)
    for call in rpcs:
        if not isinstance(call, list) or len(call) < 2 or call[0] != "saveV3":
            continue
        blob = call[1]
        if not isinstance(blob, str) or not blob:
            continue
        return {
            "source": "request",
            "player_id": _safe_id(player_id),
            "ts": record.get("ts", 0),
            "rpc": "saveV3",
            "blob": blob,
        }
    return None


def _response_save_candidate(record: dict[str, Any], current_id: str) -> dict[str, Any] | None:
    response = record.get("response_json")
    if not isinstance(response, dict):
        return None
    entries = response.get("response")
    if not isinstance(entries, list):
        return None

    player_id = _pick_player_id(record, current_id)
    latest_blob = ""
    latest_id = player_id
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_pid = entry.get("player_id") or entry.get("human_id")
        if isinstance(entry_pid, str) and entry_pid.strip():
            latest_id = entry_pid
        blob = entry.get("saved_game_pbuf")
        if isinstance(blob, str) and blob:
            latest_blob = blob

    if not latest_blob:
        return None

    return {
        "source": "response",
        "player_id": _safe_id(latest_id or player_id),
        "ts": record.get("ts", 0),
        "rpc": "getGameStatePB",
        "blob": latest_blob,
    }


def extract_latest_candidates(log_path: Path) -> dict[str, dict[str, Any]]:
    current_id = ""
    latest: dict[str, dict[str, Any]] = {}
    with log_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            current_id = _pick_player_id(record, current_id)
            if record.get("direction") == "request":
                candidate = _request_save_candidate(record, current_id)
                if candidate:
                    latest["request"] = candidate
                    current_id = candidate["player_id"] or current_id
            elif record.get("direction") == "response":
                candidate = _response_save_candidate(record, current_id)
                if candidate:
                    latest["response"] = candidate
                    current_id = candidate["player_id"] or current_id
    if not latest:
        raise SystemExit(f"No live save found in {log_path}")
    return latest


def write_exports(candidates: dict[str, dict[str, Any]], output_dir: Path, preferred: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Any] = {}

    for source, candidate in candidates.items():
        proto_bytes = _decode_blob(candidate["blob"])
        player_id = candidate["player_id"]
        named_path = output_dir / f"live_save_{source}_{player_id}.pbuf"
        named_path.write_bytes(proto_bytes)
        candidate["output_path"] = str(named_path)
        candidate["bytes"] = len(proto_bytes)
        written[source] = {
            "player_id": player_id,
            "rpc": candidate["rpc"],
            "bytes": len(proto_bytes),
            "ts": candidate["ts"],
            "output_path": named_path.name,
        }

    if preferred == "auto":
        preferred = "response" if "response" in candidates else "request"
    if preferred not in candidates:
        raise SystemExit(f"Requested source '{preferred}' not found in capture")

    latest = candidates[preferred]
    latest_path = output_dir / "latest_live_save.pbuf"
    latest_path.write_bytes(Path(latest["output_path"]).read_bytes())

    metadata = {
        "latest_source": preferred,
        "latest_player_id": latest["player_id"],
        "latest_rpc": latest["rpc"],
        "latest_output_path": latest_path.name,
        "captures": written,
    }
    (output_dir / "latest_live_save.json").write_text(
        json.dumps(metadata, indent=2),
        encoding="utf-8",
    )

    return latest_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Export latest live save from traffic capture")
    parser.add_argument(
        "--in",
        dest="input_path",
        default="captures/live/traffic.jsonl",
        help="Path to proxy traffic JSONL",
    )
    parser.add_argument(
        "--out-dir",
        dest="output_dir",
        default="captures/live/saves",
        help="Directory for exported .pbuf files",
    )
    parser.add_argument(
        "--source",
        choices=["auto", "request", "response"],
        default="auto",
        help="Choose request saveV3 export, response getGameStatePB export, or auto",
    )
    args = parser.parse_args()

    log_path = Path(args.input_path)
    if not log_path.is_file():
        raise SystemExit(f"Traffic log not found: {log_path}")

    candidates = extract_latest_candidates(log_path)
    latest_path = write_exports(candidates, Path(args.output_dir), args.source)
    meta = json.loads((Path(args.output_dir) / "latest_live_save.json").read_text(encoding="utf-8"))

    print(f"Traffic log : {log_path}")
    print(f"Output dir  : {Path(args.output_dir)}")
    for source, details in meta["captures"].items():
        print(f"{source:8} -> {details['output_path']} ({details['bytes']:,} bytes)")
    print(f"latest     -> {latest_path} [{meta['latest_source']}]")


if __name__ == "__main__":
    main()
