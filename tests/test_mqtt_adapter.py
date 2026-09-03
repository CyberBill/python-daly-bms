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


if __name__ == "__main__":
    unittest.main()
