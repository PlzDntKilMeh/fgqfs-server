#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.fetch_cdn_config import download_config_catalogs, fetch_live_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch live catalogs, then run the repo-local asset dump")
    parser.add_argument("--anon", action="store_true",
                        help="Force anonymous device bootstrap for catalog fetch")
    parser.add_argument("--email", "--username", dest="email",
                        help="Optional username/email for live catalog fetch")
    parser.add_argument("--password",
                        help="Optional password for live catalog fetch")
    parser.add_argument("--install-id", type=int, default=None,
                        help="Signed int32 install_id for catalog fetch")
    parser.add_argument("--workers", type=int, default=16,
                        help="Concurrent download workers for asset dump")
    parser.add_argument("--discovery", default="hybrid", choices=("hybrid", "schema", "regex"),
                        help="Asset discovery mode for asset dump")
    parser.add_argument("--scale-probe", default="all", choices=("all", "@4x", "@2x", "none"),
                        help="Scaled asset probing mode for asset dump")
    parser.add_argument("--retry-missing-scale", action="store_true",
                        help="Retry scale variants previously recorded as missing")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run asset dump in dry-run mode after refreshing catalogs")
    parser.add_argument("--debug", action="store_true",
                        help="Enable debug output during catalog fetch")
    args = parser.parse_args()

    if bool(args.email) ^ bool(args.password):
        parser.error("--email and --password must be provided together")

    repo_root = Path(__file__).resolve().parents[1]
    catalog_root = repo_root / "content" / "cdn_catalogs"

    print("Refreshing repo-local catalog cache...")
    config = fetch_live_config(args)
    catalog_root.mkdir(parents=True, exist_ok=True)
    (catalog_root / "config_response.json").write_text(
        __import__("json").dumps(config, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    rows = download_config_catalogs(config, catalog_root / "files")
    (catalog_root / "download_manifest.json").write_text(
        __import__("json").dumps(rows, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    cmd = [
        str(Path(sys.executable)),
        str(repo_root / "tools" / "bulk_download_cdn.py"),
        "--out", str(repo_root / "content" / "cdn_assets" / "files"),
        "--discovery", args.discovery,
        "--ui-json-followup",
        "--plist-followup",
        "--animation-png-repair",
        "--scale-probe", args.scale_probe,
        "--workers", str(max(args.workers, 1)),
        "--status-path", str(repo_root / "content" / "cdn_assets" / "download_status.json"),
        "--profile-log", str(repo_root / "content" / "cdn_assets" / "download_profile.jsonl"),
    ]
    if args.retry_missing_scale:
        cmd.append("--retry-missing-scale")
    if args.dry_run:
        cmd.append("--dry-run")

    print("Starting repo-local asset dump...")
    raise SystemExit(subprocess.run(cmd, cwd=str(repo_root)).returncode)


if __name__ == "__main__":
    main()
