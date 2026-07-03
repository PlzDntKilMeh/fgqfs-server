#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import bootstrap_save
import db


def main() -> None:
    raw = bootstrap_save.load_fresh_account_save_bytes()
    db.upsert_player("0")
    db.put_save("0", raw, save_version=bootstrap_save.FRESH_ACCOUNT_SAVE_VERSION, source="starter", note="restore starter save", force_revision=True)
    row = db.get_save("0")
    print(f"restored shared starter save -> player_id=0 save_version={row['save_version']} proto_len={len(bytes(row['proto_bytes']))}")


if __name__ == "__main__":
    main()
