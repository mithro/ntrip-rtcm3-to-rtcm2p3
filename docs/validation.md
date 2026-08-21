# Validation & cross-checks

Because a wrong correction silently degrades a receiver's position, every stage
is validated against **independent implementations** rather than only
self-consistency.

## Summary

| Stage | Independent cross-check |
|-------|-------------------------|
| Word parity ({mod}`rtcm3to2p3.parity`) | RTKLIB `decode_word` (Hamming-table formulation), in-test, 200 randomised words |
| RTCM 2.3 encoder ({mod}`rtcm3to2p3.rtcm2`) | gpsd **`gpsdecode`** (external process, in CI) + an RTKLIB-derived decoder in-test |
| RTCM 2.3 encoder | a real **u-blox 7** reports a DGPS fix from the output (hardware) |
| Satellite position ({mod}`rtcm3to2p3.ephemeris`) | **gnss_lib_py** `find_sv_states` (Stanford NAV Lab), agreement ~2 mm |
| Whole conversion ({mod}`rtcm3to2p3.dgps`) | live AUSCORS feed: raw corrections cluster < ~10 m |

## Word parity

`tests/test_parity.py` transcribes RTKLIB's `decode_word` (a Hamming-table +
popcount formulation, algorithmically different from our position-list masks) and
requires our parity to agree over 200 randomised words including the D30\*
data-inversion case. This caught an early parity-on-inverted-data bug.

## RTCM 2.3 output — three independent decoders

1. **gpsd `gpsdecode`** (external, separate codebase) decodes the encoder output
   and every field round-trips. Run in CI (`gpsd-clients` is installed there).
2. An **RTKLIB-derived decoder** in `tests/test_rtcm2.py` (6-of-8 de-framing +
   Hamming parity + Type 1 field extraction) independently recovers every field.
3. A real **u-blox 7** achieves a differential fix — see
   `scripts/validate_ublox_hardware.py`.

:::{note}
gpsd's `isgps_parity()` has the D30\* data-inversion step commented out, so its
*cold sync* is content-dependent (e.g. it will not lock onto a stream that
*starts* with station id 0 or 2, though the content is valid RTCM2). Our RTKLIB
decoder and the u-blox hardware handle those cases; we emit a configurable
non-zero station id by default.
:::

## Satellite position

`tests/test_ephemeris.py` compares our IS-GPS-200 propagation to gnss_lib_py's
independent implementation; frozen reference values agree to ~2 mm. Regenerate
with `scripts/gen_satpos_reference.py`.

## Whole conversion — live

`scripts/validate_dgps_live.py` captures a live AUSCORS feed and confirms the raw
per-satellite corrections cluster tightly (they share the base receiver clock):
any error in pseudorange reconstruction, satellite position or clock would
scatter them by kilometres. `scripts/validate_service_live.py` runs the full
service and decodes its RTCM2.3 mount with `gpsdecode`.
