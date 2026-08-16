import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools.ha_mcp_bridge import (
    SSEEvent,
    main,
    parse_sse_lines,
    resolve_mcp_url,
    run_bridge,
)


def _make_stdin_reader(lines: list[str]):
    queue = list(lines)

    async def read_stdin() -> str:
        if queue:
            return queue.pop(0)
        return ""

    return read_stdin


def _mock_aiohttp_session(
    post_response: Any = None,
    *,
    post_side_effect: Any = None,
) -> tuple[MagicMock, MagicMock]:
    """Return (session_ctx, mock_session) configured for aiohttp.ClientSession patch."""
    mock_session = MagicMock()
    if post_response is not None or post_side_effect is not None:
        post_ctx = MagicMock()
        if post_side_effect is not None:
            post_ctx.__aenter__ = AsyncMock(side_effect=post_side_effect)
        else:
            post_ctx.__aenter__ = AsyncMock(return_value=post_response)
        post_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_session.post.return_value = post_ctx

    session_ctx = MagicMock()
    session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    session_ctx.__aexit__ = AsyncMock(return_value=None)
    return session_ctx, mock_session


class TestResolveMCPUrl:
    def test_resolve_from_file(self, tmp_path: Path):
        url_file = tmp_path / ".ha-mcp-url"
        url_file.write_text("http://192.168.1.50:9583/private_abc123\n")

        resolved = resolve_mcp_url(tmp_path)
        assert resolved == "http://192.168.1.50:9583/private_abc123"

    def test_resolve_from_env_fallback(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("HA_MCP_URL", "https://ha.example.com:9583/private_xyz")
        resolved = resolve_mcp_url(tmp_path)
        assert resolved == "https://ha.example.com:9583/private_xyz"

    def test_resolve_file_takes_precedence_over_env(self, tmp_path: Path, monkeypatch):
        url_file = tmp_path / ".ha-mcp-url"
        url_file.write_text("http://file-host:9583/private_file\n")
        monkeypatch.setenv("HA_MCP_URL", "http://env-host:9583/private_env")

        resolved = resolve_mcp_url(tmp_path)
        assert resolved == "http://file-host:9583/private_file"

    def test_resolve_empty_file_falls_back_to_env(self, tmp_path: Path, monkeypatch):
        url_file = tmp_path / ".ha-mcp-url"
        url_file.write_text("   \n")
        monkeypatch.setenv("HA_MCP_URL", "http://env-host:9583/private_env")

        resolved = resolve_mcp_url(tmp_path)
        assert resolved == "http://env-host:9583/private_env"

    def test_resolve_unreadable_file_falls_back_to_env(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        url_file = tmp_path / ".ha-mcp-url"
        url_file.write_text("http://file-host:9583/token\n")
        monkeypatch.setenv("HA_MCP_URL", "http://env-host:9583/token")

        with patch.object(Path, "read_text", side_effect=OSError("Permission denied")):
            resolved = resolve_mcp_url(tmp_path)
            assert resolved == "http://env-host:9583/token"

        captured = capsys.readouterr()
        assert "Warning: Could not read" in captured.err

    def test_resolve_missing_everywhere(self, tmp_path: Path, monkeypatch, capsys):
        monkeypatch.delenv("HA_MCP_URL", raising=False)
        resolved = resolve_mcp_url(tmp_path)
        assert resolved is None
        captured = capsys.readouterr()
        assert "No HA MCP URL found" in captured.err

    def test_resolve_invalid_url_scheme_in_file(self, tmp_path: Path, capsys):
        url_file = tmp_path / ".ha-mcp-url"
        url_file.write_text("ftp://invalid-scheme:9583/token\n")

        resolved = resolve_mcp_url(tmp_path)
        assert resolved is None
        captured = capsys.readouterr()
        assert "Invalid MCP URL" in captured.err

    def test_resolve_invalid_url_scheme_in_env(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        monkeypatch.setenv("HA_MCP_URL", "invalid-scheme://token")
        resolved = resolve_mcp_url(tmp_path)
        assert resolved is None
        captured = capsys.readouterr()
        assert "Invalid MCP URL in HA_MCP_URL" in captured.err


class TestParseSSELines:
    def test_parse_endpoint_event(self):
        lines = [
            "event: endpoint",
            "data: /message?sessionId=12345",
            "",
        ]
        events = list(parse_sse_lines(lines))
        assert len(events) == 1
        assert events[0] == SSEEvent(event="endpoint", data="/message?sessionId=12345")

    def test_parse_message_event(self):
        lines = [
            "event: message",
            'data: {"jsonrpc": "2.0", "result": {"tools": []}, "id": 1}',
            "",
        ]
        events = list(parse_sse_lines(lines))
        assert len(events) == 1
        assert events[0] == SSEEvent(
            event="message",
            data='{"jsonrpc": "2.0", "result": {"tools": []}, "id": 1}',
        )

    def test_parse_multiple_events(self):
        lines = [
            "event: endpoint",
            "data: /message?sessionId=abc",
            "",
            "event: message",
            "data: line1",
            "",
        ]
        events = list(parse_sse_lines(lines))
        assert len(events) == 2
        assert events[0].event == "endpoint"
        assert events[1].event == "message"
        assert events[1].data == "line1"

    def test_parse_multiline_data(self):
        lines = [
            "event: message",
            "data: line1",
            "data: line2",
            "",
        ]
        events = list(parse_sse_lines(lines))
        assert len(events) == 1
        assert events[0].data == "line1\nline2"

    def test_parse_default_event_type(self):
        lines = [
            "data: hello",
            "",
        ]
        events = list(parse_sse_lines(lines))
        assert len(events) == 1
        assert events[0].event == "message"
        assert events[0].data == "hello"

    def test_parse_empty_data_line(self):
        lines = [
            "data",
            "",
        ]
        events = list(parse_sse_lines(lines))
        assert len(events) == 1
        assert events[0] == SSEEvent(event="message", data="")

    def test_parse_comments_ignored(self):
        lines = [
            ": keepalive comment",
            "event: message",
            "data: test",
            "",
        ]
        events = list(parse_sse_lines(lines))
        assert len(events) == 1
        assert events[0] == SSEEvent(event="message", data="test")

    def test_parse_sse_preserves_indentation(self):
        lines = [
            "event: message",
            "data:   indented line",
            "",
        ]
        events = list(parse_sse_lines(lines))
        assert len(events) == 1
        assert events[0] == SSEEvent(event="message", data="  indented line")


class TestRunBridge:
    @pytest.mark.anyio
    async def test_run_bridge_streamable_http_sse_flow(self, capsys):
        stdin_reader = _make_stdin_reader(
            [
                "\n",  # empty line should be skipped
                '{"jsonrpc":"2.0","id":1,"method":"initialize"}\n',
                "",  # EOF
            ]
        )

        mock_sse_lines = [
            b"event: message\n",
            b'data: {"jsonrpc":"2.0","id":1,"result":{"tools":[]}}\n',
            b"\n",
        ]

        class MockContent:
            def __init__(self, lines):
                self._lines = list(lines)

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._lines:
                    return self._lines.pop(0)
                raise StopAsyncIteration

        mock_post_response = AsyncMock()
        mock_post_response.status = 200
        mock_post_response.headers = {
            "content-type": "text/event-stream",
            "mcp-session-id": "session-12345",
        }
        mock_post_response.content = MockContent(mock_sse_lines)

        session_ctx, mock_session = _mock_aiohttp_session(mock_post_response)

        with patch("aiohttp.ClientSession", return_value=session_ctx):
            exit_code = await run_bridge(
                "http://ha-host:9583/private_token", stdin_reader=stdin_reader
            )
            assert exit_code == 0

            mock_session.post.assert_called_once()
            called_url = mock_session.post.call_args[0][0]
            assert called_url == "http://ha-host:9583/private_token"
            assert (
                mock_session.post.call_args[1]["data"]
                == '{"jsonrpc":"2.0","id":1,"method":"initialize"}'
            )
            assert (
                mock_session.post.call_args[1]["headers"]["Accept"]
                == "application/json, text/event-stream"
            )

        captured = capsys.readouterr()
        assert '{"jsonrpc":"2.0","id":1,"result":{"tools":[]}}' in captured.out

    @pytest.mark.anyio
    async def test_run_bridge_split_utf8_chunks(self, capsys):
        stdin_reader = _make_stdin_reader(
            [
                '{"jsonrpc":"2.0","id":1,"method":"read"}\n',
                "",
            ]
        )

        full_payload = 'event: message\ndata: {"text":"🌡️ 22°C"}\n\n'.encode()
        # Split full_payload right inside a multi-byte sequence
        split_idx = full_payload.index("🌡️".encode()[:2]) + 2
        chunk1 = full_payload[:split_idx]
        chunk2 = full_payload[split_idx:]

        class MockChunkContent:
            def __init__(self, chunks):
                self._chunks = list(chunks)

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._chunks:
                    return self._chunks.pop(0)
                raise StopAsyncIteration

        mock_post_response = AsyncMock()
        mock_post_response.status = 200
        mock_post_response.headers = {
            "content-type": "text/event-stream",
        }
        mock_post_response.content = MockChunkContent([chunk1, chunk2])

        session_ctx, _ = _mock_aiohttp_session(mock_post_response)

        with patch("aiohttp.ClientSession", return_value=session_ctx):
            exit_code = await run_bridge(
                "http://ha-host:9583/token", stdin_reader=stdin_reader
            )
            assert exit_code == 0

        captured = capsys.readouterr()
        assert '{"text":"🌡️ 22°C"}' in captured.out
        assert "\ufffd" not in captured.out

    @pytest.mark.anyio
    async def test_run_bridge_streamable_http_json_flow(self, capsys):
        stdin_reader = _make_stdin_reader(
            [
                '{"jsonrpc":"2.0","id":1,"method":"ping"}\n',
                "",
            ]
        )

        mock_post_response = AsyncMock()
        mock_post_response.status = 200
        mock_post_response.headers = {"content-type": "application/json"}
        mock_post_response.text = AsyncMock(
            return_value='{"jsonrpc":"2.0","id":1,"result":{}}'
        )

        session_ctx, _ = _mock_aiohttp_session(mock_post_response)

        with patch("aiohttp.ClientSession", return_value=session_ctx):
            exit_code = await run_bridge(
                "http://ha-host:9583/private_token", stdin_reader=stdin_reader
            )
            assert exit_code == 0

        captured = capsys.readouterr()
        assert '{"jsonrpc":"2.0","id":1,"result":{}}' in captured.out

    @pytest.mark.anyio
    async def test_run_bridge_post_warning_and_exception(self, capsys):
        stdin_reader = _make_stdin_reader(
            [
                '{"jsonrpc":"2.0","id":1,"method":"ping"}\n',
                '{"jsonrpc":"2.0","id":2,"method":"ping"}\n',
                "",
            ]
        )

        call_count = 0

        async def mock_post_enter(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                res = AsyncMock()
                res.status = 500
                res.headers = {}
                res.text = AsyncMock(return_value="")
                return res
            raise Exception("Post transport failed")

        session_ctx, _ = _mock_aiohttp_session(post_side_effect=mock_post_enter)

        with patch("aiohttp.ClientSession", return_value=session_ctx):
            exit_code = await run_bridge(
                "http://ha-host:9583/token", stdin_reader=stdin_reader
            )
            assert exit_code == 0

        captured = capsys.readouterr()
        assert "returned status 500" in captured.err
        assert "Post transport failed" in captured.err

    @pytest.mark.anyio
    async def test_run_bridge_cancels_pending_stop_waiter(self):
        stdin_reader = _make_stdin_reader(
            [
                '{"jsonrpc":"2.0","id":1,"method":"ping"}\n',
                '{"jsonrpc":"2.0","id":2,"method":"ping"}\n',
                "",
            ]
        )

        mock_post_response = AsyncMock()
        mock_post_response.status = 200
        mock_post_response.headers = {"content-type": "application/json"}
        mock_post_response.text = AsyncMock(
            return_value='{"jsonrpc":"2.0","id":1,"result":{}}'
        )

        session_ctx, _ = _mock_aiohttp_session(mock_post_response)

        tasks_created: list[asyncio.Task] = []
        orig_create_task = asyncio.create_task

        def tracking_create_task(coro, **kwargs):
            task = orig_create_task(coro, **kwargs)
            tasks_created.append(task)
            return task

        with (
            patch("aiohttp.ClientSession", return_value=session_ctx),
            patch("asyncio.create_task", side_effect=tracking_create_task),
        ):
            exit_code = await run_bridge(
                "http://ha-host:9583/token", stdin_reader=stdin_reader
            )
            assert exit_code == 0

        assert len(tasks_created) > 0
        for task in tasks_created:
            assert task.done()

    @pytest.mark.anyio
    async def test_run_bridge_handles_http_error_response(self, capsys):
        """HTTP error responses with JSON-RPC payload are still forwarded to stdout."""
        stdin_reader = _make_stdin_reader(
            [
                '{"jsonrpc":"2.0","id":1,"method":"bad"}\n',
                "",
            ]
        )

        mock_post_response = AsyncMock()
        mock_post_response.status = 400
        mock_post_response.headers = {"content-type": "application/json"}
        mock_post_response.text = AsyncMock(
            return_value=(
                '{"jsonrpc":"2.0","error":{"code":-32600,'
                '"message":"Invalid Request"},"id":1}'
            )
        )

        session_ctx, _ = _mock_aiohttp_session(mock_post_response)

        with patch("aiohttp.ClientSession", return_value=session_ctx):
            exit_code = await run_bridge(
                "http://ha-host:9583/token", stdin_reader=stdin_reader
            )
            assert exit_code == 0

        captured = capsys.readouterr()
        assert "Warning: POST" in captured.err
        assert '"error"' in captured.out

    @pytest.mark.anyio
    async def test_run_bridge_exits_when_reader_raises_exception(self):
        """Bridge exits cleanly when reader raises an unhandled exception."""

        async def failing_read_stdin() -> str:
            raise RuntimeError("stdin stream corrupted")

        session_ctx, _ = _mock_aiohttp_session()

        with patch("aiohttp.ClientSession", return_value=session_ctx):
            exit_code = await run_bridge(
                "http://ha-host:9583/token", stdin_reader=failing_read_stdin
            )
            assert exit_code == 0


class TestMain:
    def test_main_no_url(self, monkeypatch, capsys):
        monkeypatch.delenv("HA_MCP_URL", raising=False)
        with patch("tools.ha_mcp_bridge.resolve_mcp_url", return_value=None):
            exit_code = main()
            assert exit_code == 1

    def test_main_success(self, monkeypatch):
        async def mock_run_bridge(url: str) -> int:
            assert url == "http://ha:9583/token"
            return 0

        with (
            patch(
                "tools.ha_mcp_bridge.resolve_mcp_url",
                return_value="http://ha:9583/token",
            ),
            patch("tools.ha_mcp_bridge.run_bridge", new=mock_run_bridge),
        ):
            exit_code = main()
            assert exit_code == 0

    def test_main_keyboard_interrupt(self, monkeypatch):
        async def mock_run_bridge(url: str) -> int:
            raise KeyboardInterrupt

        with (
            patch(
                "tools.ha_mcp_bridge.resolve_mcp_url",
                return_value="http://ha:9583/token",
            ),
            patch("tools.ha_mcp_bridge.run_bridge", new=mock_run_bridge),
        ):
            exit_code = main()
            assert exit_code == 0
