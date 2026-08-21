"""Tests for the service orchestration: frame extraction + converter behaviour.

The full pipeline (real RTCM3 -> RTCM 2.3, decoded by gpsdecode) is exercised
live in tests/test_interop.py / scripts/validate_service_live.py.
"""
import json
import shutil
import subprocess

import pytest

from rtcm3to2p3.cli import build_parser
from rtcm3to2p3.ntrip import Feed
from rtcm3to2p3.service import Config, Converter, Service, extract_rtcm3_frames

_ADDE_ECEF = (-3924917.30, 3462747.06, -3632703.99)


def _frame(payload: bytes) -> bytes:
    ln = len(payload)
    return bytes([0xD3, (ln >> 8) & 0x03, ln & 0xFF]) + payload + b"\x00\x00\x00"


def test_extract_single_frame_consumes_buffer():
    buf = bytearray(_frame(b"abcd"))
    frames = extract_rtcm3_frames(buf)
    assert len(frames) == 1
    assert frames[0][3:7] == b"abcd"
    assert len(buf) == 0


def test_extract_multiple_frames():
    buf = bytearray(_frame(b"aa") + _frame(b"bbbb"))
    frames = extract_rtcm3_frames(buf)
    assert [f[3:-3] for f in frames] == [b"aa", b"bbbb"]


def test_extract_partial_frame_retained_then_completed():
    full = _frame(b"hello")
    buf = bytearray(full[:4])
    assert extract_rtcm3_frames(buf) == []  # incomplete, nothing yet
    buf.extend(full[4:])
    frames = extract_rtcm3_frames(buf)
    assert frames == [full]


def test_extract_drops_noise_before_preamble():
    buf = bytearray(b"\x11\x22\x33" + _frame(b"xy"))
    frames = extract_rtcm3_frames(buf)
    assert len(frames) == 1 and frames[0][3:-3] == b"xy"


def test_extract_no_preamble_clears_buffer():
    buf = bytearray(b"\x01\x02\x03\x04")
    assert extract_rtcm3_frames(buf) == []
    assert len(buf) == 0


def test_converter_passthrough_publishes_rtcm3_verbatim():
    r3, r23 = Feed("3"), Feed("23")
    conv = Converter(r3, r23)
    sub = r3.subscribe()
    raw = _frame(b"abc")
    conv.feed_obs(raw)
    assert sub.get_nowait() == raw


def test_converter_survives_unparseable_input():
    r3, r23 = Feed("3"), Feed("23")
    conv = Converter(r3, r23)
    conv.feed_obs(_frame(b"\x00\x00not a real rtcm message"))  # bad CRC -> pyrtcm rejects
    assert conv.messages_out == 0  # no crash, no output


def test_service_constructs_two_mounts():
    svc = Service(Config(listen_addresses=["127.0.0.1"]))
    assert svc.rtcm3_feed.name == "ADDE_RTCM3"
    assert svc.rtcm23_feed.name == "ADDE_RTCM23"


def test_converter_rejects_out_of_range_station_id():
    r3, r23 = Feed("3"), Feed("23")
    for bad in (0, 1024, -1):
        with pytest.raises(ValueError):
            Converter(r3, r23, station_id=bad)


def test_type3_emitted_with_base_and_gated_by_interval():
    r3, r23 = Feed("3"), Feed("23")
    conv = Converter(r3, r23, station_id=100)
    sub = r23.subscribe()
    # no base position yet -> no Type 3
    conv._maybe_emit_type3(100000.0, 0)
    assert sub.empty()
    # once the base is known, a Type 3 is emitted...
    conv.gen.set_station(_ADDE_ECEF)
    conv._maybe_emit_type3(100000.0, 0)
    assert sub.get_nowait()
    # ...but not again within the 60 s interval...
    conv._maybe_emit_type3(100030.0, 5)
    assert sub.empty()
    # ...and again once the interval has elapsed.
    conv._maybe_emit_type3(100070.0, 6)
    assert sub.get_nowait()


@pytest.mark.skipif(shutil.which("gpsdecode") is None, reason="gpsdecode not installed")
def test_emitted_type3_decodes_to_base_position():
    r3, r23 = Feed("3"), Feed("23")
    conv = Converter(r3, r23, station_id=321)
    sub = r23.subscribe()
    conv.gen.set_station(_ADDE_ECEF)
    conv._maybe_emit_type3(100000.0, 0)
    data = sub.get_nowait()
    p = subprocess.run(["gpsdecode"], input=data, capture_output=True, timeout=15)
    msgs = [json.loads(ln) for ln in p.stdout.decode().splitlines() if ln.strip()]
    m = next(m for m in msgs if m.get("class") == "RTCM2" and m.get("type") == 3)
    assert m["station_id"] == 321
    assert m["x"] == pytest.approx(_ADDE_ECEF[0], abs=0.01)
    assert m["z"] == pytest.approx(_ADDE_ECEF[2], abs=0.01)


def test_cli_rejects_out_of_range_station_id():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--station-id", "0"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--station-id", "1024"])
    assert build_parser().parse_args(["--station-id", "500"]).station_id == 500


def test_cli_exposes_dgps_tuning_knobs():
    ns = build_parser().parse_args(["--max-iod-age", "3600", "--max-residual", "50"])
    assert ns.max_iod_age == 3600.0 and ns.max_residual == 50.0
