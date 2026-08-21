"""RTCM SC-104 v2.3 message encoder (Type 1 DGPS corrections).

Wire format (verified against RTKLIB ``input_rtcm2`` and gpsd's ``gpsdecode``):

* A message is a continuous stream of 30-bit words (24 data + 6 parity, see
  :mod:`rtcm3to2p3.parity`), with the D29*/D30* seed chaining across every word
  and every message.
* Each 30-bit word is sent as five 6-bit groups, MSB group first. Each group is
  one byte: ``0x40 | reverse6(group)`` — the two high bits are ``01`` and the six
  data bits are bit-reversed within the byte.

Type 1 (Differential GPS Corrections) layout — two 24-bit header words then N
40-bit satellite records:

* Word 1: preamble(8)=0x66 · message type(6) · station id(10)
* Word 2: modified Z-count(13) · sequence no(3) · N data words(5) · station health(3)
* Per satellite (40 bits): scale factor(1) · UDRE(2) · PRN(5, 0=>32) ·
  PRC(16 signed) · RRC(8 signed) · IOD(8). PRC scale 0.02/0.32 m, RRC 0.002/0.032 m/s.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .bits import BitReader, BitWriter
from .parity import WORD_BITS, WORD_DATA_BITS, encode_word

PREAMBLE = 0x66
RTCM2_TYPE1 = 1

PRC_SCALE = (0.02, 0.32)  # metres per LSB, indexed by scale factor bit
RRC_SCALE = (0.002, 0.032)  # metres/second per LSB
_PRC_MAX = 32767  # avoid the -32768 "satellite problem" sentinel
_RRC_MAX = 127  # avoid the -128 sentinel


@dataclass(frozen=True)
class Correction:
    """One satellite's DGPS correction for a Type 1 message."""

    prn: int  # GPS PRN 1..32
    prc: float  # pseudorange correction, metres
    rrc: float  # range-rate correction, metres/second
    iod: int  # issue of data (ephemeris), 0..255
    udre: int = 0  # user differential range error indicator, 0..3


def _encode_prc_rrc(prc: float, rrc: float) -> tuple[int, int, int]:
    """Return (scale_factor_bit, prc_counts, rrc_counts), picking the scale."""
    for scale in (0, 1):
        prc_counts = round(prc / PRC_SCALE[scale])
        rrc_counts = round(rrc / RRC_SCALE[scale])
        if abs(prc_counts) <= _PRC_MAX and abs(rrc_counts) <= _RRC_MAX:
            return scale, prc_counts, rrc_counts
    # out of range even at coarse scale: clamp to the max magnitude
    scale = 1
    prc_counts = max(-_PRC_MAX, min(_PRC_MAX, round(prc / PRC_SCALE[scale])))
    rrc_counts = max(-_RRC_MAX, min(_RRC_MAX, round(rrc / RRC_SCALE[scale])))
    return scale, prc_counts, rrc_counts


def _pack_type1_body(
    station_id: int, zcount: int, seq: int, health: int, corrections: Sequence[Correction]
) -> list[int]:
    """Build the message as a list of 24-bit data words (headers + sat records)."""
    if not 0 <= station_id < (1 << 10):
        raise ValueError("station_id out of range 0..1023")
    if not 0 <= zcount < 8192:
        raise ValueError("zcount out of range 0..8191")
    if not 0 <= seq < 8:
        raise ValueError("seq out of range 0..7")
    if not 0 <= health < 8:
        raise ValueError("health out of range 0..7")

    sat = BitWriter()
    for c in corrections:
        if not 1 <= c.prn <= 32:
            raise ValueError(f"PRN out of range 1..32: {c.prn}")
        if not 0 <= c.udre < 4:
            raise ValueError(f"UDRE out of range 0..3: {c.udre}")
        if not 0 <= c.iod < 256:
            raise ValueError(f"IOD out of range 0..255: {c.iod}")
        scale, prc_counts, rrc_counts = _encode_prc_rrc(c.prc, c.rrc)
        sat.write_unsigned(scale, 1)
        sat.write_unsigned(c.udre, 2)
        sat.write_unsigned(c.prn % 32, 5)  # PRN 32 transmitted as 0
        sat.write_signed(prc_counts, 16)
        sat.write_signed(rrc_counts, 8)
        sat.write_unsigned(c.iod, 8)

    n_words = math.ceil(sat.nbits / WORD_DATA_BITS)

    hdr = BitWriter()
    hdr.write_unsigned(PREAMBLE, 8)
    hdr.write_unsigned(RTCM2_TYPE1, 6)
    hdr.write_unsigned(station_id, 10)
    hdr.write_unsigned(zcount, 13)
    hdr.write_unsigned(seq, 3)
    hdr.write_unsigned(n_words, 5)
    hdr.write_unsigned(health, 3)

    # concatenate header (48 bits) + sat body, zero-pad to a whole number of words
    total_bits = 48 + n_words * WORD_DATA_BITS
    stream = BitWriter()
    hr = BitReader(hdr.to_bytes())
    for _ in range(48):
        stream.write_unsigned(hr.read_unsigned(1), 1)
    sr = BitReader(sat.to_bytes())
    for _ in range(sat.nbits):
        stream.write_unsigned(sr.read_unsigned(1), 1)
    while stream.nbits < total_bits:
        stream.write_unsigned(0, 1)  # fill bits (ignored by decoders)

    reader = BitReader(stream.to_bytes())
    return [reader.read_unsigned(WORD_DATA_BITS) for _ in range(total_bits // WORD_DATA_BITS)]


def _reverse6(value: int) -> int:
    return sum(((value >> i) & 1) << (5 - i) for i in range(6))


def _word30_to_bytes(word30: int) -> bytes:
    """Five 6-of-8 bytes for one 30-bit word, MSB group first."""
    out = bytearray()
    for g in range(5):
        group = (word30 >> (WORD_BITS - 6 - g * 6)) & 0x3F
        out.append(0x40 | _reverse6(group))
    return bytes(out)


class Rtcm2Encoder:
    """Stateful encoder maintaining the parity seed across the continuous stream."""

    def __init__(self, d29star: int = 0, d30star: int = 0) -> None:
        self._d29 = d29star
        self._d30 = d30star

    def encode_words(self, data_words: Iterable[int]) -> bytes:
        """Encode 24-bit data words to wire bytes, chaining parity across calls."""
        out = bytearray()
        for dw in data_words:
            bits = [(dw >> (WORD_DATA_BITS - 1 - i)) & 1 for i in range(WORD_DATA_BITS)]
            word30, self._d29, self._d30 = encode_word(bits, self._d29, self._d30)
            out += _word30_to_bytes(word30)
        return bytes(out)

    def encode_type1(
        self,
        station_id: int,
        zcount: int,
        seq: int,
        health: int,
        corrections: Sequence[Correction],
    ) -> bytes:
        """Encode one Type 1 (DGPS corrections) message to wire bytes."""
        return self.encode_words(
            _pack_type1_body(station_id, zcount, seq, health, corrections)
        )
