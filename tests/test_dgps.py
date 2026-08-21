"""Tests for the DGPS correction generator.

Uses an *exact* synthetic construction: for a chosen "extra" error injected into a
satellite's pseudorange, ``raw_correction`` provably returns ``-extra`` (the
geometric range and clock cancel), so the generator must output
``PRC_i = -(extra_i - median(extra))``. That pins down the common-clock removal
and PRC arithmetic without any real data. The whole chain is additionally
validated end-to-end against a live AUSCORS feed by
``scripts/validate_dgps_live.py`` (residuals cluster < ~50 m).
"""
from statistics import median

import pytest

from dataclasses import replace

from rtcm3to2p3.dgps import DgpsGenerator, _geometric_range, raw_correction, udre_index
from rtcm3to2p3.ephemeris import C, Ephemeris, satellite_clock_bias, satellite_position

BASE = (-3924917.30, 3462747.06, -3632703.99)  # ADDE (Adelaide) ECEF, metres

_BASE_EPH = dict(
    week=2300, toe=100000.0, sqrt_a=5153.65, ecc=0.005, delta_n=4.5e-9,
    omega=0.9, i0=0.96, omega_dot=-8.0e-9, idot=1.0e-10,
    cuc=1.0e-6, cus=8.0e-6, crc=250.0, crs=-30.0, cic=-1.0e-7, cis=1.2e-7,
    toc=100000.0, af0=1.0e-4, af1=1.0e-12, af2=0.0, tgd=5.0e-9,
)


def _ephemerides():
    """A handful of distinct GPS satellites (varied m0/omega0 -> distinct geometry)."""
    ephs = {}
    for k, prn in enumerate((3, 11, 19, 27, 31)):
        ephs[prn] = Ephemeris(prn=prn, m0=0.3 + 0.9 * k, omega0=-0.5 + 1.1 * k,
                              iode=40 + prn, **_BASE_EPH)
    return ephs


def _perfect_pr(eph, tow, extra):
    """Pseudorange for which raw_correction returns exactly -extra."""
    pr = 22_000e3
    for _ in range(4):
        t_tx = tow - pr / C
        sat = satellite_position(eph, t_tx)
        dt = satellite_clock_bias(eph, t_tx, apply_tgd=True)
        pr = _geometric_range(sat, BASE) - C * dt + extra
    return pr


def test_geometric_range_includes_sagnac():
    sat = (15_000_000.0, -8_000_000.0, 20_000_000.0)
    plain = ((sat[0] - BASE[0]) ** 2 + (sat[1] - BASE[1]) ** 2 + (sat[2] - BASE[2]) ** 2) ** 0.5
    r = _geometric_range(sat, BASE)
    assert r != plain  # Sagnac term applied
    assert abs(r - plain) < 100.0  # but small (metres)


def test_raw_correction_returns_negative_extra():
    eph = _ephemerides()[3]
    tow = 100200.0
    for extra in (0.0, 5.0, -12.5, 40.0):
        pr = _perfect_pr(eph, tow, extra)
        assert raw_correction(eph, BASE, tow, pr) == pytest.approx(-extra, abs=1e-3)


def test_generator_recovers_injected_errors():
    ephs = _ephemerides()
    tow = 100200.0
    extras = {3: 0.0, 11: 5.0, 19: -8.0, 27: 12.0, 31: -3.0}
    # common base-clock offset added to every pseudorange must NOT affect the PRCs
    clock = 1234.0
    prs = {prn: _perfect_pr(ephs[prn], tow, extras[prn] + clock) for prn in ephs}

    gen = DgpsGenerator()
    gen.set_station(BASE)
    for eph in ephs.values():
        gen.add_ephemeris(eph)
    corr = {c.prn: c for c in gen.corrections(tow, prs)}

    assert set(corr) == set(ephs)
    med = median(extras.values())
    for prn, c in corr.items():
        assert c.prc == pytest.approx(-(extras[prn] - med), abs=1e-2)
        assert c.iod == ephs[prn].iode


