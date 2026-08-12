"""Tests for entity resolution in tools/commands/trace.py."""

from tests.tools.commands.test_trace import (
    _make_ws_command_side_effect,
    _mock_trace_entry,
    make_args,
)
from tools.common import HARequestError

pytest_plugins = ("tests.tools.commands.test_trace",)


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

        exit_code = run(make_args(entity_id="automation.foo_bar"))
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

        exit_code = run(make_args(entity_id="automation.my_old_auto"))
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

        assert run(make_args(entity_id="automation.auto")) == 0
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

        exit_code = run(make_args(entity_id="automation.never_triggered"))
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

        exit_code = run(make_args(entity_id="automation.nope"))
        assert exit_code == 1

        _, err = capsys.readouterr()
        # The REST error is caught internally; falls back to slug-strip,
        # which also finds no traces → "No traces found".
        assert "No traces found" in err
