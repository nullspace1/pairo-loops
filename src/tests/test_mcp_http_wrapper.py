"""Lifecycle and endpoint contract tests for local MCP HTTP hosting."""

import asyncio

import pytest

from pairo.core.mcp.mcp_http_wrapper import MCPHttpLogLevel, MCPHttpWrapper


class FakeLegacyServer:
    name = "fake-server"

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def run_streamable_http_async(self, **kwargs) -> None:
        self.calls.append(kwargs)
        await asyncio.Event().wait()


class HarnessWrapper(MCPHttpWrapper):
    fake_server = FakeLegacyServer()

    def build_mcp_server(self):
        return self.fake_server


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"port": 0}, "between 1 and 65535"),
        ({"port": 65536}, "between 1 and 65535"),
        ({"port": 8080, "host": "  "}, "host cannot be empty"),
        ({"port": 8080, "startup_timeout_sec": 0}, "greater than zero"),
    ],
)
def test_wrapper_validates_constructor_arguments(kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        HarnessWrapper(**kwargs)


@pytest.mark.parametrize(
    ("bound", "readiness"),
    [("0.0.0.0", "127.0.0.1"), ("::", "::1"), ("localhost", "localhost")],
)
def test_wrapper_maps_wildcard_hosts_for_readiness(bound, readiness) -> None:
    wrapper = HarnessWrapper(port=8080, host=bound)
    assert wrapper._readiness_host() == readiness
    assert wrapper.as_http_mcp_server().url == f"http://{bound}:8080/mcp"


def test_wrapper_starts_legacy_server_once_and_closes_it(monkeypatch) -> None:
    class FakeWriter:
        def close(self) -> None:
            pass

        async def wait_closed(self) -> None:
            pass

    async def scenario() -> None:
        server = FakeLegacyServer()

        class IsolatedWrapper(MCPHttpWrapper):
            def build_mcp_server(self):
                return server

        async def open_connection(host, port):
            assert (host, port) == ("127.0.0.1", 9123)
            return object(), FakeWriter()

        monkeypatch.setattr(asyncio, "open_connection", open_connection)
        wrapper = IsolatedWrapper(
            port=9123,
            startup_timeout_sec=0.1,
            log_level=MCPHttpLogLevel.WARNING,
        )
        await wrapper.boot_up()
        await wrapper.boot_up()
        assert wrapper.is_running
        assert server.calls == [
            {
                "host": "127.0.0.1",
                "port": 9123,
                "streamable_http_path": "/mcp",
            }
        ]
        await wrapper.close()
        await wrapper.close()
        assert not wrapper.is_running

    asyncio.run(scenario())
