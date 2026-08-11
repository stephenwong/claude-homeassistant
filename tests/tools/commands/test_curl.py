"""Tests for tools/commands/curl.py — pure-Python curl subcommand."""

import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from tests.helpers import parse_command_args
from tools.commands import curl as curl_cmd
from tools.commands.curl import (
    _CurlError,
    _CurlRequest,
    _emit_output,
    _execute_request,
    _validate_args,
)
from tools.common import HARequestError
from tools.ha.client import HAClient

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def make_args(**overrides):
    """Build args from curl's production parser, with test-specific overrides."""
    # Direct ``run`` tests must opt out of the non-TTY summary auto-detection;
    # the option itself still comes from curl's production parser.
    args = parse(["/api/states", "--no-summary"])
    for name, value in overrides.items():
        setattr(args, name, value)
    return args


def parse(argv):
    """Parse ``curl <argv>`` through the real argparse and return args."""
    return parse_command_args("curl", curl_cmd.add_parser, argv)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_client():
    """Patch HAClient.from_env to return a controlled mock client."""
    with patch("tools.commands.curl.HAClient.from_env") as mock_from_env:
        client = MagicMock(spec=HAClient)
        client.url = "http://ha:8123"
        client.token = "tok"
        client.timeout = 10
        client.headers = {
            "Authorization": "Bearer tok",
            "Content-Type": "application/json",
        }
        mock_from_env.return_value = client
        yield client


_MISSING = object()


def response_mock(
    *,
    status=200,
    ok=None,
    headers=None,
    content_type=None,
    text="",
    json_data=_MISSING,
):
    """Build a configurable response mock for curl tests."""
    r = MagicMock()
    r.ok = status < 400 if ok is None else ok
    r.status_code = status
    if headers is None:
        headers = {} if content_type is None else {"content-type": content_type}
    r.headers = headers
    r.text = text
    if json_data is not _MISSING:
        r.json.return_value = json_data
    return r


def json_resp(data, status=200):
    """Build a JSON response mock."""
    return response_mock(
        status=status,
        content_type="application/json",
        text=json.dumps(data),
        json_data=data,
    )


def text_resp(text="ok", ct="text/plain", status=200):
    """Build a non-JSON response mock."""
    return response_mock(status=status, content_type=ct, text=text)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


class TestArgparse:
    def test_subparser_registered(self):
        args = parse(["/api/states"])
        assert args.endpoint == "/api/states"
        assert args.method == "GET"
        assert callable(args.func)

    def test_default_method_is_get(self):
        args = parse(["/api/"])
        assert args.method == "GET"

    def test_post_flag_sets_method(self):
        args = parse(["-X", "/api/services/light/turn_on"])
        assert args.method == "POST"

    def test_method_flag(self):
        args = parse(["--method", "PUT", "/api/config"])
        assert args.method == "PUT"

    def test_method_invalid_value(self):
        with pytest.raises(SystemExit):
            parse(["--method", "POTS", "/api/"])

    def test_endpoint_no_slash_fails(self):
        with pytest.raises(SystemExit):
            parse(["api/states"])

    def test_count_flag(self):
        args = parse(["/api/states", "--count"])
        assert args.count is True

    def test_keys_flag(self):
        args = parse(["/api/states", "--keys"])
        assert args.keys is True

    def test_first_flag(self):
        args = parse(["/api/states", "--first", "5"])
        assert args.first == 5

    def test_first_zero_invalid(self):
        with pytest.raises(SystemExit):
            parse(["/api/states", "--first", "0"])

    def test_first_negative_invalid(self):
        with pytest.raises(SystemExit):
            parse(["/api/states", "--first", "-1"])

    def test_raw_flag(self):
        args = parse(["/api/", "--raw"])
        assert args.raw is True

    def test_pretty_flag(self):
        args = parse(["/api/states", "--pretty"])
        assert args.pretty is True

    def test_count_keys_conflict(self):
        with pytest.raises(SystemExit):
            parse(["/api/states", "--count", "--keys"])

    def test_post_method_conflict(self):
        with pytest.raises(SystemExit):
            parse(["/api/", "--post", "--method", "PUT"])

    def test_summary_flag_registered(self):
        args = parse(["/api/states", "--summary"])
        assert args.summary is True

    def test_no_summary_flag_registered(self):
        args = parse(["/api/states", "--no-summary"])
        assert args.no_summary is True

    def test_summary_defaults_false(self):
        args = parse(["/api/states"])
        assert args.summary is False
        assert args.no_summary is False

    def test_pick_flag_registered(self):
        args = parse(["/api/states", "--pick", "state,entity_id"])
        assert args.pick == "state,entity_id"

    def test_pick_defaults_none(self):
        args = parse(["/api/states"])
        assert args.pick is None

    def test_entity_flag_registered(self):
        args = parse(["/api/states", "--entity", "sensor.x"])
        assert args.entity == "sensor.x"

    def test_entity_defaults_none(self):
        args = parse(["/api/states"])
        assert args.entity is None

    def test_endpoint_optional_with_nargs(self):
        """endpoint positional should be optional (nargs='?')."""
        args = parse(["--entity", "sensor.x"])
        assert args.entity == "sensor.x"
        assert args.endpoint is None

    def test_domain_flag_registered(self):
        args = parse(["/api/states", "--domain", "light"])
        assert args.domain == "light"

    def test_domain_defaults_none(self):
        args = parse(["/api/states"])
        assert args.domain is None

    def test_max_chars_flag_registered(self):
        args = parse(["/api/states", "--max-chars", "500"])
        assert args.max_chars == 500

    def test_max_chars_defaults_none(self):
        args = parse(["/api/states"])
        assert args.max_chars is None

    def test_no_guard_flag_registered(self):
        args = parse(["/api/states", "--no-guard"])
        assert args.no_guard is True

    def test_no_guard_defaults_false(self):
        args = parse(["/api/states"])
        assert args.no_guard is False

    def test_no_guard_help_mentions_cap(self, capsys):
        """The --no-guard help text should mention it also disables max-chars."""
        with pytest.raises(SystemExit):
            _ = parse(["--help"])
        out, _ = capsys.readouterr()
        # Find the --no-guard help description (not the usage synopsis line).
        # The help description starts with two spaces, then `--no-guard`.
        for line in out.splitlines():
            stripped = line.strip()
            if stripped.startswith("--no-guard") and not stripped.startswith("usage"):
                assert "max-chars" in line.lower(), (
                    f"--no-guard help should mention max-chars: {line!r}"
                )
                return
        pytest.fail("--no-guard help description not found in output")


class TestExplicitOutputFlags:
    @pytest.mark.parametrize(
        "overrides,expected",
        [
            ({"first": None, "pick": None, "max_chars": None}, False),
            ({"first": 5, "pick": None, "max_chars": None}, True),
            ({"first": None, "pick": "a", "max_chars": None}, True),
            ({"first": None, "pick": None, "max_chars": 100}, True),
        ],
    )
    def test_flag_matrix(self, overrides, expected):
        args = make_args(**overrides)
        assert curl_cmd._has_explicit_output_flags(args) is expected


