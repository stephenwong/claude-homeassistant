#!/usr/bin/env python3
"""Official Home Assistant configuration validator using the actual HA package.

This leverages Home Assistant's own validation tools for the most
accurate results.
"""

import re
import subprocess
import sys

from tools.common import get_env_int
from tools.validators.base import ValidatorBase

_BENIGN_PACKAGE_INSTALL_MARKERS = (
    "unable to install package",
    "no solution found when resolving",
    "requirements are unsatisfiable",
    "requirements for",
)

_IGNORABLE_STDOUT_SUBSTRINGS = (
    "turbojpeg",
    "libturbojpeg",
    "Camera snapshot performance will be sub-optimal",
    "Unable to locate turbojpeg library",
    "TurboJPEGSingleton",
    "selector']['reorder']",
    "Traceback (most recent call last):",
    "could not be loaded",
) + _BENIGN_PACKAGE_INSTALL_MARKERS
_STDERR_ERROR_INDICATORS = ("error", "fail", "fatal", "exception", "traceback")
_STDERR_BENIGN_ANY = ("debug", "info:", "starting")
_STDERR_BENIGN_PHRASES = (
    "voluptuous",
    "setup of domain",
    "setup of platform",
    "loading",
    "initialized",
)


def _is_explicit_error(line: str) -> bool:
    """Return whether a stdout line starts with an explicit error prefix."""
    return bool(re.match(r"^\W*(ERROR|RuntimeError)\b", line, re.I))


def _is_benign_package_line(line: str) -> bool:
    """Return whether output contains a known-benign package marker."""
    line_lower = line.lower()
    return any(marker in line_lower for marker in _BENIGN_PACKAGE_INSTALL_MARKERS)


