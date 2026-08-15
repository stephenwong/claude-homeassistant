from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools.ha_mcp_bridge import (
    SSEEvent,
    main,
    parse_sse_lines,
    resolve_mcp_url,
    run_bridge,
)


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

    def test_parse_multi_line_data(self):
        lines = [
            "event: message",
            "data: line1",
            "data: line2",
            "",
        ]
        events = list(parse_sse_lines(lines))
        assert len(events) == 1
        assert events[0] == SSEEvent(event="message", data="line1\nline2")

    def test_parse_default_message_event_without_explicit_event_line(self):
        lines = [
            'data: {"jsonrpc": "2.0", "method": "notify"}',
            "",
        ]
        events = list(parse_sse_lines(lines))
        assert len(events) == 1
        assert events[0] == SSEEvent(
            event="message",
            data='{"jsonrpc": "2.0", "method": "notify"}',
        )

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


class TestRunBridge:
    @pytest.mark.anyio
    async def test_run_bridge_streamable_http_sse_flow(self, capsys):
        input_lines = [
            "\n",  # empty line should be skipped
            '{"jsonrpc":"2.0","id":1,"method":"initialize"}\n',
            "",  # EOF
        ]

        async def mock_read_stdin() -> str:
            if input_lines:
                return input_lines.pop(0)
            return ""

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

        post_ctx = MagicMock()
        post_ctx.__aenter__ = AsyncMock(return_value=mock_post_response)
        post_ctx.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.post.return_value = post_ctx

        session_ctx = MagicMock()
        session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        session_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch("aiohttp.ClientSession", return_value=session_ctx):
            exit_code = await run_bridge(
                "http://ha-host:9583/private_token", stdin_reader=mock_read_stdin
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
    async def test_run_bridge_streamable_http_json_flow(self, capsys):
        input_lines = [
            '{"jsonrpc":"2.0","id":1,"method":"ping"}\n',
            "",
        ]

        async def mock_read_stdin() -> str:
            if input_lines:
                return input_lines.pop(0)
            return ""

        mock_post_response = AsyncMock()
        mock_post_response.status = 200
        mock_post_response.headers = {"content-type": "application/json"}
        mock_post_response.text = AsyncMock(
            return_value='{"jsonrpc":"2.0","id":1,"result":{}}'
        )

        post_ctx = MagicMock()
        post_ctx.__aenter__ = AsyncMock(return_value=mock_post_response)
        post_ctx.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.post.return_value = post_ctx

        session_ctx = MagicMock()
        session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        session_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch("aiohttp.ClientSession", return_value=session_ctx):
            exit_code = await run_bridge(
                "http://ha-host:9583/private_token", stdin_reader=mock_read_stdin
            )
            assert exit_code == 0

        captured = capsys.readouterr()
        assert '{"jsonrpc":"2.0","id":1,"result":{}}' in captured.out

    @pytest.mark.anyio
    async def test_run_bridge_post_warning_and_exception(self, capsys):
        input_lines = [
            '{"jsonrpc":"2.0","id":1,"method":"ping"}\n',
            '{"jsonrpc":"2.0","id":2,"method":"ping"}\n',
            "",
        ]

        async def mock_read_stdin() -> str:
            if input_lines:
                return input_lines.pop(0)
            return ""

        call_count = 0

        async def mock_post_enter(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                res = AsyncMock()
                res.status = 500
                res.headers = {}
                return res
            raise Exception("Post transport failed")

        post_ctx = MagicMock()
        post_ctx.__aenter__ = AsyncMock(side_effect=mock_post_enter)
        post_ctx.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.post.return_value = post_ctx

        session_ctx = MagicMock()
        session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        session_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch("aiohttp.ClientSession", return_value=session_ctx):
            exit_code = await run_bridge(
                "http://ha-host:9583/token", stdin_reader=mock_read_stdin
            )
            assert exit_code == 0

        captured = capsys.readouterr()
        assert "returned status 500" in captured.err
        assert "Post transport failed" in captured.err


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