# ---------------------------------------------------------------------------
# Summary mode — info stderr suppression
# ---------------------------------------------------------------------------


class TestSummaryMode:
    """When ``--summary`` is active, informational stderr warnings are suppressed."""

    def test_summary_suppresses_data_get_warning(self, mock_client, capsys):
        mock_client.get.return_value = json_resp([])
        args = make_args(data='{"x":1}', summary=True, no_summary=False)
        assert curl_cmd.run(args) == 0
        err = capsys.readouterr().err
        assert "ignored for GET" not in err

    def test_summary_suppresses_data_delete_warning(self, mock_client, capsys):
        mock_client.delete.return_value = json_resp({"ok": True})
        args = make_args(
            method="DELETE",
            endpoint="/api/config/x",
            data='{"x":"y"}',
            summary=True,
            no_summary=False,
        )
        assert curl_cmd.run(args) == 0
        err = capsys.readouterr().err
        assert "ignored for DELETE" not in err

    def test_summary_suppresses_pretty_keys_warning(self, mock_client, capsys):
        mock_client.get.return_value = json_resp({"a": 1, "b": 2})
        args = make_args(keys=True, pretty=True, summary=True, no_summary=False)
        assert curl_cmd.run(args) == 0
        err = capsys.readouterr().err
        assert "no effect with --keys" not in err

    def test_summary_suppresses_first_overcount(self, mock_client, capsys):
        data = [{"id": i} for i in range(3)]
        mock_client.get.return_value = json_resp(data)
        args = make_args(first=999, summary=True, no_summary=False)
        assert curl_cmd.run(args) == 0
        err = capsys.readouterr().err
        assert "999" not in err
        assert "items available" not in err

    def test_summary_suppresses_first_dict_overcount(self, mock_client, capsys):
        data = {"a": 1, "b": 2, "c": 3}
        mock_client.get.return_value = json_resp(data)
        args = make_args(first=999, summary=True, no_summary=False)
        assert curl_cmd.run(args) == 0
        err = capsys.readouterr().err
        assert "999" not in err
        assert "keys available" not in err


# ---------------------------------------------------------------------------
# Basic HTTP methods
# ---------------------------------------------------------------------------


class TestHttpMethods:
    def test_get_compact_json(self, mock_client, capsys):
        data = [{"entity_id": "sensor.test", "state": "on"}]
        mock_client.get.return_value = json_resp(data)
        args = make_args()
        assert curl_cmd.run(args) == 0
        out = capsys.readouterr().out
        assert '"entity_id":"sensor.test"' in out
        assert "    " not in out  # no indentation = compact

    def test_get_pretty_json(self, mock_client, capsys):
        data = [{"entity_id": "sensor.test", "state": "on"}]
        mock_client.get.return_value = json_resp(data)
        args = make_args(pretty=True)
        assert curl_cmd.run(args) == 0
        out = capsys.readouterr().out
        assert '"entity_id": "sensor.test"' in out
        assert "    " in out  # has indentation

    def test_get_non_json_response(self, mock_client, capsys):
        mock_client.get.return_value = text_resp("<html>HA</html>", ct="text/html")
        args = make_args(endpoint="/api/")
        assert curl_cmd.run(args) == 0
        out = capsys.readouterr().out
        assert out == "<html>HA</html>"

    def test_empty_response_body(self, mock_client, capsys):
        mock_client.get.return_value = response_mock(status=204)
        args = make_args(endpoint="/api/")
        assert curl_cmd.run(args) == 0
        assert capsys.readouterr().out == ""

    def test_invalid_json_content_type_falls_back_to_raw_body(
        self, mock_client, capsys
    ):
        response = response_mock(
            content_type="application/json; charset=utf-8", text="not json"
        )
        response.json.side_effect = ValueError("invalid JSON")
        mock_client.get.return_value = response
        args = make_args(endpoint="/api/")
        assert curl_cmd.run(args) == 0
        assert capsys.readouterr().out == "not json"

    def test_valid_json_null_remains_available_to_json_only_flags(
        self, mock_client, capsys
    ):
        mock_client.get.return_value = json_resp(None)
        args = make_args(endpoint="/api/", first=1)
        assert curl_cmd.run(args) == 0
        assert json.loads(capsys.readouterr().out) == [None]

    @pytest.mark.parametrize(
        ("flag", "value"),
        [("first", 1), ("keys", True), ("pick", "state")],
    )
    def test_malformed_json_rejects_json_only_flags(
        self, mock_client, capsys, flag, value
    ):
        response = response_mock(content_type="application/json", text='{"broken":')
        response.json.side_effect = ValueError("invalid JSON")
        mock_client.get.return_value = response
        args = make_args(endpoint="/api/", **{flag: value})
        assert curl_cmd.run(args) == 1
        assert f"Cannot use --{flag}" in capsys.readouterr().err

    def test_json_looking_text_rejects_pick_after_parse_failure(
        self, mock_client, capsys
    ):
        response = response_mock(content_type="text/plain", text="{not-json}")
        response.json.side_effect = ValueError("invalid JSON")
        mock_client.get.return_value = response
        assert curl_cmd.run(make_args(endpoint="/api/", pick="state")) == 1
        assert "Cannot use --pick" in capsys.readouterr().err

    def test_raw_output_keeps_malformed_json_body(self, mock_client, capsys):
        response = response_mock(content_type="application/json", text="{broken")
        response.json.side_effect = ValueError("invalid JSON")
        mock_client.get.return_value = response
        assert curl_cmd.run(make_args(endpoint="/api/", raw=True)) == 0
        assert capsys.readouterr().out == "{broken"

    def test_unexpected_json_parser_failure_is_not_suppressed(self, mock_client):
        response = response_mock(content_type="application/json", text="body")
        response.json.side_effect = RuntimeError("parser bug")
        mock_client.get.return_value = response
        with pytest.raises(RuntimeError, match="parser bug"):
            curl_cmd.run(make_args(endpoint="/api/"))

    def test_json_with_charset_in_content_type(self, mock_client, capsys):
        mock_client.get.return_value = response_mock(
            content_type="application/json; charset=utf-8",
            text='{"key": "val"}',
            json_data={"key": "val"},
        )
        args = make_args()
        assert curl_cmd.run(args) == 0
        assert '"key":"val"' in capsys.readouterr().out

    def test_post_with_data(self, mock_client):
        mock_client.post.return_value = json_resp({"success": True})
        args = make_args(method="POST", data='{"entity_id": "light.kitchen"}')
        assert curl_cmd.run(args) == 0
        mock_client.post.assert_called_once()
        _args, _kwargs = mock_client.post.call_args
        assert _kwargs["json"] == {"entity_id": "light.kitchen"}

    @pytest.mark.parametrize("body, expected", [("42", 42), ("true", True)])
    def test_post_scalar_json_data(self, mock_client, body, expected):
        """Scalar JSON bodies must not be confused with an error sentinel."""
        mock_client.post.return_value = json_resp({"success": True})
        args = make_args(method="POST", data=body)

        assert curl_cmd.run(args) == 0

        mock_client.post.assert_called_once_with("/api/states", json=expected)

    def test_post_without_data(self, mock_client):
        mock_client.post.return_value = json_resp({"success": True})
        args = make_args(method="POST")
        assert curl_cmd.run(args) == 0
        mock_client.post.assert_called_once()

    def test_put_without_data(self, mock_client):
        mock_client.put.return_value = json_resp({"ok": True})
        args = make_args(method="PUT", endpoint="/api/config")
        assert curl_cmd.run(args) == 0
        mock_client.put.assert_called_once_with("/api/config", json=None)

    def test_method_put(self, mock_client):
        mock_client.put.return_value = json_resp({"ok": True})
        args = make_args(method="PUT", endpoint="/api/config", data='{"key": "val"}')
        assert curl_cmd.run(args) == 0
        mock_client.put.assert_called_once()
        _path, kwargs = mock_client.put.call_args
        assert kwargs["json"] == {"key": "val"}

    def test_method_delete(self, mock_client):
        mock_client.delete.return_value = json_resp({"ok": True})
        args = make_args(method="DELETE", endpoint="/api/config/section")
        assert curl_cmd.run(args) == 0
        mock_client.delete.assert_called_once_with("/api/config/section", json=None)

    def test_method_patch(self, mock_client):
        mock_client.patch.return_value = json_resp({"ok": True})
        args = make_args(method="PATCH", endpoint="/api/config", data='{"key": "val"}')
        assert curl_cmd.run(args) == 0
        mock_client.patch.assert_called_once()
        _path, kwargs = mock_client.patch.call_args
        assert kwargs["json"] == {"key": "val"}

    def test_patch_without_data(self, mock_client):
        mock_client.patch.return_value = json_resp({"ok": True})
        args = make_args(method="PATCH", endpoint="/api/config")
        assert curl_cmd.run(args) == 0
        mock_client.patch.assert_called_once_with("/api/config", json=None)


