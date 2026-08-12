#!/usr/bin/env python3
"""Jinja2 template linter for Home Assistant configuration files.

Renders every template string against HA's ``/api/template`` endpoint and
surfaces failures. Degrades to a brace-balance check when the HA API is
unreachable.
"""

from typing import Any

from tools.common import HARequestError
from tools.ha.client import HAClient
from tools.validators._templates import TEMPLATE_DELIMITERS, is_jinja_template
from tools.validators.base import ValidatorBase

_RUNTIME_NAMES = frozenset(
    {
        "trigger",
        "this",
        "wait",
        "wait_for_trigger",
        "repeat",
        "condition_result",
        "loop_var",
        "index",
        "index0",
    }
)
_SYNTAX_SIGNATURES = (
    "syntax error",
    "expected token",
    "unexpected",
    "no filter named",
    "encountered unknown tag",
    "unrecognized",
)


class TemplateValidator(ValidatorBase):
    """Lints Jinja2 templates by rendering them against the live HA API."""

    validator_name = "Jinja2 templates"

    def file_deps(self) -> list[str]:
        """Template validation checks live HA render API so caching is never valid."""
        return []

    @staticmethod
    def _is_template(s: str) -> bool:
        return isinstance(s, str) and is_jinja_template(s)

    @staticmethod
    def _balanced(s: str) -> bool:
        return all(
            s.count(opening) == s.count(closing)
            for opening, closing in TEMPLATE_DELIMITERS
        )

    @classmethod
    def _collect(cls, data: Any, path: str, out: list[tuple[str, str]]) -> None:
        if isinstance(data, dict):
            for k, v in data.items():
                cls._collect(v, f"{path}.{k}" if path else str(k), out)
        elif isinstance(data, list):
            for i, item in enumerate(data):
                cls._collect(item, f"{path}[{i}]", out)
        elif cls._is_template(data):
            out.append((path, data))

    @staticmethod
    def _render(client: HAClient, template: str) -> tuple[str, str]:
        try:
            resp = client.post("/api/template", json={"template": template})
        except (HARequestError, OSError) as e:
            return ("network", str(e))
        if resp.status_code == 200:
            return ("ok", resp.text)
        try:
            payload = resp.json()
            msg = (
                payload.get("message", resp.text)
                if isinstance(payload, dict)
                else resp.text
            )
        except ValueError:
            msg = resp.text
        return ("error", msg)

    def _collect_templates(self) -> tuple[list[tuple[str, str]], bool]:
        """Collect unique-candidate templates while preserving YAML diagnostics."""
        found: list[tuple[str, str]] = []
        err_before = len(self.errors)
        for fp, data in self.iter_yaml_payloads():
            self._collect(data, fp.name, found)
        return found, len(self.errors) == err_before

    def _validate_template(
        self, client: HAClient | None, path: str, template: str
    ) -> bool:
        """Validate one template, using static checks when HA is offline."""
        if client is None:
            if not self._balanced(template):
                self.errors.append(
                    f"{path}: Unbalanced template braces (live check skipped)"
                )
                return False
            return True

        status, detail = self._render(client, template)
        if status == "ok":
            return True
        if status == "network":
            self.warnings.append(f"{path}: Template render failed (network): {detail}")
            return self._validate_template(None, path, template)
        return self._record_render_error(path, detail)

    def _record_render_error(self, path: str, detail: str) -> bool:
        """Record a rendered-template error and return whether it is fatal."""
        low = detail.lower()
        if any(sig in low for sig in _SYNTAX_SIGNATURES):
            self.errors.append(f"{path}: Template syntax error: {detail}")
            return False
        if any(f"'{n}' is undefined" in low for n in _RUNTIME_NAMES):
            lines = detail.splitlines()
            self.warnings.append(
                f"{path}: Uses runtime context ({lines[0] if lines else detail})"
            )
            return True

        lines = detail.splitlines()
        self.warnings.append(
            f"{path}: Template render warning: {lines[0] if lines else detail}"
        )
        return True

    def _validate(self) -> bool:
        """Validate Jinja2 templates in all YAML files via HA render API."""
        found, all_ok = self._collect_templates()

        if not found:
            return all_ok

        client = self._try_live("Live template check", lambda c: c)

        for path, tmpl in sorted(set(found)):
            all_ok = self._validate_template(client, path, tmpl) and all_ok

        return all_ok


def main() -> int:
    """Lint Jinja2 templates from the command line."""
    return TemplateValidator.run_cli(
        "Lint Jinja2 templates in HA config via the render API."
    )


if __name__ == "__main__":
    raise SystemExit(main())
