"""Shared fixtures for tool tests."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from tests.helpers import make_response
from tools.ha.client import HAClient, HAWSClient


@pytest.fixture
def mock_clients():
    with (
        patch("tools.commands.trace.HAWSClient.from_env") as mock_from_env,
        patch("tools.commands.trace.HAClient.from_env") as mock_rest_from_env,
    ):
        mock_ws = MagicMock(spec=HAWSClient)
        session = MagicMock(spec=requests.sessions.Session)
        session.get.return_value = make_response({"attributes": {}})
        mock_rest = HAClient("http://ha:8123", "tok", session=session)
        mock_from_env.return_value = mock_ws
        mock_rest_from_env.return_value = mock_rest
        mock_ws.command.return_value = []
        yield mock_rest, mock_ws


@pytest.fixture
def mock_client(mock_clients):
    """Expose the WebSocket client for command-level tests."""
    _, mock_ws = mock_clients
    return mock_ws
