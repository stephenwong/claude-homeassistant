"""Tests for the ``trace`` subcommand (WebSocket-based)."""

import json
from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

from tests.helpers import make_parser, parse_command_args
from tools.commands import trace as trace_cmd
from tools.common import HARequestError


def test_cap_trace_dict_includes_markers_in_fit_check():
    """M14: The truncation markers must be counted toward max_chars.

    Regression guard for _cap_trace_dict overshooting max_chars because
    the fit-check loop didn't include the _truncated/dropped_steps/kept_steps
    markers that get appended after the loop.
    """
    from tools.commands.trace import _cap_trace_dict

    # Tuned so that a trial with 2 steps (out of 3) fits within max_chars=500,
    # but adding the ~50-char marker pushes it over 500 — exposing the bug
    # where the fit check doesn't include markers.
    step = {"changed_variables": {"x": "y" * 170}}
    data = {
        "item_id": "abc",
        "state": "on",
        "trace": {f"step/{i}": step for i in range(3)},
    }
    max_chars = 500
    capped = _cap_trace_dict(data, max_chars)
    assert capped.get("_truncated") is True, (
        "test data must trigger truncation at max_chars=500; "
        "adjust 'y' * N or lower max_chars"
    )
    serialized = json.dumps(capped, separators=(",", ":"))
    assert len(serialized) <= max_chars, (
        f"trace overshoot: {len(serialized)} > {max_chars} "
        f"(markers not counted in fit check)"
    )


def make_args(**overrides):
    defaults = dict(
        entity_id=None,
        first=None,
        pretty=False,
        summary=False,
        no_summary=True,
        pick=None,
        max_chars=None,
    )
    defaults.update(overrides)
    return Namespace(**defaults)


@pytest.fixture
def mock_client():
    with (
        patch("tools.commands.trace.HAWSClient.from_env") as mock_from_env,
        patch("tools.commands.trace.HAClient.from_env") as mock_rest_from_env,
    ):
        client = MagicMock()
        rest_client = MagicMock()
        rest_client.__enter__.return_value = rest_client
        rest_client.get_json.return_value = {"attributes": {}}
        mock_from_env.return_value = client
        mock_rest_from_env.return_value = rest_client
        client.rest_client = rest_client
        yield client


def _configure_single_trace(mock_client, trace, item_id="test"):
    """Configure the shared list/get response for a single-trace test."""
    mock_client.command.side_effect = [
        [{"item_id": item_id, "run_id": "r1"}],
        trace,
    ]


def _iter_changed_variables(data):
    """Yield well-formed changed-variable mappings from a shaped trace."""
    trace = data.get("trace", {})
    for entries in trace.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            changed_variables = entry.get("changed_variables")
            if isinstance(changed_variables, dict):
                yield changed_variables


SAMPLE_TRACES = [
    {
        "item_id": "morning_routine",
        "run_id": "abc123",
        "state": "stopped",
        "script_execution": "finished",
        "timestamp": {
            "start": "2026-07-02T11:00:00Z",
            "finish": "2026-07-02T11:00:01Z",
        },
        "last_step": "action/0",
        "trigger": "state of binary_sensor.is_daytime",
        "domain": "automation",
    },
    {
        "item_id": "evening_lights",
        "run_id": "def456",
        "state": "stopped",
        "script_execution": "finished",
        "timestamp": {
            "start": "2026-07-02T20:00:00Z",
            "finish": "2026-07-02T20:00:02Z",
        },
        "last_step": "action/1",
        "trigger": "time",
        "domain": "automation",
    },
]

