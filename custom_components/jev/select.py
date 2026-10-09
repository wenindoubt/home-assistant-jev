"""A live, locally restored speech profile, independent of routing and budgets."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DEFAULT_RESPONSE_STYLE, DOMAIN, RESPONSE_STYLES
from .coordinator import JevRuntimeData
from .entity import build_device_info

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Provide one style selector per Jev entry."""
    async_add_entities([JevResponseStyleSelect(entry)])


class JevResponseStyleSelect(SelectEntity, RestoreEntity):
    """Change only the wording of subsequent conversation responses."""

    _attr_has_entity_name = True
    _attr_translation_key = "response_style"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry) -> None:
        self._runtime: JevRuntimeData = entry.runtime_data
        self._attr_options = list(RESPONSE_STYLES)
        self._attr_unique_id = f"{entry.entry_id}_response_style"
        self._attr_device_info = build_device_info(entry.entry_id, self._runtime)

    @property
    def current_option(self) -> str:
        """The agent reads this same value at reply time."""
        return self._runtime.response_style

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        restored = await self.async_get_last_state()
        self._runtime.response_style = (
            restored.state
            if restored and restored.state in RESPONSE_STYLES
            else DEFAULT_RESPONSE_STYLE
        )

    async def async_select_option(self, option: str) -> None:
        """No API call, options update, entry reload or permission change."""
        if option not in RESPONSE_STYLES:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_response_style",
                translation_placeholders={"style": option},
            )
        self._runtime.response_style = option
        self.async_write_ha_state()
