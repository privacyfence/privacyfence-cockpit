import base64
import os

import pytest

from privacyfence_cockpit.crypto import (
    MasterKeyError,
    SecretDecryptError,
    decrypt,
    encrypt,
    load_master_key,
    missing_key_message,
)

KEY = bytes(range(32))


def write_key(path, raw: bytes):
    path.write_text(base64.b64encode(raw).decode() + "\n")
    return path


def test_round_trip_and_format():
    blob = encrypt(KEY, b"secret", "token:a")
    assert blob.startswith(b"PFC1")
    assert b"secret" not in blob
    assert decrypt(KEY, blob, "token:a") == b"secret"


def test_nonce_is_fresh():
    assert encrypt(KEY, b"x", "a") != encrypt(KEY, b"x", "a")


def test_wrong_aad_raises():
    blob = encrypt(KEY, b"secret", "token:a")
    with pytest.raises(SecretDecryptError):
        decrypt(KEY, blob, "token:b")


def test_wrong_key_raises():
    blob = encrypt(KEY, b"secret", "a")
    with pytest.raises(SecretDecryptError):
        decrypt(bytes(32), blob, "a")


def test_tampered_blob_raises():
    blob = bytearray(encrypt(KEY, b"secret", "a"))
    blob[-1] ^= 1
    with pytest.raises(SecretDecryptError):
        decrypt(KEY, bytes(blob), "a")


@pytest.mark.parametrize("blob", [b"", b"PFC1", b"XXXX" + bytes(40)])
def test_bad_magic_or_short_blob_raises(blob):
    with pytest.raises(SecretDecryptError):
        decrypt(KEY, blob, "a")


def test_load_master_key(tmp_path):
    assert load_master_key(write_key(tmp_path / "k", KEY)) == KEY


def test_missing_key_message_is_verbatim(tmp_path):
    path = tmp_path / "nope"
    with pytest.raises(MasterKeyError) as info:
        load_master_key(path)
    assert str(info.value) == (
        f"No master key at {path} (COCKPIT_MASTER_KEY_FILE). Create one with\n"
        "  openssl rand -base64 32 > cockpit_master_key\n"
        "and pass it to the container as the Docker secret cockpit_master_key. See the README, \"Master key\".\n"
    ).rstrip("\n")
    assert str(info.value) == missing_key_message(path)


@pytest.mark.parametrize("content", ["not base64 !!", base64.b64encode(b"short").decode(), ""])
def test_bad_key_messages_do_not_leak_the_key(tmp_path, content):
    path = tmp_path / "k"
    path.write_text(content)
    with pytest.raises(MasterKeyError) as info:
        load_master_key(path)
    assert str(info.value) == (
        f"The master key in {path} must be 32 random bytes, base64-encoded (openssl rand -base64 32)."
    )
    assert content.strip() == "" or content not in str(info.value)


def test_unreadable_key_file(tmp_path):
    path = tmp_path / "k"
    path.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(MasterKeyError):
        load_master_key(path)


def test_directory_as_key_file(tmp_path):
    with pytest.raises(MasterKeyError):
        load_master_key(tmp_path)


def test_urandom_key_round_trips_through_file(tmp_path):
    raw = os.urandom(32)
    assert load_master_key(write_key(tmp_path / "k", raw)) == raw
