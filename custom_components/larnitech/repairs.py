# Updated: 2026-08-21 16:10
"""Repair flow: confirm before removing devices after a mass Larnitech drop.

See coordinator.py `_check_mass_removal` for when the issue is raised."""
from __future__ import annotations

from typing import Any

from homeassistant.components.repairs import ConfirmRepairFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN


class MassRemovalRepairFlow(ConfirmRepairFlow):
    """Confirm step removes the devices the coordinator flagged as missing.

    The set to remove is read from the coordinator at confirm time, not
    captured here — see `confirm_mass_removal`."""

    def __init__(self, entry_id: str) -> None:
        super().__init__()
        self._entry_id = entry_id

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            coordinator = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
            if coordinator is not None:
                coordinator.confirm_mass_removal()
        return await super().async_step_confirm(user_input)


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> ConfirmRepairFlow:
    return MassRemovalRepairFlow(data["entry_id"])
