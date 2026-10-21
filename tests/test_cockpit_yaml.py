import pytest

from privacyfence_cockpit.cockpit_yaml import (
    CockpitConfig,
    CockpitConfigError,
    Schedule,
    load_cockpit_yaml,
    selector_problems,
)
from privacyfence_cockpit.recipes import discover

SAMPLE = """\
version: 1
server: https://pf.example.com/mcp
timezone: Europe/Budapest
timeout_minutes: 30
schedules:
  - name: smoke
    cron: "0 3 * * *"
    run: [imports]
  - name: nightly
    cron: "*/15 * * * *"
    run: [imports/10-fixture, transformations]
    repeat: 3
    stop_on_error: true
"""


def write(tmp_path, text):
    (tmp_path / "cockpit.yaml").write_text(text)
    return tmp_path


def schedule_yaml(**fields):
    base = {"name": "a", "cron": "0 3 * * *", "run": "[imports]"}
    base.update(fields)
    body = "\n".join(f"    {k}: {v}" for k, v in base.items())
    return "version: 1\nschedules:\n  -\n" + body + "\n"


def test_sample(tmp_path):
    config = load_cockpit_yaml(write(tmp_path, SAMPLE))
    assert config == CockpitConfig(
        server="https://pf.example.com/mcp",
        timezone="Europe/Budapest",
        timeout_minutes=30,
        schedules=(
            Schedule("smoke", "0 3 * * *", ("imports",)),
            Schedule("nightly", "*/15 * * * *", ("imports/10-fixture", "transformations"), 3, True),
        ),
    )


def test_minimal(tmp_path):
    assert load_cockpit_yaml(write(tmp_path, "version: 1\n")) == CockpitConfig()


def test_missing_file(tmp_path):
    assert load_cockpit_yaml(tmp_path) == CockpitConfig()


@pytest.mark.parametrize(
    "text",
    [
        "version: [",
        "- a\n- b\n",
        "",
        "version: 2\n",
        "version: true\n",
        "version: '1'\n",
        "schedules: []\n",
        "version: 1\nextra: 1\n",
        "version: 1\nserver: ftp://x\n",
        "version: 1\nserver: 5\n",
        "version: 1\ntimezone: Mars/Base\n",
        "version: 1\ntimezone: 5\n",
        "version: 1\ntimezone: ../etc\n",
        "version: 1\ntimeout_minutes: 0\n",
        "version: 1\ntimeout_minutes: 1441\n",
        "version: 1\ntimeout_minutes: true\n",
        "version: 1\ntimeout_minutes: '5'\n",
        "version: 1\nschedules: nope\n",
        "version: 1\nschedules: [1]\n",
        schedule_yaml(extra=1),
        schedule_yaml(name="Upper"),
        schedule_yaml(name="-x"),
        schedule_yaml(name="a" * 42),
        schedule_yaml(name="5"),
        schedule_yaml(cron="'not cron'"),
        schedule_yaml(cron="5"),
        schedule_yaml(run="[]"),
        schedule_yaml(run="imports"),
        schedule_yaml(run="[1]"),
        schedule_yaml(repeat=0),
        schedule_yaml(repeat=101),
        schedule_yaml(repeat="true"),
        schedule_yaml(stop_on_error="yes please"),
        "version: 1\nschedules:\n  - {name: a, cron: '0 3 * * *', run: [x]}\n"
        "  - {name: a, cron: '0 3 * * *', run: [x]}\n",
    ],
)
def test_invalid(tmp_path, text):
    with pytest.raises(CockpitConfigError) as exc:
        load_cockpit_yaml(write(tmp_path, text))
    assert str(exc.value).startswith("cockpit.yaml: ")


def test_invalid_utf8(tmp_path):
    (tmp_path / "cockpit.yaml").write_bytes(b"\xff\xfe\x00")
    with pytest.raises(CockpitConfigError):
        load_cockpit_yaml(tmp_path)


def test_boundary_values_accepted(tmp_path):
    text = "version: 1\ntimeout_minutes: 1440\n" + schedule_yaml(repeat=100).split("\n", 1)[1]
    config = load_cockpit_yaml(write(tmp_path, text))
    assert config.timeout_minutes == 1440
    assert config.schedules[0].repeat == 100


def test_selector_problems(tmp_path):
    (tmp_path / "workflows/10-imports/10-a").mkdir(parents=True)
    (tmp_path / "workflows/10-imports/10-a/recipe.yaml").write_text("version: 1\n")
    recipes = discover(tmp_path)
    config = CockpitConfig(
        schedules=(
            Schedule("ok", "0 3 * * *", ("imports", "imports/10-a")),
            Schedule("bad", "0 3 * * *", ("imports", "nope", "imports/99")),
        )
    )
    assert selector_problems(config, recipes) == [
        "schedule bad: no recipe or group matches 'nope'",
        "schedule bad: no recipe or group matches 'imports/99'",
    ]
    assert selector_problems(CockpitConfig(), recipes) == []
