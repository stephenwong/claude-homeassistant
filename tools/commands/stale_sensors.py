"""``stale-sensors`` subcommand: run stale sensor detection directly."""

import argparse
import sys

from tools.common import (
    add_config_dir_arg,
    add_summary_args,
    positive_int,
    resolve_summary,
)
from tools.validators.base import format_diagnostics
from tools.validators.stale_sensors import (
    DEFAULT_ONLY_DOMAINS,
    DEFAULT_THRESHOLD_HOURS,
    StaleSensorValidator,
)


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    """Wire the ``stale-sensors`` subparser."""
    parser = subparsers.add_parser(
        "stale-sensors",
        help="Detect stale Home Assistant sensors.",
        description="Query active entities via HA REST API and find stale sensors.",
    )
    add_config_dir_arg(parser, help="Path to HA config directory (default: config)")
    parser.add_argument(
        "--threshold",
        "-t",
        type=positive_int,
        default=DEFAULT_THRESHOLD_HOURS,
        help=f"Staleness threshold in hours (default: {DEFAULT_THRESHOLD_HOURS})",
    )
    parser.add_argument(
        "--exclude-domains",
        help="Comma-separated domains to completely exclude (default: none)",
    )
    parser.add_argument(
        "--exclude-platforms",
        help="Comma-separated platforms to exclude "
        "(OVERRIDES the defaults; defaults: template,group,derivative,...)",
    )
    parser.add_argument(
        "--only-domains",
        default=",".join(sorted(DEFAULT_ONLY_DOMAINS)),
        help=(
            "Comma-separated domains to analyze "
            f"(default: {','.join(sorted(DEFAULT_ONLY_DOMAINS))})"
        ),
    )
    parser.add_argument(
        "--ignore-restored",
        action="store_true",
        help="Do not flag entities restored at startup",
    )
    parser.add_argument(
        "--fail-on-stale",
        action="store_true",
        help="Exit with non-zero status code if stale sensors are found",
    )
    add_summary_args(parser)
    parser.set_defaults(func=run)


def _parse_csv_arg(value: str | None) -> set[str] | None:
    """Parse a supplied comma-separated string, or return None when omitted."""
    if not value:
        return None
    return {item.strip().lower() for item in value.split(",")}


def run(args: argparse.Namespace) -> int:
    """Entry point for the ``stale-sensors`` subcommand. Returns exit code."""
    summary = resolve_summary(args)
    only_domains = _parse_csv_arg(args.only_domains)
    if only_domains is None:
        only_domains = set(DEFAULT_ONLY_DOMAINS)

    validator = StaleSensorValidator(
        config_dir=args.config,
        threshold_hours=args.threshold,
        only_domains=only_domains,
        exclude_domains=_parse_csv_arg(args.exclude_domains),
        exclude_platforms=_parse_csv_arg(args.exclude_platforms),
        ignore_restored=args.ignore_restored,
        fail_on_stale=args.fail_on_stale,
        summary=summary,
    )

    passed = validator.validate_all()
    if summary:
        status = "PASS" if passed else "FAIL"
        print(f"{status} {validator.validator_name}")
        diagnostics = format_diagnostics(
            validator.errors, validator.warnings, validator.info
        )
        if diagnostics:
            print(diagnostics, file=sys.stderr)
    else:
        validator.print_results()
    return 0 if passed else 1