DUPLICATE_TRACES = [
    {
        "item_id": "morning_routine",
        "run_id": "abc123",
        "state": "stopped",
        "timestamp": {
            "start": "2026-07-02T11:00:00Z",
            "finish": "2026-07-02T11:00:01Z",
        },
        "trigger": "state of binary_sensor.is_daytime",
    },
    {
        "item_id": "morning_routine",
        "run_id": "xyz789",
        "state": "stopped",
        "timestamp": {
            "start": "2026-07-03T11:00:00Z",
            "finish": "2026-07-03T11:00:01Z",
        },
        "trigger": "state of binary_sensor.is_daytime",
    },
    {
        "item_id": "morning_routine",
        "run_id": "old999",
        "state": "stopped",
        "timestamp": {
            "start": "2026-07-01T11:00:00Z",
            "finish": "2026-07-01T11:00:01Z",
        },
        "trigger": "state of binary_sensor.is_daytime",
    },
    {
        "item_id": "evening_lights",
        "run_id": "def456",
        "state": "stopped",
        "timestamp": {
            "start": "2026-07-02T20:00:00Z",
            "finish": "2026-07-02T20:00:02Z",
        },
        "trigger": "time",
    },
    {
        "item_id": "evening_lights",
        "run_id": "ghi012",
        "state": "stopped",
        "timestamp": {
            "start": "2026-07-03T20:00:00Z",
            "finish": "2026-07-03T20:00:02Z",
        },
        "trigger": "time",
    },
]


class TestAddParser:
    def test_registered_no_entity_required(self):
        parser, subparsers = make_parser()
        trace_cmd.add_parser(subparsers)
        args = parser.parse_args(["trace"])
        assert callable(args.func)
        assert args.entity_id is None

    def test_registered_with_entity(self):
        args = parse_command_args("trace", trace_cmd.add_parser, ["automation.foo"])
        assert args.entity_id == "automation.foo"

    def test_has_first_flag(self):
        args = parse_command_args("trace", trace_cmd.add_parser, ["--first", "5"])
        assert args.first == 5

    def test_has_summary_flags(self):
        args = parse_command_args("trace", trace_cmd.add_parser, ["--summary"])
        assert args.summary is True


