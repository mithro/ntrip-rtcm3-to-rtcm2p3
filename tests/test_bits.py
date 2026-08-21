"""Tests for the MSB-first bit packer/unpacker."""
import pytest

from rtcm3to2p3.bits import BitReader, BitWriter


def test_exact_bit_pattern():
    w = BitWriter()
    w.write_unsigned(0b101, 3)
    w.write_unsigned(0b1, 1)
    assert w.nbits == 4
    # 0b1011 padded on the right with zeros -> 0b1011_0000
    assert w.to_bytes() == bytes([0b1011_0000])


def test_pad_bit_one():
    w = BitWriter()
    w.write_unsigned(0b1, 1)
    assert w.to_bytes(pad_bit=1) == bytes([0b1111_1111])


def test_byte_aligned_roundtrip():
    w = BitWriter()
    w.write_unsigned(0xAB, 8)
    w.write_unsigned(0xCD, 8)
    assert w.to_bytes() == bytes([0xAB, 0xCD])
    r = BitReader(w.to_bytes())
    assert r.read_unsigned(8) == 0xAB
    assert r.read_unsigned(8) == 0xCD


@pytest.mark.parametrize("nbits", range(1, 40))
def test_unsigned_roundtrip_widths(nbits):
    values = [0, 1, (1 << nbits) - 1, (1 << nbits) // 3]
    w = BitWriter()
    for v in values:
        w.write_unsigned(v, nbits)
    r = BitReader(w.to_bytes())
    for v in values:
        assert r.read_unsigned(nbits) == v


@pytest.mark.parametrize("nbits", range(1, 40))
def test_signed_roundtrip_widths(nbits):
    lo, hi = -(1 << (nbits - 1)), (1 << (nbits - 1)) - 1
    values = [lo, hi, -1, 0]
    if nbits >= 2:
        values.append(lo // 2)
    w = BitWriter()
    for v in values:
        w.write_signed(v, nbits)
    r = BitReader(w.to_bytes())
    for v in values:
        assert r.read_signed(nbits) == v


def test_signed_minus_one_is_all_ones():
    w = BitWriter()
    w.write_signed(-1, 5)
    r = BitReader(w.to_bytes())
    assert r.read_unsigned(5) == 0b11111


def test_mixed_widths_roundtrip():
    fields = [(5, 3), (-7, 5), (1023, 10), (0, 1), (-1, 12)]
    w = BitWriter()
    for value, nbits in fields:
        (w.write_signed if value < 0 else w.write_unsigned)(value, nbits)
    r = BitReader(w.to_bytes())
    for value, nbits in fields:
        got = r.read_signed(nbits) if value < 0 else r.read_unsigned(nbits)
        assert got == value


def test_position_tracking():
    r = BitReader(bytes([0xFF, 0x00]))
    assert r.remaining == 16
    r.read_unsigned(3)
    assert r.pos == 3
    assert r.remaining == 13


def test_empty_writer():
    assert BitWriter().to_bytes() == b""


def test_errors():
    w = BitWriter()
    with pytest.raises(ValueError):
        w.write_unsigned(8, 3)  # 8 needs 4 bits
    with pytest.raises(ValueError):
        w.write_unsigned(-1, 3)
    with pytest.raises(ValueError):
        w.write_signed(4, 3)  # signed 3-bit max is 3
    with pytest.raises(ValueError):
        w.write_signed(-5, 3)  # signed 3-bit min is -4
    with pytest.raises(ValueError):
        w.write_unsigned(0, 0)
    with pytest.raises(ValueError):
        w.to_bytes(pad_bit=2)
    r = BitReader(bytes([0x00]))
    with pytest.raises(ValueError):
        r.read_unsigned(9)
