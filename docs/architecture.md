# Architecture

## Pipeline

```{mermaid}
flowchart TB
    subgraph UP["AUSCORS caster (upstream RTCM 3)"]
      direction LR
      OBS["obs mount<br/>1077 GPS MSM7 · 1005/1006 station"]
      EPH["eph mount<br/>1019 GPS ephemeris"]
    end

    OBS --> OC["NtripClient (obs)"]
    EPH --> EC["NtripClient (eph)"]

    subgraph CONV["Converter (service.py)"]
      direction TB
      DEC["decode → pseudoranges + station ECEF<br/>(rtcm3_input)"]
      EPHD["decode → Ephemeris<br/>(rtcm3_input)"]
      SAT["satellite position + clock<br/>(ephemeris)"]
      GEN["per-SV PRC / RRC / UDRE<br/>(dgps.DgpsGenerator)"]
      ENC["encode RTCM 2.3 Type 1 + Type 3<br/>(rtcm2.Rtcm2Encoder)"]
      EPHD --> SAT
      SAT --> GEN
      DEC --> GEN
      GEN --> ENC
    end

    OC -->|"feed_obs (parse)"| DEC
    EC -->|"feed_eph (parse)"| EPHD
    OC -.->|"verbatim copy"| M3
    ENC --> M23

    subgraph CAST["NtripCaster (LAN)"]
      direction LR
      M3["mount ADDE_RTCM3<br/>RTCM 3 verbatim passthrough"]
      M23["mount ADDE_RTCM23<br/>generated RTCM 2.3 Type 1 + 3"]
    end

    M3 --> LAN["LAN NTRIP clients"]
    M23 --> UBX["u-blox 7 rover"]
```

## The DGPS computation

For the full theoretical and numerical-accuracy walkthrough of every step below,
see {doc}`conversion`.

For each GPS satellite the reference station computes the difference between the
geometric range (from its known position to the satellite) and the measured
pseudorange, with the satellite clock removed:

```
raw_i = geometric_range(station, satellite_i) - pseudorange_i - c · dt_sv_i
```

* **Geometric range** uses the satellite position from broadcast ephemeris
  ([IS-GPS-200](https://www.gps.gov/technical/icwg/IS-GPS-200N.pdf),
  {gh}`rtcm3to2p3/ephemeris.py`) at the signal transmit time, with the Sagnac
  (Earth-rotation) correction.
* **`dt_sv`** is the satellite clock offset (af0/af1/af2 + relativistic term +
  L1 group delay).

`raw_i` is dominated by the base receiver's clock offset, which is common to all
satellites and would overflow the 16-bit RTCM field. It is estimated as the
per-epoch **median** over satellites and subtracted; the rover absorbs the removed
offset into its own clock solution. What remains — the per-satellite PRC — is the
tropo/iono/ephemeris error the rover needs, a few metres in magnitude.

The **RRC** (range-rate correction) is the rate of change of the *raw* correction
between epochs, with the common base-clock drift removed the same way.

## The RTCM 2.3 wire encoding

RTCM SC-104 v2.3 is a legacy bit-packed format ({gh}`rtcm3to2p3/rtcm2.py`):

* Messages are a continuous stream of **30-bit words** (24 data + 6 parity), with
  the D29\*/D30\* parity seed chaining across every word and every message.
* Parity follows [IS-GPS-200](https://www.gps.gov/technical/icwg/IS-GPS-200N.pdf)
  Table 20-XIV ({gh}`rtcm3to2p3/parity.py`); on D30\* the *transmitted* data bits
  are inverted while parity is computed on the source.
* Each 30-bit word is sent as five 6-bit groups, MSB first, each a byte
  `0x40 | reverse6(group)` (the "6-of-8" framing).

Type 1 carries two 24-bit header words (preamble, type, station id, modified
Z-count, sequence, length, health) followed by 40-bit satellite records (scale,
UDRE, PRN, PRC, RRC, IOD).

Type 3 (reference-station ARP) carries the same two header words followed by the
base ECEF X, Y, Z as 32-bit signed integers in units of 0.01 m (four data
words). It is emitted about once a minute so a rover can recover the base
position; it shares the one encoder with Type 1 so the parity seed keeps
chaining across the message boundary.

## Scope and limitations

* **GPS L1 C/A only.** Corrections are generated for GPS satellites from the
  1077 (GPS MSM7) observation message; GLONASS/Galileo/BeiDou observations in the
  upstream stream are relayed on the RTCM3 mount but not converted.
* **One reference station.** A single base (from the upstream 1005/1006) feeds
  the conversion; there is no multi-base networking or VRS.
* **RTCM 2.3 Type 1 and Type 3.** Type 1 (pseudorange corrections) and Type 3
  (reference-station ARP) are emitted; other RTCM 2.x message types are not.
* **Satellite selection.** Satellites are skipped when their ephemeris is stale
  (older than a configurable age), unhealthy (SV health flag set), or their raw
  correction is an implausible outlier.
