"""Tests for tools/ha/client.py — the shared HA REST API client."""

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
import requests

from tests.helpers import make_response
from tools.common import HARequestError, MissingTokenError
from tools.ha.client import HAClient, HAWSClient


def _mock_session() -> MagicMock:
    return MagicMock(spec=requests.sessions.Session)


class TestInit:
    def test_valid_construction(self):
        c = HAClient("http://ha.local:8123", "tok", timeout=15)
        assert c.url == "http://ha.local:8123"
        assert c.token == "tok"
        assert c.timeout == 15

    def test_url_trailing_slash_stripped(self):
        c = HAClient("http://ha.local:8123/", "tok")
        assert c.url == "http://ha.local:8123"

    def test_missing_token_raises(self):
        with pytest.raises(MissingTokenError, match="HA_TOKEN") as exc_info:
            HAClient("http://ha.local:8123", "")
        assert isinstance(exc_info.value, HARequestError)

    def test_headers_format(self):
        c = HAClient("http://ha.local:8123", "abc123")
        assert c.headers == {
            "Authorization": "Bearer abc123",
            "Content-Type": "application/json",
        }


class TestFromEnv:
    """Tests for HAClient.from_env().

    These rely on the autouse ``_stub_load_env_file`` fixture in
    ``tests/conftest.py``, which no-ops the shared ``load_env_file`` helper
    and the stale-sensor validator's reference. No per-test patching is needed.
    """

    @pytest.mark.parametrize(
        "client_cls", [HAClient, HAWSClient], ids=["rest", "websocket"]
    )
    def test_reads_env_vars(self, monkeypatch, client_cls):
        monkeypatch.setenv("HA_URL", "http://ha.example:8123")
        monkeypatch.setenv("HA_TOKEN", "env-token")
        monkeypatch.setenv("HA_REQUEST_TIMEOUT", "42")
        c = client_cls.from_env()
        assert c.url == "http://ha.example:8123"
        assert c.token == "env-token"
        assert c.timeout == 42

    @pytest.mark.parametrize(
        "client_cls", [HAClient, HAWSClient], ids=["rest", "websocket"]
    )
    def test_uses_defaults_when_unset(self, monkeypatch, client_cls):
        monkeypatch.delenv("HA_URL", raising=False)
        monkeypatch.delenv("HA_TOKEN", raising=False)
        monkeypatch.delenv("HA_REQUEST_TIMEOUT", raising=False)
        with pytest.raises(HARequestError, match="HA_TOKEN"):
            client_cls.from_env()

    @pytest.mark.parametrize(
        "client_cls", [HAClient, HAWSClient], ids=["rest", "websocket"]
    )
    def test_default_url_when_only_token_set(self, monkeypatch, client_cls):
        monkeypatch.delenv("HA_URL", raising=False)
        monkeypatch.setenv("HA_TOKEN", "tok")
        monkeypatch.delenv("HA_REQUEST_TIMEOUT", raising=False)
        c = client_cls.from_env()
        assert c.url == "http://homeassistant.local:8123"
        assert c.timeout == 10

    def test_invalid_timeout_warns_and_uses_default(self, monkeypatch, capsys):
        monkeypatch.setenv("HA_URL", "http://ha.example:8123")
        monkeypatch.setenv("HA_TOKEN", "tok")
        monkeypatch.setenv("HA_REQUEST_TIMEOUT", "not-a-number")
        c = HAClient.from_env()
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == (
            "⚠️  HA_REQUEST_TIMEOUT must be an integer, got 'not-a-number'; using 10\n"
        )
        assert c.timeout == 10

    def test_env_config_delegates_to_shared_configuration(self):
        with patch(
            "tools.ha.client.get_ha_config",
            return_value=("http://ha.example:8123", "tok", 42),
        ) as get_config:
            from tools.ha.client import _env_config

            assert _env_config() == ("http://ha.example:8123", "tok", 42)

        get_config.assert_called_once_with(warning_stream=sys.stderr)

    @pytest.mark.parametrize(
        "client_cls", [HAClient, HAWSClient], ids=["rest", "websocket"]
    )
    def test_load_env_file_called_once(self, monkeypatch, client_cls):
        """Verify from_env delegates to the project's shared .env loader."""
        monkeypatch.setenv("HA_URL", "http://ha.example:8123")
        monkeypatch.setenv("HA_TOKEN", "tok")
        with patch("tools.common.load_env_file") as mock_load:
            client_cls.from_env()
            mock_load.assert_called_once()

    def test_rest_and_websocket_factories_share_environment_tuple(self, monkeypatch):
        received = []

        class RecordingREST(HAClient):
            def __init__(self, url, token, *, timeout=10, **kwargs):
                received.append(("rest", url, token, timeout))
                super().__init__(url, token, timeout=timeout, **kwargs)

        class RecordingWebSocket(HAWSClient):
            def __init__(self, url, token, *, timeout=10, **kwargs):
                received.append(("websocket", url, token, timeout))
                super().__init__(url, token, timeout=timeout, **kwargs)

        monkeypatch.setattr(
            "tools.ha.client._env_config",
            lambda: ("http://ha.example:8123", "tok", 42),
        )
        RecordingREST.from_env()
        RecordingWebSocket.from_env()

        assert received == [
            ("rest", "http://ha.example:8123", "tok", 42),
            ("websocket", "http://ha.example:8123", "tok", 42),
        ]


