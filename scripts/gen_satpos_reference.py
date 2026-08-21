#!/usr/bin/env python3
"""Regenerate the independent satellite-position reference in tests/test_ephemeris.py.

Uses gnss_lib_py (Stanford NAV Lab) ``find_sv_states`` as an independent
implementation of the IS-GPS-200 algorithm, and prints the ECEF positions plus
the delta versus our :func:`rtcm3to2p3.ephemeris.satellite_position`.

Run:  uv run --with gnss_lib_py python scripts/gen_satpos_reference.py
"""
import gnss_lib_py as glp
import numpy as np
from gnss_lib_py.utils.sv_models import find_sv_states

from rtcm3to2p3.ephemeris import Ephemeris, satellite_position

EPH = Ephemeris(
    prn=5, week=2300, toe=100000.0,
    sqrt_a=5153.65, ecc=0.005, m0=0.3, delta_n=4.5e-9,
    omega0=-0.5, omega=0.9, i0=0.96, omega_dot=-8.0e-9, idot=1.0e-10,
    cuc=1.0e-6, cus=8.0e-6, crc=250.0, crs=-30.0, cic=-1.0e-7, cis=1.2e-7,
    toc=100000.0, af0=1.0e-4, af1=1.0e-12, af2=0.0, tgd=5.0e-9, iode=42,
)
TIMES = (100200.0, 99000.0, 101800.0)

_FIELD_MAP = {
    "gps_week": "week", "t_oe": "toe", "t_oc": "toc", "e": "ecc", "sqrtA": "sqrt_a",
    "M_0": "m0", "deltaN": "delta_n", "omega": "omega", "Omega_0": "omega0",
    "OmegaDot": "omega_dot", "i_0": "i0", "IDOT": "idot", "C_uc": "cuc", "C_us": "cus",
    "C_rc": "crc", "C_rs": "crs", "C_ic": "cic", "C_is": "cis",
    "SVclockBias": "af0", "SVclockDrift": "af1", "SVclockDriftRate": "af2", "TGD": "tgd",
}


def _navdata(eph):
    nd = glp.NavData()
    nd["gnss_id"] = np.array(["gps"])
    nd["sv_id"] = np.array([eph.prn])
    for glp_field, attr in _FIELD_MAP.items():
        nd[glp_field] = np.array([float(getattr(eph, attr))])
    return nd


def main():
    nd = _navdata(EPH)
    print("# frozen into tests/test_ephemeris.py::_GLP_REF")
    for t in TIMES:
        millis = (EPH.week * 604800 + t) * 1000.0
        sv = find_sv_states(np.array([millis]), nd)
        ref = tuple(float(np.ravel(sv[k])[0]) for k in ("x_sv_m", "y_sv_m", "z_sv_m"))
        mine = satellite_position(EPH, t)
        diff = np.linalg.norm(np.array(mine) - np.array(ref))
        print(f"    {t}: {tuple(round(v, 4) for v in ref)},   # |diff|={diff:.2e} m")


if __name__ == "__main__":
    main()
