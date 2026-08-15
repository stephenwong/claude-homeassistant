"""Unit tests for template_validator.py — Jinja2 template linting via HA render API."""

from unittest.mock import MagicMock, patch

import pytest

from tests.helpers import (
    assert_diagnostic,
    assert_no_diagnostic,
    make_response,
    write_yaml,
)
from tools.common import HARequestError
from tools.ha.client import HAClient
from tools.validators._templates import is_jinja_template, template_delimiter_state
from tools.validators.templates import TemplateValidator, main

_write_automation = write_yaml


def _mock_render(success: bool = True, message: str = "") -> MagicMock:
    client = MagicMock(spec=HAClient)
    if success:
        resp = make_response(text="42")
    else:
        resp = make_response(message, status=400, content_type="text/plain")
        resp.json = MagicMock(return_value={"message": message})
    client.post.return_value = resp
    return client


def _run_with_client(config_dir, client):
    """Run template validation with an already configured client double."""
    with (
        patch("tools.validators.templates.HAClient.from_env", return_value=client),
        patch("tools.ha.client.HAClient.from_env", return_value=client),
    ):
        validator = TemplateValidator(str(config_dir))
        result = validator.validate_all()
    return validator, result


def _run_template_validation(config_dir, data, *, success=True, message=""):
    """Write one automation and run template validation with a mocked client."""
    write_yaml(config_dir, data)
    return _run_with_client(config_dir, _mock_render(success=success, message=message))


class TestFileDeps:
    def test_file_deps_empty(self):
        v = TemplateValidator()
        assert v.file_deps() == []


def test_trailing_unclosed_template_delimiter_detected():
    assert template_delimiter_state("{{") == (True, False)
    assert template_delimiter_state("message: {{") == (True, False)
    assert is_jinja_template("{{") is True


def test_client_session_closed_after_validation(config_dir):
    client = _mock_render(success=True)
    write_yaml(config_dir, [{"id": "t", "alias": "T", "action": "{{ 1 }}"}])
    _run_with_client(config_dir, client)
    client.close.assert_called_once()


class TestTemplateValidation:
    def test_valid_template_passes(self, config_dir):
        validator, result = _run_template_validation(
            config_dir,
            [
                {
                    "id": "t",
                    "alias": "T",
                    "triggers": [],
                    "actions": [
                        {"action": "notify.send", "data": {"message": "{{ 1 + 1 }}"}},
                    ],
                },
            ],
        )
        assert result is True
        assert_no_diagnostic(validator, "errors")

    def test_syntax_error_fails(self, config_dir):
        validator, result = _run_template_validation(
            config_dir,
            [
                {
                    "id": "t",
                    "alias": "T",
                    "triggers": [],
                    "actions": [
                        {"action": "notify.send", "data": {"message": "{{ 1 + }}"}},
                    ],
                },
            ],
            success=False,
            message="syntax error: unexpected end of template",
        )
        assert result is False
        assert_diagnostic(validator, "errors", "syntax error")

    def test_null_message_error_payload(self, config_dir):
        validator = TemplateValidator(str(config_dir))
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "raw error text"
        mock_resp.json.return_value = {"message": None}
        mock_client.post.return_value = mock_resp

        status, detail = validator._render(mock_client, "{{ 1 + 1 }}")
        assert status == "error"
        assert detail == "raw error text"
        validator._record_render_error("config/automations.yaml", detail)
        assert len(validator.warnings) == 1
        assert "raw error text" in validator.warnings[0]

    def test_runtime_undefined_warns(self, config_dir):
        validator, result = _run_template_validation(
            config_dir,
            [
                {
                    "id": "t",
                    "alias": "T",
                    "triggers": [],
                    "actions": [
                        {
                            "action": "notify.send",
                            "data": {"message": "{{ trigger.to_state.state }}"},
                        },
                    ],
                },
            ],
            success=False,
            message="'trigger' is undefined",
        )
        assert result is True
        assert_diagnostic(validator, "warnings", "trigger")
        assert_no_diagnostic(validator, "errors")

    def test_unknown_filter_is_error(self, config_dir):
        validator, result = _run_template_validation(
            config_dir,
            [
                {
                    "id": "t",
                    "alias": "T",
                    "triggers": [],
                    "actions": [
                        {
                            "action": "notify.send",
                            "data": {"message": "{{ 'x' | hash }}"},
                        },
                    ],
                },
            ],
            success=False,
            message="No filter named 'hash'",
        )
        assert result is False
        assert_diagnostic(validator, "errors", "no filter named")

    def test_extracts_from_value_template(self, config_dir):
        validator, result = _run_template_validation(
            config_dir,
            [
                {
                    "id": "t",
                    "alias": "T",
                    "trigger": {
                        "platform": "template",
                        "value_template": "{{ states('sensor.temp') | float > 20 }}",
                    },
                    "action": {"action": "notify.send", "data": {}},
                },
            ],
        )
        assert result is True
        assert_no_diagnostic(validator, "errors")

    def test_skips_non_template_strings(self, config_dir):
        validator, result = _run_template_validation(
            config_dir,
            [
                {
                    "id": "t",
                    "alias": "T",
                    "triggers": [],
                    "actions": [
                        {"action": "notify.send", "data": {"message": "Hello world"}},
                    ],
                },
            ],
        )
        assert result is True
        assert_no_diagnostic(validator, "info")
        assert_no_diagnostic(validator, "errors")

    def test_multiple_templates_all_valid(self, config_dir):
        validator, result = _run_template_validation(
            config_dir,
            [
                {
                    "id": "a",
                    "alias": "A",
                    "triggers": [],
                    "actions": [
                        {
                            "action": "notify.send",
                            "data": {
                                "title": "{{ 'hello' }}",
                                "message": "{{ 2 + 2 }}",
                            },
                        },
                    ],
                },
                {
                    "id": "b",
                    "alias": "B",
                    "triggers": [],
                    "actions": [
                        {
                            "action": "light.turn_on",
                            "data": {"brightness": "{{ 255 }}"},
                        },
                    ],
                },
            ],
        )
        assert result is True
        assert_no_diagnostic(validator, "errors")


