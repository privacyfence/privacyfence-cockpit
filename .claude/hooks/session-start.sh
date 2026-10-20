#!/bin/bash
# SessionStart hook for Claude Code on the web: make a fresh container able to run the
# definition of done (docs/coding-guidelines.md) without a manual setup step.
#
# The web checkout arrives shallow and with no tags. setuptools_scm resolves the version
# through `git describe`, so an install in that state bakes [tool.setuptools_scm]'s
# fallback_version into the metadata that privacyfence_cockpit.__version__ reads. Restoring
# history and tags therefore runs before the install. Deliberately synchronous for the same reason.
set -euo pipefail

# Web sessions only; a local checkout belongs to the maintainer and is already set up.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel)}"

echo "==> Restoring full history and tags (setuptools_scm needs them)"
if [ "$(git rev-parse --is-shallow-repository)" = "true" ]; then
  # --unshallow fails outright on a complete repo, hence the branch; the bare --tags
  # retry covers a server that refuses the deepening but will still hand over tags.
  git fetch --unshallow --tags origin || git fetch --tags origin
else
  git fetch --tags origin
fi

if git describe --tags >/dev/null 2>&1; then
  echo "    version resolves as: $(git describe --tags)"
else
  echo "    WARNING: no tag reachable from HEAD; __version__ will be fallback_version"
fi

echo "==> Installing privacyfence-cockpit with the dev and test extras"
python3 -m pip install -e ".[dev,test]"
