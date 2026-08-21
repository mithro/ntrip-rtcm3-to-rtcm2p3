"""Tests for the RTCM 2.3 Type 1 encoder.

Output is validated with *independent* decoders:
  1. gpsd's ``gpsdecode`` (external process, entirely separate codebase) — skipped
     if the binary is not installed.
  2. A from-scratch decoder in this file, transcribed from RTKLIB's
     ``input_rtcm2``/``decode_word`` (6-of-8 de-framing + Hamming parity + Type 1
     field extraction) — a second implementation independent of ``rtcm2.py``.
The u-blox 7 hardware is the third, ultimate validator, exercised in the
end-to-end service validation (not a unit test).
"""
import json
import shutil
import subprocess

import pytest

from rtcm3to2p3.rtcm2 import PRC_SCALE, Correction, Rtcm2Encoder

CORR = [
    Correction(prn=5, prc=1.24, rrc=0.010, iod=45, udre=0),
    Correction(prn=12, prc=-4.56, rrc=-0.020, iod=200, udre=1),
    Correction(prn=32, prc=10.00, rrc=0.000, iod=7, udre=2),  # PRN 32 -> transmitted as 0
]


# --- independent decoder (RTKLIB formulation) -------------------------------
_HAMMING = (0xBB1F3480, 0x5D8F9A40, 0xAEC7CD00, 0x5763E680, 0x6BB1F340, 0x8B7A89C0)


def _decode_word(word32):
    w = word32 ^ 0x3FFFFFC0 if word32 & 0x40000000 else word32
    parity = 0
    for h in _HAMMING:
        parity = (parity << 1) | (bin(w & h).count("1") & 1)
    if parity != (word32 & 0x3F):
        return None
    return bytes(((w >> 22) & 0xFF, (w >> 14) & 0xFF, (w >> 6) & 0xFF))


def _getbits(buff, pos, n, signed):
    v = 0
    for _ in range(n):
        v = (v << 1) | ((buff[pos >> 3] >> (7 - (pos & 7))) & 1)
        pos += 1
    if signed and v & (1 << (n - 1)):
        v -= 1 << n
    return v


def decode_rtcm2(data):
    """Independent RTCM2 decoder: returns list of Type 1 dicts."""
    word = nbit = nbyte = length = 0
    buff = bytearray()
    out = []
    for raw in data:
        if (raw & 0xC0) != 0x40:
            continue
        b = raw
        for _ in range(6):
            word = ((word << 1) | (b & 1)) & 0xFFFFFFFF
            b >>= 1
            if nbyte == 0:
                preamb = (word >> 22) & 0xFF
                if word & 0x40000000:
                    preamb ^= 0xFF
                if preamb != 0x66:
                    continue
                dw = _decode_word(word)
                if dw is None:
                    continue
                buff = bytearray(dw)
                nbyte, nbit = 3, 0
                continue
            nbit += 1
            if nbit < 30:
                continue
            nbit = 0
            dw = _decode_word(word)
            if dw is None:
                nbyte, word = 0, word & 0x3
                continue
            buff += dw
            nbyte += 3
            if nbyte == 6:
                length = (buff[5] >> 3) * 3 + 6
            if nbyte < length:
                continue
            nbyte, word = 0, word & 0x3
            out.append(_parse_type1(buff))
    return out


def _parse_type1(buff):
    msg = {
        "type": _getbits(buff, 8, 6, False),
        "station_id": _getbits(buff, 14, 10, False),
        "zcount": _getbits(buff, 24, 13, False),
        "seq": _getbits(buff, 37, 3, False),
        "length": _getbits(buff, 40, 5, False),
        "health": _getbits(buff, 45, 3, False),
        "sats": [],
    }
    i = 48
    while i + 40 <= len(buff) * 8:
        scale = _getbits(buff, i, 1, False)
        udre = _getbits(buff, i + 1, 2, False)
        prn = _getbits(buff, i + 3, 5, False) or 32
        prc = _getbits(buff, i + 8, 16, True)
        rrc = _getbits(buff, i + 24, 8, True)
        iod = _getbits(buff, i + 32, 8, False)
        i += 40
        msg["sats"].append(
            {"prn": prn, "udre": udre, "prc": prc, "rrc": rrc, "iod": iod, "scale": scale}
        )
    return msg


# --- tests ------------------------------------------------------------------
def test_independent_decoder_roundtrip():
    enc = Rtcm2Encoder()
    data = b"".join(enc.encode_type1(42, 100 + k, k % 8, 0, CORR) for k in range(3))
    msgs = decode_rtcm2(data)
    assert len(msgs) == 3
    m = msgs[0]
    assert m["type"] == 1 and m["station_id"] == 42 and m["length"] == 5
    assert m["zcount"] == 100 and m["seq"] == 0 and m["health"] == 0
    assert len(m["sats"]) == 3
    for got, want in zip(m["sats"], CORR, strict=True):
        assert got["prn"] == want.prn
        assert got["udre"] == want.udre
        assert got["iod"] == want.iod
        assert got["prc"] * PRC_SCALE[got["scale"]] == pytest.approx(want.prc, abs=0.02)


