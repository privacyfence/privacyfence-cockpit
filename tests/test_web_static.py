import hashlib
import re
from pathlib import Path

import jinja2
import pytest

from privacyfence_cockpit.models import OUTCOME_LABELS, Outcome

WEB = Path(__file__).parent.parent / "src" / "privacyfence_cockpit" / "web"
STATIC = WEB / "static"
TEMPLATES = WEB / "templates"

HTMX_SHA256 = "e209dda5c8235479f3166defc7750e1dbcd5a5c1808b7792fc2e6733768fb447"

# §W0 and §W2: every class the pages use
CSS_CLASSES = [
    "banner",
    "banner-info",
    "banner-warning",
    "banner-error",
    "flash",
    "table.data",
    "actions",
    "form.inline",
    "muted",
    "mono",
    "pre.log",
    "pre.diff",
    "add",
    "del",
    "danger",
    "card",
    "grid2",
    "textarea.key",
    "chip",
    *(f"chip-{outcome.value}" for outcome in Outcome),
]


def environment() -> jinja2.Environment:
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES), autoescape=True)
    env.filters["outcome_label"] = lambda value: OUTCOME_LABELS[Outcome(value)]
    return env


def context(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "cockpit": None,
        "banners": [],
        "flash": None,
        "csrf_token": "tok",
        "nav": "",
        "version": "1.2.3",
        "authenticated": True,
    }
    base.update(overrides)
    return base


def render_base(**overrides: object) -> str:
    template = environment().from_string('{% extends "base.html" %}{% block content %}BODY{% endblock %}')
    return template.render(context(**overrides))


def test_htmx_is_the_verified_file() -> None:
    data = (STATIC / "htmx.min.js").read_bytes()
    assert len(data) == 50917
    assert hashlib.sha256(data).hexdigest() == HTMX_SHA256


def test_templates_have_no_inline_script_or_style() -> None:
    files = sorted(TEMPLATES.glob("*.html"))
    assert files
    for path in files:
        text = path.read_text()
        assert not re.search(r"<script(?![^>]*\bsrc=)", text, re.IGNORECASE), path.name
        assert not re.search(r"<style\b", text, re.IGNORECASE), path.name
        assert not re.search(r"\sstyle\s*=", text, re.IGNORECASE), path.name


@pytest.mark.parametrize("name", CSS_CLASSES)
def test_css_defines_class(name: str) -> None:
    css = (STATIC / "cockpit.css").read_text()
    tag, _, cls = name.rpartition(".") if "." in name else ("", "", name)
    assert re.search(rf"{re.escape(tag)}\.{re.escape(cls)}(?![\w-])", css), name


def test_css_has_dark_palette() -> None:
    assert "@media (prefers-color-scheme: dark)" in (STATIC / "cockpit.css").read_text()


def test_js_has_no_innerhtml() -> None:
    assert "innerHTML" not in (STATIC / "cockpit.js").read_text()


def test_js_streams_log_and_links_approvals() -> None:
    js = (STATIC / "cockpit.js").read_text()
    for needle in ("EventSource", "createElement", "noopener noreferrer", "run-outcome", "waiting for approval"):
        assert needle in js


def test_unauthenticated_page_has_no_nav_or_banners() -> None:
    html = render_base(
        authenticated=False,
        banners=[{"level": "info", "text": "hidden banner", "link": None}],
    )
    assert "BODY" in html
    assert "<nav" not in html
    assert "hidden banner" not in html
    assert "Log out" not in html


def test_authenticated_page_has_nav_banners_and_flash() -> None:
    html = render_base(
        nav="history",
        flash="Saved.",
        banners=[
            {"level": "warning", "text": "careful", "link": "/code"},
            {"level": "info", "text": "fyi", "link": None},
        ],
    )
    for href, label in [
        ("/", "Overview"),
        ("/history", "History"),
        ("/schedules", "Schedules"),
        ("/code", "Code"),
        ("/tokens", "Tokens"),
        ("/permissions", "Permissions"),
        ("/notifications", "Notifications"),
        ("/settings", "Settings"),
    ]:
        assert f'<a href="{href}"' in html
        assert f">{label}</a>" in html
    assert re.search(r'<a href="/history" class="current"', html)
    assert not re.search(r'<a href="/" class="current"', html)
    assert 'action="/logout"' in html
    assert 'class="banner banner-warning"' in html
    assert 'class="banner banner-info"' in html
    assert '<a href="/code">Open</a>' in html
    assert "Saved." in html
    assert "1.2.3" in html


def test_overview_is_current_on_empty_nav() -> None:
    assert re.search(r'<a href="/" class="current"', render_base(nav=""))


def test_shell_wiring() -> None:
    html = render_base(csrf_token='a"b<c')
    assert """<meta name="htmx-config" content='{"includeIndicatorStyles": false}'>""" in html
    assert '<script src="/static/htmx.min.js"' in html
    assert '<script src="/static/cockpit.js"' in html
    assert 'href="/static/cockpit.css"' in html
    body = re.search(r"<body hx-headers='([^']*)'", html)
    assert body
    assert "X-CSRF-Token" in body.group(1)
    assert "<c" not in body.group(1)


def test_title_block_is_overridable() -> None:
    template = environment().from_string('{% extends "base.html" %}{% block title %}History{% endblock %}')
    assert "<title>History</title>" in template.render(context())


def test_flash_and_banner_text_are_escaped() -> None:
    html = render_base(flash="<b>x</b>", banners=[{"level": "error", "text": "<i>y</i>", "link": None}])
    assert "<b>x</b>" not in html
    assert "<i>y</i>" not in html
    assert "&lt;b&gt;x&lt;/b&gt;" in html


def test_macros() -> None:
    env = environment()
    out = env.from_string(
        '{% from "_macros.html" import chip, post_button, field_error %}'
        '{{ chip("no_change") }}|{{ chip(none) }}|'
        '{{ post_button("/x", "Go", "tok", {"recipe": "a/b"}, "danger") }}|'
        '{{ post_button("/y", "Plain", "tok") }}|{{ field_error("bad") }}|{{ field_error(none) }}'
    ).render()
    chip_part, empty_chip, button, plain, error, empty_error = out.split("|")
    assert chip_part == '<span class="chip chip-no_change">no change</span>'
    assert empty_chip == ""
    assert 'action="/x"' in button
    assert 'name="csrf_token" value="tok"' in button
    assert 'name="recipe" value="a/b"' in button
    assert 'class="danger"' in button
    assert "class=" not in plain.split("<button")[1]
    assert '<p class="field-error"' in error
    assert "bad" in error
    assert empty_error == ""