class TestClientCleanup:
    def _client_with_session(self):
        session = MagicMock()
        client = HAClient("http://ha:8123", "tok", session=session)
        return client, session

    def test_run_closes_client_after_success(self):
        client, session = self._client_with_session()
        session.get.return_value = json_resp({"ok": True})
        with patch("tools.commands.curl.HAClient.from_env", return_value=client):
            assert curl_cmd.run(make_args(endpoint="/api/")) == 0
        session.close.assert_called_once()

    def test_run_closes_client_after_request_failure(self):
        client, session = self._client_with_session()
        session.get.side_effect = requests.ConnectionError("connection lost")
        with patch("tools.commands.curl.HAClient.from_env", return_value=client):
            assert curl_cmd.run(make_args(endpoint="/api/")) == 1
        session.close.assert_called_once()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_http_404(self, mock_client, capsys):
        mock_client.get.return_value = text_resp("Not found", status=404)
        args = make_args()
        assert curl_cmd.run(args) == 1
        err = capsys.readouterr().err
        assert "HTTP 404" in err
        assert "Not found" in err

    def test_http_500(self, mock_client, capsys):
        mock_client.get.return_value = text_resp("Internal error", status=500)
        args = make_args()
        assert curl_cmd.run(args) == 1
        err = capsys.readouterr().err
        assert "HTTP 500" in err

    def test_connection_error(self, mock_client, capsys):
        mock_client.get.side_effect = HARequestError("GET /api/states failed: boom")
        args = make_args()
        assert curl_cmd.run(args) == 1
        err = capsys.readouterr().err
        assert "boom" in err

    def test_missing_token(self, capsys):
        with patch("tools.commands.curl.HAClient.from_env") as m:
            m.side_effect = HARequestError("HA_TOKEN not found")
            args = make_args()
            assert curl_cmd.run(args) == 1
        err = capsys.readouterr().err
        assert "HA_TOKEN" in err

    def test_data_invalid_json(self, capsys):
        with patch("tools.commands.curl.HAClient.from_env"):
            args = make_args(method="POST", data="bad{")
            assert curl_cmd.run(args) == 1
        err = capsys.readouterr().err
        assert "Invalid JSON" in err

    def test_data_absent_is_none(self, mock_client):
        """POST without --data does not crash (json=None is valid)."""
        mock_client.post.return_value = json_resp({"ok": True})
        args = make_args(method="POST")  # no data passed
        assert curl_cmd.run(args) == 0

    def test_data_with_get_warns(self, mock_client, capsys):
        mock_client.get.return_value = json_resp([])
        args = make_args(data='{"ignore": true}')
        assert curl_cmd.run(args) == 0
        err = capsys.readouterr().err
        assert "ignored for GET" in err

    def test_delete_with_data_warns(self, mock_client, capsys):
        mock_client.delete.return_value = json_resp({"ok": True})
        args = make_args(method="DELETE", endpoint="/api/config/x", data='{"x": "y"}')
        assert curl_cmd.run(args) == 0
        err = capsys.readouterr().err
        assert "ignored for DELETE" in err

    def test_non_json_first_errors(self, mock_client, capsys):
        mock_client.get.return_value = text_resp("<html>HA</html>", ct="text/html")
        args = make_args(first=3)
        assert curl_cmd.run(args) == 1
        err = capsys.readouterr().err
        assert "Cannot use --first on non-JSON" in err

    def test_non_json_keys_errors(self, mock_client, capsys):
        mock_client.get.return_value = text_resp("<html>HA</html>", ct="text/html")
        args = make_args(keys=True)
        assert curl_cmd.run(args) == 1
        err = capsys.readouterr().err
        assert "Cannot use --keys on non-JSON" in err

    def test_raw_pretty_conflict(self, capsys):
        with patch("tools.commands.curl.HAClient.from_env"):
            args = make_args(raw=True, pretty=True)
            assert curl_cmd.run(args) == 1
        err = capsys.readouterr().err
        assert "Cannot combine --raw with --pretty" in err


# ---------------------------------------------------------------------------
# Token efficiency: --count
# ---------------------------------------------------------------------------


