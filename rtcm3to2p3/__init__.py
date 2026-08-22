"""ntrip-rtcm3-to-rtcm2p3: NTRIP relay with RTCM 3.x -> RTCM 2.3 DGPS conversion."""
from __future__ import annotations

# The version is derived from git tags at build time by hatch-vcs and written to
# rtcm3to2p3/_version.py. Fall back to the installed package metadata, then to a
# dev placeholder when running from a source tree that has not been built.
try:
    from ._version import __version__
except ImportError:  # pragma: no cover - only when built metadata is absent
    try:
        from importlib.metadata import PackageNotFoundError, version

        __version__ = version("ntrip-rtcm3-to-rtcm2p3")
    except PackageNotFoundError:  # pragma: no cover
        __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