class TestRun:
    def test_invalid_entity_returns_1(self, capsys):
        args = make_args(entity_id="../../bad")
        assert trace_cmd.run(args) == 1
        err = capsys.readouterr().err
        assert "Invalid entity_id" in err

    def test_list_mode_compact(self, mock_client, capsys):
        mock_client.command.return_value = SAMPLE_TRACES
        args = make_args()
        assert trace_cmd.run(args) == 0
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert len(parsed) == 2
        assert "    " not in out  # compact

    def test_list_mode_calls_trace_list(self, mock_client):
        mock_client.command.return_value = []
        args = make_args()
        trace_cmd.run(args)
        mock_client.command.assert_called_once_with("trace/list", domain="automation")

    def test_list_mode_pretty(self, mock_client, capsys):
        mock_client.command.return_value = SAMPLE_TRACES
        args = make_args(pretty=True)
        assert trace_cmd.run(args) == 0
        out = capsys.readouterr().out
        assert "    " in out  # indented

    def test_list_mode_summary_projects_fields(self, mock_client, capsys):
        """Summary mode projects trace list to {item_id, state, trigger, timestamp}."""
        mock_client.command.return_value = SAMPLE_TRACES
        args = make_args(summary=True, no_summary=False)
        assert trace_cmd.run(args) == 0
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert set(parsed[0].keys()) == {"item_id", "state", "trigger", "timestamp"}
        # Ensure full-field keys are absent in summary mode
        assert {"script_execution", "last_step", "domain"}.isdisjoint(parsed[0].keys())

    def test_summarize_trace_list_projects_and_deduplicates(self):
        from tools.commands.trace import _summarize_trace_list

        traces = [
            {
                "item_id": "same",
                "state": "stopped",
                "trigger": "old",
                "timestamp": {"start": "2026-01-01"},
            },
            {
                "item_id": "same",
                "state": "stopped",
                "trigger": "new",
                "timestamp": {"start": "2026-01-02"},
            },
        ]
        assert _summarize_trace_list(traces) == [
            {
                "item_id": "same",
                "state": "stopped",
                "trigger": "new",
                "timestamp": "2026-01-02",
                "runs": 2,
            }
        ]

    def test_summarize_trace_list_orders_timezone_offsets_by_instant(self):
        from tools.commands.trace import _summarize_trace_list

        traces = [
            {
                "item_id": "same",
                "state": "stopped",
                "trigger": "older",
                "timestamp": {"start": "2026-01-01T11:00:00+02:00"},
            },
            {
                "item_id": "same",
                "state": "stopped",
                "trigger": "newer",
                "timestamp": {"start": "2026-01-01T10:30:00+00:00"},
            },
        ]

        result = _summarize_trace_list(traces)

        assert result[0]["trigger"] == "newer"

    def test_malformed_trace_list_response_returns_error(self, mock_client, capsys):
        mock_client.command.return_value = {"not": "a list"}

        assert trace_cmd.run(make_args()) == 1
        assert "invalid trace/list response" in capsys.readouterr().err.lower()

    def test_malformed_trace_get_response_returns_error(self, mock_client, capsys):
        mock_client.command.side_effect = [
            [{"item_id": "morning_routine", "run_id": "r1"}],
            None,
        ]
        args = make_args(entity_id="automation.morning_routine")
        assert trace_cmd.run(args) == 1
        assert "invalid trace/get response" in capsys.readouterr().err.lower()

    def test_summary_dedupes_by_item_id(self, mock_client, capsys):
        """Summary mode dedupes trace list to one entry per item_id + runs field."""
        mock_client.command.return_value = DUPLICATE_TRACES
        args = make_args(summary=True, no_summary=False)
        assert trace_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        assert len(result) == 2  # 2 unique item_ids
        runs_by_id = {e["item_id"]: e.get("runs") for e in result}
        assert runs_by_id["morning_routine"] == 3
        assert runs_by_id["evening_lights"] == 2
        # Kept the most-recent timestamp
        kept = {e["item_id"]: e["timestamp"] for e in result}
        assert kept["morning_routine"] == "2026-07-03T11:00:00Z"
        assert kept["evening_lights"] == "2026-07-03T20:00:00Z"

    def test_summary_omits_runs_when_no_dupes(self, mock_client, capsys):
        """runs field is absent when all item_ids are unique."""
        mock_client.command.return_value = SAMPLE_TRACES
        args = make_args(summary=True, no_summary=False)
        assert trace_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        for entry in result:
            assert "runs" not in entry

    def test_summary_first_slices_after_dedupe(self, mock_client, capsys):
        """--first in summary mode slices the deduped list."""
        mock_client.command.return_value = DUPLICATE_TRACES
        args = make_args(summary=True, no_summary=False, first=1)
        assert trace_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        assert len(result) == 1
        # Most recent run (evening_lights has newest timestamp)
        assert result[0]["item_id"] == "evening_lights"

    def test_verbose_keeps_all_entries(self, mock_client, capsys):
        """Verbose mode (no summary) still returns all entries."""
        mock_client.command.return_value = DUPLICATE_TRACES
        args = make_args()
        assert trace_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        assert len(result) == 5

    def test_list_mode_first_n(self, mock_client, capsys):
        mock_client.command.return_value = SAMPLE_TRACES
        args = make_args(first=1)
        assert trace_cmd.run(args) == 0
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert len(parsed) == 1
        assert parsed[0]["item_id"] == "morning_routine"  # first in sample order

    def test_first_invalid_returns_1(self, capsys):
        args = make_args(first=0)
        assert trace_cmd.run(args) == 1
        err = capsys.readouterr().err
        assert "--first must be >= 1" in err

    def test_first_plus_entity_id_warns(self, mock_client, capsys):
        """--first is silently ignored when an entity_id is given; stderr warns."""
        mock_client.command.side_effect = [
            SAMPLE_TRACES,
            {"trace": {}, "item_id": "morning_routine"},
        ]
        args = make_args(entity_id="automation.morning_routine", first=3)
        assert trace_cmd.run(args) == 0
        captured = capsys.readouterr()
        assert "ignored" in captured.err

    def test_single_entity_two_step_lookup(self, mock_client, capsys):
        """Single entity calls trace/list then trace/get."""
        mock_client.command.side_effect = [
            SAMPLE_TRACES,
            {
                "trace": {"trigger/1": {}},
                "config": {},
                "item_id": "morning_routine",
            },
        ]
        args = make_args(entity_id="automation.morning_routine")
        assert trace_cmd.run(args) == 0
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed["item_id"] == "morning_routine"
        assert mock_client.command.call_count == 2
        second_call = mock_client.command.call_args_list[1]
        assert second_call[0][0] == "trace/get"
        assert second_call[1] == {
            "domain": "automation",
            "item_id": "morning_routine",
            "run_id": "abc123",
        }

    def test_single_entity_rest_lookup_closes_after_success(self, mock_client, capsys):
        mock_client.command.side_effect = [
            SAMPLE_TRACES,
            {"trace": {}, "item_id": "morning_routine"},
        ]
        args = make_args(entity_id="automation.morning_routine")
        assert trace_cmd.run(args) == 0
        mock_client.rest_client.__exit__.assert_called_once()

    def test_single_entity_rest_lookup_closes_after_failure(self, mock_client, capsys):
        mock_client.rest_client.get_json.side_effect = HARequestError("missing")
        mock_client.command.return_value = []
        args = make_args(entity_id="automation.morning_routine")
        assert trace_cmd.run(args) == 1
        mock_client.rest_client.__exit__.assert_called_once()

    def test_single_entity_not_found_returns_1(self, mock_client, capsys):
        mock_client.command.return_value = SAMPLE_TRACES
        args = make_args(entity_id="automation.nonexistent")
        assert trace_cmd.run(args) == 1
        err = capsys.readouterr().err
        assert "No traces found" in err

    def test_single_entity_trace_get_failure(self, mock_client, capsys):
        """trace/get may fail if run_id expired between list and get."""
        mock_client.command.side_effect = [
            SAMPLE_TRACES,
            HARequestError("trace/get failed: trace not found"),
        ]
        args = make_args(entity_id="automation.morning_routine")
        assert trace_cmd.run(args) == 1
        err = capsys.readouterr().err
        assert "trace not found" in err

    def test_missing_token_returns_1(self, capsys):
        with patch("tools.commands.trace.HAWSClient.from_env") as m:
            m.side_effect = HARequestError("HA_TOKEN not found")
            args = make_args()
            assert trace_cmd.run(args) == 1
        err = capsys.readouterr().err
        assert "HA_TOKEN" in err

    def test_websocket_error_returns_1(self, mock_client, capsys):
        mock_client.command.side_effect = HARequestError(
            "trace/list failed: unknown command"
        )
        args = make_args()
        assert trace_cmd.run(args) == 1
        err = capsys.readouterr().err
        assert "unknown command" in err


