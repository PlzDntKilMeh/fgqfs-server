"""
SQLite persistence layer.

Tables
------
players          : one row per player_id, holds account credentials + metadata
saves            : one row per player_id, latest serialised save blob (raw proto bytes)
username_auth    : maps username → user_id + scrypt verifier material
"""
from __future__ import annotations

import sqlite3
import threading
import time
import uuid
import hashlib
import zlib
from pathlib import Path
from typing import Optional

import bootstrap_save
import config

_local = threading.local()


def _conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(str(config.DB_PATH), check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


def init_db() -> None:
    c = _conn()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS players (
            player_id   TEXT PRIMARY KEY,
            human_id    TEXT NOT NULL,
            install_id  TEXT,
            created_at  INTEGER NOT NULL,
            last_seen   INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS saves (
            player_id    TEXT PRIMARY KEY REFERENCES players(player_id),
            save_version TEXT NOT NULL DEFAULT '2.1.0.0',
            proto_bytes  BLOB,
            updated_at   INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS save_revisions (
            revision_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id     TEXT NOT NULL REFERENCES players(player_id),
            save_version  TEXT NOT NULL DEFAULT '2.1.0.0',
            proto_bytes   BLOB NOT NULL,
            proto_encoding TEXT NOT NULL DEFAULT 'raw',
            raw_bytes     INTEGER,
            blob_md5      TEXT NOT NULL,
            source        TEXT NOT NULL DEFAULT '',
            note          TEXT NOT NULL DEFAULT '',
            created_at    INTEGER NOT NULL
        );

        -- Username-based auth (mirrors UsernameAuthenticationApi in libfg.so)
        CREATE TABLE IF NOT EXISTS username_auth (
            user_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            username     TEXT UNIQUE NOT NULL,
            salt_hex     TEXT NOT NULL,
            derived_hex  TEXT NOT NULL,
            player_id    TEXT REFERENCES players(player_id),
            is_stub      INTEGER NOT NULL DEFAULT 0
        );

        -- Generic identity → player mapping (device, facebook, google, guest, etc.)
        CREATE TABLE IF NOT EXISTS identity_map (
            identity_type  TEXT NOT NULL,
            identity_id    TEXT NOT NULL,
            player_id      TEXT NOT NULL REFERENCES players(player_id),
            created_at     INTEGER NOT NULL,
            PRIMARY KEY (identity_type, identity_id)
        );

        -- Session tokens (login RPC returns a new session each startup)
        CREATE TABLE IF NOT EXISTS sessions (
            session_token TEXT PRIMARY KEY,
            player_id     TEXT NOT NULL REFERENCES players(player_id),
            created_at    INTEGER NOT NULL
        );

        -- Salt cache for preparePasswordAuthentication challenges
        CREATE TABLE IF NOT EXISTS auth_challenges (
            challenge_hex TEXT PRIMARY KEY,
            username      TEXT NOT NULL,
            salt_hex      TEXT NOT NULL,
            created_at    INTEGER NOT NULL
        );
    """)
    _ensure_revision_columns(c)
    rows = c.execute("""
        SELECT s.player_id, s.save_version, s.proto_bytes
        FROM saves s
        LEFT JOIN save_revisions sr ON sr.player_id = s.player_id
        WHERE sr.revision_id IS NULL AND s.proto_bytes IS NOT NULL
    """).fetchall()
    now = int(time.time())
    for row in rows:
        proto_bytes = bytes(row["proto_bytes"])
        stored_bytes, encoding = _encode_revision_proto(proto_bytes)
        c.execute("""
            INSERT INTO save_revisions (
                player_id, save_version, proto_bytes, proto_encoding, raw_bytes,
                blob_md5, source, note, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            row["player_id"],
            row["save_version"] or "2.1.0.0",
            stored_bytes,
            encoding,
            len(proto_bytes),
            _blob_md5(proto_bytes),
            "backfill",
            "backfilled existing current save",
            now,
        ))
    c.commit()

    if config.USE_SHARED_SAVE and config.SHARED_SAVE_PID:
        upsert_player(config.SHARED_SAVE_PID, human_id=config.SHARED_SAVE_PID)
        ensure_bootstrap_save(
            config.SHARED_SAVE_PID,
            source="startup",
            note="starter save for shared gameplay slot",
        )


def _ensure_revision_columns(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(save_revisions)").fetchall()}
    if "proto_encoding" not in columns:
        conn.execute("ALTER TABLE save_revisions ADD COLUMN proto_encoding TEXT NOT NULL DEFAULT 'raw'")
    if "raw_bytes" not in columns:
        conn.execute("ALTER TABLE save_revisions ADD COLUMN raw_bytes INTEGER")


# ---------------------------------------------------------------------------
# Players
# ---------------------------------------------------------------------------

def get_player(player_id: str) -> Optional[sqlite3.Row]:
    return _conn().execute(
        "SELECT * FROM players WHERE player_id = ?", (player_id,)
    ).fetchone()


def upsert_player(player_id: str, human_id: str = "", install_id: str = "") -> None:
    now = int(time.time())
    _conn().execute("""
        INSERT INTO players (player_id, human_id, install_id, created_at, last_seen)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(player_id) DO UPDATE SET last_seen=excluded.last_seen,
            install_id=COALESCE(excluded.install_id, install_id)
    """, (player_id, human_id or player_id, install_id, now, now))
    _conn().commit()


def ensure_bootstrap_save(player_id: str, source: str = "bootstrap",
                          note: str = "initial starter save") -> int | None:
    row = get_save(player_id)
    if row and row["proto_bytes"]:
        return None
    proto_bytes = bootstrap_save.load_fresh_account_save_bytes()
    return put_save(
        player_id,
        proto_bytes,
        save_version=bootstrap_save.FRESH_ACCOUNT_SAVE_VERSION,
        source=source,
        note=note,
        force_revision=True,
    )


# ---------------------------------------------------------------------------
# Generic identity map  (device, facebook, google, guest, …)
# ---------------------------------------------------------------------------

def get_or_create_player_for_identity(identity_type: str, identity_id: str) -> str:
    """Return a stable player_id for any (type, id) pair, creating one if new."""
    row = _conn().execute(
        "SELECT player_id FROM identity_map WHERE identity_type=? AND identity_id=?",
        (identity_type, identity_id),
    ).fetchone()
    if row:
        return row["player_id"]
    player_id = uuid.uuid4().hex
    human_id  = f"p{player_id[:8]}"
    upsert_player(player_id, human_id=human_id)
    ensure_bootstrap_save(player_id, source=f"identity:{identity_type}", note=f"starter save for {identity_type} identity")
    _conn().execute(
        "INSERT INTO identity_map (identity_type, identity_id, player_id, created_at) VALUES (?,?,?,?)",
        (identity_type, identity_id, player_id, int(time.time())),
    )
    _conn().commit()
    return player_id


# ---------------------------------------------------------------------------
# Saves
# ---------------------------------------------------------------------------

def get_save(player_id: str) -> Optional[sqlite3.Row]:
    return _conn().execute(
        "SELECT * FROM saves WHERE player_id = ?", (player_id,)
    ).fetchone()


def list_saves() -> list[sqlite3.Row]:
    return _conn().execute("""
        SELECT
            p.player_id,
            p.human_id,
            p.install_id,
            p.created_at,
            p.last_seen,
            s.save_version,
            s.updated_at,
            LENGTH(s.proto_bytes) AS save_bytes,
            ua.username,
            (
                SELECT COUNT(*)
                FROM save_revisions sr
                WHERE sr.player_id = p.player_id
            ) AS revision_count
        FROM players p
        LEFT JOIN saves s ON s.player_id = p.player_id
        LEFT JOIN username_auth ua ON ua.player_id = p.player_id
        ORDER BY COALESCE(s.updated_at, 0) DESC, p.last_seen DESC, p.player_id
    """).fetchall()


def _blob_md5(proto_bytes: bytes) -> str:
    return hashlib.md5(proto_bytes).hexdigest()


def _encode_revision_proto(proto_bytes: bytes) -> tuple[bytes, str]:
    return zlib.compress(proto_bytes, level=6), "zlib"


def revision_proto_bytes(row: sqlite3.Row) -> bytes:
    proto_bytes = bytes(row["proto_bytes"])
    encoding = "raw"
    try:
        encoding = row["proto_encoding"] or "raw"
    except (IndexError, KeyError):
        pass
    if encoding == "zlib":
        return zlib.decompress(proto_bytes)
    if encoding == "raw":
        return proto_bytes
    raise ValueError(f"Unsupported save revision encoding: {encoding}")


def get_latest_save_revision(player_id: str) -> Optional[sqlite3.Row]:
    return _conn().execute("""
        SELECT *
        FROM save_revisions
        WHERE player_id = ?
        ORDER BY revision_id DESC
        LIMIT 1
    """, (player_id,)).fetchone()


def get_save_revision(revision_id: int) -> Optional[sqlite3.Row]:
    return _conn().execute(
        "SELECT * FROM save_revisions WHERE revision_id = ?",
        (revision_id,),
    ).fetchone()


def list_save_revisions(player_id: str, limit: int = 100) -> list[sqlite3.Row]:
    return _conn().execute("""
        SELECT revision_id, player_id, save_version, blob_md5, source, note, created_at,
               COALESCE(raw_bytes, LENGTH(proto_bytes)) AS save_bytes,
               LENGTH(proto_bytes) AS stored_bytes,
               proto_encoding
        FROM save_revisions
        WHERE player_id = ?
        ORDER BY revision_id DESC
        LIMIT ?
    """, (player_id, limit)).fetchall()


def put_save(player_id: str, proto_bytes: bytes, save_version: str = "2.1.0.0",
             source: str = "", note: str = "", force_revision: bool = False) -> int | None:
    now = int(time.time())
    blob_md5 = _blob_md5(proto_bytes)
    _conn().execute("""
        INSERT INTO saves (player_id, save_version, proto_bytes, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(player_id) DO UPDATE SET
            proto_bytes=excluded.proto_bytes,
            save_version=excluded.save_version,
            updated_at=excluded.updated_at
    """, (player_id, save_version, proto_bytes, now))

    latest = get_latest_save_revision(player_id)
    should_insert_revision = force_revision or not latest or (
        latest["blob_md5"] != blob_md5 or latest["save_version"] != save_version
    )
    revision_id = None
    if should_insert_revision:
        stored_bytes, encoding = _encode_revision_proto(proto_bytes)
        cur = _conn().execute("""
            INSERT INTO save_revisions (
                player_id, save_version, proto_bytes, proto_encoding, raw_bytes,
                blob_md5, source, note, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (player_id, save_version, stored_bytes, encoding, len(proto_bytes), blob_md5, source, note, now))
        revision_id = cur.lastrowid
    _conn().commit()
    return revision_id


def restore_save_revision(target_player_id: str, revision_id: int,
                          source: str = "restore", note: str = "") -> int:
    row = get_save_revision(revision_id)
    if not row:
        raise ValueError(f"Unknown revision_id={revision_id}")
    if not get_player(target_player_id):
        upsert_player(target_player_id, human_id=f"p{target_player_id[:8]}")
    put_save(
        target_player_id,
        revision_proto_bytes(row),
        save_version=row["save_version"],
        source=source,
        note=note or f"restored from revision {revision_id}",
        force_revision=True,
    )
    return revision_id


def export_current_save(player_id: str) -> bytes:
    row = get_save(player_id)
    if not row or not row["proto_bytes"]:
        raise ValueError(f"No save for player_id={player_id}")
    return bytes(row["proto_bytes"])


# ---------------------------------------------------------------------------
# Username auth
# ---------------------------------------------------------------------------

def get_username_auth(username: str) -> Optional[sqlite3.Row]:
    return _conn().execute(
        "SELECT * FROM username_auth WHERE username = ?", (username.lower(),)
    ).fetchone()


def get_player_id_for_username(username: str) -> Optional[str]:
    row = _conn().execute(
        "SELECT player_id FROM username_auth WHERE username = ?",
        (username.lower(),),
    ).fetchone()
    return row["player_id"] if row and row["player_id"] else None


def create_username_auth(username: str, salt_hex: str, derived_hex: str,
                         player_id: str, is_stub: bool = False) -> int:
    cur = _conn().execute("""
        INSERT INTO username_auth (username, salt_hex, derived_hex, player_id, is_stub)
        VALUES (?, ?, ?, ?, ?)
    """, (username.lower(), salt_hex, derived_hex, player_id, 1 if is_stub else 0))
    _conn().commit()
    return cur.lastrowid


def upgrade_stub_auth(username: str, derived_hex: str) -> None:
    """Replace stub derived key with the real scrypt-derived bytes."""
    _conn().execute("""
        UPDATE username_auth SET derived_hex=?, is_stub=0 WHERE username=?
    """, (derived_hex, username.lower()))
    _conn().commit()


def get_or_create_player_for_user(user_id: int) -> str:
    """Return the player_id linked to user_id, creating one if needed."""
    row = _conn().execute(
        "SELECT player_id FROM username_auth WHERE user_id = ?", (user_id,)
    ).fetchone()
    if row and row["player_id"]:
        return row["player_id"]
    player_id = str(uuid.uuid4()).replace("-", "")
    human_id  = f"p{player_id[:8]}"
    upsert_player(player_id, human_id=human_id)
    ensure_bootstrap_save(player_id, source="username", note="starter save for username account")
    _conn().execute(
        "UPDATE username_auth SET player_id = ? WHERE user_id = ?",
        (player_id, user_id)
    )
    _conn().commit()
    return player_id


# ---------------------------------------------------------------------------
# Auth challenges  (short-lived, cleaned up on next startup)
# ---------------------------------------------------------------------------

def store_challenge(challenge_hex: str, username: str, salt_hex: str) -> None:
    now = int(time.time())
    _conn().execute("""
        INSERT OR REPLACE INTO auth_challenges (challenge_hex, username, salt_hex, created_at)
        VALUES (?, ?, ?, ?)
    """, (challenge_hex, username.lower(), salt_hex, now))
    _conn().commit()


def pop_challenge(challenge_hex: str) -> Optional[sqlite3.Row]:
    row = _conn().execute(
        "SELECT * FROM auth_challenges WHERE challenge_hex = ?", (challenge_hex,)
    ).fetchone()
    if row:
        _conn().execute(
            "DELETE FROM auth_challenges WHERE challenge_hex = ?", (challenge_hex,)
        )
        _conn().commit()
    return row


def expire_challenges(max_age_s: int = 300) -> None:
    cutoff = int(time.time()) - max_age_s
    _conn().execute("DELETE FROM auth_challenges WHERE created_at < ?", (cutoff,))
    _conn().commit()


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def create_session(player_id: str) -> str:
    token = uuid.uuid4().hex + uuid.uuid4().hex  # 64-char hex
    _conn().execute("""
        INSERT INTO sessions (session_token, player_id, created_at)
        VALUES (?, ?, ?)
    """, (token, player_id, int(time.time())))
    _conn().commit()
    return token


def get_session(token: str) -> Optional[sqlite3.Row]:
    return _conn().execute(
        "SELECT * FROM sessions WHERE session_token = ?", (token,)
    ).fetchone()
