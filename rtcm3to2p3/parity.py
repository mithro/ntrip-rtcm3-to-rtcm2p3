"""GPS / RTCM 2.3 word parity (IS-GPS-200 Table 20-XIV).

Each 30-bit word carries 24 data bits (d1..d24, MSB first) followed by 6 parity
bits (D25..D30) computed from the *source* data and the last two bits of the
previous word (``d29star``, ``d30star``). Per IS-GPS-200, when D30* is set the
24 *transmitted* data bits are complemented — but the parity is computed on the
un-inverted source data (a decoder un-inverts, then checks parity; verified
against RTKLIB's ``decode_word``).

This algorithm is shared by the GPS navigation message and RTCM SC-104 v2.x.
Matching it exactly is what lets independent decoders (gpsd's ``gpsdecode``,
RTKLIB, and u-blox receivers) accept and correctly interpret our output.
"""
from __future__ import annotations

from collections.abc import Sequence

# 1-indexed data-bit positions XORed into each parity bit (Table 20-XIV).
_PARITY_MASKS: tuple[tuple[int, ...], ...] = (
    (1, 2, 3, 5, 6, 10, 11, 12, 13, 14, 17, 18, 20, 23),      # D25
    (2, 3, 4, 6, 7, 11, 12, 13, 14, 15, 18, 19, 21, 24),      # D26
    (1, 3, 4, 5, 7, 8, 12, 13, 14, 15, 16, 19, 20, 22),       # D27
    (2, 4, 5, 6, 8, 9, 13, 14, 15, 16, 17, 20, 21, 23),       # D28
    (1, 3, 5, 6, 7, 9, 10, 14, 15, 16, 17, 18, 21, 22, 24),   # D29
    (3, 5, 6, 8, 9, 10, 11, 13, 15, 19, 22, 23, 24),          # D30
)
# Leading term seed: D25/D27/D30 use D29*, D26/D28/D29 use D30*.
_SEED_IS_D29: tuple[bool, ...] = (True, False, True, False, False, True)

WORD_DATA_BITS = 24
WORD_PARITY_BITS = 6
WORD_BITS = WORD_DATA_BITS + WORD_PARITY_BITS


def parity_bits(data: Sequence[int], d29star: int, d30star: int) -> list[int]:
    """Return the 6 parity bits ``[D25..D30]`` for 24 data bits ``d1..d24``.

    ``data`` is 24 values (each 0 or 1), MSB first, the *source* (un-inverted)
    data. ``d29star``/``d30star`` are the previous word's last two transmitted
    bits. Inversion on D30* applies to the transmitted data bits (see
    ``encode_word``), not to this parity computation.
    """
    if len(data) != WORD_DATA_BITS:
        raise ValueError(f"expected {WORD_DATA_BITS} data bits, got {len(data)}")
    if any(b not in (0, 1) for b in data) or d29star not in (0, 1) or d30star not in (0, 1):
        raise ValueError("all bits must be 0 or 1")
    out = []
    for mask, seed_d29 in zip(_PARITY_MASKS, _SEED_IS_D29, strict=True):
        p = d29star if seed_d29 else d30star
        for idx in mask:
            p ^= data[idx - 1]
        out.append(p)
    return out


def encode_word(data: Sequence[int], d29star: int, d30star: int) -> tuple[int, int, int]:
    """Encode a full 30-bit word.

    Returns ``(word, next_d29star, next_d30star)`` where ``word`` is the 30-bit
    transmitted word (inverted data in the high 24 bits, parity in the low 6),
    and the next seeds are this word's D29/D30 for chaining to the next word.
    """
    parity = parity_bits(data, d29star, d30star)
    d = [b ^ d30star for b in data]
    word = 0
    for b in d:
        word = (word << 1) | b
    for b in parity:
        word = (word << 1) | b
    return word, parity[4], parity[5]  # D29, D30


def word_to_bits(data: Sequence[int], d29star: int, d30star: int) -> list[int]:
    """Convenience: the 30 transmitted bits (24 possibly-inverted data + 6 parity)."""
    word, _, _ = encode_word(data, d29star, d30star)
    return [(word >> (WORD_BITS - 1 - i)) & 1 for i in range(WORD_BITS)]
