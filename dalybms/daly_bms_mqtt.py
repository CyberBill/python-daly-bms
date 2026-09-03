import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class MqttMessage:
    topic: str
    payload: object
    retain: bool = True


class DalyBMSMQTT:
    """Serialize Daly BMS payloads into MQTT and Home Assistant discovery messages."""

    def __init__(
        self,
        device_id,
        device_name,
        topic_root,
        serial_number=None,
        logger=None,
    ):
        self.device_id = device_id
        self.device_name = device_name
        self.topic_root = topic_root.rstrip("/")
        self.serial_number = serial_number
        self.logger = logger

    @staticmethod
    def sanitize_identifier(value):
        """Convert a serial number or label into a safe MQTT/HA identifier."""
        return re.sub(
            r"[^a-z0-9_]+",
            "_",
            str(value).lower(),
        ).strip("_")

    @staticmethod
    def humanize_entity_name(base):
        """Convert an MQTT path into a concise Home Assistant entity name."""
        parts = [part for part in base.strip("/").split("/") if part]

        if len(parts) == 2 and parts[0] == "cell_voltages":
            return f"Cell {int(parts[1]):02d} Voltage"

        if len(parts) == 2 and parts[0] == "temperatures":
            return f"Temperature {parts[1]}"

        leaf = parts[-1] if parts else "value"

        friendly_names = {
            "total_voltage": "Pack Voltage",
            "current": "Current",
            "soc_percent": "State of Charge",
            "battery_code": "Battery Code",
            "serial_number": "Serial Number",
            "bms_sw_version": "BMS Software Version",
            "bms_hw_version": "BMS Hardware Version",
            "board_number": "Board Number",
            "slave_number": "Slave Number",
            "highest_voltage": "Highest Cell Voltage",
            "lowest_voltage": "Lowest Cell Voltage",
            "spread": "Cell Voltage Spread",
            "highest_cell": "Highest Cell Number",
            "lowest_cell": "Lowest Cell Number",
            "highest_temperature": "Highest Temperature",
            "lowest_temperature": "Lowest Temperature",
            "highest_sensor": "Highest Temperature Sensor",
            "lowest_sensor": "Lowest Temperature Sensor",
        }

        if leaf in friendly_names:
            return friendly_names[leaf]

        return leaf.replace("_", " ").title()

    @staticmethod
    def utc_now_iso():
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _topic_for_base(self, base):
        if not base:
            return self.topic_root
        return f"{self.topic_root}{base}"

    def build_hass_config_discovery(self, base):
        entity_path = self.sanitize_identifier(base.replace("/", "_"))
        entity_unique_id = f"{self.device_id}_{entity_path}"
        hass_config_topic = (
            f"homeassistant/sensor/{self.device_id}/{entity_path}/config"
        )
        state_topic = self._topic_for_base(base)

        hass_config_data = {
            "unique_id": entity_unique_id,
            "name": self.humanize_entity_name(base),
            "state_topic": state_topic,
        }

        if "soc_percent" in base:
            hass_config_data["device_class"] = "battery"
            hass_config_data["unit_of_measurement"] = "%"
            hass_config_data["state_class"] = "measurement"
        elif (
            "voltage" in base
            and not ("lowest_cell" in base or "highest_cell" in base)
        ):
            hass_config_data["device_class"] = "voltage"
            hass_config_data["unit_of_measurement"] = "V"
            hass_config_data["state_class"] = "measurement"
            hass_config_data["suggested_display_precision"] = 3
        elif "current" in base:
            hass_config_data["device_class"] = "current"
            hass_config_data["unit_of_measurement"] = "A"
            hass_config_data["state_class"] = "measurement"
        elif "temperature" in base and "sensor" not in base:
            hass_config_data["device_class"] = "temperature"
            hass_config_data["unit_of_measurement"] = "°C"
            hass_config_data["state_class"] = "measurement"
            hass_config_data["suggested_display_precision"] = 1
        elif "capacity" in base:
            hass_config_data["unit_of_measurement"] = "Ah"
            hass_config_data["state_class"] = "measurement"

        hass_device = {
            "identifiers": [self.device_id],
            "manufacturer": "Daly",
            "model": "Smart BMS",
            "name": self.device_name,
            "serial_number": self.serial_number,
        }

        hass_config_data["device"] = hass_device
        return MqttMessage(
            topic=hass_config_topic,
            payload=json.dumps(hass_config_data),
            retain=True,
        )

    def _append_scalar_message(self, base, value, messages, include_hass_discovery):
        if include_hass_discovery:
            messages.append(self.build_hass_config_discovery(base))

        if isinstance(value, list):
            payload = json.dumps(value)
        else:
            payload = value

        messages.append(
            MqttMessage(
                topic=self._topic_for_base(base),
                payload=payload,
                retain=True,
            )
        )

    def derived_values(self, result):
        """Return helper metrics derived from the raw BMS payload."""
        if not isinstance(result, dict):
            return result

        derived = dict(result)

        cell_range = derived.get("cell_voltage_range")
        if isinstance(cell_range, dict):
            highest = cell_range.get("highest_voltage")
            lowest = cell_range.get("lowest_voltage")
            if highest is not None and lowest is not None:
                cell_range = dict(cell_range)
                cell_range["spread"] = round(float(highest) - float(lowest), 3)
                derived["cell_voltage_range"] = cell_range

        return derived

    def _flatten(self, result, base="", include_hass_discovery=False):
        messages = []

        if isinstance(result, dict):
            for key, value in result.items():
                current_base = f"{base}/{key}" if base else f"/{key}"
                if isinstance(value, dict):
                    messages.extend(
                        self._flatten(
                            value,
                            current_base,
                            include_hass_discovery=include_hass_discovery,
                        )
                    )
                else:
                    self._append_scalar_message(
                        current_base,
                        value,
                        messages,
                        include_hass_discovery,
                    )
            return messages

        self._append_scalar_message(
            base,
            result,
            messages,
            include_hass_discovery,
        )
        return messages

    def serialize(self, result, include_hass_discovery=False, add_last_active_utc=False):
        if result is None or result is False:
            return []

        if not isinstance(result, dict):
            return self._flatten(
                result,
                base="",
                include_hass_discovery=include_hass_discovery,
            )

        payload = self.derived_values(result)
        if add_last_active_utc:
            payload["last_active_utc"] = self.utc_now_iso()

        return self._flatten(
            payload,
            base="",
            include_hass_discovery=include_hass_discovery,
        )

    def publish(self, mqtt_client, result, include_hass_discovery=False, add_last_active_utc=False):
        messages = self.serialize(
            result,
            include_hass_discovery=include_hass_discovery,
            add_last_active_utc=add_last_active_utc,
        )

        for message in messages:
            if self.logger:
                self.logger.debug(
                    "Send data: %s on topic: %s, retain flag: %s",
                    message.payload,
                    message.topic,
                    message.retain,
                )

            publish_result = mqtt_client.publish(
                message.topic,
                message.payload,
                qos=1,
                retain=message.retain,
            )

            if hasattr(publish_result, "wait_for_publish"):
                publish_result.wait_for_publish()

            if getattr(publish_result, "rc", 0) != 0:
                raise RuntimeError(
                    f"MQTT publish failed for topic {message.topic}, "
                    f"result code: {publish_result.rc}"
                )

        return messages