class TestCount:
    def test_count_list(self, mock_client, capsys):
        mock_client.get.return_value = json_resp([{}, {"a": 1}, {"b": 2}])
        args = make_args(count=True)
        assert curl_cmd.run(args) == 0
        assert capsys.readouterr().out.strip() == "3"

    def test_count_dict(self, mock_client, capsys):
        mock_client.get.return_value = json_resp({"a": 1, "b": 2, "c": 3})
        args = make_args(count=True)
        assert curl_cmd.run(args) == 0
        assert capsys.readouterr().out.strip() == "3"

    def test_count_scalar_null(self, mock_client, capsys):
        mock_client.get.return_value = json_resp(None)
        args = make_args(count=True)
        assert curl_cmd.run(args) == 0
        assert capsys.readouterr().out.strip() == "0"

    def test_count_scalar_number(self, mock_client, capsys):
        mock_client.get.return_value = json_resp(42)
        args = make_args(count=True)
        assert curl_cmd.run(args) == 0
        assert capsys.readouterr().out.strip() == "0"

    def test_count_scalar_boolean(self, mock_client, capsys):
        mock_client.get.return_value = json_resp(True)
        args = make_args(count=True)
        assert curl_cmd.run(args) == 0
        assert capsys.readouterr().out.strip() == "0"

    def test_count_non_json(self, mock_client, capsys):
        mock_client.get.return_value = text_resp("hello world", ct="text/plain")
        args = make_args(count=True, endpoint="/api/")
        assert curl_cmd.run(args) == 0
        assert capsys.readouterr().out.strip() == "0"  # non-JSON returns 0

    def test_count_empty_non_json(self, mock_client, capsys):
        mock_client.get.return_value = response_mock(status=204)
        args = make_args(count=True, endpoint="/api/")
        assert curl_cmd.run(args) == 0
        assert capsys.readouterr().out.strip() == "0"

    def test_keys_with_pretty_warns(self, mock_client, capsys):
        mock_client.get.return_value = json_resp({"a": 1, "b": 2})
        args = make_args(keys=True, pretty=True)
        assert curl_cmd.run(args) == 0
        err = capsys.readouterr().err
        assert "no effect with --keys" in err


# ---------------------------------------------------------------------------
# Token efficiency: --keys
# ---------------------------------------------------------------------------


class TestKeys:
    def test_key_collection_normalizes_list_and_dict_shapes(self):
        assert curl_cmd._collect_key_names([{"b": 1}, {"a": 2}]) == (
            ["a", "b"],
            "list",
        )
        assert curl_cmd._collect_key_names({"b": 1, "a": 2}) == (
            ["b", "a"],
            "dict",
        )

    def test_keys_list_of_dicts(self, mock_client, capsys):
        mock_client.get.return_value = json_resp(
            [
                {"id": 1, "name": "a"},
                {"id": 2, "name": "b", "extra": True},
            ]
        )
        args = make_args(keys=True)
        assert curl_cmd.run(args) == 0
        out, err = capsys.readouterr()
        assert "2 items" in err
        assert "extra" in out and "id" in out and "name" in out
        parsed = json.loads(out)
        assert sorted(parsed) == ["extra", "id", "name"]

    def test_keys_dict(self, mock_client, capsys):
        mock_client.get.return_value = json_resp({"name": "test", "state": "on"})
        args = make_args(keys=True)
        assert curl_cmd.run(args) == 0
        out, err = capsys.readouterr()
        assert "2 keys" in err
        parsed = json.loads(out)
        assert sorted(parsed) == ["name", "state"]

    def test_keys_empty_list(self, mock_client, capsys):
        mock_client.get.return_value = json_resp([])
        args = make_args(keys=True)
        assert curl_cmd.run(args) == 0
        out, err = capsys.readouterr()
        assert "empty list" in err
        assert json.loads(out) == []

    def test_keys_non_dict_items(self, mock_client, capsys):
        mock_client.get.return_value = json_resp(["a", "b", "c"])
        args = make_args(keys=True)
        assert curl_cmd.run(args) == 0
        out, err = capsys.readouterr()
        assert "non-dict" in err
        assert json.loads(out) == []

    def test_keys_mixed_list_first_non_dict(self, mock_client, capsys):
        """First item is not a dict, but later items are — keys should still emit."""
        mock_client.get.return_value = json_resp(["skip", {"a": 1}, {"b": 2}])
        args = make_args(keys=True)
        assert curl_cmd.run(args) == 0
        out, err = capsys.readouterr()
        assert "2 unique keys" in err
        parsed = json.loads(out)
        assert sorted(parsed) == ["a", "b"]

    def test_keys_on_scalar(self, mock_client, capsys):
        mock_client.get.return_value = json_resp(42)
        args = make_args(keys=True)
        assert curl_cmd.run(args) == 0
        out, err = capsys.readouterr()
        assert "not a JSON object or list" in err
        assert out.strip() == "42"

    def test_keys_summary_drops_inline_list(self, mock_client, capsys):
        mock_client.get.return_value = json_resp(
            [{"id": 1, "name": "a"}, {"id": 2, "name": "b", "extra": True}]
        )
        args = make_args(keys=True, summary=True, no_summary=False)
        assert curl_cmd.run(args) == 0
        out, err = capsys.readouterr()
        assert "2 items" in err
        assert "extra" not in err  # inline list suppressed in summary
        assert "id" not in err
        parsed = json.loads(out)
        assert sorted(parsed) == ["extra", "id", "name"]


# ---------------------------------------------------------------------------
# Token efficiency: --first
# ---------------------------------------------------------------------------


class TestFirst:
    def test_first_n(self, mock_client, capsys):
        data = [{"id": i} for i in range(10)]
        mock_client.get.return_value = json_resp(data)
        args = make_args(first=3)
        assert curl_cmd.run(args) == 0
        parsed = json.loads(capsys.readouterr().out)
        assert len(parsed) == 3
        assert parsed == [{"id": 0}, {"id": 1}, {"id": 2}]

    def test_first_with_pretty(self, mock_client, capsys):
        data = [{"id": i} for i in range(3)]
        mock_client.get.return_value = json_resp(data)
        args = make_args(first=3, pretty=True)
        assert curl_cmd.run(args) == 0
        out = capsys.readouterr().out
        assert "    " in out
        assert '"id": 0' in out

    def test_first_on_dict(self, mock_client, capsys):
        data = {"a": 1, "b": 2, "c": 3, "d": 4, "e": 5}
        mock_client.get.return_value = json_resp(data)
        args = make_args(first=3)
        assert curl_cmd.run(args) == 0
        parsed = json.loads(capsys.readouterr().out)
        assert len(parsed) == 3

    def test_first_overcount(self, mock_client, capsys):
        data = [{"id": i} for i in range(3)]
        mock_client.get.return_value = json_resp(data)
        args = make_args(first=999)
        assert curl_cmd.run(args) == 0
        out, err = capsys.readouterr()
        parsed = json.loads(out)
        assert len(parsed) == 3
        assert "999" in err and "3" in err

    def test_first_dict_overcount(self, mock_client, capsys):
        data = {"a": 1, "b": 2, "c": 3}
        mock_client.get.return_value = json_resp(data)
        args = make_args(first=999)
        assert curl_cmd.run(args) == 0
        out, err = capsys.readouterr()
        assert "999" in err and "3" in err
        assert len(json.loads(out)) == 3

    def test_first_on_scalar(self, mock_client, capsys):
        mock_client.get.return_value = json_resp(42)
        args = make_args(first=3)
        assert curl_cmd.run(args) == 0
        assert json.loads(capsys.readouterr().out) == [42]


# ---------------------------------------------------------------------------
# --pick (field projection)
# ---------------------------------------------------------------------------


