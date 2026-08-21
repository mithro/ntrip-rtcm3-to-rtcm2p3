"""GPS satellite position and clock from broadcast ephemeris (IS-GPS-200).

Given a set of broadcast ephemeris parameters (as decoded from RTCM3 message
1019 or a RINEX navigation record), :func:`satellite_position` returns the
satellite's ECEF position at a given transmit time, and
:func:`satellite_clock_bias` returns its clock offset. These feed the DGPS
reference-station correction computation (:mod:`rtcm3to2p3.dgps`).

The algorithm follows IS-GPS-200 §20.3.3.4.3 (user algorithm for ephemeris
determination). Positions are cross-checked against gnss_lib_py's
``find_sv_states`` in the test suite.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# IS-GPS-200 constants
MU = 3.986005e14  # WGS-84 Earth gravitational constant [m^3/s^2]
OMEGA_E_DOT = 7.2921151467e-5  # WGS-84 Earth rotation rate [rad/s]
C = 2.99792458e8  # speed of light [m/s]
F_REL = -4.442807633e-10  # relativistic correction factor -2*sqrt(MU)/C^2 [s/sqrt(m)]

_HALF_WEEK = 302400.0
_WEEK = 604800.0


@dataclass(frozen=True)
class Ephemeris:
    """GPS broadcast ephemeris (Keplerian + clock), SI units / radians."""

    prn: int
    week: int  # GPS week of toe, as broadcast (RTCM3 1019 DF076 is mod-1024)
    toe: float  # time of ephemeris [s of week]
    sqrt_a: float  # sqrt semi-major axis [sqrt(m)]
    ecc: float  # eccentricity [-]
    m0: float  # mean anomaly at reference time [rad]
    delta_n: float  # mean motion difference [rad/s]
    omega0: float  # longitude of ascending node at week epoch [rad]
    omega: float  # argument of perigee [rad]
    i0: float  # inclination at reference time [rad]
    omega_dot: float  # rate of right ascension [rad/s]
    idot: float  # rate of inclination [rad/s]
    cuc: float  # argument-of-latitude cosine harmonic [rad]
    cus: float  # argument-of-latitude sine harmonic [rad]
    crc: float  # orbit-radius cosine harmonic [m]
    crs: float  # orbit-radius sine harmonic [m]
    cic: float  # inclination cosine harmonic [rad]
    cis: float  # inclination sine harmonic [rad]
    # clock
    toc: float  # clock reference time [s of week]
    af0: float  # clock bias [s]
    af1: float  # clock drift [s/s]
    af2: float  # clock drift rate [s/s^2]
    tgd: float = 0.0  # group delay [s]
    iode: int = 0  # issue of data, ephemeris
    health: int = 0  # SV health (0 = healthy)
    ura: int = 0  # user range accuracy index


def _delta_t_week(t: float, t_ref: float) -> float:
    """Time from a week-of-seconds reference, corrected for week rollover."""
    dt = t - t_ref
    if dt > _HALF_WEEK:
        dt -= _WEEK
    elif dt < -_HALF_WEEK:
        dt += _WEEK
    return dt


def _eccentric_anomaly(mk: float, ecc: float) -> float:
    """Solve Kepler's equation Ek = Mk + e*sin(Ek) by iteration."""
    ek = mk
    for _ in range(30):
        prev = ek
        ek = mk + ecc * math.sin(ek)
        if abs(ek - prev) < 1e-13:
            break
    return ek


def satellite_position(eph: Ephemeris, t: float) -> tuple[float, float, float]:
    """ECEF position [m] of the satellite at transmit time ``t`` [s of week]."""
    a = eph.sqrt_a**2
    tk = _delta_t_week(t, eph.toe)
    n = math.sqrt(MU / a**3) + eph.delta_n
    mk = eph.m0 + n * tk
    ek = _eccentric_anomaly(mk, eph.ecc)

    sin_ek, cos_ek = math.sin(ek), math.cos(ek)
    vk = math.atan2(math.sqrt(1 - eph.ecc**2) * sin_ek, cos_ek - eph.ecc)
    phik = vk + eph.omega

    sin2, cos2 = math.sin(2 * phik), math.cos(2 * phik)
    uk = phik + eph.cus * sin2 + eph.cuc * cos2
    rk = a * (1 - eph.ecc * cos_ek) + eph.crs * sin2 + eph.crc * cos2
    ik = eph.i0 + eph.idot * tk + eph.cis * sin2 + eph.cic * cos2

    xp = rk * math.cos(uk)
    yp = rk * math.sin(uk)
    omega_k = eph.omega0 + (eph.omega_dot - OMEGA_E_DOT) * tk - OMEGA_E_DOT * eph.toe

    sin_o, cos_o = math.sin(omega_k), math.cos(omega_k)
    cos_i = math.cos(ik)
    x = xp * cos_o - yp * cos_i * sin_o
    y = xp * sin_o + yp * cos_i * cos_o
    z = yp * math.sin(ik)
    return x, y, z


def satellite_clock_bias(eph: Ephemeris, t: float, apply_tgd: bool = True) -> float:
    """Satellite clock offset [s] at transmit time ``t`` [s of week].

    Includes the af0/af1/af2 polynomial and the relativistic eccentricity term.
    When ``apply_tgd`` is set, the L1 group delay (Tgd) is also removed, which is
    correct for a single-frequency (L1 C/A) user such as the u-blox 7.
    """
    a = eph.sqrt_a**2
    tk = _delta_t_week(t, eph.toe)
    n = math.sqrt(MU / a**3) + eph.delta_n
    ek = _eccentric_anomaly(eph.m0 + n * tk, eph.ecc)
    dt_rel = F_REL * eph.ecc * eph.sqrt_a * math.sin(ek)

    dtc = _delta_t_week(t, eph.toc)
    bias = eph.af0 + eph.af1 * dtc + eph.af2 * dtc**2 + dt_rel
    if apply_tgd:
        bias -= eph.tgd
    return bias
