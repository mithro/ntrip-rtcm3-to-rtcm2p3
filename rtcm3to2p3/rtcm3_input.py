"""Decode the RTCM3 messages the DGPS converter needs, via pyrtcm.

Extracts the three ingredients of an RTCM 2.3 Type 1 correction:

* station position (message 1005/1006 -> ECEF metres),
* GPS broadcast ephemeris (message 1019 -> :class:`~rtcm3to2p3.ephemeris.Ephemeris`),
* GPS L1 C/A pseudoranges (message 1077, GPS MSM7 -> {PRN: metres}).

RTCM3 framing, CRC and field scaling are handled by pyrtcm; this module maps its
fields to SI units. RTCM stores the ephemeris angular terms in *semicircles*, so
they are multiplied by pi to get radians.
"""
from __future__ import annotations

import math

from .ephemeris import C, Ephemeris

# pyrtcm message identities
STATION_MESSAGES = frozenset({"1005", "1006"})
GPS_EPHEMERIS = "1019"
GPS_MSM7 = "1077"

_MS_TO_M = C * 1e-3  # metres per millisecond of range
_L1CA = "1C"  # MSM signal id for GPS L1 C/A
_SEMI = math.pi  # semicircles -> radians


def parse_station(msg) -> tuple[float, float, float]:
    """ECEF (x, y, z) metres of the reference station from a 1005/1006 message."""
    return float(msg.DF025), float(msg.DF026), float(msg.DF027)


def parse_ephemeris(msg) -> Ephemeris:
    """Map an RTCM3 1019 (GPS ephemeris) message to an :class:`Ephemeris`."""
    return Ephemeris(
        prn=int(msg.DF009),
        week=int(msg.DF076),  # GPS week mod 1024
        toe=float(msg.DF093),
        sqrt_a=float(msg.DF092),
        ecc=float(msg.DF090),
        m0=float(msg.DF088) * _SEMI,
        delta_n=float(msg.DF087) * _SEMI,
        omega0=float(msg.DF095) * _SEMI,
        omega=float(msg.DF099) * _SEMI,
        i0=float(msg.DF097) * _SEMI,
        omega_dot=float(msg.DF100) * _SEMI,
        idot=float(msg.DF079) * _SEMI,
        cuc=float(msg.DF089),
        cus=float(msg.DF091),
        crc=float(msg.DF098),
        crs=float(msg.DF086),
        cic=float(msg.DF094),
        cis=float(msg.DF096),
        toc=float(msg.DF081),
        af0=float(msg.DF084),
        af1=float(msg.DF083),
        af2=float(msg.DF082),
        tgd=float(msg.DF101),
        iode=int(msg.DF071),
        health=int(msg.DF102),
        ura=int(msg.DF077),
    )


def _count(msg, prefix: str) -> int:
    n = 0
    while hasattr(msg, f"{prefix}{n + 1:02d}"):
        n += 1
    return n


def parse_gps_msm7(msg) -> tuple[float, dict[int, float]]:
    """Return (tow_seconds, {PRN: L1 C/A pseudorange metres}) from a 1077 message.

    DF004 is the GPS epoch time (TOW) in ms. Per satellite the rough range is
    DF397 (integer ms) + DF398 (ms), and per cell the fine pseudorange is DF405
    (ms); the L1 C/A pseudorange is their sum times c.
    """
    tow = float(msg.DF004) / 1000.0
    n_sat = _count(msg, "DF397_")
    # Rough range per satellite, keyed by PRN. pyrtcm exposes PRN_<nn> for each
    # satellite-mask slot, so we map slot -> PRN directly rather than inferring it
    # from cell order (which is wrong when a masked satellite has no signal cells).
    rough: dict[int, float] = {}
    for i in range(1, n_sat + 1):
        int_ms = getattr(msg, f"DF397_{i:02d}")
        if int_ms >= 255:  # DF397 == 255 => invalid/no range
            continue
        prn = int(getattr(msg, f"PRN_{i:02d}"))
        rough[prn] = int_ms + getattr(msg, f"DF398_{i:02d}")

    n_cell = _count(msg, "DF405_")
    pseudoranges: dict[int, float] = {}
    for i in range(1, n_cell + 1):
        if getattr(msg, f"CELLSIG_{i:02d}") != _L1CA:
            continue
        fine = getattr(msg, f"DF405_{i:02d}")
        if fine is None:
            continue
        prn = int(getattr(msg, f"CELLPRN_{i:02d}"))
        if prn not in rough:
            continue
        pseudoranges[prn] = (rough[prn] + fine) * _MS_TO_M
    return tow, pseudoranges
