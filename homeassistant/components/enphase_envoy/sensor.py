"""Support for Enphase Envoy solar energy monitor."""

from datetime import timedelta
import logging

import async_timeout
from envoy_reader.envoy_reader import EnvoyReader
import httpcore
import voluptuous as vol

from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.const import (
    CONF_IP_ADDRESS,
    CONF_MONITORED_CONDITIONS,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_USERNAME,
    ENERGY_WATT_HOUR,
    POWER_WATT,
)
from homeassistant.exceptions import PlatformNotReady
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

_LOGGER = logging.getLogger(__name__)

SENSORS = {
    "production": ("Envoy Current Energy Production", POWER_WATT),
    "daily_production": ("Envoy Today's Energy Production", ENERGY_WATT_HOUR),
    "seven_days_production": (
        "Envoy Last Seven Days Energy Production",
        ENERGY_WATT_HOUR,
    ),
    "lifetime_production": ("Envoy Lifetime Energy Production", ENERGY_WATT_HOUR),
    "consumption": ("Envoy Current Energy Consumption", POWER_WATT),
    "daily_consumption": ("Envoy Today's Energy Consumption", ENERGY_WATT_HOUR),
    "seven_days_consumption": (
        "Envoy Last Seven Days Energy Consumption",
        ENERGY_WATT_HOUR,
    ),
    "lifetime_consumption": ("Envoy Lifetime Energy Consumption", ENERGY_WATT_HOUR),
    "inverters": ("Envoy Inverter", POWER_WATT),
}

SCAN_INTERVAL = timedelta(seconds=60)

ICON = "mdi:flash"
CONST_DEFAULT_HOST = "envoy"

SCAN_INTERVAL = timedelta(seconds=60)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Optional(CONF_IP_ADDRESS, default=CONST_DEFAULT_HOST): cv.string,
        vol.Optional(CONF_USERNAME, default="envoy"): cv.string,
        vol.Optional(CONF_PASSWORD, default=""): cv.string,
        vol.Optional(CONF_MONITORED_CONDITIONS, default=list(SENSORS)): vol.All(
            cv.ensure_list, [vol.In(list(SENSORS))]
        ),
        vol.Optional(CONF_NAME, default=""): cv.string,
    }
)


async def async_setup_platform(
    homeassistant, config, async_add_entities, discovery_info=None
):
    """Set up the Enphase Envoy sensor."""
    ip_address = config[CONF_IP_ADDRESS]
    monitored_conditions = config[CONF_MONITORED_CONDITIONS]
    name = config[CONF_NAME]
    username = config[CONF_USERNAME]
    password = config[CONF_PASSWORD]

    envoy_reader = EnvoyReader(ip_address, username, password)

    async def async_update_data():
        """Fetch data from API endpoint."""
        async with async_timeout.timeout(30):
            i = 0
            while i < 3:
                try:
                    data = await envoy_reader.update()
                except httpcore.ProtocolError as err:
                    raise UpdateFailed(f"Error communicating with API: {err}") from err
                except httpcore.ConnectTimeout as err:
                    raise UpdateFailed(f"Timeout error with API: {err}") from err

                _LOGGER.debug("Retrieved data from API: %s", data)

                if "can't handle event type ConnectionClosed" in str(
                    data.get("inverters_production")
                ):
                    _LOGGER.debug("Retry polling Envoy.  Previous attempt failed.")
                    i += 1
                else:
                    _LOGGER.debug("Returning API data.")
                    return data

            # After 3 communication errors log the message and wait
            # for the next polling cycle
            _LOGGER.warning(
                "Communication error with Enphase Envoy.  Will retrieve data on the next poll."
            )

    coordinator = DataUpdateCoordinator(
        homeassistant,
        _LOGGER,
        name="sensor",
        update_method=async_update_data,
        update_interval=SCAN_INTERVAL,
    )

    await coordinator.async_refresh()

    entities = []
    # Iterate through the list of sensors
    for condition in monitored_conditions:
        if condition == "inverters" and coordinator.data is not None:
            inverters = coordinator.data.get("inverters_production")
            _LOGGER.debug("Inverter data: %s", inverters)
            if isinstance(inverters, dict):
                for inverter in inverters:
                    entities.append(
                        Envoy(
                            condition,
                            f"{name}{SENSORS[condition][0]} {inverter}",
                            SENSORS[condition][1],
                            coordinator,
                        )
                    )
                    _LOGGER.debug(
                        "Adding inverter SN: %s - Type: %s.",
                        f"{name}{SENSORS[condition][0]} {inverter}",
                        condition,
                    )
            elif "Unable to connect to Envoy" in inverters:
                _LOGGER.error(
                    "Unable to connect to Enphase Envoy during setup. Inverter entities not added. Please check IP address and credentials are correct."
                )
            elif "can't handle event type ConnectionClosed" in inverters:
                _LOGGER.error(
                    "Communication error with Enphase Envoy during setup. Inverter entities not added."
                )
        elif coordinator.data is not None:
            entities.append(
                Envoy(
                    condition,
                    f"{name}{SENSORS[condition][0]}",
                    SENSORS[condition][1],
                    coordinator,
                )
            )
            _LOGGER.debug(
                "Adding sensor: %s - Type: %s.",
                f"{name}{SENSORS[condition][0]})",
                condition,
            )
        else:
            raise PlatformNotReady
    async_add_entities(entities)


class Envoy(CoordinatorEntity):
    """Envoy entity."""

    def __init__(self, sensor_type, name, unit, coordinator):
        """Initialize Envoy entity."""
        self._type = sensor_type
        self._name = name
        self._unit_of_measurement = unit

        super().__init__(coordinator)

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def state(self):
        """Return the state of the sensor."""
        if self._type != "inverters":
            try:
                value = self.coordinator.data.get(self._type)
            except AttributeError:
                _LOGGER.debug("Data for sensor %s is not a number.", self._type)
                return

            _LOGGER.debug("Updating: %s - %s", self._type, value)

        elif self._type == "inverters":
            serial_number = self._name.split(" ")[2]
            try:
                value = self.coordinator.data.get("inverters_production").get(
                    serial_number
                )[0]
            except AttributeError:
                _LOGGER.debug(
                    "Data received from inverter %s was an unsupported format.",
                    serial_number,
                )
                return

            _LOGGER.debug(
                "Updating: %s (%s) - %s.",
                self._type,
                serial_number,
                value,
            )

        return value

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement of this entity, if any."""
        return self._unit_of_measurement

    @property
    def icon(self):
        """Icon to use in the frontend, if any."""
        return ICON

    @property
    def device_state_attributes(self):
        """Return the state attributes."""
        if self._type == "inverters":
            serial_number = self._name.split(" ")[2]
            try:
                value = self.coordinator.data.get("inverters_production").get(
                    serial_number
                )[1]
                return {"last_reported": value}
            except AttributeError:
                _LOGGER.debug(
                    "Data received from inverter for last reported time was an unsupported format."
                )
                return

        return None
