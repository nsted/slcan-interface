# slcan-interface

SLCAN CAN bus gateway firmware for the **XIAO ESP32S3**, with two transport options:

- **USB Serial** — plug-and-play with `python-can`, `slcand`, Wireshark, and any other SLCAN-compatible tool
- **WiFi + WebSocket** — same SLCAN text protocol over a WebSocket connection for wireless use

---

## Hardware

| Signal | Pin | GPIO |
|--------|-----|------|
| CAN TX | D6  | 43   |
| CAN RX | D7  | 44   |

A CAN transceiver (e.g. SN65HVD230 or TJA1050) is required between the ESP32S3 and the CAN bus.

---

## Firmware setup

### Dependencies

Install via Arduino Library Manager:
- [ESP32-TWAI-CAN](https://github.com/handmade0octopus/ESP32-TWAI-CAN)
- [arduinoWebSockets](https://github.com/Links2004/arduinoWebSockets) *(WebSocket mode only)*

### Transport selection

At the top of `slcan-interface.ino`:

```cpp
// Comment out to use WebSocket instead
#define USE_SERIAL
```

| Mode | `USE_SERIAL` |
|------|-------------|
| USB Serial (SLCAN) | defined ✓ |
| WiFi + WebSocket   | commented out |

### WiFi credentials *(WebSocket mode only)*

```cpp
const char *ssid     = "YOUR_SSID";
const char *password = "YOUR_PASSWORD";
```

The device advertises itself as `can-gw.local` via mDNS.

---

## SLCAN protocol

| Command | Description |
|---------|-------------|
| `S<n>` | Set CAN speed: S0=10k S3=100k S4=125k S5=250k S6=500k S8=1M |
| `O` | Open channel |
| `C` | Close channel |
| `t<III><N><DD…>` | Transmit standard frame (11-bit ID) |
| `T<IIIIIIII><N><DD…>` | Transmit extended frame (29-bit ID) |
| `F` | Read status flags |
| `V` / `v` / `N` | Version / serial number |

Responses: `\r` = OK, `\a` = error, `z`/`Z` = TX ack, `t`/`T` = received frame

---

## Python test scripts

### Requirements

```bash
pip install python-can websockets
```

### Serial (USB) — `test_can_serial.py`

```bash
python3 test_can_serial.py [port]
python3 test_can_serial.py /dev/cu.usbmodem101
```

Default port: `/dev/cu.usbmodem101`

### WebSocket (WiFi) — `test_can_ws.py`

```bash
python3 test_can_ws.py [host]
python3 test_can_ws.py can-gw.local   # default
python3 test_can_ws.py 192.168.1.50   # by IP
```

Both scripts send a **motor disable command on exit**, including on Ctrl-C and SIGTERM.

---

## Manual use via Serial monitor

You can drive the gateway directly by typing SLCAN commands into any serial terminal (Arduino Serial Monitor, `screen`, `minicom`, etc.) at **115200 baud with line endings set to CR or NL+CR**.

Quick start sequence:

```
S8       → set 1 Mbps
O        → open channel
t0013FFFFFFFFFFFFFFFF FC    → enable motor (ID 0x001, 8 bytes)
t0013FFFFFFFFFFFFFFFF FD    → disable motor
C        → close channel
```

The device responds with `\r` (CR) for success and `\a` (BEL) for errors. Received CAN frames are printed automatically as `t<ID><N><data>\r`.

> **Arduino Serial Monitor note:** set the line ending dropdown to **"Carriage Return"** (CR only). The "Both NL & CR" option works too; "Newline only" does not.

---

## Latency logging

Uncomment `#define LOG_LATENCY` in the firmware to print per-frame timing over Serial:

```
ser_rx=120us twai_rtt=95us ser_tx=45us total=260us
```

---

## License

MIT
