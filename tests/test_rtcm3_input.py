"""Tests for the pyrtcm-based RTCM3 field extraction.

pyrtcm messages are mocked with SimpleNamespace carrying the DFxxx attributes.
The 1019 values are a real (public) GPS broadcast-ephemeris record captured from
AUSCORS BCEP00BKG0; the correctness signals are the semicircle->radian
conversion (i0 = 0.3046 semicircles -> 0.957 rad = 55 deg) and that the resulting
orbit is physical. The MSM7 case is synthetic.
"""
import math
from types import SimpleNamespace

import pytest

from rtcm3to2p3.ephemeris import C, satellite_position
from rtcm3to2p3.rtcm3_input import parse_ephemeris, parse_gps_msm7, parse_station

# real 1019 (GPS PRN 1) field values from pyrtcm
_EPH_1019 = dict(
    DF009=1, DF076=384, DF093=489600, DF092=5153.595941543579,
    DF090=0.0018910535145550966, DF088=0.003662495408207178, DF087=1.4695160643896088e-09,
    DF095=0.03997285943478346, DF099=0.05384706100448966, DF097=0.3046126179397106,
    DF100=-2.6278712539351545e-09, DF079=3.4219738154206425e-11, DF089=2.089887857437134e-06,
    DF091=3.9637088775634766e-06, DF098=302.71875, DF086=38.71875,
    DF094=7.264316082000732e-08, DF096=-4.6566128730773926e-08, DF081=489600,
    DF084=0.000191432423889637, DF083=-9.208633855450898e-12, DF082=0.0,
    DF101=-8.847564458847046e-09, DF071=149,
    DF102=0, DF077=0,  # SV health (0 = healthy), URA index
)


def test_parse_station():
    msg = SimpleNamespace(DF025=-3924917.30, DF026=3462747.06, DF027=-3632703.99)
    assert parse_station(msg) == (-3924917.30, 3462747.06, -3632703.99)


def test_parse_ephemeris_units_and_orbit():
    eph = parse_ephemeris(SimpleNamespace(**_EPH_1019))
    assert eph.prn == 1
    assert eph.iode == 149
    assert eph.sqrt_a == pytest.approx(5153.5959, abs=1e-3)
    # semicircle -> radian conversion (0.3046 semicircles * pi = 0.957 rad = 55 deg)
    assert eph.i0 == pytest.approx(0.3046126 * math.pi, rel=1e-6)
    assert math.degrees(eph.i0) == pytest.approx(54.83, abs=0.1)
    assert eph.omega == pytest.approx(0.05384706 * math.pi, rel=1e-6)
    # C-terms and eccentricity are NOT semicircle-scaled
    assert eph.crc == 302.71875
    assert eph.ecc == _EPH_1019["DF090"]  # eccentricity passed through unchanged
    # resulting orbit is physical
    x, y, z = satellite_position(eph, eph.toe + 100.0)
    assert 25.0e6 < math.sqrt(x * x + y * y + z * z) < 27.0e6


def _msm7_mock():
    return SimpleNamespace(
        DF004=483948000,  # TOW ms
        # PRN_<nn> gives each satellite-mask slot's PRN (as pyrtcm does)
        PRN_01="001", DF397_01=67, DF398_01=0.9951171875,
        PRN_02="002", DF397_02=68, DF398_02=0.4736328125,
        PRN_03="003", DF397_03=255, DF398_03=0.0,  # PRN 3: invalid int ms -> dropped
        CELLPRN_01="001", CELLSIG_01="1C", DF405_01=0.00044626370072364807,
        CELLPRN_02="001", CELLSIG_02="2W", DF405_02=0.0005,  # L2, skipped
        CELLPRN_03="002", CELLSIG_03="1C", DF405_03=0.00047195330262184143,
        CELLPRN_04="003", CELLSIG_04="1C", DF405_04=0.0001,  # PRN 3 dropped (invalid rough)
    )


def test_parse_gps_msm7_reconstruction():
    tow, prs = parse_gps_msm7(_msm7_mock())
    assert tow == 483948.0
    ms_to_m = C * 1e-3
    assert set(prs) == {1, 2}  # PRN 3 dropped (DF397==255), L2 cell ignored
    assert prs[1] == pytest.approx((67 + 0.9951171875 + 0.00044626370072364807) * ms_to_m)
    assert prs[2] == pytest.approx((68 + 0.4736328125 + 0.00047195330262184143) * ms_to_m)
    # sanity: GPS pseudoranges are ~20 000-25 000 km
    assert all(19_000e3 < p < 26_000e3 for p in prs.values())


def test_parse_gps_msm7_masked_sat_without_cells():
    # PRN 2 is in the satellite mask (slot 2) but has NO signal cells (legal MSM).
    # The rough range for PRN 3 (slot 3) must still be read from slot 3, not
    # shifted onto slot 2's value by cell-order inference.
    msg = SimpleNamespace(
        DF004=483948000,
        PRN_01="001", DF397_01=70, DF398_01=0.0,
        PRN_02="002", DF397_02=80, DF398_02=0.0,  # PRN 2: no cell below
        PRN_03="003", DF397_03=90, DF398_03=0.0,
        CELLPRN_01="001", CELLSIG_01="1C", DF405_01=0.0,
        CELLPRN_02="003", CELLSIG_02="1C", DF405_02=0.0,
    )
    _, prs = parse_gps_msm7(msg)
    ms_to_m = C * 1e-3
    assert set(prs) == {1, 3}  # PRN 2 has no cell -> no pseudorange
    assert prs[1] == pytest.approx(70 * ms_to_m)
    assert prs[3] == pytest.approx(90 * ms_to_m)  # slot 3's 90 ms, NOT PRN 2's 80
