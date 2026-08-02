"""L2.5: Hypothesis stateful test comparing IUT with InMemoryChannelLayer."""

from __future__ import annotations

import asyncio
import threading
import uuid
from typing import TYPE_CHECKING, TypeVar, cast

from hypothesis import HealthCheck, settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    Bundle,
    RuleBasedStateMachine,
    initialize,
    invariant,
    rule,
)
from typing_extensions import override

from channels_shm import SharedMemoryChannelLayer

if TYPE_CHECKING:
    from collections.abc import Awaitable, Coroutine

    from channels_shm.serializer import Message

T = TypeVar("T")


def st_sample_message() -> st.SearchStrategy[Message]:
    """Simple message strategy for the state machine."""
    return cast(
        "st.SearchStrategy[Message]",
        st.fixed_dictionaries(
            {
                "type": st.sampled_from(["test.a", "test.b", "test.c"]),
                "value": st.integers(min_value=0, max_value=100),
            },
        ),
    )


class _EventLoopThread:
    """Manages a persistent event loop in a background thread."""

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

    Uses a persistent event loop thread to avoid the issue where each
    async_to_sync call creates a new event loop, breaking pump registration.
    """

    _loop_thread: _EventLoopThread
    _prefix: str
    # InMemoryChannelLayer is dynamically imported and untyped; treat as object
    # and cast to SharedMemoryChannelLayer for method access (same interface).
    model: object
    iut: SharedMemoryChannelLayer

    def __init__(self) -> None:
        super().__init__()
        self._loop_thread = _EventLoopThread()
        prefix = f"test_sm_{uuid.uuid4().hex[:8]}"
        self._prefix = prefix

        # Create layers on the persistent loop
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

    @initialize(target=channels)
    def new_channel(self) -> str:
        return self._loop_thread.run(self.iut.new_channel("test."))

    @rule(channel=channels, msg=st_sample_message())
    def send(self, channel: str, msg: Message) -> None:
        iut_exc = self._run_send(self.iut, channel, msg)
        model_exc = self._run_send(self.model, channel, msg)
        assert type(iut_exc) is type(model_exc)

    @rule(channel=channels)
    def receive(self, channel: str) -> None:
        iut_result = self._run_receive(self.iut, channel)
        model_result = self._run_receive(self.model, channel)
        if iut_result is not None and model_result is not None:
            assert _normalize(iut_result) == _normalize(model_result)

    @invariant()
    def layer_alive(self) -> None:
        assert self.iut is not None

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
                return await asyncio.wait_for(layer_typed.receive(channel), timeout=2.0)

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
            model_typed = cast("SharedMemoryChannelLayer", self.model)
            _ = self._loop_thread.run(model_typed.flush())
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


# Export the test case
TestChannelLayerMachine = ChannelLayerComparison.TestCase  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
TestChannelLayerMachine.settings = settings(
    stateful_step_count=10,
    max_examples=5,
    deadline=None,
    suppress_health_check=[HealthCheck.differing_executors],
)