class TestPick:
    """Tests for --pick <field1,field2> — keep only specified keys."""

    def test_pick_list_of_dicts(self, mock_client, capsys):
        data = [
            {"entity_id": "sensor.a", "state": "on", "attributes": {"x": 1}},
            {"entity_id": "sensor.b", "state": "off", "attributes": {"x": 0}},
        ]
        mock_client.get.return_value = json_resp(data)
        args = make_args(pick="entity_id,state")
        assert curl_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        assert result == [
            {"entity_id": "sensor.a", "state": "on"},
            {"entity_id": "sensor.b", "state": "off"},
        ]

    def test_pick_missing_keys_omitted(self, mock_client, capsys):
        data = [{"entity_id": "sensor.a", "state": "on", "extra": "x"}]
        mock_client.get.return_value = json_resp(data)
        args = make_args(pick="entity_id,nonexistent")
        assert curl_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        assert result == [{"entity_id": "sensor.a"}]

    def test_pick_single_dict(self, mock_client, capsys):
        data = {"entity_id": "sensor.a", "state": "on", "attributes": {"x": 1}}
        mock_client.get.return_value = json_resp(data)
        args = make_args(pick="state")
        assert curl_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        assert result == {"state": "on"}

    def test_pick_non_dict_items_pass_through(self, mock_client, capsys):
        data = [42, {"entity_id": "sensor.a", "state": "on"}]
        mock_client.get.return_value = json_resp(data)
        args = make_args(pick="entity_id")
        assert curl_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        assert result == [42, {"entity_id": "sensor.a"}]

    def test_pick_with_first_stacks_slice_then_pick(self, mock_client, capsys):
        data = [
            {"entity_id": "sensor.a", "state": "on"},
            {"entity_id": "sensor.b", "state": "off"},
            {"entity_id": "sensor.c", "state": "unknown"},
        ]
        mock_client.get.return_value = json_resp(data)
        args = make_args(first=2, pick="state")
        assert curl_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        assert result == [{"state": "on"}, {"state": "off"}]

    @pytest.mark.parametrize(
        ("flag", "value"),
        [("count", True), ("keys", True), ("raw", True)],
    )
    def test_pick_conflicts_with_flags(self, capsys, flag, value):
        with patch("tools.commands.curl.HAClient.from_env"):
            args = make_args(pick="state", **{flag: value})
            assert curl_cmd.run(args) == 1

    def test_pick_on_non_json_errors(self, mock_client, capsys):
        mock_client.get.return_value = text_resp("<html>", ct="text/html")
        args = make_args(pick="state")
        assert curl_cmd.run(args) == 1
        err = capsys.readouterr().err
        assert "Cannot use --pick on non-JSON" in err

    def test_pick_with_first_on_dict(self, mock_client, capsys):
        """--pick on a top-level dict picks from the dict's own keys."""
        data = {"a": 1, "b": 2, "c": {"x": 6}}
        mock_client.get.return_value = json_resp(data)
        args = make_args(first=2, pick="b")
        assert curl_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        assert result == {"b": 2}

    def test_pick_with_pretty(self, mock_client, capsys):
        data = [{"entity_id": "sensor.a", "state": "on", "extra": "x"}]
        mock_client.get.return_value = json_resp(data)
        args = make_args(pick="entity_id", pretty=True)
        assert curl_cmd.run(args) == 0
        out = capsys.readouterr().out
        # Pretty output has indentation
        assert '"entity_id"' in out
        assert "    " in out

    def test_pick_on_scalar_passes_through(self, mock_client, capsys):
        mock_client.get.return_value = json_resp(42)
        args = make_args(pick="state")
        assert curl_cmd.run(args) == 0
        assert json.loads(capsys.readouterr().out) == 42

    def test_pick_empty_string_passes_through(self, mock_client, capsys):
        data = [{"entity_id": "sensor.a", "state": "on"}]
        mock_client.get.return_value = json_resp(data)
        args = make_args(pick="")
        assert curl_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        assert result == data

    def test_pick_whitespace_around_fields(self, mock_client, capsys):
        data = [{"entity_id": "sensor.a", "state": "on"}]
        mock_client.get.return_value = json_resp(data)
        args = make_args(pick=" entity_id , state ")
        assert curl_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        assert result == [{"entity_id": "sensor.a", "state": "on"}]


# ---------------------------------------------------------------------------
# --entity (server-side single fetch)
# ---------------------------------------------------------------------------


class TestEntity:
    """Tests for --entity <entity_id> — single-entity fetch."""

    def test_entity_fetch_single_dict(self, mock_client, capsys):
        data = {"entity_id": "sensor.test", "state": "on"}
        mock_client.get.return_value = json_resp(data)
        args = make_args(entity="sensor.test")
        assert curl_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        assert result == data
        # Verify the correct endpoint was called
        mock_client.get.assert_called_once_with("/api/states/sensor.test")

    def test_entity_no_endpoint_required(self, mock_client, capsys):
        """--entity should work without specifying an endpoint."""
        data = {"entity_id": "light.kitchen", "state": "on"}
        mock_client.get.return_value = json_resp(data)
        args = make_args(endpoint=None, entity="light.kitchen")
        assert curl_cmd.run(args) == 0
        mock_client.get.assert_called_once_with("/api/states/light.kitchen")

    def test_entity_with_endpoint_not_api_states_errors(self, mock_client, capsys):
        args = make_args(endpoint="/api/history/period", entity="sensor.x")
        assert curl_cmd.run(args) == 1
        err = capsys.readouterr().err
        assert "entity" in err.lower()

    def test_entity_invalid_id_errors(self, capsys):
        with patch("tools.commands.curl.HAClient.from_env"):
            args = make_args(entity="not-valid")
            assert curl_cmd.run(args) == 1
        err = capsys.readouterr().err
        assert "invalid" in err.lower()

    @pytest.mark.parametrize(
        ("flag", "value"),
        [("count", True), ("keys", True), ("raw", True)],
    )
    def test_entity_conflicts_with_flags(self, capsys, flag, value):
        with patch("tools.commands.curl.HAClient.from_env"):
            args = make_args(entity="sensor.x", **{flag: value})
            assert curl_cmd.run(args) == 1

    def test_entity_with_pick(self, mock_client, capsys):
        data = {"entity_id": "sensor.test", "state": "on", "attributes": {}}
        mock_client.get.return_value = json_resp(data)
        args = make_args(entity="sensor.test", pick="entity_id,state")
        assert curl_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        assert result == {"entity_id": "sensor.test", "state": "on"}

    def test_entity_not_found_errors(self, mock_client, capsys):
        mock_client.get.return_value = json_resp(None, status=404)
        args = make_args(entity="sensor.nonexistent")
        assert curl_cmd.run(args) == 1
        err = capsys.readouterr().err
        assert "404" in err

    def test_entity_with_pretty(self, mock_client, capsys):
        data = {"entity_id": "sensor.test", "state": "on"}
        mock_client.get.return_value = json_resp(data)
        args = make_args(entity="sensor.test", pretty=True)
        assert curl_cmd.run(args) == 0
        out = capsys.readouterr().out
        assert '  "' in out  # 2-space indentation
        assert '"entity_id"' in out

    def test_entity_with_explicit_api_states(self, mock_client, capsys):
        data = {"entity_id": "sensor.test", "state": "on"}
        mock_client.get.return_value = json_resp(data)
        args = make_args(endpoint="/api/states", entity="sensor.test")
        assert curl_cmd.run(args) == 0
        mock_client.get.assert_called_once_with("/api/states/sensor.test")

    def test_entity_uppercase_id_rejected(self, capsys):
        with patch("tools.commands.curl.HAClient.from_env"):
            args = make_args(entity="Sensor.X")
            assert curl_cmd.run(args) == 1
        err = capsys.readouterr().err
        assert "invalid" in err.lower()

    def test_entity_double_dot_rejected(self, capsys):
        with patch("tools.commands.curl.HAClient.from_env"):
            args = make_args(entity="sensor.x.y")
            assert curl_cmd.run(args) == 1
        err = capsys.readouterr().err
        assert "invalid" in err.lower()


