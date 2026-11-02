"""Tests that keep manifest, constants and translations in sync.

These files are maintained by hand and otherwise drift apart unnoticed — a
missing key is only spotted in the UI.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "crowdsec"
sys.path.insert(0, str(COMPONENT))

import const  # noqa: E402

MANIFEST = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
STRINGS = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
TRANSLATIONS = sorted((COMPONENT / "translations").glob("*.json"))


def _keys(node, prefix=""):
    """All leaf paths of a nested dict."""
    if not isinstance(node, dict):
        return {prefix}
    result = set()
    for key, value in node.items():
        result |= _keys(value, f"{prefix}.{key}" if prefix else key)
    return result


def test_version_comes_from_the_manifest():
    assert MANIFEST["version"] == const.INTEGRATION_VERSION
    assert const.INTEGRATION_VERSION != "0.0.0"


def test_user_agent_is_parseable_by_crowdsec():
    # CrowdSec stores the user agent as the version of the machine and expects
    # exactly one "name/version" — otherwise the login fails with a 401.
    name, _, version = const.USER_AGENT.partition("/")
    assert name and version
    assert "/" not in version
    assert " " not in const.USER_AGENT


def test_domain_matches_manifest():
    assert MANIFEST["domain"] == const.DOMAIN


def test_translations_exist():
    assert TRANSLATIONS, "no translation files found"


def test_translations_cover_all_strings():
    expected = _keys(STRINGS)
    for path in TRANSLATIONS:
        actual = _keys(json.loads(path.read_text(encoding="utf-8")))
        assert not expected - actual, (
            f"{path.name} is missing: {sorted(expected - actual)}"
        )
        assert not actual - expected, (
            f"{path.name} has extra: {sorted(actual - expected)}"
        )


# The same expression hassfest uses to check translations. It already triggers
# on a placeholder like "<host>" — not only on real HTML.
HTML_PATTERN = re.compile(r"<[a-z][\s\S]*>")


def _values(node, prefix=""):
    """All leaf values together with their path."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _values(value, f"{prefix}.{key}" if prefix else key)
    elif isinstance(node, str):
        yield prefix, node


def test_no_html_in_translations():
    for path in [COMPONENT / "strings.json", *TRANSLATIONS]:
        content = json.loads(path.read_text(encoding="utf-8"))
        for where, text in _values(content):
            assert not HTML_PATTERN.search(text), (
                f"{path.name}: HTML in {where}: {text}"
            )


def test_placeholders_are_balanced():
    """Curly braces are placeholders — a lone one breaks the display."""
    for path in [COMPONENT / "strings.json", *TRANSLATIONS]:
        content = json.loads(path.read_text(encoding="utf-8"))
        for where, text in _values(content):
            assert text.count("{") == text.count("}"), f"{path.name}: {where}"


def test_services_are_documented():
    """Every service offered in services.yaml needs a text."""
    raw = (COMPONENT / "services.yaml").read_text(encoding="utf-8")
    declared = {
        line.split(":")[0]
        for line in raw.splitlines()
        if line and not line[0].isspace() and line.rstrip().endswith(":")
    }
    assert declared == {
        const.SERVICE_BAN_IP,
        const.SERVICE_UNBAN_IP,
        const.SERVICE_REFRESH,
    }
    assert declared == set(STRINGS["services"])


def test_card_constants_match_the_build():
    """The card is only served if the build writes it where const expects it."""
    rollup = (COMPONENT.parents[1] / "card" / "rollup.config.js").read_text(
        encoding="utf-8"
    )
    assert const.CARD_FILENAME in rollup
    assert f"custom_components/{const.DOMAIN}/www" in rollup


def test_the_manifest_declares_what_the_card_needs():
    """Static path, frontend registration and WebSocket commands are HA parts."""
    assert set(MANIFEST["dependencies"]) >= {"http", "frontend", "websocket_api"}


def test_websocket_commands_are_namespaced():
    """Command names have to start with the domain, or HA rejects them."""
    for command in (
        const.WS_INSTANCES,
        const.WS_DECISIONS_LIST,
        const.WS_DECISIONS_DELETE,
    ):
        assert command.startswith(f"{const.DOMAIN}/")


# The one code that is not translated on purpose: its message carries what the
# LAPI said, and a fixed sentence in its place would hide the reason.
UNTRANSLATED_ERRORS = {"request_failed"}


def test_every_websocket_error_has_a_card_text():
    """The cards say an error in the user's language by its code.

    A code the card does not know falls back to the English message from the
    server — or, worse, a code reused for a second purpose keeps a text that
    no longer fits, as "No decision was selected" did for a mistyped address.
    """
    source = (COMPONENT / "websocket_api.py").read_text(encoding="utf-8")
    codes = set(re.findall(r'send_error\(\s*msg\["id"\],\s*"([a-z_]+)"', source))
    assert codes, "no error codes found — has the call changed shape?"

    card = (COMPONENT.parents[1] / "card" / "src" / "localize.ts").read_text(
        encoding="utf-8"
    )
    texts = set(re.findall(r'"error\.([a-z_]+)":', card))

    assert codes - UNTRANSLATED_ERRORS <= texts
