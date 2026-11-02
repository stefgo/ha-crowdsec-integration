"""Repair flows.

Only one so far: CrowdSec serves ``GET /v1/decisions`` to bouncers only, so
without a bouncer API key the ban table stays empty with no visible reason. The
flow below asks for a key, tries it against the instance and stores it, which
is the whole fix.
"""

from __future__ import annotations

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import CrowdSecAuthError, CrowdSecConnectionError
from .const import CONF_BOUNCER_API_KEY, ISSUE_DECISIONS_UNAVAILABLE

SECRET_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

SCHEMA = vol.Schema({vol.Required(CONF_BOUNCER_API_KEY): SECRET_SELECTOR})


class BouncerKeyRepairFlow(RepairsFlow):
    """Ask for a bouncer API key and store it on the entry."""

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        from . import build_client

        errors: dict[str, str] = {}
        detail = ""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            # The instance was removed while the repair sat in the list.
            return self.async_abort(reason="entry_not_found")

        if user_input is not None:
            data = {
                **entry.data,
                CONF_BOUNCER_API_KEY: user_input[CONF_BOUNCER_API_KEY],
            }
            client = build_client(self.hass, data, entry.data.get("verify_ssl", True))
            try:
                await client.async_get_active_decision_count()
            except CrowdSecAuthError as err:
                errors["base"] = "invalid_auth_bouncer"
                detail = str(err)
            except CrowdSecConnectionError as err:
                errors["base"] = "cannot_connect"
                detail = str(err)
            else:
                self.hass.config_entries.async_update_entry(entry, data=data)
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_create_entry(data={})

        return self.async_show_form(
            step_id="confirm",
            data_schema=SCHEMA,
            errors=errors,
            description_placeholders={"name": entry.title, "error_detail": detail},
        )


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Hand Home Assistant the flow for an issue."""
    if issue_id.startswith(ISSUE_DECISIONS_UNAVAILABLE) and data:
        return BouncerKeyRepairFlow(str(data["entry_id"]))
    raise ValueError(f"Unknown repair issue: {issue_id}")
