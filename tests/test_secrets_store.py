import shutil

import pytest

from privacyfence_cockpit.crypto import MasterKeyError, SecretDecryptError
from privacyfence_cockpit.secrets_store import SecretStore

KEY = bytes(range(32))


@pytest.fixture
def store(tmp_path):
    return SecretStore(tmp_path / "secrets", KEY)


def test_set_get_has_delete(store):
    assert store.get("token", "a") is None
    assert not store.has("token", "a")
    store.set("token", "a", "pfst_value")
    assert store.get("token", "a") == "pfst_value"
    assert store.has("token", "a")
    store.set("token", "a", "pfst_other")
    assert store.get("token", "a") == "pfst_other"
    assert store.delete("token", "a") is True
    assert store.delete("token", "a") is False
    assert store.get("token", "a") is None


def test_names_sorted_and_per_kind(store):
    assert store.names("token") == []
    store.set("token", "imports/b", "1")
    store.set("token", "default", "2")
    store.set("git", "pat", "3")
    assert store.names("token") == ["default", "imports/b"]
    assert store.names("git") == ["pat"]
    assert store.names("webhook") == []


def test_files_are_private_and_hold_no_plaintext(store, tmp_path):
    store.set("token", "imports/a", "pfst_plaintext_value")
    files = list((tmp_path / "secrets" / "token").iterdir())
    assert len(files) == 1
    assert files[0].name.endswith(".bin")
    assert "=" not in files[0].name and "/" not in files[0].name
    assert files[0].stat().st_mode & 0o777 == 0o600
    assert b"pfst_plaintext_value" not in files[0].read_bytes()
    assert not list(files[0].parent.glob("*.tmp"))


def test_file_copied_to_another_name_fails(store, tmp_path):
    store.set("token", "a", "pfst_value")
    store.set("token", "b", "pfst_value")
    folder = tmp_path / "secrets" / "token"
    by_size = sorted(folder.iterdir())
    shutil.copy(by_size[0], by_size[1])
    with pytest.raises(SecretDecryptError):
        store.get("token", "b")


def test_unknown_kind_rejected(store):
    with pytest.raises(ValueError, match="unknown secret kind"):
        store.set("../x", "a", "b")


def test_canary_created_then_verified(store, tmp_path):
    store.verify_canary()
    canary = tmp_path / "secrets" / "canary.bin"
    assert canary.stat().st_mode & 0o777 == 0o600
    first = canary.read_bytes()
    store.verify_canary()
    assert canary.read_bytes() == first


def test_canary_with_another_key_raises(store, tmp_path):
    store.verify_canary()
    root = tmp_path / "secrets"
    with pytest.raises(MasterKeyError) as info:
        SecretStore(root, bytes(32)).verify_canary()
    assert str(info.value) == (
        f"The master key does not match the one that encrypted {root}. "
        f"Use the original key file, or move {root} away to start without the stored secrets."
    )


def test_canary_with_wrong_plaintext_raises(tmp_path):
    from privacyfence_cockpit.crypto import encrypt

    root = tmp_path / "secrets"
    root.mkdir()
    (root / "canary.bin").write_bytes(encrypt(KEY, b"something else", "canary"))
    with pytest.raises(MasterKeyError):
        SecretStore(root, KEY).verify_canary()


def test_repr_has_no_key(store):
    assert "secrets" in repr(store)
    assert str(KEY) not in repr(store)
