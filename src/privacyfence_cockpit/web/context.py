"""Page-wide context: the banners that `base.html` shows above every page."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from privacyfence_cockpit.cockpit_yaml import selector_problems
from privacyfence_cockpit.recipes import DiscoveryError, Recipe, discover
from privacyfence_cockpit.services import Cockpit

log = logging.getLogger(__name__)

Level = Literal["info", "warning", "error"]

MAX_PROBLEMS_SHOWN = 3


@dataclass(frozen=True)
class Banner:
    level: Level
    text: str
    link: str | None = None


def banners(cockpit: Cockpit) -> list[Banner]:
    found: list[Banner] = []
    if cockpit.env.env_token is not None:
        text = "PRIVACYFENCE_TOKEN is set in the container environment: it overrides every stored token."
        found.append(Banner("warning", text))
    status = cockpit.git.status()
    if not status.configured:
        found.append(Banner("info", "No recipe repository yet.", "/code"))
    config_error = cockpit.config_or_default()[1]
    if config_error is not None:
        found.append(Banner("error", config_error, "/schedules"))
    discovered = None
    try:
        discovered = discover(cockpit.paths.repo)
    except DiscoveryError as exc:
        found.append(Banner("error", str(exc)))
    if config_error is None and discovered is not None:
        banner = _selector_banner(cockpit, discovered)
        if banner is not None:
            found.append(banner)
    if status.pinned_sha is not None:
        found.append(
            Banner(
                "warning",
                f"Schedules run the pinned commit {status.pinned_sha[:7]}, not the tip of {status.branch}.",
                "/code",
            )
        )
    venv_error = cockpit.db.get_setting("venv.last_error")
    if venv_error:
        found.append(Banner("error", f"The recipe environment could not be built: {venv_error}", "/code"))
    if cockpit.db.get_setting("git.failing_since"):
        text = f"Fetching the recipe repository fails: {status.last_fetch_error or 'unknown error'}"
        found.append(Banner("warning", text, "/code"))
    if status.new_commits > 0 and status.pinned_sha is None:
        found.append(Banner("info", f"{status.new_commits} new commit(s) on {status.branch}.", "/code"))
    return found


def _selector_banner(cockpit: Cockpit, recipes: Sequence[Recipe]) -> Banner | None:
    # Banners render on every page, so a failure here must never break one.
    try:
        problems = selector_problems(cockpit.config_or_default()[0], recipes)
    except Exception:
        log.exception("could not check the schedule selectors")
        return None
    if not problems:
        return None
    text = "; ".join(problems[:MAX_PROBLEMS_SHOWN])
    if len(problems) > MAX_PROBLEMS_SHOWN:
        text += f" (and {len(problems) - MAX_PROBLEMS_SHOWN} more)"
    return Banner("warning", f"Schedule selectors match no recipe: {text}", "/schedules")
