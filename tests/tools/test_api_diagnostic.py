"""Focused tests for the development API diagnostic guidance."""

import sys
from unittest.mock import Mock, patch

import pytest
import requests

from tools._dev.api_diagnostic import (
    _request,
    _request_with_failure_handling,
    get_config,
    main,
    show_websocket_info,
)
from tools._dev.api_diagnostic import (
    test_api_connection as diagnostic_api_connection,
)
from tools._dev.api_diagnostic import (
    test_api_endpoints as diagnostic_api_endpoints,
)
from tools._dev.api_diagnostic import (
    test_entity_registry_read as diagnostic_entity_registry_read,
)
from tools._dev.api_diagnostic import (
    test_entity_rename as diagnostic_entity_rename,
)
from tools._dev.api_diagnostic import (
    test_service_call_method as diagnostic_service_call_method,
)
from tools._dev.api_diagnostic import (
    test_states_endpoint as diagnostic_states_endpoint,
)


def test_get_config_returns_typed_runtime_shape():
    with patch(
        "tools._dev.api_diagnostic.get_ha_config",
        return_value=("https://ha.example.com", "secret", 9),
    ) as shared_config:
        assert get_config() == {
            "ha_url": "https://ha.example.com",
            "token": "secret",
            "request_timeout": 9,
        }

    shared_config.assert_called_once_with(warning_stream=sys.stdout)


def test_get_config_keeps_timeout_warning_on_stdout(monkeypatch, capsys):
    monkeypatch.setenv("HA_REQUEST_TIMEOUT", "not-a-number")
    with patch("tools.common.load_env_file"):
        assert get_config()["request_timeout"] == 10

    captured = capsys.readouterr()
    assert captured.out == (
        "⚠️  HA_REQUEST_TIMEOUT must be an integer, got 'not-a-number'; using 10\n"
    )
    assert captured.err == ""


def test_request_centralizes_get_transport_contract():
    response = Mock()
    with patch(
        "tools._dev.api_diagnostic.requests.request", return_value=response
    ) as request:
        assert (
            _request(
                "http://ha.example.com:8123",
                "secret",
                "/api/states",
                request_timeout=7,
            )
            is response
        )

    request.assert_called_once_with(
        "GET",
        "http://ha.example.com:8123/api/states",
        headers={"Authorization": "Bearer secret"},
        timeout=7,
    )


def test_request_centralizes_post_transport_contract():
    response = Mock()
    payload = {"entity_id": "light.test"}
    with patch(
        "tools._dev.api_diagnostic.requests.request", return_value=response
    ) as request:
        _request(
            "https://ha.example.com",
            "secret",
            "/api/service",
            method="POST",
            request_timeout=3,
            payload=payload,
        )

    request.assert_called_once_with(
        "POST",
        "https://ha.example.com/api/service",
        headers={
            "Authorization": "Bearer secret",
            "Content-Type": "application/json",
        },
        timeout=3,
        json=payload,
    )


def test_request_normalizes_base_url_path_and_query():
    response = Mock()
    with patch(
        "tools._dev.api_diagnostic.requests.request", return_value=response
    ) as request:
        _request(
            "https://ha.example.com/ha/?stale=1#fragment",
            "secret",
            "/api/states",
        )

    assert request.call_args.args[1] == "https://ha.example.com/ha/api/states"


def test_request_propagates_transport_errors():
    with (
        patch(
            "tools._dev.api_diagnostic.requests.request",
            side_effect=requests.RequestException("offline"),
        ),
        pytest.raises(requests.RequestException, match="offline"),
    ):
        _request("http://ha.example.com", "secret", "/api/")


def test_request_with_failure_handling_preserves_output_and_sentinel(capsys):
    sentinel = object()

    with patch(
        "tools._dev.api_diagnostic._request",
        side_effect=requests.RequestException("offline"),
    ):
        assert (
            _request_with_failure_handling(
                "http://ha",
                "token",
                "/api/",
                request_timeout=1,
                error_prefix="   ❌",
                sentinel=sentinel,
            )
            is sentinel
        )

    captured = capsys.readouterr()
    assert captured.out == "   ❌ Exception: offline\n"
    assert captured.err == ""


def test_api_connection_handles_request_failure(capsys):
    with patch(
        "tools._dev.api_diagnostic._request",
        side_effect=requests.RequestException("offline"),
    ):
        assert diagnostic_api_connection("http://ha", "token") is False

    captured = capsys.readouterr()
    assert captured.out == "🔗 Testing API Connection...\n   Exception: offline\n"
    assert captured.err == ""


def test_api_endpoints_handles_request_failure_and_continues(capsys):
    response = Mock(status_code=500, text="not found")
    with patch(
        "tools._dev.api_diagnostic._request",
        side_effect=[requests.RequestException("offline"), *([response] * 6)],
    ):
        assert diagnostic_api_endpoints("http://ha", "token") == []

    captured = capsys.readouterr()
    assert "   ❌ Exception: offline\n" in captured.out
    assert "   Testing: /api/config/entity_registry/list (Entity Registry List)" in (
        captured.out
    )
    assert captured.err == ""


def test_entity_registry_read_handles_request_failure(capsys):
    with patch(
        "tools._dev.api_diagnostic._request",
        side_effect=requests.RequestException("offline"),
    ):
        assert diagnostic_entity_registry_read("http://ha", "token") == []

    captured = capsys.readouterr()
    assert captured.out == (
        "\n📋 Testing Entity Registry Read Access...\n   ❌ Exception: offline\n"
    )
    assert captured.err == ""


def test_states_endpoint_handles_request_failure(capsys):
    with patch(
        "tools._dev.api_diagnostic._request",
        side_effect=requests.RequestException("offline"),
    ):
        assert diagnostic_states_endpoint("http://ha", "token") is False

    captured = capsys.readouterr()
    assert captured.out == (
        "\n📊 Testing States Endpoint for Entity Info...\n   ❌ Exception: offline\n"
    )
    assert captured.err == ""


@pytest.mark.parametrize(
    ("ha_url", "websocket_url"),
    [
        (
            "http://homeassistant.local:8123",
            "ws://homeassistant.local:8123/api/websocket",
        ),
        ("https://ha.example.com", "wss://ha.example.com/api/websocket"),
        (
            "http://ha.example.com:8124",
            "ws://ha.example.com:8124/api/websocket",
        ),
        (
            "https://ha.example.com:8443/ha",
            "wss://ha.example.com:8443/ha/api/websocket",
        ),
    ],
)
def test_websocket_guidance_uses_configured_ha_url(ha_url, websocket_url, capsys):
    show_websocket_info(ha_url)
    assert websocket_url in capsys.readouterr().out


def test_mutating_diagnostic_steps_are_read_only():
    with patch("tools._dev.api_diagnostic._request") as request:
        assert (
            diagnostic_entity_rename("http://ha", "token", [{"entity_id": "light.x"}])
            is False
        )
        diagnostic_service_call_method("http://ha", "token", [{"entity_id": "light.x"}])
    request.assert_not_called()


def test_main_returns_failure_when_connection_fails(monkeypatch):
    monkeypatch.setattr(
        "tools._dev.api_diagnostic.get_config",
        lambda: {"ha_url": "http://ha", "token": "token", "request_timeout": 1},
    )
    monkeypatch.setattr(
        "tools._dev.api_diagnostic.test_api_connection", lambda *_: False
    )
    assert main() == 1
