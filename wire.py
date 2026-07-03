"""
Wire-format helpers shared across the server.

Mirrors the client-side logic in fg_tapservice.py / fg_login.py:
  - request chksum  : md5(PREFIX + json_str + SUFFIX)
  - response digest : md5(PREFIX + raw_body + SUFFIX)
  - save blob codec : base64(zlib(protobuf))  with optional "p:" prefix
  - native URL encoder (A-Za-z0-9-._~ safe, lowercase hex)
"""
from __future__ import annotations

import base64
import hashlib
import json
import urllib.parse
import zlib
from typing import Any

from config import CHKSUM_PREFIX, CHKSUM_SUFFIX

# ---------------------------------------------------------------------------
# Checksums
# ---------------------------------------------------------------------------

def request_chksum(json_str: str) -> str:
    return hashlib.md5(CHKSUM_PREFIX + json_str.encode() + CHKSUM_SUFFIX).hexdigest()


def response_digest(raw_body: bytes) -> str:
    return hashlib.md5(CHKSUM_PREFIX + raw_body + CHKSUM_SUFFIX).hexdigest()


def verify_request_chksum(json_str: str, claimed: str) -> bool:
    return request_chksum(json_str) == claimed.lower()


# ---------------------------------------------------------------------------
# Native URL encoder (mirrors sub_19B8A20 / sub_1993708 in libfg.so)
# ---------------------------------------------------------------------------

_SAFE = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def native_urlencode(s: str) -> str:
    out = []
    for b in s.encode("utf-8"):
        out.append(chr(b) if b in _SAFE else f"%{b:02x}")
    return "".join(out)


def decode_request_body(body: str) -> tuple[dict, str]:
    """
    Parse 'request=<urlencoded_json>&chksum=<md5>' into (payload_dict, chksum).
    """
    parts = {}
    for part in body.split("&"):
        if "=" in part:
            k, v = part.split("=", 1)
            parts[k] = v
    raw_json = urllib.parse.unquote(parts.get("request", ""))
    chksum   = parts.get("chksum", "")
    payload  = json.loads(raw_json)
    return payload, chksum, raw_json


# ---------------------------------------------------------------------------
# Response builder
# ---------------------------------------------------------------------------

def build_response(response_list: list[Any]) -> tuple[bytes, str]:
    """
    Wrap handler results in {"response": [...]} and compute x-tc-digest.
    Returns (raw_body_bytes, digest_hex).
    """
    obj      = {"response": response_list}
    raw_body = json.dumps(obj, separators=(",", ":")).encode()
    digest   = response_digest(raw_body)
    return raw_body, digest


# ---------------------------------------------------------------------------
# Save blob codec
# ---------------------------------------------------------------------------

def decode_save_blob(field_value: str) -> bytes:
    """base64(zlib(proto)) — strip optional 'p:' prefix."""
    b64 = field_value[2:] if field_value.startswith("p:") else field_value
    return zlib.decompress(base64.b64decode(b64))


def encode_save_blob(raw_proto: bytes, add_prefix: bool = False) -> str:
    compressed = zlib.compress(raw_proto, level=6)
    b64 = base64.b64encode(compressed).decode()
    return ("p:" + b64) if add_prefix else b64


def inner_blob_digest(raw_proto: bytes) -> str:
    """md5 of raw (pre-compression) protobuf bytes — data[0][4] in saveV3."""
    return hashlib.md5(raw_proto).hexdigest()
