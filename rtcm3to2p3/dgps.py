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

Caveat: because the median is recomputed each epoch from whatever satellites are
present, a satellite rising/setting shifts the absolute PRC level of every other
satellite by a small step. The rover absorbs this into its clock, so position is
unaffected; the RRC is computed on the raw corrections (see below) precisely to
avoid inheriting that jump.

Satellites are skipped when their ephemeris is unhealthy, stale (older than
``max_iod_age_s``), or their raw correction is an implausible outlier (magnitude
above ``max_residual_m`` after common-clock removal).
"""
from __future__ import annotations

import logging
import math
from statistics import median

from .ephemeris import (
    OMEGA_E_DOT,
    C,
    Ephemeris,
    _delta_t_week,
    satellite_clock_bias,
    satellite_position,
)
from .rtcm2 import Correction

logger = logging.getLogger(__name__)


def _geometric_range(sat: tuple[float, float, float], rcv: tuple[float, float, float]) -> float:
    """Range station->satellite with the Sagnac (Earth-rotation) correction."""
    dx, dy, dz = sat[0] - rcv[0], sat[1] - rcv[1], sat[2] - rcv[2]
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


def udre_index(prc: float) -> int:
    """RTCM 2.3 UDRE index (0..3) from the correction magnitude (metres)."""
    magnitude = abs(prc)
    if magnitude <= 1.0:
        return 0
    if magnitude <= 4.0:
        return 1
    if magnitude <= 8.0:
        return 2
    return 3


class DgpsGenerator:
    """Turns decoded RTCM3 (station + ephemerides + GPS obs) into corrections."""

    def __init__(self, max_iod_age_s: float = 7200.0, max_residual_m: float = 100.0) -> None:
        self.base: tuple[float, float, float] | None = None
        self.ephemerides: dict[int, Ephemeris] = {}
        self._prev: dict[int, tuple[float, float]] = {}  # prn -> (tow, raw correction)
        self.max_iod_age_s = max_iod_age_s  # reject ephemeris older than this
        self.max_residual_m = max_residual_m  # reject implausible corrections

    def set_station(self, ecef: tuple[float, float, float]) -> None:
        self.base = ecef

    def add_ephemeris(self, eph: Ephemeris) -> None:
        self.ephemerides[eph.prn] = eph

    def corrections(self, tow: float, pseudoranges: dict[int, float]) -> list[Correction]:
        """Compute Type 1 corrections for the satellites we can (station + eph + obs)."""
        if self.base is None:
            logger.debug("no station position yet; %d obs deferred", len(pseudoranges))
            return []
        raw: dict[int, float] = {}
        skipped = {"no_eph": 0, "unhealthy": 0, "stale": 0}
        for prn, pr in pseudoranges.items():
            eph = self.ephemerides.get(prn)
            if eph is None or pr <= 0:
                skipped["no_eph"] += 1
                continue
            if eph.health != 0:
                skipped["unhealthy"] += 1
                continue
            t_tx = tow - pr / C
            if abs(_delta_t_week(t_tx, eph.toe)) > self.max_iod_age_s:
                skipped["stale"] += 1
                continue
            raw[prn] = raw_correction(eph, self.base, tow, pr)
        if any(skipped.values()):
            logger.debug("epoch %.0f: skipped %s", tow, skipped)
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
        dropped = 0
        for prn, value in sorted(raw.items()):
            prc = value - offset
            self._prev[prn] = (tow, value)
            if abs(prc) > self.max_residual_m:  # gross outlier (bad obs/eph) -> skip
                dropped += 1
                continue
            rrc = raw_rate[prn] - rate_offset if prn in raw_rate else 0.0
            out.append(Correction(prn=prn, prc=prc, rrc=rrc, udre=udre_index(prc),
                                  iod=self.ephemerides[prn].iode))
        if dropped:
            logger.debug("epoch %.0f: dropped %d outlier(s) (>%.0f m)", tow, dropped,
                         self.max_residual_m)
        return out
