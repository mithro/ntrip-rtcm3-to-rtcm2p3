# ntrip-rtcm3-to-rtcm2p3

An [NTRIP](https://software.rtcm-ntrip.org/) **receiver + LAN rebroadcaster** that
also **converts** an [RTCM](https://www.rtcm.org/publications) 3.x reference stream
into **RTCM 2.3** DGPS corrections, so that legacy single-band receivers that only
understand RTCM 2.3 (e.g. the [u-blox 7](https://www.u-blox.com/en/product/neo-7-series))
can benefit from a modern RTCM3-only correction network.

> Built incrementally with a full test suite; every conversion stage is
> cross-checked against independent implementations (see the Validation docs).

📖 **[Documentation](https://ntrip-rtcm3-to-rtcm2p3.readthedocs.io/)** (Read the Docs)

## Why

Modern correction networks (e.g. Geoscience Australia's
[AUSCORS](https://gnss.ga.gov.au/)) broadcast **RTCM 3.x** only. A u-blox 7 / NEO-7
accepts **RTCM 2.3** DGPS corrections but has no RTCM3 decoder. No off-the-shelf
tool converts RTCM3 → RTCM 2.3 ([RTKLIB](https://github.com/tomojitakasu/RTKLIB)'s
`str2str` can only *emit* RTCM3; the
[BKG NTRIP Client (BNC)](https://igs.bkg.bund.de/ntrip/bnc) only *decodes* RTCM2).
RTCM 2.3 Type 1 messages are
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

## Install

The packages are published as a signed apt repository per Debian suite:
trixie, forky and sid. They are `Architecture: all`, so the one build serves
every architecture. Put your suite's name in place of `trixie` below.

```sh
sudo install -d -m0755 /etc/apt/keyrings
curl -fsSL https://mith.ro/ntrip-rtcm3-to-rtcm2p3/ntrip-rtcm3-to-rtcm2p3.gpg | sudo tee /etc/apt/keyrings/ntrip-rtcm3-to-rtcm2p3.gpg > /dev/null
echo "deb [signed-by=/etc/apt/keyrings/ntrip-rtcm3-to-rtcm2p3.gpg] https://mith.ro/ntrip-rtcm3-to-rtcm2p3/trixie/ ./" \
  | sudo tee /etc/apt/sources.list.d/ntrip-rtcm3-to-rtcm2p3.list
sudo apt update
sudo apt install ntrip-rtcm3-to-rtcm2p3
```

The repository is signed with the key
`36E5 D845 2935 9CFE 8874  F1F1 44EA 5E20 5EE9 F42E`
(`gpg --show-keys /etc/apt/keyrings/ntrip-rtcm3-to-rtcm2p3.gpg` shows it).
It also carries `python3-pyrtcm` and `python3-pynmeagps`, which Debian doesn't
have, so apt finds every dependency.

`ntrip-rtcm3-to-rtcm2p3` is the systemd service; `python3-ntrip-rtcm3-to-rtcm2p3`
alone is the library and the command. Set the upstream caster, credentials and
bind addresses in `/etc/ntrip-rtcm3-to-rtcm2p3/env`, then
`sudo systemctl restart ntrip-rtcm3-to-rtcm2p3`. See the
[usage documentation](https://ntrip-rtcm3-to-rtcm2p3.readthedocs.io/usage.html).

## Development

```bash
uv run --extra dev pytest        # test suite
uv run --extra dev ruff check .  # lint
```

The `Debian packages` workflow runs the tests in each suite's container, then
builds the packages with
[mithro/apt-repo-action](https://github.com/mithro/apt-repo-action)'s shared
`build-deb` action, install-tests them (`packaging/install-test.sh`) and, from
`main`, publishes them. The version comes from `git describe` plus the suite's
`~deb<R>` (`0.1.0.post25~deb13`; nothing for sid). There is no committed
`debian/changelog`: the build writes one with just its own entry, and git
ignores it.

## License

Apache-2.0. See [LICENSE](LICENSE).
