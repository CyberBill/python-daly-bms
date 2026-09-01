#!/usr/bin/env python3
import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

from dalybms import DalyBMS
from dalybms import DalyBMSSinowealth


@dataclass
class MQTTContext:
    args: object
    logger: object
    mqtt_client: object
    topic_root: str
    device_id: str
    device_name: str
    serial_number: str | None = None

def sanitize_identifier(value):
    """
    Convert a serial number or label into a safe MQTT/HA identifier.
    """
    return re.sub(
        r"[^a-z0-9_]+",
        "_",
        str(value).lower(),
    ).strip("_")


def humanize_entity_name(base):
    """
    Convert an MQTT path into a concise Home Assistant entity name.

    Examples:
      /soc/total_voltage     -> Total Voltage
      /cell_voltages/12      -> Cell 12 Voltage
      /temperatures/1        -> Temperature 1
    """
    parts = [
        part
        for part in base.strip("/").split("/")
        if part
    ]

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

def build_mqtt_hass_config_discovery(base, mqtt_context):
    entity_path = sanitize_identifier(
        base.replace("/", "_")
    )

    entity_unique_id = (
        f"{mqtt_context.device_id}_{entity_path}"
    )

    hass_config_topic = (
        f"homeassistant/sensor/"
        f"{mqtt_context.device_id}/"
        f"{entity_path}/config"
    )

    state_topic = (
        f"{mqtt_context.topic_root}{base}"
    )

    hass_config_data = {
        "unique_id": entity_unique_id,
        "name": humanize_entity_name(base),
        "state_topic": state_topic,
    }

    if "soc_percent" in base:
        hass_config_data["device_class"] = "battery"
        hass_config_data["unit_of_measurement"] = "%"
        hass_config_data["state_class"] = "measurement"

    elif (
        "voltage" in base
        and not (
            "lowest_cell" in base
            or "highest_cell" in base
        )
    ):
        hass_config_data["device_class"] = "voltage"
        hass_config_data["unit_of_measurement"] = "V"
        hass_config_data["state_class"] = "measurement"
        hass_config_data[
            "suggested_display_precision"
        ] = 3

    elif "current" in base:
        hass_config_data["device_class"] = "current"
        hass_config_data["unit_of_measurement"] = "A"
        hass_config_data["state_class"] = "measurement"

    elif (
        "temperature" in base
        and "sensor" not in base
    ):
        hass_config_data["device_class"] = "temperature"
        hass_config_data["unit_of_measurement"] = "°C"
        hass_config_data["state_class"] = "measurement"
        hass_config_data[
            "suggested_display_precision"
        ] = 1

    elif "capacity" in base:
        hass_config_data["unit_of_measurement"] = "Ah"
        hass_config_data["state_class"] = "measurement"

    hass_device = {
        "identifiers": [mqtt_context.device_id],
        "manufacturer": "Daly",
        "model": "Smart BMS",
        "name": mqtt_context.device_name,
        "serial_number": mqtt_context.serial_number,
    }

    hass_config_data["device"] = hass_device

    return (
        hass_config_topic,
        json.dumps(hass_config_data),
    )


def mqtt_single_out(topic, data, mqtt_context, retain=False):
    mqtt_context.logger.debug(
        f'Send data: {data} on topic: {topic}, retain flag: {retain}'
    )

    publish_result = mqtt_context.mqtt_client.publish(
        topic,
        data,
        qos=1,
        retain=retain,
    )

    publish_result.wait_for_publish()

    if publish_result.rc != 0:
        raise RuntimeError(
            f'MQTT publish failed for topic {topic}, '
            f'result code: {publish_result.rc}'
        )


