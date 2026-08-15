"""Tests for tools/output_shape.py — shared JSON output-shaping helper."""

import json

import pytest

from tools.output_shape import apply_output_shape


class TestFirstBound:
    """M23: ``first`` must be >= 1 (argparse guarantees this, but the public
    function must guard against programmatic misuse)."""

    def test_first_zero_raises(self):
        with pytest.raises(ValueError, match="first"):
            apply_output_shape([1, 2, 3], first=0)

    def test_first_negative_raises(self):
        with pytest.raises(ValueError, match="first"):
            apply_output_shape([1, 2, 3], first=-1)

    def test_first_none_passes_through(self):
        assert apply_output_shape([1, 2, 3]) == [1, 2, 3]


class TestNoOp:
    def test_no_kwargs_returns_unchanged(self):
        data = [{"a": 1}, {"b": 2}]
        assert apply_output_shape(data) is data

    def test_none_explicit_returns_unchanged(self):
        data = {"x": 1}
        assert apply_output_shape(data, first=None, pick=None, max_chars=None) is data

    def test_empty_pick_delimiters_with_max_chars_truncates(self):
        data = [{"a": "x" * 100}, {"b": "y" * 100}]
        result = apply_output_shape(data, pick=" , ", max_chars=50)
        assert len(json.dumps(result)) <= 50
        assert result[0].get("_truncated") is True
        assert result[0]["shown"] == 0


class TestJsonValueContract:
    @pytest.mark.parametrize(
        "data",
        [
            None,
            True,
            3.5,
            "text",
            [None, {"nested": [True, 2]}],
            {"value": None, "items": [1, False]},
        ],
    )
    def test_shaped_values_remain_json_serializable(self, data):
        shaped = apply_output_shape(data, first=2, pick="value,nested", max_chars=200)
        assert json.loads(json.dumps(shaped, ensure_ascii=False)) == shaped


class TestFirst:
    def test_list_slice(self):
        data = [{"id": i} for i in range(10)]
        assert apply_output_shape(data, first=3) == [{"id": 0}, {"id": 1}, {"id": 2}]

    def test_dict_slice(self):
        data = {"a": 1, "b": 2, "c": 3, "d": 4}
        result = apply_output_shape(data, first=2)
        assert len(result) == 2

    def test_scalar_wraps_in_list(self):
        assert apply_output_shape(42, first=3) == [42]

    def test_overcount_clamps(self):
        data = [{"id": i} for i in range(3)]
        assert len(apply_output_shape(data, first=999)) == 3

    def test_empty_list(self):
        assert apply_output_shape([], first=5) == []

    def test_empty_dict(self):
        assert apply_output_shape({}, first=5) == {}

    def test_none_no_change(self):
        data = [1, 2, 3]
        assert apply_output_shape(data, first=None) is data


class TestPick:
    def test_list_of_dicts(self):
        data = [
            {"entity_id": "sensor.a", "state": "on", "attributes": {"x": 1}},
            {"entity_id": "sensor.b", "state": "off", "attributes": {"x": 0}},
        ]
        assert apply_output_shape(data, pick="entity_id,state") == [
            {"entity_id": "sensor.a", "state": "on"},
            {"entity_id": "sensor.b", "state": "off"},
        ]

    def test_missing_keys_omitted(self):
        data = [{"entity_id": "sensor.a", "state": "on"}]
        assert apply_output_shape(data, pick="entity_id,nonexistent") == [
            {"entity_id": "sensor.a"}
        ]

    def test_single_dict(self):
        data = {"entity_id": "sensor.a", "state": "on", "extra": "x"}
        assert apply_output_shape(data, pick="state") == {"state": "on"}

    def test_non_dict_items_pass_through(self):
        data = [42, {"entity_id": "sensor.a"}]
        assert apply_output_shape(data, pick="entity_id") == [
            42,
            {"entity_id": "sensor.a"},
        ]

    def test_empty_string_no_change(self):
        data = [{"a": 1}]
        assert apply_output_shape(data, pick="") == [{"a": 1}]

    def test_whitespace_around_fields(self):
        data = [{"entity_id": "sensor.a", "state": "on"}]
        assert apply_output_shape(data, pick=" entity_id , state ") == [
            {"entity_id": "sensor.a", "state": "on"}
        ]

    def test_scalar_passes_through(self):
        assert apply_output_shape(42, pick="state") == 42

    def test_pick_comma_only_returns_data_unchanged(self):
        """L57: pick=',' must behave as a no-op, not produce empty dicts."""
        data = {"a": 1, "b": 2}
        assert apply_output_shape(data, pick=",") == data

    def test_pick_trailing_comma_ignored(self):
        """L57: pick='a,' must pick just 'a' (no empty key)."""
        data = {"a": 1, "b": 2}
        out = apply_output_shape(data, pick="a,")
        assert out == {"a": 1}


