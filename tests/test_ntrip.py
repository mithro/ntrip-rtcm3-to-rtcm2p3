"""Tests for the NTRIP client and caster (Python-level).

Interoperability against non-Python implementations (RTKLIB str2str, BNC,
wangkanai/caster) is covered separately in tests/test_interop.py.
"""
import socket
import threading
import time

from rtcm3to2p3.ntrip import Feed, MountInfo, NtripCaster, NtripClient


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_feed_fanout_and_availability():
    feed = Feed("F")
    assert not feed.available.is_set()
    a, b = feed.subscribe(), feed.subscribe()
    feed.publish(b"xyz")
    assert feed.available.is_set()
    assert a.get_nowait() == b"xyz"
    assert b.get_nowait() == b"xyz"
    feed.unsubscribe(a)
    feed.publish(b"q")
    assert b.get_nowait() == b"q"
    assert a.empty()


def test_sourcetable_lists_available_mounts():
    port = _free_port()
    caster = NtripCaster(port=port)
    feed = Feed("TEST")
    feed.available.set()
    caster.add_feed(feed, MountInfo(mount="TEST", fmt="RTCM 2.3", lat=-34.9, lon=138.6))
    caster.start(["127.0.0.1"])
    try:
        time.sleep(0.2)
        s = socket.create_connection(("127.0.0.1", port), timeout=5)
        s.sendall(b"GET / HTTP/1.1\r\nHost: x\r\nNtrip-Version: Ntrip/2.0\r\n\r\n")
        s.settimeout(3)
        data = b""
        try:
            while b"ENDSOURCETABLE" not in data:
                data += s.recv(1024)
        except TimeoutError:
            pass
        s.close()
    finally:
        caster.stop()
    assert b"SOURCETABLE 200 OK" in data
    assert b"STR;TEST;" in data and b"RTCM 2.3" in data


def test_client_caster_roundtrip():
    port = _free_port()
    caster = NtripCaster(port=port)
    feed = Feed("TEST")
    caster.add_feed(feed, MountInfo(mount="TEST"))
    caster.start(["127.0.0.1"])

    stop_pub = threading.Event()

    def publisher():
        while not stop_pub.is_set():
            feed.publish(b"\xd3\x00\x05hello!")
            time.sleep(0.02)

    got = bytearray()
    stop_cli = threading.Event()

    def on_data(d: bytes) -> None:
        got.extend(d)
        if len(got) > 20:
            stop_cli.set()

    try:
        time.sleep(0.2)
        threading.Thread(target=publisher, daemon=True).start()
        client = NtripClient("127.0.0.1", port, "TEST")
        t = threading.Thread(target=client.stream, args=(on_data, stop_cli), daemon=True)
        t.start()
        t.join(timeout=5)
    finally:
        stop_cli.set()
        stop_pub.set()
        caster.stop()

    assert b"hello!" in bytes(got)


def test_client_rejects_missing_mount_with_sourcetable():
    # requesting an unknown mount returns the sourcetable, so the client's 200 check fails
    port = _free_port()
    caster = NtripCaster(port=port)
    caster.start(["127.0.0.1"])
    try:
        time.sleep(0.2)
        s = socket.create_connection(("127.0.0.1", port), timeout=5)
        s.sendall(b"GET /NOPE HTTP/1.1\r\nHost: x\r\n\r\n")
        s.settimeout(3)
        data = s.recv(1024)
        s.close()
    finally:
        caster.stop()
    assert b"SOURCETABLE 200 OK" in data
