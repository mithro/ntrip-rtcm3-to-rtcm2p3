"""Tests for GPS/RTCM 2.3 word parity.

The reference implementation here is transcribed from RTKLIB's ``decode_word``
(``rtcm.c``): its Hamming-table + popcount formulation is algorithmically
*independent* of ``parity.py``'s position-list masks, so agreement between the
two is a strong correctness signal beyond mere self-consistency. (Whole-message
output is additionally validated against gpsd's ``gpsdecode``, RTKLIB, and the
u-blox 7 hardware in the RTCM 2.3 encoder tests.)
"""
import random

import pytest

from rtcm3to2p3.parity import encode_word, parity_bits, word_to_bits

# RTKLIB rtcm.c hamming[] — masks over a 32-bit word (bit31=D29*, bit30=D30*,
# bits29..6 = data d1..d24, bits5..0 = parity).
_HAMMING = (0xBB1F3480, 0x5D8F9A40, 0xAEC7CD00, 0x5763E680, 0x6BB1F340, 0x8B7A89C0)


def _rtklib_word(data, d29star, d30star):
    word = (d29star << 31) | (d30star << 30)
    for i, b in enumerate(data):
        word |= (b & 1) << (29 - i)
    return word


def _rtklib_parity(data, d29star, d30star):
    """Independent parity (RTKLIB formulation): popcount over source data + seeds.

    Parity is computed on the *source* (un-inverted) data — the transmitted data
    inversion on D30* is applied separately by the encoder, and a decoder
    un-inverts before checking parity (see _rtklib_decode).
    """
    word = _rtklib_word(data, d29star, d30star)
    return [bin(word & h).count("1") & 1 for h in _HAMMING]


def _rtklib_decode(word30, d29star, d30star):
    """RTKLIB's decode_word: returns (parity_ok, recovered_original_data[24])."""
    word = (d29star << 31) | (d30star << 30) | word30
    w = word ^ 0x3FFFFFC0 if word & 0x40000000 else word
    parity = 0
    for h in _HAMMING:
        parity = (parity << 1) | (bin(w & h).count("1") & 1)
    ok = parity == (word30 & 0x3F)
    data = [(w >> (29 - i)) & 1 for i in range(24)]
    return ok, data


def _rand_data(rng):
    return [rng.randint(0, 1) for _ in range(24)]


@pytest.mark.parametrize("seed", range(200))
def test_matches_rtklib_parity(seed):
    rng = random.Random(seed)
    data, d29, d30 = _rand_data(rng), rng.randint(0, 1), rng.randint(0, 1)
    assert parity_bits(data, d29, d30) == _rtklib_parity(data, d29, d30)


@pytest.mark.parametrize("seed", range(200))
def test_rtklib_decodes_our_word(seed):
    """Encode with our code, decode with RTKLIB's algorithm: parity ok + data recovered."""
    rng = random.Random(seed + 1000)
    data, d29, d30 = _rand_data(rng), rng.randint(0, 1), rng.randint(0, 1)
    word, _, _ = encode_word(data, d29, d30)
    ok, recovered = _rtklib_decode(word, d29, d30)
    assert ok
    assert recovered == data


def test_all_zero_word_has_zero_parity():
    assert parity_bits([0] * 24, 0, 0) == [0] * 6
    word, nd29, nd30 = encode_word([0] * 24, 0, 0)
    assert word == 0 and nd29 == 0 and nd30 == 0


def test_d30star_inverts_data():
    # all-zero source with D30*=1 -> transmitted data are all ones
    bits = word_to_bits([0] * 24, 0, 1)
    assert bits[:24] == [1] * 24


def test_seed_chaining_returns_d29_d30():
    rng = random.Random(7)
    data = _rand_data(rng)
    word, nd29, nd30 = encode_word(data, 1, 0)
    assert nd29 == (word >> 1) & 1  # D29 = 2nd-last bit
    assert nd30 == word & 1  # D30 = last bit


def test_single_bit_flip_breaks_parity():
    rng = random.Random(3)
    data = _rand_data(rng)
    word, _, _ = encode_word(data, 0, 0)
    ok, _ = _rtklib_decode(word ^ (1 << 29), 0, 0)  # flip a data bit
    assert not ok


def test_word_to_bits_length():
    assert len(word_to_bits([1] * 24, 0, 0)) == 30


def test_errors():
    with pytest.raises(ValueError):
        parity_bits([0] * 23, 0, 0)
    with pytest.raises(ValueError):
        parity_bits([2] * 24, 0, 0)
    with pytest.raises(ValueError):
        parity_bits([0] * 24, 0, 2)
