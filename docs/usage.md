# Usage

## Install

From the Debian apt repository (published via GitHub Pages):

```bash
# Signing key
curl -fsSL https://mithro.github.io/ntrip-rtcm3-to-rtcm2p3/ntrip-rtcm3-to-rtcm2p3.gpg \
  | sudo tee /etc/apt/keyrings/ntrip-rtcm3-to-rtcm2p3.gpg > /dev/null
# apt source (flat repo at the site root)
echo "deb [signed-by=/etc/apt/keyrings/ntrip-rtcm3-to-rtcm2p3.gpg] \
  https://mithro.github.io/ntrip-rtcm3-to-rtcm2p3/ ./" \
  | sudo tee /etc/apt/sources.list.d/ntrip-rtcm3-to-rtcm2p3.list
sudo apt-get update && sudo apt-get install ntrip-rtcm3-to-rtcm2p3
```

The repo also carries `python3-pyrtcm` and `python3-pynmeagps` (not yet in the
main Debian archive), so `apt` resolves all dependencies from this one source.

Or with `pip`/`uv` from source:

```bash
uv pip install git+https://github.com/mithro/ntrip-rtcm3-to-rtcm2p3
```

## Running

```bash
ntrip-rtcm3-to-rtcm2p3 \
  --upstream-host ntrip.data.gnss.ga.gov.au --upstream-port 2101 \
  --upstream-user mithro --password-file ~/.auscors-passwd \
  --obs-mount ADDE00AUS0 --eph-mount BCEP00BKG0 \
  --listen 0.0.0.0 --listen-port 2101 --station-id 1023
```

This connects to the upstream caster, pulls the reference-station observations
(`--obs-mount`, which must include station position 1005/1006 and GPS MSM7 1077)
and the broadcast ephemeris (`--eph-mount`, carrying 1019), and serves two local
mounts:

* **`ADDE_RTCM3`** — the upstream RTCM3 relayed verbatim (for RTK-capable clients)
* **`ADDE_RTCM23`** — generated RTCM 2.3 Type 1 corrections (for RTCM 2.3 / DGPS
  receivers such as the u-blox 7)

The upstream password is read from a file (`--password-file`) or the
`NTRIP_UPSTREAM_PASSWORD` environment variable, never placed on the command line.

## Options

Run `ntrip-rtcm3-to-rtcm2p3 --help` for the full list. Key options:

`--listen`
: Comma-separated bind addresses. Bind only the LAN interfaces you want to serve
  (e.g. `10.1.10.1,10.1.90.1`) so the caster need not be exposed publicly.

`--station-id`
: The RTCM 2.3 reference-station id to emit (1..1023). It is an identifier only;
  the default (1023) is used rather than the upstream base's id because some
  casters send 0, and station id 0/2 trips gpsd's cold-sync.

`--upstream-tls`
: Connect to the upstream caster over TLS (port 443) instead of plain 2101.

## Deploying as a service

A `systemd` unit runs it as a dedicated user, reading the password from a
root-only file and binding the LAN interfaces. See the packaged
`ntrip-rtcm3-to-rtcm2p3.service`.

## Which receivers benefit

The u-blox 7 (and other RTCM-2.3-only receivers) apply the Type 1 corrections for
a **DGPS** (sub-metre-class) solution. Modern RTK receivers (u-blox F9P, …) should
consume the **RTCM3** passthrough mount directly instead.
