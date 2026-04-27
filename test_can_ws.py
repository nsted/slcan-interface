#!/usr/bin/env python3
"""
test_can_ws.py — CAN Bus WebSocket Gateway Test
XIAO ESP32S3 / can-gateway

Tests WebSocket connectivity and CAN throughput against the gateway.
Motor protocol: CubeMars GL40 II MIT mode.

Usage:
    python3 test_can_ws.py [host]      # default: can-gw.local
    python3 test_can_ws.py 192.168.1.x

Requirements:
    pip install websockets
"""

import asyncio
import signal
import struct
import sys
import time
import websockets

# ── Config ────────────────────────────────────────────────────────────────────
HOST         = sys.argv[1] if len(sys.argv) > 1 else "can-gw.local"
WS_URL       = f"ws://{HOST}:8080"
MOTOR_ID     = 0x001   # GL40 II default CAN ID
MASTER_ID    = 0x000   # feedback ID

THROUGHPUT_FRAMES  = 200   # frames to send in throughput test
THROUGHPUT_DELAY_S = 0.005  # 5 ms between frames (~50 Hz burst)

# ── MIT protocol helpers ──────────────────────────────────────────────────────
# Reference: CubeMars AK series MIT protocol
# https://github.com/tmoore2016/cubemars_mit_mode

P_MIN, P_MAX   = -12.5, 12.5
V_MIN, V_MAX   = -30.0, 30.0
KP_MIN, KP_MAX =   0.0, 500.0
KD_MIN, KD_MAX =   0.0,   5.0
T_MIN, T_MAX   = -10.0,  10.0

def _float_to_uint(x, x_min, x_max, bits):
    span   = x_max - x_min
    x      = max(x_min, min(x_max, x))
    return int((x - x_min) / span * ((1 << bits) - 1))

def _uint_to_float(x, x_min, x_max, bits):
    return x_min + x / ((1 << bits) - 1) * (x_max - x_min)

def mit_enable_bytes():
    return bytes([0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFC])

def mit_disable_bytes():
    return bytes([0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFD])

def mit_command_bytes(pos=0.0, vel=0.0, kp=0.0, kd=1.0, torque=0.0):
    """Build 8-byte MIT mode command frame."""
    p  = _float_to_uint(pos,    P_MIN, P_MAX,   16)
    v  = _float_to_uint(vel,    V_MIN, V_MAX,   12)
    kp_ = _float_to_uint(kp,   KP_MIN, KP_MAX,  12)
    kd_ = _float_to_uint(kd,   KD_MIN, KD_MAX,  12)
    t  = _float_to_uint(torque, T_MIN, T_MAX,   12)
    d = bytearray(8)
    d[0] = p >> 8
    d[1] = p & 0xFF
    d[2] = v >> 4
    d[3] = ((v & 0xF) << 4) | (kp_ >> 8)
    d[4] = kp_ & 0xFF
    d[5] = kd_ >> 4
    d[6] = ((kd_ & 0xF) << 4) | (t >> 8)
    d[7] = t & 0xFF
    return bytes(d)

def parse_feedback(data_hex: str):
    """Parse MIT feedback hex string → (motor_id, pos, vel, torque)."""
    raw = bytes.fromhex(data_hex)
    if len(raw) < 6:
        return None
    motor_id = raw[0] >> 4
    p_raw = (raw[1] << 8) | raw[2]
    v_raw = (raw[3] << 4) | (raw[4] >> 4)
    t_raw = ((raw[4] & 0xF) << 8) | raw[5]
    pos = _uint_to_float(p_raw, P_MIN, P_MAX, 16)
    vel = _uint_to_float(v_raw, V_MIN, V_MAX, 12)
    tor = _uint_to_float(t_raw, T_MIN, T_MAX, 12)
    return motor_id, pos, vel, tor

# ── SLCAN WebSocket helpers ───────────────────────────────────────────────────
def make_raw(can_id: int, data: bytes) -> str:
    """SLCAN standard frame: tIIINDD… (11-bit ID, 3 hex chars)."""
    return f"t{can_id:03X}{len(data)}{data.hex().upper()}"

def parse_fb(msg: str):
    """Parse SLCAN frame 't/TIIIINDD…' → (can_id, data_hex_str) or None."""
    msg = msg.strip()
    if not msg or msg[0] not in ('t', 'T'):
        return None
    extd  = (msg[0] == 'T')
    id_len = 8 if extd else 3
    if len(msg) < 1 + id_len + 1:
        return None
    can_id   = int(msg[1:1 + id_len], 16)
    data_hex = msg[1 + id_len + 1:]   # skip DLC digit
    return can_id, data_hex

