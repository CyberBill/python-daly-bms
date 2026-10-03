import logging
import struct
import unittest
from unittest.mock import MagicMock

from dalybms import DalyBMS, DalyBMSSinowealth


class TestDalyBMSLogging(unittest.TestCase):
    def test_logs_rejected_response_frames(self):
        logger = logging.getLogger("test_daly_bms_logging")
        logger.setLevel(logging.DEBUG)

        bms = DalyBMS(request_retries=1, bms_id=13, logger=logger)
        bms.serial = MagicMock()
        bms.serial.is_open = True
        bms.serial.read.side_effect = [
            bytes([0xA5, 0x0A, 0x00, 0x00, 0x90]) + b"\x00" * 7 + b"\x00",
            b"",
        ]
        bms.serial.write.return_value = True

        with self.assertLogs(logger, level="DEBUG") as captured:
            bms._read("90")

        logs = "\n".join(captured.output)
        self.assertIn("raw response", logs)
        self.assertIn("discarding response", logs)

    def test_bms_id_and_address_are_packed_as_nibbles(self):
        bms_rs485 = DalyBMS(request_retries=1, address=4, bms_id=1)
        rs485_packet = bms_rs485._format_message("94")
        self.assertEqual(rs485_packet[:4].hex(), "a5409408")
        self.assertEqual(len(rs485_packet), 13)

        bms_uart = DalyBMS(request_retries=1, address=8, bms_id=1)
        uart_packet = bms_uart._format_message("95")
        self.assertEqual(uart_packet[:4].hex(), "a5809508")

    def test_rejects_bms_ids_above_16(self):
        with self.assertRaises(ValueError):
            DalyBMS(bms_id=17)

    def test_mosfet_status_reports_remaining_capacity(self):
        bms = DalyBMS()
        response_data = struct.pack(
            ">b??Bl",
            1,
            True,
            False,
            0,
            43_210,
        )

        result = bms.get_mosfet_status(response_data=response_data)
        assert result is not False

        self.assertEqual(result["mode"], "charging")
        self.assertTrue(result["charging_mosfet"])
        self.assertFalse(result["discharging_mosfet"])
        self.assertEqual(result["remaining_capacity_ah"], 43.21)
        self.assertNotIn("capacity_ah", result)

    def test_rated_parameters_decode_capacity_and_cell_voltage(self):
        bms = DalyBMS()
        response_data = bytes.fromhex("00014ff000000e74")

        result = bms.get_rated_parameters(response_data=response_data)

        self.assertEqual(
            result,
            {
                "rated_capacity_ah": 86.0,
                "rated_cell_voltage": 3.7,
            },
        )

    def test_sinowealth_uses_rated_capacity_name(self):
        bms = DalyBMSSinowealth()
        bms._read = MagicMock(
            side_effect=[86_000, 43_210, "0" * 16]
        )

        result = bms.get_mosfet_status()

        self.assertEqual(result["rated_capacity_ah"], 86.0)
        self.assertEqual(result["remaining_capacity_ah"], 43.21)
        self.assertNotIn("full_capacity_ah", result)

    def test_set_soc_encodes_100_percent_and_returns_success(self):
        bms = DalyBMS()
        bms._read_request = MagicMock(return_value=b"\x00" * 8)

        result = bms.set_soc(100)

        self.assertTrue(result)
        bms._read_request.assert_called_once_with(
            "21",
            extra="00000000000003E8",
        )


if __name__ == "__main__":
    unittest.main()
