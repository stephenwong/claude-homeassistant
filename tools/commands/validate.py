"""``validate`` subcommand: in-process validator runner.

Runs YAML/reference/HA-official validators in parallel threads (no subprocess
spawn) and aggregates results. Replaces the old ValidationTestRunner.
"""

import argparse
import concurrent.futures
import contextlib
import hashlib
import inspect
import sys
import time
from dataclasses import dataclass
from typing import Any

from tools.cache import _compute_hash_status, load_cache, save_cache
from tools.common import add_config_dir_arg, add_summary_args, resolve_summary
from tools.validators.base import ValidatorBase, format_diagnostics
from tools.validators.duplicate_ids import DuplicateIDValidator
from tools.validators.ha_official import HAOfficialValidator
from tools.validators.references import ReferenceValidator
from tools.validators.services import ServiceValidator
from tools.validators.stale_sensors import StaleSensorValidator
from tools.validators.templates import TemplateValidator
from tools.validators.yaml import YAMLValidator


@dataclass
class ValidatorResult:
    """Outcome of running one validator."""

    description: str
    passed: bool
    stderr: str = ""
    duration: float = 0.0
    cached: bool = False


@dataclass(frozen=True)
class _ValidatorExecutionResult:
    """Internal validator outcome before the public description is attached."""

    passed: bool
    stderr: str = ""
    cached: bool = False
    duration: float | None = None


# (module_class, description) tuples for each validator in the default suite.
_VALIDATORS: list[tuple[type[Any], str]] = [
    (YAMLValidator, "YAML Syntax Validation"),
    (ReferenceValidator, "Entity/Device Reference Validation"),
    (DuplicateIDValidator, "Duplicate Automation ID Validation"),
    (ServiceValidator, "Service Reference Validation"),
    (TemplateValidator, "Jinja2 Template Validation"),
    (StaleSensorValidator, "Stale Sensor Validation"),
    (HAOfficialValidator, "Official Home Assistant Configuration Validation"),
]


def _build_diagnostic_stderr(instance: Any) -> str:
    """Build stderr from instance.errors/warnings/info for diagnostic output."""
    return format_diagnostics(
        getattr(instance, "errors", []),
        getattr(instance, "warnings", []),
        getattr(instance, "info", []),
    )


def _validator_source_hash(cls: type) -> str:
    """Fingerprint the concrete validator and the shared validator base."""
    try:
        source = "\n".join((inspect.getsource(cls), inspect.getsource(ValidatorBase)))
        return hashlib.sha1(source.encode("utf-8")).hexdigest()
    except OSError, TypeError:
        return "no-source"


def _cache_key(instance: Any, cls: type) -> str | None:
    """Calculate the file-plus-source cache key for a validator."""
    file_deps = instance.file_deps()
    if not file_deps:
        return None

    data_hash: str | None = None
    complete = False
    with contextlib.suppress(OSError):
        data_hash, complete = _compute_hash_status(instance.config_dir, file_deps)
    if not data_hash or not complete:
        return None

    # Fold validator source into the cache key so logic edits invalidate it.
    return f"{data_hash}:{_validator_source_hash(cls)}"


def _load_cached_result(
    instance: Any,
    name: str,
    file_hash: str | None,
    force: bool,
) -> _ValidatorExecutionResult | None:
    """Return a matching cached result, or ``None`` on a cache miss."""
    if force or file_hash is None:
        return None
    try:
        cached = load_cache(instance.config_dir, name)
        if cached and cached["hash"] == file_hash and cached["passed"] is True:
            return _ValidatorExecutionResult(
                passed=cached["passed"],
                stderr=cached.get("stderr", ""),
                duration=cached.get("duration", 0.0),
                cached=True,
            )
    except OSError, ValueError:
        pass
    return None


def _run_validator(instance: Any, start: float) -> _ValidatorExecutionResult:
    """Execute one validator and translate its expected failure boundaries."""
    try:
        passed = bool(instance.validate_all())
        stderr = _build_diagnostic_stderr(instance)
    except SystemExit as e:
        passed = e.code in (0, None)
        stderr = (
            _build_diagnostic_stderr(instance)
            or f"Validator raised SystemExit({e.code!r})"
        )
    except Exception as e:
        return _ValidatorExecutionResult(
            passed=False,
            stderr=f"Failed to run validator: {e}",
            duration=time.perf_counter() - start,
        )
    return _ValidatorExecutionResult(
        passed=passed,
        stderr=stderr,
        duration=time.perf_counter() - start,
    )