class TestMaxChars:
    def test_truncates_list_with_marker(self):
        data = [{"id": i, "data": "x" * 50} for i in range(20)]
        result = apply_output_shape(data, max_chars=200)
        assert isinstance(result, list)
        assert result[-1].get("_truncated") is True
        assert result[-1]["total"] == 20

    def test_zero_disables(self):
        data = [{"id": i} for i in range(5)]
        assert apply_output_shape(data, max_chars=0) == data

    def test_negative_disables(self):
        data = [{"id": i} for i in range(5)]
        assert apply_output_shape(data, max_chars=-1) == data

    def test_small_data_unchanged(self):
        data = [{"id": 1}]
        assert apply_output_shape(data, max_chars=500) == [{"id": 1}]

    def test_oversized_dict_is_truncated(self):
        data = {"big": "x" * 1000}
        # Oversized dict is now truncated (H14); a dict that fits passes through.
        result = apply_output_shape(data, max_chars=10)
        assert result != data
        assert isinstance(result, dict)
        assert result.get("_truncated") is True

    @pytest.mark.parametrize(
        "data",
        [
            {"small": "x", "big1": "v" * 500, "big2": "w" * 500},
            {"big1": "v" * 500},
        ],
        ids=["multiple-keys", "single-key"],
    )
    def test_max_chars_dict_is_bounded(self, data):
        import json

        out = apply_output_shape(data, max_chars=80)
        serialized = json.dumps(out, separators=(",", ":"), ensure_ascii=False)
        assert len(serialized) <= 80
        assert isinstance(out, dict)
        assert out.get("_truncated") is True

    def test_cap_dict_marker_consistent(self):
        """Dropped keys must NOT appear in the result data."""
        data = {"small": "x", "big1": "v" * 500, "big2": "w" * 500}
        out = apply_output_shape(data, max_chars=80)
        assert isinstance(out, dict)
        dropped = out.get("dropped_keys", [])
        for k in dropped:
            assert k not in out, f"dropped key {k} still in result"
        kept = out.get("kept_keys", [])
        actual_keys = set(out.keys()) - {"_truncated", "dropped_keys", "kept_keys"}
        assert set(kept) == actual_keys

    def test_output_fits_under_limit(self):
        data = [{"data": "x" * 100} for _ in range(10)]
        result = apply_output_shape(data, max_chars=300)
        serialized = pytest.importorskip("json").dumps(result)
        assert len(serialized) <= 300


class TestPrintJson:
    """L58: print_json output formatting."""

    def test_compact_default(self, capsys):
        from tools.output_shape import print_json

        print_json({"a": 1})
        assert capsys.readouterr().out == '{"a":1}\n'

    def test_pretty_indents(self, capsys):
        from tools.output_shape import print_json

        print_json({"a": 1}, pretty=True)
        out = capsys.readouterr().out
        assert "\n" in out


class TestTruncateMarker:
    def test_only_marker_fits(self):
        """When every item is larger than max_chars, return just the marker."""
        data = [{"very_long_key_" * 50: "x"}]
        result = apply_output_shape(data, max_chars=20)
        assert result == [{"_truncated": True, "shown": 0, "total": 1}]