def test_rrc_from_consecutive_epochs():
    ephs = _ephemerides()
    gen = DgpsGenerator()
    gen.set_station(BASE)
    for eph in ephs.values():
        gen.add_ephemeris(eph)
    # inject an error that grows linearly in time for PRN 11 only
    def prs_at(tow, err11):
        extras = {3: 0.0, 11: err11, 19: 0.0, 27: 0.0, 31: 0.0}
        return {p: _perfect_pr(ephs[p], tow, extras[p]) for p in ephs}

    gen.corrections(100200.0, prs_at(100200.0, 0.0))
    c2 = {c.prn: c for c in gen.corrections(100210.0, prs_at(100210.0, 20.0))}
    # PRC(11) changed by ~ -(20 - median-shift) over 10 s
    assert c2[11].rrc != 0.0
    assert abs(c2[11].rrc) > abs(c2[3].rrc)


def test_no_station_returns_empty():
    gen = DgpsGenerator()
    gen.add_ephemeris(_ephemerides()[3])
    assert gen.corrections(100200.0, {3: 22e6}) == []


def test_missing_ephemeris_is_skipped():
    ephs = _ephemerides()
    gen = DgpsGenerator()
    gen.set_station(BASE)
    gen.add_ephemeris(ephs[3])
    tow = 100200.0
    prs = {3: _perfect_pr(ephs[3], tow, 0.0), 99: 22e6}  # no eph for PRN 99
    corr = gen.corrections(tow, prs)
    assert [c.prn for c in corr] == [3]


def test_unhealthy_ephemeris_is_skipped():
    ephs = _ephemerides()
    tow = 100200.0
    prs = {p: _perfect_pr(ephs[p], tow, 0.0) for p in ephs}
    gen = DgpsGenerator()
    gen.set_station(BASE)
    for prn, eph in ephs.items():
        gen.add_ephemeris(replace(eph, health=1) if prn == 11 else eph)
    prns = {c.prn for c in gen.corrections(tow, prs)}
    assert 11 not in prns and prns == set(ephs) - {11}


def test_stale_ephemeris_is_skipped():
    ephs = _ephemerides()  # toe = 100000
    tow = 100200.0  # 200 s after toe
    prs = {p: _perfect_pr(ephs[p], tow, 0.0) for p in ephs}
    gen = DgpsGenerator(max_iod_age_s=100.0)  # 200 s > 100 s -> all stale
    gen.set_station(BASE)
    for eph in ephs.values():
        gen.add_ephemeris(eph)
    assert gen.corrections(tow, prs) == []
    # with a generous window the same epoch yields corrections
    gen2 = DgpsGenerator(max_iod_age_s=7200.0)
    gen2.set_station(BASE)
    for eph in ephs.values():
        gen2.add_ephemeris(eph)
    assert {c.prn for c in gen2.corrections(tow, prs)} == set(ephs)


def test_residual_outlier_is_dropped():
    ephs = _ephemerides()
    tow = 100200.0
    extras = {3: 0.0, 11: 0.0, 19: 0.0, 27: 0.0, 31: 1000.0}  # PRN 31 way off
    prs = {p: _perfect_pr(ephs[p], tow, extras[p]) for p in ephs}
    gen = DgpsGenerator(max_residual_m=100.0)
    gen.set_station(BASE)
    for eph in ephs.values():
        gen.add_ephemeris(eph)
    prns = {c.prn for c in gen.corrections(tow, prs)}
    assert 31 not in prns and prns == set(ephs) - {31}


def test_udre_index_buckets():
    assert udre_index(0.5) == 0
    assert udre_index(-1.0) == 0
    assert udre_index(3.9) == 1
    assert udre_index(7.5) == 2
    assert udre_index(-50.0) == 3


def test_corrections_set_udre_from_magnitude():
    ephs = _ephemerides()
    tow = 100200.0
    # PRN 27 gets a ~6 m residual (relative to the others) -> UDRE bucket 2
    extras = {3: 0.0, 11: 0.0, 19: 0.0, 27: 6.0, 31: 0.0}
    prs = {p: _perfect_pr(ephs[p], tow, extras[p]) for p in ephs}
    gen = DgpsGenerator()
    gen.set_station(BASE)
    for eph in ephs.values():
        gen.add_ephemeris(eph)
    corr = {c.prn: c for c in gen.corrections(tow, prs)}
    for prn, c in corr.items():
        assert c.udre == udre_index(c.prc)
    assert corr[27].udre == 2
