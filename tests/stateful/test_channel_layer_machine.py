"""Differential stateful test: SharedMemoryChannelLayer vs InMemoryChannelLayer.

Model-based (hypothesis.stateful): the same operation sequence is applied to
the IUT (shm) and a reference model (InMemoryChannelLayer), then the two are
forced to agree. The receive rule is preconditioned on a pending message, so it
never blocks on an empty channel — that alone cut this file's runtime from ~53s
to well under a second.

The layer is bound to a single event loop per thread (E-03), so all ops run on
one persistent loop thread.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from typing import TYPE_CHECKING, TypeVar, cast

from hypothesis import HealthCheck, settings
from hypothesis.stateful import (
    Bundle,
    RuleBasedStateMachine,
    initialize,
    precondition,
    rule,
)
from typing_extensions import override

from channels_shm import SharedMemoryChannelLayer
from tests.strategies import st_messages

if TYPE_CHECKING:
    from collections.abc import Awaitable, Coroutine

    from channels_shm.serializer import Message

T = TypeVar("T")

# Per-receive bound: with a pending message both sides must deliver promptly;
# the timeout only guards against an actual divergence (a lost/dropped message).
_RECEIVE_TIMEOUT = 2.0


class _EventLoopThread:
    """Runs a persistent event loop in a background thread."""

    _loop: asyncio.AbstractEventLoop
    _thread: threading.Thread

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def run(self, coro: Awaitable[T]) -> T:
        """Run a coroutine on the persistent loop and return the result."""
        future = asyncio.run_coroutine_threadsafe(
            cast("Coroutine[object, object, T]", coro), self._loop
        )
        return future.result(timeout=10)

    def close(self) -> None:
        _ = self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        self._loop.close()


class ChannelLayerComparison(RuleBasedStateMachine):
    """Model-based: IUT (shm) vs reference (InMemory) — sequential equivalence.

    Each send/group_send is mirrored to the model and the error classes must
    agree; each receive (only when a message is pending) must return the same
    value from both.
    """

    _loop_thread: _EventLoopThread
    _prefix: str
    model: object
    iut: SharedMemoryChannelLayer
    _pending: dict[str, int]

    def __init__(self) -> None:
        super().__init__()
        self._pending = {}
        self._loop_thread = _EventLoopThread()
        prefix = f"test_sm_{uuid.uuid4().hex[:8]}"
        self._prefix = prefix

        # Create layers on the persistent loop so the pump stays bound to it.
        self.model = self._loop_thread.run(self._create_model())
        self.iut = self._loop_thread.run(self._create_iut(prefix))

    @staticmethod
    async def _create_model() -> object:
        from channels.layers import InMemoryChannelLayer

        return InMemoryChannelLayer(expiry=60, capacity=10)

    @staticmethod
    async def _create_iut(prefix: str) -> SharedMemoryChannelLayer:
        return SharedMemoryChannelLayer(
            expiry=60,
            capacity=10,
            prefix=prefix,
            shm_size=8 * 1024 * 1024,
            max_channels=50,
            max_groups=10,
            max_processes=4,
            watchdog_interval=None,
        )

    channels: Bundle[str] = Bundle("channels")

    # Both sides have this capacity; keeping a channel's in-flight (unsent-then-
    # unreceived) messages below it avoids the at-capacity divergence (the model
    # raises ChannelFull on a full queue, the shm side drains via its pump and
    # returns None). That overflow semantic is covered in tests/layer, not here.
    _CAPACITY: int = 10

    @initialize(target=channels)
    def new_channel(self) -> str:
        return self._loop_thread.run(self.iut.new_channel("test."))

    def _has_send_room(self) -> bool:
        """True while some in-flight budget remains (precondition for send)."""
        return sum(self._pending.values()) < self._CAPACITY

    def _has_pending(self) -> bool:
        """True while any message is awaiting a receive (precondition)."""
        return sum(self._pending.values()) > 0

    @precondition(_has_send_room)
    @rule(channel=channels, msg=st_messages())
    def send(self, channel: str, msg: Message) -> None:
        iut_exc = self._run_send(self.iut, channel, msg)
        model_exc = self._run_send(self.model, channel, msg)
        assert type(iut_exc) is type(model_exc), (
            f"send divergence: iut raised {iut_exc!r}, model raised {model_exc!r}"
        )
        if iut_exc is None:
            self._pending[channel] = self._pending.get(channel, 0) + 1

    @precondition(_has_pending)
    @rule(channel=channels)
    def receive(self, channel: str) -> None:
        # A precondition cannot see the bundle value, so it gates on the global
        # pending count; skip when THIS channel has nothing pending (else the
        # rule would block on an empty queue, not compare anything).
        if self._pending.get(channel, 0) == 0:
            return
        iut = self._run_receive(self.iut, channel)
        model = self._run_receive(self.model, channel)
        assert iut is not None, f"receive divergence: iut={iut!r}, model={model!r}"
        assert model is not None, f"receive divergence: iut={iut!r}, model={model!r}"
        assert _normalize(iut) == _normalize(model)
        self._pending[channel] -= 1

    def _run_send(
        self,
        layer: object,
        channel: str,
        msg: Message,
    ) -> type[BaseException] | None:
        try:
            layer_typed = cast("SharedMemoryChannelLayer", layer)
            _ = self._loop_thread.run(layer_typed.send(channel, msg))
        except Exception as e:
            return type(e)
        else:
            return None

    def _run_receive(
        self,
        layer: object,
        channel: str,
    ) -> Message | None:
        try:

            async def _recv() -> Message:
                layer_typed = cast("SharedMemoryChannelLayer", layer)
                return await asyncio.wait_for(
                    layer_typed.receive(channel), timeout=_RECEIVE_TIMEOUT
                )

            return self._loop_thread.run(_recv())
        except Exception:
            return None

    @override
    def teardown(self) -> None:
        try:
            _ = self._loop_thread.run(self.iut.close())
        except Exception:
            pass
        try:
            self.iut.unlink_shm()
        except Exception:
            pass
        try:
            self._loop_thread.close()
        except Exception:
            pass


def _normalize(value: object) -> object:
    """Recursively normalize tuples to lists for comparison."""
    if isinstance(value, tuple):
        elems = cast("tuple[object, ...]", value)  # ty: ignore[redundant-cast]
        return [_normalize(v) for v in elems]
    if isinstance(value, list):
        elems = cast("list[object]", value)
        return [_normalize(v) for v in elems]
    if isinstance(value, dict):
        entries = cast("dict[object, object]", value)
        return {k: _normalize(v) for k, v in entries.items()}
    return value


# Export the test case.
TestChannelLayerMachine = ChannelLayerComparison.TestCase  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
TestChannelLayerMachine.settings = settings(
    stateful_step_count=20,
    max_examples=20,
    # Receives block on real waits; timing is legitimately variable here.
    deadline=None,
    suppress_health_check=[HealthCheck.differing_executors],
)
