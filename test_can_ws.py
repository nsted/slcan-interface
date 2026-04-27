#!/usr/bin/env python3
"""
test_can_ws.py — Motor enable/disable toggle via WebSocket gateway
XIAO ESP32S3 / can-gateway

Alternately enables and disables the motor every 2 seconds.

Usage:
    python3 test_can_ws.py [host]      # default: can-gw.local
    python3 test_can_ws.py 192.168.1.x

Requirements:
    pip install websockets
"""

import asyncio
import signal
import sys
import websockets

HOST     = sys.argv[1] if len(sys.argv) > 1 else "can-gw.local"
WS_URL   = f"ws://{HOST}:8080"
MOTOR_ID = 0x001
INTERVAL = 2.0

# ── MIT protocol helpers ──────────────────────────────────────────────────────
def mit_enable_bytes():
    return bytes([0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFC])

def mit_disable_bytes():
    return bytes([0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFD])

def make_frame(can_id: int, data: bytes) -> str:
    return f"t{can_id:03X}{len(data)}{data.hex().upper()}"

# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    print(f"Connecting to {WS_URL} …")
    try:
        async with websockets.connect(WS_URL, ping_interval=None, open_timeout=5) as ws:
            print("Connected. Toggling motor enable/disable every 2s — Ctrl-C to stop.\n")
            enabled = False
            try:
                while True:
                    enabled = not enabled
                    if enabled:
                        print("ENABLE  →", flush=True)
                        await ws.send(make_frame(MOTOR_ID, mit_enable_bytes()))
                    else:
                        print("DISABLE →", flush=True)
                        await ws.send(make_frame(MOTOR_ID, mit_disable_bytes()))
                    await asyncio.sleep(INTERVAL)
            except (KeyboardInterrupt, asyncio.CancelledError):
                print("\nInterrupted — disabling motor...")
                raise
            finally:
                try:
                    await ws.send(make_frame(MOTOR_ID, mit_disable_bytes()))
                    await asyncio.sleep(0.1)
                except Exception:
                    pass
    except OSError as e:
        print(f"\nCould not connect: {e}")
        print(f"Is the device on WiFi? Try: python3 {sys.argv[0]} <ip_address>")

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    task = loop.create_task(main())
    loop.add_signal_handler(signal.SIGINT,  task.cancel)
    loop.add_signal_handler(signal.SIGTERM, task.cancel)
    try:
        loop.run_until_complete(task)
    except (asyncio.CancelledError, SystemExit):
        pass
    finally:
        loop.close()
