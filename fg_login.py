#!/usr/bin/env python3
"""
fg_login.py  -  Get a Family Guy tapservice session.

Usage:
    python fg_login.py                          # default: anonymous device bootstrap
    python fg_login.py --username you@example.com --password yourpass
    python fg_login.py --anon --debug

USERNAME AUTH FLOW (verified against HTTPToolkit_2026-06-12_20-21_signUpLogin_p2.har
and IDA, libfg.so UsernameAuthenticationApi.cpp):
  1. preparePasswordAuthentication(username)
     -> {salt, challenge, new_salt}
  2. derivedPassword = scrypt(password, rawSalt, N=16384, r=8, p=1, dkLen=32)
  3. authenticateUsername(username, md5(rawChallenge + derivedPassword + rawChallenge),
                         challengeHex)
     -> {success, user_id, username}
  4. getSalt + getOrCreatePlayerId({type:"username", id:str(user_id)})
     -> signed_salt + player_id
  5. login + config + getGameStatePB + getTransactionSummary

Wire format (IDA-verified):
  POST application/x-www-form-urlencoded
  Body:  request=<lowercase-percent-encoded JSON>&chksum=<md5(PREFIX+json+SUFFIX)>
  URL encoder safe set: A-Za-z0-9-._~  hex lowercase  (sub_1993708)
  Checksum salts from vtable 0x278EA5C
  Headers: rpc=<comma-list>  User-Agent=familyguy/7.2.3 android/25
"""

import argparse
import base64
import hashlib
import json
import random
import struct
import time
import uuid
from pathlib import Path

try:
    import requests
except ImportError:
    raise SystemExit("pip install requests")

# -- Salts from libfg.so vtable 0x278EA5C -------------------------------------
CHKSUM_PREFIX = b"ypNmGzEKUckojNaizWDvkIQvLPcGkPRteUfDpMkw"
CHKSUM_SUFFIX = b"WofskAaPxqIhQQykAQbRhjzoQdlicanFEbKcPtHH"

BASE_URL   = "https://familyguy.tinyco.com/tapservice/api/"
CREDS_FILE = Path("credentials.json")
ANON_DEVICE_FILE = Path("anon_device.json")
APP_VER    = "7.2.3"
SCRYPT_N   = 16384
SCRYPT_R   = 8
SCRYPT_P   = 1
SCRYPT_LEN = 32

# -- URL encoder: mirrors sub_1993708 -----------------------------------------
# Safe set A-Za-z0-9-._~, percent-encode everything else with lowercase hex.
# IDA confirmed: uses +87 (0x57) for hex digits -> lowercase a-f.
_SAFE = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")

def _urlencode(s: str) -> str:
    out = []
    for b in s.encode("utf-8"):
        out.append(chr(b) if b in _SAFE else f"%{b:02x}")
    return "".join(out)

def _chksum(json_str: str) -> str:
    return hashlib.md5(CHKSUM_PREFIX + json_str.encode() + CHKSUM_SUFFIX).hexdigest()

def _md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()

def _build_body(payload: dict) -> tuple[str, str]:
    json_str = json.dumps(payload, separators=(",", ":"))
    body = f"request={_urlencode(json_str)}&chksum={_chksum(json_str)}"
    return json_str, body

def _rand_int32() -> int:
    """Signed int32 from arc4random() -- how the game generates install_id."""
    return struct.unpack(">i", random.randbytes(4))[0]

def _rand_hex(n: int) -> str:
    return random.randbytes(n).hex()

def _rand_id(length: int = 32) -> str:
    chars = "abcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(random.choice(chars) for _ in range(length))

# -- Credential persistence ---------------------------------------------------

def load_creds() -> dict:
    if CREDS_FILE.exists():
        return json.loads(CREDS_FILE.read_text())
    return {}

def save_creds(creds: dict):
    CREDS_FILE.write_text(json.dumps(creds, indent=2))
    print(f"  Saved to {CREDS_FILE.resolve()}")


def load_anon_device() -> dict:
    if ANON_DEVICE_FILE.exists():
        return json.loads(ANON_DEVICE_FILE.read_text())
    return {}


def save_anon_device(device: dict):
    ANON_DEVICE_FILE.write_text(json.dumps(device, indent=2))
    print(f"  Saved anon device to {ANON_DEVICE_FILE.resolve()}")

# -- Shared device identity ---------------------------------------------------

def _make_device(install_id: int) -> dict:
    android_id = _rand_hex(8)
    return {
        "install_id": install_id,
        "android_id": android_id,
        "session": _rand_hex(16),
        "android_identifiers": {
            "SERIAL_ID":    _rand_hex(8),
            "ANDROID_ID":   android_id,
            "RANDOM_ID":    _rand_id(32),
            "WIFI_ID":      "02:00:00:00:00:00",
            "referrer_str": "",
            "idfa":         str(uuid.uuid4()),
        },
    }


