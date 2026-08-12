"""Shared Jinja2 template detection for validators."""

import re

TEMPLATE_DELIMITERS = (("{{", "}}"), ("{%", "%}"))
TEMPLATE_PATTERN = re.compile(
    "|".join(
        f"{re.escape(opening)}.*?{re.escape(closing)}"
        for opening, closing in TEMPLATE_DELIMITERS
    ),
    re.DOTALL,
)


def is_jinja_template(value: str) -> bool:
    """True if value contains a ``{{ ... }}`` or ``{% ... %}`` Jinja2 expression.

    Uses ``re.DOTALL`` so multi-line templates are detected. Callers that
    also need to detect HA tags (``!secret``, ``!include``) must check
    ``value.startswith('!')`` separately — this helper is pure Jinja2 detection.
    """
    return template_delimiter_state(value)[0]


def template_delimiter_state(value: str) -> tuple[bool, bool]:
    """Return the existing detection and balance results for *value*.

    The detection rules intentionally include unmatched opening delimiters,
    while balance remains a per-delimiter count. Keep both results together so
    validators cannot drift into different malformed-template behavior.
    """
    detected = bool(TEMPLATE_PATTERN.search(value))
    if not detected:
        for opening, closing in TEMPLATE_DELIMITERS:
            for match in re.finditer(re.escape(opening), value):
                if (
                    match.end() < len(value)
                    and value.find(closing, 0, match.start()) == -1
                ):
                    detected = True
                    break
            if detected:
                break

    balanced = all(
        value.count(opening) == value.count(closing)
        for opening, closing in TEMPLATE_DELIMITERS
    )
    return detected, balanced
