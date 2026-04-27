#!/usr/bin/env python3
"""
test_can_serial.py — Motor enable/disable toggle via SLCAN serial
XIAO ESP32S3 / can-gateway

Alternately enables and disables the motor every 2 seconds.

Usage:
    python3 test_can_serial.py [port]
    python3 test_can_serial.py /dev/cu.usbmodem101

Requirements:
    pip install python-can
"""

import can
import signal
import sys
import time

PORT     = sys.argv[1] if len(sys.argv) > 1 else "/dev/cu.usbmodem101"
BAUD     = 115200
BITRATE  = 1_000_000
MOTOR_ID = 0x001
INTERVAL = 2.0

# ── MIT protocol helpers ──────────────────────────────────────────────────────
def mit_enable_bytes():
    return bytes([0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFC])

def mit_disable_bytes():
    return bytes([0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFD])

# ── Gateway ───────────────────────────────────────────────────────────────────
class Gateway:
    def __init__(self):
        self.bus = can.Bus(
            interface='slcan',
            channel=PORT,
            bitrate=BITRATE,
            ttyBaudrate=BAUD,
        )

    def send(self, can_id, data):
        msg = can.Message(arbitration_id=can_id, data=data, is_extended_id=False)
        self.bus.send(msg, timeout=0.05)

    def close(self):
        self.bus.shutdown()

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"Opening {PORT} at {BAUD} baud (CAN {BITRATE//1000} kbps)…")
    try:
        gw = Gateway()
    except Exception as e:
        print(f"Could not open SLCAN bus: {e}")
        sys.exit(1)

    def _disable_and_close():
        try:
            gw.send(MOTOR_ID, mit_disable_bytes())
            time.sleep(0.05)
        except Exception:
            pass
        gw.close()

    signal.signal(signal.SIGTERM, lambda s, f: (_disable_and_close(), sys.exit(0)))

    print("Toggling motor enable/disable every 2s — Ctrl-C to stop.\n")
    enabled = False
    try:
        while True:
            enabled = not enabled
            if enabled:
                print("ENABLE  →", flush=True)
                gw.send(MOTOR_ID, mit_enable_bytes())
            else:
                print("DISABLE →", flush=True)
                gw.send(MOTOR_ID, mit_disable_bytes())
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        print("\nInterrupted — disabling motor.")
    finally:
        _disable_and_close()

if __name__ == "__main__":
    main()
