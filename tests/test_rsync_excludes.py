"""Integration tests for rsync exclude rules.

Tests that the .rsync-excludes-* files correctly:
1. Exclude sensitive directories from transfer (pull)
2. Protect server-side runtime state from deletion during push
3. Allow normal config files to sync
"""

# pylint: disable=import-error,redefined-outer-name

import shutil
import subprocess
from pathlib import Path

import pytest

PULL_EXCLUDES = Path(__file__).parent.parent / ".rsync-excludes-pull"
PUSH_EXCLUDES = Path(__file__).parent.parent / ".rsync-excludes-push"

pytestmark = pytest.mark.skipif(not shutil.which("rsync"), reason="rsync not installed")


@pytest.fixture
def local_dir(tmp_path):
    """Create a local config tree used as the rsync source."""
    local = tmp_path / "local"
    local.mkdir()
    (local / ".storage" / "core").mkdir(parents=True)
    (local / ".storage" / "core" / "entity_registry").write_text(
        "entity_registry_v2_updated"
    )
    (local / "configuration.yaml").write_text("homeassistant: NEW")
    (local / "automations.yaml").write_text("automation: NEW")
    return local


@pytest.fixture
def remote_dir(tmp_path):
    """Create a remote config tree used as the rsync destination."""
    remote = tmp_path / "remote"
    remote.mkdir()
    (remote / ".storage" / "auth").mkdir(parents=True)
    (remote / ".storage" / "core").mkdir(parents=True)
    (remote / "backups").mkdir()
    (remote / "www").mkdir()
    (remote / "custom_components").mkdir()
    (remote / "image").mkdir()
    (remote / "tmp_backups").mkdir()
    (remote / "deps").mkdir()
    (remote / "tts").mkdir()

    (remote / ".storage" / "auth" / "tokens.json").write_text("SECRET_AUTH_TOKEN")
    # auth also exists as a plain file on real HA (not just a directory)
    (remote / ".storage" / "auth_file").write_text("SECRET_AUTH_FILE_DATA")
    (remote / ".storage" / "trace.saved_traces").write_text("trace_data")
    (remote / ".storage" / "core" / "entity_registry").write_text("entity_registry_v1")
    (remote / "zigbee2mqtt").mkdir()
    (remote / "zigbee2mqtt" / "database.db").write_text("zigbee_db")
    (remote / "zigbee2mqtt" / "state.json").write_text("zigbee_state")
    (remote / "zigbee2mqtt" / "configuration.yaml").write_text("zigbee_config")
    (remote / "zigbee2mqtt" / "coordinator_backup.json").write_text('{"channel":15}')
    (remote / "zigbee2mqtt" / "log").mkdir()
    (remote / "zigbee2mqtt" / "log" / "2026-01-01.log").write_text("zigbee_log")
    (remote / "backups" / "backup.tar").write_text("backup_data")
    (remote / "www" / "index.html").write_text("<html>dashboard</html>")
    (remote / "custom_components" / "my_comp.py").write_text("custom_code")

    (remote / "configuration.yaml").write_text("homeassistant: old")
    (remote / "automations.yaml").write_text("automation: old")

    return remote


@pytest.fixture
def pull_dir(tmp_path):
    """Create a local directory used as the rsync pull destination."""
    pull = tmp_path / "local_pull"
    pull.mkdir()
    return pull


