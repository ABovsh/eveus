async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Eveus from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "config": entry.data
    }

    # Create input_number entities
    for input_id, config in INPUT_NUMBERS.items():
        try:
            input_config = {
                input_id: {
                    "min": config["min"],
                    "max": config["max"],
                    "step": config["step"],
                    "mode": config["mode"],
                    "unit_of_measurement": config["unit_of_measurement"],
                    "icon": config["icon"],
                    "name": config["name"],
                }
            }
            if "initial" in config:
                input_config[input_id]["initial"] = config["initial"]
            
            _LOGGER.debug("Creating input_number with config: %s", input_config)
            await hass.services.async_call(
                INPUT_NUMBER_DOMAIN,
                "setup",
                service_data=input_config,
                blocking=True,
            )
            _LOGGER.debug("Created input_number: %s", input_id)
        except Exception as err:
            _LOGGER.error("Error creating input_number %s: %s", input_id, err)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True
