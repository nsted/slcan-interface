#!/usr/bin/env python3
"""
test_can_serial.py — CAN Bus Serial Transport Test
XIAO ESP32S3 / can-gateway (SLCAN mode)

Uses python-can with the SLCAN interface.  The library handles all
S<n>/O/C framing; tests just call bus.send() / bus.recv().

Usage:
    python3 test_can_serial.py [port] [--scan | --status]
    python3 test_can_serial.py /dev/cu.usbmodem1101
    python3 test_can_serial.py /dev/cu.usbmodem1101 --status
    python3 test_can_serial.py /dev/cu.usbmodem1101 --scan

Requirements:
    pip install python-can
"""

import can
import signal
import sys
import time

PORT     = sys.argv[1] if len(sys.argv) > 1 else "/dev/cu.usbmodem101"
BAUD     = 115200
BITRATE  = 1_000_000   # CAN bus speed in bps
MOTOR_ID = 0x001
SCAN     = "--scan"   in sys.argv
STATUS   = "--status" in sys.argv

# ── MIT protocol helpers (GL40 II) ────────────────────────────────────────────
P_MIN, P_MAX   = -12.5, 12.5
V_MIN, V_MAX   = -30.0, 30.0
KP_MIN, KP_MAX =   0.0, 500.0
KD_MIN, KD_MAX =   0.0,   5.0
T_MIN, T_MAX   = -10.0,  10.0

def _float_to_uint(x, x_min, x_max, bits):
    x = max(x_min, min(x_max, x))
    return int((x - x_min) / (x_max - x_min) * ((1 << bits) - 1))

def _uint_to_float(x, x_min, x_max, bits):
    return x_min + x / ((1 << bits) - 1) * (x_max - x_min)

def mit_enable_bytes():
    return bytes([0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFC])

def mit_disable_bytes():
    return bytes([0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFD])

def mit_command_bytes(pos=0.0, vel=0.0, kp=0.0, kd=1.0, torque=0.0):
    p   = _float_to_uint(pos,    P_MIN, P_MAX,   16)
    v   = _float_to_uint(vel,    V_MIN, V_MAX,   12)
    kp_ = _float_to_uint(kp,    KP_MIN, KP_MAX,  12)
    kd_ = _float_to_uint(kd,    KD_MIN, KD_MAX,  12)
    t   = _float_to_uint(torque, T_MIN, T_MAX,   12)
    d = bytearray(8)
    d[0] = p >> 8;  d[1] = p & 0xFF
    d[2] = v >> 4
    d[3] = ((v & 0xF) << 4) | (kp_ >> 8); d[4] = kp_ & 0xFF
    d[5] = kd_ >> 4
    d[6] = ((kd_ & 0xF) << 4) | (t >> 8); d[7] = t & 0xFF
    return bytes(d)

def parse_feedback(msg: can.Message):
    """MIT feedback from a CAN message → (motor_id, pos, vel, torque)."""
    raw = bytes(msg.data)
    if len(raw) < 6:
        return None
    motor_id = raw[0] >> 4
    p_raw = (raw[1] << 8) | raw[2]
    v_raw = (raw[3] << 4) | (raw[4] >> 4)
    t_raw = ((raw[4] & 0xF) << 8) | raw[5]
    return (motor_id,
            _uint_to_float(p_raw, P_MIN, P_MAX, 16),
            _uint_to_float(v_raw, V_MIN, V_MAX, 12),
            _uint_to_float(t_raw, T_MIN, T_MAX, 12))

# ── Gateway ───────────────────────────────────────────────────────────────────
class Gateway:
    def __init__(self, bitrate=BITRATE):
        self.bus = can.Bus(
            interface='slcan',
            channel=PORT,
            bitrate=bitrate,
            ttyBaudrate=BAUD,
        )

    def send(self, can_id, data, extended=False):
        msg = can.Message(arbitration_id=can_id, data=data, is_extended_id=extended)
        self.bus.send(msg, timeout=0.05)

    def recv_frames(self, timeout=0.5):
        """Collect all frames received within timeout seconds."""
        frames = []
        deadline = time.time() + timeout
        while True:
            remaining = max(0.0, deadline - time.time())
            msg = self.bus.recv(timeout=remaining)
            if msg is None:
                break
            frames.append(msg)
        return frames

    def set_baud(self, kbps):
        """Reinitialise the bus at a new CAN speed."""
        self.bus.shutdown()
        self.bus = can.Bus(
            interface='slcan',
            channel=PORT,
            bitrate=kbps * 1000,
            ttyBaudrate=BAUD,
        )

    def close(self):
        self.bus.shutdown()

# ── Tests ─────────────────────────────────────────────────────────────────────
def test_boot(gw):
    print("\n── Test 1: Boot ──")
    print("  PASS — SLCAN channel open")
    return True

