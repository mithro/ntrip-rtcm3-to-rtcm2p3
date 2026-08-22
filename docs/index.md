# ntrip-rtcm3-to-rtcm2p3

An [NTRIP](https://software.rtcm-ntrip.org/) **receiver + LAN rebroadcaster** that
also **converts** an [RTCM](https://www.rtcm.org/publications) 3.x
reference-station stream into **RTCM 2.3** DGPS corrections, so legacy
single-band receivers that only understand RTCM 2.3 (e.g. the
[u-blox 7](https://www.u-blox.com/en/product/neo-7-series) / NEO-7) can benefit
from a modern RTCM3-only correction network such as Geoscience Australia's
[AUSCORS](https://gnss.ga.gov.au/).

## Why this exists

Modern correction networks broadcast **RTCM 3.x** only. A
[u-blox 7](https://www.u-blox.com/en/product/neo-7-series) accepts **RTCM 2.3**
DGPS corrections but has no RTCM3 decoder, and no off-the-shelf tool converts
between them — [RTKLIB](https://github.com/tomojitakasu/RTKLIB)'s `str2str` can only *emit*
RTCM3, and the [BKG NTRIP Client (BNC)](https://igs.bkg.bund.de/ntrip/bnc) only
*decodes* RTCM2. RTCM 2.3 Type 1 messages are *derived* pseudorange corrections,
not a reformat: they must be computed from the base station's observations, its
known position, and satellite positions from broadcast ephemeris.

This project does that computation and re-serves both streams on the LAN.

## Verified end-to-end

Every conversion stage is cross-checked in the automated test suite against
independent implementations — [gpsd](https://gpsd.gitlab.io/gpsd/)'s
[`gpsdecode`](https://gpsd.gitlab.io/gpsd/gpsdecode.html) and an
[RTKLIB](https://github.com/tomojitakasu/RTKLIB)-derived decoder (RTCM 2.3 output),
[gnss_lib_py](https://github.com/Stanford-NavLab/gnss_lib_py) (satellite position
**and** clock), and RTKLIB's `str2str` (NTRIP transport). As a hardware check,
feeding the generated corrections to a real
[u-blox 7](https://www.u-blox.com/en/product/neo-7-series) produced a differential
(DGPS) fix; that step is manual (it needs the receiver on a serial port) and is
reproduced by {gh}`scripts/validate_ublox_hardware.py`, not run in CI. See
{doc}`validation`.

```{toctree}
:maxdepth: 2
:caption: Contents

usage
architecture
conversion
validation
api
```