class TestTruncateList:
    """M24: characterisation tests for _truncate_list — pin behaviour before
    the O(N²) → O(N log N) refactor."""

    def test_truncate_list_empty_input(self):
        from tools.output_shape import _truncate_list

        result = _truncate_list([], 100)
        assert result == [{"_truncated": True, "shown": 0, "total": 0}]

    def test_truncate_list_keeps_largest_prefix_that_fits(self):
        import json

        from tools.output_shape import _truncate_list

        items = [{"i": i, "v": "x" * 10} for i in range(50)]
        max_chars = 300
        result = _truncate_list(items, max_chars)
        assert isinstance(result, list)
        assert result[-1]["_truncated"] is True
        marker = result[-1]
        assert marker["total"] == 50
        assert result[:-1] == items[: marker["shown"]]
        serialized = json.dumps(result, separators=(",", ":"), ensure_ascii=False)
        assert len(serialized) <= max_chars

    def test_truncate_list_tiny_cap_returns_marker_only(self):
        from tools.output_shape import _truncate_list

        result = _truncate_list(["x" * 200], 10)
        assert result == [{"_truncated": True, "shown": 0, "total": 1}]


class TestMaxCharsPrettyAsymmetry:
    """M25: --max-chars measures compact-JSON size, not pretty size.

    This is intentional: compact size is the proxy for token consumption.
    Pretty printing is only used for human consumption, and can produce larger
    output. If you change this behaviour, update the module docstring and
    these tests together.
    """

    def test_max_chars_measures_compact_not_pretty(self):
        import json

        from tools.output_shape import apply_output_shape

        data = [{"k": i} for i in range(5)]
        out = apply_output_shape(data, max_chars=200)
        compact = len(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
        pretty = len(json.dumps(out, indent=2, ensure_ascii=False))
        assert compact <= 200
        assert pretty >= compact, (
            "pretty output must be >= compact size (asymmetry documented in M25)"
        )


class TestOrdering:
    """Transforms apply in order: first → pick → max_chars."""

    def test_first_then_pick(self):
        data = [
            {"entity_id": "sensor.a", "state": "on"},
            {"entity_id": "sensor.b", "state": "off"},
            {"entity_id": "sensor.c", "state": "unknown"},
        ]
        assert apply_output_shape(data, first=2, pick="state") == [
            {"state": "on"},
            {"state": "off"},
        ]

    def test_first_pick_maxchars(self):
        data = [
            {"entity_id": f"sensor.{i}", "state": str(i), "attributes": {"x": i}}
            for i in range(50)
        ]
        result = apply_output_shape(
            data, first=20, pick="entity_id,state", max_chars=100
        )
        assert isinstance(result, list)
        assert result[-1].get("_truncated") is True


class TestTruncateDictByKeySize:
    """Direct tests for the unified truncation helper."""

    def test_no_truncation_when_already_fits(self):
        from tools.output_shape import truncate_dict_by_key_size

        data = {"a": 1, "b": 2}
        assert truncate_dict_by_key_size(data, max_chars=500) is data

    def test_flat_dict_default_marker_uses_dropped_keys_kept_keys(self):
        from tools.output_shape import truncate_dict_by_key_size

        data = {"small": "x", "big1": "v" * 500, "big2": "w" * 500}
        out = truncate_dict_by_key_size(data, max_chars=80)
        assert out.get("_truncated") is True
        assert "dropped_keys" in out
        assert "kept_keys" in out

    def test_flat_dict_dropped_keys_absent_from_result(self):
        from tools.output_shape import truncate_dict_by_key_size

        data = {"small": "x", "big1": "v" * 500, "big2": "w" * 500}
        out = truncate_dict_by_key_size(data, max_chars=80)
        for k in out.get("dropped_keys", []):
            assert k not in out

    def test_flat_dict_kept_keys_matches_actual_keys(self):
        from tools.output_shape import truncate_dict_by_key_size

        data = {"small": "x", "big1": "v" * 500, "big2": "w" * 500}
        out = truncate_dict_by_key_size(data, max_chars=80)
        actual = set(out.keys()) - {"_truncated", "dropped_keys", "kept_keys"}
        assert set(out["kept_keys"]) == actual

    def test_nested_target_preserves_top_level_fields(self):
        from tools.output_shape import truncate_dict_by_key_size

        data = {
            "item_id": "abc",
            "state": "on",
            "trace": {
                f"step/{i}": {"changed_variables": {"x": "y" * 200}} for i in range(5)
            },
        }
        out = truncate_dict_by_key_size(
            data,
            max_chars=400,
            target_key="trace",
            dropped_key_name="dropped_steps",
            kept_key_name="kept_steps",
            preserve_min=1,
        )
        assert out["item_id"] == "abc"
        assert out["state"] == "on"
        assert isinstance(out["trace"], dict)
        assert len(out["trace"]) >= 1

    def test_nested_target_marker_uses_custom_field_names(self):
        from tools.output_shape import truncate_dict_by_key_size

        data = {
            "item_id": "abc",
            "trace": {f"step/{i}": {"x": "y" * 200} for i in range(5)},
        }
        out = truncate_dict_by_key_size(
            data,
            max_chars=300,
            target_key="trace",
            dropped_key_name="dropped_steps",
            kept_key_name="kept_steps",
            preserve_min=1,
        )
        assert out.get("_truncated") is True
        assert "dropped_steps" in out
        assert "kept_steps" in out
        assert "dropped_keys" not in out
        assert "kept_keys" not in out

    def test_nested_target_bails_when_subdict_missing(self):
        from tools.output_shape import truncate_dict_by_key_size

        data = {"item_id": "abc"}
        out = truncate_dict_by_key_size(
            data,
            max_chars=10,
            target_key="trace",
            preserve_min=1,
        )
        assert out is data

    def test_nested_target_bails_when_subdict_too_small(self):
        from tools.output_shape import truncate_dict_by_key_size

        data = {"item_id": "abc", "trace": {"only_one": "x" * 1000}}
        out = truncate_dict_by_key_size(
            data,
            max_chars=10,
            target_key="trace",
            preserve_min=1,
        )
        assert out is data

    def test_preserve_min_prevents_dropping_below_threshold(self):
        from tools.output_shape import truncate_dict_by_key_size

        data = {k: "v" * 100 for k in "abcde"}
        out = truncate_dict_by_key_size(data, max_chars=50, preserve_min=3)
        actual_keys = set(out.keys()) - {"_truncated", "dropped_keys", "kept_keys"}
        assert len(actual_keys) >= 3

    def test_result_compact_serialization_fits_max_chars(self):
        import json

        from tools.output_shape import truncate_dict_by_key_size

        data = {"small": "x", **{f"big{i}": "v" * 200 for i in range(10)}}
        out = truncate_dict_by_key_size(data, max_chars=150)
        serialized = json.dumps(out, separators=(",", ":"))
        assert len(serialized) <= 150

    def test_does_not_mutate_input(self):
        import copy

        from tools.output_shape import truncate_dict_by_key_size

        data = {"small": "x", "big1": "v" * 500, "big2": "w" * 500}
        snapshot = copy.deepcopy(data)
        truncate_dict_by_key_size(data, max_chars=80)
        assert data == snapshot

    def test_nested_target_does_not_mutate_input(self):
        import copy

        from tools.output_shape import truncate_dict_by_key_size

        data = {
            "item_id": "abc",
            "trace": {f"step/{i}": {"x": "y" * 200} for i in range(5)},
        }
        snapshot = copy.deepcopy(data)
        truncate_dict_by_key_size(
            data,
            max_chars=300,
            target_key="trace",
            dropped_key_name="dropped_steps",
            kept_key_name="kept_steps",
            preserve_min=1,
        )
        assert data == snapshot
