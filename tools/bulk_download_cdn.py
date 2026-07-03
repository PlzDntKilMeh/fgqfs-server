#!/usr/bin/env python3
"""
Bulk CDN asset downloader / archiver.

Uses the already-downloaded catalog JSONs (content/cdn_catalogs/files/) to build
a URL list and bulk-fetch all referenced binary assets (images, audio, bundles).

Usage:
    python tools/bulk_download_cdn.py --dry-run
    python tools/bulk_download_cdn.py
    python tools/bulk_download_cdn.py --out /path
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import re
import sys
import threading
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import quote, urljoin, urlparse

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import requests
except ImportError:
    raise SystemExit("pip install requests")
from requests.adapters import HTTPAdapter

from config import ASSET_CACHE_DIR, ASSET_LIVE_BASE, CDN_CACHE_DIR, CDN_LIVE_BASE

AKAMAI_SCALE_BASE = "https://staticfg-a.akamaihd.net"
ASSET_HINTS_PATH = Path(__file__).parent.parent / "content" / "cdn_asset_hints.txt"
SCALEABLE_SUFFIXES = {".ccz", ".plist", ".png", ".atlas"}
SCALE_RE = re.compile(r"@\d+x(?=\.)", re.IGNORECASE)
PLIST_TEXTURE_RE = re.compile(
    r"<key>(?:textureFileName|texture|realTextureFileName|file)</key>\s*<string>([^<]+)</string>"
)
PLIST_ASSET_KEY_RE = re.compile(
    r"<key>([^<]+\.(?:png|jpg|jpeg|webp|plist|pvr\.ccz|astc\.ccz))</key>",
    re.IGNORECASE,
)
STATUS_WRITE_INTERVAL_S = 1.0
PROFILE_WRITE_INTERVAL_S = 5.0
PROGRESS_PRINT_INTERVAL_S = 1.0

UI_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".compressed")
UI_LTA_EXTS = (".lta",)
UI_JSON_EXTS = (".json",)
CATALOG_VALUE_EXTS = (
    ".json", ".png", ".jpg", ".jpeg", ".webp",
    ".wav", ".ogg", ".mp3", ".plist", ".lta",
    ".compressed", ".pvr.ccz", ".astc.ccz",
)
PLIST_FIELD_SUFFIXES = ("plist", "p_list", "plistname")
_TRAILING_COMMA_RE = re.compile(r",(\s*[\]}])")

_URL_RE = re.compile(
    r'(https?://(?:cdn\d*\.familyguy\.tinyco\.com|familyguy\.tinyco\.com)/[^\s"\'<>]+)'
    r"|"
    r'"([^"]+\.(?:png|jpg|webp|mp3|ogg|wav|unity3d|assetbundle|bundle))"',
    re.IGNORECASE,
)
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class AssetTask:
    name: str
    dest: Path
    urls: tuple[str, ...]
    phase: str = "catalog"


@dataclass
class AssetResult:
    task: AssetTask
    status: str
    bytes_written: int = 0
    error: str = ""
    elapsed_s: float = 0.0
    url: str = ""


@dataclass(frozen=True)
class AnimationRepairTask:
    base: str


def count_cache_files(path: Path) -> int:
    if not path.is_dir():
        return 0
    return sum(1 for p in path.iterdir() if p.is_file() and p.name != ".gitkeep")


def bucket_error(error: str) -> str:
    lowered = (error or "").lower()
    if "failed to resolve" in lowered or "nameresolutionerror" in lowered or "getaddrinfo failed" in lowered:
        return "dns"
    if "http 403" in lowered:
        return "http_403"
    if "http 404" in lowered:
        return "http_404"
    if "timed out" in lowered or "timeout" in lowered:
        return "timeout"
    if "connection" in lowered:
        return "connection"
    if "no @4x/@2x/base png atlas found" in lowered:
        return "animation_missing"
    return "other"


def summarize_results(results: list[AssetResult]) -> dict[str, int]:
    buckets = Counter(bucket_error(result.error) for result in results)
    return dict(sorted(buckets.items()))


def write_status(status_path: Path | None, payload: dict) -> None:
    if not status_path:
        return
    status_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = status_path.with_suffix(status_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp_path.replace(status_path)


def append_profile_event(profile_path: Path | None, payload: dict) -> None:
    if not profile_path:
        return
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    with profile_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, separators=(",", ":")) + "\n")


class Progress:
    def __init__(
        self,
        label: str,
        total: int,
        skipped: int,
        dry_run: bool = False,
        on_update: Callable[[dict], None] | None = None,
    ) -> None:
        self.label = label
        self.total = total
        self.skipped = skipped
        self.pending = total - skipped
        self.dry_run = dry_run
        self.downloaded = 0
        self.failed = 0
        self.completed = 0
        self.bytes_written = 0
        self.start = time.monotonic()
        self._lock = threading.Lock()
        self._on_update = on_update
        self._last_print = 0.0
        self._last_print_bytes = 0

    def update(self, result: AssetResult) -> None:
        with self._lock:
            self.completed += 1
            if result.status == "ok":
                self.downloaded += 1
                self.bytes_written += result.bytes_written
            elif result.status == "fail":
                self.failed += 1
            elif result.status == "missing":
                self.failed += 1
            self._print(result)
            if self._on_update:
                self._on_update(self.snapshot())

    def _print(self, result: AssetResult) -> None:
        now = time.monotonic()
        is_final = self.completed >= self.pending
        should_print = (
            result.status in {"fail", "missing"}
            or is_final
            or (now - self._last_print) >= PROGRESS_PRINT_INTERVAL_S
        )
        if not should_print:
            return
        last_print = self._last_print or self.start
        window_elapsed = max(now - last_print, 0.001)
        window_bytes = max(self.bytes_written - self._last_print_bytes, 0)
        current_mbps = ((window_bytes / window_elapsed) * 8) / (1024 * 1024)
        self._last_print = now
        self._last_print_bytes = self.bytes_written
        elapsed = max(time.monotonic() - self.start, 0.001)
        remaining = max(self.pending - self.completed, 0)
        rate = self.completed / elapsed
        eta_s = int(remaining / rate) if rate > 0 else 0
        mb = self.bytes_written / (1024 * 1024)
        label = result.status.upper()
        tail = result.task.name
        if result.status in {"fail", "missing"} and result.error:
            tail = f"{tail} :: {result.error}"
        elif result.status == "ok":
            tail = f"mbps={current_mbps:.2f}"
        else:
            tail = ""
        print(
            f"{self.label} [{self.completed}/{self.pending}] {label:<7} "
            f"ok={self.downloaded} fail={self.failed} skip={self.skipped} "
            f"written={mb:.1f}MB eta={eta_s}s  {tail}"
        )

    def summary(self) -> dict:
        elapsed = time.monotonic() - self.start
        return {
            "total_urls": self.total,
            "skipped_existing": self.skipped,
            "attempted": self.pending,
            "downloaded": self.downloaded,
            "failed": self.failed,
            "bytes_written": self.bytes_written,
            "elapsed_s": round(elapsed, 2),
        }

    def snapshot(self) -> dict:
        elapsed = max(time.monotonic() - self.start, 0.001)
        remaining = max(self.pending - self.completed, 0)
        rate = self.completed / elapsed if elapsed else 0.0
        eta_s = int(remaining / rate) if rate > 0 else 0
        bytes_per_s = self.bytes_written / elapsed if elapsed > 0 else 0.0
        return {
            "label": self.label,
            "total": self.total,
            "skipped_existing": self.skipped,
            "pending": self.pending,
            "completed": self.completed,
            "downloaded": self.downloaded,
            "failed": self.failed,
            "bytes_written": self.bytes_written,
            "elapsed_s": round(elapsed, 2),
            "eta_s": eta_s,
            "items_per_s": round(rate, 2),
            "bytes_per_s": int(bytes_per_s),
            "mbps": round((bytes_per_s * 8) / (1024 * 1024), 2),
        }


def extract_urls(json_path: Path) -> set[str]:
    text = json_path.read_text(encoding="utf-8", errors="ignore")
    urls: set[str] = set()
    for m in _URL_RE.finditer(text):
        full_url = m.group(1)
        rel_path = m.group(2)
        if full_url:
            urls.add(full_url)
        elif rel_path:
            urls.add(urljoin(CDN_LIVE_BASE + "/", rel_path.lstrip("/")))
    return urls


def load_json_relaxed(path: Path) -> object:
    text = path.read_text(encoding="utf-8", errors="ignore")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(_TRAILING_COMMA_RE.sub(r"\1", text))


def cached_path(url: str, out_dir: Path) -> Path:
    parsed = urlparse(url)
    filename = parsed.path.lstrip("/").replace("/", "_") or hashlib.md5(url.encode()).hexdigest()
    return out_dir / filename


def quote_url_path(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.path:
        return url
    return parsed._replace(path=quote(parsed.path, safe="/@():,._+-")).geturl()


def catalog_candidate_urls(url: str) -> tuple[str, ...]:
    parsed = urlparse(url)
    raw_path = parsed.path.lstrip("/")
    if not raw_path:
        return (quote_url_path(url),)
    candidates: list[str] = [quote_url_path(url)]
    for base in (AKAMAI_SCALE_BASE, ASSET_LIVE_BASE):
        candidate = asset_url(base, raw_path)
        if candidate not in candidates:
            candidates.append(candidate)
    return tuple(candidates)


def asset_url(base: str, name: str) -> str:
    return f"{base.rstrip('/')}/{quote(name.lstrip('/'), safe='/@():,._+-')}"


def build_catalog_tasks(out_dir: Path) -> tuple[list[AssetTask], int, int]:
    catalog_files = list(CDN_CACHE_DIR.glob("*.json"))
    all_urls: set[str] = set()
    for jf in catalog_files:
        try:
            all_urls.update(extract_urls(jf))
        except Exception:
            pass

    tasks = [
        AssetTask(
            name=cached_path(url, out_dir).name,
            dest=cached_path(url, out_dir),
            urls=catalog_candidate_urls(url),
            phase="catalog",
        )
        for url in sorted(all_urls)
    ]
    return tasks, len(catalog_files), len(all_urls)


def scan_catalog_string_value(value: str, out: set[str], depth: int) -> None:
    candidate = value.strip()
    if not candidate:
        return
    if candidate.endswith(CATALOG_VALUE_EXTS):
        out.update(normalize_asset_name(candidate))
    if candidate.startswith(("{", "[")) and candidate.endswith(("}", "]")):
        try:
            scan_catalog_asset_values(json.loads(candidate), out, depth + 1)
        except json.JSONDecodeError:
            pass


def looks_like_asset_base(value: str) -> bool:
    candidate = value.strip()
    if not candidate or "." in candidate or "://" in candidate or "\\" in candidate or "/" in candidate:
        return False
    if _CONTROL_CHAR_RE.search(candidate):
        return False
    return bool(re.search(r"[A-Za-z]", candidate)) and len(candidate) > 3


def add_plist_atlas_base(value: str, out: set[str]) -> None:
    base = value.strip()
    if not looks_like_asset_base(base):
        return
    out.add(f"{base}.plist")
    out.add(f"{base}@4x.astc.ccz")


def scan_catalog_asset_values(obj: object, out: set[str], depth: int = 0) -> None:
    if depth > 18:
        return
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(value, str):
                key_l = str(key).lower()
                if key_l.endswith(PLIST_FIELD_SUFFIXES) or "plist" in key_l:
                    add_plist_atlas_base(value, out)
                scan_catalog_string_value(value, out, depth)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        scan_catalog_string_value(item, out, depth)
                    elif isinstance(item, (dict, list)):
                        scan_catalog_asset_values(item, out, depth + 1)
            elif isinstance(value, (dict, list)):
                scan_catalog_asset_values(value, out, depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, str):
                scan_catalog_string_value(item, out, depth)
            elif isinstance(item, (dict, list)):
                scan_catalog_asset_values(item, out, depth + 1)


def collect_catalog_field_assets() -> tuple[set[str], int]:
    out: set[str] = set()
    scanned = 0
    for json_path in CDN_CACHE_DIR.glob("*.json"):
        try:
            data = load_json_relaxed(json_path)
        except Exception:
            continue
        scan_catalog_asset_values(data, out, depth=0)
        scanned += 1
    out = {name for name in out if name and len(name) > 3 and not name.startswith("http")}
    return out, scanned


def collect_asset_hints() -> set[str]:
    if not ASSET_HINTS_PATH.is_file():
        return set()
    out: set[str] = set()
    for line in ASSET_HINTS_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.update(normalize_asset_name(line))
    return out


def build_catalog_field_tasks(out_dir: Path) -> tuple[list[AssetTask], int, int]:
    names, scanned = collect_catalog_field_assets()
    tasks = [
        AssetTask(
            name=name,
            dest=out_dir / name,
            urls=(
                asset_url(AKAMAI_SCALE_BASE, name),
                asset_url(ASSET_LIVE_BASE, name),
            ),
            phase="catalog-fields",
        )
        for name in sorted(names)
    ]
    return tasks, scanned, len(names)


def build_asset_hint_tasks(out_dir: Path) -> tuple[list[AssetTask], int]:
    names = collect_asset_hints()
    tasks = [
        AssetTask(
            name=name,
            dest=out_dir / name,
            urls=(
                asset_url(AKAMAI_SCALE_BASE, name),
                asset_url(ASSET_LIVE_BASE, name),
            ),
            phase="asset-hints",
        )
        for name in sorted(names)
    ]
    return tasks, len(names)


def dedupe_tasks(tasks: Iterable[AssetTask]) -> list[AssetTask]:
    deduped: dict[Path, AssetTask] = {}
    for task in tasks:
        if task.dest not in deduped:
            deduped[task.dest] = task
    return list(deduped.values())


def scale_name(name: str, scale: str) -> str | None:
    try:
        idx = name.index(".")
    except ValueError:
        return None
    return name[:idx] + scale + name[idx:]


def is_scaleable(name: str) -> bool:
    if SCALE_RE.search(name):
        return False
    if name.endswith(".ccz"):
        return True
    return Path(name).suffix.lower() in SCALEABLE_SUFFIXES


def normalize_asset_name(name: str) -> list[str]:
    name = name.strip()
    if not name or name.startswith("#") or _CONTROL_CHAR_RE.search(name):
        return []
    if "://" in name or "\\" in name:
        return []
    name = name.lstrip("/")
    lowered = name.lower()
    if "/" in name and (
        lowered.startswith(("familyguy/", "users/", "facespace portraits/"))
        or "/ui/" in lowered
        or "/output/" in lowered
    ):
        name = name.rsplit("/", 1)[-1]
    if name.endswith(".compressed"):
        base = name[: -len(".compressed")]
        return [f"{base}.pvr.ccz", f"{base}.astc.ccz"]
    if "." not in name:
        return []
    return [name]


def load_known_missing_variants(path: Path) -> set[str]:
    if not path.exists():
        return set()
    known: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        known.add(line)
    return known


def append_known_missing_variants(path: Path, scale: str, names: Iterable[str]) -> None:
    unique = sorted(set(names))
    if not unique:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(f"# {scale} {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        for name in unique:
            f.write(name + "\n")


def build_scale_tasks(
    out_dir: Path,
    scales: list[str],
    known_missing: set[str],
    retry_missing: bool,
) -> list[AssetTask]:
    tasks: list[AssetTask] = []
    seen: set[str] = set()
    for path in sorted(out_dir.iterdir()) if out_dir.is_dir() else []:
        if not path.is_file() or not is_scaleable(path.name):
            continue
        for scale in scales:
            scaled = scale_name(path.name, scale)
            if not scaled or scaled in seen:
                continue
            seen.add(scaled)
            dest = out_dir / scaled
            if dest.exists():
                continue
            if not retry_missing and scaled in known_missing:
                continue
            tasks.append(
                AssetTask(
                    name=scaled,
                    dest=dest,
                    urls=(
                        asset_url(AKAMAI_SCALE_BASE, scaled),
                        asset_url(ASSET_LIVE_BASE, scaled),
                    ),
                    phase=f"scale:{scale}",
                )
            )
    return tasks


def collect_plist_texture_refs(out_dir: Path) -> set[str]:
    refs: set[str] = set()
    if not out_dir.is_dir():
        return refs
    for plist_path in out_dir.glob("*.plist"):
        try:
            text = plist_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for ref in PLIST_TEXTURE_RE.findall(text):
            refs.update(normalize_asset_name(ref))
        # Some game plists also expose standalone CDN image names as sprite keys.
        # This is intentionally limited to asset-looking keys to avoid probing every frame name.
        for ref in PLIST_ASSET_KEY_RE.findall(text):
            refs.update(normalize_asset_name(ref))
    return refs


def build_plist_followup_tasks(out_dir: Path) -> list[AssetTask]:
    refs = sorted(collect_plist_texture_refs(out_dir))
    tasks: list[AssetTask] = []
    for name in refs:
        dest = out_dir / name
        if dest.exists():
            continue
        tasks.append(
            AssetTask(
                name=name,
                dest=dest,
                urls=(
                    asset_url(AKAMAI_SCALE_BASE, name),
                    asset_url(ASSET_LIVE_BASE, name),
                ),
                phase="plist",
            )
        )
    return tasks


def scan_ui_json_obj(obj: object, out: set[str], depth: int = 0) -> None:
    if depth > 20 or not isinstance(obj, (dict, list)):
        return
    if isinstance(obj, dict):
        img = obj.get("image", "")
        if isinstance(img, str):
            img = img.strip()
            if img.endswith(UI_IMAGE_EXTS):
                out.update(normalize_asset_name(img))

        tex = obj.get("texture", "")
        if isinstance(tex, str):
            tex = tex.strip()
            if tex.endswith(UI_IMAGE_EXTS):
                out.update(normalize_asset_name(tex))

        mask = obj.get("mask", "")
        if isinstance(mask, str):
            mask = mask.strip()
            if mask.endswith(UI_IMAGE_EXTS):
                out.update(normalize_asset_name(mask))

        lta_val = obj.get("lta", "")
        if isinstance(lta_val, str):
            lta_val = lta_val.strip()
            if lta_val.endswith(UI_LTA_EXTS):
                out.update(normalize_asset_name(lta_val))

        cell_json = obj.get("cell-json", "")
        if isinstance(cell_json, str):
            cell_json = cell_json.strip()
            if cell_json.endswith(UI_JSON_EXTS):
                out.update(normalize_asset_name(cell_json))

        for value in obj.values():
            if isinstance(value, str):
                scan_catalog_string_value(value, out, depth)
            elif isinstance(value, (dict, list)):
                scan_ui_json_obj(value, out, depth + 1)
    else:
        for item in obj:
            scan_ui_json_obj(item, out, depth + 1)


def collect_ui_json_assets(out_dir: Path) -> set[str]:
    out: set[str] = set()
    for json_path in out_dir.glob("*.json"):
        try:
            data = load_json_relaxed(json_path)
        except Exception:
            continue
        scan_ui_json_obj(data, out, depth=0)
    return {name for name in out if name and len(name) > 3 and not name.startswith("http")}


def build_ui_json_followup_tasks(out_dir: Path) -> list[AssetTask]:
    names = sorted(collect_ui_json_assets(out_dir))
    tasks: list[AssetTask] = []
    for name in names:
        dest = out_dir / name
        if dest.exists():
            continue
        tasks.append(
            AssetTask(
                name=name,
                dest=dest,
                urls=(
                    asset_url(AKAMAI_SCALE_BASE, name),
                    asset_url(ASSET_LIVE_BASE, name),
                ),
                phase="ui-json",
            )
        )
    return tasks


def has_any_variant(out_dir: Path, base: str, exts: tuple[str, ...]) -> bool:
    for scale in ("@4x", "@2x", ""):
        for ext in exts:
            if (out_dir / f"{base}{scale}{ext}").exists():
                return True
    return False


def build_animation_repair_tasks(out_dir: Path) -> list[AnimationRepairTask]:
    tasks: list[AnimationRepairTask] = []
    if not out_dir.is_dir():
        return tasks
    for lta_path in sorted(out_dir.glob("*.lta")):
        base = lta_path.stem
        if SCALE_RE.search(base):
            continue
        if has_any_variant(out_dir, base, (".png", ".astc.ccz")):
            continue
        tasks.append(AnimationRepairTask(base=base))
    return tasks


_tls = threading.local()


def _get_session() -> requests.Session:
    if not hasattr(_tls, "session"):
        session = requests.Session()
        session.headers["User-Agent"] = "familyguy/7.2.3 android/25"
        adapter = HTTPAdapter(pool_connections=2, pool_maxsize=2, max_retries=0, pool_block=False)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        _tls.session = session
    return _tls.session


def download_asset(task: AssetTask, dry_run: bool = False, chunk_size: int = 131072) -> AssetResult:
    if task.dest.exists():
        return AssetResult(task=task, status="skip")
    if dry_run:
        return AssetResult(task=task, status="ok")

    started = time.monotonic()
    tmp_path = task.dest.with_suffix(task.dest.suffix + ".part")
    saw_missing: set[int] = set()
    last_error = ""
    try:
        for url in task.urls:
            try:
                response = _get_session().get(url, timeout=(10, 60), stream=True)
                if response.status_code in {403, 404}:
                    saw_missing.add(response.status_code)
                    continue
                response.raise_for_status()
                task.dest.parent.mkdir(parents=True, exist_ok=True)
                written = 0
                with tmp_path.open("wb") as f:
                    for chunk in response.iter_content(chunk_size):
                        if not chunk:
                            continue
                        f.write(chunk)
                        written += len(chunk)
                tmp_path.replace(task.dest)
                return AssetResult(
                    task=task,
                    status="ok",
                    bytes_written=written,
                    elapsed_s=time.monotonic() - started,
                    url=url,
                )
            except Exception as exc:
                last_error = str(exc)
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        if saw_missing:
            missing_codes = "/".join(str(code) for code in sorted(saw_missing))
            return AssetResult(
                task=task,
                status="missing",
                error=f"HTTP {missing_codes}",
                elapsed_s=time.monotonic() - started,
            )
        return AssetResult(
            task=task,
            status="fail",
            error=last_error or "unknown error",
            elapsed_s=time.monotonic() - started,
        )
    except Exception as exc:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        return AssetResult(task=task, status="fail", error=str(exc), elapsed_s=time.monotonic() - started)


def download_animation_png_repair(task: AnimationRepairTask, out_dir: Path, chunk_size: int = 131072) -> AssetResult:
    started = time.monotonic()
    candidates = [f"{task.base}@4x.png", f"{task.base}@2x.png", f"{task.base}.png"]
    asset_task = AssetTask(
        name=task.base,
        dest=out_dir / candidates[-1],
        urls=tuple(),
        phase="animation-repair",
    )
    for candidate in candidates:
        dest = out_dir / candidate
        if dest.exists():
            return AssetResult(
                task=AssetTask(name=candidate, dest=dest, urls=tuple(), phase="animation-repair"),
                status="skip",
                bytes_written=dest.stat().st_size,
                elapsed_s=time.monotonic() - started,
            )
        probe_task = AssetTask(
            name=candidate,
            dest=dest,
            urls=(
                asset_url(AKAMAI_SCALE_BASE, candidate),
                asset_url(ASSET_LIVE_BASE, candidate),
            ),
            phase="animation-repair",
        )
        result = download_asset(probe_task, dry_run=False, chunk_size=chunk_size)
        if result.status in {"ok", "skip"}:
            return result
    return AssetResult(
        task=asset_task,
        status="missing",
        error="No @4x/@2x/base PNG atlas found",
        elapsed_s=time.monotonic() - started,
    )


def write_manifest(
    out_dir: Path,
    summary: dict,
    tasks: list[AssetTask],
    failures: list[AssetResult],
    missing: list[AssetResult],
    missing_variants_path: Path,
) -> None:
    manifest_path = out_dir.parent / "download_manifest.json"
    phase_counts = Counter(task.phase for task in tasks)
    payload = {
        "summary": summary,
        "generated_at": int(time.time()),
        "phase_counts": dict(sorted(phase_counts.items())),
        "failure_buckets": summarize_results(failures),
        "missing_buckets": summarize_results(missing),
        "failed_urls": [{"task": r.task.name, "phase": r.task.phase, "urls": list(r.task.urls), "error": r.error} for r in failures],
        "missing_urls": [{"task": r.task.name, "phase": r.task.phase, "urls": list(r.task.urls), "error": r.error} for r in missing],
        "missing_variants_file": str(missing_variants_path),
        "total_destinations": len(tasks),
    }
    manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Manifest: {manifest_path}")


def execute_phase(
    label: str,
    tasks: list[AssetTask],
    workers: int,
    dry_run: bool,
    on_update: Callable[[dict], None] | None = None,
) -> tuple[dict, list[AssetResult], list[AssetResult]]:
    skipped = sum(1 for task in tasks if task.dest.exists())
    pending_tasks = [task for task in tasks if not task.dest.exists()]
    print(f"{label}: selected={len(tasks)} pending={len(pending_tasks)} skipped={skipped} workers={workers}")

    if dry_run:
        for task in pending_tasks:
            print(f"[dry:{label.lower()}] {task.name} -> {task.urls[0]}")
        return {
            "selected": len(tasks),
            "skipped_existing": skipped,
            "attempted": len(pending_tasks),
            "downloaded": 0,
            "failed": 0,
            "missing": 0,
            "bytes_written": 0,
            "elapsed_s": 0.0,
        }, [], []

    progress = Progress(label=label, total=len(tasks), skipped=skipped, dry_run=dry_run, on_update=on_update)
    failures: list[AssetResult] = []
    missing: list[AssetResult] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(workers, 1)) as pool:
        futures = [pool.submit(download_asset, task, False) for task in pending_tasks]
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result.status == "fail":
                failures.append(result)
            elif result.status == "missing":
                missing.append(result)
            progress.update(result)

    summary = progress.summary()
    summary["selected"] = len(tasks)
    summary["missing"] = len(missing)
    return summary, failures, missing


def execute_animation_repair_phase(
    out_dir: Path,
    tasks: list[AnimationRepairTask],
    workers: int,
    dry_run: bool,
    on_update: Callable[[dict], None] | None = None,
) -> tuple[dict, list[AssetResult], list[AssetResult]]:
    print(f"ANIM: selected={len(tasks)} pending={len(tasks)} skipped=0 workers={workers}")
    if dry_run:
        for task in tasks:
            print(f"[dry:anim] {task.base}@4x.png -> fallback @2x/base")
        return {
            "selected": len(tasks),
            "skipped_existing": 0,
            "attempted": len(tasks),
            "downloaded": 0,
            "failed": 0,
            "missing": 0,
            "bytes_written": 0,
            "elapsed_s": 0.0,
        }, [], []

    progress = Progress(label="ANIM", total=len(tasks), skipped=0, dry_run=dry_run, on_update=on_update)
    failures: list[AssetResult] = []
    missing: list[AssetResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(workers, 1)) as pool:
        futures = [pool.submit(download_animation_png_repair, task, out_dir) for task in tasks]
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result.status == "fail":
                failures.append(result)
            elif result.status == "missing":
                missing.append(result)
            progress.update(result)

    summary = progress.summary()
    summary["selected"] = len(tasks)
    summary["missing"] = len(missing)
    return summary, failures, missing


def execute_ui_json_followup(
    out_dir: Path,
    workers: int,
    dry_run: bool,
    on_update: Callable[[dict], None] | None = None,
    max_rounds: int = 10,
) -> tuple[dict, list[AssetResult], list[AssetResult]]:
    combined_summary = {
        "selected": 0,
        "skipped_existing": 0,
        "attempted": 0,
        "downloaded": 0,
        "failed": 0,
        "missing": 0,
        "bytes_written": 0,
        "elapsed_s": 0.0,
        "rounds": 0,
        "new_jsons": 0,
    }
    all_failures: list[AssetResult] = []
    all_missing: list[AssetResult] = []

    for rnd in range(1, max_rounds + 1):
        print(f"UIJSON round={rnd}")
        tasks = build_ui_json_followup_tasks(out_dir)
        print(f"UIJSON: referenced={len(tasks)} new assets pending from scanned JSON tree")
        if not tasks:
            break
        summary, failures, missing = execute_phase(
            f"UIJSON-{rnd}",
            tasks,
            workers,
            dry_run,
            on_update=on_update,
        )
        combined_summary["selected"] += summary["selected"]
        combined_summary["skipped_existing"] += summary["skipped_existing"]
        combined_summary["attempted"] += summary["attempted"]
        combined_summary["downloaded"] += summary["downloaded"]
        combined_summary["failed"] += summary["failed"]
        combined_summary["missing"] += summary["missing"]
        combined_summary["bytes_written"] += summary["bytes_written"]
        combined_summary["elapsed_s"] = round(combined_summary["elapsed_s"] + summary["elapsed_s"], 2)
        combined_summary["rounds"] = rnd
        all_failures.extend(failures)
        all_missing.extend(missing)

        if dry_run:
            break

        new_jsons = sum(1 for task in tasks if task.name.endswith(".json") and task.dest.exists())
        combined_summary["new_jsons"] = new_jsons
        if new_jsons == 0:
            break

    return combined_summary, all_failures, all_missing


def main() -> None:
    parser = argparse.ArgumentParser(description="FG:QfS CDN bulk downloader")
    parser.add_argument("--out", default=str(ASSET_CACHE_DIR),
                        help="Output directory (default: content/cdn_assets/files/)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max assets to download (0 = unlimited)")
    parser.add_argument("--workers", type=int, default=16,
                        help="Concurrent download workers")
    parser.add_argument("--discovery", default="hybrid", choices=("hybrid", "schema", "regex"),
                        help="Asset discovery mode: catalog fields, regex URLs, or both (default: hybrid)")
    parser.add_argument("--ui-json-followup", action="store_true",
                        help="Scan downloaded .json layout files for nested asset references and fetch them")
    parser.add_argument("--plist-followup", action="store_true",
                        help="Scan downloaded .plist files for extra texture references and fetch them")
    parser.add_argument("--animation-png-repair", action="store_true",
                        help="For cached .lta files missing PNG/ASTC atlases, try @4x then @2x then base PNG")
    parser.add_argument("--scale-probe", default="all", choices=("all", "@4x", "@2x", "none"),
                        help="After base download, probe scaled variants (default: all)")
    parser.add_argument("--retry-missing-scale", action="store_true",
                        help="Retry scale variants previously recorded as missing")
    parser.add_argument("--status-path", default="",
                        help="Optional JSON file to update with run progress")
    parser.add_argument("--profile-log", default="",
                        help="Optional JSONL profile log for throughput and phase timing")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    missing_variants_path = out_dir.parent / "scale_probe_missing.txt"
    status_path = Path(args.status_path) if args.status_path else None
    profile_log_path = Path(args.profile_log) if args.profile_log else (out_dir.parent / "download_profile.jsonl")

    status_payload: dict = {
        "state": "starting",
        "started_at": int(time.time()),
        "updated_at": int(time.time()),
        "out_dir": str(out_dir),
        "missing_variants_path": str(missing_variants_path),
        "profile_log_path": str(profile_log_path),
        "options": {
            "discovery": args.discovery,
            "workers": max(args.workers, 1),
            "ui_json_followup": args.ui_json_followup,
            "plist_followup": args.plist_followup,
            "animation_png_repair": args.animation_png_repair,
            "scale_probe": args.scale_probe,
            "retry_missing_scale": args.retry_missing_scale,
            "dry_run": args.dry_run,
        },
        "phases": {},
    }

    def update_status(**fields: object) -> None:
        status_payload.update(fields)
        status_payload["updated_at"] = int(time.time())
        write_status(status_path, status_payload)

    last_phase_write: dict[str, float] = {}
    last_profile_write: dict[str, float] = {}

    def profile_event(event: str, **fields: object) -> None:
        append_profile_event(
            profile_log_path,
            {
                "ts": int(time.time()),
                "event": event,
                **fields,
            },
        )

    def phase_updater(label: str) -> Callable[[dict], None]:
        def _apply(snapshot: dict) -> None:
            now = time.monotonic()
            last = last_phase_write.get(label, 0.0)
            last_profile = last_profile_write.get(label, 0.0)
            is_final = snapshot.get("completed", 0) >= snapshot.get("pending", 0)
            if not is_final and (now - last) < STATUS_WRITE_INTERVAL_S:
                if (now - last_profile) < PROFILE_WRITE_INTERVAL_S:
                    return
            if is_final or (now - last) >= STATUS_WRITE_INTERVAL_S:
                last_phase_write[label] = now
                status_payload.setdefault("phases", {})[label] = snapshot
                status_payload["current_phase"] = label
                status_payload["phase_progress"] = snapshot
                status_payload["updated_at"] = int(time.time())
                write_status(status_path, status_payload)
            if is_final or (now - last_profile) >= PROFILE_WRITE_INTERVAL_S:
                last_profile_write[label] = now
                profile_event("phase_progress", phase=label, snapshot=snapshot)
        return _apply

    update_status(state="starting")
    profile_event("run_start", out_dir=str(out_dir), options=status_payload["options"])

    if not CDN_CACHE_DIR.is_dir():
        update_status(state="failed", error=f"CDN cache dir not found: {CDN_CACHE_DIR}")
        sys.exit(f"CDN cache dir not found: {CDN_CACHE_DIR}\nRun tools/fetch_cdn_config.py first.")

    existing_asset_files = count_cache_files(out_dir)
    existing_catalog_files = len(list(CDN_CACHE_DIR.glob("*.json")))
    known_missing_scale = len(load_known_missing_variants(missing_variants_path))
    startup_cache = {
        "asset_files": existing_asset_files,
        "catalog_json_files": existing_catalog_files,
        "known_missing_scale": known_missing_scale,
    }
    update_status(startup_cache=startup_cache)
    profile_event("startup_cache", **startup_cache)

    if args.discovery in {"hybrid", "regex"} and existing_catalog_files == 0:
        message = (
            f"Catalog cache is empty: {CDN_CACHE_DIR}. "
            "Run tools/fetch_cdn_config.py first, or use --discovery schema."
        )
        update_status(state="failed", error=message)
        sys.exit(message)

    catalog_tasks: list[AssetTask] = []
    catalog_field_tasks: list[AssetTask] = []
    asset_hint_tasks: list[AssetTask] = []
    catalog_count = total_urls = 0
    catalog_field_count = catalog_field_scanned = 0
    asset_hint_count = 0
    if args.discovery in {"hybrid", "regex"}:
        catalog_tasks, catalog_count, total_urls = build_catalog_tasks(out_dir)
    if args.discovery in {"hybrid", "schema"}:
        catalog_field_tasks, catalog_field_scanned, catalog_field_count = build_catalog_field_tasks(out_dir)
        asset_hint_tasks, asset_hint_count = build_asset_hint_tasks(out_dir)

    if args.discovery == "hybrid":
        base_tasks = dedupe_tasks(catalog_tasks + catalog_field_tasks + asset_hint_tasks)
    elif args.discovery == "regex":
        base_tasks = dedupe_tasks(catalog_tasks)
    else:
        base_tasks = dedupe_tasks(catalog_field_tasks + asset_hint_tasks)

    if args.limit:
        base_tasks = base_tasks[:args.limit]

    print(
        f"Discovery={args.discovery} catalogs={catalog_count} regex_urls={total_urls} "
        f"catalog_field_assets={catalog_field_count} asset_hints={asset_hint_count} "
        f"selected={len(base_tasks)}"
    )
    profile_event(
        "discovery_ready",
        discovery=args.discovery,
        catalogs=catalog_count,
        regex_urls=total_urls,
        catalog_field_assets=catalog_field_count,
        catalog_field_scanned=catalog_field_scanned,
        asset_hints=asset_hint_count,
        selected=len(base_tasks),
        cache_hit=False,
    )
    update_status(
        state="running",
        discovery={
            "catalogs": catalog_count,
            "regex_urls": total_urls,
            "catalog_field_assets": catalog_field_count,
            "catalog_field_scanned": catalog_field_scanned,
            "asset_hints": asset_hint_count,
            "selected": len(base_tasks),
            "cache_hit": False,
        },
        current_phase="BASE",
    )
    base_summary, base_failures, base_missing = execute_phase(
        "BASE",
        base_tasks,
        max(args.workers, 1),
        args.dry_run,
        on_update=phase_updater("BASE"),
    )
    status_payload["phases"]["BASE"] = base_summary
    profile_event("phase_complete", phase="BASE", summary=base_summary)

    ui_json_summary = {
        "selected": 0,
        "skipped_existing": 0,
        "attempted": 0,
        "downloaded": 0,
        "failed": 0,
        "missing": 0,
        "bytes_written": 0,
        "elapsed_s": 0.0,
        "rounds": 0,
        "new_jsons": 0,
    }
    ui_json_failures: list[AssetResult] = []
    ui_json_missing: list[AssetResult] = []
    if args.ui_json_followup:
        update_status(state="running", current_phase="UIJSON")
        ui_json_summary, ui_json_failures, ui_json_missing = execute_ui_json_followup(
            out_dir,
            max(args.workers, 1),
            args.dry_run,
            on_update=phase_updater("UIJSON"),
        )
        status_payload["phases"]["UIJSON"] = ui_json_summary
        profile_event("phase_complete", phase="UIJSON", summary=ui_json_summary)

    plist_tasks: list[AssetTask] = []
    plist_summary = {
        "selected": 0,
        "skipped_existing": 0,
        "attempted": 0,
        "downloaded": 0,
        "failed": 0,
        "missing": 0,
        "bytes_written": 0,
        "elapsed_s": 0.0,
    }
    plist_failures: list[AssetResult] = []
    plist_missing: list[AssetResult] = []
    if args.plist_followup:
        update_status(state="running", current_phase="PLIST")
        plist_tasks = build_plist_followup_tasks(out_dir)
        plist_summary, plist_failures, plist_missing = execute_phase(
            "PLIST",
            plist_tasks,
            max(args.workers, 1),
            args.dry_run,
            on_update=phase_updater("PLIST"),
        )
        status_payload["phases"]["PLIST"] = plist_summary
        profile_event("phase_complete", phase="PLIST", summary=plist_summary)

    animation_tasks: list[AnimationRepairTask] = []
    animation_summary = {
        "selected": 0,
        "skipped_existing": 0,
        "attempted": 0,
        "downloaded": 0,
        "failed": 0,
        "missing": 0,
        "bytes_written": 0,
        "elapsed_s": 0.0,
    }
    animation_failures: list[AssetResult] = []
    animation_missing: list[AssetResult] = []
    if args.animation_png_repair:
        update_status(state="running", current_phase="ANIM")
        animation_tasks = build_animation_repair_tasks(out_dir)
        animation_summary, animation_failures, animation_missing = execute_animation_repair_phase(
            out_dir,
            animation_tasks,
            max(args.workers, 1),
            args.dry_run,
            on_update=phase_updater("ANIM"),
        )
        status_payload["phases"]["ANIM"] = animation_summary
        profile_event("phase_complete", phase="ANIM", summary=animation_summary)

    scale_tasks: list[AssetTask] = []
    scale_summary = {
        "selected": 0,
        "skipped_existing": 0,
        "attempted": 0,
        "downloaded": 0,
        "failed": 0,
        "missing": 0,
        "bytes_written": 0,
        "elapsed_s": 0.0,
    }
    scale_failures: list[AssetResult] = []
    scale_missing: list[AssetResult] = []
    if args.scale_probe != "none":
        scales = ["@4x", "@2x"] if args.scale_probe == "all" else [args.scale_probe]
        known_missing = load_known_missing_variants(missing_variants_path)
        if known_missing and not args.retry_missing_scale:
            print(f"SCALE: skipping {len(known_missing)} previously-missing variants from {missing_variants_path.name}")
        scale_tasks = build_scale_tasks(out_dir, scales, known_missing, args.retry_missing_scale)
        update_status(
            state="running",
            current_phase="SCALE",
            scale_probe={"scales": scales, "known_missing": len(known_missing), "selected": len(scale_tasks)},
        )
        scale_summary, scale_failures, scale_missing = execute_phase(
            "SCALE",
            scale_tasks,
            max(args.workers, 1),
            args.dry_run,
            on_update=phase_updater("SCALE"),
        )
        status_payload["phases"]["SCALE"] = scale_summary
        profile_event("phase_complete", phase="SCALE", summary=scale_summary)
        if scale_missing and not args.dry_run:
            append_known_missing_variants(
                missing_variants_path,
                args.scale_probe,
                [result.task.name for result in scale_missing],
            )

    overall_summary = {
        "base": base_summary,
        "ui_json": ui_json_summary,
        "plist": plist_summary,
        "animation": animation_summary,
        "scale": scale_summary,
        "downloaded": base_summary["downloaded"] + ui_json_summary["downloaded"] + plist_summary["downloaded"] + animation_summary["downloaded"] + scale_summary["downloaded"],
        "failed": base_summary["failed"] + ui_json_summary["failed"] + plist_summary["failed"] + animation_summary["failed"] + scale_summary["failed"],
        "missing": base_summary["missing"] + ui_json_summary["missing"] + plist_summary["missing"] + animation_summary["missing"] + scale_summary["missing"],
        "skipped_existing": base_summary["skipped_existing"] + ui_json_summary["skipped_existing"] + plist_summary["skipped_existing"] + animation_summary["skipped_existing"] + scale_summary["skipped_existing"],
        "bytes_written": base_summary["bytes_written"] + ui_json_summary["bytes_written"] + plist_summary["bytes_written"] + animation_summary["bytes_written"] + scale_summary["bytes_written"],
        "elapsed_s": round(base_summary["elapsed_s"] + ui_json_summary["elapsed_s"] + plist_summary["elapsed_s"] + animation_summary["elapsed_s"] + scale_summary["elapsed_s"], 2),
    }
    animation_manifest_tasks = [
        AssetTask(name=task.base, dest=out_dir / f"{task.base}.png", urls=tuple(), phase="animation-repair")
        for task in animation_tasks
    ]
    all_tasks = base_tasks + plist_tasks + animation_manifest_tasks + scale_tasks
    all_failures = base_failures + ui_json_failures + plist_failures + animation_failures + scale_failures
    all_missing = base_missing + ui_json_missing + plist_missing + animation_missing + scale_missing

    if not args.dry_run:
        write_manifest(out_dir, overall_summary, all_tasks, all_failures, all_missing, missing_variants_path)
        update_status(state="completed", summary=overall_summary, current_phase="done")
        profile_event("run_complete", summary=overall_summary)
        print(
            f"\nDone. downloaded={overall_summary['downloaded']} "
            f"skipped={overall_summary['skipped_existing']} failed={overall_summary['failed']} "
            f"missing={overall_summary['missing']} "
            f"written={overall_summary['bytes_written']:,} bytes elapsed={overall_summary['elapsed_s']}s"
        )
    else:
        update_status(state="dry_run_complete", summary=overall_summary, current_phase="done")
        profile_event("run_complete", summary=overall_summary, dry_run=True)


if __name__ == "__main__":
    main()
