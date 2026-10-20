"""PrivacyFence Cockpit: runs privacyfence-sync recipes on a schedule, with a web dashboard."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("privacyfence-cockpit")
except PackageNotFoundError:  # pragma: no cover - the test environment always has the package installed
    __version__ = "0.0.0.dev0"