def test_get_json_accepts_2xx():
    session = _mock_session()
    resp = make_response({"ok": True}, status=201)
    session.get.return_value = resp
    client = HAClient("http://h.local:8123", "tok", session=session)
    assert client.get_json("/api/x") == {"ok": True}


def test_request_error_wording():
    session = _mock_session()
    session.get.side_effect = requests.RequestException("boom")
    client = HAClient("http://h.local:8123", "tok", session=session)
    with pytest.raises(HARequestError, match=r"GET /api/x failed"):
        client.get("/api/x")


@pytest.mark.parametrize(
    ("verb", "url", "json_body"),
    [
        ("get", "/api/", None),
        ("post", "/api/services/light/turn_on", {"entity_id": "light.kitchen"}),
        ("put", "/api/config", {"key": "val"}),
        ("delete", "/api/config/section", None),
        ("patch", "/api/config", {"key": "val"}),
    ],
)
def test_verb_returns_response(verb, url, json_body):
    session = _mock_session()
    response_mock = make_response()
    setattr(session, verb, MagicMock(return_value=response_mock))
    c = HAClient("http://ha.local:8123", "tok", session=session)
    kwargs = {"json": json_body} if json_body is not None else {}
    r = getattr(c, verb)(url, **kwargs)
    assert r.status_code == 200
    args, kwargs = getattr(session, verb).call_args
    assert args[0] == f"http://ha.local:8123{url}"
    assert kwargs["headers"]["Authorization"] == "Bearer tok"
    assert kwargs["timeout"] == 10
    if json_body is not None:
        assert kwargs["json"] == json_body


@pytest.mark.parametrize(
    ("verb", "url", "exception"),
    [
        ("get", "/api/", requests.ConnectionError("boom")),
        ("post", "/api/services/light/turn_on", requests.Timeout("slow")),
        ("put", "/api/config", requests.ConnectionError("boom")),
        ("delete", "/api/config/section", requests.Timeout("slow")),
        ("patch", "/api/config", requests.ConnectionError("boom")),
    ],
)
def test_verb_raises_on_request_exception(verb, url, exception):
    session = _mock_session()
    setattr(session, verb, MagicMock(side_effect=exception))
    c = HAClient("http://ha.local:8123", "tok", session=session)
    with pytest.raises(HARequestError, match=f"{verb.upper()} .* failed"):
        getattr(c, verb)(url)


