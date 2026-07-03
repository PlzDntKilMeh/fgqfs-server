"""
Local CDN mirror.

The game fetches assets from cdn2.familyguy.tinyco.com (and similar).
We serve them from the local cache produced by fg_catalogs.py.

Cache layout (CDN_CACHE_DIR):
  Each raw CDN response is stored as a file named by a hash or the URL path
  components.  fg_catalogs.py also writes a <name>.json sidecar.

The CDN router in server.py mounts this module at /cdn/<path:filename>.

Fall-through strategy:
  1. Check CDN_CACHE_DIR for an exact filename match.
  2. Check CDN_CACHE_DIR for the basename only.
  3. Check ASSET_CACHE_DIR (repo-local bulk downloaded assets).
  4. Check ASSET_LAZY_DIR (previously lazy-fetched at runtime).
  5. Fetch from live CDN → save to ASSET_LAZY_DIR (NOT ASSET_CACHE_DIR).
     Every live fetch is logged to cdn_lazy_fetch.log for later review.
"""
from __future__ import annotations

import logging
import time as _time
from pathlib import Path
from typing import Optional

import re as _re

from config import CDN_CACHE_DIR, ASSET_CACHE_DIR, ASSET_LIVE_BASE, ASSET_LAZY_DIR

# Dedicated log for assets fetched from live CDN at runtime.
_LAZY_LOG = ASSET_LAZY_DIR.parent / "cdn_lazy_fetch.log"

log = logging.getLogger("cdn")

# Matches @2x, @3x, @4x etc. scale suffixes before the extension(s)
_SCALE_PAT = _re.compile(r'@\d+x', _re.IGNORECASE)

def _strip_scale(filename: str) -> Optional[str]:
    """'coast-tiles_v4@4x.astc.ccz' → 'coast-tiles_v4.astc.ccz', or None if no suffix."""
    stripped = _SCALE_PAT.sub("", filename)
    return stripped if stripped != filename else None


def find_cached(filename: str) -> Optional[Path]:
    """
    Try to find `filename` in the local CDN cache.
    Accepts both the exact relative path (subdir/file) and bare filename.
    """
    if not CDN_CACHE_DIR.is_dir():
        return None

    exact = CDN_CACHE_DIR / filename
    if exact.exists():
        return exact

    # Bare filename match (strip any leading path components)
    bare = Path(filename).name
    candidate = CDN_CACHE_DIR / bare
    if candidate.exists():
        return candidate

    return None


def find_asset_cached(filename: str) -> Optional[Path]:
    """
    Check repo-local ASSET_CACHE_DIR, then ASSET_LAZY_DIR (runtime fetches) for a match.
    Also tries the scale-stripped name (e.g. coast-tiles_v4@4x.astc.ccz → coast-tiles_v4.astc.ccz).
    """
    bare = Path(filename).name
    names = [n for n in (bare, _strip_scale(bare)) if n]
    for cache_dir in (ASSET_CACHE_DIR, ASSET_LAZY_DIR):
        if not cache_dir.is_dir():
            continue
        for name in names:
            candidate = cache_dir / name
            if candidate.exists():
                return candidate
    return None


_AKAMAI_BASE = "https://staticfg-a.akamaihd.net"


def _write_lazy_log(name: str, url: str, size: int) -> None:
    """Append one line to cdn_lazy_fetch.log so we can review runtime misses later."""
    try:
        _LAZY_LOG.parent.mkdir(parents=True, exist_ok=True)
        ts = _time.strftime("%Y-%m-%d %H:%M:%S")
        with _LAZY_LOG.open("a", encoding="utf-8") as f:
            f.write(f"{ts}  {size:>10d}B  {name}  ({url})\n")
    except Exception as exc:
        log.debug("lazy log write failed: %s", exc)


def fetch_and_cache_asset(filename: str, timeout: int = 30) -> Optional[Path]:
    """
    Download a missing asset from the live CDN and save to ASSET_LAZY_DIR.
    ASSET_LAZY_DIR is separate from ASSET_CACHE_DIR so runtime discoveries
    are clearly distinct from the normal repo-local bulk dump.  Every fetch
    is logged to cdn_lazy_fetch.log for later review.
    Tries Akamai first, then jamcity-static.  Also tries scale-stripped name.
    """
    try:
        import requests as _req
    except ImportError:
        log.warning("requests not installed — cannot lazy-fetch asset %s", filename)
        return None

    bare     = Path(filename).name
    stripped = _strip_scale(bare)
    ASSET_LAZY_DIR.mkdir(parents=True, exist_ok=True)

    for name in ([bare, stripped] if stripped else [bare]):
        for base in (_AKAMAI_BASE, ASSET_LIVE_BASE):
            url = f"{base.rstrip('/')}/{name}"
            log.info("asset MISS — lazy-fetching %s", url)
            try:
                r = _req.get(url, timeout=timeout)
                if r.status_code == 404:
                    continue
                r.raise_for_status()
                dest = ASSET_LAZY_DIR / name
                dest.write_bytes(r.content)
                size = len(r.content)
                log.info("asset lazy-cached: %s  (%d B) → %s", name, size, dest)
                _write_lazy_log(name, url, size)
                return dest
            except Exception as exc:
                log.debug("lazy-fetch failed %s: %s", url, exc)

    log.warning("fetch_and_cache_asset: all attempts failed for %s", filename)
    return None


def list_cached_files() -> list[str]:
    if not CDN_CACHE_DIR.is_dir():
        return []
    return [p.name for p in sorted(CDN_CACHE_DIR.iterdir()) if p.is_file()]


def guess_content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    types = {
        ".json": "application/json",
        ".csv":  "text/csv",
        ".png":  "image/png",
        ".jpg":  "image/jpeg",
        ".mp3":  "audio/mpeg",
        ".ogg":  "audio/ogg",
        ".webp": "image/webp",
    }
    return types.get(suffix, "application/octet-stream")
