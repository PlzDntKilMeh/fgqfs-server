"""
Session RPCs: login, config, getTransactionSummary.
"""
from __future__ import annotations

import json
import hashlib
import logging
import time

import db
from config import CDN_CACHE_DIR, CDN_INTERCEPT_BASE, CONTENT_DIR

log = logging.getLogger("handlers.session")

_CKS_AES_KEYS = (
    "546f6f206561737920736f206661722ed6cda565a46d266534da32a92d23659"
    "acae5a4c1fe4731b7f1f7d377517f3bbb"
)
_cks_cache: list[dict] | None = None
_CONFIG_RESPONSE_PATH = CONTENT_DIR / "cdn_catalogs" / "config_response.json"


def _load_recorded_cks() -> list[dict] | None:
    if not _CONFIG_RESPONSE_PATH.is_file():
        return None
    try:
        with _CONFIG_RESPONSE_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        log.warning("failed to read %s: %s", _CONFIG_RESPONSE_PATH, exc)
        return None

    cks = data.get("cks")
    if not isinstance(cks, list):
        return None

    entries = [entry for entry in cks if isinstance(entry, dict) and entry.get("f") and entry.get("m")]
    return entries or None


def _build_cks() -> list[dict]:
    global _cks_cache
    if _cks_cache is not None:
        return _cks_cache

    recorded = _load_recorded_cks()
    if recorded is not None:
        _cks_cache = recorded
        return _cks_cache

    entries = []
    if CDN_CACHE_DIR.is_dir():
        for path in sorted(CDN_CACHE_DIR.iterdir()):
            if path.is_file() and path.suffix != ".json" and not path.name.startswith("."):
                md5 = hashlib.md5(path.read_bytes()).hexdigest()
                entries.append({"f": path.name, "m": md5})

    _cks_cache = entries
    return entries


def handle_login(player_id: str, sk: str = "", fbt: str = "") -> dict:
    if not db.get_player(player_id):
        db.upsert_player(player_id)

    session_token = db.create_session(player_id)
    row = db.get_player(player_id)
    return {
        "success": True,
        "player_id": player_id,
        "human_id": row["human_id"] if row else player_id,
        "session": session_token,
        "device_flags": [],
    }


def handle_config(player_id: str, locale: str = "en_US") -> dict:
    now = int(time.time())
    cks = _build_cks()

    log.info("config player=%s locale=%s cks_source=cache cks=%d",
             player_id, locale, len(cks))
    return {
        "success": True,
        "server_time": now,
        "locale": locale,
        "cks": cks,
        "cksAESKeys": _CKS_AES_KEYS,
        "useCDN": True,
        "env": "master",
        "config_tags": ["NUX", "NUXOnly"],
        "bson_types": [],
        "bson_types_handling": [],
        "features": {},
        "adHocConfigs": {
            "adhocs": {
                "ConfigURL": CDN_INTERCEPT_BASE,
                "Server": {
                    "CalendarDay": now,
                    "UtcTimeStamp": now,
                    "DailyBonusDate": now,
                    "InstallDate": now,
                    "DaysSinceInstall": 0,
                    "RunNumberToday": 1,
                    "ccpa": False,
                    "PeriodicBackgroundSaveFrequency": 60,
                },
                "Social": {
                    "CalendarDate": now,
                    "TermsURL": "",
                },
                "AnalyticsEndpoints": {
                    "__collector__": {"URL": f"http://{CDN_INTERCEPT_BASE.split('/')[2]}/analytics/"},
                    "tce": {"URL": f"http://{CDN_INTERCEPT_BASE.split('/')[2]}/analytics/"},
                },
                "Settings": {
                    "userInfo": "",
                    "HowToURL": "",
                    "ReportProblemURL": "",
                },
            }
        },
    }


def handle_get_transaction_summary(player_id: str) -> dict:
    return {
        "success": True,
        "Currency": {
            "1": {
                "Q": 23544,
                "ironsource_events": {},
                "tapjoy_events": {},
            }
        },
    }


def handle_get_push_preferences(player_id: str) -> dict:
    return {
        "success": True,
        "push_preferences": [
            {"is_enabled": True, "category_id": "miscellaneous"},
        ],
    }


def handle_get_client_message_queue(player_id: str, client_id: str = "") -> list:
    return []


def handle_logout(player_id: str) -> dict:
    return {"success": True}


def handle_save_social_data(player_id: str) -> dict:
    return {"success": True}


def handle_get_content_pack_revisions() -> dict:
    return {"success": True, "content_pack_revisions": []}