# ---------------------------------------------------------------------------
# --domain (client-side list filter)
# ---------------------------------------------------------------------------


class TestDomain:
    """Tests for --domain <name> — filter list items by entity_id prefix."""

    def test_domain_filters_by_prefix(self, mock_client, capsys):
        data = [
            {"entity_id": "light.kitchen", "state": "on"},
            {"entity_id": "sensor.temp", "state": "25"},
            {"entity_id": "light.bedroom", "state": "off"},
        ]
        mock_client.get.return_value = json_resp(data)
        args = make_args(domain="light")
        assert curl_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        assert len(result) == 2
        assert all(i["entity_id"].startswith("light.") for i in result)

    def test_domain_empty_domain(self, mock_client, capsys):
        data = [{"entity_id": "sensor.temp", "state": "25"}]
        mock_client.get.return_value = json_resp(data)
        args = make_args(domain="nonexistent")
        assert curl_cmd.run(args) == 0
        assert json.loads(capsys.readouterr().out) == []

    def test_domain_with_pick(self, mock_client, capsys):
        data = [
            {"entity_id": "light.kitchen", "state": "on", "brightness": 100},
            {"entity_id": "sensor.temp", "state": "25"},
        ]
        mock_client.get.return_value = json_resp(data)
        args = make_args(domain="light", pick="entity_id,state")
        assert curl_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        assert result == [{"entity_id": "light.kitchen", "state": "on"}]

    def test_domain_with_first(self, mock_client, capsys):
        data = [
            {"entity_id": "light.a", "state": "on"},
            {"entity_id": "light.b", "state": "off"},
            {"entity_id": "light.c", "state": "unknown"},
        ]
        mock_client.get.return_value = json_resp(data)
        args = make_args(domain="light", first=2)
        assert curl_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        assert len(result) == 2  # first 2 of the filtered set

    @pytest.mark.parametrize(
        ("flag", "value"),
        [
            ("entity", "sensor.x"),
            ("count", True),
            ("keys", True),
            ("raw", True),
        ],
    )
    def test_domain_conflicts_with_flags(self, capsys, flag, value):
        with patch("tools.commands.curl.HAClient.from_env"):
            args = make_args(domain="light", **{flag: value})
            assert curl_cmd.run(args) == 1

    def test_domain_works_on_any_endpoint(self, mock_client, capsys):
        """--domain should filter any list response with entity_id."""
        data = [
            {"entity_id": "light.kitchen", "state": "on"},
            {"entity_id": "sensor.temp", "state": "25"},
        ]
        mock_client.get.return_value = json_resp(data)
        args = make_args(endpoint="/api/history/period", domain="light")
        assert curl_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        assert len(result) == 1
        assert result[0]["entity_id"] == "light.kitchen"

    def test_domain_handles_null_entity_id(self, mock_client, capsys):
        """List items with entity_id: null should not crash."""
        data = [
            {"entity_id": None, "state": "orphan"},
            {"entity_id": "light.kitchen", "state": "on"},
        ]
        mock_client.get.return_value = json_resp(data)
        args = make_args(domain="light")
        assert curl_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        assert len(result) == 1
        assert result[0]["entity_id"] == "light.kitchen"

    def test_domain_non_list_passes_through(self, mock_client, capsys):
        mock_client.get.return_value = json_resp({"status": "ok"})
        args = make_args(domain="light")
        assert curl_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        assert result == {"status": "ok"}


# ---------------------------------------------------------------------------
# --max-chars (compact-JSON character-length truncation)
# ---------------------------------------------------------------------------


class TestMaxChars:
    """Tests for --max-chars <N> using compact-JSON character length."""

    def test_truncates_list_when_exceeds_limit(self, mock_client, capsys):
        data = [{"id": i, "data": "x" * 50} for i in range(20)]
        mock_client.get.return_value = json_resp(data)
        args = make_args(max_chars=200)
        assert curl_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        assert isinstance(result, list)
        assert result[-1].get("_truncated") is True
        assert result[-1]["total"] == 20

    def test_zero_disables_truncation(self, mock_client, capsys):
        data = [{"id": i} for i in range(5)]
        mock_client.get.return_value = json_resp(data)
        args = make_args(max_chars=0)
        assert curl_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        assert len(result) == 5
        assert isinstance(result, list)

    def test_small_list_fits_untouched(self, mock_client, capsys):
        data = [{"id": 1}]
        mock_client.get.return_value = json_resp(data)
        args = make_args(max_chars=500)
        assert curl_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        assert result == [{"id": 1}]

    def test_exempt_with_count(self, mock_client, capsys):
        mock_client.get.return_value = json_resp([{"id": 1}, {"id": 2}])
        args = make_args(max_chars=10, count=True)
        assert curl_cmd.run(args) == 0
        assert capsys.readouterr().out.strip() == "2"

    def test_exempt_with_keys(self, mock_client, capsys):
        mock_client.get.return_value = json_resp([{"id": 1}])
        args = make_args(max_chars=10, keys=True)
        assert curl_cmd.run(args) == 0
        # keys output: ["id"]
        assert json.loads(capsys.readouterr().out) == ["id"]

    def test_exempt_with_raw(self, mock_client, capsys):
        mock_client.get.return_value = text_resp("hello world")
        args = make_args(max_chars=5, raw=True)
        assert curl_cmd.run(args) == 0
        assert capsys.readouterr().out == "hello world"

    def test_output_remains_parseable_after_truncation(self, mock_client, capsys):
        data = [{"data": "x" * 100} for _ in range(10)]
        mock_client.get.return_value = json_resp(data)
        args = make_args(max_chars=300)
        assert curl_cmd.run(args) == 0
        out = capsys.readouterr().out
        # Must be valid JSON
        result = json.loads(out)
        assert isinstance(result, list)
        assert result[-1].get("_truncated") is True

    def test_truncation_with_first(self, mock_client, capsys):
        """--first slices first, then --max-chars evaluates the slice."""
        data = [{"data": "x" * 100} for _ in range(20)]
        mock_client.get.return_value = json_resp(data)
        args = make_args(first=3, max_chars=50)
        assert curl_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        assert isinstance(result, list)
        # Should truncate further (3 items of ~110 bytes each >> 50 chars)
        assert result[-1].get("_truncated") is True
        assert result[-1]["shown"] < 3

    def test_keys_respects_max_chars(self, mock_client, capsys):
        """--keys with --max-chars truncates the printed key list, not unbounded."""
        data = [{f"k{i}": i for i in range(500)}]
        mock_client.get.return_value = json_resp(data)
        args = make_args(endpoint="/api/states", keys=True, max_chars=120)
        assert curl_cmd.run(args) == 0
        out = capsys.readouterr().out
        assert len(out) <= 200