class HAOfficialValidator(ValidatorBase):
    """Validates Home Assistant configuration using the official HA package.

    This validator depends on the installed HA version and Python packages,
    not just config files. It declares no file dependencies so the caching
    layer never caches it.
    """

    validator_name = "Home Assistant configuration"

    def __init__(
        self, config_dir: str = "config", quiet: bool = False, summary: bool = False
    ):
        """Initialize validator and timeout configuration."""
        super().__init__(config_dir, quiet=quiet, summary=summary)
        self.validation_timeout = self._get_timeout("HA_VALIDATION_TIMEOUT", 120)

    def file_deps(self) -> list[str]:
        """Return an empty list — result depends on HA environment, not files."""
        return []

    def _get_timeout(self, env_name: str, default: int) -> int:
        """Read timeout env vars with validation."""
        timeout, warning = get_env_int(env_name, default)
        if warning:
            self.warnings.append(warning)
        return timeout

    def run_ha_check_config(self) -> bool:
        """Run Home Assistant's official check_config script."""
        try:
            # Use the hass command to check configuration
            cmd = [
                sys.executable,
                "-m",
                "homeassistant",
                "--config",
                str(self.config_dir),
                "--script",
                "check_config",
            ]

            # Run the command
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=self.validation_timeout,
            )

            # Parse the output
            self.parse_check_config_output(result.stdout, result.stderr)

            # Return success if exit code is 0. HA's "Successful config (partial)"
            # exits 0; on exit 0 demote any parsed errors to warnings so they stay
            # visible without failing CI (per AGENTS.md "exit 0 partial = pass").
            passed = result.returncode == 0
            if not passed and not self.errors:
                self.errors.append(
                    "Home Assistant configuration check failed without diagnostics"
                )
            if passed and self.errors:
                self.warnings.extend(self.errors)
                self.errors.clear()
            return passed

        except subprocess.TimeoutExpired:
            self.errors.append(
                "Home Assistant configuration check timed out "
                f"after {self.validation_timeout} seconds"
            )
            return False
        except FileNotFoundError:
            self.errors.append(
                "Home Assistant not found. "
                "Please install with: pip install homeassistant"
            )
            return False
        except (subprocess.SubprocessError, OSError) as e:
            self.errors.append(f"Failed to run Home Assistant config check: {e}")
            return False

    def _has_benign_package_context(self, stdout: str, stderr: str) -> bool:
        blob = (stdout + "\n" + stderr).lower()
        return _is_benign_package_line(blob)

    def is_ignorable_message(self, line: str) -> bool:
        """Check if a message can be safely ignored (non-critical warnings).

        RuntimeError/^^^ suppression is handled contextually in
        parse_check_config_output (M12) — not here.
        """
        line_lower = line.lower()
        return any(s.lower() in line_lower for s in _IGNORABLE_STDOUT_SUBSTRINGS)

    def is_ignorable_traceback_line(
        self, line: str, *, benign_ctx: bool = False
    ) -> bool:
        """Check if line is part of a Python traceback we can ignore.

        Only suppresses ``File ... .py`` lines when *benign_ctx* is set
        (i.e. the surrounding output contains a known-benign package-install
        marker). Without that context, traceback lines are real errors.

        ``benign_ctx`` defaults to ``False`` so existing callers that invoke
        ``is_ignorable_traceback_line(line)`` without the keyword continue to
        get the strict (non-suppressing) behaviour.
        """
        return benign_ctx and line.strip().startswith("File ") and ".py" in line

    def parse_check_config_output(self, stdout: str, stderr: str):
        """Parse Home Assistant check_config output."""
        benign_ctx = self._has_benign_package_context(stdout, stderr)
        self._parse_stdout(stdout, benign_ctx=benign_ctx)
        self._parse_stderr(stderr)

    def _parse_stdout(self, stdout: str, *, benign_ctx: bool) -> None:
        """Classify each stdout line into self.info / self.errors / self.warnings.

        Skips ignorable lines and (when *benign_ctx*) benign-traceback lines.
        """
        if not stdout:
            return
        for line in stdout.split("\n"):
            line = line.strip()
            if not line:
                continue
            if _is_explicit_error(line) and not (
                benign_ctx and _is_benign_package_line(line)
            ):
                self._classify_stdout_line(line)
                continue
            if self.is_ignorable_message(line):
                continue
            if benign_ctx:
                if line.strip() == "^^^":
                    continue
                if self.is_ignorable_traceback_line(line, benign_ctx=True):
                    continue
            self._classify_stdout_line(line)

    def _classify_stdout_line(self, line: str) -> None:
        """Route a single (non-ignorable) stdout line to the right bucket."""
        if (
            "Testing configuration at" in line
            or "Configuration check successful!" in line
        ):
            self.info.append(f"HA Check: {line}")
        elif m := re.search(r"(?:found\s+)?(\d+)\s+errors?\b", line, re.I):
            if m.group(1) == "0":
                self.info.append(f"HA Check: {line}")
            else:
                self.errors.append(f"HA Check: {line}")
        elif _is_explicit_error(line):
            self.errors.append(f"HA Check: {line}")
        elif re.match(r"^\W*WARNING\b", line, re.I):
            self.warnings.append(f"HA Check: {line}")
        else:
            if line and not line.startswith("INFO:"):
                self.info.append(f"HA Check: {line}")

    def _parse_stderr(self, stderr: str) -> None:
        """Classify each stderr line. Real-error indicators override benign phrases."""
        if not stderr:
            return
        for line in stderr.split("\n"):
            line = line.strip()
            if not line:
                continue
            line_lower = line.lower()
            if re.match(r"^\W*warning\b", line, re.I):
                self.warnings.append(f"HA Warning: {line}")
                continue
            if any(ind in line_lower for ind in _STDERR_ERROR_INDICATORS):
                self.errors.append(f"HA Error: {line}")
                continue
            if any(x in line_lower for x in _STDERR_BENIGN_ANY):
                continue
            if any(x in line_lower for x in _STDERR_BENIGN_PHRASES):
                continue
            self.errors.append(f"HA Error: {line}")

    def _validate(self) -> bool:
        """Run complete validation using Home Assistant."""
        # Check if configuration.yaml exists
        config_file = self.config_dir / "configuration.yaml"
        if not config_file.exists():
            self.errors.append("configuration.yaml not found")
            return False

        # Run the official Home Assistant validation
        return self.run_ha_check_config()


def main() -> int:
    """Run Home Assistant configuration validation from command line."""
    return HAOfficialValidator.run_cli(
        "Validate Home Assistant configuration using the official HA package."
    )


if __name__ == "__main__":
    raise SystemExit(main())
