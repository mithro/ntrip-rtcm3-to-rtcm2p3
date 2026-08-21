"""Smoke test: the package imports and exposes a version."""
import rtcm3to2p3


def test_version_present():
    assert isinstance(rtcm3to2p3.__version__, str)
    assert rtcm3to2p3.__version__
