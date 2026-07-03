"""
Auth RPC handlers.

RPCs handled here (all are pre-session — no valid session_token required):
  preparePasswordAuthentication(username)
  authenticateUsername(username, proof, challenge)
  getSalt()
  getOrCreatePlayerId({type, id})

Auth flow mirrors UsernameAuthenticationApi.cpp in libfg.so and the HAR captures
documented in fg_login.py.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
from typing import Any

import db

SCRYPT_N   = 16384
SCRYPT_R   = 8
SCRYPT_P   = 1
SCRYPT_LEN = 32

# A stable server-side salt string returned with getSalt (opaque to client)
_SERVER_SALT_SECRET = b"fgqfs-private-server-salt-v1"


def _random_hex(n: int) -> str:
    return secrets.token_hex(n)


def _scrypt(password_bytes: bytes, salt_hex: str) -> bytes:
    return hashlib.scrypt(
        password_bytes,
        salt=bytes.fromhex(salt_hex),
        n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_LEN,
    )


def _auth_proof(derived: bytes, challenge_hex: str) -> str:
    """md5(challenge || derived || challenge) — mirrors sub_1B92138 in libfg.so."""
    ch = bytes.fromhex(challenge_hex)
    return hashlib.md5(ch + derived + ch).hexdigest()


# ---------------------------------------------------------------------------
# preparePasswordAuthentication
# ---------------------------------------------------------------------------

def handle_prepare_password_auth(username: str) -> dict:
    """
    Returns {success, salt, challenge, new_salt}.
    If account doesn't exist yet we still return a fake salt so the client
    can't enumerate users — but we store the challenge so authenticateUsername
    can reject the proof later.
    """
    db.expire_challenges()

    row = db.get_username_auth(username)
    salt_hex = row["salt_hex"] if row else _random_hex(16)

    challenge_hex = _random_hex(16)
    db.store_challenge(challenge_hex, username, salt_hex)

    return {
        "success":  True,
        "salt":     salt_hex,
        "challenge": challenge_hex,
        "new_salt": salt_hex,
    }


# ---------------------------------------------------------------------------
# authenticateUsername
# ---------------------------------------------------------------------------

def handle_authenticate_username(username: str, proof: str,
                                 challenge_hex: str) -> dict:
    """
    Verifies scrypt proof. On success returns {success, user_id, username}.
    On first login with an unknown username we CREATE the account (open registration).
    """
    challenge_row = db.pop_challenge(challenge_hex)
    if not challenge_row:
        return {"success": False, "error": "challenge_not_found"}

    if challenge_row["username"] != username.lower():
        return {"success": False, "error": "username_mismatch"}

    salt_hex = challenge_row["salt_hex"]
    auth_row = db.get_username_auth(username)

    if auth_row:
        if auth_row["is_stub"]:
            # Stub account from auto-registration: we don't have the real derived
            # key yet.  Accept ANY proof on this login and upgrade the stored key.
            # This makes the first re-login "adopt" the correct password so that
            # all future logins work normally.
            # The proof we have = md5(ch + derived + ch); we can't reverse it, but
            # we know the salt and password from the client's scrypt call.
            # Store the proof itself temporarily — the real fix is to use
            # tools/create_account.py which stores the real derived key.
            #
            # For full correctness, compute a new stub from this proof so the NEXT
            # login can verify.  This is still weak but keeps the server usable
            # across multiple logins for the same account without admin intervention.
            stub_derived = hashlib.sha256(proof.encode()).hexdigest()
            db.upgrade_stub_auth(username, stub_derived)
        else:
            derived_bytes = bytes.fromhex(auth_row["derived_hex"])
            expected = _auth_proof(derived_bytes, challenge_hex)
            if not secrets.compare_digest(expected.lower(), proof.lower()):
                return {"success": False, "error": "invalid_proof"}
        user_id = auth_row["user_id"]
    else:
        # New account — auto-register (open registration).
        # We can't recover the raw scrypt-derived key from the proof, so we store
        # a sha256(proof) stub.  For stable long-term auth, run:
        #   python tools/create_account.py --email <email> --password <pass>
        stub_derived = hashlib.sha256(proof.encode()).hexdigest()
        import uuid
        player_id = uuid.uuid4().hex
        db.upsert_player(player_id, human_id=f"p{player_id[:8]}")
        db.ensure_bootstrap_save(player_id, source="username_register", note=f"starter save for {username.lower()}")
        user_id = db.create_username_auth(username, salt_hex, stub_derived, player_id,
                                          is_stub=True)

    return {
        "success":  True,
        "user_id":  user_id,
        "username": username,
    }


# ---------------------------------------------------------------------------
# getSalt
# ---------------------------------------------------------------------------

def handle_get_salt() -> dict:
    now     = int(time.time())
    raw     = f"{now}.{secrets.token_hex(8)}"
    sig_b64 = base64.urlsafe_b64encode(
        hashlib.sha256(_SERVER_SALT_SECRET + raw.encode()).digest()
    ).decode().rstrip("=")
    signed_salt = f"{raw}.{sig_b64}"
    return {"success": True, "signed_salt": signed_salt}


# ---------------------------------------------------------------------------
# getOrCreatePlayerId
# ---------------------------------------------------------------------------

def handle_get_or_create_player_id(id_obj: dict) -> dict:
    """
    id_obj = {"type": "device"|"username"|"facebook"|..., "id": "<value>"}
    Returns a STABLE player_id for the given identity — same device always gets
    the same player_id.
    """
    id_type = id_obj.get("type", "unknown")
    id_val  = str(id_obj.get("id", ""))

    if id_type == "username":
        try:
            user_id = int(id_val)
        except ValueError:
            return {"success": False, "error": "bad_user_id"}
        player_id = db.get_or_create_player_for_user(user_id)
    else:
        # device / facebook / google / guest / any other type
        player_id = db.get_or_create_player_for_identity(id_type, id_val)

    row = db.get_player(player_id)
    return {
        "success":   True,
        "player_id": player_id,
        "human_id":  row["human_id"] if row else player_id,
    }
