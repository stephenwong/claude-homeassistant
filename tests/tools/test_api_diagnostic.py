"""Focused tests for the development API diagnostic guidance."""

from unittest.mock import Mock, patch

import pytest
import requests

from tools._dev.api_diagnostic import (
    _request,
    get_config,
    main,
    show_websocket_info,
)
from tools._dev.api_diagnostic import (
    test_entity_rename as diagnostic_entity_rename,
)
from tools._dev.api_diagnostic import (
    test_service_call_method as diagnostic_service_call_method,
)


def test_get_config_returns_typed_runtime_shape():
    with (
        patch("tools._dev.api_diagnostic.load_env_file"),
        patch.dict(
            "os.environ",
            {
                "HA_URL": "https://ha.example.com",
                "HA_TOKEN": "secret",
                "HA_REQUEST_TIMEOUT": "9",
            },
            clear=True,
        ),
    ):
        assert get_config() == {
            "ha_url": "https://ha.example.com",
            "token": "secret",
            "request_timeout": 9,
        }


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
