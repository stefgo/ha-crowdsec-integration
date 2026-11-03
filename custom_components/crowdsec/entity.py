"""Common base of all CrowdSec entities."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntry, DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import BOUNCER_UNIQUE_ID_INFIX, CONF_LAPI_URL, DOMAIN
from .coordinator import CrowdSecConfigEntry, CrowdSecCoordinator, CrowdSecData


def bouncer_device_identifier(entry_id: str, name: str) -> str:
    """Identifier of a bouncer's device — the same string as its entity's ID."""
    return f"{entry_id}{BOUNCER_UNIQUE_ID_INFIX}{name}"


def bouncer_device_info(entry: ConfigEntry, name: str) -> DeviceInfo:
    """A bouncer is a device of its own, connected via its instance.

    That gives it a device page with "Delete", which is how a bouncer that is
    gone from CrowdSec is removed. The name carries the instance, so that the
    entity's name is the one it had while it hung off the instance's device.
    """
    return DeviceInfo(
        identifiers={(DOMAIN, bouncer_device_identifier(entry.entry_id, name))},
        via_device=(DOMAIN, entry.entry_id),
        manufacturer="CrowdSec",
        model="Bouncer",
        translation_key="bouncer",
        translation_placeholders={"instance": entry.title, "name": name},
    )


def bouncer_of_device(entry: ConfigEntry, device: DeviceEntry) -> str | None:
    """The bouncer a device stands for, or ``None`` for the instance's device."""
    prefix = bouncer_device_identifier(entry.entry_id, "")
    for domain, identifier in device.identifiers:
        if domain == DOMAIN and identifier.startswith(prefix):
            return identifier[len(prefix) :]
    return None


class CrowdSecEntity(CoordinatorEntity[CrowdSecCoordinator]):
    """Binds an entity to the device of its instance, or to the one given."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: CrowdSecCoordinator,
        entry: CrowdSecConfigEntry,
        description: EntityDescription,
        device_info: DeviceInfo | None = None,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = device_info or DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="CrowdSec",
            model="Security Engine",
            sw_version=coordinator.data.version if coordinator.data else None,
            configuration_url=entry.data.get(CONF_LAPI_URL),
        )

    @property
    def data(self) -> CrowdSecData:
        """Current data of the coordinator."""
        return self.coordinator.data
