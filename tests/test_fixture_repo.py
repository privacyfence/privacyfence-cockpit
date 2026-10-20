import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from privacyfence_cockpit.cockpit_yaml import load_cockpit_yaml, selector_problems
from privacyfence_cockpit.recipes import discover
from privacyfence_cockpit.venvs import pin_problem

FIXTURE = Path(__file__).parent / "fixtures" / "recipe-repo"


def _fake() -> ModuleType:
    path = FIXTURE / "lib" / "privacyfence-sync-fake" / "privacyfence_sync_fake.py"
    spec = importlib.util.spec_from_file_location("privacyfence_sync_fake", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_discover_finds_the_fixture_recipe() -> None:
    assert [r.path for r in discover(FIXTURE)] == ["imports/10-fixture"]


def test_cockpit_yaml_is_valid() -> None:
    config = load_cockpit_yaml(FIXTURE)
    assert [s.name for s in config.schedules] == ["smoke"]
    assert selector_problems(config, discover(FIXTURE)) == []


def test_requirements_pass_the_pin_gate() -> None:
    assert pin_problem(FIXTURE) is None


def test_fake_handles_the_gate_commands(capsys: pytest.CaptureFixture[str]) -> None:
    main = _fake().main
    assert main(["--help"]) == 0
    assert main(["run", "--help"]) == 0
    assert "--result-file" in capsys.readouterr().out
    assert main(["permissions", "--help"]) == 0
    assert "--json" in capsys.readouterr().out
    assert main(["check", "recipe.yaml"]) == 0
    assert "fixture: 1 step(s), OK" in capsys.readouterr().out
    assert main([]) == 0
    assert main(["bogus"]) == 2


def test_fake_run_writes_the_result_file(tmp_path: Path) -> None:
    result = tmp_path / "result.json"
    assert _fake().main(["run", "recipe.yaml", "--result-file", str(result)]) == 0
    assert '"outcome": "ok"' in result.read_text()
    assert _fake().main(["run", "recipe.yaml"]) == 0
