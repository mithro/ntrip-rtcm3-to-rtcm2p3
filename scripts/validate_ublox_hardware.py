#!/usr/bin/env python3
"""Definitive hardware validation: feed the service's RTCM 2.3 to a real u-blox.

Runs the service against AUSCORS, then streams the generated RTCM 2.3 corrections
into a u-blox receiver's UART while reading its NMEA. Success = the receiver
reports a differential fix (GGA fix-quality 2). This is the ground-truth check
that the generated corrections are valid and usable.

ten64-specific: the u-blox 7 is on /dev/ttyS1 (held by gpsd). This stops gpsd for
the test and restarts it afterwards. Run with sudo access:
  sudo systemctl stop gpsd gpsd.socket   # (script does this)
  uv run --with pyrtcm,pyserial python scripts/validate_ublox_hardware.py
"""
import os
import queue
import subprocess
import sys
import threading
import time

import serial

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rtcm3to2p3.service import Config, Service  # noqa: E402

PORT = os.environ.get("UBLOX_PORT", "/dev/ttyS1")
BAUD = int(os.environ.get("UBLOX_BAUD", "9600"))
DURATION = float(os.environ.get("UBLOX_TEST_SECONDS", "120"))
PW_FILE = os.environ.get("NTRIP_PW_FILE", "/home/tim/.gpsd-auscors-ntrip-passwd")


def main() -> None:
    pw = open(PW_FILE).read().strip()
    svc = Service(Config(listen_addresses=["127.0.0.1"], listen_port=0, upstream_password=pw))
    # we drive the caster ourselves; just run the converter via the clients
    stop = threading.Event()
    obs = threading.Thread(target=svc._obs_client.stream,
                           args=(svc.converter.feed_obs, stop), daemon=True)
    eph = threading.Thread(target=svc._eph_client.stream,
                           args=(svc.converter.feed_eph, stop), daemon=True)
    eph.start()
    obs.start()

    print("waiting for RTCM2.3 conversion to start ...")
    for _ in range(90):
        if svc.rtcm23_feed.available.is_set():
            break
        time.sleep(1)
    print(f"converting: station={svc.converter.gen.base}, "
          f"ephem={len(svc.converter.gen.ephemerides)}")

    subprocess.run(["sudo", "systemctl", "stop", "gpsd", "gpsd.socket"], check=False)
    time.sleep(1)
    best_quality = 0
    dgps_epochs = 0
    try:
        ser = serial.Serial(PORT, BAUD, timeout=1)
        # gpsd left the receiver in UBX mode; re-enable NMEA GGA/GSA output
        def ubx(cls, mid, payload=b""):
            body = bytes([cls, mid]) + len(payload).to_bytes(2, "little") + payload
            a = c = 0
            for x in body:
                a = (a + x) & 0xFF
                c = (c + a) & 0xFF
            return b"\xb5\x62" + body + bytes([a, c])
        ser.write(ubx(0x06, 0x01, bytes([0xF0, 0x00, 1])))  # NMEA GGA rate 1
        ser.write(ubx(0x06, 0x01, bytes([0xF0, 0x02, 1])))  # NMEA GSA rate 1
        time.sleep(1)
        sub = svc.rtcm23_feed.subscribe()
        deadline = time.time() + DURATION
        line = b""
        while time.time() < deadline:
            # forward any pending corrections to the receiver
            try:
                while True:
                    ser.write(sub.get_nowait())
            except queue.Empty:
                pass
            # read NMEA and look for GGA fix quality
            data = ser.read(256)
            if data:
                line += data
                while b"\n" in line:
                    one, _, line = line.partition(b"\n")
                    text = one.decode(errors="replace").strip()
                    if "GGA" in text:
                        fields = text.split(",")
                        if len(fields) > 6 and fields[6].isdigit():
                            q = int(fields[6])
                            best_quality = max(best_quality, q)
                            if q >= 2:
                                dgps_epochs += 1
                                print(f"  DGPS fix! GGA quality={q}  sats={fields[7]}")
        ser.close()
    finally:
        stop.set()
        subprocess.run(["sudo", "systemctl", "start", "gpsd"], check=False)

    print(f"\nbest GGA fix quality seen = {best_quality} "
          f"(2 = differential/DGPS);  DGPS epochs = {dgps_epochs}")
    print("RESULT:", "PASS - receiver used the corrections" if best_quality >= 2
          else "no DGPS fix observed")


if __name__ == "__main__":
    main()
