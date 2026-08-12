#!/usr/bin/env python3
"""Service reference validator for Home Assistant configuration files.

Validates that all ``service:``/``action:`` targets in automations and scripts
correspond to loaded services on the Home Assistant instance. Degrades to a
format-only check when the HA API is unreachable.
"""

import re
from typing import Any

from tools.ha.client import HAClient  # noqa: F401 — kept for test patch targets
from tools.validators._templates import is_jinja_template
from tools.validators.base import ValidatorBase

_SERVICE_RE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")
_STEP_SERVICE_KEYS = ("action", "service")

# Top-level keys whose values are service-call payloads (not nested steps).
# Recursing into them picks up notification-button labels, target/entity
# mirrors, etc. as bogus service calls — see M9.
_NO_RECURSE = {
    "data",
    "data_template",
    "target",
    "target_template",
    "event_data",
    "event_data_template",
    "variables",
    "fields",
    "template",
}


def _device_action_service(data: Any) -> str | None:
    """Return the synthetic service name for a device-action step."""
    if (
        not isinstance(data, dict)
        or "device_id" not in data
        or "domain" not in data
        or "type" not in data
        or data.get("condition") == "device"
        or data.get("trigger") == "device"
        or data.get("platform") == "device"
        or not isinstance(data.get("domain"), str)
        or not isinstance(data.get("type"), str)
    ):
        return None

    synthetic = f"{data['domain']}.{data['type']}"
    if synthetic.startswith("!") or is_jinja_template(synthetic):
        return None
    return synthetic


def _normalize_service_catalog(catalog: list[Any]) -> set[str]:
    """Flatten a validated Home Assistant service catalog into service IDs."""
    valid: set[str] = set()
    for entry in catalog:
        if not isinstance(entry, dict):
            continue
        domain = entry.get("domain")
        services = entry.get("services")
        if not isinstance(services, dict):
            continue
        for svc in services:
            if domain and svc:
                valid.add(f"{domain}.{svc}")
    return valid


class ServiceValidator(ValidatorBase):
    """Validates service references in automation/script steps."""

    validator_name = "Service references"

    def file_deps(self) -> list[str]:
        """Service validation checks live services so caching is never valid."""
        return []

    @staticmethod
    def _looks_dynamic(value: str) -> bool:
        return value.startswith("!") or is_jinja_template(value)

    @classmethod
    def _extract_services(
        cls, data: Any, path: str, out: list[tuple[str, str]]
    ) -> None:
        if isinstance(data, dict):
            # M10a: HA device-action steps have no `service`/`action` key.
            synthetic = _device_action_service(data)
            if synthetic is not None:
                out.append((synthetic, f"{path}.device_action"))
            for k, v in data.items():
                p = f"{path}.{k}" if path else str(k)
                if k in _STEP_SERVICE_KEYS and isinstance(v, str):
                    if not cls._looks_dynamic(v):
                        out.append((v, p))
                elif k in _NO_RECURSE:
                    continue
                else:
                    cls._extract_services(v, p, out)
        elif isinstance(data, list):
            for i, item in enumerate(data):
                cls._extract_services(item, f"{path}[{i}]", out)

    def _get_services(self) -> set[str] | None:
        len_before = len(self.info)
        catalog = self._try_live(
            "Live service check", lambda c: c.get_json("/api/services")
        )
        if catalog is None:
            if len(self.info) == len_before:
                self.info.append(
                    "Live service check skipped: null response from /api/services"
                )
            return None
        if not isinstance(catalog, list):
            self.warnings.append(
                "Live service check skipped: invalid response from /api/services"
            )
            return None
        return _normalize_service_catalog(catalog)

    def _validate(self) -> bool:
        """Validate service references in all YAML files against HA API."""
        found: list[tuple[str, str]] = []
        err_before = len(self.errors)
        for fp, data in self.iter_yaml_payloads():
            self._extract_services(data, fp.name, found)
        all_ok = len(self.errors) == err_before

        if not found:
            return all_ok

        references = sorted(set(found))

        # L45: report non-domain info even when catalog fetch is skipped
        has_domain = any("." in svc for svc, _ in references)
        for svc, path in references:
            if "." not in svc:
                self.info.append(
                    f"{path}: Ignoring non-domain value '{svc}' (service reference?)"
                )

        if not has_domain:
            return all_ok

        valid = self._get_services()

        for svc, path in references:
            if "." in svc and not _SERVICE_RE.fullmatch(svc):
                self.errors.append(f"{path}: Malformed service '{svc}'")
                all_ok = False
            elif valid is not None and "." in svc and svc not in valid:
                self.warnings.append(
                    f"{path}: Unknown service '{svc}' (service not loaded?)"
                )

        return all_ok


def main() -> int:
    """Validate service references from the command line."""
    return ServiceValidator.run_cli("Validate service references in HA config.")


if __name__ == "__main__":
    raise SystemExit(main())