def mqtt_iterator(result, mqtt_context, base=''):
    for key in result.keys():
        current_base = f'{base}/{key}' if base else f'/{key}'

        if isinstance(result[key], dict):
            mqtt_iterator(
                result[key],
                mqtt_context,
                current_base,
            )
        else:
            if mqtt_context.args and mqtt_context.args.mqtt_hass:
                mqtt_context.logger.debug('Sending out hass discovery message')
                topic, output = build_mqtt_hass_config_discovery(
                    current_base,
                    mqtt_context,
                )
                mqtt_single_out(topic, output, mqtt_context, retain=True)

            if isinstance(result[key], list):
                val = json.dumps(result[key])
            else:
                val = result[key]

            mqtt_single_out(
                f"{mqtt_context.topic_root}{current_base}",
                val,
                mqtt_context,
                retain=True,
            )


def utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def with_last_active_utc(result, mqtt_context=None):
    if not mqtt_context or not mqtt_context.args or not mqtt_context.args.mqtt:
        return result

    if result is None or result is False:
        return result

    if not isinstance(result, dict):
        return result

    updated_result = dict(result)
    updated_result["last_active_utc"] = utc_now_iso()
    return updated_result


def print_result(result, mqtt_context=None):
    result = with_last_active_utc(result, mqtt_context)

    if mqtt_context and mqtt_context.args.mqtt:
        mqtt_iterator(result, mqtt_context)
    else:
        print(json.dumps(result, indent=2))