def test_request_merges_caller_headers_without_typeerror():
    """M4: passing headers= must merge, not collide with the client's defaults."""
    session = _mock_session()
    client = HAClient("http://ha.local", "tok", session=session)
    client.get("/api/states", headers={"X-Custom": "y"})
    args, kwargs = session.get.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer tok"
    assert kwargs["headers"]["X-Custom"] == "y"


def test_request_caller_timeout_overrides_default():
    """M4: passing timeout= must override the client default cleanly."""
    session = _mock_session()
    client = HAClient("http://ha.local", "tok", timeout=10, session=session)
    client.get("/api/states", timeout=30)
    _, kwargs = session.get.call_args
    assert kwargs["timeout"] == 30


def test_client_context_manager_closes_session():
    """M6: `with HAClient(...) as c:` must close the session on exit."""
    session = _mock_session()
    client = HAClient("http://ha.local", "tok", session=session)
    with client as c:
        assert c is client
    session.close.assert_called_once()


def test_client_context_manager_closes_session_on_exception():
    session = _mock_session()
    client = HAClient("http://ha.local", "tok", session=session)
    with pytest.raises(RuntimeError), client:
        raise RuntimeError("boom")
    session.close.assert_called_once()


def test_client_close_method_closes_session():
    """M6: close() method must close the underlying session."""
    session = _mock_session()
    client = HAClient("http://ha.local", "tok", session=session)
    client.close()
    session.close.assert_called_once()


def test_client_close_with_owned_session():
    """M6: when the client created its own session, close() must still close it."""
    with patch("tools.ha.client.requests.Session") as mock_session_cls:
        mock_session = _mock_session()
        mock_session_cls.return_value = mock_session
        client = HAClient("http://ha.local", "tok")
        client.close()
        mock_session.close.assert_called_once()


class TestGetJson:
    def test_docstring_describes_error_contract(self):
        doc = HAClient.get_json.__doc__ or ""
        assert "non-2xx" in doc
        assert "non-JSON" in doc
        assert "HARequestError" in doc

    def test_parses_json_response(self):
        session = _mock_session()
        resp = make_response({"message": "API running."})
        session.get.return_value = resp
        c = HAClient("http://ha.local:8123", "tok", session=session)
        assert c.get_json("/api/") == {"message": "API running."}

    def test_non_2xx_raises_with_body_preview(self):
        session = _mock_session()
        resp = make_response("Unauthorized", status=401, content_type="text/plain")
        session.get.return_value = resp
        c = HAClient("http://ha.local:8123", "tok", session=session)
        with pytest.raises(HARequestError, match="HTTP 401"):
            c.get_json("/api/")

    def test_non_json_response_raises(self):
        session = _mock_session()
        resp = make_response("<html>not json</html>", content_type="text/html")
        session.get.return_value = resp
        c = HAClient("http://ha.local:8123", "tok", session=session)
        with pytest.raises(HARequestError, match="non-JSON"):
            c.get_json("/api/")


class TestCallService:
    def test_success_returns_true(self):
        session = _mock_session()
        session.post.return_value = make_response()
        c = HAClient("http://ha.local:8123", "tok", session=session)
        assert c.call_service("automation", "reload") is True
        args, _ = session.post.call_args
        assert args[0] == "http://ha.local:8123/api/services/automation/reload"

    def test_non_2xx_returns_false(self):
        session = _mock_session()
        session.post.return_value = make_response(status=500)
        c = HAClient("http://ha.local:8123", "tok", session=session)
        assert (
            c.call_service("light", "turn_on", data={"entity_id": "light.x"}) is False
        )

    def test_data_passed_as_json(self):
        session = _mock_session()
        session.post.return_value = make_response()
        c = HAClient("http://ha.local:8123", "tok", session=session)
        c.call_service("light", "turn_on", data={"entity_id": "light.kitchen"})
        _, kwargs = session.post.call_args
        assert kwargs["json"] == {"entity_id": "light.kitchen"}

    def test_no_data_defaults_to_empty_dict(self):
        session = _mock_session()
        session.post.return_value = make_response()
        c = HAClient("http://ha.local:8123", "tok", session=session)
        c.call_service("automation", "reload")
        _, kwargs = session.post.call_args
        assert kwargs["json"] == {}