class TestDefaultCap:
    def _big(self, n=200):
        return [{"id": i, "blob": "x" * 200} for i in range(n)]

    def test_summary_caps_large_output(self, mock_client, capsys):
        mock_client.get.return_value = json_resp(self._big())
        args = make_args(endpoint="/api/history/period", summary=True, no_summary=False)
        assert curl_cmd.run(args) == 0
        out = capsys.readouterr().out
        assert len(out) <= 8000
        assert json.loads(out)[-1].get("_truncated") is True

    def test_verbose_no_cap(self, mock_client, capsys):
        mock_client.get.return_value = json_resp(self._big())
        args = make_args(endpoint="/api/history/period", no_summary=True, summary=False)
        assert curl_cmd.run(args) == 0
        assert len(capsys.readouterr().out) > 20000

    def test_env_override(self, mock_client, capsys, monkeypatch):
        monkeypatch.setenv("HA_CLI_MAX_CHARS", "1500")
        mock_client.get.return_value = json_resp(self._big(50))
        args = make_args(endpoint="/api/history/period", summary=True, no_summary=False)
        assert curl_cmd.run(args) == 0
        assert len(capsys.readouterr().out) <= 1500

    def test_explicit_zero_disables(self, mock_client, capsys):
        mock_client.get.return_value = json_resp(self._big())
        args = make_args(
            endpoint="/api/history/period", max_chars=0, summary=True, no_summary=False
        )
        assert curl_cmd.run(args) == 0
        assert len(capsys.readouterr().out) > 20000

    def test_no_guard_disables_default_cap(self, mock_client, capsys):
        mock_client.get.return_value = json_resp(self._big())
        args = make_args(
            endpoint="/api/states", no_guard=True, summary=True, no_summary=False
        )
        assert curl_cmd.run(args) == 0
        assert len(capsys.readouterr().out) > 20000


# ---------------------------------------------------------------------------
# Bare endpoint guardrail
# ---------------------------------------------------------------------------


class TestGuardrail:
    """Tests for bare ``/api/states`` guardrail — count + hint vs dump all."""

    def test_bare_api_states_in_summary_shows_count(self, mock_client, capsys):
        data = [{"entity_id": f"sensor.{i}"} for i in range(10)]
        mock_client.get.return_value = json_resp(data)
        args = make_args(endpoint="/api/states", summary=True, no_summary=False)
        assert curl_cmd.run(args) == 0
        out, err = capsys.readouterr()
        assert out.strip() == "10"
        assert "use --first" in err

    def test_bare_api_states_rejects_malformed_json(self, mock_client, capsys):
        response = response_mock(content_type="application/json", text="{broken")
        response.json.side_effect = ValueError("invalid JSON")
        mock_client.get.return_value = response
        args = make_args(endpoint="/api/states", summary=True, no_summary=False)

        assert curl_cmd.run(args) == 1
        assert "not valid JSON" in capsys.readouterr().err

    def test_no_guard_flag_disables_guardrail(self, mock_client, capsys):
        data = [{"entity_id": f"sensor.{i}"} for i in range(3)]
        mock_client.get.return_value = json_resp(data)
        args = make_args(
            endpoint="/api/states", no_guard=True, summary=True, no_summary=False
        )
        assert curl_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        assert len(result) == 3

    def test_no_summary_disables_guardrail(self, mock_client, capsys):
        """In verbose mode (no_summary) the guardrail does not fire."""
        data = [{"entity_id": f"sensor.{i}"} for i in range(3)]
        mock_client.get.return_value = json_resp(data)
        args = make_args(endpoint="/api/states", no_summary=True, summary=False)
        assert curl_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        assert len(result) == 3

    def test_transform_flag_disables_guardrail(self, mock_client, capsys):
        data = [{"entity_id": f"sensor.{i}"} for i in range(5)]
        mock_client.get.return_value = json_resp(data)
        args = make_args(
            endpoint="/api/states", first=3, summary=True, no_summary=False
        )
        assert curl_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        assert len(result) == 3

    def test_pretty_disables_guardrail(self, mock_client, capsys):
        """--pretty counts as an explicit output request."""
        data = [{"entity_id": "sensor.test", "state": "on"}]
        mock_client.get.return_value = json_resp(data)
        args = make_args(
            endpoint="/api/states", pretty=True, summary=True, no_summary=False
        )
        assert curl_cmd.run(args) == 0
        out, err = capsys.readouterr()
        assert "use --first" not in err  # no guardrail hint
        assert '"entity_id"' in out
        assert '  "' in out  # pretty indentation

    def test_post_method_no_guardrail(self, mock_client, capsys):
        """POST requests should not trigger the guardrail."""
        mock_client.post.return_value = json_resp({"ok": True})
        args = make_args(endpoint="/api/states", method="POST", data='{"x": "y"}')
        assert curl_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        assert result == {"ok": True}

    def test_sub_path_no_guardrail(self, mock_client, capsys):
        """Sub-paths like /api/states/sensor.x should not be guarded."""
        data = {"entity_id": "sensor.test", "state": "on"}
        mock_client.get.return_value = json_resp(data)
        args = make_args(endpoint="/api/states/sensor.test")
        assert curl_cmd.run(args) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["entity_id"] == "sensor.test"

    def test_unknown_method_returns_one(self, mock_client, capsys):
        """Calling run() with an unrecognized method should error."""
        args = make_args(endpoint="/api/")
        args.method = "UNKNOWN"
        assert curl_cmd.run(args) == 1
        err = capsys.readouterr().err
        assert "Unknown HTTP method" in err


# ---------------------------------------------------------------------------
# --raw
# ---------------------------------------------------------------------------


class TestRaw:
    def test_raw_output(self, mock_client, capsys):
        mock_client.get.return_value = text_resp("raw body", ct="text/plain")
        args = make_args(raw=True)
        assert curl_cmd.run(args) == 0
        assert capsys.readouterr().out == "raw body"


# ---------------------------------------------------------------------------
# TQ2: --max-chars interaction tests
# ---------------------------------------------------------------------------


