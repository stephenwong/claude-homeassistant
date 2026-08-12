import pytest


@pytest.fixture
def config_dir(tmp_path):
    return tmp_path


@pytest.fixture(autouse=True)
def _stub_load_env_file(monkeypatch):
    monkeypatch.setattr("tools.common.load_env_file", lambda: None)
    monkeypatch.setattr("tools.validators.stale_sensors.load_env_file", lambda: None)
