# Coding guidelines

## Conventions

- Python 3.12. Type hints everywhere; `mypy` runs in strict mode over `src`.
- Use `pathlib` for paths.
- No bare `except`; catch the exceptions you can handle.
- Subprocess calls use a fixed argv and never `shell=True`.
- Tests are plain pytest functions that use `tmp_path` for files and make no network calls.
- Comments only where the *why* is non-obvious.

## Definition of done

Run these from the repository root; all must pass (`/dod` runs them from `.claude/toolkit.yaml`, and
`tests.yml` runs them in CI on Python 3.12 and 3.13):

```bash
python3 -m pytest -v --cov=privacyfence_cockpit --cov-branch --cov-report=term-missing --cov-fail-under=95
ruff check .
python3 -m mypy
bandit -c pyproject.toml -r src
```

Then check by hand:

- User-visible change? A line under `CHANGELOG.md`'s `## [Unreleased]`, never under a version heading.
- A decision that is hard to reverse, moves a trust boundary, or changes how the image is built or
  released needs an ADR in `docs/adr/`.
- No log line, notification or page carries a token, password, master key or recipe data.
- Comments only where the why is non-obvious.
