"""Type stub for the Rust native extension."""

from typing import Final

# Layout constants
MAGIC: Final[int]
VERSION: Final[int]
HDR_SIZE: Final[int]
CH_SLOT_SIZE: Final[int]
GRP_SLOT_SIZE: Final[int]
MEMBER_ENTRY_SIZE: Final[int]
REG_SLOT_SIZE: Final[int]
RING_HEADER_SIZE: Final[int]
SLOT_SIZE: Final[int]
SIZE_CLASSES: Final[tuple[int, ...]]

class ShmRegion:
    """Shared memory region with atomic operations."""

    def __init__(self, ptr: int, len: int) -> None: ...
    def load_u64(self, offset: int) -> int: ...
    def store_u64(self, offset: int, value: int) -> None: ...
    def cas_u64(self, offset: int, expected: int, desired: int) -> tuple[bool, int]: ...
    def fetch_add_u64(self, offset: int, delta: int) -> int: ...
    def copy_in(self, offset: int, data: bytes) -> None: ...
    def copy_out(self, offset: int, length: int) -> bytes: ...
    def read_u32(self, offset: int) -> int: ...
    def write_u32(self, offset: int, value: int) -> None: ...
    def read_u16(self, offset: int) -> int: ...
    def write_u16(self, offset: int, value: int) -> None: ...
    def read_u8(self, offset: int) -> int: ...
    def write_u8(self, offset: int, value: int) -> None: ...
    def read_bytes(self, offset: int, length: int) -> bytes: ...
    def len(self) -> int: ...

class Ring:
    """Vyukov bounded MPMC ring buffer in shared memory."""

    def __init__(self, ring_offset: int) -> None: ...
    def init(self, region: ShmRegion, capacity: int) -> None: ...
    def offset(self) -> int: ...
    def capacity(self, region: ShmRegion) -> int: ...
    def try_enqueue(
        self,
        region: ShmRegion,
        slab: SlabAllocator,
        channel_name: bytes,
        msg_data: bytes | bytearray | memoryview,
        expiry_ts: float,
        pid: int,
        start_time: int,
    ) -> bool: ...
    def try_dequeue(
        self,
        region: ShmRegion,
        slab: SlabAllocator,
        now: float,
        pid: int,
        start_time: int,
    ) -> tuple[bytes, bytes] | None: ...
    def reset(self, region: ShmRegion) -> None: ...
    def compact(
        self, region: ShmRegion, slab: SlabAllocator, start_time: int
    ) -> None: ...

class SlabAllocator:
    """Size-class slab allocator for shared memory dynamic pool."""

    def __init__(self, pool_offset: int, pool_size: int) -> None: ...
    def init(self, region: ShmRegion) -> None: ...
    def alloc(self, region: ShmRegion, size: int) -> int: ...
    def free(self, region: ShmRegion, offset: int, size: int) -> None: ...
    def alloc_cold(self, region: ShmRegion, size: int) -> int: ...
    def free_cold(self, region: ShmRegion, offset: int, size: int) -> None: ...
    def reset(self, region: ShmRegion) -> None: ...

def fnv1a_hash(name: bytes) -> int: ...
def read_self_starttime() -> int: ...
def pid_dead(pid: int, start_time: int) -> bool: ...
def compute_offsets(
    max_channels: int,
    max_groups: int,
    max_members_per_group: int,
    max_processes: int,
) -> tuple[int, int, int, int, int, int]: ...
def size_class_for(size: int) -> int | None: ...
def shm_init(
    region: ShmRegion,
    total_size: int,
    inline_size: int,
    default_capacity: int,
    expiry: int,
    group_expiry: int,
    max_channels: int,
    max_groups: int,
    max_members_per_group: int,
    max_processes: int,
    slab: SlabAllocator,
) -> None: ...
def check_magic(region: ShmRegion) -> bool: ...
def read_version(region: ShmRegion) -> int: ...
def validate_config(
    region: ShmRegion,
    inline_size: int,
    default_capacity: int,
    max_channels: int,
    max_groups: int,
    max_members_per_group: int,
    max_processes: int,
) -> bool: ...
def channel_index_lookup(
    region: ShmRegion,
    name: str,
    max_channels: int,
) -> tuple[bool, int, int, int, bool]: ...
def channel_index_create(
    region: ShmRegion,
    name: str,
    ring_offset: int,
    capacity: int,
    non_local: bool,
    max_channels: int,
) -> tuple[int, bool]: ...
def group_index_lookup(
    region: ShmRegion,
    name: str,
    max_groups: int,
) -> tuple[bool, int, int, int, bool]: ...
def group_index_create_or_find(
    region: ShmRegion,
    name: str,
    slab: SlabAllocator,
    max_groups: int,
    max_members_per_group: int,
) -> tuple[int, int]: ...
def registry_register(
    region: ShmRegion,
    client_prefix: str,
    socket_path: str,
    pid: int,
    start_time: int,
    max_processes: int,
) -> int: ...
def registry_mark_dead(region: ShmRegion, slot_offset: int) -> None: ...
def registry_get_valid(
    region: ShmRegion,
    max_processes: int,
) -> list[tuple[int, bytes]]: ...
def group_member_read(
    region: ShmRegion,
    members_offset: int,
    index: int,
) -> tuple[bool, bytes, int]: ...
def group_member_add(
    region: ShmRegion,
    grp_slot_off: int,
    members_offset: int,
    channel_name: str,
    now: int,
    max_members: int,
    group_expiry: int,
) -> bool: ...
def group_member_remove(
    region: ShmRegion,
    grp_slot_off: int,
    members_offset: int,
    channel_name: str,
    max_members: int,
) -> bool: ...
def group_members_read_all(
    region: ShmRegion,
    members_offset: int,
    max_members: int,
    now: int,
    group_expiry: int,
) -> list[str]: ...
def registry_lookup_socket(
    region: ShmRegion,
    target_prefix: str,
    max_processes: int,
) -> bytes | None: ...
def flush(
    region: ShmRegion,
    slab: SlabAllocator,
    max_channels: int,
    max_groups: int,
) -> None: ...
def compact(
    region: ShmRegion,
    slab: SlabAllocator,
    max_channels: int,
    start_time: int,
) -> None: ...
