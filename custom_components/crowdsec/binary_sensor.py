"""Binary sensors: reachability, the aggregate problem flag and the bouncers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .bouncers import BouncerStatus
from .const import BOUNCER_UNIQUE_ID_INFIX
from .coordinator import CrowdSecConfigEntry, CrowdSecCoordinator, CrowdSecData
from .entity import CrowdSecEntity


@dataclass(frozen=True, kw_only=True)
class CrowdSecBinarySensorDescription(BinarySensorEntityDescription):
    """Description with a value and an attribute function."""

    value_fn: Callable[[CrowdSecData], bool]
    attrs_fn: Callable[[CrowdSecData], dict[str, Any]] | None = None


BINARY_SENSORS: tuple[CrowdSecBinarySensorDescription, ...] = (
    CrowdSecBinarySensorDescription(
        key="reachable",
        translation_key="reachable",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda data: data.reachable,
        attrs_fn=lambda data: {"errors": data.errors},
    ),
    CrowdSecBinarySensorDescription(
        key="problem",
        translation_key="problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda data: data.problem,
        attrs_fn=lambda data: {
            "reasons": data.problem_reasons,
            "problems": data.problems,
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CrowdSecConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the binary sensors of an instance.

    The bouncers are not known up front: one turns up when it first fetches
    decisions, so their entities are added as the coordinator finds them.
    """
    coordinator = entry.runtime_data
    async_add_entities(
        CrowdSecBinarySensor(coordinator, entry, description)
        for description in BINARY_SENSORS
    )

    added: set[str] = set()

    @callback
    def _add_new_bouncers() -> None:
        data = coordinator.data
        if data is None:
            return
        new = [status.name for status in data.bouncers if status.name not in added]
        if not new:
            return
        added.update(new)
        async_add_entities(
            CrowdSecBouncerSensor(coordinator, entry, name, added) for name in new
        )

    _add_new_bouncers()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_bouncers))


class CrowdSecBinarySensor(CrowdSecEntity, BinarySensorEntity):
    """Reachability or aggregate problem flag of an instance."""

    entity_description: CrowdSecBinarySensorDescription

    @property
    def available(self) -> bool:
        """Deliberately always available.

        These two entities are the ones that *report* the outage — they must
        not go ``unavailable`` themselves when a scrape fails.
        """
        return self.coordinator.data is not None

    @property
    def is_on(self) -> bool:
        return self.entity_description.value_fn(self.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.data)


class CrowdSecBouncerSensor(CrowdSecEntity, BinarySensorEntity):
    """Whether one bouncer still fetches decisions.

    On while it does, off once it has not asked for N intervals in a row,
    unknown until either is clear. Disabling the entity is how a bouncer is
    ignored: the coordinator then leaves it out of the status and the counts.
    """

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(
        self,
        coordinator: CrowdSecCoordinator,
        entry: CrowdSecConfigEntry,
        name: str,
        added: set[str],
    ) -> None:
        super().__init__(
            coordinator,
            entry,
            BinarySensorEntityDescription(
                key=f"bouncer_{name}", translation_key="bouncer"
            ),
        )
        # The coordinator reads the bouncer's name back from this ID in the
        # entity registry.
        self._attr_unique_id = f"{entry.entry_id}{BOUNCER_UNIQUE_ID_INFIX}{name}"
        self._attr_translation_placeholders = {"name": name}
        self._bouncer = name
        self._added = added

    @property
    def _status(self) -> BouncerStatus | None:
        data = self.coordinator.data
        if data is None:
            return None
        for status in data.bouncers:
            if status.name == self._bouncer:
                return status
        return None

    @property
    def available(self) -> bool:
        """Follows the metrics scrape, like every value read off it."""
        data = self.coordinator.data
        return data is not None and data.metrics_ok and self._status is not None

    @property
    def is_on(self) -> bool | None:
        status = self._status
        return None if status is None else status.active

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        status = self._status
        if status is None:
            return None
        values = status.as_dict()
        return {
            "queries_per_minute": values["queries_per_minute"],
            "last_query": values["last_query"],
            "idle_intervals": status.idle_cycles,
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.coordinator.bouncer_entity_added(self._bouncer)

    async def async_will_remove_from_hass(self) -> None:
        """Let the platform add the bouncer again if it comes back.

        That is the case after the entity was deleted and the bouncer then
        queried again.
        """
        self._added.discard(self._bouncer)
        await super().async_will_remove_from_hass()
