"""Recipe discovery and selection over the ``workflows/`` tree of the recipe repository."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, order=True)
class Recipe:
    group_folder: str
    folder: str
    group: str
    path: str
    dir: Path

    @property
    def yaml_path(self) -> Path:
        return self.dir / "recipe.yaml"


class DiscoveryError(ValueError):
    pass


class SelectionError(ValueError):
    pass


def _visible_dirs(parent: Path) -> list[Path]:
    return sorted(
        (p for p in parent.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))),
        key=lambda p: p.name,
    )


def discover(repo: Path) -> list[Recipe]:
    workflows = repo / "workflows"
    if not workflows.is_dir():
        return []
    recipes: list[Recipe] = []
    seen: dict[str, str] = {}
    for group_dir in _visible_dirs(workflows):
        group = re.sub(r"^\d+-", "", group_dir.name)
        if group in seen:
            raise DiscoveryError(
                f"workflows/{seen[group]} and workflows/{group_dir.name} "
                f"both have the group name {group}"
            )
        seen[group] = group_dir.name
        for recipe_dir in _visible_dirs(group_dir):
            if (recipe_dir / "recipe.yaml").is_file():
                recipes.append(
                    Recipe(
                        group_folder=group_dir.name,
                        folder=recipe_dir.name,
                        group=group,
                        path=f"{group}/{recipe_dir.name}",
                        dir=recipe_dir.resolve(),
                    )
                )
    return sorted(recipes)


def group_names(recipes: Sequence[Recipe]) -> list[str]:
    names: list[str] = []
    for recipe in recipes:
        if recipe.group not in names:
            names.append(recipe.group)
    return names


def _normalise(selector: str) -> str:
    text = selector.removeprefix("./").removeprefix("workflows/")
    text = text.removesuffix("/recipe.yaml")
    return text.rstrip("/")


def _matches(recipe: Recipe, text: str) -> bool:
    if "/" in text:
        group, _, folder = text.partition("/")
        return recipe.folder == folder and group in (recipe.group, recipe.group_folder)
    return text in (recipe.group, recipe.group_folder)


def select(recipes: Sequence[Recipe], selectors: Sequence[str]) -> list[Recipe]:
    if not selectors:
        return sorted(recipes)
    chosen: set[Recipe] = set()
    for selector in selectors:
        text = _normalise(selector)
        found = [r for r in recipes if _matches(r, text)]
        if not found:
            raise SelectionError(f"no recipe or group matches {selector!r}")
        chosen.update(found)
    return sorted(chosen)
