import pytest

from privacyfence_cockpit.checks import cockpit_problems
from privacyfence_cockpit.recipes import discover
from tests.gitutil import git


@pytest.fixture
def repo(tmp_path):
    folder = tmp_path / "repo"
    (folder / "workflows" / "10-g" / "10-r").mkdir(parents=True)
    git(folder, "init", "-b", "main")
    return folder


def make(repo, text, ignore=None):
    (repo / "workflows" / "10-g" / "10-r" / "recipe.yaml").write_text(text)
    if ignore is not None:
        (repo / ".gitignore").write_text(ignore)
    return discover(repo)[0]


def test_ignored_work_dir_that_does_not_exist_yet(repo):
    assert cockpit_problems(make(repo, "name: x\n", "work/\n"), repo) == []


def test_ignored_explicit_work_dir(repo):
    recipe = make(repo, "work_dir: data\n", "data/\n")
    assert cockpit_problems(recipe, repo) == []


def test_not_ignored_default_work_dir(repo):
    problems = cockpit_problems(make(repo, "name: x\n"), repo)
    assert problems == [
        "work_dir workflows/10-g/10-r/work is not git-ignored; add it to .gitignore so source data never reaches git"
    ]


def test_work_dir_outside_the_folder(repo):
    problems = cockpit_problems(make(repo, "work_dir: ../../elsewhere\n"), repo)
    assert problems == ["work_dir ../../elsewhere is outside the recipe folder"]


def test_not_a_mapping(repo):
    assert cockpit_problems(make(repo, "- a\n- b\n"), repo) == ["recipe.yaml is not a YAML mapping"]


def test_unparseable_yaml(repo):
    assert cockpit_problems(make(repo, "a: [\n"), repo) == ["recipe.yaml is not a YAML mapping"]


def test_not_a_git_repo(tmp_path):
    folder = tmp_path / "plain"
    (folder / "workflows" / "10-g" / "10-r").mkdir(parents=True)
    recipe = make(folder, "name: x\n")
    assert cockpit_problems(recipe, folder) == ["the recipe repository is not a git checkout"]
