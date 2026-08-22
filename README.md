# ntrip-rtcm3-to-rtcm2p3

An NTRIP **receiver + LAN rebroadcaster** that also **converts** an RTCM 3.x
reference stream into **RTCM 2.3** DGPS corrections, so that legacy single-band
receivers that only understand RTCM 2.3 (e.g. u-blox 7) can benefit from a modern
RTCM3-only correction network.

> Built incrementally with a full test suite; every conversion stage is
> cross-checked against independent implementations (see the Validation docs).

📖 **[Documentation](https://ntrip-rtcm3-to-rtcm2p3.readthedocs.io/)** (Read the Docs)

## Why

Modern correction networks (e.g. Geoscience Australia's AUSCORS) broadcast
**RTCM 3.x** only. A u-blox 7 / NEO-7 accepts **RTCM 2.3** DGPS corrections but has
no RTCM3 decoder. No off-the-shelf tool converts RTCM3 → RTCM 2.3 (`str2str` can
only *emit* RTCM3; BNC only *decodes* RTCM2). RTCM 2.3 Type 1 messages are
*derived* pseudorange corrections, not a reformat — they must be computed from the
base station's observations, its known position, and satellite positions from
broadcast ephemeris.

## Pipeline

```
        obs + base position                 broadcast ephemeris
   (RTCM3 MSM7 1077, 1006)                 (RTCM3 1019, separate mount)
             │                                       │
             ▼                                       ▼
      decode pseudoranges  ───────────────►  satellite position + clock
             │                                       │
             └───────────────┬───────────────────────┘
                             ▼
                   per-SV PRC / RRC  (DGPS reference-station math)
                             ▼
                   encode RTCM 2.3 Type 1 / 3 (parity, IOD, scaling)
                             ▼
              serve as a local NTRIP mount alongside the raw RTCM3
```

## Development

```bash
uv run --extra dev pytest        # test suite
uv run --extra dev ruff check .  # lint
```

## License

Apache-2.0. See [LICENSE](LICENSE).
