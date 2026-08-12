#!/usr/bin/env python3
"""Stale sensor diagnostic validator for Home Assistant.

Queries the live HA REST API for states, filters out disabled/hidden entities and
virtual platforms using the local core.entity_registry, and identifies sensors
whose last update or radio check-in exceeds the configured threshold.
"""

import json
import os
import time
from datetime import UTC, datetime
from typing import Any

from tools.common import HARequestError, get_env_int, load_env_file
from tools.ha.client import HAClient
from tools.validators._storage import load_storage_registry
from tools.validators.base import ValidatorBase

_EPOCH_MS_THRESHOLD = 1e11
DEFAULT_THRESHOLD_HOURS = 24
DEFAULT_ONLY_DOMAINS = frozenset({"sensor"})
DEFAULT_EXCLUDE_PLATFORMS = frozenset(
    {
        "template",
        "group",
        "derivative",
        "utility_meter",
        "min_max",
        "threshold",
        "integration",
        "history_stats",
        "filter",
    }
)


class StaleSensorValidator(ValidatorBase):
    """Validates that active sensors are updating and have not gone stale."""

    validator_name = "Stale sensors"

    def __init__(
        self,
        config_dir: str = "config",
        quiet: bool = False,
        summary: bool = False,
        threshold_hours: int = DEFAULT_THRESHOLD_HOURS,
        only_domains: set[str] | None = None,
        exclude_platforms: set[str] | None = None,
        ignore_restored: bool = False,
        fail_on_stale: bool = False,
        *,
        exclude_domains: set[str] | None = None,
    ):
        """Initialize the StaleSensorValidator.

        Args:
            config_dir: Configuration directory path.
            quiet: If True, suppress stdout printing on success.
            summary: If True, use compact output format.
            threshold_hours: Inactivity limit in hours before triggering stale state.
            only_domains: Domains to analyze (e.g., {'sensor'}).
            exclude_platforms: Integration platforms to ignore (e.g., {'template'}).
            exclude_domains: Domains to subtract from only_domains.
            ignore_restored: If True, restored entities at startup won't be flagged.
            fail_on_stale: If True, validator returns False when staleness is detected.
        """
        super().__init__(config_dir, quiet=quiet, summary=summary)
        self.threshold_hours = threshold_hours
        base_domains = (
            only_domains if only_domains is not None else set(DEFAULT_ONLY_DOMAINS)
        )
        self.only_domains = base_domains - (exclude_domains or set())
        self.fail_on_stale = fail_on_stale
        self.stale_entities: list[str] = []

        self.exclude_platforms = (
            exclude_platforms
            if exclude_platforms is not None
            else set(DEFAULT_EXCLUDE_PLATFORMS)
        )
        self.ignore_restored = ignore_restored

    def file_deps(self) -> list[str]:
        """Return empty list to completely skip validation caching.

        Staleness is a time-sensitive live check and must never be cached.
        """
        return []

    def _get_current_time(self) -> datetime:
        """Get the current UTC time. Easy to mock in unit tests."""
        return datetime.now(UTC)

    def _load_registry(self) -> dict[str, Any] | None:
        """Load and parse the local entity registry with decode-only retry.

        Atomic writes to ``.storage/`` can cause a transient
        ``JSONDecodeError``; only that failure receives the 100ms retry.
        """
        registry_file = self.config_dir / ".storage" / "core.entity_registry"
        if not registry_file.exists():
            self.info.append(
                f"Entity registry not found at {registry_file}. "
                "Falling back to state-only analysis."
            )
            return None

        last_error: Exception | None = None
        for attempt in range(2):
            try:
                return load_storage_registry(
                    registry_file, list_key="entities", key_field="entity_id"
                )
            except json.JSONDecodeError as e:
                last_error = e
                if attempt == 0:
                    time.sleep(0.1)
                    continue
            except (OSError, KeyError, TypeError, ValueError, AttributeError) as e:
                last_error = e
            break

        if last_error is not None:
            self.warnings.append(
                f"Failed to read entity registry: {last_error}. "
                "Falling back to state-only analysis."
            )
        return None

    def _parse_iso_string(self, s: str) -> datetime | None:
        """Parse an ISO-8601 string into an offset-aware datetime.

        Normalises a trailing ``Z`` to ``+00:00``, attaches UTC to naive
        datetimes, and returns ``None`` on parse failure (no warning —
        callers decide whether to warn).
        """
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt

    def _parse_epoch(self, value: float) -> datetime:
        """Parse a numeric epoch value, normalising milliseconds to seconds."""
        if value > _EPOCH_MS_THRESHOLD:
            value = value / 1000.0
        return datetime.fromtimestamp(value, tz=UTC)

    def parse_timestamp(self, value: Any) -> datetime | None:
        """Parse string or epoch numeric timestamps into offset-aware UTC datetimes."""
        if value is None or isinstance(value, bool):
            return None

        if isinstance(value, (int, float)):
            try:
                return self._parse_epoch(value)
            except (OverflowError, OSError, ValueError) as e:  # pragma: no cover
                self.warnings.append(
                    f"Failed to parse numeric timestamp '{value}': {e}"
                )
                return None

        if isinstance(value, str):
            dt = self._parse_iso_string(value)
            if dt is not None:
                return dt
            try:
                return self._parse_epoch(float(value))
            except OverflowError, OSError, ValueError:
                self.warnings.append(f"Failed to parse string timestamp '{value}'")
                return None

        return None

    def _eligible_state(
        self, state: dict[str, Any], registry: dict[str, Any] | None
    ) -> tuple[str, str, dict[str, Any]] | None:
        """Return domain, entity ID, and attributes for an eligible state."""
        entity_id = state.get("entity_id")
        if not isinstance(entity_id, str) or "." not in entity_id:
            return None

        domain, _ = entity_id.split(".", 1)
        if domain not in self.only_domains:
            return None

        reg_entry = registry.get(entity_id) if registry else None
        if reg_entry:
            if reg_entry.get("disabled_by") is not None:
                return None
            if reg_entry.get("hidden_by") is not None:
                return None

            platform = reg_entry.get("platform")
            if platform in self.exclude_platforms:
                return None

        raw_attrs = state.get("attributes")
        if raw_attrs is not None and not isinstance(raw_attrs, dict):
            return None
        attrs = raw_attrs or {}
        if attrs.get("restored") is True:
            if not self.ignore_restored:
                self.warnings.append(
                    f"{entity_id}: Entity has 'restored' status at HA startup. "
                    "Real hardware state is unknown."
                )
            return None

        return domain, entity_id, attrs

    def _heartbeat_timestamp(self, attrs: dict[str, Any]) -> datetime | None:
        """Choose the newest available radio heartbeat timestamp."""
        heartbeat_ts = None
        for key in ("last_seen", "last_reported"):
            val = attrs.get(key)
            parsed = self.parse_timestamp(val)
            if parsed and (heartbeat_ts is None or parsed > heartbeat_ts):
                heartbeat_ts = parsed
        return heartbeat_ts

    def _baseline_timestamp(
        self, state: dict[str, Any], heartbeat_ts: datetime | None
    ) -> datetime | None:
        """Choose heartbeat first, otherwise the older state timestamp."""
        if heartbeat_ts is not None:
            return heartbeat_ts

        # Prefer last_changed (value-change) over last_updated (attr-write).
        last_changed = state.get("last_changed")
        last_updated = state.get("last_updated")
        if last_changed is not None and last_updated is not None:
            # Use whichever is older — value change before latest attr write
            # is the stronger staleness signal.
            tc = self.parse_timestamp(last_changed)
            tu = self.parse_timestamp(last_updated)
            if tc is not None and tu is not None:
                return min(tc, tu)
            return tc if tc is not None else tu

        timestamp = last_changed if last_changed is not None else last_updated
        return self.parse_timestamp(timestamp)

    def _record_stale(self, entity_id: str, warning: str) -> None:
        """Record a stale entity and its diagnostic warning."""
        self.stale_entities.append(entity_id)
        self.warnings.append(warning)

    def _scan_states(
        self,
        states: list[Any],
        registry: dict[str, Any] | None,
        current_time: datetime,
    ) -> tuple[list[str], list[str]]:
        """Scan API states and return newly stale IDs and diagnostic warnings."""
        stale_start = len(self.stale_entities)
        warning_start = len(self.warnings)

        for state in states:
            if not isinstance(state, dict):
                continue

            eligible = self._eligible_state(state, registry)
            if eligible is None:
                continue
            domain, entity_id, attrs = eligible

            # Unavailable/unknown sensors are not reporting; surface them
            # immediately rather than waiting for threshold_hours.
            st_value = state.get("state")
            if st_value in ("unavailable", "unknown", None):
                self._record_stale(
                    entity_id,
                    f"{entity_id}: State is {st_value!r} — device not reporting.",
                )
                continue

            # Heartbeat check: Z2M or custom attributes represent
            # actual radio communication time.
            # Binary sensors are only validated if a heartbeat attribute
            # is explicitly present.
            heartbeat_ts = self._heartbeat_timestamp(attrs)

            if domain == "binary_sensor" and heartbeat_ts is None:
                # Skip binary sensors without device heartbeats
                # to avoid rare-event false positives.
                continue

            # Determine baseline timestamp to compare
            baseline_ts = self._baseline_timestamp(state, heartbeat_ts)

            if baseline_ts is None:
                continue

            # Calculate difference
            delta = current_time - baseline_ts
            elapsed_hours = delta.total_seconds() / 3600.0

            if elapsed_hours > self.threshold_hours:
                self._record_stale(
                    entity_id,
                    f"{entity_id}: Stale state detected. Last update was "
                    f"{elapsed_hours:.1f} hours ago (limit: {self.threshold_hours}h).",
                )

        return (
            self.stale_entities[stale_start:],
            self.warnings[warning_start:],
        )

    def _fetch_live_states(self) -> list[Any] | None:
        """Fetch and validate the live HA state payload.

        ``None`` is an explicit skip result for missing credentials, an
        unreachable API, or an unexpected response shape. The caller keeps
        staleness policy separate from this live I/O boundary.
        """
        url = os.getenv("HA_URL")
        token = os.getenv("HA_TOKEN")
        if not url or not token:
            self.info.append(
                "Skipped stale sensor validation: HA_URL or HA_TOKEN not set."
            )
            return None

        timeout_val, warn = get_env_int("HA_STALE_TIMEOUT", 2)
        if warn:
            self.info.append(warn)
        client: HAClient | None = None
        try:
            client = HAClient(url, token, timeout=timeout_val)
            states = client.get_json("/api/states")
        except (HARequestError, OSError) as e:
            self.info.append(f"Skipped stale sensor validation: API unreachable - {e}")
            return None
        finally:
            if client is not None:
                client.close()

        if not isinstance(states, list):
            self.info.append(
                "Skipped stale sensor validation: invalid API states format."
            )
            return None
        return states

    def _validate(self) -> bool:
        """Query Home Assistant for stale sensors.

        Returns True when no staleness or when fail_on_stale is off (default).
        Returns False when fail_on_stale is True and stale sensors exist.
        Populates warnings/errors/info with diagnostic details.
        """
        self.stale_entities.clear()
        self.errors.clear()
        self.warnings.clear()
        self.info.clear()

        # Fast skip in CI/CD environments
        if os.getenv("CI", "").strip().lower() in {"1", "true", "yes"}:
            self.info.append(
                "Skipped stale sensor validation: Running in CI environment."
            )
            return True

        load_env_file()
        fail_on_stale = self.fail_on_stale
        if not fail_on_stale:
            fail_on_stale = os.getenv("HA_STALE_FAIL", "").strip().lower() in (
                "1",
                "true",
                "yes",
            )
        states = self._fetch_live_states()
        if states is None:
            return True

        registry = self._load_registry()
        current_time = self._get_current_time()

        self._scan_states(states, registry, current_time)

        if fail_on_stale and self.stale_entities:
            self.errors.append(
                f"Stale sensor check failed: "
                f"{len(self.stale_entities)} stale sensor(s) detected (see warnings)"
            )
            return False

        return True


def main() -> int:
    """Verify active HA sensors are reporting updates."""
    return StaleSensorValidator.run_cli(
        "Verify active HA sensors are reporting updates."
    )


if __name__ == "__main__":
    raise SystemExit(main())