@pytest.mark.skipif(shutil.which("gpsdecode") is None, reason="gpsdecode not installed")
def test_gpsdecode_roundtrip():
    enc = Rtcm2Encoder()
    data = b"".join(enc.encode_type1(42, 100 + k, k % 8, 0, CORR) for k in range(4))
    p = subprocess.run(["gpsdecode"], input=data, capture_output=True, timeout=15)
    msgs = [json.loads(ln) for ln in p.stdout.decode().splitlines() if ln.strip()]
    rtcm = [m for m in msgs if m.get("class") == "RTCM2" and m.get("type") == 1]
    assert len(rtcm) == 4
    m = rtcm[0]
    assert m["station_id"] == 42 and m["length"] == 5 and m["station_health"] == 0
    by_prn = {s["ident"]: s for s in m["satellites"]}
    # gpsdecode reports the raw RTCM2 sat-id, so PRN 32 appears as ident 0
    # (RTKLIB maps 0->32; the wire bits are identical either way).
    assert set(by_prn) == {5, 12, 0}
    for want in CORR:
        s = by_prn[0 if want.prn == 32 else want.prn]
        assert s["udre"] == want.udre and s["iod"] == want.iod
        assert s["prc"] == pytest.approx(want.prc, abs=0.02)
        assert s["rrc"] == pytest.approx(want.rrc, abs=0.004)


def test_scale_factor_selects_coarse_for_large_prc():
    # 700 m exceeds fine-scale range (32767 * 0.02 = 655 m) -> forces scale 1
    enc = Rtcm2Encoder()
    data = b"".join(
        enc.encode_type1(1, k, 0, 0, [Correction(prn=1, prc=700.0, rrc=0.0, iod=0)])
        for k in range(3)
    )
    m = decode_rtcm2(data)[0]
    assert m["sats"][0]["scale"] == 1
    assert m["sats"][0]["prc"] * PRC_SCALE[1] == pytest.approx(700.0, abs=0.32)


def test_prn32_transmitted_as_zero():
    enc = Rtcm2Encoder()
    data = b"".join(
        enc.encode_type1(1, k, 0, 0, [Correction(prn=32, prc=0.0, rrc=0.0, iod=0)])
        for k in range(3)
    )
    assert decode_rtcm2(data)[0]["sats"][0]["prn"] == 32


def test_empty_corrections_header_only():
    # a legitimate station-health-only message: two header words, N=0, no sats
    enc = Rtcm2Encoder()
    data = b"".join(enc.encode_type1(7, k, 0, 3, []) for k in range(3))
    m = decode_rtcm2(data)[0]
    assert m["type"] == 1 and m["station_id"] == 7 and m["length"] == 0
    assert m["health"] == 3 and m["sats"] == []


def test_never_emits_sentinel_values():
    # -655.36 m / -0.256 m/s round to the fine-scale sentinels (-32768 / -128);
    # the encoder must switch scale to avoid them. The 1e6 values overflow even
    # coarse scale and must clamp to +/-max (never the sentinel).
    enc = Rtcm2Encoder()
    corr = [
        Correction(prn=1, prc=-655.36, rrc=-0.256, iod=0),
        Correction(prn=2, prc=1e6, rrc=1e3, iod=0),
        Correction(prn=3, prc=-1e6, rrc=-1e3, iod=0),
    ]
    data = b"".join(enc.encode_type1(1, k, 0, 0, corr) for k in range(3))
    m = decode_rtcm2(data)[0]
    for s in m["sats"]:
        assert s["prc"] != -32768 and s["rrc"] != -128
    s0 = m["sats"][0]
    assert s0["prc"] * PRC_SCALE[s0["scale"]] == pytest.approx(-655.36, abs=0.32)
    assert m["sats"][1]["prc"] == 32767 and m["sats"][1]["rrc"] == 127  # clamped positive max
    assert m["sats"][2]["prc"] == -32767 and m["sats"][2]["rrc"] == -127  # clamped, not sentinel


def test_field_range_errors():
    enc = Rtcm2Encoder()
    bad_calls = [
        lambda: enc.encode_type1(2000, 0, 0, 0, CORR),  # station_id > 1023
        lambda: enc.encode_type1(1, 9999, 0, 0, []),  # zcount > 8191
        lambda: enc.encode_type1(1, 0, 9, 0, []),  # seq > 7
        lambda: enc.encode_type1(1, 0, 0, 9, []),  # health > 7
        lambda: enc.encode_type1(1, 0, 0, 0, [Correction(prn=99, prc=0, rrc=0, iod=0)]),  # prn high
        lambda: enc.encode_type1(1, 0, 0, 0, [Correction(prn=0, prc=0, rrc=0, iod=0)]),  # prn=0
        lambda: enc.encode_type1(1, 0, 0, 0, [Correction(prn=1, prc=0, rrc=0, iod=999)]),  # iod
        lambda: enc.encode_type1(1, 0, 0, 0, [Correction(prn=1, prc=0, rrc=0, iod=0, udre=9)]),
    ]
    for call in bad_calls:
        with pytest.raises(ValueError):
            call()
