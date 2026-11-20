"""Keep blocking startup and shutdown off the ASGI event loop."""

from contextlib import asynccontextmanager

from anyio.to_thread import run_sync as run_in_threadpool


def lifespan_for(services):
    @asynccontextmanager
    async def lifespan(app):
        try:
            await run_in_threadpool(services.start)
            yield
        finally:
            await run_in_threadpool(services.close)

    return lifespan