class TestRenderErrors:
    def test_catch_all_warning_path(self, config_dir):
        """A 400 response that matches neither syntax nor runtime signatures
        should produce a warning, not an error."""
        _write_automation(
            config_dir,
            [
                {
                    "id": "t",
                    "alias": "T",
                    "triggers": [],
                    "actions": [
                        {
                            "action": "notify.send",
                            "data": {"message": "{{ something }}"},
                        },
                    ],
                },
            ],
        )
        client = _mock_render(False, "Something went wrong")
        with patch("tools.validators.templates.HAClient.from_env", return_value=client):
            v = TemplateValidator(str(config_dir))
            assert v.validate_all() is True
            assert_no_diagnostic(v, "errors")
            assert_diagnostic(v, "warnings", "warning")

    def test_post_raises_request_error(self, config_dir):
        """When from_env succeeds but post() raises HARequestError, warn."""
        _write_automation(
            config_dir,
            [
                {
                    "id": "t",
                    "alias": "T",
                    "triggers": [],
                    "actions": [
                        {"action": "notify.send", "data": {"message": "{{ 1 + 1 }}"}},
                    ],
                },
            ],
        )
        client = MagicMock(spec=HAClient)
        client.post.side_effect = HARequestError("connection refused")
        with patch("tools.validators.templates.HAClient.from_env", return_value=client):
            v = TemplateValidator(str(config_dir))
            assert v.validate_all() is True
            assert_diagnostic(v, "warnings", "connection refused")

    def test_non_json_error_body_handled(self, config_dir):
        """When HA returns 400 with non-JSON body, fall back to resp.text."""
        _write_automation(
            config_dir,
            [
                {
                    "id": "t",
                    "alias": "T",
                    "triggers": [],
                    "actions": [
                        {"action": "notify.send", "data": {"message": "{{ 1 + }}"}},
                    ],
                },
            ],
        )
        client = MagicMock(spec=HAClient)
        resp = make_response("plain text error", status=400, content_type="text/plain")
        resp.json.side_effect = ValueError("not json")
        client.post.return_value = resp
        with patch("tools.validators.templates.HAClient.from_env", return_value=client):
            v = TemplateValidator(str(config_dir))
            assert v.validate_all() is True
            assert_no_diagnostic(v, "errors")
            assert_diagnostic(v, "warnings", "warning")


