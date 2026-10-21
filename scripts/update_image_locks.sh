#!/usr/bin/env bash
# Regenerates the hash-locked requirements the Dockerfile installs from.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m pip install uv
uv pip compile --universal --python-version 3.12 --generate-hashes pyproject.toml -o requirements/image.lock.txt
uv pip compile --universal --python-version 3.12 --generate-hashes requirements/build.in -o requirements/build.lock.txt
