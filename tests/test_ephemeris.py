"""Tests for satellite position/clock from broadcast ephemeris.

The ECEF positions are cross-checked against gnss_lib_py's ``find_sv_states``
(Stanford NAV Lab) — an entirely independent implementation of the IS-GPS-200
algorithm. Reference values were generated once with gnss_lib_py and frozen here
(agreement was ~2 mm), so the test needs no heavy runtime dependency. The
regeneration script lives at ``scripts/gen_satpos_reference.py``.
"""
import math

import pytest

from rtcm3to2p3.ephemeris import Ephemeris, satellite_clock_bias, satellite_position

EPH = Ephemeris(
    prn=5, week=2300, toe=100000.0,
    sqrt_a=5153.65, ecc=0.005, m0=0.3, delta_n=4.5e-9,
    omega0=-0.5, omega=0.9, i0=0.96, omega_dot=-8.0e-9, idot=1.0e-10,
    cuc=1.0e-6, cus=8.0e-6, crc=250.0, crs=-30.0, cic=-1.0e-7, cis=1.2e-7,
    toc=100000.0, af0=1.0e-4, af1=1.0e-12, af2=0.0, tgd=5.0e-9, iode=42,
)

# Independent reference ECEF (metres) from gnss_lib_py.utils.sv_models.find_sv_states
_GLP_REF = {
    100200.0: (14699693.1496, -8089105.1314, 20426876.8612),
    99000.0: (14820605.0308, -11128748.3423, 18840889.5171),
    101800.0: (14863373.6987, -3756099.8351, 21551178.4947),
}


@pytest.mark.parametrize(("t", "ref"), _GLP_REF.items())
def test_satpos_matches_gnss_lib_py(t, ref):
    pos = satellite_position(EPH, t)
    for got, want in zip(pos, ref, strict=True):
        assert got == pytest.approx(want, abs=0.01)  # 1 cm vs independent impl


def test_orbital_radius_is_physical():
    for t in _GLP_REF:
        x, y, z = satellite_position(EPH, t)
        r = math.sqrt(x * x + y * y + z * z)
        assert 25.0e6 < r < 27.0e6  # GPS MEO radius ~26 560 km


def test_delta_t_week_rollover():
    from rtcm3to2p3.ephemeris import _delta_t_week

    assert _delta_t_week(100200.0, 100000.0) == pytest.approx(200.0)  # no wrap
    assert _delta_t_week(90.0, 604700.0) == pytest.approx(190.0)  # forward across week end
    assert _delta_t_week(604700.0, 90.0) == pytest.approx(-190.0)  # backward across week start


def test_satpos_finite_near_week_boundary():
    eph = Ephemeris(**{**EPH.__dict__, "toe": 604700.0, "toc": 604700.0})
    for t in (604790.0, 10.0, 90.0):
        x, y, z = satellite_position(eph, t)
        assert all(math.isfinite(v) for v in (x, y, z))
        assert 25.0e6 < math.sqrt(x * x + y * y + z * z) < 27.0e6


def test_clock_bias_polynomial_and_relativistic():
    b = satellite_clock_bias(EPH, EPH.toc, apply_tgd=False)
    assert b == pytest.approx(EPH.af0, abs=2e-8)  # af0 dominates; relativistic is tiny
    assert b != EPH.af0  # but the relativistic eccentricity term is present


def test_tgd_removed_for_single_frequency():
    b_no = satellite_clock_bias(EPH, EPH.toc, apply_tgd=False)
    b_tgd = satellite_clock_bias(EPH, EPH.toc, apply_tgd=True)
    assert b_no - b_tgd == pytest.approx(EPH.tgd)


def test_clock_drift_dominated_by_af1():
    dt = 3600.0
    b0 = satellite_clock_bias(EPH, EPH.toc, apply_tgd=False)
    b1 = satellite_clock_bias(EPH, EPH.toc + dt, apply_tgd=False)
    assert abs((b1 - b0) - EPH.af1 * dt) < 1e-8