class TestSummaryModeSingle:
    """In summary mode, single-entity trace drops verbose fields."""

    FULL_TRACE = {
        "trace": {
            "trigger/0": [
                {
                    "path": "trigger/0",
                    "timestamp": "2026-07-03T13:00:00.434087+00:00",
                    "changed_variables": {
                        "this": {
                            "entity_id": "automation.test",
                            "state": "on",
                            "attributes": {"friendly_name": "Test", "id": "test"},
                        },
                        "trigger": {
                            "entity_id": "binary_sensor.motion",
                            "state": "on",
                            "attributes": {
                                "friendly_name": "Motion Sensor",
                                "device_class": "motion",
                                "battery_level": 85,
                            },
                        },
                        "other_var": {
                            "entity_id": "light.test",
                            "state": "off",
                            "attributes": {"brightness": 255},
                        },
                    },
                }
            ],
        },
        "config": {"alias": "Test", "triggers": [], "actions": []},
        "blueprint_inputs": {"param": "x"},
        "item_id": "test",
        "state": "stopped",
        "timestamp": {"start": "t1", "finish": "t2"},
    }

    BIG_TRACE_STEPS = 3
    BIG_TRACE = {
        "trace": {
            f"step/{i}": [{"path": f"step/{i}", "result": {"value": "y" * 1800}}]
            for i in range(BIG_TRACE_STEPS)
        },
        "config": {"alias": "Big"},
        "blueprint_inputs": {},
        "item_id": "big",
        "state": "stopped",
        "script_execution": "finished",
        "last_step": "step/2",
        "domain": "automation",
        "trigger": "time",
    }

    def test_single_entity_summary_drops_config(self, mock_client, capsys):
        mock_client.command.side_effect = [
            [{"item_id": "test", "run_id": "r1"}],
            self.FULL_TRACE,
        ]
        args = make_args(entity_id="automation.test", summary=True, no_summary=False)
        assert trace_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        assert "config" not in result
        assert "blueprint_inputs" not in result
        assert result["item_id"] == "test"
        assert result["state"] == "stopped"

    def test_single_entity_no_summary_keeps_config(self, mock_client, capsys):
        mock_client.command.side_effect = [
            [{"item_id": "test", "run_id": "r1"}],
            self.FULL_TRACE,
        ]
        args = make_args(entity_id="automation.test")
        assert trace_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        assert "config" in result
        assert "blueprint_inputs" in result

    def test_single_entity_summary_with_pretty_keeps_config(self, mock_client, capsys):
        """--pretty overrides summary projection."""
        mock_client.command.side_effect = [
            [{"item_id": "test", "run_id": "r1"}],
            self.FULL_TRACE,
        ]
        args = make_args(
            entity_id="automation.test", summary=True, no_summary=False, pretty=True
        )
        assert trace_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        assert "config" in result
        assert "blueprint_inputs" in result

    def test_summary_strips_this_attributes(self, mock_client, capsys):
        """Summary mode strips changed_variables.this.attributes."""
        _configure_single_trace(mock_client, self.FULL_TRACE)
        args = make_args(entity_id="automation.test", summary=True, no_summary=False)
        assert trace_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        for cv in _iter_changed_variables(result):
            this = cv.get("this") or {}
            assert "attributes" not in this, (
                f"this.attributes should be stripped: {this}"
            )
            if "entity_id" in this:
                assert this["entity_id"] == "automation.test"

    def test_summary_strips_all_attributes_in_changed_variables(
        self, mock_client, capsys
    ):
        """Summary strips .attributes from ALL changed_variables, not just this."""
        _configure_single_trace(mock_client, self.FULL_TRACE)
        args = make_args(entity_id="automation.test", summary=True, no_summary=False)
        assert trace_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        for cv in _iter_changed_variables(result):
            for key, val in cv.items():
                if isinstance(val, dict):
                    assert "attributes" not in val, (
                        f"{key}.attributes stripped in summary: {val}"
                    )

    def test_no_summary_keeps_all_attributes_in_changed_variables(
        self, mock_client, capsys
    ):
        """Verbose keeps .attributes in ALL changed_variables, not just this."""
        _configure_single_trace(mock_client, self.FULL_TRACE)
        args = make_args(entity_id="automation.test")
        assert trace_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        attrs_found = {"this": False, "trigger": False, "other_var": False}
        for cv in _iter_changed_variables(result):
            for key in attrs_found:
                val = cv.get(key)
                if isinstance(val, dict) and "attributes" in val:
                    attrs_found[key] = True
        assert all(attrs_found.values()), (
            f"all cv keys should have .attributes in verbose: {attrs_found}"
        )

    def test_no_summary_keeps_this_attributes(self, mock_client, capsys):
        """Verbose mode keeps this.attributes intact."""
        _configure_single_trace(mock_client, self.FULL_TRACE)
        args = make_args(entity_id="automation.test")
        assert trace_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        attrs_found = False
        for cv in _iter_changed_variables(result):
            this = cv.get("this") or {}
            if "attributes" in this:
                attrs_found = True
        assert attrs_found, "this.attributes should survive in verbose mode"

    def test_summary_with_pretty_keeps_this_attributes(self, mock_client, capsys):
        """--pretty overrides summary projection; this.attributes survives."""
        mock_client.command.side_effect = [
            [{"item_id": "test", "run_id": "r1"}],
            self.FULL_TRACE,
        ]
        args = make_args(
            entity_id="automation.test", summary=True, no_summary=False, pretty=True
        )
        assert trace_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        trace = result.get("trace", {})
        attrs_found = False
        for entries in trace.values():
            if isinstance(entries, list):
                for entry in entries:
                    cv = entry.get("changed_variables") or {}
                    this = cv.get("this") or {}
                    if "attributes" in this:
                        attrs_found = True
        assert attrs_found, "this.attributes should survive with --pretty"

    def test_prune_malformed_trace_does_not_crash(self, mock_client):
        """Malformed trace entries (non-list values, missing cv) should not crash."""
        weird_trace = {
            "trace": {
                "trigger/0": "not_a_list",
                "trigger/1": [{"path": "x"}],  # no changed_variables
                "trigger/2": [{"changed_variables": None}],
                "trigger/3": [{"changed_variables": {"this": None}}],
                "trigger/4": [
                    {"changed_variables": {"this": {"state": "on"}}}
                ],  # no attrs
            },
            "config": {},
            "item_id": "test",
            "state": "stopped",
        }
        mock_client.command.side_effect = [
            [{"item_id": "test", "run_id": "r1"}],
            weird_trace,
        ]
        args = make_args(entity_id="automation.test", summary=True, no_summary=False)
        assert trace_cmd.run(args) == 0

    def test_summary_timestamp_is_start_string(self, mock_client, capsys):
        mock_client.command.return_value = [
            {
                "item_id": "x",
                "state": "stopped",
                "trigger": "t",
                "timestamp": {
                    "start": "2026-07-02T13:00:00+00:00",
                    "finish": "2026-07-02T13:00:01+00:00",
                },
            }
        ]
        args = make_args(summary=True, no_summary=False)
        assert trace_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        assert result[0]["timestamp"] == "2026-07-02T13:00:00+00:00"

    def test_pick_keeps_fields(self, mock_client, capsys):
        mock_client.command.return_value = [
            {
                "item_id": "x",
                "state": "stopped",
                "trigger": "t",
                "timestamp": {"start": "s", "finish": "f"},
            }
        ]
        args = make_args(pick="item_id,state")
        assert trace_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        assert result == [{"item_id": "x", "state": "stopped"}]

    def test_single_entity_max_chars_caps_dict(self, mock_client, capsys):
        """--max-chars caps single-entity trace dict by dropping largest step keys."""
        mock_client.command.side_effect = [
            [{"item_id": "big", "run_id": "r1"}],
            self.BIG_TRACE,
        ]
        args = make_args(
            entity_id="automation.big",
            max_chars=2000,
            summary=True,
            no_summary=False,
        )
        assert trace_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        serialized = json.dumps(result, separators=(",", ":"))
        assert (
            len(serialized) <= 2100
        )  # small tolerance — 1 step + markers exceeds 2000 for BIG_TRACE
        assert result.get("_truncated") is True
        assert len(result.get("dropped_steps", [])) >= 1
        assert len(result.get("kept_steps", [])) >= 1

    def test_single_entity_max_chars_stderr_warning(self, mock_client, capsys):
        """stderr warns when --max-chars forces truncation of a single-entity trace."""
        mock_client.command.side_effect = [
            [{"item_id": "big", "run_id": "r1"}],
            self.BIG_TRACE,
        ]
        args = make_args(
            entity_id="automation.big",
            max_chars=2000,
            summary=True,
            no_summary=False,
        )
        assert trace_cmd.run(args) == 0
        captured = capsys.readouterr()
        assert "truncated" in captured.err.lower() or "dropped" in captured.err.lower()

    def test_single_entity_max_chars_noop_when_fits(self, mock_client, capsys):
        """--max-chars > actual size should not truncate or add _truncated marker."""
        mock_client.command.side_effect = [
            [{"item_id": "test", "run_id": "r1"}],
            self.FULL_TRACE,
        ]
        args = make_args(
            entity_id="automation.test",
            max_chars=99999,
            summary=True,
            no_summary=False,
        )
        assert trace_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        assert "_truncated" not in result

    def test_list_default_cap(self, mock_client, capsys):
        mock_client.command.return_value = [
            {
                "item_id": f"auto_{i}",
                "state": "stopped",
                "trigger": "t",
                "timestamp": {
                    "start": f"2026-07-02T{i:02d}:00:00+00:00",
                    "finish": "f",
                },
            }
            for i in range(200)
        ]
        args = make_args(summary=True, no_summary=False)
        assert trace_cmd.run(args) == 0
        out = capsys.readouterr().out
        assert len(out) <= 8000
        result = json.loads(out)
        assert result[-1].get("_truncated") is True


