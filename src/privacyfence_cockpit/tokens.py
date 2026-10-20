"""Token names, token value checks and the lookup chain."""

from __future__ import annotations

import difflib
from collections.abc import Sequence
from dataclasses import dataclass, field

from privacyfence_cockpit.recipes import Recipe, group_names
from privacyfence_cockpit.secrets_store import SecretStore

DEFAULT_TOKEN = "default"  # noqa: S105  # nosec B105  # the name of the fallback token, not a secret
TOKEN_PREFIX = "pfst_"  # noqa: S105  # nosec B105  # the public prefix of PrivacyFence scoped tokens, not a secret
MIN_TOKEN_LENGTH = 20


class TokenNameError(ValueError):
    pass


class TokenValueError(ValueError):
    pass


def valid_token_names(recipes: Sequence[Recipe]) -> list[str]:
    return [*(r.path for r in recipes), *group_names(recipes), DEFAULT_TOKEN]


def check_token_name(name: str, recipes: Sequence[Recipe]) -> None:
    valid = valid_token_names(recipes)
    if name in valid:
        return
    matches = difflib.get_close_matches(name, valid, n=1, cutoff=0.6)
    hint = f"; did you mean {matches[0]!r}?" if matches else "."
    raise TokenNameError(
        f"unknown token name {name!r}{hint} Token names are recipe paths, group names or default."
    )


def clean_token_value(value: str) -> str:
    cleaned = value.strip()
    if not cleaned.startswith(TOKEN_PREFIX) or len(cleaned) < MIN_TOKEN_LENGTH:
        raise TokenValueError("Only PrivacyFence scoped tokens (pfst_…) are supported.")
    return cleaned


@dataclass(frozen=True)
class ResolvedToken:
    value: str | None = field(repr=False)
    source: str


class TokenResolver:
    def __init__(self, store: SecretStore, env_token: str | None) -> None:
        self._store = store
        self._env_token = env_token

    def resolve(self, recipe: Recipe) -> ResolvedToken:
        if self._env_token:
            return ResolvedToken(self._env_token, "env")
        for name, source in (
            (recipe.path, f"recipe:{recipe.path}"),
            (recipe.group, f"group:{recipe.group}"),
            (DEFAULT_TOKEN, "default"),
        ):
            value = self._store.get("token", name)
            if value is not None:
                return ResolvedToken(value, source)
        return ResolvedToken(None, "none")

    def resolve_name(self, name: str) -> str | None:
        return self._store.get("token", name)
