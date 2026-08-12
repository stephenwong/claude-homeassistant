"""RED: assert legacy backward-compat shims are gone.

After every shim file under tools/ is deleted, these imports
will raise ImportError and the test turns GREEN.
"""

import pytest

SHIM_MODULES = [
    "tools.duplicate_id_validator",
    "tools.ha_config_validator",
    "tools.ha_official_validator",
    "tools.reference_validator",
    "tools.service_validator",
    "tools.template_validator",
    "tools.yaml_validator",
    "tools.run_tests",
]


@pytest.mark.parametrize("modname", SHIM_MODULES)
def test_legacy_shim_gone(modname: str) -> None:
    with pytest.raises(ModuleNotFoundError) as exc_info:
        __import__(modname)
    assert exc_info.value.name == modname