# ====================================================================
# HAWSClient tests
# ====================================================================


def _make_mock_ws(messages):
    """Build a mock WebSocket with a sequence of messages from receive_json."""
    ws = AsyncMock()
    ws.receive_json = AsyncMock(side_effect=list(messages))
    ws.send_json = AsyncMock()
    return ws


def _make_mock_session_factory(ws):
    """Build a session_factory for HAWSClient testing.

    Chain: session_factory() -> session -> session.ws_connect() -> ws
    """
    ws_ctx = AsyncMock()
    ws_ctx.__aenter__ = AsyncMock(return_value=ws)
    ws_ctx.__aexit__ = AsyncMock(return_value=None)

    session = AsyncMock()
    session.ws_connect = MagicMock(return_value=ws_ctx)

    session_ctx = AsyncMock()
    session_ctx.__aenter__ = AsyncMock(return_value=session)
    session_ctx.__aexit__ = AsyncMock(return_value=None)

    return MagicMock(return_value=session_ctx)


class TestHAWSInit:
    def test_valid_construction_stores_http_url(self):
        c = HAWSClient("http://ha.local:8123", "tok", timeout=15)
        assert c.url == "http://ha.local:8123"
        assert c.token == "tok"
        assert c.timeout == 15

    def test_url_trailing_slash_stripped(self):
        c = HAWSClient("http://ha.local:8123/", "tok")
        assert c.url == "http://ha.local:8123"

    def test_ws_url_converts_http_to_ws(self):
        c = HAWSClient("http://ha.local:8123", "tok")
        assert c._ws_url == "ws://ha.local:8123"

    def test_ws_url_converts_https_to_wss(self):
        c = HAWSClient("https://ha.example.com", "tok")
        assert c._ws_url == "wss://ha.example.com"


@pytest.mark.parametrize(
    "client_cls", [HAClient, HAWSClient], ids=["rest", "websocket"]
)
def test_common_constructor_rejects_invalid_url(client_cls):
    with pytest.raises(HARequestError, match="HA_URL"):
        client_cls("ftp://ha.local", "tok")


@pytest.mark.parametrize(
    "client_cls", [HAClient, HAWSClient], ids=["rest", "websocket"]
)
def test_common_constructor_rejects_missing_token(client_cls):
    with pytest.raises(MissingTokenError, match="HA_TOKEN") as exc_info:
        client_cls("http://ha.local:8123", "")
    assert isinstance(exc_info.value, HARequestError)


class TestHAWSAuthenticate:
    def test_authentication_uses_receive_helper(self):
        ws = _make_mock_ws([])
        c = HAWSClient("http://ha:8123", "tok")
        c._receive_dict = AsyncMock(
            side_effect=[{"type": "auth_required"}, {"type": "auth_ok"}]
        )

        asyncio.run(c._authenticate(ws))

        assert c._receive_dict.await_args_list == [
            call(ws, "during authentication"),
            call(ws, "during authentication"),
        ]

    def test_success(self):
        ws = _make_mock_ws(
            [
                {"type": "auth_required"},
                {"type": "auth_ok"},
            ]
        )
        c = HAWSClient("http://ha:8123", "mytoken")
        asyncio.run(c._authenticate(ws))
        ws.send_json.assert_called_once_with(
            {"type": "auth", "access_token": "mytoken"}
        )

    def test_auth_invalid_raises(self):
        ws = _make_mock_ws(
            [
                {"type": "auth_required"},
                {"type": "auth_invalid", "message": "Invalid token"},
            ]
        )
        c = HAWSClient("http://ha:8123", "bad")
        with pytest.raises(HARequestError, match="authentication failed"):
            asyncio.run(c._authenticate(ws))

    def test_unexpected_first_message_raises(self):
        ws = _make_mock_ws([{"type": "something_else"}])
        c = HAWSClient("http://ha:8123", "tok")
        with pytest.raises(HARequestError, match="auth_required"):
            asyncio.run(c._authenticate(ws))

    @pytest.mark.parametrize("message", [None, ["auth_required"]])
    def test_malformed_message_raises_request_error(self, message):
        ws = _make_mock_ws([message])
        c = HAWSClient("http://ha:8123", "tok")
        with pytest.raises(HARequestError, match="Invalid WebSocket message"):
            asyncio.run(c._authenticate(ws))


