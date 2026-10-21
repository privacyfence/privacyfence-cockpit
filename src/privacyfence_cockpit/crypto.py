"""The master key and AES-GCM encryption of stored secrets."""

from __future__ import annotations

import base64
import binascii
import os
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"PFC1"
NONCE_SIZE = 12
KEY_SIZE = 32


class MasterKeyError(ValueError):
    pass


class SecretDecryptError(ValueError):
    pass


def missing_key_message(path: Path) -> str:
    return (
        f"No master key at {path} (COCKPIT_MASTER_KEY_FILE). Create one with\n"
        "  openssl rand -base64 32 > cockpit_master_key\n"
        "and pass it to the container as the Docker secret cockpit_master_key. See the README, \"Master key\"."
    )


def load_master_key(path: Path) -> bytes:
    if not path.exists():
        raise MasterKeyError(missing_key_message(path))
    invalid = MasterKeyError(
        f"The master key in {path} must be 32 random bytes, base64-encoded (openssl rand -base64 32)."
    )
    try:
        text = path.read_text().strip()
        key = base64.b64decode(text, validate=True)
    except (OSError, UnicodeDecodeError, binascii.Error):
        raise invalid from None
    if len(key) != KEY_SIZE:
        raise invalid
    return key


def encrypt(key: bytes, plaintext: bytes, aad: str) -> bytes:
    nonce = os.urandom(NONCE_SIZE)
    return MAGIC + nonce + AESGCM(key).encrypt(nonce, plaintext, aad.encode())


def decrypt(key: bytes, blob: bytes, aad: str) -> bytes:
    if not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + NONCE_SIZE:
        raise SecretDecryptError("stored secret is not in the expected format")
    nonce = blob[len(MAGIC) : len(MAGIC) + NONCE_SIZE]
    try:
        return AESGCM(key).decrypt(nonce, blob[len(MAGIC) + NONCE_SIZE :], aad.encode())
    except InvalidTag:
        raise SecretDecryptError("stored secret could not be decrypted") from None
