"""MSB-first bit packing/unpacking.

RTCM 2.3 messages are built from fields of arbitrary bit widths that do not fall
on byte boundaries, so the encoder needs to append unsigned and two's-complement
signed integers bit-by-bit and later read them back. These helpers are the
workhorse for that; they are deliberately format-agnostic (no RTCM specifics).
"""
from __future__ import annotations


class BitWriter:
    """Accumulate integer fields MSB-first into a bit buffer."""

    def __init__(self) -> None:
        self._acc = 0
        self._nbits = 0

    @property
    def nbits(self) -> int:
        """Number of bits written so far."""
        return self._nbits

    def write_unsigned(self, value: int, nbits: int) -> None:
        """Append ``value`` as an ``nbits``-wide unsigned integer.

        Raises ValueError if ``nbits`` < 1 or ``value`` does not fit unsigned.
        """
        if nbits < 1:
            raise ValueError(f"nbits must be >= 1, got {nbits}")
        if not 0 <= value < (1 << nbits):
            raise ValueError(f"{value} does not fit in {nbits} unsigned bits")
        self._acc = (self._acc << nbits) | value
        self._nbits += nbits

    def write_signed(self, value: int, nbits: int) -> None:
        """Append ``value`` as an ``nbits``-wide two's-complement signed integer.

        Raises ValueError if ``nbits`` < 1 or ``value`` is out of range.
        """
        if nbits < 1:
            raise ValueError(f"nbits must be >= 1, got {nbits}")
        lo, hi = -(1 << (nbits - 1)), (1 << (nbits - 1)) - 1
        if not lo <= value <= hi:
            raise ValueError(f"{value} does not fit in {nbits} signed bits [{lo},{hi}]")
        self.write_unsigned(value & ((1 << nbits) - 1), nbits)

    def to_bytes(self, pad_bit: int = 0) -> bytes:
        """Return the buffer as bytes, right-padding the final byte with ``pad_bit``."""
        if pad_bit not in (0, 1):
            raise ValueError("pad_bit must be 0 or 1")
        rem = (-self._nbits) % 8
        acc, total = self._acc, self._nbits + rem
        if rem:
            acc = (acc << rem) | (((1 << rem) - 1) if pad_bit else 0)
        return acc.to_bytes(total // 8, "big") if total else b""


class BitReader:
    """Read integer fields MSB-first from a byte buffer."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0  # bit position

    @property
    def pos(self) -> int:
        """Current bit position."""
        return self._pos

    @property
    def remaining(self) -> int:
        """Bits left to read."""
        return len(self._data) * 8 - self._pos

    def read_unsigned(self, nbits: int) -> int:
        """Read the next ``nbits`` as an unsigned integer."""
        if nbits < 1:
            raise ValueError(f"nbits must be >= 1, got {nbits}")
        if nbits > self.remaining:
            raise ValueError(f"need {nbits} bits, only {self.remaining} remain")
        value = 0
        for _ in range(nbits):
            byte = self._data[self._pos >> 3]
            bit = (byte >> (7 - (self._pos & 7))) & 1
            value = (value << 1) | bit
            self._pos += 1
        return value

    def read_signed(self, nbits: int) -> int:
        """Read the next ``nbits`` as a two's-complement signed integer."""
        value = self.read_unsigned(nbits)
        if value & (1 << (nbits - 1)):
            value -= 1 << nbits
        return value
