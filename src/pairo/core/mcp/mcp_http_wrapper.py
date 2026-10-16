"""Run an MCP server locally and expose its Codex HTTP configuration."""

from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from contextlib import suppress
from enum import Enum
import logging
from typing import Any, Protocol

from .servers import HttpMCPServer


class StreamableHttpMCPServer(Protocol):
    """The streamable-HTTP surface provided by the MCP Python SDK."""

    @property
    def name(self) -> str: ...

    async def run_streamable_http_async(
        self,
        *,
        host: str,
        port: int,
        streamable_http_path: str,
    ) -> None: ...

    def streamable_http_app(
        self,
        *,
        streamable_http_path: str,
        host: str,
    ) -> Any: ...


class MCPHttpLogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class MCPHttpWrapper(ABC):
    """Manage one local MCP streamable-HTTP server.

    ``boot_up`` starts the server in a background task. The returned
    ``HttpMCPServer`` from ``as_http_mcp_server`` can be placed directly in an
    an ``Agent`` after startup.
    """

    def __init__(
        self,
        port: int,
        host: str = "127.0.0.1",
        startup_timeout_sec: float = 10.0,
        log_level: MCPHttpLogLevel = MCPHttpLogLevel.ERROR,
    ) -> None:
        normalized_host = host.strip()
        if not normalized_host:
            raise ValueError("MCP HTTP host cannot be empty.")
        if not 1 <= port <= 65_535:
            raise ValueError("MCP HTTP port must be between 1 and 65535.")
        if startup_timeout_sec <= 0:
            raise ValueError("MCP startup timeout must be greater than zero.")

        self._host = normalized_host
        self._port = port
        self._startup_timeout_sec = startup_timeout_sec
        self._log_level = log_level
        self._server_task: asyncio.Task[None] | None = None
        self._uvicorn_server: Any | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._mcp_server = self.build_mcp_server()

    @property
    def is_running(self) -> bool:
        return self._server_task is not None and not self._server_task.done()
    
    @abstractmethod
    def build_mcp_server(self) -> StreamableHttpMCPServer: ...

    async def boot_up(self) -> None:
        """Start the MCP server on the configured host and port."""
        mcp_server = self._mcp_server
        async with self._lifecycle_lock:
            if self.is_running:
                return
            self._server_task = asyncio.create_task(
                self._serve_mcp_server(),
                name=f"mcp-http-{mcp_server.name}",
            )
            await asyncio.sleep(0)
            self._configure_loggers()
            if self._server_task.done():
                self._server_task.result()
            try:
                await self._wait_until_ready()
            except BaseException:
                await self._stop_server()
                raise

    def as_http_mcp_server(self) -> HttpMCPServer:
        """Return the configuration object for this local MCP endpoint."""
        return HttpMCPServer(
            name=self._mcp_server.name,
            url=f"http://{self._host}:{self._port}/mcp",
            startup_timeout_sec=self._startup_timeout_sec,
        )

    async def close(self) -> None:
        """Stop the background server task, if it is running."""
        async with self._lifecycle_lock:
            await self._stop_server()

    def _readiness_host(self) -> str:
        if self._host == "0.0.0.0":
            return "127.0.0.1"
        if self._host == "::":
            return "::1"
        return self._host

    async def _wait_until_ready(self) -> None:
        deadline = asyncio.get_running_loop().time() + self._startup_timeout_sec
        while True:
            server_task = self._server_task
            if server_task is None:
                raise RuntimeError("MCP server startup was cancelled.")
            if server_task.done():
                server_task.result()
            try:
                _reader, writer = await asyncio.open_connection(
                    self._readiness_host(),
                    self._port,
                )
            except OSError:
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError(
                        f"MCP server did not start within {self._startup_timeout_sec} seconds."
                    )
                await asyncio.sleep(0.01)
                continue
            writer.close()
            await writer.wait_closed()
            return

    async def _stop_server(self) -> None:
        logging.getLogger("asyncio").setLevel(logging.CRITICAL)
        if self._server_task is None:
            return
        if self._uvicorn_server is not None:
            self._uvicorn_server.should_exit = True
            await self._server_task
        else:
            self._server_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._server_task
        self._server_task = None
        self._uvicorn_server = None

    async def _serve_mcp_server(self) -> None:
        app_factory = getattr(self._mcp_server, "streamable_http_app", None)
        if app_factory is None:
            await self._mcp_server.run_streamable_http_async(
                host=self._host,
                port=self._port,
                streamable_http_path="/mcp",
            )
            return

        import uvicorn

        application = app_factory(
            streamable_http_path="/mcp",
            host=self._host,
        )
        configuration = uvicorn.Config(
            application,
            host=self._host,
            port=self._port,
            log_level=self._log_level.value.lower(),
        )
        self._uvicorn_server = uvicorn.Server(configuration)
        await self._uvicorn_server.serve()

    def _configure_loggers(self) -> None:
        """Apply one level to Uvicorn and MCP's HTTP transport loggers."""
        for logger_name in (
            "uvicorn",
            "uvicorn.access",
            "uvicorn.error",
            "mcp",
            "mcp.server",
            "mcp.server.streamable_http",
            "mcp.server.streamable_http_manager",
        ):
            logging.getLogger(logger_name).setLevel(self._log_level.value)


__all__ = ["MCPHttpLogLevel", "MCPHttpWrapper", "StreamableHttpMCPServer"]

