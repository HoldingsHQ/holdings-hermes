"""Offline storage proof. The caller supplies the separately protected key."""
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

HEADER = b"HOLDINGS\x01"
LIMIT = 4 * 1024 * 1024
NONCE_SIZE = 12
TAG_SIZE = 16


class VaultLocked(ValueError):
    """The key or scope is unavailable, or the envelope cannot be authenticated."""


def _context(key, scope):
    if not isinstance(key, bytes) or len(key) != 32 or not isinstance(scope, bytes) or not 1 <= len(scope) <= 256:
        raise VaultLocked("Holdings vault is locked")
    return AESGCM(key), HEADER + scope


def seal(data, *, key, scope):
    """Create an authenticated envelope before any persistence."""
    cipher, aad = _context(key, scope)
    if not isinstance(data, bytes) or len(data) > LIMIT:
        raise ValueError("Holdings vault snapshot exceeds the allowed size")
    nonce = os.urandom(NONCE_SIZE)
    return HEADER + nonce + cipher.encrypt(nonce, data, aad)


def store(path, data, *, key, scope):
    """Encrypt before the first disk write, then atomically replace one snapshot."""
    envelope = seal(data, key=key, scope=scope)
    path = Path(path)
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, prefix=".holdings-", delete=False) as output:
            temporary = Path(output.name)
            output.write(envelope)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def unseal(envelope, *, key, scope):
    cipher, aad = _context(key, scope)
    start = len(HEADER)
    end = start + NONCE_SIZE
    if not envelope.startswith(HEADER) or not end + TAG_SIZE <= len(envelope) <= end + LIMIT + TAG_SIZE:
        raise VaultLocked("Holdings vault could not be authenticated")
    try:
        return cipher.decrypt(envelope[start:end], envelope[end:], aad)
    except InvalidTag:
        raise VaultLocked("Holdings vault could not be authenticated") from None


def load(path, *, key, scope):
    with Path(path).open("rb") as source:
        envelope = source.read(len(HEADER) + NONCE_SIZE + LIMIT + TAG_SIZE + 1)
    return unseal(envelope, key=key, scope=scope)


def rotate(path, *, old_key, new_key, scope):
    """Rotate one offline snapshot. The owner must serialize writes and rotate backups."""
    if old_key == new_key:
        raise ValueError("The replacement key must be different")
    _context(new_key, scope)
    store(path, load(path, key=old_key, scope=scope), key=new_key, scope=scope)
