import yaml

from privacyfence_cockpit.replay import REPLAY_ALLOWLIST, REPLAY_FILE, cut_replay


def recipe(tmp_path, document):
    path = tmp_path / "recipe.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False))
    return path


def test_allowlist_has_the_ten_read_only_tools():
    assert len(REPLAY_ALLOWLIST) == 10
    assert "grist_get_records" in REPLAY_ALLOWLIST


def test_cut_at_the_first_non_allowlisted_tool(tmp_path):
    steps = [
        {"id": "a", "run": "make"},
        {"id": "b", "tool": "drive_list_files"},
        {"id": "c", "template": "x"},
        {"id": "d", "tool": "gmail_send"},
        {"id": "e", "tool": "drive_list_files"},
    ]
    path = recipe(tmp_path, {"name": "n", "work_dir": "w", "steps": steps})
    target = cut_replay(path)
    assert target == tmp_path / REPLAY_FILE
    cut = yaml.safe_load(target.read_text())
    assert cut == {"name": "n", "work_dir": "w", "steps": steps[:3]}
    assert list(cut) == ["name", "work_dir", "steps"]


def test_all_read_only_recipe_is_kept_whole(tmp_path):
    steps = [{"id": "a", "tool": "grist_get_records"}, {"id": "b", "tool": "drive_download_file"}]
    target = cut_replay(recipe(tmp_path, {"steps": steps}))
    assert yaml.safe_load(target.read_text())["steps"] == steps


def test_first_step_writes_gives_none_and_no_file(tmp_path):
    path = recipe(tmp_path, {"steps": [{"id": "a", "tool": "gmail_send"}]})
    assert cut_replay(path) is None
    assert not (tmp_path / REPLAY_FILE).exists()


def test_no_steps_gives_none(tmp_path):
    assert cut_replay(recipe(tmp_path, {"name": "n"})) is None
