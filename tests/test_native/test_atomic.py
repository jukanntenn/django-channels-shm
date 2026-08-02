"""Integration tests for the ShmRegion atomic operations exposed via PyO3."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from channels_shm._native import ShmRegion

if TYPE_CHECKING:
    from tests.test_native._types import NativeRegion


def test_load_store_u64_roundtrip(region: NativeRegion) -> None:
    """store_u64 followed by load_u64 should round-trip."""
    r = region.region
    r.store_u64(0, 42)
    assert r.load_u64(0) == 42
    r.store_u64(8, 0xDEAD_BEEF_CAFE_BABE)
    assert r.load_u64(8) == 0xDEAD_BEEF_CAFE_BABE
    r.store_u64(16, (1 << 64) - 1)
    assert r.load_u64(16) == (1 << 64) - 1


def test_store_load_at_distinct_offsets(region: NativeRegion) -> None:
    """Writes to distinct offsets must not interfere."""
    r = region.region
    r.store_u64(0, 1)
    r.store_u64(8, 2)
    r.store_u64(16, 3)
    assert r.load_u64(0) == 1
    assert r.load_u64(8) == 2
    assert r.load_u64(16) == 3


def test_cas_u64_success(region: NativeRegion) -> None:
    """CAS should succeed when expected matches and update the value.

    The returned ``actual`` is the previous value (== expected); the new value
    is observable via load_u64.
    """
    r = region.region
    r.store_u64(0, 10)
    ok, actual = r.cas_u64(0, 10, 20)
    assert ok is True
    assert actual == 10
    assert r.load_u64(0) == 20


def test_cas_u64_failure(region: NativeRegion) -> None:
    """CAS should fail when expected does not match and leave the value."""
    r = region.region
    r.store_u64(0, 10)
    ok, actual = r.cas_u64(0, 99, 20)
    assert ok is False
    assert actual == 10
    assert r.load_u64(0) == 10


def test_fetch_add_u64(region: NativeRegion) -> None:
    """fetch_add_u64 returns the previous value and adds the delta."""
    r = region.region
    r.store_u64(0, 100)
    assert r.fetch_add_u64(0, 1) == 100
    assert r.load_u64(0) == 101
    assert r.fetch_add_u64(0, 10) == 101
    assert r.load_u64(0) == 111


def test_fetch_add_u64_wraps(region: NativeRegion) -> None:
    """fetch_add_u64 should wrap on overflow (unsigned semantics)."""
    r = region.region
    # u64::MAX - 5 + 10 wraps to 4 (mod 2^64).
    r.store_u64(0, (1 << 64) - 6)  # u64::MAX - 5
    assert r.fetch_add_u64(0, 10) == (1 << 64) - 6
    assert r.load_u64(0) == 4  # wrapped


def test_copy_in_out_roundtrip(region: NativeRegion) -> None:
    """copy_in / copy_out should round-trip bytes."""
    r = region.region
    data = b"hello world"
    r.copy_in(0, data)
    assert r.copy_out(0, len(data)) == data


def test_copy_in_does_not_overwrite_other_offsets(region: NativeRegion) -> None:
    """copy_in at one offset should not affect data at another."""
    r = region.region
    r.copy_in(0, b"AAAA")
    r.copy_in(64, b"BBBB")
    assert r.copy_out(0, 4) == b"AAAA"
    assert r.copy_out(64, 4) == b"BBBB"


def test_read_write_u32_u16_u8(region: NativeRegion) -> None:
    """read/write helpers for u32/u16/u8 should round-trip."""
    r = region.region
    r.write_u32(0, 0x1234_5678)
    assert r.read_u32(0) == 0x1234_5678
    r.write_u16(4, 0xABCD)
    assert r.read_u16(4) == 0xABCD
    r.write_u8(6, 0xFF)
    assert r.read_u8(6) == 0xFF


def test_read_bytes(region: NativeRegion) -> None:
    """read_bytes should match copy_out for the same region."""
    r = region.region
    r.copy_in(0, b"abcdef")
    assert r.read_bytes(0, 6) == b"abcdef"


def test_len_matches_size(region: NativeRegion) -> None:
    """ShmRegion.len() should return the size passed at construction."""
    assert region.region.len() == region.size


def test_shm_region_rejects_null_pointer() -> None:
    """Construction with a null pointer should raise ValueError."""
    with pytest.raises(ValueError):
        _ = ShmRegion(0, 1024)
