"""Tests for tools/commands/trace.py."""

import argparse
import json
from unittest.mock import MagicMock, patch

import pytest

from tools.common import HARequestError
from tools.ha.client import HAWSClient

# ── helpers ──────────────────────────────────────────────────────────


def _make_args(**overrides) -> argparse.Namespace:
    """Build a minimal argparse Namespace with safe defaults."""
    defaults = {
        "entity_id": None,
        "summary": True,
        "no_summary": False,
        "pretty": False,
        "first": None,
        "pick": None,
        "max_chars": None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _mock_trace_entry(
    *,
    item_id: str = "baz_qux",
    run_id: str = "run456",
    timestamp: dict | None = None,
) -> dict:
    """A single trace/list entry matching the real HA response shape."""
    return {
        "item_id": item_id,
        "run_id": run_id,
        "state": "stopped",
        "last_step": "action/0/choose/0/sequence/0",
        "trigger": "state of binary_sensor.test",
        "timestamp": timestamp
        or {
            "start": "2026-01-01T00:00:00+00:00",
            "finish": "2026-01-01T00:00:01+00:00",
        },
        "domain": "automation",
        "script_execution": "finished",
    }


def _make_ws_command_side_effect(
    traces: list[dict] | None = None,
    trace_detail: dict | None = None,
):
    """Return a callable side_effect for ``HAWSClient.command``.

    Simulates ``trace/list`` (filtered by ``item_id``) and ``trace/get``.
    """
    all_traces = traces if traces is not None else [_mock_trace_entry()]
    detail = trace_detail or {
        "item_id": "baz_qux",
        "run_id": "run456",
        "trace": {"1": [{"path": "action/0", "result": "ok"}]},
    }

    def _side_effect(cmd: str, **kw):
        if cmd == "trace/list":
            if "item_id" in kw:
                return [t for t in all_traces if t["item_id"] == kw["item_id"]]
            return list(all_traces)
        if cmd == "trace/get":
            item_id = kw.get("item_id", "")
            run_id = kw.get("run_id", "")
            if not item_id or not run_id:
                raise HARequestError("trace/get: missing item_id or run_id")
            return dict(detail, item_id=item_id, run_id=run_id)
        raise HARequestError(f"Unknown command: {cmd}")

    return _side_effect


# ── fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def mock_env(monkeypatch):
    """Stub HA env vars so ``from_env()`` doesn't fail."""
    monkeypatch.setenv("HA_URL", "http://ha.local:8123")
    monkeypatch.setenv("HA_TOKEN", "test-token")


@pytest.fixture
def mock_clients(mock_env):
    """Mock both HAClient and HAWSClient ``from_env()``.

    Yields ``(mock_hac, mock_ws)`` instances.  Callers configure
    ``mock_hac.get_json.return_value`` and
    ``mock_ws.command.side_effect``.
    """
    mock_hac = MagicMock()
    mock_hac.__enter__.return_value = mock_hac
    mock_ws = MagicMock(spec=HAWSClient)
    # Default: trace/list returns nothing (no matching traces).
    mock_ws.command.side_effect = _make_ws_command_side_effect(traces=[])
    with (
        patch("tools.ha.client.HAClient.from_env", return_value=mock_hac),
        patch("tools.ha.client.HAWSClient.from_env", return_value=mock_ws),
    ):
        yield mock_hac, mock_ws


# ── tests ────────────────────────────────────────────────────────────


class TestEntityResolution:
    """Entity_id → item_id resolution (the main bug)."""

    def test_slug_differs_from_item_id_resolves_via_state_attributes(
        self, mock_clients, capsys
    ):
        """entity_id slug != item_id → resolves via state ``attributes.id``."""
        mock_hac, mock_ws = mock_clients

        # State API returns an ``id`` that differs from the slug.
        mock_hac.get_json.return_value = {
            "entity_id": "automation.foo_bar",
            "state": "on",
            "attributes": {"id": "baz_qux", "friendly_name": "Foo & Bar"},
        }
        mock_ws.command.side_effect = _make_ws_command_side_effect(
            traces=[_mock_trace_entry(item_id="baz_qux")],
        )

        from tools.commands.trace import run

        exit_code = run(_make_args(entity_id="automation.foo_bar"))
        assert exit_code == 0

        # Resolution fetched state attributes.
        mock_hac.get_json.assert_called_once_with("/api/states/automation.foo_bar")
        # trace/list was called with the *resolved* item_id.
        mock_ws.command.assert_any_call(
            "trace/list", domain="automation", item_id="baz_qux"
        )
        # trace/get was called with resolved id + run_id.
        mock_ws.command.assert_any_call(
            "trace/get", domain="automation", item_id="baz_qux", run_id="run456"
        )
        # Output contains trace data (not an error).
        out, err = capsys.readouterr()
        assert "baz_qux" in out
        assert "No traces found" not in err

    def test_falls_back_to_slug_when_state_has_no_id_attr(self, mock_clients, capsys):
        """If ``attributes.id`` is absent, fall back to slug-strip.

        This preserves backward compat for very old automations that
        lack an explicit ``id`` field.
        """
        mock_hac, mock_ws = mock_clients

        # No ``id`` in attributes (None or missing).
        mock_hac.get_json.return_value = {
            "entity_id": "automation.my_old_auto",
            "state": "on",
            "attributes": {"friendly_name": "My Old Auto"},
        }
        # trace/list has an entry where item_id HAPPENS TO match the slug.
        mock_ws.command.side_effect = _make_ws_command_side_effect(
            traces=[_mock_trace_entry(item_id="my_old_auto", run_id="run789")],
        )

        from tools.commands.trace import run

        exit_code = run(_make_args(entity_id="automation.my_old_auto"))
        assert exit_code == 0

        out, err = capsys.readouterr()
        assert "my_old_auto" in out
        assert "No traces found" not in err

    def test_single_entity_selects_newest_run(self, mock_clients, capsys):
        mock_hac, mock_ws = mock_clients
        mock_hac.get_json.return_value = {
            "attributes": {"id": "auto_id"},
        }
        mock_ws.command.side_effect = _make_ws_command_side_effect(
            traces=[
                _mock_trace_entry(
                    item_id="auto_id",
                    run_id="old",
                    timestamp={"start": "2026-01-01T00:00:00+00:00"},
                ),
                _mock_trace_entry(
                    item_id="auto_id",
                    run_id="new",
                    timestamp={"start": "2026-01-02T00:00:00+00:00"},
                ),
            ]
        )
        from tools.commands.trace import run

        assert run(_make_args(entity_id="automation.auto")) == 0
        mock_ws.command.assert_any_call(
            "trace/get", domain="automation", item_id="auto_id", run_id="new"
        )

    def test_no_traces_returns_clean_error(self, mock_clients, capsys):
        """Genuinely no traces for a known automation → clean stderr message."""
        mock_hac, mock_ws = mock_clients

        mock_hac.get_json.return_value = {
            "entity_id": "automation.never_triggered",
            "attributes": {"id": "never_triggered_id"},
        }
        # trace/list returns empty for this item_id.
        mock_ws.command.side_effect = _make_ws_command_side_effect(traces=[])

        from tools.commands.trace import run

        exit_code = run(_make_args(entity_id="automation.never_triggered"))
        assert exit_code == 1

        _, err = capsys.readouterr()
        assert "No traces found" in err

    def test_entity_state_api_failure_shows_error(self, mock_clients, capsys):
        """If the REST state lookup fails, fall back to slug-strip; still works."""
        mock_hac, mock_ws = mock_clients

        mock_hac.get_json.side_effect = HARequestError(
            "GET /api/states/automation.nope returned HTTP 404"
        )
        # No traces match the slug-stripped id either.
        mock_ws.command.side_effect = _make_ws_command_side_effect(traces=[])

        from tools.commands.trace import run

        exit_code = run(_make_args(entity_id="automation.nope"))
        assert exit_code == 1

        _, err = capsys.readouterr()
        # The REST error is caught internally; falls back to slug-strip,
        # which also finds no traces → "No traces found".
        assert "No traces found" in err


class TestListMode:
    """``ha_cli trace`` (no entity_id) — unchanged by the fix."""

    def test_list_all_traces_summary(self, mock_clients, capsys):
        """List mode returns deduplicated summary."""
        _, mock_ws = mock_clients

        mock_ws.command.side_effect = _make_ws_command_side_effect(
            traces=[
                _mock_trace_entry(
                    item_id="auto_a",
                    run_id="r1",
                    timestamp={
                        "start": "2026-02-01T00:00:00+00:00",
                        "finish": "2026-02-01T00:00:01+00:00",
                    },
                ),
                _mock_trace_entry(
                    item_id="auto_b",
                    run_id="r2",
                    timestamp={
                        "start": "2026-02-01T00:01:00+00:00",
                        "finish": "2026-02-01T00:01:01+00:00",
                    },
                ),
                _mock_trace_entry(
                    item_id="auto_a",
                    run_id="r3",
                    timestamp={
                        "start": "2026-02-01T00:02:00+00:00",
                        "finish": "2026-02-01T00:02:01+00:00",
                    },
                ),
            ],
        )

        from tools.commands.trace import run

        exit_code = run(_make_args(entity_id=None, summary=True))
        assert exit_code == 0

        out, err = capsys.readouterr()
        data = json.loads(out)
        # Deduped: only 2 unique item_ids
        assert len(data) == 2
        # The one with `runs: 2` should be auto_a (had 2 entries)
        ids_with_runs = {d.get("item_id") for d in data if d.get("runs")}
        assert "auto_a" in ids_with_runs
        assert "auto_b" not in {d.get("item_id") for d in data if d.get("runs")}
        auto_a = next(d for d in data if d["item_id"] == "auto_a")
        assert auto_a["timestamp"] == "2026-02-01T00:02:00+00:00"


class TestValidation:
    """Input validation — should reject invalid entity_id early."""

    def test_invalid_entity_id_format_rejected(self):
        """Bad entity_id format → exit 2 (argparse rejects)."""
        from tools.commands.trace import run

        exit_code = run(_make_args(entity_id="not_an_entity_id"))
        assert exit_code == 1  # prints "Invalid entity_id" to stderr

    def test_invalid_entity_id_message(self, capsys):
        from tools.commands.trace import run

        run(_make_args(entity_id="bad_format"))
        _, err = capsys.readouterr()
        assert "Invalid entity_id" in err
