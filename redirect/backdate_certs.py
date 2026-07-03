"""
mitmproxy addon — widen certificate validity windows so TLS still works when the
DEVICE clock is set into the past (time-travel testing of timed-promo / shop UI).

Why this exists
---------------
The game reads the *device's local clock* for some timed-promo / shop checks, not
just the server-supplied UtcTimeStamp. To test an old event (e.g. BL23, Mar 2023)
we set the device clock back. But mitmproxy normally issues:

    CA cert   : notBefore = now-2d,  valid 10 years
    leaf cert : notBefore = now-2d,  valid 199 days   (certs.py CERT_* constants)

so with the device at 2023 every cert mitmproxy presents looks "not yet valid"
(its notBefore is ~2026) and the TLS handshake fails -> "Could not connect".

This addon patches the mitmproxy.certs constants at import time, BEFORE the CA and
any leaf certs are generated, to a very wide window (~2016 .. ~2036). That window
covers both a back-dated device clock AND the real host clock, so TLS works either
way.

IMPORTANT — you must regenerate the CA after enabling this:
    1. Stop mitmproxy.
    2. Delete or back up the repo-local mitmproxy/ directory so mitmproxy recreates it with the wide window.
    3. Start mitmproxy WITH this addon loaded (see run command below) — it writes a
       fresh mitmproxy/mitmproxy-ca-cert.pem with notBefore ~2016.
    4. Re-push & RE-INSTALL that new CA on the device (the old one won't validate
       at 2023 and is a different cert anyway).

Run:
    mitmdump -s redirect/backdate_certs.py -s redirect/mitm_addon.py

The widening only affects certs mitmproxy mints locally for interception; it has no
effect on real upstream servers (you run in offline mode anyway).
"""
from __future__ import annotations

import datetime

from mitmproxy import certs as _certs

# notBefore = ~10 years before the host's real clock. With the host at 2026 this is
# ~2016, comfortably before any event we'd time-travel to.
_BACKDATE = datetime.timedelta(days=-3650)
# Make both CA and leaf certs valid for ~20 years from that notBefore (-> ~2036),
# so the window also covers the real host clock and stays valid for a long time.
_WIDE_EXPIRY = datetime.timedelta(days=20 * 365)

_certs.CERT_VALIDITY_OFFSET = _BACKDATE      # used by dummy_ca() AND dummy_cert()
_certs.CA_EXPIRY = _WIDE_EXPIRY              # CA notAfter
_certs.CERT_EXPIRY = _WIDE_EXPIRY           # leaf notAfter

print(
    f"[backdate_certs] cert window widened: notBefore = now{_BACKDATE.days}d, "
    f"valid {_WIDE_EXPIRY.days}d  (regenerate repo-local mitmproxy CA + reinstall CA!)"
)
