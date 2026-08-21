# ntrip-rtcm3-to-rtcm2p3

An NTRIP **receiver + LAN rebroadcaster** that also **converts** an RTCM 3.x
reference-station stream into **RTCM 2.3** DGPS corrections, so legacy
single-band receivers that only understand RTCM 2.3 (e.g. the u-blox 7 / NEO-7)
can benefit from a modern RTCM3-only correction network such as Geoscience
Australia's [AUSCORS](https://gnss.ga.gov.au/).

## Why this exists

Modern correction networks broadcast **RTCM 3.x** only. A u-blox 7 accepts
**RTCM 2.3** DGPS corrections but has no RTCM3 decoder, and no off-the-shelf tool
converts between them — `str2str` can only *emit* RTCM3, and BNC only *decodes*
RTCM2. RTCM 2.3 Type 1 messages are *derived* pseudorange corrections, not a
reformat: they must be computed from the base station's observations, its known
position, and satellite positions from broadcast ephemeris.

This project does that computation and re-serves both streams on the LAN.

## Verified end-to-end

The generated corrections were fed to a real **u-blox 7**, which reported a
**differential (DGPS) fix** — ground-truth proof the output is receiver-usable.
Every stage is additionally cross-checked against independent implementations
(gpsd's `gpsdecode`, RTKLIB, gnss_lib_py). See {doc}`validation`.

```{toctree}
:maxdepth: 2
:caption: Contents

usage
architecture
validation
api
```
