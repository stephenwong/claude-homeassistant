"""Shared fixtures and builders for trace command tests."""

from argparse import Namespace

from tools.common import HARequestError


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


def _mock_trace_entry(
    *,
    item_id: str = "baz_qux",
    run_id: str = "run456",
    timestamp: dict | None = None,
) -> dict:
    """Build a trace/list entry matching the real HA response shape."""
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
    """Build WebSocket responses for trace/list and trace/get calls."""
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