class TestOfflineDegradation:
    def test_offline_warns_and_static_checks(self, config_dir):
        _write_automation(
            config_dir,
            [
                {
                    "id": "t",
                    "alias": "T",
                    "triggers": [],
                    "actions": [
                        {
                            "action": "notify.send",
                            "data": {"message": "{{ states('sensor.temp') }}"},
                        },
                    ],
                },
            ],
        )
        with patch(
            "tools.validators.templates.HAClient.from_env",
            side_effect=HARequestError("offline"),
        ):
            v = TemplateValidator(str(config_dir))
            assert v.validate_all() is True
            assert_diagnostic(v, "info", "skipped")

    def test_offline_balanced_template_passes(self, config_dir):
        _write_automation(
            config_dir,
            [
                {
                    "id": "t",
                    "alias": "T",
                    "triggers": [],
                    "actions": [
                        {
                            "action": "notify.send",
                            "data": {"message": "{{ states('sensor.x') }}"},
                        },
                    ],
                },
            ],
        )
        with patch(
            "tools.validators.templates.HAClient.from_env",
            side_effect=HARequestError("offline"),
        ):
            v = TemplateValidator(str(config_dir))
            assert v.validate_all() is True
            assert_no_diagnostic(v, "errors")
            assert_diagnostic(v, "info", "skipped")

    def test_offline_unbalanced_brace_errors(self, config_dir):
        """A string with balanced {{ }} pairs PLUS extra unmatched }} is
        extractable (regex finds the pairs) but _balanced catches the mismatch."""
        _write_automation(
            config_dir,
            [
                {
                    "id": "t",
                    "alias": "T",
                    "triggers": [],
                    "actions": [
                        {
                            "action": "notify.send",
                            "data": {"message": "{{ a }} }} {{ b }}"},
                        },
                    ],
                },
            ],
        )
        with patch(
            "tools.validators.templates.HAClient.from_env",
            side_effect=HARequestError("offline"),
        ):
            v = TemplateValidator(str(config_dir))
            assert v.validate_all() is False
            assert_diagnostic(v, "errors", "unbalanced")

    def test_unmatched_opening_delimiter_is_checked(self, config_dir):
        found = []
        TemplateValidator._collect("{{ bad", "template", found)
        assert found == [("template", "{{ bad")]


class TestEdgeCases:
    def test_nonexistent_dir_errors(self):
        v = TemplateValidator("/nonexistent")
        assert v.validate_all() is False
        assert_diagnostic(v, "errors", "does not exist")

    def test_no_templates_passes(self, config_dir):
        _write_automation(
            config_dir,
            [
                {"id": "t", "alias": "T", "triggers": [], "actions": []},
            ],
        )
        client = _mock_render(True)
        with patch("tools.validators.templates.HAClient.from_env", return_value=client):
            v = TemplateValidator(str(config_dir))
            assert v.validate_all() is True

    def test_broken_yaml_fails(self, config_dir):
        (config_dir / "automations.yaml").write_text("{{{ not valid yaml\n")
        client = _mock_render(True)
        with patch("tools.validators.templates.HAClient.from_env", return_value=client):
            v = TemplateValidator(str(config_dir))
            assert v.validate_all() is False
            assert_diagnostic(v, "errors", "syntax error")

    def test_secrets_yaml_skipped(self, config_dir):
        (config_dir / "secrets.yaml").write_text(
            "api_key: '{{ template_in_secret }}'\n"
        )
        client = _mock_render(True)
        with patch("tools.validators.templates.HAClient.from_env", return_value=client):
            v = TemplateValidator(str(config_dir))
            assert v.validate_all() is True

    def test_mixed_syntax_and_runtime(self, config_dir):
        _write_automation(
            config_dir,
            [
                {
                    "id": "t",
                    "alias": "T",
                    "triggers": [],
                    "actions": [
                        {
                            "action": "notify.send",
                            "data": {
                                "message": "{{ bad_filter | nonexistent }}",
                                "title": "{{ 'hello' }}",
                            },
                        },
                    ],
                },
            ],
        )
        client = _mock_render(False, "No filter named 'nonexistent'")
        with patch("tools.validators.templates.HAClient.from_env", return_value=client):
            v = TemplateValidator(str(config_dir))
            assert v.validate_all() is False  # syntax error = fail
            assert_diagnostic(v, "errors", "nonexistent")

    def test_control_flow_template_detected(self, config_dir):
        _write_automation(
            config_dir,
            [
                {
                    "id": "t",
                    "alias": "T",
                    "triggers": [],
                    "actions": [
                        {
                            "action": "notify.send",
                            "data": {"message": "{% if x %}a{% else %}b{% endif %}"},
                        },
                    ],
                },
            ],
        )
        client = _mock_render(True)
        with patch("tools.validators.templates.HAClient.from_env", return_value=client):
            v = TemplateValidator(str(config_dir))
            assert v.validate_all() is True