def _make_or_load_anon_device(install_id: int | None = None) -> dict:
    existing = load_anon_device()
    if existing:
        device = dict(existing)
        if install_id is not None:
            device["install_id"] = install_id
        device["session"] = _rand_hex(16)
        return device

    device = _make_device(install_id if install_id is not None else _rand_int32())
    save_anon_device(device)
    return device

def _base_envelope(device: dict, player_id: str = "", session: str = "",
                   human_id: str = "") -> dict:
    """Outer envelope present in every request (HAR + IDA sub_1993B6C)."""
    now = int(time.time())
    return {
        "android_identifiers":      device["android_identifiers"],
        "appid":                    "com.tinycorp.familyguy.android",
        "client_timestamp":         now,
        "country":                  "US",
        "device_id":                device["android_id"],
        "device_id_prefer_imei":    device["android_id"],
        "device_manufacturer":      "samsung",
        "device_model":             "d2q",
        "device_model_name":        "SM-N976N",
        "human_id":                 human_id,
        "identifier_type":          "ANDROID_ID",
        "install_id":               device["install_id"],
        "ip_address":               "169.254.0.1",
        "language":                 "en",
        "level":                    0,
        "locale":                   "en_US",
        "memory_cap":               128,
        "native_memory_cap":        3482,
        "network_info":             "Wi-Fi",
        "network_link_Mbps":        -1,
        "num_attempts":             0,
        "os_type":                  "android",
        "os_version":               "25",
        "player_id":                player_id,
        "run_number":               1,
        "run_number_this_version":  1,
        "session":                  session,
        "software_version":         APP_VER,
        "starting_free_memory":     1375,
        "timezone_gmt_offset":      -18000,
    }

def _post(http: requests.Session, rpc_names: list, payload: dict,
          debug: bool = False) -> dict:
    json_str, body = _build_body(payload)
    headers = {
        "rpc":             ",".join(rpc_names),
        "User-Agent":      f"familyguy/{APP_VER} android/25",
        "Content-Type":    "application/x-www-form-urlencoded; charset=utf-8",
        "Accept-Encoding": "gzip",
        "Connection":      "Keep-Alive",
    }

    if debug:
        print(f"\n  -- REQUEST ({','.join(rpc_names)}) -------------------")
        print(f"  Payload:\n{json.dumps(payload, indent=2)[:1500]}")
        print(f"  Checksum: {_chksum(json_str)}")
        print(f"  Body[:300]: {body[:300]}")

    resp = http.post(BASE_URL, data=body, headers=headers, timeout=30)

    if debug or not resp.ok:
        print(f"\n  -- RESPONSE (HTTP {resp.status_code}) ----------------")
        try:
            print(json.dumps(resp.json(), indent=2)[:3000])
        except Exception:
            print(resp.text[:2000])
        print()

    resp.raise_for_status()
    return resp.json()

def _first_response(raw: dict, label: str) -> dict:
    resp_array = raw.get("response", raw) if isinstance(raw, dict) else raw
    if not isinstance(resp_array, list) or not resp_array:
        raise RuntimeError(f"{label}: unexpected response shape: {raw}")
    entry = resp_array[0]
    if not isinstance(entry, dict):
        raise RuntimeError(f"{label}: first response entry is not a dict: {entry}")
    return entry

def _derive_password_bytes(password: str, salt: str) -> bytes:
    return hashlib.scrypt(
        password.encode(),
        salt=bytes.fromhex(salt),
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_LEN,
    )

def _auth_proof(derived_password: bytes, challenge_hex: str) -> str:
    # IDA sub_19A5FA0 decodes challenge hex to bytes, sub_19A8B88 returns raw
    # scrypt bytes, and sub_1B92138 MD5s: challenge || derived || challenge.
    challenge = bytes.fromhex(challenge_hex)
    return hashlib.md5(challenge + derived_password + challenge).hexdigest()

def prepare_password_auth(http: requests.Session, device: dict, username: str,
                          debug: bool = False) -> dict:
    envelope = _base_envelope(device, session=device["session"])
    envelope["data"] = [["preparePasswordAuthentication", username]]

    print(f"  [Auth 1] POST preparePasswordAuthentication  username={username}")
    entry = _first_response(
        _post(http, ["preparePasswordAuthentication"], envelope, debug=debug),
        "preparePasswordAuthentication",
    )
    if not entry.get("success"):
        raise RuntimeError(f"preparePasswordAuthentication failed: {entry}")
    for key in ("salt", "challenge"):
        if not entry.get(key):
            raise RuntimeError(f"preparePasswordAuthentication missing {key}: {entry}")
    return entry