class TestMaxCharsInteractions:
    """--max-chars combined with other flags."""

    def test_pretty_with_max_chars_bounds_compact_size(self, mock_client, capsys):
        """--pretty + --max-chars: compact size must be bounded (pretty may exceed)."""
        data = [{f"k{i}": i} for i in range(100)]
        mock_client.get.return_value = json_resp(data)
        args = make_args(endpoint="/api/states", pretty=True, max_chars=200)
        assert curl_cmd.run(args) == 0
        out = capsys.readouterr().out
        parsed = json.loads(out)
        compact = len(json.dumps(parsed, separators=(",", ":"), ensure_ascii=False))
        assert compact <= 200, f"compact size {compact} exceeds max_chars=200"

    def test_no_guard_disables_env_max_chars(self, mock_client, monkeypatch, capsys):
        """--no-guard disables even HA_CLI_MAX_CHARS env var (user preference wins)."""
        monkeypatch.setenv("HA_CLI_MAX_CHARS", "150")
        data = [{"x": i} for i in range(500)]
        mock_client.get.return_value = json_resp(data)
        args = make_args(endpoint="/api/states", no_guard=True)
        assert curl_cmd.run(args) == 0
        parsed = json.loads(capsys.readouterr().out)
        assert len(parsed) == 500  # all items, no cap

    def test_auth_token_never_in_stderr(self, mock_client, capsys):
        """On HTTP error, the auth token must not appear in stderr."""
        mock_client.get.return_value = json_resp({}, status=500)
        args = make_args(endpoint="/api/states")
        assert curl_cmd.run(args) == 1
        err = capsys.readouterr().err
        assert "Bearer" not in err
        assert "tok" not in err

    def test_large_list_does_not_hang(self, mock_client, capsys):
        """1000+ items must shape in reasonable time (perf guard)."""
        import time

        data = [{"i": i, "v": "x" * 20} for i in range(2000)]
        mock_client.get.return_value = json_resp(data)
        args = make_args(endpoint="/api/states", max_chars=500)
        start = time.monotonic()
        assert curl_cmd.run(args) == 0
        elapsed = time.monotonic() - start
        assert elapsed < 3.0, f"shaping took {elapsed:.2f}s (may be slow under CI load)"

    def test_headers_are_case_insensitive(self, mock_client, capsys):
        """CaseInsensitiveDict headers should not break response parsing."""
        from requests.structures import CaseInsensitiveDict

        mock_client.get.return_value = response_mock(
            headers=CaseInsensitiveDict({"CONTENT-TYPE": "application/json"}),
            json_data={"ok": True},
            text='{"ok": true}',
        )
        args = make_args(endpoint="/api/states")
        assert curl_cmd.run(args) == 0


class TestValidateArgs:
    """Direct unit tests for the extracted _validate_args helper.

    Pins the conflict checks and endpoint/method normalization that were
    previously inlined in run(); invalid combinations raise ``_CurlError``.
    """

    def _args(self, **overrides):
        defaults = {"endpoint": None}
        defaults.update(overrides)
        return make_args(**defaults)

    def test_raw_and_pretty_raises_curl_error(self):
        args = self._args(raw=True, pretty=True)
        with pytest.raises(_CurlError, match="Cannot combine --raw with --pretty"):
            _validate_args(args, summary=False)

    def test_pick_with_count_raises_curl_error(self):
        args = self._args(pick="state", count=True)
        with pytest.raises(_CurlError, match="Cannot combine --pick"):
            _validate_args(args, summary=False)

    def test_entity_sets_endpoint_and_forces_get(self):
        args = self._args(entity="sensor.foo")
        result = _validate_args(args, summary=False)
        assert result.method == "GET"
        assert result.endpoint == "/api/states/sensor.foo"
        assert args.endpoint is None

    def test_entity_with_invalid_id_raises_curl_error(self):
        args = self._args(entity="not-an-entity-id")
        with pytest.raises(_CurlError, match="Invalid entity_id"):
            _validate_args(args, summary=False)

    def test_domain_with_entity_raises_curl_error(self):
        args = self._args(entity="sensor.foo", domain="light")
        with pytest.raises(_CurlError, match="Cannot combine --domain"):
            _validate_args(args, summary=False)

    def test_missing_endpoint_raises_curl_error(self):
        args = self._args()
        with pytest.raises(_CurlError, match="endpoint path is required"):
            _validate_args(args, summary=False)

    def test_explicit_endpoint_passthrough(self):
        args = self._args(endpoint="/api/services")
        request = _validate_args(args, summary=False)
        assert request.method == "GET"
        assert request.endpoint == "/api/services"


class TestExecuteRequest:
    """Direct unit tests for the dispatch-table _execute_request."""

    def _client(self):
        client = MagicMock()
        client.get.return_value = MagicMock(ok=True, status_code=200)
        client.post.return_value = MagicMock(ok=True, status_code=200)
        client.put.return_value = MagicMock(ok=True, status_code=200)
        client.delete.return_value = MagicMock(ok=True, status_code=200)
        client.patch.return_value = MagicMock(ok=True, status_code=200)
        return client

    def test_get_calls_client_get(self):
        client = self._client()
        _execute_request(client, "GET", "/api/states", None)
        client.get.assert_called_once_with("/api/states")

    def test_post_calls_client_post_with_json(self):
        client = self._client()
        _execute_request(client, "POST", "/api/services/x/y", {"foo": 1})
        client.post.assert_called_once_with("/api/services/x/y", json={"foo": 1})

    def test_delete_passes_json_kwarg(self):
        client = self._client()
        _execute_request(client, "DELETE", "/api/x", None)
        client.delete.assert_called_once_with("/api/x", json=None)

    def test_unknown_method_raises_curl_error(self):
        client = self._client()
        with pytest.raises(_CurlError, match="Unknown HTTP method"):
            _execute_request(client, "HEAD", "/api/x", None)

    def test_harequesterror_raises_curl_error(self):
        client = MagicMock()
        client.get.side_effect = HARequestError("boom")
        with pytest.raises(_CurlError, match="boom"):
            _execute_request(client, "GET", "/api/x", None)


class TestEmitOutput:
    """Direct unit tests for the extracted _emit_output helper."""

    def test_count_handler_prints_length_and_returns_0(self, capsys):
        args = make_args(count=True, endpoint="/api/any")
        result = _emit_output(
            args,
            _CurlRequest("GET", "/api/any"),
            [1, 2, 3],
            "[1,2,3]",
            True,
            summary=False,
        )
        assert result == 0
        assert capsys.readouterr().out.strip() == "3"

    def test_raw_handler_prints_raw_text(self, capsys):
        args = make_args(raw=True, endpoint="/api/any")
        result = _emit_output(
            args,
            _CurlRequest("GET", "/api/any"),
            None,
            "raw text here",
            False,
            summary=False,
        )
        assert result == 0
        assert capsys.readouterr().out == "raw text here"
