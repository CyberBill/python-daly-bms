AGENTS.md — Project overview and how it works

Overview
--------
This project implements a Python client for Daly Smart BMS devices (RS-485 / UART). The primary library is the `dalybms` package which exposes `DalyBMS` (and `DalyBMSSinowealth`) classes for querying telemetry and sending control commands.

Key components
--------------
- `dalybms/daly_bms.py` — Main Daly BMS protocol implementation. Contains `DalyBMS` class with methods such as `get_soc()`, `get_cell_voltages()`, `get_status()`, `get_errors()`, and control methods like `set_soc()` and `set_charge_mosfet()`.
- `bin/daly-bms-cli` — CLI driver that demonstrates using the library, supports RS-485 and UART, and can publish telemetry to MQTT / Home Assistant.
- `requirements.txt` — External dependencies. Currently requires `pyserial`.

How it communicates
-------------------
- The library formats and sends 13-byte request frames (padded hex messages) to the BMS over serial and reads 13-byte response frames, validating a checksum and parsing payloads with `struct.unpack`.
- The CLI includes optional MQTT publishing and Home Assistant discovery payloads; you can use this to publish telemetry into Home Assistant without extra integration code.

Notes for contributors / agents
------------------------------
- Serial safety: RS-485 is a shared bus. When implementing multi-BMS polling, ensure a single adapter instance controls the bus and serialize access using locks or a single-threaded scheduler per adapter.
- Error handling: The codebase should validate frame lengths and CRCs before unpacking or interpreting fields. Device disconnects and serial exceptions should be handled gracefully.
- Extensibility: To support 28 BMS devices across multiple adapters, implement an adapter manager that shares a `serial` instance (or one per physical adapter) and a `DalyBMS` thin client that references the adapter for I/O.

Running locally
---------------
- Create and activate a venv (the project includes a configured venv in `.venv` if using the workspace dev setup).
- Install dependencies: `pip install -r requirements.txt` (or use the provided venv command).
- Example CLI usage:

    bin/daly-bms-cli -d /dev/ttyUSB0 --status

- For MQTT/Home Assistant publishing, run with `--mqtt --mqtt-hass --mqtt-broker <host>` and configure broker credentials.

Testing and validation
----------------------
- Add smoke tests that mock `serial.Serial` to verify message formatting and response parsing for each `DalyBMS` method.
- When testing with physical hardware, run the CLI against a single adapter first and validate responses before scaling to multiple adapters.

Contributing
------------
- Follow the project's coding style and add unit tests for any behavioral changes.
- When changing parsing logic, include sample binary frames in test fixtures to prevent regressions.

Contact
-------
- Maintainer: see project `PKG-INFO` or `SOURCES.txt` for metadata.