class TestHAWSSendAndReceive:
    def test_command_results_use_receive_helper(self):
        ws = _make_mock_ws([])
        c = HAWSClient("http://ha:8123", "tok")
        c._receive_dict = AsyncMock(
            side_effect=[
                {"type": "event", "id": 1},
                {"type": "result", "id": 1, "success": True, "result": "ok"},
            ]
        )

        assert asyncio.run(c._send_and_receive(ws, "trace/list")) == "ok"

        assert c._receive_dict.await_args_list == [
            call(ws, "while awaiting result"),
            call(ws, "while awaiting result"),
        ]

    def test_success_returns_result(self):
        ws = _make_mock_ws(
            [
                {"type": "result", "id": 1, "success": True, "result": {"data": "ok"}},
            ]
        )
        c = HAWSClient("http://ha:8123", "tok")
        result = asyncio.run(c._send_and_receive(ws, "system_log/list"))
        assert result == {"data": "ok"}

    def test_skips_non_result_messages(self):
        ws = _make_mock_ws(
            [
                {"type": "event", "id": 1},
                {"type": "pong"},
                {"type": "result", "id": 1, "success": True, "result": [1, 2]},
            ]
        )
        c = HAWSClient("http://ha:8123", "tok")
        result = asyncio.run(c._send_and_receive(ws, "trace/list", domain="automation"))
        assert result == [1, 2]

    def test_success_false_raises(self):
        ws = _make_mock_ws(
            [
                {
                    "type": "result",
                    "id": 1,
                    "success": False,
                    "error": {"code": "unknown_command", "message": "Unknown command."},
                },
            ]
        )
        c = HAWSClient("http://ha:8123", "tok")
        with pytest.raises(HARequestError, match="Unknown command"):
            asyncio.run(c._send_and_receive(ws, "bad/command"))

    def test_malformed_result_message_raises_request_error(self):
        ws = _make_mock_ws([None])
        c = HAWSClient("http://ha:8123", "tok")
        with pytest.raises(HARequestError, match="Invalid WebSocket message"):
            asyncio.run(c._send_and_receive(ws, "trace/list"))


class TestHAWSReceive:
    def test_returns_dictionary_message(self):
        message = {"type": "auth_required"}
        ws = _make_mock_ws([message])
        c = HAWSClient("http://ha:8123", "tok")

        assert asyncio.run(c._receive_dict(ws, "during authentication")) == message

    @pytest.mark.parametrize(
        ("context", "message"),
        [
            (
                "during authentication",
                "Invalid WebSocket message during authentication",
            ),
            (
                "while awaiting result",
                "Invalid WebSocket message while awaiting result",
            ),
        ],
    )
    @pytest.mark.parametrize("received", [None, ["message"]])
    def test_rejects_non_dictionary_message(self, context, message, received):
        ws = _make_mock_ws([received])
        c = HAWSClient("http://ha:8123", "tok")

        with pytest.raises(HARequestError) as exc_info:
            asyncio.run(c._receive_dict(ws, context))

        assert str(exc_info.value) == message


