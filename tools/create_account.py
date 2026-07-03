#!/usr/bin/env python3
"""
Admin tool — create or update a player account in the private server DB.

Usage:
    python tools/create_account.py --email you@example.com --password secret
    python tools/create_account.py --email you@example.com --password secret \
        --save path/to/live_save.pbuf        # import an existing save
    python tools/create_account.py --list    # list all accounts

This correctly stores the scrypt-derived key (not a one-time proof), so the
player can log in repeatedly with the same credentials.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import uuid
from pathlib import Path

# Allow running from the tools/ subdirectory
sys.path.insert(0, str(Path(__file__).parent.parent))

import db
from config import DB_PATH

SCRYPT_N   = 16384
SCRYPT_R   = 8
SCRYPT_P   = 1
SCRYPT_LEN = 32


def _derive(password: str, salt_hex: str) -> bytes:
    return hashlib.scrypt(
        password.encode(),
        salt=bytes.fromhex(salt_hex),
        n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_LEN,
    )


def create_account(email: str, password: str, save_path: str | None = None) -> None:
    db.init_db()

    existing = db.get_username_auth(email)
    if existing:
        print(f"Account exists for {email}  (user_id={existing['user_id']}, "
              f"player_id={existing['player_id']})")
        player_id = existing["player_id"]
        # Update derived key (password change)
        import secrets
        salt_hex    = existing["salt_hex"]
        derived     = _derive(password, salt_hex)
        db._conn().execute(
            "UPDATE username_auth SET derived_hex=? WHERE username=?",
            (derived.hex(), email.lower())
        )
        db._conn().commit()
        print(f"  Password updated.")
    else:
        import secrets
        salt_hex    = secrets.token_hex(16)
        derived     = _derive(password, salt_hex)
        player_id   = uuid.uuid4().hex
        human_id    = f"p{player_id[:8]}"
        db.upsert_player(player_id, human_id=human_id)
        db.ensure_bootstrap_save(player_id, source="create_account", note=f"starter save for {email}")
        user_id = db.create_username_auth(email, salt_hex, derived.hex(), player_id)
        print(f"Created account:")
        print(f"  email     : {email}")
        print(f"  user_id   : {user_id}")
        print(f"  player_id : {player_id}")
        print(f"  human_id  : {human_id}")

    if save_path:
        p = Path(save_path)
        if not p.exists():
            print(f"ERROR: save file not found: {save_path}")
            return
        proto_bytes = p.read_bytes()
        db.put_save(player_id, proto_bytes, save_version="2.1.0.0", source="account_import", note=f"import for {email}", force_revision=True)
        print(f"  Save imported: {len(proto_bytes):,} bytes")


def list_accounts() -> None:
    db.init_db()
    rows = db._conn().execute("""
        SELECT ua.user_id, ua.username, ua.player_id, p.human_id, p.last_seen,
               (SELECT s.updated_at FROM saves s WHERE s.player_id = ua.player_id) AS save_ts
        FROM username_auth ua
        LEFT JOIN players p ON p.player_id = ua.player_id
        ORDER BY ua.user_id
    """).fetchall()
    if not rows:
        print("No accounts in DB.")
        return
    print(f"{'user_id':>8}  {'username':<30}  {'player_id':<34}  save")
    print("-" * 90)
    for r in rows:
        save_info = f"yes (t={r['save_ts']})" if r["save_ts"] else "none"
        print(f"{r['user_id']:>8}  {r['username']:<30}  {r['player_id']:<34}  {save_info}")


def import_save_for_player(player_id: str, save_path: str) -> None:
    """Directly import a .pbuf save for an existing player_id (device-auth players)."""
    db.init_db()
    p = Path(save_path)
    if not p.exists():
        print(f"ERROR: save file not found: {save_path}")
        return
    # Ensure the player row exists
    if not db.get_player(player_id):
        db.upsert_player(player_id, human_id=f"p{player_id[:8]}")
        print(f"  Created player row for {player_id}")
    proto_bytes = p.read_bytes()
    db.put_save(player_id, proto_bytes, save_version="2.1.0.0", source="player_import", note=f"import for {player_id}", force_revision=True)
    print(f"  Imported {len(proto_bytes):,} bytes -> player_id={player_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="FG:QfS private server account manager")
    parser.add_argument("--email", "--username", dest="email",
                        help="Email / username for the account")
    parser.add_argument("--password", help="Password for the account")
    parser.add_argument("--save", metavar="PATH",
                        help="Path to a .pbuf save file to import")
    parser.add_argument("--player-id", metavar="ID",
                        help="Import save directly for a player_id (device-auth accounts)")
    parser.add_argument("--list", action="store_true", help="List all accounts")
    args = parser.parse_args()

    print(f"DB: {DB_PATH}\n")

    if args.list:
        list_accounts()
        return

    if args.player_id:
        if not args.save:
            parser.error("--save is required with --player-id")
        import_save_for_player(args.player_id, args.save)
        return

    if not args.email or not args.password:
        parser.error("--email and --password are required unless --list or --player-id")

    create_account(args.email, args.password, args.save)


if __name__ == "__main__":
    main()
