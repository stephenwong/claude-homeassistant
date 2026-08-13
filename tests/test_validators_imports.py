"""Re-import tests for moved validators under tools/validators/."""

import pytest

from tools.validators.duplicate_ids import DuplicateIDValidator
from tools.validators.ha_official import HAOfficialValidator
from tools.validators.references import ReferenceValidator
from tools.validators.services import ServiceValidator
from tools.validators.templates import TemplateValidator
from tools.validators.yaml import YAMLValidator

VALIDATOR_CLASSES = (
    DuplicateIDValidator,
    YAMLValidator,
    ReferenceValidator,
    ServiceValidator,
    HAOfficialValidator,
    TemplateValidator,
)


def test_common_reexports_base():
    import tools.common as c
    import tools.validators.base as b

    assert c.ValidatorBase is b.ValidatorBase
    assert c.HAYamlLoader is b.HAYamlLoader


@pytest.mark.parametrize(
    ("cls", "expected_name"),
    [
        (YAMLValidator, "YAML syntax"),
        (DuplicateIDValidator, "Duplicate automation IDs"),
        (ReferenceValidator, "Entity/device references"),
        (ServiceValidator, "Service references"),
        (HAOfficialValidator, "Home Assistant configuration"),
        (TemplateValidator, "Jinja2 templates"),
    ],
)
def test_validator_imports_have_expected_shape(cls, expected_name):
    v = cls()
    assert v.validator_name == expected_name
    assert v.errors == []
    assert v.warnings == []
    assert v.info == []


@pytest.mark.parametrize("cls", VALIDATOR_CLASSES)
def test_validator_quiet_kwarg_accepted(cls):
    assert cls(quiet=True).quiet is True


class TestFileDeps:
    def test_yaml_validator_file_deps_yaml_files(self):
        """YAMLValidator depends on top-level YAML files (base class default)."""
        v = YAMLValidator()
        deps = v.file_deps()
        assert "*.yaml" in deps
        assert "*.yml" in deps

    def test_reference_validator_file_deps_includes_storage(self):
        """ReferenceValidator depends on YAML files + .storage registries."""
        v = ReferenceValidator()
        deps = v.file_deps()
        assert "*.yaml" in deps
        assert ".storage/core.entity_registry" in deps
        assert ".storage/core.device_registry" in deps
        assert ".storage/core.area_registry" in deps

    def test_duplicate_id_validator_file_deps(self):
        """DuplicateIDValidator reads automations.yaml and scripts.yaml (M10b)."""
        v = DuplicateIDValidator()
        deps = v.file_deps()
        assert "automations.yaml" in deps
        assert "scripts.yaml" in deps
        assert len(deps) == 2

    def test_service_validator_file_deps(self):
        """ServiceValidator returns empty deps (depends on live HA)."""
        v = ServiceValidator()
        deps = v.file_deps()
        assert deps == []

    def test_template_validator_file_deps(self):
        """TemplateValidator returns empty deps (depends on live HA)."""
        v = TemplateValidator()
        deps = v.file_deps()
        assert deps == []

    def test_ha_official_validator_file_deps(self):
        """HAOfficialValidator returns empty deps (result depends on HA env)."""
        v = HAOfficialValidator()
        deps = v.file_deps()
        assert deps == []


def test_entity_definitions_imports():
    from tools.validators.entity_definitions import EntityDefinitionExtractor

    assert {"sun.sun", "zone.home"} == EntityDefinitionExtractor.BUILTIN_ENTITIES


class TestL80FileDeps:
    """L80: round-trip compute_hash + summary= kwarg forwarding."""

    def test_file_deps_patterns_resolve_against_fixture_config(self, tmp_path):
        """Every declared dependency pattern must match a fixture file."""
        from pathlib import Path

        from tools.cache import compute_hash

        for cls in (DuplicateIDValidator, YAMLValidator, ReferenceValidator):
            config_dir = Path(tmp_path)
            instance = cls(config_dir=str(config_dir))
            deps = instance.file_deps()
            for pattern in deps:
                if pattern.startswith("*"):
                    path = config_dir / ("fixture" + pattern[1:])
                else:
                    path = config_dir / pattern
                    path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}")
            assert all(any(config_dir.glob(pattern)) for pattern in deps)
            h = compute_hash(Path(config_dir), deps)
            assert isinstance(h, str)
            assert len(h) == 64


@pytest.mark.parametrize("cls", VALIDATOR_CLASSES)
def test_summary_kwarg_forwarded(cls):
    """L80 (AGENTS.md): every subclass __init__ must accept+forward summary=."""
    instance = cls(summary=True)
    assert hasattr(instance, "summary")
    assert instance.summary is True
