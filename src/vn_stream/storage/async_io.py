"""Drain owned filesystem work before propagating request cancellation."""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")


async def filesystem_call(function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    """Keep a cancelled request from closing a file still used by its worker."""
    worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not worker.cancelled():
            worker.exception()
        raise


@asynccontextmanager
async def filesystem_resource(
    acquire: Callable[P, T], release: Callable[[T], None], *args: P.args, **kwargs: P.kwargs
) -> AsyncIterator[T]:
    """Own a worker-acquired resource even when cancellation precedes its handoff."""
    owned: list[T] = []

    def open_resource() -> T:
        value = acquire(*args, **kwargs)
        owned.append(value)
        return value

    try:
        value = await filesystem_call(open_resource)
        yield value
    finally:
        if owned:
            await filesystem_call(release, owned.pop())
