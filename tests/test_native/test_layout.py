"""Integration tests for layout helpers exposed via PyO3."""

from __future__ import annotations

from channels_shm._native import (
    CH_SLOT_SIZE,
    GRP_SLOT_SIZE,
    HDR_SIZE,
    MAGIC,
    MEMBER_ENTRY_SIZE,
    REG_SLOT_SIZE,
    RING_HEADER_SIZE,
    SIZE_CLASSES,
    SLOT_SIZE,
    VERSION,
    compute_offsets,
    fnv1a_hash,
    read_self_starttime,
    size_class_for,
)


def test_compute_offsets_layout_order() -> None:
    """compute_offsets should return offsets in increasing order with no gaps.

    R-01: no group-members region is reserved (member arrays come from the
    dynamic pool slab). So `members` is a vestigial placeholder equal to `reg`,
    and the registry follows the group index directly.
    """
    ch, grp, members, reg, metrics, pool = compute_offsets(100, 50, 64, 16)
    # Order: header < channel_index < group_index < registry < metrics < pool.
    assert ch == HDR_SIZE
    assert grp == ch + 100 * CH_SLOT_SIZE
    assert reg == grp + 50 * GRP_SLOT_SIZE
    assert members == reg  # vestigial placeholder (zero-length)
    assert metrics == reg + 16 * REG_SLOT_SIZE
    assert pool == metrics + 64 * 8  # METRICS_COUNTER_COUNT * 8


def test_compute_offsets_scales_with_config() -> None:
    """compute_offsets should scale offsets when config grows.

    The first offset (channel_index_off) is always HDR_SIZE; the remaining
    offsets must strictly increase as config grows.
    """
    small = compute_offsets(10, 10, 10, 10)
    large = compute_offsets(100, 100, 100, 100)
    # First offset is fixed at HDR_SIZE for both.
    assert small[0] == large[0] == HDR_SIZE
    # All subsequent offsets should strictly grow.
    for s, lg in zip(small[1:], large[1:], strict=True):
        assert lg > s


def test_fnv1a_hash_deterministic() -> None:
    """fnv1a_hash should be deterministic for the same input."""
    assert fnv1a_hash(b"test.channel") == fnv1a_hash(b"test.channel")


def test_fnv1a_hash_distinguishes_inputs() -> None:
    """Different inputs should produce different hashes (with high probability)."""
    assert fnv1a_hash(b"foo") != fnv1a_hash(b"bar")


def test_fnv1a_hash_empty_input() -> None:
    """Empty input should return the FNV-1a offset basis."""
    # FNV-1a 64-bit offset basis is 0xcbf29ce484222325.
    assert fnv1a_hash(b"") == 0xCBF2_9CE4_8422_2325


def test_size_class_for_picks_smallest_fit() -> None:
    """size_class_for should return the smallest class >= size."""
    assert size_class_for(1) == 512
    assert size_class_for(512) == 512
    assert size_class_for(513) == 2048
    assert size_class_for(2048) == 2048


def test_size_class_for_too_large_returns_none() -> None:
    """size_class_for should return None when no class fits."""
    max_class = SIZE_CLASSES[-1]
    assert size_class_for(max_class + 1) is None


def test_read_self_starttime_returns_nonzero() -> None:
    """read_self_starttime should return a valid starttime for the current process."""
    # starttime is field 22 of /proc/self/stat — always > 0 for a live process.
    assert read_self_starttime() > 0


def test_magic_and_version_exposed() -> None:
    """MAGIC and VERSION constants should be exposed and match the spec."""
    assert MAGIC == 0x4348_5348  # "CHSH"
    assert VERSION == 1


def test_layout_size_constants_exposed() -> None:
    """Layout size constants should be exposed to Python.

    Name fields are [u8;128] (R-03: avoid silent truncation of process-specific
    channel names whose worst-case length is ~128B), so slot sizes are larger
    than the V1 100-byte-field layout.
    """
    assert HDR_SIZE == 4096
    assert RING_HEADER_SIZE == 40
    assert SLOT_SIZE == 704
    assert CH_SLOT_SIZE == 168
    assert GRP_SLOT_SIZE == 176
    assert MEMBER_ENTRY_SIZE == 144
    assert REG_SLOT_SIZE == 168
