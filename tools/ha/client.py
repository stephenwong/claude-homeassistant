"""Shared HTTP client for Home Assistant REST API.

Consolidates the duplicated auth/header/timeout/JSON-parse code that previously
lived in `tools/reload_config.py` and `tools/ha_api_diagnostic.py`.
"""

import asyncio
import sys
from typing import Any, Protocol, cast

import aiohttp
import requests

from tools.common import (
    DEFAULT_HA_TIMEOUT,
    HARequestError,
    MissingTokenError,
    get_ha_config,
    validate_ha_url,
)

_MAX_RESULT_MESSAGES = 500


class _ClientConstructor[ClientT](Protocol):
    """Callable constructor accepted by the shared environment factory."""

    def __call__(self, url: str, token: str, *, timeout: int) -> ClientT: ...


def _validate_connection(url: str, token: str) -> str:
    """Validate URL format and token presence; return stripped URL.

    Raises:
        HARequestError: URL is malformed.
        MissingTokenError: Token is empty (also a HARequestError subclass).
    """
    error = validate_ha_url(url)
    if error:
        raise HARequestError(error)
    if not token:
        raise MissingTokenError(
            "HA_TOKEN not found. Set it in .env or the environment."
        )
    return url.rstrip("/")


def _env_config() -> tuple[str, str, int]:
    """Return (url, token, timeout) from the shared environment configuration."""
    return get_ha_config(warning_stream=sys.stderr)


def _client_from_env[ClientT](client_cls: _ClientConstructor[ClientT]) -> ClientT:
    """Construct a client class from the shared environment configuration."""
    url, token, timeout = _env_config()
    return client_cls(url, token, timeout=timeout)


