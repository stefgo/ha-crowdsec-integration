"""Diagnostics data for support requests."""

from __future__ import annotations

import re
from dataclasses import asdict
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import (
    CONF_BOUNCER_API_KEY,
    CONF_LAPI_URL,
    CONF_MACHINE_ID,
    CONF_MACHINE_PASSWORD,
    CONF_METRICS_URL,
)
from .coordinator import CrowdSecConfigEntry

TO_REDACT = {CONF_MACHINE_ID, CONF_MACHINE_PASSWORD, CONF_BOUNCER_API_KEY}

# The addresses are not secrets, but they carry internal host names, DDNS
# entries and tunnel endpoints — and diagnostics get pasted into public issues.
# The host goes, the shape stays: scheme, port and path are what a support
# question is actually about.
TO_REDACT_HOSTS = (CONF_LAPI_URL, CONF_METRICS_URL)

REDACTED = "**REDACTED**"

# CrowdSec labels ``cs_lapi_machine_requests_total`` with the machine's name —
# the very machine ID the config section redacts.
MACHINE_LABEL = "machine"

# The fields of the data that carry free text. Error messages quote the URL
# they failed on (``Cannot connect to host crowdsec.lan:8080``), so the host
# redacted in the config would otherwise turn up again here.
TEXT_FIELDS = ("errors", "problem_reasons")


def _redact_host(url: Any) -> Any:
    """Replace the host of a URL, keeping scheme, port and path."""
    if not isinstance(url, str) or not url.strip():
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return "**REDACTED**"
    if not parts.hostname:
        return "**REDACTED**"
    netloc = "**REDACTED**"
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _redact_key(key: Any) -> Any:
    """Keep what kind of row a key describes, drop the address inside it.

    Only the ``id:`` keys are built from the decision ID; the other two are
    assembled from the address itself (``val:<origin>:<ip>``,
    ``hist:<ip>:<scenario>:<timestamp>``). Redacting ``value`` alone would
    therefore leave the address in plain sight one field further along.
    """
    if not isinstance(key, str) or key.startswith("id:"):
        return key
    prefix, separator, _ = key.partition(":")
    return f"{prefix}:**REDACTED**" if separator else "**REDACTED**"


def _redact_addresses(data: dict[str, Any]) -> None:
    """Replace IP addresses with placeholders; the counts are preserved."""
    if isinstance(data.get("top_attackers"), list):
        data["top_attackers"] = [
            {**entry, "ip": "**REDACTED**"} if isinstance(entry, dict) else entry
            for entry in data["top_attackers"]
        ]
    if data.get("top_attacker") is not None:
        data["top_attacker"] = "**REDACTED**"

    # The decision table is the one place where every banned address is listed
    # in full. What a support request needs is the shape of the rows, not who
    # is in them — so the addresses go and everything else stays.
    if isinstance(data.get("decisions"), list):
        data["decisions"] = [
            {
                **{
                    key: value
                    for key, value in entry.items()
                    if key not in ("value", "as_name", "as_number", "key")
                },
                "value": "**REDACTED**",
                "key": _redact_key(entry.get("key")),
            }
            if isinstance(entry, dict)
            else entry
            for entry in data["decisions"]
        ]


def _sensitive_pattern(entry_data: dict[str, Any]) -> re.Pattern[str] | None:
    """The configured hosts and the machine ID, as one pattern over text.

    Matched as whole words so that a short machine ID does not eat into
    longer words around it.
    """
    words: set[str] = set()
    for key in TO_REDACT_HOSTS:
        url = entry_data.get(key)
        if not isinstance(url, str):
            continue
        try:
            host = urlsplit(url).hostname
        except ValueError:
            host = None
        if host:
            words.add(host)
    machine = str(entry_data.get(CONF_MACHINE_ID) or "").strip()
    if machine:
        words.add(machine)
    if not words:
        return None
    # Longest first, so a host is not half-replaced by a shorter word in it.
    alternatives = "|".join(
        re.escape(word) for word in sorted(words, key=len, reverse=True)
    )
    return re.compile(rf"(?<![\w-])(?:{alternatives})(?![\w-])", re.IGNORECASE)


def _scrub_texts(data: dict[str, Any], pattern: re.Pattern[str] | None) -> None:
    """Remove hosts and the machine ID from the free-text fields."""
    if pattern is None:
        return

    def scrub(text: Any) -> Any:
        return pattern.sub(REDACTED, text) if isinstance(text, str) else text

    for key in TEXT_FIELDS:
        if isinstance(data.get(key), list):
            data[key] = [scrub(text) for text in data[key]]
    if isinstance(data.get("problems"), list):
        data["problems"] = [
            {**entry, "text": scrub(entry.get("text"))}
            if isinstance(entry, dict)
            else entry
            for entry in data["problems"]
        ]


def _redact_metrics(
    metrics: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    """The raw metrics with the machine names taken out of their labels."""
    return {
        name: [
            {
                **sample,
                "labels": {
                    key: REDACTED if key == MACHINE_LABEL else value
                    for key, value in sample.get("labels", {}).items()
                },
            }
            for sample in samples
        ]
        for name, samples in metrics.items()
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: CrowdSecConfigEntry
) -> dict[str, Any]:
    """Configuration (redacted), latest data and the raw metrics."""
    coordinator = entry.runtime_data
    data = asdict(coordinator.data) if coordinator.data else None

    if data is not None:
        for key, value in list(data.items()):
            if isinstance(value, datetime):
                data[key] = value.isoformat()
        # The same shape as in the attribute of "Active bouncers".
        data["bouncers"] = [status.as_dict() for status in coordinator.data.bouncers]
        _redact_addresses(data)
        _scrub_texts(data, _sensitive_pattern(dict(entry.data)))

    config = async_redact_data(dict(entry.data), TO_REDACT)
    for key in TO_REDACT_HOSTS:
        if key in config:
            config[key] = _redact_host(config[key])

    return {
        "config": config,
        "options": dict(entry.options),
        "data": data,
        # Without the raw counters there is no way to tell what CrowdSec
        # actually returned when a calculation goes wrong.
        "metrics": _redact_metrics(coordinator.raw_metrics),
    }
