"""security/secrets_fallback.py — AES-256-GCM secret storage with OS-keychain
attempt + passphrase-encrypted file fallback.

§10, §25 Phase 8 ⑟. On environments without a usable OS keychain (most headless
Linux servers and containers), Universe automatically falls back to a
passphrase-encrypted local file. The fallback path is exercised by a test, not
just present as unreachable code (§STEP 5).

The encryption key is derived from a passphrase via scrypt. The passphrase
itself is stored... nowhere persistent by default: the user is asked once per
session, OR it's set via UNIVERSE_PASSPHRASE env var (for servers/CI). When no
passphrase is available, secrets are stored with a derived key from a machine
local secret (less secure, but still encrypted at rest — and the docs are
explicit about this degraded mode).
"""
from __future__ import annotations

import base64
import hashlib
import os
import threading
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


_lock = threading.Lock()
_passphrase: str | None = None
_keyring_secret: bytes | None = None
# derived-key cache: hashlib.scrypt(n=2**14) costs ~1.2s PER CALL on this
# hardware. decrypt_secret ran it on every LLM completion — measured 1.17s
# added to every provider round-trip (the "first message is very slow"
# report: classify + reply decrypt back-to-back). (passphrase, salt) -> key
# is a pure function, so a bounded cache is exactly equivalent.
_derived_keys: "dict[tuple[str, bytes], bytes]" = {}
_MAX_DERIVED_KEYS = 64


class DecryptionError(Exception):
    """Raised when a stored secret cannot be decrypted with any known key.

    Returning the ciphertext itself (the old behavior) silently smuggled an
    undecryptable blob into provider requests as if it were the API key."""


def _machine_fallback_passphrase() -> str:
    """The single, canonical machine-local fallback passphrase.

    Derivation used to be duplicated with a subtle divergence (nodename
    present in one copy, empty string in the other), which made decryption
    fail depending on which code path ran. One function, one value."""
    if hasattr(os, "uname"):
        machine = f"{Path.home()}|{os.uname().nodename}"
    else:  # Windows: no os.uname; home dir is the stable machine anchor
        machine = f"{Path.home()}|win32"
    return hashlib.sha256(machine.encode("utf-8")).hexdigest()


def _get_passphrase() -> str:
    """Resolve the passphrase: env var > cached > keychain > machine-local."""
    global _passphrase
    if _passphrase is not None:
        return _passphrase
    env = os.environ.get("UNIVERSE_PASSPHRASE")
    if env:
        _passphrase = env
        return env
    # try the OS keychain
    try:
        import keyring
        sec = keyring.get_password("universe-agent", "master-passphrase")
        if sec:
            _passphrase = sec
            return sec
    except Exception:
        pass
    # PROVISIONING (audit 2026-09): set_passphrase() existed but no
    # production code ever called it, so the keyring entry was never stored
    # and every install silently encrypted under the machine-derivable
    # fallback (sha256(home|win32) — guessable by any local process).
    # On first use with a working keychain, generate a random master
    # passphrase and store it. Round-trip verified before trusting it: a
    # backend that accepts writes but fails reads would brick every later
    # decrypt. Old secrets stay readable: decrypt_secret retries with the
    # machine fallback when the keyring passphrase fails.
    try:
        if keyring_available():
            import keyring as _kr
            import secrets as _secrets
            generated = _secrets.token_urlsafe(32)
            _kr.set_password("universe-agent", "master-passphrase", generated)
            if _kr.get_password("universe-agent", "master-passphrase") == generated:
                _passphrase = generated
                return generated
    except Exception:
        pass
    # machine-local fallback: derive from a stable per-machine value
    # (this is the degraded mode — encrypted at rest, but a local attacker
    # with the machine could derive it; documented in PRIVACY.md)
    _passphrase = _machine_fallback_passphrase()
    return _passphrase


def set_passphrase(p: str) -> None:
    global _passphrase
    _passphrase = p
    try:
        import keyring
        keyring.set_password("universe-agent", "master-passphrase", p)
    except Exception:
        pass


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    """scrypt key derivation, memoized per (passphrase, salt). The cache is
    bounded: a machine holds a handful of distinct salts (one per stored
    secret), never thousands. See _derived_keys for why this matters."""
    ck = (passphrase, salt)
    with _lock:
        cached = _derived_keys.get(ck)
        if cached is not None:
            return cached
    key = hashlib.scrypt(passphrase.encode("utf-8"), salt=salt, n=2**14,
                         r=8, p=1, dklen=32)
    with _lock:
        if len(_derived_keys) >= _MAX_DERIVED_KEYS:
            _derived_keys.clear()
        _derived_keys[ck] = key
    return key


def encrypt_secret(plaintext: str) -> str:
    """AES-256-GCM encrypt. Returns base64(salt||nonce||ciphertext)."""
    if not plaintext:
        return ""
    with _lock:
        passphrase = _get_passphrase()
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = _derive_key(passphrase, salt)
    aes = AESGCM(key)
    ct = aes.encrypt(nonce, plaintext.encode("utf-8"), None)
    blob = salt + nonce + ct
    return base64.b64encode(blob).decode("ascii")


def decrypt_secret(blob: str) -> str:
    """Decrypt a stored secret.

    Raises DecryptionError when the blob looks encrypted but no known
    passphrase can decrypt it — the caller must surface a setup error,
    NEVER send the ciphertext anywhere as if it were the secret. Plain
    non-base64 / short legacy plaintext values still pass through for
    migration compatibility."""
    if not blob:
        return ""
    try:
        raw = base64.b64decode(blob)
    except Exception:
        return blob  # legacy plaintext (pre-encryption storage / tests)
    if len(raw) < 28:
        return blob  # not our blob format — legacy plaintext
    salt, nonce, ct = raw[:16], raw[16:28], raw[28:]
    with _lock:
        passphrase = _get_passphrase()
    key = _derive_key(passphrase, salt)
    aes = AESGCM(key)
    try:
        pt = aes.decrypt(nonce, ct, None)
        return pt.decode("utf-8")
    except Exception:
        # maybe the passphrase changed — try the machine fallback
        fb = _machine_fallback_passphrase()
        if fb != passphrase:
            key2 = _derive_key(fb, salt)
            try:
                return AESGCM(key2).decrypt(nonce, ct, None).decode("utf-8")
            except Exception:
                pass
    raise DecryptionError(
        "stored secret could not be decrypted (passphrase changed or keychain "
        "unavailable) — re-enter the key via /setup or the provider settings")


def keyring_available() -> bool:
    """Test whether the OS keychain is actually usable (not just importable)."""
    try:
        import keyring
        # probe: try to get a throwaway key
        keyring.get_password("universe-probe", "probe")
        return True
    except Exception:
        return False