def authenticate_username(http: requests.Session, device: dict, username: str,
                          password: str, auth_info: dict,
                          debug: bool = False) -> dict:
    derived = _derive_password_bytes(password, auth_info["salt"])
    challenge = auth_info["challenge"]
    proof = _auth_proof(derived, challenge)

    envelope = _base_envelope(device, session=device["session"])
    envelope["data"] = [["authenticateUsername", username, proof, challenge]]

    print("  [Auth 2] POST authenticateUsername")
    if debug:
        print(f"  scrypt salt={auth_info['salt']} derived={derived.hex()}")
        print(f"  challenge={challenge} proof={proof}")
    entry = _first_response(
        _post(http, ["authenticateUsername"], envelope, debug=debug),
        "authenticateUsername",
    )
    if not entry.get("success"):
        raise RuntimeError(f"authenticateUsername failed: {entry}")
    if not entry.get("user_id"):
        raise RuntimeError(f"authenticateUsername missing user_id: {entry}")
    return entry

def get_salt_and_username_player(http: requests.Session, device: dict,
                                 user_id,
                                 debug: bool = False) -> tuple[str, str, str]:
    envelope = _base_envelope(device, session=device["session"])
    envelope["data"] = [
        ["getSalt"],
        ["getOrCreatePlayerId", {
            "type": "username",
            "id": str(user_id),
        }],
    ]

    print(f"  [Auth 3] POST getSalt,getOrCreatePlayerId  user_id={user_id}")
    raw = _post(http, ["getSalt", "getOrCreatePlayerId"], envelope, debug=debug)
    resp_array = raw.get("response", raw) if isinstance(raw, dict) else raw
    if not isinstance(resp_array, list) or len(resp_array) < 2:
        raise RuntimeError(f"getSalt/getOrCreatePlayerId: unexpected response: {raw}")

    salt_resp, player_resp = resp_array[0], resp_array[1]
    if not isinstance(salt_resp, dict) or not isinstance(player_resp, dict):
        raise RuntimeError(f"getSalt/getOrCreatePlayerId: bad entries: {resp_array}")

    signed_salt = salt_resp.get("signed_salt", "")
    player_id = player_resp.get("player_id", "")
    human_id = player_resp.get("human_id") or ""

    if not signed_salt or not player_id:
        raise RuntimeError(f"getSalt/getOrCreatePlayerId missing data: {resp_array}")
    return player_id, human_id, signed_salt


def get_salt_and_device_player(http: requests.Session, device: dict,
                               debug: bool = False) -> tuple[str, str, str]:
    envelope = _base_envelope(device, session=device["session"])
    envelope["data"] = [
        ["getSalt"],
        ["getOrCreatePlayerId", {
            "type": "device",
            "id": device["android_id"],
        }],
    ]

    print(f"  [Auth 1] POST getSalt,getOrCreatePlayerId  device_id={device['android_id']}")
    raw = _post(http, ["getSalt", "getOrCreatePlayerId"], envelope, debug=debug)
    resp_array = raw.get("response", raw) if isinstance(raw, dict) else raw
    if not isinstance(resp_array, list) or len(resp_array) < 2:
        raise RuntimeError(f"getSalt/getOrCreatePlayerId: unexpected response: {raw}")

    salt_resp, player_resp = resp_array[0], resp_array[1]
    if not isinstance(salt_resp, dict) or not isinstance(player_resp, dict):
        raise RuntimeError(f"getSalt/getOrCreatePlayerId: bad entries: {resp_array}")

    signed_salt = salt_resp.get("signed_salt", "")
    player_id = player_resp.get("player_id", "")
    human_id = player_resp.get("human_id") or ""

    if not signed_salt or not player_id:
        raise RuntimeError(f"getSalt/getOrCreatePlayerId missing data: {resp_array}")
    return player_id, human_id, signed_salt