def _save_success_cache(
    instance: Any,
    name: str,
    description: str,
    file_hash: str | None,
    duration: float,
    stderr: str,
) -> None:
    """Best-effort save of a passing validator result."""
    if file_hash is None:
        return
    with contextlib.suppress(OSError, TypeError, ValueError):
        save_cache(
            instance.config_dir,
            name,
            description,
            file_hash,
            True,
            duration,
            stderr=stderr,
        )


def _run_one(
    cls: type,
    description: str,
    config_dir: str,
    quiet: bool,
    force: bool,
    summary: bool = False,
) -> ValidatorResult:
    """Instantiate and run a single validator; may return cached result.

    Validator ``main()``s raise SystemExit; we catch it here so that the
    parallel executor doesn't kill sibling validators. ``validate_all()``
    itself does not raise SystemExit — only ``main()`` does — so this is
    defensive for validators that have been refactored to a callable form.

    We do NOT use ``contextlib.redirect_stdout`` because it mutates
    ``sys.stdout`` globally and is not thread-safe.

    The file hash is computed once before validation and reused when
    saving the cache to avoid state-drift (e.g. HA writing .storage/
    while validation runs).
    """
    start = time.perf_counter()
    try:
        instance = cls(config_dir, quiet=quiet, summary=summary)
        name = cls.__name__

        # Compute hash once — reuse for both cache check and cache save.
        # Validators with no file dependencies are never cached.
        fhash = _cache_key(instance, cls)

        # --- cache check (skip when --force or when file_deps is empty) ---
        cached_result = _load_cached_result(instance, name, fhash, force)
        if cached_result is not None:
            return ValidatorResult(
                description=description,
                passed=cached_result.passed,
                stderr=cached_result.stderr,
                duration=cached_result.duration or 0.0,
                cached=cached_result.cached,
            )

        # --- cache miss or forced — actually run the validator ---
        execution = _run_validator(instance, start)
        duration = (
            execution.duration
            if execution.duration is not None
            else time.perf_counter() - start
        )

        # --- save to cache (only on success; failures always re-run) ---
        if execution.passed:
            _save_success_cache(
                instance,
                name,
                description,
                fhash,
                duration,
                execution.stderr,
            )

        return ValidatorResult(
            description=description,
            passed=execution.passed,
            stderr=execution.stderr,
            duration=duration,
            cached=execution.cached,
        )
    except Exception as e:
        return ValidatorResult(
            description=description,
            passed=False,
            stderr=f"Validator orchestration failed: {e}",
            duration=time.perf_counter() - start,
        )


def run_validators(
    config_dir: str, quiet: bool = False, force: bool = False, summary: bool = False
) -> list[ValidatorResult]:
    """Run all default-suite validators in parallel threads.

    Validators touch disjoint files, so contention is minimal.
    ``ReferenceValidator`` itself spawns an inner ThreadPoolExecutor for its
    five-file entity extraction — total active threads peak around 8-15.
    """
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = [
            executor.submit(_run_one, cls, desc, config_dir, quiet, force, summary)
            for cls, desc in _VALIDATORS
        ]
        return [f.result() for f in futures]


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    """Wire the ``validate`` subparser."""
    parser = subparsers.add_parser(
        "validate",
        help="Run all configuration validators in-process.",
        description="Run YAML, reference, and HA-official validators in parallel.",
    )
    add_config_dir_arg(parser, help="Path to the config directory (default: config)")
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-validator output on success. Errors still print.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-run all validators ignoring cached results (cache is refreshed).",
    )
    add_summary_args(parser)
    parser.set_defaults(func=run)


def _print_intro(force: bool, quiet: bool, summary: bool) -> None:
    """Print the banner and force-message to stderr (skipped in summary/quiet)."""
    if quiet or summary:
        return
    print(
        "\U0001f50d Running Home Assistant Configuration Validation Tests",
        file=sys.stderr,
    )
    print("=" * 60, file=sys.stderr)
    print(file=sys.stderr)
    if force:
        print(
            "Re-running all validators (cache ignored, will be refreshed)...",
            file=sys.stderr,
        )
    else:
        print("Running all validators in parallel...", file=sys.stderr)
    print(file=sys.stderr)