class TestValidateArgs:
    """Direct unit tests for the extracted _validate_args helper."""

    def _args(self, **overrides):
        defaults = dict(entity_id=None, first=None, pretty=False)
        defaults.update(overrides)
        return Namespace(**defaults)

    def test_invalid_entity_id_returns_1(self, capsys):
        from tools.commands.trace import _validate_args

        assert _validate_args(self._args(entity_id="not-valid"), summary=False) == 1
        assert "Invalid entity_id" in capsys.readouterr().err

    def test_first_less_than_1_returns_1(self, capsys):
        from tools.commands.trace import _validate_args

        assert _validate_args(self._args(first=0), summary=False) == 1
        assert "--first must be >= 1" in capsys.readouterr().err

    def test_valid_args_returns_none(self):
        from tools.commands.trace import _validate_args

        assert _validate_args(self._args(), summary=False) is None

    def test_first_with_entity_id_warns_in_verbose(self, capsys):
        from tools.commands.trace import _validate_args

        assert (
            _validate_args(
                self._args(entity_id="automation.foo", first=5), summary=False
            )
            is None
        )
        assert "ignored" in capsys.readouterr().err


class TestShapeSingleEntityData:
    """Direct unit tests for _shape_single_entity_data."""

    def test_summary_strips_config_and_blueprint_inputs(self):
        from tools.commands.trace import _shape_single_entity_data

        data = {
            "config": {"x": 1},
            "blueprint_inputs": {"y": 2},
            "trace": {},
            "extra": 1,
        }
        args = Namespace(pick=None, max_chars=None, pretty=False)
        out = _shape_single_entity_data(data, args, summary=True)
        assert "config" not in out
        assert "blueprint_inputs" not in out
        assert out["extra"] == 1

    def test_verbose_keeps_config(self):
        from tools.commands.trace import _shape_single_entity_data

        data = {"config": {"x": 1}, "trace": {}}
        args = Namespace(pick=None, max_chars=None, pretty=False)
        out = _shape_single_entity_data(data, args, summary=False)
        assert "config" in out

    def test_pick_keeps_only_specified_keys(self):
        from tools.commands.trace import _shape_single_entity_data

        data = {"config": "x", "trace": {}, "extra": "keep"}
        args = Namespace(pick="extra", max_chars=None, pretty=False)
        out = _shape_single_entity_data(data, args, summary=True)
        assert out == {"extra": "keep"}

    def test_max_chars_invokes_cap_trace_dict(self):
        from tools.commands.trace import _shape_single_entity_data

        data = {"trace": {f"step_{i}": {"x": i} for i in range(50)}}
        args = Namespace(pick=None, max_chars=200, pretty=False)
        out = _shape_single_entity_data(data, args, summary=True)
        assert out.get("_truncated") is True


class TestShapeListData:
    """Direct unit tests for _shape_list_data."""

    def test_first_truncates_list(self):
        from tools.commands.trace import _shape_list_data

        data = [{"i": i} for i in range(10)]
        args = Namespace(first=3, pick=None, max_chars=None)
        out = _shape_list_data(data, args, summary=False)
        assert len(out) == 3

    def test_pick_projects_keys(self):
        from tools.commands.trace import _shape_list_data

        data = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
        args = Namespace(first=None, pick="a", max_chars=None)
        out = _shape_list_data(data, args, summary=False)
        assert out == [{"a": 1}, {"a": 3}]

    def test_no_shaping_flags_returns_unchanged(self):
        from tools.commands.trace import _shape_list_data

        data = [{"x": 1}]
        args = Namespace(first=None, pick=None, max_chars=None)
        assert _shape_list_data(data, args, summary=False) == data
