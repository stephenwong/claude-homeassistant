"""Direct unit tests for tools.validators._storage.load_storage_registry."""

import json

import pytest

from tests.helpers import write_storage_registry
from tools.validators._storage import _load_storage_data, load_storage_registry


@pytest.fixture
def registry_file(tmp_path):
    """Write a registry JSON with two entities indexed by entity_id."""
    return write_storage_registry(
        tmp_path,
        "core.entity_registry",
        "entities",
        [
            {"entity_id": "sensor.one", "id": "aaa"},
            {"entity_id": "sensor.two", "id": "bbb"},
        ],
    )


class TestLoadStorageRegistry:
    def test_indexes_items_by_key_field(self, registry_file):
        result = load_storage_registry(
            registry_file, list_key="entities", key_field="entity_id"
        )
        assert set(result.keys()) == {"sensor.one", "sensor.two"}
        assert result["sensor.one"]["id"] == "aaa"

    def test_index_by_alternate_key_field(self, registry_file):
        result = load_storage_registry(
            registry_file, list_key="entities", key_field="id"
        )
        assert set(result.keys()) == {"aaa", "bbb"}

    def test_retries_transient_json_decode_error(self, registry_file, monkeypatch):
        """Retries once on transient JSONDecodeError."""
        import json

        orig_load = json.load
        calls = 0

        def flaky_load(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise json.JSONDecodeError("Expecting value", "doc", 0)
            return orig_load(*args, **kwargs)

        monkeypatch.setattr(json, "load", flaky_load)
        result = load_storage_registry(
            registry_file, list_key="entities", key_field="entity_id"
        )
        assert calls == 2
        assert "sensor.one" in result

    def test_returns_empty_when_list_empty(self, tmp_path):
        path = tmp_path / "core.entity_registry"
        path.write_text(json.dumps({"data": {"entities": []}}))
        result = load_storage_registry(path, list_key="entities", key_field="entity_id")
        assert result == {}

    def test_raises_when_list_key_missing(self, tmp_path):
        path = tmp_path / "core.entity_registry"
        path.write_text(json.dumps({"data": {}}))
        with pytest.raises(ValueError, match="entities"):
            load_storage_registry(path, list_key="entities", key_field="entity_id")

    def test_raises_valueerror_when_data_key_missing(self, tmp_path):
        path = tmp_path / "core.entity_registry"
        path.write_text(json.dumps({"version": 1}))
        with pytest.raises(ValueError, match="data"):
            load_storage_registry(path, list_key="entities", key_field="entity_id")

    def test_raises_filenotfound_for_missing_file(self, tmp_path):
        with pytest.raises(OSError):
            load_storage_registry(
                tmp_path / "nonexistent", list_key="entities", key_field="entity_id"
            )

    def test_raises_jsondecodeerror_for_malformed_json(self, tmp_path):
        path = tmp_path / "core.entity_registry"
        path.write_text("not valid json{")
        with pytest.raises(json.JSONDecodeError):
            load_storage_registry(path, list_key="entities", key_field="entity_id")

    def test_raises_valueerror_when_top_level_is_list(self, tmp_path):
        path = tmp_path / "core.entity_registry"
        path.write_text("[]")
        with pytest.raises(ValueError):
            load_storage_registry(path, list_key="entities", key_field="entity_id")

    def test_raises_valueerror_when_item_missing_key_field(self, tmp_path):
        path = tmp_path / "core.entity_registry"
        path.write_text(json.dumps({"data": {"entities": [{"wrong_field": "x"}]}}))
        with pytest.raises(ValueError):
            load_storage_registry(path, list_key="entities", key_field="entity_id")

    @pytest.mark.parametrize("key", [None, 123, True])
    def test_raises_valueerror_when_key_field_is_not_string(self, tmp_path, key):
        path = tmp_path / "core.entity_registry"
        path.write_text(json.dumps({"data": {"entities": [{"entity_id": key}]}}))
        with pytest.raises(ValueError, match="string field"):
            load_storage_registry(path, list_key="entities", key_field="entity_id")

    def test_raises_valueerror_when_top_level_is_null(self, tmp_path):
        path = tmp_path / "core.entity_registry"
        path.write_text("null")
        with pytest.raises(ValueError):
            load_storage_registry(path, list_key="entities", key_field="entity_id")

    def test_raises_valueerror_when_top_level_is_string(self, tmp_path):
        path = tmp_path / "core.entity_registry"
        path.write_text('"a string"')
        with pytest.raises(ValueError):
            load_storage_registry(path, list_key="entities", key_field="entity_id")

    def test_raises_valueerror_when_data_is_not_mapping(self, tmp_path):
        path = tmp_path / "core.entity_registry"
        path.write_text(json.dumps({"data": []}))
        with pytest.raises(ValueError):
            load_storage_registry(path, list_key="entities", key_field="entity_id")

    def test_raises_valueerror_when_json_has_duplicate_keys(self, tmp_path):
        path = tmp_path / "core.entity_registry"
        path.write_text('{"data": {"entities": []}, "data": {}}')
        with pytest.raises(ValueError, match="duplicate"):
            load_storage_registry(path, list_key="entities", key_field="entity_id")

    def test_raises_valueerror_when_item_list_is_not_list(self, tmp_path):
        path = tmp_path / "core.entity_registry"
        path.write_text(json.dumps({"data": {"entities": {}}}))
        with pytest.raises(ValueError):
            load_storage_registry(path, list_key="entities", key_field="entity_id")

    def test_raises_valueerror_when_item_not_dict(self, tmp_path):
        path = tmp_path / "core.entity_registry"
        path.write_text(json.dumps({"data": {"entities": ["not_a_dict"]}}))
        with pytest.raises(ValueError):
            load_storage_registry(path, list_key="entities", key_field="entity_id")

    def test_duplicate_keys_are_rejected(self, tmp_path):
        path = tmp_path / "core.entity_registry"
        path.write_text(
            json.dumps(
                {
                    "data": {
                        "entities": [
                            {"entity_id": "sensor.dup", "id": "first"},
                            {"entity_id": "sensor.dup", "id": "second"},
                        ]
                    }
                }
            )
        )
        with pytest.raises(ValueError, match="duplicate"):
            load_storage_registry(path, list_key="entities", key_field="entity_id")


class TestLoadStorageData:
    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            ({"items": []}, {"items": []}),
            (
                [{"state": {"entity_id": "sensor.one"}}],
                [{"state": {"entity_id": "sensor.one"}}],
            ),
        ],
    )
    def test_returns_storage_data_mapping_or_list(self, tmp_path, data, expected):
        path = tmp_path / "storage"
        path.write_text(json.dumps({"data": data}))

        assert _load_storage_data(path) == expected

    @pytest.mark.parametrize("data", [None, "not a mapping or list", 123])
    def test_rejects_non_collection_data(self, tmp_path, data):
        path = tmp_path / "storage"
        path.write_text(json.dumps({"data": data}))

        with pytest.raises(ValueError, match="'data' must be an object or list"):
            _load_storage_data(path)
