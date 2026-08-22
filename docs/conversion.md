# The RTCM 3.x → RTCM 2.3 conversion, in detail

This page is a complete walkthrough of how this project turns an RTCM 3.x
reference-station stream into RTCM 2.3 Type 1 pseudorange corrections (and Type 3
station coordinates) that a legacy single-frequency receiver such as a u-blox 7
can use for a **DGPS** (Differential GPS) position fix. It is split into a
**theoretical** part (the geodesy and the equations) and a **practical** part
(the numerical types and the order of operations that keep the result accurate to
the few-millimetre level set by the RTCM quantisation step — §2.8 — and free of
*accumulated* rounding error).

Every external claim is linked to a primary or high-quality secondary source, and
every reference to a file or symbol in this project links to its location on
GitHub.

## Why a conversion is even needed

Modern correction networks — e.g. Geoscience Australia's
[AUSCORS](https://www.ga.gov.au/scientific-topics/positioning-navigation/geodesy/auscors)
— broadcast **RTCM 3.x** only. A u-blox 7 (and other legacy single-band
receivers) accepts **RTCM 2.3** Type 1 DGPS corrections but has no RTCM 3 decoder.
No off-the-shelf tool converts between them: RTKLIB's
[`str2str`](https://www.rtklib.com/) only *relays/encodes* RTCM 3, and
[BNC](https://igs.bkg.bund.de/ntrip/bnc) only *decodes* RTCM 2. The reason is
that an RTCM 2.3 Type 1 message is not a re-packaging of RTCM 3 fields — it is a
**derived** quantity that must be *computed* from three separate inputs:

* the reference station's raw GPS observations (RTCM 3 message 1077, GPS MSM7 —
  "Multiple Signal Message type 7"),
* the station's surveyed position (RTCM 3 message 1005 or 1006), and
* the GPS broadcast ephemeris (RTCM 3 message 1019).

The decode of those three messages lives in
{gh}`rtcm3to2p3/rtcm3_input.py`; the maths below lives in
{gh}`rtcm3to2p3/ephemeris.py` and {gh}`rtcm3to2p3/dgps.py`; the RTCM 2.3 bit-level
encoder lives in {gh}`rtcm3to2p3/rtcm2.py` and {gh}`rtcm3to2p3/parity.py`.

```{mermaid}
flowchart LR
    OBS["obs mount (RTCM 3)<br/>1077 MSM7 · 1005/1006 station"]
    EPH["eph mount (RTCM 3)<br/>1019 ephemeris"]

    OBS --> PR["L1 C/A pseudoranges<br/>{PRN: metres}"]
    OBS --> S["station ECEF"]
    EPH --> K["Keplerian ephemeris"]

    K --> SAT["satellite position<br/>+ clock bias"]
    PR --> D
    S --> D
    SAT --> D["DGPS generator<br/>PRC · RRC · UDRE · IOD"]

    D --> ENC["RTCM 2.3 encoder<br/>(shared parity seed)"]
    S --> ENC
    ENC --> T1["Type 1<br/>corrections"]
    ENC --> T3["Type 3<br/>base position"]
    T1 --> O["LAN NTRIP mount<br/>→ u-blox 7"]
    T3 --> O
```

*The stream is carried over [NTRIP](https://igs.org/wg/ntrip/), the standard
HTTP-based transport for RTCM. The acronyms in the diagram (PRC, RRC, UDRE, IOD,
ECEF, PRN) are all defined in Part 1 below.*

---

## Part 1 — the theory

### 1.1 The pseudorange observation model

For satellite $i$ a receiver measures a *pseudorange* $P_i$ — the signal's
measured travel time multiplied by the speed of light. It is an *apparent*
distance, because the clocks at the two ends are imperfect, so it differs from the
true geometric range $\rho_i$ by a sum of physical error terms
([ESA Navipedia, *Combination of GNSS
Measurements*](https://gssc.esa.int/navipedia/index.php/Combination_of_GNSS_Measurements)):

$$
P_i = \rho_i + c\,(\delta t_{\text{rcv}} - \delta t^{(i)}_{\text{sv}})
      + I_i + T_i + \varepsilon_i
$$

where $c$ is the speed of light, $\delta t_{\text{rcv}}$ the receiver clock
offset, $\delta t^{(i)}_{\text{sv}}$ the satellite clock offset, $I_i$ the
ionospheric delay, $T_i$ the tropospheric delay, and $\varepsilon_i$ multipath +
noise.

A DGPS **reference station** knows its own position, so it can compute $\rho_i$
independently and form the *pseudorange correction* (PRC): the quantity a rover
should add to its own measured pseudorange to cancel the errors that are common
to both receivers at that moment ($\delta t^{(i)}_{\text{sv}}$, $I_i$, $T_i$, and
ephemeris error). This is the classical local-area DGPS technique described in
[RTCM 10402.3](https://www.rtcm.org/publications) and
[Misra & Enge, *Global Positioning System: Signals, Measurements, and
Performance*](https://gpstextbook.com/).

The base station's own clock offset $\delta t_{\text{rcv}}$ is **not** common to
the rover, but it is identical across all satellites in a single epoch, so it can
be estimated and removed as a lump (see §1.6).

### 1.2 Satellite position from the broadcast ephemeris

The geometric range needs the satellite's position at the instant it *transmitted*
the signal the base received. The broadcast ephemeris (RTCM 3 message 1019,
decoded to {gh}`rtcm3to2p3/ephemeris.py` `Ephemeris`) is a set of quasi-Keplerian
orbital elements; the position algorithm is the "user algorithm for ephemeris
determination" in [IS-GPS-200](https://www.gps.gov/technical/icwg/IS-GPS-200N.pdf)
§20.3.3.4.3.1 (Table 20-IV), reproduced here as implemented in
{gh}`rtcm3to2p3/ephemeris.py` (`satellite_position`). It is cross-checked in the
test suite against [gnss_lib_py](https://github.com/Stanford-NavLab/gnss_lib_py)'s
independent implementation (agreement ≈ 2 mm) — see {doc}`validation`.

With the IS-GPS-200 value $\mu = 3.986005\times10^{14}\ \mathrm{m^3/s^2}$ (the GM
constant *mandated* for the GPS ephemeris user algorithm — subtly different from
the modern WGS-84 GM, and using the WGS-84 value here would be an error) and the
WGS-84 Earth-rotation rate $\dot\Omega_e = 7.2921151467\times10^{-5}\ \mathrm{rad/s}$
([IS-GPS-200](https://www.gps.gov/technical/icwg/IS-GPS-200N.pdf) Table 20-IV),
each line below feeds the next:

$$
\begin{aligned}
a &= (\sqrt{a})^2 & &\text{semi-major axis} \\
t_k &= t - t_{oe} & &\text{time from ephemeris epoch (week-corrected, §2.3)} \\
n &= \sqrt{\mu/a^3} + \Delta n & &\text{corrected mean motion} \\
M_k &= M_0 + n\,t_k & &\text{mean anomaly} \\
E_k &= M_k + e\sin E_k & &\text{Kepler's equation (iterate, §2.4)} \\
\nu_k &= \operatorname{atan2}\!\big(\sqrt{1-e^2}\sin E_k,\ \cos E_k - e\big) & &\text{true anomaly} \\
\phi_k &= \nu_k + \omega & &\text{argument of latitude}
\end{aligned}
$$

then the second-harmonic corrections and the in-plane coordinates:

$$
\begin{aligned}
u_k &= \phi_k + C_{us}\sin 2\phi_k + C_{uc}\cos 2\phi_k & &\text{corrected argument of latitude} \\
r_k &= a(1 - e\cos E_k) + C_{rs}\sin 2\phi_k + C_{rc}\cos 2\phi_k & &\text{corrected orbital radius} \\
i_k &= i_0 + \dot i\,t_k + C_{is}\sin 2\phi_k + C_{ic}\cos 2\phi_k & &\text{corrected inclination} \\
\Omega_k &= \Omega_0 + (\dot\Omega - \dot\Omega_e)\,t_k - \dot\Omega_e\,t_{oe} & &\text{corrected node longitude}
\end{aligned}
$$

and finally the Earth-fixed (ECEF) position, rotating the in-plane point
$(x'_k, y'_k)$ out through the inclination and node:

$$
\begin{aligned}
x'_k &= r_k\cos u_k, \quad y'_k = r_k\sin u_k & &\text{position in the orbital plane} \\
x &= x'_k\cos\Omega_k - y'_k\cos i_k\sin\Omega_k \\
y &= x'_k\sin\Omega_k + y'_k\cos i_k\cos\Omega_k \\
z &= y'_k\sin i_k
\end{aligned}
$$

See also [ESA Navipedia, *GPS and Galileo Satellite Coordinates
Computation*](https://gssc.esa.int/navipedia/index.php/GPS_and_Galileo_Satellite_Coordinates_Computation)
for a step-by-step derivation of the same algorithm.

### 1.3 Satellite clock correction

The satellite clock offset removes the $\delta t^{(i)}_{\text{sv}}$ term. It is a
quadratic polynomial about the clock reference time $t_{oc}$ plus a relativistic
eccentricity term and, for a single-frequency L1 user, the group delay $T_{GD}$
([IS-GPS-200](https://www.gps.gov/technical/icwg/IS-GPS-200N.pdf) §20.3.3.3.3.1;
[ESA Navipedia, *Relativistic Clock
Correction*](https://gssc.esa.int/navipedia/index.php/Relativistic_Clock_Correction)):

$$
\delta t_{\text{sv}} = a_{f0} + a_{f1}(t - t_{oc}) + a_{f2}(t - t_{oc})^2
  + \underbrace{F\,e\,\sqrt{a}\,\sin E_k}_{\text{relativistic}} - T_{GD}
$$

with $F = -2\sqrt{\mu}/c^2 = -4.442807633\times10^{-10}\ \mathrm{s/\sqrt m}$. This
is {gh}`rtcm3to2p3/ephemeris.py` `satellite_clock_bias`, cross-checked to machine
precision against gnss_lib_py's `b_sv_m` (which likewise applies $T_{GD}$) — see
{doc}`validation`. Applying $T_{GD}$ is correct precisely because the target
rover is a single-frequency receiver using **L1 C/A** — the civilian
coarse/acquisition code on the GPS L1 carrier
([IS-GPS-200](https://www.gps.gov/technical/icwg/IS-GPS-200N.pdf) §20.3.3.3.3.2).

### 1.4 Geometric range and the Sagnac correction

The geometric range is the Euclidean distance between the satellite (at transmit
time, in the ECEF frame *at that instant*) and the known base station, plus a
correction for the Earth's rotation during the signal's flight — the **Sagnac /
earth-rotation** term ([ESA Navipedia, *Sagnac Effect* and *Emission Time
Computation*](https://gssc.esa.int/navipedia/index.php/Emission_Time_Computation)):

$$
\rho_i = \lVert \mathbf{r}^{(i)}_{\text{sat}} - \mathbf{r}_{\text{base}} \rVert
  + \frac{\dot\Omega_e}{c}\,\big(x_{\text{sat}}\,y_{\text{base}}
    - y_{\text{sat}}\,x_{\text{base}}\big)
$$

This is `_geometric_range` in {gh}`rtcm3to2p3/dgps.py`. The Sagnac term is only a
few metres but omitting it would bias every correction.

### 1.5 Forming the raw correction

Per satellite, the base station's *raw* correction (before removing its own clock)
is the geometric range minus the measured pseudorange minus the modelled
satellite-clock range — `raw_correction` in {gh}`rtcm3to2p3/dgps.py`:

$$
\text{raw}_i = \rho_i - P_i - c\,\delta t^{(i)}_{\text{sv}}
$$

Substituting the model of §1.1 shows $\text{raw}_i \approx -\,c\,\delta
t_{\text{rcv}} - (I_i + T_i + \varepsilon_i + \text{ephemeris error})$: it is
dominated by the base receiver clock (common to all satellites) with the small
per-satellite atmospheric/ephemeris residual on top — exactly the quantity DGPS
needs to transmit, once the common part is stripped.

### 1.6 Removing the common base-clock term

$-c\,\delta t_{\text{rcv}}$ can be kilometres — far larger than the ±655 m the
16-bit PRC field holds at its fine 0.02 m scale (§1.8) — but it is identical for
every satellite in the epoch. We estimate it as the **median** of $\text{raw}_i$
across satellites and subtract it:

$$
\text{PRC}_i = \text{raw}_i - \operatorname{median}_j(\text{raw}_j)
$$

The median (rather than the mean) is used because it is robust to a single
grossly wrong satellite ([median as a robust location
estimator](https://en.wikipedia.org/wiki/Median#Robustness)). The rover simply
absorbs the removed offset into its own clock solution, so its position is
unaffected. This is the core of `DgpsGenerator.corrections` in
{gh}`rtcm3to2p3/dgps.py`.

:::{note}
Because the median is recomputed each epoch from whatever satellites are
currently visible, a satellite rising or setting shifts the absolute PRC level of
*every* satellite by a small step. That step is harmless for position (again
absorbed by the rover clock) but it is why the **rate** correction below is
computed on the raw correction, not on the PRC.
:::

### 1.7 The range-rate correction (RRC)

RTCM 2.3 Type 1 also carries a rate term so a rover can extrapolate the
correction between the ~1 Hz updates. It is the time derivative of the raw
correction (which drifts smoothly with the base clock) with the common drift
removed the same way as the offset:

$$
\text{RRC}_i = \frac{\text{raw}_i(t) - \text{raw}_i(t-\Delta t)}{\Delta t}
  - \operatorname{median}_j\!\left(\frac{\text{raw}_j(t)-\text{raw}_j(t-\Delta t)}{\Delta t}\right)
$$

Computing the rate on the raw values (not the PRC) avoids inheriting the
per-epoch median jump described in §1.6.

### 1.8 The RTCM 2.3 Type 1 / Type 3 wire encoding

The corrections are packed into RTCM 2.3 (RTCM 10402.3) messages by
{gh}`rtcm3to2p3/rtcm2.py`. Salient points, all verified against gpsd's
[`gpsdecode`](https://gpsd.gitlab.io/gpsd/gpsdecode.html) and an RTKLIB-derived
decoder (see {doc}`validation`):

* **Type 1** — two 24-bit header words then one 40-bit record per satellite:
  scale-factor(1) · UDRE(2) · PRN(5) · PRC(16, signed) · RRC(8, signed) · IOD(8).
  ("PRN" is the satellite's pseudo-random-noise code number — its identifier.)
* **Scale factor** — PRC has two resolutions, $0.02\ \mathrm{m}$ and
  $0.32\ \mathrm{m}$ per LSB (RRC: $0.002$ and $0.032\ \mathrm{m/s}$). The encoder
  picks the fine scale when the value fits and the coarse scale otherwise.
* **Sentinel avoidance** — the most-negative value of each field ($-32768$ =
  `0x8000` for PRC, $-128$ = `0x80` for RRC) is a reserved "satellite problem"
  flag, so the encoder never emits it (it changes scale or clamps to $\pm$max
  magnitude).
* **UDRE** — a 2-bit user differential range error indicator derived from the
  correction magnitude (`udre_index` in {gh}`rtcm3to2p3/dgps.py`).
* **IOD** — Issue Of Data (ephemeris): identifies which broadcast-ephemeris set
  (§1.2) the correction was computed against, so the rover applies the correction
  only while it is using that same orbit/clock data.
* **Type 3** — the base station's ECEF X/Y/Z as 32-bit signed integers in
  $0.01\ \mathrm{m}$ units, emitted about once a minute so a rover can recover the
  antenna reference point.

Every 30-bit word carries 24 data bits + 6 parity bits computed with the GPS
parity algorithm ([IS-GPS-200](https://www.gps.gov/technical/icwg/IS-GPS-200N.pdf)
Table 20-XIV). Each word's parity depends on the last two bits of the *previous*
word (the D29\*/D30\* "seed"), and that seed is carried across word *and message*
boundaries; the 30-bit words are then packed into bytes with RTCM's "6-of-8"
framing (6 data bits per byte). This is {gh}`rtcm3to2p3/parity.py`; §2.9 covers
why it must use exact integer arithmetic.

---

## Part 2 — the practice (numerical accuracy)

Getting the equations right is necessary but not sufficient: a DGPS correction is
a **small difference of large numbers** (metres out of a ~20 000 km range), so the
*types* and the *order of operations* decide whether the millimetre-level signal
survives. The rules below are what {gh}`rtcm3to2p3/ephemeris.py` and
{gh}`rtcm3to2p3/dgps.py` follow.

### 2.1 Everything is IEEE-754 binary64

All geodetic maths uses Python `float`, i.e.
[IEEE-754 double precision](https://en.wikipedia.org/wiki/Double-precision_floating-point_format)
(binary64), whose 53-bit significand (52 stored bits + 1 implicit leading bit)
gives $\log_{10}(2^{53}) \approx 15.95$ decimal significant digits. A GPS
range is ≈ $2.6\times10^{7}\ \mathrm{m}$; to preserve millimetres
($10^{-3}\ \mathrm{m}$) we need $\log_{10}(2.6\times10^{7}/10^{-3}) \approx 10.4$
significant digits, comfortably inside binary64's ~15.9. Single precision
(binary32, ~7.2 digits) would lose the correction entirely — hence float64 is a
hard requirement, not a convenience.

### 2.2 The difference of large numbers (catastrophic cancellation)

$\text{raw}_i = \rho_i - P_i - c\,\delta t^{(i)}_{\text{sv}}$ subtracts two ≈
$2.0\text{–}2.6\times10^{7}\ \mathrm{m}$ quantities. Their difference is dominated
by the base receiver clock $-c\,\delta t_{\text{rcv}}$, which can be
metres-to-kilometres (§1.6); only after the common-mode removal of §1.6 is the
per-satellite residual a few metres. Either way the subtraction is
[catastrophic cancellation](https://en.wikipedia.org/wiki/Catastrophic_cancellation):
the *absolute* error of each operand is what survives, not the relative error. At
binary64, $2.6\times10^{7}\ \mathrm{m}$ has an absolute resolution (ULP) of about
$2.6\times10^{7}\times2^{-52} \approx 6\times10^{-9}\ \mathrm{m}$ — a few
nanometres — so even a metre-scale difference is preserved to ≈ 9 significant
digits. That headroom is why the code computes each operand at full range and
subtracts directly rather than trying to pre-remove a nominal range.

### 2.3 Week-crossing time differences

Times of week wrap every 604 800 s. Any $t - t_{\text{ref}}$ (for $t_{oe}$,
$t_{oc}$, or the previous epoch) is reduced into $[-302400, +302400]\ \mathrm{s}$
by `_delta_t_week` in {gh}`rtcm3to2p3/ephemeris.py`, per
[IS-GPS-200](https://www.gps.gov/technical/icwg/IS-GPS-200N.pdf) §20.3.3.4.3.1.
Doing the subtraction first and *then* the wrap keeps $t_k$ small (seconds to
hours), so $n\,t_k$ and the harmonic arguments never lose precision to a huge
operand.

### 2.4 Iterations: Kepler and transmit time

* **Kepler's equation** $E_k = M_k + e\sin E_k$ has no closed form; it is solved
  by [fixed-point iteration](https://en.wikipedia.org/wiki/Kepler%27s_equation).
  GPS eccentricity is tiny ($e \lesssim 0.02$), so the iteration converges
  linearly and fast; the code iterates to a $10^{-13}\ \mathrm{rad}$ change —
  which maps to a $\approx 2.6\ \mathrm{\mu m}$ position change
  ($10^{-13}\times2.6\times10^{7}\ \mathrm{m}$), far below the millimetre accuracy
  requirement — with a bounded iteration count so it can never spin.
* **Transmit time** — the satellite position is needed at signal *emission*, which
  is $P_i/c \approx 0.07\ \mathrm{s}$ before reception. That interval is itself
  derived from the pseudorange, so it is computed as $t_{tx} = t_{ow} - P_i/c$,
  where $t_{ow}$ is the reception time of week
  ([ESA Navipedia, *Emission Time
  Computation*](https://gssc.esa.int/navipedia/index.php/Emission_Time_Computation)).
  A single evaluation is used per epoch; the residual geometry error from not
  re-iterating is far below the correction's own noise floor.

### 2.5 Angles: semicircles → radians

RTCM 3 (like the GPS navigation message) stores the ephemeris angular terms in
**semicircles**, not radians. They are multiplied by $\pi$ exactly once, at parse
time, in {gh}`rtcm3to2p3/rtcm3_input.py`; every downstream trig call then takes
radians. Converting in one place avoids the classic double-conversion /
missed-conversion bug and keeps the value at full precision (multiplication by
$\pi$ is a single correctly-rounded operation).

### 2.6 The Sagnac term is added at range scale

The earth-rotation term $\frac{\dot\Omega_e}{c}(x_{\text{sat}}y_{\text{base}} -
y_{\text{sat}}x_{\text{base}})$ multiplies two ≈ $10^{7}\ \mathrm{m}$ coordinates
(product ≈ $10^{14}$) by ≈ $2.4\times10^{-13}$, giving a few metres. Because it is
added to $\rho_i$ *before* the big subtraction in §2.2, it enters at the same
scale as the range and is not separately amplified.

### 2.7 Robust common-mode removal

The median (§1.6) is computed with Python's exact
[`statistics.median`](https://docs.python.org/3/library/statistics.html#statistics.median),
which averages the two middle values for an even count — an exact float operation.
Subtracting a single common offset from every satellite is a same-scale operation
(metres − metres), so it introduces no cancellation error of its own.

### 2.8 Scaling into the fixed-point RTCM fields

The final step quantises the float PRC/RRC into the signed integer fields of §1.8.
Each is divided by its LSB and **rounded to nearest** (Python `round`, banker's
rounding) rather than truncated, halving the quantisation bias. The encoder then:

1. tries the fine scale and, only if the value would overflow the field, the
   coarse scale;
2. clamps to $\pm$(max−1) so it can never emit a reserved sentinel (§1.8);

all in {gh}`rtcm3to2p3/rtcm2.py` (`_encode_prc_rrc`). Quantisation to
$0.02\ \mathrm{m}$ is the dominant error in the whole pipeline (≈ 6 mm RMS) and it
is *bounded per message* — it does not accumulate, because every epoch is encoded
from the freshly computed float value, never from the previously transmitted
integer.

### 2.9 Bit and parity arithmetic is exact integer work

Field packing, the two's-complement encoding of signed values, the GPS parity
computation, the D29\*/D30\* seed chaining and the 6-of-8 framing are all done on
Python arbitrary-precision **integers** ({gh}`rtcm3to2p3/bits.py`,
{gh}`rtcm3to2p3/parity.py`), so there is no floating-point involved and the wire
output is bit-exact. The parity masks are transcribed from
[IS-GPS-200](https://www.gps.gov/technical/icwg/IS-GPS-200N.pdf) Table 20-XIV and
independently checked against RTKLIB's Hamming-table formulation over randomised
words (see {doc}`validation`).

### 2.10 No error accumulates across epochs

The one place state crosses epochs is the RRC's previous-epoch raw value
({gh}`rtcm3to2p3/dgps.py`). Everything else — satellite position, clock, range,
PRC, quantisation — is recomputed from scratch each epoch from the current
ephemeris and observation. There is no recursive filter and no integrator, so
rounding errors cannot build up over time; each message is independently correct
to the bounds above.

---

## Validation

The theory and the implementation are cross-checked against several independent,
non-Python implementations (gpsd `gpsdecode`, RTKLIB, gnss_lib_py) and a real
u-blox 7 receiver. See {doc}`validation` for the full matrix and how to reproduce
each check.

## Primary references

* [IS-GPS-200 — NAVSTAR GPS Space Segment / Navigation User Interfaces](https://www.gps.gov/technical/icwg/IS-GPS-200N.pdf)
  (satellite position §20.3.3.4.3, clock §20.3.3.3.3, parity Table 20-XIV).
* [RTCM Standards (SC-104)](https://www.rtcm.org/publications) — 10402.3 (RTCM 2.3)
  and 10403.x (RTCM 3.x).
* [ESA Navipedia — GNSS theory](https://gssc.esa.int/navipedia/index.php/Main_Page)
  (satellite coordinates, emission time, relativistic clock, Sagnac effect).
* [RTKLIB](https://www.rtklib.com/) — reference C implementation
  ([source](https://github.com/tomojitakasu/RTKLIB)).
* [gnss_lib_py (Stanford NAV Lab)](https://github.com/Stanford-NavLab/gnss_lib_py)
  — independent satellite position/clock used as the numerical reference.
* [gpsd / gpsdecode](https://gpsd.gitlab.io/gpsd/) — independent RTCM 2 decoder.
* [Misra & Enge, *Global Positioning System: Signals, Measurements, and
  Performance*](https://gpstextbook.com/) — DGPS technique and error budget.
