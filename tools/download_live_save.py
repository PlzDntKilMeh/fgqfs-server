#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
import db
import live_save_tools


def main() -> None:
    parser = argparse.ArgumentParser(description="Download a live save from the official server")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--install-id", type=int)
    parser.add_argument("--activate", action="store_true",
                        help="Activate the downloaded save into the gameplay slot")
    parser.add_argument("--target-player-id", default=config.SHARED_SAVE_PID or "0")
    args = parser.parse_args()

    metadata = live_save_tools.download_live_save(
        args.email,
        args.password,
        install_id=args.install_id,
    )
    print(f"Downloaded {metadata['bytes']:,} bytes")
    print(f"Saved file : {metadata['output_path']}")

    if args.activate:
        db.init_db()
        db.upsert_player(args.target_player_id, human_id=f"p{args.target_player_id[:8]}")
        proto_bytes = Path(metadata["latest_output_path"]).read_bytes()
        db.put_save(
            args.target_player_id,
            proto_bytes,
            save_version="2.1.0.0",
            source="live_download",
            note=f"official server download for {args.email}",
            force_revision=True,
        )
        print(f"Activated into player_id={args.target_player_id}")


if __name__ == "__main__":
    main()