class TestHAWSCommand:
    def test_transport_error_is_wrapped(self):
        ws = _make_mock_ws([])
        sf = _make_mock_session_factory(ws)
        c = HAWSClient("http://ha:8123", "tok", session_factory=sf)
        c._authenticate = AsyncMock(side_effect=OSError("network down"))
        with pytest.raises(HARequestError, match="cannot connect"):
            asyncio.run(c._command("system_log/list"))

    def test_deliberate_request_error_is_preserved(self):
        ws = _make_mock_ws([])
        sf = _make_mock_session_factory(ws)
        c = HAWSClient("http://ha:8123", "tok", session_factory=sf)
        c._authenticate = AsyncMock(side_effect=HARequestError("auth contract"))
        with pytest.raises(HARequestError, match="auth contract"):
            asyncio.run(c._command("system_log/list"))

    def test_programming_error_is_not_mislabeled_as_connection_failure(self):
        ws = _make_mock_ws([{"type": "auth_required"}, {"type": "auth_ok"}])
        sf = _make_mock_session_factory(ws)
        c = HAWSClient("http://ha:8123", "tok", session_factory=sf)
        c._send_and_receive = AsyncMock(side_effect=TypeError("bad result shape"))
        with pytest.raises(TypeError, match="bad result shape"):
            asyncio.run(c._command("system_log/list"))

    def test_full_flow_returns_result(self):
        ws = _make_mock_ws(
            [
                {"type": "auth_required"},
                {"type": "auth_ok"},
                {
                    "type": "result",
                    "id": 1,
                    "success": True,
                    "result": [{"level": "ERROR"}],
                },
            ]
        )
        sf = _make_mock_session_factory(ws)
        c = HAWSClient("http://ha:8123", "tok", session_factory=sf)
        result = c.command("system_log/list")
        assert result == [{"level": "ERROR"}]

    def test_auth_failure_raises(self):
        ws = _make_mock_ws(
            [
                {"type": "auth_required"},
                {"type": "auth_invalid", "message": "Invalid token"},
            ]
        )
        sf = _make_mock_session_factory(ws)
        c = HAWSClient("http://ha:8123", "tok", session_factory=sf)
        with pytest.raises(HARequestError, match="authentication failed"):
            c.command("system_log/list")

    def test_command_failure_raises(self):
        ws = _make_mock_ws(
            [
                {"type": "auth_required"},
                {"type": "auth_ok"},
                {
                    "type": "result",
                    "id": 1,
                    "success": False,
                    "error": {"code": "unknown_command", "message": "Unknown command."},
                },
            ]
        )
        sf = _make_mock_session_factory(ws)
        c = HAWSClient("http://ha:8123", "tok", session_factory=sf)
        with pytest.raises(HARequestError, match="Unknown command"):
            c.command("bad/command")

    def test_command_does_not_require_msg_id_instance_attr(self):
        """W5.1: msg_id is a local inside _send_and_receive, not an instance attr.

        The instance attr gave a misleading signal that it carried state across
        commands. The reset at the start of command() proved it didn't. Drop the
        instance attr and use a local.
        """
        ws = _make_mock_ws(
            [
                {"type": "auth_required"},
                {"type": "auth_ok"},
                {"type": "result", "id": 1, "success": True, "result": "ok"},
            ]
        )
        sf = _make_mock_session_factory(ws)
        c = HAWSClient("http://ha:8123", "tok", session_factory=sf)
        c.command("system_log/list")
        assert not hasattr(c, "_msg_id")

        # Second command still works.
        ws2 = _make_mock_ws(
            [
                {"type": "auth_required"},
                {"type": "auth_ok"},
                {"type": "result", "id": 1, "success": True, "result": "ok"},
            ]
        )
        sf2 = _make_mock_session_factory(ws2)
        c._session_factory = sf2
        result = c.command("system_log/list")
        assert result == "ok"

    def test_loop_exhaustion_raises(self):
        """Sending _MAX_RESULT_MESSAGES+ non-result messages exhausts loop guard."""
        from tools.ha.client import _MAX_RESULT_MESSAGES

        messages = [{"type": "event", "id": i} for i in range(_MAX_RESULT_MESSAGES + 1)]
        ws = _make_mock_ws(messages)
        c = HAWSClient("http://ha:8123", "tok")
        with pytest.raises(HARequestError, match="timed out"):
            asyncio.run(c._send_and_receive(ws, "system_log/list"))
