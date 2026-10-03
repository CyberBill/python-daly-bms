import json
import unittest

from dalybms import DalyBMSMQTT


class TestDalyBMSMQTT(unittest.TestCase):
    def test_serialize_flattens_raw_payload_and_adds_hass_discovery(self):
        adapter = DalyBMSMQTT(
            device_id="daly_123",
            device_name="Daly BMS 123",
            topic_root="battery/daly/123",
            serial_number="123ABC",
        )

        payload = {
            "soc": {"total_voltage": 57.7, "soc_percent": 99.1},
            "status": {"cells": 14},
            "cell_voltage_range": {
                "highest_voltage": 3.790,
                "lowest_voltage": 3.710,
            },
        }

        messages = adapter.serialize(payload, include_hass_discovery=True)

        topics = {message.topic for message in messages}

        self.assertIn("battery/daly/123/soc/total_voltage", topics)
        self.assertIn("battery/daly/123/status/cells", topics)
        self.assertIn("battery/daly/123/cell_voltage_range/spread", topics)
        self.assertIn(
            "homeassistant/sensor/daly_123/soc_total_voltage/config",
            topics,
        )
        self.assertIn(
            "homeassistant/sensor/daly_123/cell_voltage_range_spread/config",
            topics,
        )

        value_by_topic = {
            message.topic: message.payload for message in messages
        }
        self.assertEqual(value_by_topic["battery/daly/123/soc/total_voltage"], 57.7)
        self.assertEqual(value_by_topic["battery/daly/123/status/cells"], 14)
        self.assertEqual(value_by_topic["battery/daly/123/cell_voltage_range/spread"], 0.08)

        self.assertTrue(all(message.retain for message in messages))

    def test_last_active_timestamp_uses_timestamp_device_class(self):
        adapter = DalyBMSMQTT(
            device_id="daly_123",
            device_name="Daly BMS 123",
            topic_root="battery/daly/123",
        )

        messages = adapter.serialize(
            {"last_active_utc": "2026-10-02T16:21:43Z"},
            include_hass_discovery=True,
        )

        config = next(
            message
            for message in messages
            if message.topic
            == "homeassistant/sensor/daly_123/last_active_utc/config"
        )

        payload = json.loads(config.payload)
        self.assertEqual(payload["device_class"], "timestamp")
        self.assertEqual(
            payload["state_topic"],
            "battery/daly/123/last_active_utc",
        )

    def test_remaining_capacity_uses_ah_and_a_descriptive_name(self):
        adapter = DalyBMSMQTT(
            device_id="daly_123",
            device_name="Daly BMS 123",
            topic_root="battery/daly/123",
        )

        messages = adapter.serialize(
            {"mosfet_status": {"remaining_capacity_ah": 43.21}},
            include_hass_discovery=True,
        )

        config = next(
            message
            for message in messages
            if message.topic
            == (
                "homeassistant/sensor/daly_123/"
                "mosfet_status_remaining_capacity_ah/config"
            )
        )

        payload = json.loads(config.payload)
        self.assertEqual(payload["name"], "Remaining Capacity")
        self.assertEqual(payload["unit_of_measurement"], "Ah")
        self.assertEqual(payload["state_class"], "measurement")

    def test_rated_parameters_use_correct_names_and_units(self):
        adapter = DalyBMSMQTT(
            device_id="daly_123",
            device_name="Daly BMS 123",
            topic_root="battery/daly/123",
        )

        messages = adapter.serialize(
            {
                "rated_parameters": {
                    "rated_capacity_ah": 86.0,
                    "rated_cell_voltage": 3.7,
                }
            },
            include_hass_discovery=True,
        )

        payload_by_topic = {
            message.topic: json.loads(message.payload)
            for message in messages
            if message.topic.startswith("homeassistant/")
        }

        capacity = payload_by_topic[
            "homeassistant/sensor/daly_123/"
            "rated_parameters_rated_capacity_ah/config"
        ]
        voltage = payload_by_topic[
            "homeassistant/sensor/daly_123/"
            "rated_parameters_rated_cell_voltage/config"
        ]

        self.assertEqual(capacity["name"], "Rated Capacity")
        self.assertEqual(capacity["unit_of_measurement"], "Ah")
        self.assertEqual(voltage["name"], "Rated Cell Voltage")
        self.assertEqual(voltage["unit_of_measurement"], "V")


if __name__ == "__main__":
    unittest.main()
