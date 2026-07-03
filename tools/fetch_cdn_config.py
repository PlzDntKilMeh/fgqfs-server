#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import requests
except ImportError:
    raise SystemExit("pip install requests")

import fg_login


def fetch_live_config(args: argparse.Namespace) -> dict[str, Any]:
    if args.email and args.password and not args.anon:
        creds = fg_login.full_login(
            args.email,
            args.password,
            install_id=args.install_id or fg_login._rand_int32(),
            include_config=True,
            debug=args.debug,
        )
    else:
        creds = fg_login.anon_login(
            install_id=args.install_id,
            include_config=True,
            debug=args.debug,
        )

    config = creds.get("config")
    if not isinstance(config, dict) or not config:
        raise RuntimeError("login succeeded but no config payload was returned")
    return config


def download_config_catalogs(config: dict[str, Any], out_dir: Path,
                             limit: int = 0, name_filter: str = "") -> list[dict[str, Any]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    base_url = ((config.get("adHocConfigs") or {}).get("adhocs") or {}).get("ConfigURL", "")
    cks = config.get("cks") or []
    if not base_url or not isinstance(cks, list):
        raise RuntimeError("config payload missing ConfigURL or cks")

    pattern = re.compile(name_filter) if name_filter else None
    session = requests.Session()
    session.headers["User-Agent"] = f"familyguy/{fg_login.APP_VER} android/25"

    rows: list[dict[str, Any]] = []
    selected = 0
    for entry in cks:
        if not isinstance(entry, dict):
            continue
        filename = str(entry.get("f") or "").strip()
        md5 = str(entry.get("m") or "").strip()
        if not filename or not md5:
            continue
        if pattern and not pattern.search(filename):
            continue
        if limit and len(rows) >= limit:
            break
        selected += 1

        url = urljoin(base_url.rstrip("/") + "/", filename)
        raw_path = out_dir / filename
        sidecar_path = out_dir / f"{filename}.json"
        status = "cached"
        size = raw_path.stat().st_size if raw_path.exists() else 0

        if not raw_path.exists():
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
            raw_path.write_bytes(resp.content)
            size = len(resp.content)
            status = "downloaded"
            try:
                decoded = resp.json()
                sidecar_path.write_text(json.dumps(decoded, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass

        rows.append({
            "file": filename,
            "md5": md5,
            "url": url,
            "status": status,
            "size": size,
        })

    print(f"config_url={base_url}")
    print(f"cks_entries={selected}")
    print(f"downloaded_or_cached={len(rows)}")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch live FG:QfS config catalogs (default: anon)")
    parser.add_argument("--anon", action="store_true",
                        help="Force anonymous device bootstrap (default if no creds supplied)")
    parser.add_argument("--email", "--username", dest="email",
                        help="Optional username/email for live login")
    parser.add_argument("--password",
                        help="Optional password for live login")
    parser.add_argument("--install-id", type=int, default=None,
                        help="Signed int32 install_id (generated/reused if omitted)")
    parser.add_argument("--out-dir", default=str(Path("content") / "cdn_catalogs"),
                        help="Output directory (default: content/cdn_catalogs)")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max number of cks files to download (0 = all)")
    parser.add_argument("--filter", default="",
                        help="Regex filter for cks filenames")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    if bool(args.email) ^ bool(args.password):
        parser.error("--email and --password must be provided together")

    out_dir = Path(args.out_dir)
    config = fetch_live_config(args)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "config_response.json").write_text(
        json.dumps(config, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    rows = download_config_catalogs(
        config,
        out_dir / "files",
        limit=args.limit,
        name_filter=args.filter,
    )
    (out_dir / "download_manifest.json").write_text(
        json.dumps(rows, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"out_dir={out_dir}")


if __name__ == "__main__":
    main()
