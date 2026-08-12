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
    if TEMPLATE_PATTERN.search(value):
        return True
    for opening, closing in TEMPLATE_DELIMITERS:
        for match in re.finditer(re.escape(opening), value):
            if match.end() < len(value) and value.find(closing, 0, match.start()) == -1:
                return True
    return False
