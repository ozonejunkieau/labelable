"""Dual HTTP+HTTPS mode must run the app lifespan exactly once.

Both listeners serve the same app object, so a second lifespan would build a
second set of printers, queue workers and Cloudflare consumers.
"""

import asyncio
from contextlib import asynccontextmanager

import aiohttp
import pytest
import uvicorn
from fastapi import FastAPI

from labelable.__main__ import serve_dual


def _app_with_counter() -> tuple[FastAPI, dict[str, int]]:
    counts = {"startup": 0, "shutdown": 0}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        counts["startup"] += 1
        yield
        counts["shutdown"] += 1

    app = FastAPI(lifespan=lifespan)

    @app.get("/ping")
    async def ping() -> dict[str, int]:
        return counts

    return app, counts


def _server(app: FastAPI) -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    return server


def _port(server: uvicorn.Server) -> int:
    return server.servers[0].sockets[0].getsockname()[1]


async def _wait_started(*servers: uvicorn.Server) -> None:
    for _ in range(500):
        if all(s.started for s in servers):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("servers did not start")


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["primary", "secondary"])
async def test_lifespan_runs_once_and_both_listeners_serve(stop: str):
    app, counts = _app_with_counter()
    primary, secondary = _server(app), _server(app)
    task = asyncio.create_task(serve_dual(primary, secondary))
    await _wait_started(primary, secondary)

    async with aiohttp.ClientSession() as session:
        for server in (primary, secondary):
            async with session.get(f"http://127.0.0.1:{_port(server)}/ping") as resp:
                assert resp.status == 200

    assert counts["startup"] == 1

    # Stopping either listener stops both, and shutdown also runs once
    (primary if stop == "primary" else secondary).should_exit = True
    await asyncio.wait_for(task, timeout=10)
    assert primary.should_exit and secondary.should_exit
    assert counts == {"startup": 1, "shutdown": 1}


def test_secondary_not_started_when_primary_startup_fails():
    @asynccontextmanager
    async def broken(app: FastAPI):
        raise RuntimeError("bad config")
        yield

    app = FastAPI(lifespan=broken)
    primary, secondary = _server(app), _server(app)
    # uvicorn exits the process with code 3 on a failed startup. asyncio
    # re-raises SystemExit out of the event loop rather than through await,
    # so it has to be caught around the loop.
    with pytest.raises(SystemExit):
        asyncio.run(serve_dual(primary, secondary))
    assert not secondary.started