class TestEmptyRenderErrorBody:
    """An empty render error body does not crash validation."""

    def test_empty_error_body_does_not_crash(self, config_dir):
        """A render response with an empty message does not crash validation."""

        _write_automation(
            config_dir,
            [
                {
                    "id": "t",
                    "alias": "T",
                    "triggers": [],
                    "actions": [
                        {
                            "action": "notify.send",
                            "data": {"message": "{{ something }}"},
                        },
                    ],
                },
            ],
        )
        client = MagicMock(spec=HAClient)
        resp = make_response({"message": ""}, status=400)
        client.post.return_value = resp
        with patch("tools.validators.templates.HAClient.from_env", return_value=client):
            v = TemplateValidator(str(config_dir))
            assert v.validate_all() is True
            assert_no_diagnostic(v, "errors")


class TestClientCreationOSError:
    """An OSError while creating the client degrades gracefully."""

    def test_oserror_from_from_env_is_caught(self, config_dir):
        """An OSError from client creation is reported as a skipped live check."""
        _write_automation(
            config_dir,
            [
                {
                    "id": "t",
                    "alias": "T",
                    "triggers": [],
                    "actions": [
                        {
                            "action": "notify.send",
                            "data": {"message": "{{ 1 + 1 }}"},
                        },
                    ],
                },
            ],
        )
        with patch(
            "tools.validators.templates.HAClient.from_env",
            side_effect=OSError("connection refused"),
        ):
            v = TemplateValidator(str(config_dir))
            assert v.validate_all() is True
            assert_diagnostic(v, "info", "skipped")


class TestMain:
    def test_main_dispatches_clean(self, config_dir, monkeypatch):
        _write_automation(
            config_dir,
            [
                {
                    "id": "t",
                    "alias": "T",
                    "triggers": [],
                    "actions": [
                        {"action": "notify.send", "data": {"message": "{{ 'ok' }}"}},
                    ],
                },
            ],
        )
        client = _mock_render(True)
        monkeypatch.setattr("sys.argv", ["templates", str(config_dir)])
        with patch("tools.validators.templates.HAClient.from_env", return_value=client):
            assert main() == 0

    def test_main_invalid(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["templates", "/nonexistent"])
        assert main() == 1


class TestIsJinjaTemplate:
    """Tests for the shared template-detection helper."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("{{ bad", (True, False)),
            ("{{", (True, False)),
            ("{{ foo %}", (True, False)),
            ("}} {{", (False, True)),
            ("{{ a }} }} {{ b }}", (True, False)),
        ],
    )
    def test_malformed_delimiter_state_is_preserved(self, value, expected):
        assert template_delimiter_state(value) == expected

    def test_plain_string_not_template(self):
        assert is_jinja_template("sensor.temperature") is False
        assert is_jinja_template("normal text") is False
        assert is_jinja_template("") is False

    def test_double_brace_expression_is_template(self):
        assert is_jinja_template("{{ states('sensor.temp') }}") is True
        assert is_jinja_template("Value: {{ 25 + 5 }}") is True

    def test_control_flow_is_template(self):
        assert is_jinja_template("{% if true %}sensor.a{% endif %}") is True
        assert is_jinja_template("{%- if x -%}sensor.a{%- endif -%}") is True

    def test_multiline_template_detected(self):
        multiline = "{{ states('sensor.x')\n+ states('sensor.y') }}"
        assert is_jinja_template(multiline) is True

    def test_ha_tag_not_template(self):
        assert is_jinja_template("!secret api_key") is False
        assert is_jinja_template("!input sensor_name") is False

    def test_unpaired_braces_not_template(self):
        assert is_jinja_template("}} {{") is False
        assert is_jinja_template("foo }} bar {{ baz") is False