def startup_login(http: requests.Session, device: dict,
                  player_id: str, human_id: str, signed_salt: str,
                  game_state_token: str = "",
                  include_game_state: bool = False,
                  include_config: bool = False,
                  debug: bool = False) -> dict:
    """POST captured startup batch. Returns creds dict."""

    now         = int(time.time())
    salt_ts     = f"{now}.{random.randint(100000, 999999)}"
    salt_md5    = _rand_hex(16)
    salt_b64    = base64.urlsafe_b64encode(random.randbytes(27)).decode().rstrip("=")
    config_salt = f'[{salt_ts}, "{salt_md5}"].{salt_b64}'

    envelope = _base_envelope(
        device,
        player_id=player_id,
        human_id=human_id,
        session=device["session"],
    )
    envelope["data"] = [
        ["login", {
            "sk":  "",
            "fbt": "",
        }],
        ["config",
            {"fullLocale": "en_US", "language": "en", "locale": "en_US",
             "preferred": "", "country": "US"},
            {"salt": signed_salt or config_salt, "signature": ""},
            ["t"]
        ],
        ["getGameStatePB", game_state_token],
        ["getTransactionSummary"],
    ]

    print("  [Auth 4] POST login,config,getGameStatePB,getTransactionSummary")
    raw = _post(http, ["login", "config", "getGameStatePB", "getTransactionSummary"],
                envelope, debug=debug)

    resp_array = raw.get("response", raw) if isinstance(raw, dict) else raw
    if not isinstance(resp_array, list) or not resp_array:
        raise RuntimeError(f"startup login: unexpected response shape: {raw}")

    login_resp = resp_array[0]
    if not isinstance(login_resp, dict):
        raise RuntimeError(f"startup login: login entry not a dict: {login_resp}")

    out_player_id = login_resp.get("player_id", player_id)
    out_session   = login_resp.get("session", device["session"])
    out_human_id  = login_resp.get("human_id", "")

    if not out_session:
        print(f"\n  Startup login response:\n{json.dumps(login_resp, indent=2)}")
        raise RuntimeError("startup login: no session in login response")

    return {
        "player_id":  out_player_id,
        "human_id":   out_human_id,
        "install_id": device["install_id"],
        "android_id": device["android_id"],
        "session":    out_session,
        **({"config": resp_array[1] if len(resp_array) > 1 else {}}
           if include_config else {}),
        **({"game_state": resp_array[2] if len(resp_array) > 2 else {}}
           if include_game_state else {}),
    }

# -- Top-level ----------------------------------------------------------------

def full_login(email: str, password: str, install_id: int,
               game_state_token: str = "",
               include_game_state: bool = False,
               include_config: bool = False,
               debug: bool = False) -> dict:
    device = _make_device(install_id)
    http   = requests.Session()

    auth_info = prepare_password_auth(http, device, email, debug=debug)
    auth_resp = authenticate_username(http, device, email, password, auth_info,
                                      debug=debug)
    player_id, human_id, signed_salt = get_salt_and_username_player(
        http, device, auth_resp["user_id"], debug=debug
    )
    creds = startup_login(
        http, device, player_id, human_id, signed_salt,
        game_state_token=game_state_token,
        include_game_state=include_game_state,
        include_config=include_config,
        debug=debug,
    )
    creds["username"] = auth_resp.get("username", email)
    creds["user_id"] = auth_resp["user_id"]
    return creds


def anon_login(install_id: int | None = None,
               game_state_token: str = "",
               include_game_state: bool = False,
               include_config: bool = False,
               debug: bool = False) -> dict:
    device = _make_or_load_anon_device(install_id)
    http = requests.Session()
    player_id, human_id, signed_salt = get_salt_and_device_player(http, device, debug=debug)
    creds = startup_login(
        http, device, player_id, human_id, signed_salt,
        game_state_token=game_state_token,
        include_game_state=include_game_state,
        include_config=include_config,
        debug=debug,
    )
    creds["auth_mode"] = "anon"
    return creds


def main():
    parser = argparse.ArgumentParser(description="FG tapservice login (default: anonymous device)")
    parser.add_argument("--username", "--email", dest="email")
    parser.add_argument("--password")
    parser.add_argument("--anon", action="store_true",
                        help="Force anonymous device bootstrap")
    parser.add_argument("--install-id", type=int, default=None,
                        help="Signed int32 install_id (generated if omitted)")
    parser.add_argument("--game-state-token", default="",
                        help="Optional getGameStatePB token from a capture")
    parser.add_argument("--debug",      action="store_true")
    args = parser.parse_args()

    existing   = load_creds()
    install_id = args.install_id or existing.get("install_id") or _rand_int32()
    print(f"\n  install_id : {install_id}")

    use_anon = args.anon or not (args.email and args.password)
    if use_anon:
        print("\n  Starting anonymous device login...")
        creds = anon_login(
            install_id=install_id,
            game_state_token=args.game_state_token,
            debug=args.debug,
        )
        print(f"\n  OK  player_id : {creds['player_id']}")
        print(f"  OK  session   : {creds['session']}")
        print(f"  OK  human_id  : {creds['human_id']}")
        print()
        save_creds(creds)
        return

    print("\n  Starting username login...")
    creds = full_login(
        args.email, args.password,
        install_id=install_id,
        game_state_token=args.game_state_token,
        debug=args.debug,
    )

    print(f"\n  OK  player_id : {creds['player_id']}")
    print(f"  OK  session   : {creds['session']}")
    print(f"  OK  human_id  : {creds['human_id']}")
    print()
    save_creds(creds)


if __name__ == "__main__":
    main()