def _format_result_line(r: ValidatorResult, summary: bool, quiet: bool) -> str | None:
    """Format one validator PASS/FAIL line.

    Returns the formatted string, or ``None`` if quiet mode suppresses it.
    """
    if quiet and r.passed:
        return None
    if r.passed:
        if summary:
            if r.cached:
                return f"PASS {r.description} C"
            return f"PASS {r.description} ({r.duration:.2f}s)"
        suffix = " (cached)" if r.cached else f" ({r.duration:.2f}s)"
        return f"  \u2705 {r.description}: PASSED{suffix}"
    if summary:
        return f"FAIL {r.description} ({r.duration:.2f}s)"
    return f"  \u274c {r.description}: FAILED ({r.duration:.2f}s)"


def _print_failure_detail(results: list[ValidatorResult], summary: bool) -> None:
    """Print detailed output for failed validators (to stderr)."""
    for r in results:
        if r.passed:
            continue
        if summary:
            for line in r.stderr.strip().splitlines():
                if line:
                    print(f"  {line}", file=sys.stderr)
        else:
            print(f"\n\U0001f4cb {r.description}", file=sys.stderr)
            print("-" * 50, file=sys.stderr)
            print("Status: \u274c FAILED", file=sys.stderr)
            print(f"Duration: {r.duration:.2f}s", file=sys.stderr)
            if r.stderr.strip():
                print("\nErrors:", file=sys.stderr)
                for sline in r.stderr.strip().splitlines():
                    print(f"  {sline}", file=sys.stderr)
            print(file=sys.stderr)


def _print_summary_block(
    results: list[ValidatorResult],
    all_passed: bool,
    overall_duration: float,
    summary: bool,
    quiet: bool,
) -> None:
    """Print final summary stats (PASSED X/Y or TEST SUMMARY block)."""
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed
    if summary:
        if all_passed:
            print(f"PASSED {passed}/{total} ({overall_duration:.2f}s)")
        else:
            print(f"FAILED {passed}/{total} ({overall_duration:.2f}s)")
    elif not quiet:
        print(file=sys.stderr)
        print(
            f"Total execution time: {overall_duration:.2f}s (parallel)",
            file=sys.stderr,
        )
        print("=" * 60, file=sys.stderr)
        print("\n\U0001f4ca TEST SUMMARY", file=sys.stderr)
        print("=" * 30, file=sys.stderr)
        print(f"Total tests: {total}", file=sys.stderr)
        print(f"Passed: {passed}", file=sys.stderr)
        print(f"Failed: {failed}", file=sys.stderr)
        if failed == 0:
            print(
                "\n\U0001f389 All tests passed! Your Home Assistant"
                " configuration is valid.",
                file=sys.stderr,
            )
        else:
            print(
                f"\n\u26a0\ufe0f  {failed} test(s) failed."
                " Please review the errors above.",
                file=sys.stderr,
            )
        print(file=sys.stderr)


def run(args: argparse.Namespace) -> int:
    """Entry point for the ``validate`` subcommand. Returns exit code."""
    config_dir = args.config
    quiet = bool(getattr(args, "quiet", False))
    force = bool(getattr(args, "force", False))
    summary = resolve_summary(args)

    _print_intro(force, quiet, summary)

    overall_start = time.perf_counter()
    results = run_validators(config_dir, quiet=quiet, force=force, summary=summary)
    overall_duration = time.perf_counter() - overall_start

    all_passed = True
    for r in results:
        line = _format_result_line(r, summary, quiet)
        if line is not None:
            target = sys.stdout if summary else sys.stderr
            print(line, file=target)
        if r.passed and not summary and not quiet and r.stderr.strip():
            for sline in r.stderr.strip().splitlines():
                if sline:
                    print(f"      {sline}", file=sys.stderr)
        if not r.passed:
            all_passed = False

    if not all_passed:
        _print_failure_detail(results, summary)

    _print_summary_block(results, all_passed, overall_duration, summary, quiet)

    return 0 if all_passed else 1
