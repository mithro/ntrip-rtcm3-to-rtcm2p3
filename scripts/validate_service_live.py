#!/usr/bin/env python3
"""Run the full service against AUSCORS and decode its RTCM2.3 mount with gpsdecode.

Starts the Service in-process, waits for the converted mount to go live, pulls it
as an NTRIP client, and pipes the bytes to gpsd's gpsdecode. Success = gpsdecode
reports Type 1 corrections with sane fields.

Run:  uv run --with pyrtcm python scripts/validate_service_live.py
"""
import os
import socket
import subprocess
import threading
import time

from rtcm3to2p3.service import Config, Service


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def main() -> None:
    port = _free_port()
    pw = open(os.path.expanduser("~/.gpsd-auscors-ntrip-passwd")).read().strip()
    config = Config(listen_addresses=["127.0.0.1"], listen_port=port, upstream_password=pw)
    svc = Service(config)
    stop = threading.Event()
    threading.Thread(target=svc.run, args=(stop,), daemon=True).start()

    print("waiting for the RTCM2.3 mount to go live ...")
    for _ in range(90):
        if svc.rtcm23_feed.available.is_set():
            break
        time.sleep(1)
    else:
        print("FAILED: no RTCM2.3 produced")
        stop.set()
        return
    print(f"RTCM2.3 live after conversion ({svc.converter.messages_out} msgs so far); "
          f"station={svc.converter.gen.base}, ephem={len(svc.converter.gen.ephemerides)}")

    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    s.sendall(f"GET /{config.rtcm23_mount} HTTP/1.1\r\nHost: x\r\n"
              f"Ntrip-Version: Ntrip/2.0\r\nUser-Agent: v\r\n\r\n".encode())
    s.settimeout(15)
    hdr = b""
    while b"\r\n\r\n" not in hdr:
        hdr += s.recv(1)
    data = bytearray()
    t0 = time.time()
    while time.time() - t0 < 12:
        try:
            data += s.recv(4096)
        except TimeoutError:
            break
    s.close()
    stop.set()

    print(f"\ncollected {len(data)} bytes of RTCM2.3; decoding with gpsdecode ...\n")
    p = subprocess.run(["gpsdecode"], input=bytes(data), capture_output=True, timeout=15)
    lines = [ln for ln in p.stdout.decode(errors="replace").splitlines() if '"type":1' in ln]
    print(f"gpsdecode produced {len(lines)} Type 1 messages")
    if lines:
        print(lines[0][:400])


if __name__ == "__main__":
    main()