def discover_bms_devices(args, logger, address, silent_logger, bitfield=0xFFFFFFFF):
    """
    Scan Daly RS485 BMS IDs represented by the set bits in a 32-bit mask.

    A BMS is considered present when it returns valid board information.
    Additional identity fields are queried only after that first response.
    """
    discovered = []

    print(f"Scanning Daly BMS IDs from mask 0x{bitfield:08X} on {args.device}...")
    print()

    scan_mask = bitfield
    bms_id = 1
    while scan_mask != 0:
        if scan_mask & 1:
            logger.debug("Scanning BMS ID %d", bms_id)

            candidate = DalyBMS(
                request_retries=1,
                address=address,
                bms_id=bms_id,
                logger=silent_logger,
            )

            try:
                candidate.connect(device=args.device, timeout=0.05)
                candidate.serial.timeout = 0.05
                candidate.serial.writeTimeout = 0.05

                board_info = candidate.get_board_info()

                if not board_info:
                    print(".", end="", flush=True)
                    bms_id += 1
                    scan_mask >>= 1
                    continue

                print(f"[{bms_id}]", end="", flush=True)

                serial_number = candidate.get_serial_number()
                battery_code = candidate.get_battery_code()

                device = {
                    "bms_id": bms_id,
                    "board_number": board_info.get("board_number"),
                    "slave_number": board_info.get("slave_number"),
                    "serial_number": serial_number or None,
                    "battery_code": battery_code or None,
                }

                discovered.append(device)

            except Exception:
                print(".", end="", flush=True)

            finally:
                if getattr(candidate, "serial", None) and candidate.serial.is_open:
                    candidate.disconnect()

        bms_id += 1
        scan_mask >>= 1

    print()
    print()

    for device in discovered:
        print(f"ID {device['bms_id']}")
        print(f"  Board number: {device['board_number']}")
        print(f"  Slave number: {device['slave_number']}")
        print(
            f"  Serial number: "
            f"{device['serial_number'] or 'Unavailable'}"
        )
        print(
            f"  Battery code: "
            f"{device['battery_code'] or 'Unavailable'}"
        )
        print()

    print(
        f"Found {len(discovered)} BMS "
        f"{'device' if len(discovered) == 1 else 'devices'}."
    )

    return discovered

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-d", "--device",
                        help="RS485 device, e.g. /dev/ttyUSB0",
                        type=str, required=True)
    parser.add_argument("--uart", help="UART instead of RS485", action="store_true")
    parser.add_argument("--sinowealth", help="BMS with Sinowealth chip", action="store_true")
    parser.add_argument("--status", help="show status", action="store_true")
    parser.add_argument("--soc", help="show voltage, current, SOC", action="store_true")
    parser.add_argument("--mosfet", help="show mosfet status", action="store_true")
    parser.add_argument("--cell-voltages", help="show cell voltages", action="store_true")
    parser.add_argument("--temperatures", help="show temperature sensor values", action="store_true")
    parser.add_argument("--balancing", help="show cell balancing status", action="store_true")
    parser.add_argument("--errors", help="show BMS errors", action="store_true")
    parser.add_argument("--battery-code",help="show configured battery code",action="store_true")
    parser.add_argument("--serial-number",help="show BMS serial number",action="store_true")
    parser.add_argument("--all", help="show all", action="store_true")
    parser.add_argument("--check", help="Nagios style check", action="store_true")
    parser.add_argument("--set-charge-mosfet", help="'on' or 'off'", type=str)
    parser.add_argument("--set-discharge-mosfet", help="'on' or 'off'", type=str)
    parser.add_argument("--set-soc", help="'0.0' to '100.0'", type=str)
    parser.add_argument("--restart", help="restart bms", action="store_true")
    parser.add_argument("--retry", help="retry X times if the request fails, default 5", type=int, default=5)
    parser.add_argument("--bms-id", help="BMS ID (1-16), default 1", type=int, default=1)
    parser.add_argument("--verbose", help="Verbose output", action="store_true")

    parser.add_argument("--mqtt", help="Write output to MQTT", action="store_true")
    parser.add_argument("--mqtt-hass", help="MQTT Home Assistant Mode", action="store_true")

    parser.add_argument("--mqtt-topic",
                        help=(
                            "MQTT topic root. If omitted, defaults to "
                            "battery/daly/<serial-number>"
                        ),
                        type=str,
                        default=None)

    parser.add_argument("--mqtt-broker",
                        help="MQTT broker (server). default localhost",
                        type=str,
                        default="localhost")

    parser.add_argument("--mqtt-port",
                        help="MQTT port. default 1883",
                        type=int,
                        default=1883)

    parser.add_argument("--mqtt-user",
                        help="Username to authenticate MQTT with",
                        type=str)

    parser.add_argument("--mqtt-password",
                        help="Password to authenticate MQTT with",
                        type=str)

    parser.add_argument(
        "--discover",
        nargs='?',
        const=0xFFFFFFFF,
        default=None,
        type=lambda value: int(value, 0),
        help="Scan RS485 BMS IDs represented by a 32-bit bitmask; default is 0xFFFFFFFF",
    )

    args = parser.parse_args()

    log_format = '%(levelname)-8s [%(filename)s:%(lineno)d] %(message)s'
    if args.verbose:
        level = logging.DEBUG
    else:
        level = logging.WARNING

    logging.basicConfig(level=level, format=log_format, datefmt='%H:%M:%S')
    logger = logging.getLogger()

    silent_logger = logging.getLogger()
    silent_logger.setLevel(logging.CRITICAL)
    silent_logger.propagate = False

    bms = None

    if args.uart:
        address = 8
    else:
        address = 4

    if args.discover is None:
        if args.sinowealth:
            bms = DalyBMSSinowealth(
                request_retries=args.retry,
                logger=logger,
            )
        else:
            bms = DalyBMS(
                request_retries=args.retry,
                address=address,
                bms_id=args.bms_id,
                logger=logger,
            )

        bms.connect(device=args.device)

    result = False

    mqtt_context = None
    mqtt_client = None

    if args.mqtt:
        bms_serial_number = bms.get_serial_number()

        if not bms_serial_number:
            logger.error(
                "Unable to read BMS serial number; "
                "cannot create a stable MQTT identity"
            )
            bms.disconnect()
            sys.exit(1)

        serial_identifier = sanitize_identifier(
            bms_serial_number
        )

        mqtt_device_id = f"daly_{serial_identifier}"
        mqtt_device_name = (
            f"Daly BMS {bms_serial_number}"
        )

        mqtt_topic_root = (
            args.mqtt_topic.rstrip("/")
            if args.mqtt_topic
            else f"battery/daly/{bms_serial_number}"
        )

        import paho.mqtt.client as paho

        mqtt_client = paho.Client()
        mqtt_client.enable_logger(logger)
        mqtt_client.username_pw_set(args.mqtt_user, args.mqtt_password)
        mqtt_client.connect(args.mqtt_broker, port=args.mqtt_port)
        mqtt_client.loop_start()

        mqtt_context = MQTTContext(
            args=args,
            logger=logger,
            mqtt_client=mqtt_client,
            topic_root=mqtt_topic_root,
            device_id=mqtt_device_id,
            device_name=mqtt_device_name,
            serial_number=bms_serial_number,
        )

    if args.discover is not None:
        if args.uart:
            print("--discover is only supported over RS485.")
            sys.exit(1)

        if args.sinowealth:
            print("--discover is not supported for Sinowealth devices.")
            sys.exit(1)

        result = discover_bms_devices(
            args=args,
            logger=logger,
            address=address,
            silent_logger=silent_logger,
            bitfield=args.discover,
        )
    if args.status:
        result = bms.get_status()
        print_result(result, mqtt_context)
    if args.soc:
        result = bms.get_soc()
        print_result(result, mqtt_context)
    if args.mosfet:
        result = bms.get_mosfet_status()
        print_result(result, mqtt_context)
    if args.cell_voltages:
        if not args.status:
            bms.get_status()
        result = bms.get_cell_voltages()
        print_result(result, mqtt_context)
    if args.temperatures:
        result = bms.get_temperatures()
        print_result(result, mqtt_context)
    if args.balancing:
        result = bms.get_balancing_status()
        print_result(result, mqtt_context)
    if args.errors:
        result = bms.get_errors()
        print_result(result, mqtt_context)
    if args.battery_code:
        result = bms.get_battery_code()
        print_result(result, mqtt_context)
    if args.serial_number:
        result = bms.get_serial_number()
        print_result(result, mqtt_context)
    if args.all:
        result = bms.get_all()
        print_result(result, mqtt_context)

    if mqtt_context and mqtt_context.args.mqtt and result and isinstance(result, dict):
        # Publish a retained fresh UTC timestamp for the most recent successful BMS contact.
        mqtt_single_out(
            f"{mqtt_context.topic_root}/last_active_utc",
            utc_now_iso(),
            mqtt_context,
            retain=True,
        )

    if args.check:
        status = bms.get_status()
        status_code = 0  # OK
        status_codes = ('OK', 'WARNING', 'CRITICAL', 'UNKNOWN')
        status_line = ''

        data = bms.get_soc()
        perfdata = []
        if data:
            for key, value in data.items():
                perfdata.append('%s=%s' % (key, value))

        # todo: read errors

        if status_code == 0:
            status_line = '%0.1f volt, %0.1f amper' % (data['total_voltage'], data['current'])

        print("%s - %s | %s" % (status_codes[status_code], status_line, " ".join(perfdata)))
        sys.exit(status_code)

    if args.set_charge_mosfet:
        if args.set_charge_mosfet == 'on':
            on = True
        elif args.set_charge_mosfet == 'off':
            on = False
        else:
            print("invalid value '%s', expected 'on' or 'off'" % args.set_charge_mosfet)
            sys.exit(1)

        result = bms.set_charge_mosfet(on=on)

    if args.set_discharge_mosfet:
        if args.set_discharge_mosfet == 'on':
            on = True
        elif args.set_discharge_mosfet == 'off':
            on = False
        else:
            print("invalid value '%s', expected 'on' or 'off'" % args.set_discharge_mosfet)
            sys.exit(1)

        result = bms.set_discharge_mosfet(on=on)

    if args.set_soc:
        try :
            v = float(args.set_soc)
        except :
            print("invalid value '%s', expected float value betwen 0 and 100" % args.set_soc)
            sys.exit(1)

        result = bms.set_soc(v)

    if args.restart:
        result = bms.restart()

        
    if mqtt_client:
        mqtt_client.disconnect()
        mqtt_client.loop_stop()

    if bms:
        bms.disconnect()

    if not result:
        sys.exit(1)

if __name__ == "__main__":
    main()