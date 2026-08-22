# Validation & cross-checks

Because a wrong correction silently degrades a receiver's position, every stage
is validated against **independent implementations** rather than only
self-consistency.

## Summary

| Stage | Independent cross-check |
|-------|-------------------------|
| Word parity ({gh}`rtcm3to2p3/parity.py`) | [RTKLIB](https://github.com/tomojitakasu/RTKLIB) `decode_word` (Hamming-table formulation), in-test, 200 randomised words |
| RTCM 2.3 output (Type 1 + Type 3) | [gpsd](https://gpsd.gitlab.io/gpsd/) [`gpsdecode`](https://gpsd.gitlab.io/gpsd/gpsdecode.html) (external, in CI) + an [RTKLIB](https://github.com/tomojitakasu/RTKLIB)-derived decoder + a word-aligned IS-GPS-200 parity validator, all in-test |
| RTCM 2.3 encoder | a real [u-blox 7](https://www.u-blox.com/en/product/neo-7-series) reports a DGPS fix from the output (hardware) |
| Satellite position ({gh}`rtcm3to2p3/ephemeris.py`) | [gnss_lib_py](https://github.com/Stanford-NavLab/gnss_lib_py) `find_sv_states` (Stanford NAV Lab), agreement ~2 mm |
| Satellite clock ({gh}`rtcm3to2p3/ephemeris.py`) | [gnss_lib_py](https://github.com/Stanford-NavLab/gnss_lib_py) `b_sv_m`, agreement to machine precision |
| Whole conversion ({gh}`rtcm3to2p3/dgps.py`) | live [AUSCORS](https://gnss.ga.gov.au/) feed: raw corrections cluster < ~50 m |
| NTRIP client + caster ({gh}`rtcm3to2p3/ntrip.py`) | [RTKLIB](https://github.com/tomojitakasu/RTKLIB) `str2str` (in CI); [BKG `bnc`](https://igs.bkg.bund.de/ntrip/bnc) and [wangkanai/caster](https://github.com/wangkanai/caster) (.NET) optional, run when the tool is present |

## Word parity

{gh}`tests/test_parity.py` transcribes [RTKLIB](https://github.com/tomojitakasu/RTKLIB)'s
`decode_word` (a Hamming-table + popcount formulation, algorithmically different
from our position-list masks) and requires our parity to agree over 200 randomised
words including the D30\* data-inversion case. This caught an early
parity-on-inverted-data bug.

## RTCM 2.3 output — three independent decoders

1. **[gpsd](https://gpsd.gitlab.io/gpsd/)
   [`gpsdecode`](https://gpsd.gitlab.io/gpsd/gpsdecode.html)** (external, separate
   codebase) decodes the encoder output and every field round-trips. Run in CI
   (`gpsd-clients` is installed there).
2. An **[RTKLIB](https://github.com/tomojitakasu/RTKLIB)-derived decoder** in
   {gh}`tests/test_rtcm2.py` (6-of-8 de-framing + Hamming parity + Type 1/Type 3
   field extraction) independently recovers every field.
3. A **word-aligned [IS-GPS-200](https://navcen.uscg.gov/sites/default/files/pdf/gps/IS-GPS-200N.pdf)
   Table 20-XIV parity validator** (`parity_violations` in
   {gh}`tests/test_rtcm2.py`) recomputes every word's six parity bits directly from
   the standard equations. Unlike a bit-by-bit resync (which can false-lock on a
   Type3→Type1 seam, as RTKLIB and gpsd both can), it cannot mis-align, so it
   proves the parity seed chains cleanly across interleaved message types.
4. A real [u-blox 7](https://www.u-blox.com/en/product/neo-7-series) achieves a
   differential fix — see {gh}`scripts/validate_ublox_hardware.py`.

:::{note}
gpsd's `isgps_parity()` has the D30\* data-inversion step commented out, so its
*cold sync* is content-dependent (e.g. it will not lock onto a stream that
*starts* with station id 0 or 2, though the content is valid RTCM2). Our RTKLIB
decoder and the u-blox hardware handle those cases; we emit a configurable
non-zero station id by default.
:::

## Satellite position and clock

{gh}`tests/test_ephemeris.py` compares our
[IS-GPS-200](https://navcen.uscg.gov/sites/default/files/pdf/gps/IS-GPS-200N.pdf) propagation to
[gnss_lib_py](https://github.com/Stanford-NavLab/gnss_lib_py)'s independent
implementation: frozen reference positions agree to ~2 mm, and the clock bias
(polynomial + relativistic + $T_{GD}$) matches gnss_lib_py's `b_sv_m` to machine
precision. Regenerate the references with {gh}`scripts/gen_satpos_reference.py`.

## Whole conversion — live

{gh}`scripts/validate_dgps_live.py` captures a live
[AUSCORS](https://gnss.ga.gov.au/) feed and confirms the raw per-satellite
corrections cluster tightly (they share the base receiver clock): any error in
pseudorange reconstruction, satellite position or clock would scatter them by
kilometres. {gh}`scripts/validate_service_live.py` runs the full service and
decodes its RTCM2.3 mount with [`gpsdecode`](https://gpsd.gitlab.io/gpsd/gpsdecode.html).

## NTRIP transport interoperability

{gh}`tests/test_interop.py` exercises our client and caster against separate,
non-Python NTRIP implementations. Each is skipped unless the tool is present, so
the default suite stays dependency-light:

* **[RTKLIB](https://github.com/tomojitakasu/RTKLIB) `str2str`** — pulls a mount from our caster,
  and serves a mount our client pulls (both directions). Enabled by installing
  RTKLIB (in CI).
* **[BKG NTRIP Client `bnc`](https://igs.bkg.bund.de/ntrip/bnc)** — headless
  `bnc --conf <file> --nw` subscribes to our caster and writes the raw stream,
  which we assert matches what we published. Enabled by installing `bnc`.
* **[wangkanai/caster](https://github.com/wangkanai/caster)** (.NET) — our client
  pulls a mount from a running instance. Launch it (e.g.
  `dotnet run --project <checkout>` listening on a port with a mount) and point
  the test at it with `WANGKANAI_CASTER=host:port/MOUNT pytest tests/test_interop.py`.
