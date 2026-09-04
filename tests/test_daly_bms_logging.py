import logging
import unittest
from unittest.mock import MagicMock

from dalybms import DalyBMS


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


if __name__ == "__main__":
    unittest.main()
