"""Tests for the service orchestration: frame extraction + converter behaviour.

The full pipeline (real RTCM3 -> RTCM 2.3, decoded by gpsdecode) is exercised
live in tests/test_interop.py / scripts/validate_service_live.py.
"""
import json
import queue
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from rtcm3to2p3.cli import build_parser
from rtcm3to2p3.dgps import _geometric_range
from rtcm3to2p3.ephemeris import (
    C,
    Ephemeris,
    satellite_clock_bias,
    satellite_position,
)
from rtcm3to2p3.ntrip import Feed
from rtcm3to2p3.service import Config, Converter, Service, extract_rtcm3_frames

_ADDE_ECEF = (-3924917.30, 3462747.06, -3632703.99)

_EPH = Ephemeris(
    prn=1, week=2300, toe=100000.0, sqrt_a=5153.65, ecc=0.005, delta_n=4.5e-9,
    m0=0.3, omega0=-0.5, omega=0.9, i0=0.96, omega_dot=-8.0e-9, idot=1.0e-10,
    cuc=1.0e-6, cus=8.0e-6, crc=250.0, crs=-30.0, cic=-1.0e-7, cis=1.2e-7,
    toc=100000.0, af0=1.0e-4, af1=1.0e-12, af2=0.0, tgd=5.0e-9, iode=42,
)


def _consistent_pr(tow):
    """A pseudorange for _EPH/_ADDE with zero residual (survives every gate)."""
    pr = 22_000e3
    for _ in range(4):
        t_tx = tow - pr / C
        sat = satellite_position(_EPH, t_tx)
        dt = satellite_clock_bias(_EPH, t_tx, apply_tgd=True)
        pr = _geometric_range(sat, _ADDE_ECEF) - C * dt
    return pr


def _msm7_msg(tow):
    """A 1077 mock (as pyrtcm would present it) for PRN 1 at ``tow``."""
    pr_ms = _consistent_pr(tow) / (C * 1e-3)
    int_ms = int(pr_ms)
    return SimpleNamespace(
        identity="1077", DF004=int(round(tow * 1000)),
        PRN_01="001", DF397_01=int_ms, DF398_01=pr_ms - int_ms,
        CELLPRN_01="001", CELLSIG_01="1C", DF405_01=0.0,
    )


def _drain(sub) -> bytes:
    out = bytearray()
    try:
        while True:
            out += sub.get_nowait()
    except queue.Empty:
        pass
    return bytes(out)


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


def test_modified_zcount_wraps_at_hour_boundary():
    from rtcm3to2p3.service import modified_zcount
    assert modified_zcount(0.0) == 0
    assert modified_zcount(0.6) == 1
    assert modified_zcount(600.0) == 1000
    # top of the hour: round() reaches 6000, which must wrap to 0 (not stay 6000)
    assert modified_zcount(3599.95) == 0
    # never emits an out-of-range value anywhere in the hour
    assert all(0 <= modified_zcount(t / 100) < 6000 for t in range(360000))


def _converter_with_msm7(tow, n=5, **kw):
    """A Converter seeded with station+ephemeris, fed ``n`` MSM7 epochs from tow."""
    r3, r23 = Feed("3"), Feed("23")
    conv = Converter(r3, r23, **kw)
    conv.gen.set_station(_ADDE_ECEF)
    conv.gen.add_ephemeris(_EPH)
    sub = r23.subscribe()
    epochs = iter([tow + k for k in range(n)])
    conv._parse = lambda frame: _msm7_msg(next(epochs))
    for _ in range(n):
        conv.feed_obs(_frame(b"x"))
    return conv, sub


def test_converter_emits_type1_from_msm7():
    conv, sub = _converter_with_msm7(100200.0, station_id=300)
    assert conv.messages_out == 5
    assert _drain(sub)  # bytes were published to the RTCM2.3 feed


@pytest.mark.skipif(shutil.which("gpsdecode") is None, reason="gpsdecode not installed")
def test_emitted_type1_decodes_with_correct_fields():
    conv, sub = _converter_with_msm7(100200.0, station_id=300)
    data = _drain(sub)
    p = subprocess.run(["gpsdecode"], input=data, capture_output=True, timeout=15)
    msgs = [json.loads(ln) for ln in p.stdout.decode().splitlines() if ln.strip()]
    t1 = [m for m in msgs if m.get("class") == "RTCM2" and m.get("type") == 1]
    assert t1, "no Type 1 decoded from the converter output"
    m = t1[0]
    assert m["station_id"] == 300
    assert m["zcount"] < 3600.0  # seconds; in range
    assert 1 in {s["ident"] for s in m["satellites"]}