class HAClient:
    """Thin wrapper around the Home Assistant REST API."""

    def __init__(
        self,
        url: str,
        token: str,
        *,
        timeout: int = DEFAULT_HA_TIMEOUT,
        session: requests.Session | None = None,
    ):
        """Initialize the client.

        Args:
            url: Base HA URL (e.g. ``http://homeassistant.local:8123``).
            token: Long-lived access token.
            timeout: Per-request timeout in seconds.
            session: Optional pre-configured requests.Session for testing.
        """
        self.url = _validate_connection(url, token)
        self.token = token
        self.timeout = timeout
        self._session = session or requests.Session()

    @property
    def headers(self) -> dict[str, str]:
        """Auth + content-type headers for every request."""
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    @classmethod
    def from_env(cls) -> HAClient:
        """Construct a client from HA_URL/HA_TOKEN/HA_REQUEST_TIMEOUT.

        Loads ``.env`` exactly once via the shared environment configuration
        helper so callers don't need to remember to do it. The underlying
        loader is idempotent and only sets variables absent from the environment.
        """
        return _client_from_env(cast(_ClientConstructor[HAClient], cls))

    def close(self) -> None:
        """Close the underlying requests.Session, releasing pooled connections."""
        self._session.close()

    def __enter__(self) -> HAClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        """Dispatch a request to ``path``. Raises HARequestError on failure."""
        url = f"{self.url}{path}"
        # M4: caller-provided headers/timeouts win over defaults.
        merged_headers = {**self.headers, **kwargs.pop("headers", {})}
        kwargs.setdefault("timeout", self.timeout)
        try:
            return getattr(self._session, method.lower())(
                url, headers=merged_headers, **kwargs
            )
        except requests.RequestException as e:
            raise HARequestError(f"{method.upper()} {path} failed: {e}") from e

    def get(self, path: str, **kwargs: Any) -> requests.Response:
        """GET ``path`` (e.g. ``/api/states``). Raises HARequestError on failure."""
        return self._request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> requests.Response:
        """POST to ``path``. Raises HARequestError on failure."""
        return self._request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> requests.Response:
        """PUT to ``path``. Raises HARequestError on failure."""
        return self._request("PUT", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> requests.Response:
        """DELETE ``path``. Raises HARequestError on failure."""
        return self._request("DELETE", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> requests.Response:
        """PATCH ``path``. Raises HARequestError on failure."""
        return self._request("PATCH", path, **kwargs)

    def get_json(self, path: str, **kwargs: Any) -> Any:
        """GET ``path`` and parse JSON.

        Raises :class:`HARequestError` for non-2xx or non-JSON responses.
        """
        response = self.get(path, **kwargs)
        if response.status_code < 200 or response.status_code >= 300:
            snippet = response.content[:200].decode("utf-8", errors="replace")
            raise HARequestError(
                f"GET {path} returned HTTP {response.status_code}: {snippet}"
            )
        try:
            return response.json()
        except ValueError as e:
            raise HARequestError(f"GET {path} returned non-JSON response: {e}") from e

    def call_service(
        self,
        domain: str,
        service: str,
        data: dict | None = None,
    ) -> bool:
        """Call ``<domain>/<service>`` (e.g. ``automation``, ``reload``).

        Returns ``True`` if HA responded with any 2xx status code.
        """
        path = f"/api/services/{domain}/{service}"
        response = self.post(path, json=data or {})
        return 200 <= response.status_code < 300


class HAWSClient:
    """Thin WebSocket client for HA commands not available via REST.

    HA removed /api/error_log and /api/automation/trace from the REST API.
    These are now WebSocket-only (system_log/list, trace/list, trace/get).
    """

    def __init__(
        self,
        url: str,
        token: str,
        *,
        timeout: int = DEFAULT_HA_TIMEOUT,
        session_factory=None,
    ):
        self.url = _validate_connection(url, token)
        self.token = token
        self.timeout = timeout
        self._session_factory = session_factory

    @classmethod
    def from_env(cls) -> HAWSClient:
        """Construct a client from HA_URL/HA_TOKEN/HA_REQUEST_TIMEOUT."""
        return _client_from_env(cast(_ClientConstructor[HAWSClient], cls))

    @property
    def _ws_url(self) -> str:
        """Convert http URL to WebSocket URL (ws:// or wss://)."""
        url = self.url
        if url.lower().startswith("https://"):
            return "wss://" + url[8:]
        return "ws://" + url[7:]

    def command(self, command_type: str, **params: Any) -> Any:
        """Send a WebSocket command synchronously, return the result.

        Raises HARequestError on transport, auth, or command failure.
        Unexpected programming errors are allowed to propagate for diagnosis.
        """
        return asyncio.run(self._command(command_type, **params))

    async def _command(self, command_type: str, **params: Any) -> Any:
        session_factory = self._session_factory or aiohttp.ClientSession
        ws_timeout = aiohttp.ClientWSTimeout(
            ws_receive=self.timeout, ws_close=self.timeout
        )
        try:
            async with (
                session_factory() as session,
                session.ws_connect(
                    f"{self._ws_url}/api/websocket",
                    timeout=ws_timeout,
                ) as ws,
            ):
                await self._authenticate(ws)
                return await self._send_and_receive(ws, command_type, **params)
        except (OSError, aiohttp.ClientError, ValueError) as e:
            raise HARequestError(
                f"cannot connect to HA WebSocket at {self._ws_url}: {e}"
            ) from e

    async def _authenticate(self, ws) -> None:
        """Perform the WebSocket auth handshake."""
        msg = await self._receive_dict(ws, "during authentication")
        if msg.get("type") != "auth_required":
            raise HARequestError(
                f"unexpected WebSocket message: expected auth_required, "
                f"got {msg.get('type')}"
            )
        await ws.send_json({"type": "auth", "access_token": self.token})
        msg = await self._receive_dict(ws, "during authentication")
        if msg.get("type") == "auth_invalid":
            raise HARequestError(
                f"authentication failed \u2014 check HA_TOKEN: "
                f"{msg.get('message', 'invalid token')}"
            )
        if msg.get("type") != "auth_ok":
            raise HARequestError(
                f"unexpected WebSocket message: expected auth_ok, got {msg.get('type')}"
            )

    async def _receive_dict(self, ws, context: str) -> dict[str, Any]:
        """Receive and validate a dictionary WebSocket message."""
        msg = await ws.receive_json()
        if not isinstance(msg, dict):
            raise HARequestError(f"Invalid WebSocket message {context}")
        return msg

    async def _send_and_receive(self, ws, command_type: str, **params: Any) -> Any:
        """Send a command and loop until we receive the matching result."""
        msg_id = 1
        await ws.send_json({"id": msg_id, "type": command_type, **params})

        for _ in range(_MAX_RESULT_MESSAGES):
            msg = await self._receive_dict(ws, "while awaiting result")
            if msg.get("type") == "result" and msg.get("id") == msg_id:
                if not msg.get("success", False):
                    error = msg.get("error", {})
                    message = (
                        error.get("message")
                        if isinstance(error, dict)
                        else "unknown error"
                    )
                    raise HARequestError(
                        f"{command_type} failed: {message or 'unknown error'}"
                    )
                return msg.get("result")

        raise HARequestError(f"{command_type} timed out waiting for result")
