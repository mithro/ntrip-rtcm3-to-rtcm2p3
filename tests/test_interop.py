"""Interoperability with non-Python NTRIP implementations.

Our NTRIP client and caster are exercised against separate, non-Python NTRIP
software:

* **RTKLIB ``str2str``** (C): str2str pulls a mount from our caster, and our
  client pulls a mount from a str2str caster. Skipped when str2str is absent.
* **BKG NTRIP Client ``bnc``** (C++/Qt): headless ``bnc --conf ... --nw`` pulls a
  mount from our caster and stores the raw stream. Skipped when ``bnc`` is absent.
* **wangkanai/caster** (.NET): our client pulls a mount from a running instance
  pointed to by the ``WANGKANAI_CASTER`` env var (``host:port/MOUNT``). Skipped
  when it is not set. See docs/validation.md for how to launch it.

(The RTCM 2.3 *output* is additionally decoded by the non-Python gpsd
``gpsdecode`` in tests/test_rtcm2.py, and by a real u-blox 7 in
scripts/validate_ublox_hardware.py.)
"""
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time

import pytest

from rtcm3to2p3.ntrip import Feed, MountInfo, NtripCaster, NtripClient

STR2STR = shutil.which("str2str")
BNC = shutil.which("bnc")
WANGKANAI_CASTER = os.environ.get("WANGKANAI_CASTER")  # "host:port/MOUNT"
_RTCM3ish = b"\xd3\x00\x04TEST\x11\x22\x33"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.mark.skipif(not STR2STR, reason="RTKLIB str2str not installed")
def test_rtklib_str2str_pulls_from_our_caster():
    port = _free_port()
    caster = NtripCaster(port=port)
    feed = Feed("TEST")
    caster.add_feed(feed, MountInfo(mount="TEST"))
    caster.start(["127.0.0.1"])
    stop = threading.Event()

    def publisher():
        while not stop.is_set():
            feed.publish(_RTCM3ish)
            time.sleep(0.05)

    out = tempfile.mktemp(suffix=".bin")
    try:
        time.sleep(0.3)
        threading.Thread(target=publisher, daemon=True).start()
        time.sleep(0.2)
        try:
            subprocess.run([STR2STR, "-in", f"ntrip://127.0.0.1:{port}/TEST", "-out", out],
                           timeout=4, capture_output=True)
        except subprocess.TimeoutExpired:
            pass
        data = open(out, "rb").read() if os.path.exists(out) else b""
    finally:
        stop.set()
        caster.stop()
        if os.path.exists(out):
            os.remove(out)
    assert b"TEST" in data  # RTKLIB's NTRIP client received our stream


@pytest.mark.skipif(not STR2STR, reason="RTKLIB str2str not installed")
def test_our_client_pulls_from_str2str_caster():
    caster_port, src_port = _free_port(), _free_port()
    # str2str: read a TCP source we push into, serve it as an NTRIP caster mount.
    proc = subprocess.Popen(
        [STR2STR, "-in", f"tcpsvr://:{src_port}", "-out", f"ntripc://:@:{caster_port}/M"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    push = None
    got = bytearray()
    stop = threading.Event()

    def on_data(d: bytes) -> None:
        got.extend(d)
        if len(got) > 12:
            stop.set()

    try:
        time.sleep(0.6)
        push = socket.create_connection(("127.0.0.1", src_port), timeout=5)

        def pusher():
            for _ in range(80):
                if stop.is_set():
                    break
                try:
                    push.sendall(_RTCM3ish)
                except OSError:
                    break
                time.sleep(0.05)

        threading.Thread(target=pusher, daemon=True).start()
        time.sleep(0.3)
        client = NtripClient("127.0.0.1", caster_port, "M")
        t = threading.Thread(target=client.stream, args=(on_data, stop), daemon=True)
        t.start()
        t.join(timeout=6)
    finally:
        stop.set()
        proc.terminate()
        if push:
            push.close()
    assert b"TEST" in bytes(got)  # our client received RTKLIB's caster stream


@pytest.mark.skipif(not BNC, reason="BKG NTRIP Client (bnc) not installed")
def test_bnc_pulls_from_our_caster(tmp_path):
    # BNC (a separate C++/Qt implementation) subscribes to our caster headlessly
    # and writes the raw stream to a file; we assert our bytes made the round trip.
    port = _free_port()
    caster = NtripCaster(port=port)
    feed = Feed("TEST")
    caster.add_feed(feed, MountInfo(mount="TEST"))
    caster.start(["127.0.0.1"])
    stop = threading.Event()

    def publisher():
        while not stop.is_set():
            feed.publish(_RTCM3ish)
            time.sleep(0.05)

    raw_out = tmp_path / "bnc_raw.bin"
    conf = tmp_path / "bnc.conf"
    # mountPoints value: //user:pass@host:port/MOUNT format lat lon nmea ntripVer
    conf.write_text(
        "[General]\n"
        f"mountPoints=//:@127.0.0.1:{port}/TEST RTCM_3.3 -34.9 138.6 no 2\n"
        f"rawOutFile={raw_out}\n"
        f"logFile={tmp_path / 'bnc.log'}\n"
        "autoStart=1\n"
    )
    try:
        time.sleep(0.3)
        threading.Thread(target=publisher, daemon=True).start()
        try:
            # --nw runs until killed; let it collect a few seconds then time out.
            subprocess.run([BNC, "--conf", str(conf), "--nw"], timeout=6,
                           capture_output=True)
        except subprocess.TimeoutExpired:
            pass
        data = raw_out.read_bytes() if raw_out.exists() else b""
    finally:
        stop.set()
        caster.stop()
    assert b"TEST" in data  # BNC received and stored our RTCM stream


@pytest.mark.skipif(not WANGKANAI_CASTER,
                    reason="WANGKANAI_CASTER not set (host:port/MOUNT)")
def test_our_client_pulls_from_wangkanai_caster():
    # Our client pulls from a running wangkanai/caster (.NET) instance the operator
    # launched and pointed us at via WANGKANAI_CASTER=host:port/MOUNT.
    hostport, _, mount = WANGKANAI_CASTER.partition("/")
    host, _, port_s = hostport.partition(":")
    got = bytearray()
    stop = threading.Event()

    def on_data(d: bytes) -> None:
        got.extend(d)
        if len(got) > 8:
            stop.set()

    client = NtripClient(host, int(port_s), mount)
    t = threading.Thread(target=client.stream, args=(on_data, stop), daemon=True)
    t.start()
    t.join(timeout=8)
    stop.set()
    assert got  # our client received a stream from the .NET caster
