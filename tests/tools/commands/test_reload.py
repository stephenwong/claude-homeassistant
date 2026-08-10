"""Tests for tools/commands/reload.py — reload subcommand wrapper."""

from argparse import Namespace
from unittest.mock import patch

from tests.helpers import make_parser, parse_command_args
from tools.commands import reload as reload_cmd


class TestAddParser:
    def test_subparser_registered(self):
        parser, subparsers = make_parser()
        reload_cmd.add_parser(subparsers)
        args = parser.parse_args(["reload"])
        assert args.command == "reload"
        assert callable(args.func)

    def test_summary_flag_registered(self):
        args = parse_command_args("reload", reload_cmd.add_parser, ["--summary"])
        assert args.summary is True

    def test_no_summary_flag_registered(self):
        args = parse_command_args("reload", reload_cmd.add_parser, ["--no-summary"])
        assert args.no_summary is True

    def test_summary_defaults_false(self):
        args = parse_command_args("reload", reload_cmd.add_parser, [])
        assert args.summary is False
        assert args.no_summary is False


class TestRun:
    def test_success_returns_zero(self):
        with patch("tools.commands.reload.reload_config", return_value=True):
            assert reload_cmd.run(Namespace()) == 0

    def test_failure_returns_one(self):
        with patch("tools.commands.reload.reload_config", return_value=False):
            assert reload_cmd.run(Namespace()) == 1

    def test_delegates_to_reload_config(self):
        """run() should call reload_config() and propagate its return value."""
        with patch(
            "tools.commands.reload.reload_config", return_value=True
        ) as mock_reload:
            reload_cmd.run(Namespace())
        mock_reload.assert_called_once()

    def test_summary_flag_treated_as_true(self):
        with patch(
            "tools.commands.reload.reload_config", return_value=True
        ) as mock_reload:
            reload_cmd.run(Namespace(summary=True, no_summary=False))
        assert mock_reload.call_args.kwargs.get("summary") is True

    def test_no_summary_flag_treated_as_false(self):
        with patch(
            "tools.commands.reload.reload_config", return_value=True
        ) as mock_reload:
            reload_cmd.run(Namespace(summary=False, no_summary=True))
        assert mock_reload.call_args.kwargs.get("summary") is False

    def test_default_uses_is_tty(self):
        with patch(
            "tools.commands.reload.reload_config", return_value=True
        ) as mock_reload:
            with patch("tools.common._is_tty", return_value=False):
                reload_cmd.run(Namespace(summary=False, no_summary=False))
            assert mock_reload.call_args.kwargs.get("summary") is True

        with patch(
            "tools.commands.reload.reload_config", return_value=True
        ) as mock_reload:
            with patch("tools.common._is_tty", return_value=True):
                reload_cmd.run(Namespace(summary=False, no_summary=False))
            assert mock_reload.call_args.kwargs.get("summary") is False

    def test_conflicting_flags_warning(self, capsys):
        with patch("tools.commands.reload.reload_config", return_value=True):
            reload_cmd.run(Namespace(summary=True, no_summary=True))
        _, err = capsys.readouterr()
        assert "WARN" in err
        assert "--summary" in err
