"""Tests for tools/commands/reload.py — reload subcommand wrapper."""

from argparse import Namespace
from unittest.mock import patch

import pytest

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
    @pytest.mark.parametrize(
        ("reload_result", "expected_exit_code"),
        [(True, 0), (False, 1)],
        ids=["success", "failure"],
    )
    def test_returns_reload_result_exit_code(self, reload_result, expected_exit_code):
        with patch(
            "tools.commands.reload.reload_config", return_value=reload_result
        ) as mock_reload:
            assert reload_cmd.run(Namespace()) == expected_exit_code
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