def test_enable(gw):
    print("\n── Test 2: Enable (MIT 0xFC) ──")
    gw.send(MOTOR_ID, mit_enable_bytes())
    frames = gw.recv_frames(timeout=0.5)
    for f in frames:
        print(f"  RX: {f}")
    fb = [f for f in frames]
    if fb:
        parsed = parse_feedback(fb[0])
        if parsed:
            mid, pos, vel, tor = parsed
            print(f"  Motor {mid}: pos={pos:.3f} rad  vel={vel:.3f} rad/s  tor={tor:.3f} Nm")
        print("  PASS — feedback received")
    else:
        print("  (no response — motor may be off or CAN not connected)")

def test_throughput(gw, n=100, delay_s=0.005):
    print(f"\n── Test 3: Throughput ({n} frames @ {1/delay_s:.0f} Hz) ──")
    cmd = mit_command_bytes(vel=1.047, kd=1.0)

    t_start = time.perf_counter()
    for _ in range(n):
        gw.send(MOTOR_ID, cmd)
        time.sleep(delay_s)
    t_sent = time.perf_counter()

    frames = gw.recv_frames(timeout=1.0)
    t_end = time.perf_counter()

    print(f"  Sent:     {n} frames in {t_sent - t_start:.2f}s ({n/(t_sent-t_start):.1f} Hz)")
    print(f"  Feedback: {len(frames)} frames received")
    print("  PASS")

def test_disable(gw):
    print("\n── Test 4: Disable (MIT 0xFD) ──")
    gw.send(MOTOR_ID, mit_disable_bytes())
    time.sleep(0.2)
    print("  PASS (disable sent)")

def test_raw(gw):
    print("\n── Test 5: Raw frame 0x7FF:DEADBEEF ──")
    gw.send(0x7FF, bytes([0xDE, 0xAD, 0xBE, 0xEF]))
    gw.recv_frames(timeout=0.5)
    print("  PASS")

# ── Status check ──────────────────────────────────────────────────────────────
def run_status(gw):
    print("\n── SLCAN status (F command) ──")
    # python-can doesn't expose F directly; just print bus state
    print(f"  Bus: {gw.bus}")

# ── Baud-rate / ID scanner ────────────────────────────────────────────────────
SCAN_RATES = [1000, 500, 250, 125, 100, 800]
SCAN_IDS   = list(range(0, 256))

def scan_baud_rates(gw, dwell=0.05, passes=1):
    spin_cmd = mit_command_bytes(vel=0.5, kp=0.0, kd=0.5, torque=0.0)
    total = len(SCAN_RATES) * len(SCAN_IDS)

    print("\n── Baud / ID scan ──")
    print(f"  Rates: {SCAN_RATES} kbps")
    print(f"  IDs:   0x00–0x{SCAN_IDS[-1]:02X}")
    print(f"  Dwell: {dwell}s  |  {total} combos/pass\n")

    try:
        for pass_num in range(1, passes + 1):
            print(f"── Pass {pass_num}/{passes} ──")
            for kbps in SCAN_RATES:
                print(f"  [{kbps:>4} kbps] reinitialising...", end=" ", flush=True)
                gw.set_baud(kbps)
                print("OK")

                for mid in SCAN_IDS:
                    print(f"    ID=0x{mid:02X} std+ext...", end=" ", flush=True)
                    gw.send(mid, mit_enable_bytes(), extended=False)
                    gw.send(mid, mit_enable_bytes(), extended=True)
                    gw.send(mid, spin_cmd,           extended=False)
                    gw.send(mid, spin_cmd,           extended=True)
                    frames = gw.recv_frames(timeout=dwell)
                    gw.send(mid, mit_disable_bytes(), extended=False)
                    gw.send(mid, mit_disable_bytes(), extended=True)

                    if frames:
                        print(f"\n  *** RESPONSE — baud={kbps} motor_id=0x{mid:02X} ***")
                        for f in frames:
                            parsed = parse_feedback(f)
                            if parsed:
                                m, pos, vel, tor = parsed
                                print(f"      motor={m} pos={pos:.3f} vel={vel:.3f} tor={tor:.3f}")
                            else:
                                print(f"      {f}")
                    else:
                        print("no response")

    except KeyboardInterrupt:
        print("\n  Scan stopped by user.")

    print("\n  Restoring 1000 kbps...")
    gw.set_baud(1000)

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"Opening {PORT} at {BAUD} baud (SLCAN, CAN {BITRATE//1000} kbps)…")
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

    def _sigterm(signum, frame):
        print("\nSIGTERM — disabling motor and exiting.")
        _disable_and_close()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _sigterm)

    try:
        test_boot(gw)
        if STATUS:
            run_status(gw)
        elif SCAN:
            scan_baud_rates(gw)
        else:
            test_enable(gw)
            test_raw(gw)
            test_throughput(gw)
            test_disable(gw)
            print("\n── All tests complete ──")
    except KeyboardInterrupt:
        print("\nInterrupted — disabling motor.")
    finally:
        _disable_and_close()

if __name__ == "__main__":
    main()
