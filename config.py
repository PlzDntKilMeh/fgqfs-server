"""Central config for the shareable FG:QfS private server repo."""
import json
import os
import socket
import time
from pathlib import Path

ROOT = Path(__file__).parent
SETTINGS_PATH = ROOT / "server_settings.json"


def _load_settings() -> dict:
    if not SETTINGS_PATH.is_file():
        return {}
    with SETTINGS_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{SETTINGS_PATH} must contain a JSON object")
    return data


def _setting_str(settings: dict, name: str, default: str) -> str:
    env_name = f"FGQFS_{name}"
    if env_name in os.environ:
        return os.environ[env_name]
    value = settings.get(name.lower(), default)
    return "" if value is None else str(value)


def _sanitize_public_host(value: str, fallback: str) -> str:
    host = (value or "").strip().strip("/")
    if not host or host.startswith("+") or host.startswith(":") or host.startswith("http://") or host.startswith("https://"):
        return fallback
    return host


def _detect_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        pass
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    return "127.0.0.1"


def _setting_bool(settings: dict, name: str, default: bool) -> bool:
    env_name = f"FGQFS_{name}"
    if env_name in os.environ:
        value = os.environ[env_name]
    else:
        value = settings.get(name.lower(), default)
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _setting_int(settings: dict, name: str, default: int, aliases: tuple[str, ...] = ()) -> int:
    for key in (name, *aliases):
        env_name = f"FGQFS_{key}"
        if env_name in os.environ:
            return int(os.environ[env_name])
        lowered = key.lower()
        if lowered in settings:
            return int(settings[lowered])
    return default


_SETTINGS = _load_settings()

CONTENT_DIR = ROOT / "content"
BOOTSTRAP_DIR = CONTENT_DIR / "bootstrap"
CDN_CACHE_DIR = CONTENT_DIR / "cdn_catalogs" / "files"
_DEFAULT_ASSET_CACHE_DIR = CONTENT_DIR / "cdn_assets" / "files"
ASSET_CACHE_DIR = _DEFAULT_ASSET_CACHE_DIR
ASSET_LAZY_DIR = CONTENT_DIR / "cdn_assets_lazy"

DB_PATH = ROOT / "fgqfs.db"

HOST = "0.0.0.0"
PORT = _setting_int(_SETTINGS, "SERVER_PORT", 6767, aliases=("PORT",))
PROXY_PORT = _setting_int(_SETTINGS, "PROXY_PORT", 6769)
_DETECTED_PUBLIC_HOST = f"{_detect_lan_ip()}:{PORT}"
PUBLIC_HOST = _sanitize_public_host(
    _setting_str(_SETTINGS, "PUBLIC_HOST", _DETECTED_PUBLIC_HOST),
    _sanitize_public_host(str(_SETTINGS.get("public_host", _DETECTED_PUBLIC_HOST)), _DETECTED_PUBLIC_HOST),
)
CDN_INTERCEPT_BASE = f"http://{PUBLIC_HOST}/cdn/"

CDN_LIVE_BASE = "https://cdn2.familyguy.tinyco.com"
ASSET_LIVE_BASE = "https://family-guy-qfs.jamcity-static.com"

CHKSUM_PREFIX = b"ypNmGzEKUckojNaizWDvkIQvLPcGkPRteUfDpMkw"
CHKSUM_SUFFIX = b"WofskAaPxqIhQQykAQbRhjzoQdlicanFEbKcPtHH"

APP_VER = "7.2.3"
LAZY_FETCH: bool = _setting_bool(_SETTINGS, "LAZY_FETCH", False)

USE_SHARED_SAVE: bool = _setting_bool(_SETTINGS, "USE_SHARED_SAVE", True)
SHARED_SAVE_PID: str = _setting_str(_SETTINGS, "SHARED_SAVE_PID", "0")


def current_server_time() -> int:
    return int(time.time())
