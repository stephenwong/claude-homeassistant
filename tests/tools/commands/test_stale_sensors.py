"""Tests for tools/commands/stale_sensors.py — stale-sensors subcommand wrapper."""

from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

from tests.helpers import make_parser, parse_command_args
from tools.commands import stale_sensors as stale_cmd


@pytest.fixture
def stale_parser():
    parser, subparsers = make_parser()
    stale_cmd.add_parser(subparsers)
    return parser


class TestAddParser:
    def test_subparser_registered(self):
        args = parse_command_args("stale-sensors", stale_cmd.add_parser, [])
        assert args.command == "stale-sensors"
        assert callable(args.func)

    def test_default_help_uses_shared_threshold(self, stale_parser, capsys):
        from tools.validators.stale_sensors import DEFAULT_THRESHOLD_HOURS

        with pytest.raises(SystemExit):
            stale_parser.parse_args(["stale-sensors", "--help"])
        assert f"default: {DEFAULT_THRESHOLD_HOURS}" in capsys.readouterr().out

    def test_accepts_all_flags(self):
        args = parse_command_args(
            "stale-sensors",
            stale_cmd.add_parser,
            [
                "--config",
                "my_config",
                "--threshold",
                "12",
                "--exclude-domains",
                "binary_sensor,light",
                "--exclude-platforms",
                "template, group",
                "--only-domains",
                "sensor",
                "--ignore-restored",
                "--fail-on-stale",
            ],
        )
        assert args.config == "my_config"
        assert args.threshold == 12
        assert args.exclude_domains == "binary_sensor,light"
        assert args.exclude_platforms == "template, group"
        assert args.only_domains == "sensor"
        assert args.ignore_restored is True
        assert args.fail_on_stale is True

    def test_negative_threshold_rejected(self):
        """A negative threshold is rejected at argparse time."""
        with pytest.raises(SystemExit):
            parse_command_args(
                "stale-sensors", stale_cmd.add_parser, ["--threshold", "-1"]
            )

    def test_zero_threshold_rejected(self):
        """A zero threshold is rejected at argparse time."""
        with pytest.raises(SystemExit):
            parse_command_args(
                "stale-sensors", stale_cmd.add_parser, ["--threshold", "0"]
            )

    def test_exclude_platforms_help_documents_override(self, stale_parser, capsys):
        """The platform help text documents that supplied values override defaults."""
        with pytest.raises(SystemExit):
            stale_parser.parse_args(["stale-sensors", "--help"])
        out = capsys.readouterr().out
        assert "OVERRIDES" in out


