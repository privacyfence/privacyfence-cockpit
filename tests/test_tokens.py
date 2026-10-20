from pathlib import Path

import pytest

from privacyfence_cockpit.recipes import Recipe
from privacyfence_cockpit.secrets_store import SecretStore
from privacyfence_cockpit.tokens import (
    DEFAULT_TOKEN,
    TOKEN_PREFIX,
    ResolvedToken,
    TokenNameError,
    TokenResolver,
    TokenValueError,
    check_token_name,
    clean_token_value,
    valid_token_names,
)

GOOD = "pfst_" + "a" * 20


def recipe(group_folder, folder):
    group = group_folder.split("-", 1)[1]
    return Recipe(group_folder, folder, group, f"{group}/{folder}", Path("/x") / group_folder / folder)


RECIPES = [recipe("10-imports", "10-a"), recipe("10-imports", "20-b"), recipe("20-exports", "10-c")]


@pytest.fixture
def store(tmp_path):
    return SecretStore(tmp_path / "secrets", bytes(32))


def test_constants():
    assert DEFAULT_TOKEN == "default"
    assert TOKEN_PREFIX == "pfst_"


def test_valid_names_order():
    assert valid_token_names(RECIPES) == [
        "imports/10-a",
        "imports/20-b",
        "exports/10-c",
        "imports",
        "exports",
        "default",
    ]
    assert valid_token_names([]) == ["default"]


def test_known_names_pass():
    for name in valid_token_names(RECIPES):
        check_token_name(name, RECIPES)


def test_suggestion_text():
    with pytest.raises(TokenNameError) as info:
        check_token_name("imprts", RECIPES)
    assert str(info.value) == (
        "unknown token name 'imprts'; did you mean 'imports'? "
        "Token names are recipe paths, group names or default."
    )


def test_no_suggestion_clause():
    with pytest.raises(TokenNameError) as info:
        check_token_name("zzz", RECIPES)
    assert "did you mean" not in str(info.value)
    assert str(info.value).startswith("unknown token name 'zzz'")
    assert str(info.value).endswith("Token names are recipe paths, group names or default.")


def test_clean_token_value():
    assert clean_token_value(f"  {GOOD}\n") == GOOD
    for bad in ["", "abc" * 10, "pfst_short", "xpfst_" + "a" * 30]:
        with pytest.raises(TokenValueError, match=r"Only PrivacyFence scoped tokens \(pfst_…\) are supported\."):
            clean_token_value(bad)


def test_resolved_token_repr_hides_value():
    assert "secret" not in repr(ResolvedToken("secret", "env"))


def test_resolve_none(store):
    assert TokenResolver(store, None).resolve(RECIPES[0]) == ResolvedToken(None, "none")


def test_resolve_chain(store):
    resolver = TokenResolver(store, None)
    store.set("token", "default", "d")
    assert resolver.resolve(RECIPES[0]) == ResolvedToken("d", "default")
    store.set("token", "imports", "g")
    assert resolver.resolve(RECIPES[0]) == ResolvedToken("g", "group:imports")
    assert resolver.resolve(RECIPES[2]) == ResolvedToken("d", "default")
    store.set("token", "imports/10-a", "r")
    assert resolver.resolve(RECIPES[0]) == ResolvedToken("r", "recipe:imports/10-a")
    assert resolver.resolve(RECIPES[1]) == ResolvedToken("g", "group:imports")


def test_env_wins(store):
    store.set("token", "imports/10-a", "r")
    assert TokenResolver(store, "pfst_env").resolve(RECIPES[0]) == ResolvedToken("pfst_env", "env")


def test_resolve_name(store):
    resolver = TokenResolver(store, "pfst_env")
    assert resolver.resolve_name("imports") is None
    store.set("token", "imports", "g")
    assert resolver.resolve_name("imports") == "g"
