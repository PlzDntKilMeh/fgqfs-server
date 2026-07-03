from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parent
BOOTSTRAP_DIR = ROOT / "content" / "bootstrap"
FRESH_ACCOUNT_SAVE_HEX_PATH = BOOTSTRAP_DIR / "fresh_account_save.hex"
FRESH_ACCOUNT_SAVE_VERSION = "1.0"


def load_fresh_account_save_bytes() -> bytes:
    text = FRESH_ACCOUNT_SAVE_HEX_PATH.read_text(encoding="utf-8")
    hex_text = "".join(
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    )
    if not hex_text:
        raise ValueError(f"Bootstrap save file is empty: {FRESH_ACCOUNT_SAVE_HEX_PATH}")
    return bytes.fromhex(hex_text)