def run_rsync(source, dest, excludes):
    """Run rsync with the provided exclude file."""
    cmd = [
        "rsync",
        "-avz",
        "--delete",
        "--checksum",
        f"--exclude-from={excludes}",
        f"{source}/",
        f"{dest}/",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise AssertionError(
            f"rsync failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result


def test_push_updates_config_files(local_dir, remote_dir):
    """Push updates to configuration files."""
    run_rsync(local_dir, remote_dir, PUSH_EXCLUDES)

    assert (remote_dir / "configuration.yaml").exists(), (
        "configuration.yaml should be updated"
    )
    assert (remote_dir / "configuration.yaml").read_text() == "homeassistant: NEW", (
        "configuration.yaml should have NEW content"
    )
    assert (remote_dir / "automations.yaml").exists(), (
        "automations.yaml should be updated"
    )
    assert (remote_dir / "automations.yaml").read_text() == "automation: NEW", (
        "automations.yaml should have NEW content"
    )


def test_push_preserves_storage(local_dir, remote_dir):
    """Push does not overwrite .storage contents on remote."""
    run_rsync(local_dir, remote_dir, PUSH_EXCLUDES)

    assert (
        remote_dir / ".storage" / "core" / "entity_registry"
    ).read_text() == "entity_registry_v1", "Remote .storage should remain unchanged"


def test_push_preserves_backups(local_dir, remote_dir):
    """Push preserves backups on the remote."""
    run_rsync(local_dir, remote_dir, PUSH_EXCLUDES)

    assert (remote_dir / "backups" / "backup.tar").exists(), (
        "Backups should be preserved"
    )


def test_push_preserves_www(local_dir, remote_dir):
    """Push preserves the www directory on the remote."""
    run_rsync(local_dir, remote_dir, PUSH_EXCLUDES)

    assert (remote_dir / "www" / "index.html").exists(), (
        "www directory should be preserved"
    )


def test_push_preserves_custom_components(local_dir, remote_dir):
    """Push preserves custom_components on the remote."""
    run_rsync(local_dir, remote_dir, PUSH_EXCLUDES)

    assert (remote_dir / "custom_components" / "my_comp.py").exists(), (
        "custom_components should be preserved"
    )


def test_pull_excludes_auth_tokens(pull_dir, remote_dir):
    """Pull excludes auth tokens locally."""
    run_rsync(remote_dir, pull_dir, PULL_EXCLUDES)

    assert not (pull_dir / ".storage" / "auth" / "tokens.json").exists(), (
        "Auth tokens should NOT be pulled"
    )


def test_pull_preserves_existing_excluded_auth_token(pull_dir, remote_dir):
    """--delete must not remove protected local auth state."""
    (pull_dir / ".storage" / "auth").mkdir(parents=True)
    protected = pull_dir / ".storage" / "auth" / "tokens.json"
    protected.write_text("LOCAL_SECRET")

    run_rsync(remote_dir, pull_dir, PULL_EXCLUDES)

    assert protected.read_text() == "LOCAL_SECRET"


def test_pull_allows_storage_core(pull_dir, remote_dir):
    """Pull includes non-sensitive .storage files."""
    run_rsync(remote_dir, pull_dir, PULL_EXCLUDES)

    assert (
        pull_dir / ".storage" / "core" / "entity_registry"
    ).read_text() == "entity_registry_v1", "Storage core should be pulled"


def test_pull_excludes_backups(pull_dir, remote_dir):
    """Pull excludes backups locally."""
    run_rsync(remote_dir, pull_dir, PULL_EXCLUDES)

    assert not (pull_dir / "backups" / "backup.tar").exists(), (
        "Backups should NOT be pulled"
    )


def test_pull_gets_config_files(pull_dir, remote_dir):
    """Pull brings down config files."""
    run_rsync(remote_dir, pull_dir, PULL_EXCLUDES)

    assert (pull_dir / "configuration.yaml").exists(), (
        "configuration.yaml should be pulled"
    )
    assert (pull_dir / "automations.yaml").exists(), "automations.yaml should be pulled"


def test_pull_excludes_auth_file(tmp_path, pull_dir):
    """Pull excludes .storage/auth when it exists as a plain file (real HA layout)."""
    remote = tmp_path / "remote_auth_file"
    remote.mkdir()
    (remote / ".storage").mkdir()
    (remote / ".storage" / "auth").write_text("SECRET_AUTH_DATA")
    (remote / ".storage" / "core.entity_registry").write_text("entities")

    run_rsync(remote, pull_dir, PULL_EXCLUDES)

    assert not (pull_dir / ".storage" / "auth").exists(), (
        ".storage/auth file should NOT be pulled"
    )


def test_pull_excludes_trace_saved_traces(pull_dir, remote_dir):
    """Pull excludes .storage/trace.saved_traces."""
    run_rsync(remote_dir, pull_dir, PULL_EXCLUDES)

    assert not (pull_dir / ".storage" / "trace.saved_traces").exists(), (
        "trace.saved_traces should NOT be pulled"
    )


def test_pull_zigbee2mqtt_selective(pull_dir, remote_dir):
    """Pull includes Z2M config files but excludes runtime state."""
    run_rsync(remote_dir, pull_dir, PULL_EXCLUDES)

    assert (pull_dir / "zigbee2mqtt" / "configuration.yaml").exists(), (
        "zigbee2mqtt/configuration.yaml SHOULD be pulled"
    )
    assert (pull_dir / "zigbee2mqtt" / "coordinator_backup.json").exists(), (
        "zigbee2mqtt/coordinator_backup.json SHOULD be pulled"
    )
    assert not (pull_dir / "zigbee2mqtt" / "database.db").exists(), (
        "zigbee2mqtt/database.db should NOT be pulled (runtime state)"
    )
    assert not (pull_dir / "zigbee2mqtt" / "state.json").exists(), (
        "zigbee2mqtt/state.json should NOT be pulled (runtime state)"
    )
    assert not (pull_dir / "zigbee2mqtt" / "log").exists(), (
        "zigbee2mqtt/log/ should NOT be pulled"
    )


def test_pull_deletes_stale_local_files(pull_dir, remote_dir):
    """Pull deletes stale local files with --delete."""
    (pull_dir / "stale_file.yaml").write_text("should be deleted")

    run_rsync(remote_dir, pull_dir, PULL_EXCLUDES)

    assert not (pull_dir / "stale_file.yaml").exists(), (
        "Stale files should be deleted by --delete"
    )


def test_push_excludes_z2m_coordinator_backup(local_dir, remote_dir):
    """L80: the push-side exclude file must protect z2m coordinator_backup.json."""
    run_rsync(local_dir, remote_dir, PUSH_EXCLUDES)

    assert (remote_dir / "zigbee2mqtt" / "coordinator_backup.json").exists(), (
        "coordinator_backup.json should be preserved on remote during push"
    )


def test_push_does_not_overwrite_local_z2m_coordinator_backup(local_dir, remote_dir):
    """A protected local coordinator backup cannot overwrite remote state."""
    local_backup = local_dir / "zigbee2mqtt" / "coordinator_backup.json"
    local_backup.parent.mkdir()
    local_backup.write_text('{"channel": 25}')

    run_rsync(local_dir, remote_dir, PUSH_EXCLUDES)

    assert (remote_dir / "zigbee2mqtt" / "coordinator_backup.json").read_text() == (
        '{"channel":15}'
    )
