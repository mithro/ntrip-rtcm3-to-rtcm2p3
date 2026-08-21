#!/usr/bin/env python3
"""End-to-end sanity check of the conversion math against a live AUSCORS feed.

Captures ADDE00AUS0 (station 1006 + GPS obs 1077) and BCEP00BKG0 (ephemeris
1019), computes the raw per-satellite corrections, and checks they cluster: the
raw values share the base receiver clock, so their spread about the median is the
tropo/iono/ephemeris residual (expected < ~50 m). A scattered result (km) means a
bug in pseudorange reconstruction, satellite position, or clock.

Run:  uv run --with pyrtcm python scripts/validate_dgps_live.py
"""
import base64
import io
import os
import socket
import time
from statistics import median

from pyrtcm import RTCMReader

from rtcm3to2p3.dgps import DgpsGenerator, raw_correction
from rtcm3to2p3.rtcm3_input import parse_ephemeris, parse_gps_msm7, parse_station

USER, PW = "mithro", open(os.path.expanduser("~/.gpsd-auscors-ntrip-passwd")).read().strip()


def capture(mount, seconds):
    tok = base64.b64encode(f"{USER}:{PW}".encode()).decode()
    s = socket.create_connection(("ntrip.data.gnss.ga.gov.au", 2101), timeout=20)
    s.sendall((f"GET /{mount} HTTP/1.1\r\nHost: h\r\nNtrip-Version: Ntrip/2.0\r\n"
               f"User-Agent: x\r\nAuthorization: Basic {tok}\r\n"
               "Connection: close\r\n\r\n").encode())
    s.settimeout(seconds)
    buf = b""
    while b"\r\n\r\n" not in buf:
        buf += s.recv(1)
    data = bytearray()
    t0 = time.time()
    while time.time() - t0 < seconds:
        try:
            d = s.recv(4096)
        except TimeoutError:
            break
        if not d:
            break
        data += d
    s.close()
    return bytes(data)


def main():
    gen = DgpsGenerator()
    adde = capture("ADDE00AUS0", 14)
    for _, m in RTCMReader(io.BytesIO(adde)):
        if m is None:
            continue
        if m.identity in ("1005", "1006"):
            gen.set_station(parse_station(m))
    for _, m in RTCMReader(io.BytesIO(capture("BCEP00BKG0", 8))):
        if m is not None and m.identity == "1019":
            gen.add_ephemeris(parse_ephemeris(m))

    print(f"station={gen.base}  ephemerides={sorted(gen.ephemerides)}")
    # last full GPS obs epoch
    last = None
    for _, m in RTCMReader(io.BytesIO(adde)):
        if m is not None and m.identity == "1077":
            last = parse_gps_msm7(m)
    tow, prs = last
    raw = {prn: raw_correction(gen.ephemerides[prn], gen.base, tow, pr)
           for prn, pr in prs.items() if prn in gen.ephemerides}
    med = median(raw.values())
    print(f"\ntow={tow}  sats={len(raw)}")
    print(f"{'PRN':>4} {'pseudorange_km':>15} {'raw_m':>12} {'PRC=raw-median':>16}")
    spread = []
    for prn in sorted(raw):
        prc = raw[prn] - med
        spread.append(abs(prc))
        print(f"{prn:>4} {prs[prn] / 1000:15.3f} {raw[prn]:12.2f} {prc:16.2f}")
    print(f"\nmedian raw (base clock) = {med:.1f} m")
    print(f"max |PRC| = {max(spread):.2f} m   (expect < ~50 m if the chain is correct)")


if __name__ == "__main__":
    main()
