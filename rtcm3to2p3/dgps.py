"""Compute RTCM 2.3 Type 1 pseudorange corrections from an RTCM3 reference stream.

A DGPS reference station broadcasts, per satellite, the difference between the
geometric range (known station position -> satellite position from ephemeris)
and the measured pseudorange, with the satellite clock removed:

    raw_i = geometric_i - pseudorange_i - c * dt_sv_i

``raw_i`` is dominated by the base receiver's clock offset, which is common to
all satellites and would overflow the 16-bit RTCM field. We estimate it as the
median over satellites and subtract it (the rover simply absorbs the removed
offset into its own clock solution); what remains is the per-satellite
tropo/iono/ephemeris error the rover actually needs (metres, not kilometres).

The range-rate correction (RRC) is the change in PRC between consecutive epochs.
"""
from __future__ import annotations

import math
from statistics import median

from .ephemeris import OMEGA_E_DOT, C, Ephemeris, satellite_clock_bias, satellite_position
from .rtcm2 import Correction


def _geometric_range(sat: tuple[float, float, float], rcv: tuple[float, float, float]) -> float:
    """Range station->satellite with the Sagnac (Earth-rotation) correction."""
    dx = sat[0] - rcv[0]
    dy = sat[1] - rcv[1]
    dz = sat[2] - rcv[2]
    r = math.sqrt(dx * dx + dy * dy + dz * dz)
    r += OMEGA_E_DOT / C * (sat[0] * rcv[1] - sat[1] * rcv[0])
    return r


def raw_correction(
    eph: Ephemeris, base: tuple[float, float, float], tow: float, pr: float
) -> float:
    """raw PRC (metres) for one satellite before common-clock removal."""
    t_tx = tow - pr / C  # transmit time
    sat = satellite_position(eph, t_tx)
    dt_sv = satellite_clock_bias(eph, t_tx, apply_tgd=True)
    return _geometric_range(sat, base) - pr - C * dt_sv


class DgpsGenerator:
    """Turns decoded RTCM3 (station + ephemerides + GPS obs) into corrections."""

    def __init__(self, max_iod_age_s: float = 7200.0) -> None:
        self.base: tuple[float, float, float] | None = None
        self.ephemerides: dict[int, Ephemeris] = {}
        self._prev: dict[int, tuple[float, float]] = {}  # prn -> (tow, raw correction)
        self.max_residual_m = 100.0  # reject satellites whose residual is implausible

    def set_station(self, ecef: tuple[float, float, float]) -> None:
        self.base = ecef

    def add_ephemeris(self, eph: Ephemeris) -> None:
        self.ephemerides[eph.prn] = eph

    def corrections(self, tow: float, pseudoranges: dict[int, float]) -> list[Correction]:
        """Compute Type 1 corrections for the satellites we can (station + eph + obs)."""
        if self.base is None:
            return []
        raw: dict[int, float] = {}
        for prn, pr in pseudoranges.items():
            eph = self.ephemerides.get(prn)
            if eph is None or pr <= 0:
                continue
            raw[prn] = raw_correction(eph, self.base, tow, pr)
        if not raw:
            return []

        offset = median(raw.values())  # common base-clock term (removed from PRC)

        # RRC is the rate of change of the *raw* correction (which drifts smoothly
        # with the base clock) minus the common drift; computing it on the PRC
        # instead would be corrupted by the per-epoch median jumping as the
        # satellite set changes.
        raw_rate: dict[int, float] = {}
        for prn, value in raw.items():
            prev = self._prev.get(prn)
            if prev is not None and tow != prev[0]:
                raw_rate[prn] = (value - prev[1]) / (tow - prev[0])
        rate_offset = median(raw_rate.values()) if raw_rate else 0.0

        out: list[Correction] = []
        for prn, value in sorted(raw.items()):
            prc = value - offset
            self._prev[prn] = (tow, value)
            if abs(prc) > 1.0e5:  # gross outlier (bad obs/eph) -> skip
                continue
            rrc = raw_rate[prn] - rate_offset if prn in raw_rate else 0.0
            out.append(Correction(prn=prn, prc=prc, rrc=rrc, iod=self.ephemerides[prn].iode))
        return out
