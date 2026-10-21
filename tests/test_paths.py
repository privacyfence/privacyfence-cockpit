import stat

from privacyfence_cockpit.paths import DataPaths, branch_folder


def test_layout(tmp_path):
    paths = DataPaths(tmp_path)
    assert paths.repo == tmp_path / "repo"
    assert paths.venvs == tmp_path / "venvs"
    assert paths.logs == tmp_path / "logs"
    assert paths.db_file == tmp_path / "cockpit.db"
    assert paths.secrets == tmp_path / "secrets"
    assert paths.home == tmp_path / "home"
    assert paths.run_lock == tmp_path / "run.lock"
    assert paths.git_lock == tmp_path / "git.lock"


def test_branch_paths_and_folder_names(tmp_path):
    paths = DataPaths(tmp_path)
    assert paths.branches == tmp_path / "branches"
    assert paths.branch_venvs == tmp_path / "branch-venvs"
    assert branch_folder("feature/x/y") == "feature%2Fx%2Fy"
    assert branch_folder("main") == "main"


def test_ensure_creates_private_folders_but_not_repo(tmp_path):
    root = tmp_path / "data"
    paths = DataPaths(root)
    assert paths.ensure() is paths
    for folder in (root, paths.venvs, paths.logs, paths.secrets, paths.home, paths.home / ".ssh"):
        assert folder.is_dir()
        assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert not paths.repo.exists()


def test_ensure_is_idempotent_and_tightens_modes(tmp_path):
    paths = DataPaths(tmp_path)
    paths.ensure()
    paths.secrets.chmod(0o755)
    paths.ensure()
    assert stat.S_IMODE(paths.secrets.stat().st_mode) == 0o700