# ── Tests ─────────────────────────────────────────────────────────────────────
async def test_ping(ws):
    """Send enable frame, wait for any feedback within 500 ms."""
    print("\n── Test 1: Enable / ping ──")
    cmd = make_raw(MOTOR_ID, mit_enable_bytes())
    print(f"  TX  {cmd}")
    await ws.send(cmd)

    try:
        resp = await asyncio.wait_for(ws.recv(), timeout=0.5)
        print(f"  RX  {resp}")
        parsed = parse_fb(resp)
        if parsed:
            can_id, data_hex = parsed
            fb = parse_feedback(data_hex)
            if fb:
                mid, pos, vel, tor = fb
                print(f"  Motor {mid}: pos={pos:.3f} rad  vel={vel:.3f} rad/s  tor={tor:.3f} Nm")
        print("  PASS")
    except asyncio.TimeoutError:
        print("  (no feedback within 500 ms — motor may be off, but TX succeeded)")

async def test_throughput(ws):
    """
    Send THROUGHPUT_FRAMES MIT velocity commands, count feedback frames.
    Measures round-trip throughput.
    """
    print(f"\n── Test 2: Throughput ({THROUGHPUT_FRAMES} frames @ ~{1/THROUGHPUT_DELAY_S:.0f} Hz) ──")

    received = []
    done     = asyncio.Event()

    async def reader():
        while not done.is_set():
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=0.1)
                received.append((time.perf_counter(), msg))
            except asyncio.TimeoutError:
                pass

    reader_task = asyncio.create_task(reader())

    cmd_bytes = mit_command_bytes(vel=1.047, kd=1.0)
    t_start = time.perf_counter()
    for i in range(THROUGHPUT_FRAMES):
        cmd = make_raw(MOTOR_ID, cmd_bytes)
        await ws.send(cmd)
        await asyncio.sleep(THROUGHPUT_DELAY_S)

    # Wait up to 1s for trailing feedback
    await asyncio.sleep(1.0)
    done.set()
    await reader_task

    t_end = time.perf_counter()
    elapsed = t_end - t_start
    sent_hz = THROUGHPUT_FRAMES / elapsed
    recv_hz = len(received) / elapsed if elapsed > 0 else 0

    print(f"  Sent:     {THROUGHPUT_FRAMES} frames in {elapsed:.2f}s ({sent_hz:.1f} Hz)")
    print(f"  Received: {len(received)} frames ({recv_hz:.1f} Hz)")
    if received:
        print(f"  Sample RX: {received[0][1]}")
    print("  PASS" if len(received) > 0 else "  WARN: no feedback received (motor may be off)")

async def test_disable(ws):
    """Send disable frame."""
    print("\n── Test 3: Disable ──")
    cmd = make_raw(MOTOR_ID, mit_disable_bytes())
    print(f"  TX  {cmd}")
    await ws.send(cmd)
    await asyncio.sleep(0.2)
    print("  PASS (disable sent)")

async def test_raw_echo(ws):
    """Send a raw frame to a safe broadcast ID and check feedback."""
    print("\n── Test 4: Raw frame to 0x7FF (broadcast, no motor needed) ──")
    data = bytes([0xDE, 0xAD, 0xBE, 0xEF])
    cmd = make_raw(0x7FF, data)
    print(f"  TX  {cmd}")
    await ws.send(cmd)
    await asyncio.sleep(0.3)
    print("  PASS (frame transmitted)")

# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    print(f"Connecting to {WS_URL} …")
    try:
        async with websockets.connect(WS_URL, ping_interval=None, open_timeout=5) as ws:
            print(f"Connected.\n")
            try:
                await test_ping(ws)
                await test_raw_echo(ws)
                await test_throughput(ws)
                await test_disable(ws)
                print("\n── All tests complete ──")
            except (KeyboardInterrupt, asyncio.CancelledError):
                print("\nInterrupted — disabling motor...")
                raise
            finally:
                try:
                    await ws.send(make_raw(MOTOR_ID, mit_disable_bytes()))
                    await asyncio.sleep(0.1)
                except Exception:
                    pass
    except OSError as e:
        print(f"\nCould not connect: {e}")
        print(f"Is the device on WiFi? Try: python3 {sys.argv[0]} <ip_address>")
        sys.exit(1)

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    task = loop.create_task(main())
    loop.add_signal_handler(signal.SIGTERM, task.cancel)
    try:
        loop.run_until_complete(task)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        loop.close()