class TestRun:
    @patch("tools.commands.stale_sensors.StaleSensorValidator")
    def test_run_delegates_to_validator(self, mock_val_class):
        mock_val = MagicMock()
        mock_val.validate_all.return_value = True
        mock_val.warnings = []
        mock_val_class.return_value = mock_val

        args = Namespace(
            config="config_path",
            threshold=12,
            exclude_domains="light,switch",
            exclude_platforms="template,group",
            only_domains="sensor",
            ignore_restored=True,
            fail_on_stale=False,
        )

        exit_code = stale_cmd.run(args)
        assert exit_code == 0

        # Verify StaleSensorValidator instantiation
        mock_val_class.assert_called_once_with(
            config_dir="config_path",
            threshold_hours=12,
            only_domains={"sensor"},
            exclude_domains={"light", "switch"},
            exclude_platforms={"template", "group"},
            ignore_restored=True,
            fail_on_stale=False,
            summary=True,
        )
        mock_val.validate_all.assert_called_once()

    @patch("tools.commands.stale_sensors.StaleSensorValidator")
    def test_run_returns_validate_all_result(self, mock_val_class):
        """The command returns 1 exactly when validate_all() returns false."""
        mock_val = MagicMock()
        mock_val_class.return_value = mock_val

        mock_val.validate_all.return_value = True
        mock_val.warnings = []
        args = Namespace(
            config="config_path",
            threshold=24,
            exclude_domains=None,
            exclude_platforms=None,
            only_domains=None,
            ignore_restored=False,
            fail_on_stale=False,
            summary=True,
            no_summary=False,
        )
        assert stale_cmd.run(args) == 0

        mock_val.validate_all.return_value = False
        assert stale_cmd.run(args) == 1

    @patch("tools.commands.stale_sensors.StaleSensorValidator")
    def test_run_prints_validator_diagnostics(self, mock_val_class, capsys):
        mock_val = MagicMock()
        mock_val.validate_all.return_value = False
        mock_val.errors = ["stale sensor failure"]
        mock_val.warnings = ["sensor warning"]
        mock_val.info = ["sensor info"]
        mock_val_class.return_value = mock_val

        args = Namespace(
            config="config_path",
            threshold=24,
            exclude_domains=None,
            exclude_platforms=None,
            only_domains=None,
            ignore_restored=False,
            fail_on_stale=True,
            summary=True,
            no_summary=False,
        )
        assert stale_cmd.run(args) == 1
        err = capsys.readouterr().err
        assert "ERROR: stale sensor failure" in err
        assert "WARN: sensor warning" in err
        assert "INFO: sensor info" in err

    @patch("tools.commands.stale_sensors.StaleSensorValidator")
    def test_run_delegates_to_validator_explicit_summary(self, mock_val_class):
        """The command passes its resolved summary mode to the validator."""
        mock_val = MagicMock()
        mock_val.validate_all.return_value = True
        mock_val.warnings = []
        mock_val_class.return_value = mock_val

        args = Namespace(
            config="config_path",
            threshold=24,
            exclude_domains=None,
            exclude_platforms=None,
            only_domains="sensor",
            ignore_restored=False,
            fail_on_stale=False,
            summary=True,
            no_summary=False,
        )
        stale_cmd.run(args)
        mock_val_class.assert_called_once()
        assert mock_val_class.call_args.kwargs["summary"] is True

    @patch("tools.commands.stale_sensors.StaleSensorValidator")
    def test_exclude_domains_subtracts_from_default(self, mock_val_class):
        """Excluded domains are passed separately for validator subtraction."""
        mock_val = MagicMock()
        mock_val.validate_all.return_value = True
        mock_val.warnings = []
        mock_val_class.return_value = mock_val

        args = Namespace(
            config="config_path",
            threshold=24,
            exclude_domains="sensor",
            exclude_platforms=None,
            only_domains="sensor,light",
            ignore_restored=False,
            fail_on_stale=False,
        )
        stale_cmd.run(args)
        mock_val_class.assert_called_once()
        assert mock_val_class.call_args.kwargs["only_domains"] == {"sensor", "light"}
        assert mock_val_class.call_args.kwargs["exclude_domains"] == {"sensor"}

    @pytest.mark.parametrize(
        ("fail_on_stale", "validator_result", "expected_exit_code"),
        [(False, True, 0), (True, False, 1)],
        ids=["diagnostic-only", "failing-on-stale"],
    )
    @patch("tools.commands.stale_sensors.StaleSensorValidator")
    def test_fail_on_stale_is_forwarded_and_controls_exit_code(
        self, mock_val_class, fail_on_stale, validator_result, expected_exit_code
    ):
        mock_val = MagicMock()
        mock_val.validate_all.return_value = validator_result
        mock_val_class.return_value = mock_val

        args = Namespace(
            config="config_path",
            threshold=24,
            exclude_domains=None,
            exclude_platforms=None,
            only_domains=None,
            ignore_restored=False,
            fail_on_stale=fail_on_stale,
        )
        assert stale_cmd.run(args) == expected_exit_code
        assert mock_val_class.call_args.kwargs["fail_on_stale"] is fail_on_stale


class TestParseCsvArg:
    def test_returns_none_for_none(self):
        assert stale_cmd._parse_csv_arg(None) is None

    def test_returns_none_for_empty_string(self):
        assert stale_cmd._parse_csv_arg("") is None

    def test_returns_lowercased_set(self):
        assert stale_cmd._parse_csv_arg("Sensor,Binary_Sensor") == {
            "sensor",
            "binary_sensor",
        }

    def test_strips_whitespace(self):
        assert stale_cmd._parse_csv_arg("  a , b ,c") == {"a", "b", "c"}

    def test_preserves_explicit_empty_entries(self):
        assert stale_cmd._parse_csv_arg("a,,b,") == {"a", "b", ""}

    def test_explicit_empty_tokens_do_not_select_default(self):
        assert stale_cmd._parse_csv_arg(",,") == {""}
