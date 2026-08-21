"""Tests for the service orchestration: frame extraction + converter behaviour.

The full pipeline (real RTCM3 -> RTCM 2.3, decoded by gpsdecode) is exercised
live in tests/test_interop.py / scripts/validate_service_live.py.
"""
from rtcm3to2p3.ntrip import Feed
from rtcm3to2p3.service import Config, Converter, Service, extract_rtcm3_frames


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
