import pytest

from privacyfence_cockpit.recipes import (
    DiscoveryError,
    SelectionError,
    discover,
    group_names,
    select,
)


def make(repo, *relative):
    for rel in relative:
        folder = repo / "workflows" / rel
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "recipe.yaml").write_text("version: 1\n")


@pytest.fixture
def repo(tmp_path):
    make(
        tmp_path,
        "20-transformations/10-join",
        "10-imports/20-b",
        "10-imports/10-a",
        "10-imports/_disabled",
        "10-imports/.hidden",
        "_shared/block",
        ".git/x",
    )
    (tmp_path / "workflows" / "10-imports" / "30-empty").mkdir()
    (tmp_path / "workflows" / "notes.txt").write_text("x")
    (tmp_path / "workflows" / "10-imports" / "readme.md").write_text("x")
    return tmp_path


def test_no_workflows_folder(tmp_path):
    assert discover(tmp_path) == []


def test_discover_order_and_fields(repo):
    recipes = discover(repo)
    assert [r.path for r in recipes] == [
        "imports/10-a",
        "imports/20-b",
        "transformations/10-join",
    ]
    first = recipes[0]
    assert first.group_folder == "10-imports"
    assert first.folder == "10-a"
    assert first.group == "imports"
    assert first.dir == (repo / "workflows/10-imports/10-a").resolve()
    assert first.yaml_path == first.dir / "recipe.yaml"


def test_group_without_number_prefix(tmp_path):
    make(tmp_path, "dash/x")
    (recipe,) = discover(tmp_path)
    assert recipe.group == "dash"
    assert recipe.group_folder == "dash"


def test_duplicate_group_short_names(tmp_path):
    make(tmp_path, "40-imports/a", "10-imports/a")
    with pytest.raises(DiscoveryError) as exc:
        discover(tmp_path)
    assert str(exc.value) == (
        "workflows/10-imports and workflows/40-imports both have the group name imports"
    )


def test_group_names_in_run_order(repo):
    assert group_names(discover(repo)) == ["imports", "transformations"]
    assert group_names([]) == []


@pytest.mark.parametrize(
    "selector",
    [
        "imports/10-a",
        "10-imports/10-a",
        "./imports/10-a",
        "workflows/imports/10-a",
        "./workflows/10-imports/10-a/",
        "workflows/imports/10-a/recipe.yaml",
    ],
)
def test_select_single_recipe_forms(repo, selector):
    assert [r.path for r in select(discover(repo), [selector])] == ["imports/10-a"]


@pytest.mark.parametrize("selector", ["imports", "10-imports", "workflows/imports/"])
def test_select_group(repo, selector):
    assert [r.path for r in select(discover(repo), [selector])] == ["imports/10-a", "imports/20-b"]


def test_select_empty_means_all(repo):
    recipes = discover(repo)
    assert select(recipes, []) == recipes


@pytest.mark.parametrize("selector", ["nope", "imports/99-x", "nope/10-a", "imports/10-a/extra", ""])
def test_select_unknown(repo, selector):
    with pytest.raises(SelectionError) as exc:
        select(discover(repo), [selector])
    assert str(exc.value) == f"no recipe or group matches {selector!r}"


def test_select_dedupes_and_keeps_run_order(repo):
    chosen = select(discover(repo), ["transformations", "imports/20-b", "imports", "imports/10-a"])
    assert [r.path for r in chosen] == ["imports/10-a", "imports/20-b", "transformations/10-join"]
