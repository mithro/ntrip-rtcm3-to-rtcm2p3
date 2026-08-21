"""Minimal NTRIP v1/v2 client and caster.

:class:`NtripClient` connects to an upstream caster, requests a mountpoint and
streams the raw bytes to a callback, reconnecting with backoff. The upstream
password is passed in (read from a file by the caller), never placed on a command
line.

:class:`NtripCaster` re-serves one or more named :class:`Feed` streams to LAN
clients, answering the sourcetable request (``GET /``) and per-mount stream
requests. It binds only the addresses it is given, so it need not be exposed on a
public interface.
"""
from __future__ import annotations

import base64
import logging
import queue
import socket
import ssl
import threading
from collections.abc import Callable
from dataclasses import dataclass

_CRLF = b"\r\n"
logger = logging.getLogger(__name__)


class Feed:
    """A named byte stream fanned out to any number of subscriber queues."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._subs: set[queue.Queue[bytes]] = set()
        self._lock = threading.Lock()
        self.available = threading.Event()
        self.total_bytes = 0

    def subscribe(self) -> queue.Queue[bytes]:
        q: queue.Queue[bytes] = queue.Queue(maxsize=512)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: queue.Queue[bytes]) -> None:
        with self._lock:
            self._subs.discard(q)

    def publish(self, data: bytes) -> None:
        if not data:
            return
        self.total_bytes += len(data)
        self.available.set()
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(data)
            except queue.Full:
                pass  # drop for a slow client rather than stall the whole feed


@dataclass
class MountInfo:
    """Sourcetable metadata for one mount (STR record fields)."""

    mount: str
    identifier: str = "relay"
    fmt: str = "RTCM 3"
    fmt_details: str = ""
    carrier: int = 2
    nav: str = "GPS"
    country: str = "AUS"
    lat: float = 0.0
    lon: float = 0.0

    def str_record(self) -> str:
        return (f"STR;{self.mount};{self.identifier};{self.fmt};{self.fmt_details};"
                f"{self.carrier};{self.nav};relay;{self.country};{self.lat};{self.lon};"
                f"0;0;ntrip-rtcm3-to-rtcm2p3;none;B;N;0;")


def _basic_auth(user: str, password: str) -> str:
    return base64.b64encode(f"{user}:{password}".encode()).decode()


class NtripClient:
    """Reconnecting NTRIP client that streams a mountpoint to a callback."""

    def __init__(self, host: str, port: int, mountpoint: str, *, user: str = "",
                 password: str = "", tls: bool = False, user_agent: str = "NTRIP rtcm3to2p3",
                 timeout: float = 30.0) -> None:
        self.host, self.port, self.mountpoint = host, port, mountpoint
        self.user, self.password, self.tls = user, password, tls
        self.user_agent, self.timeout = user_agent, timeout

    def _request(self) -> bytes:
        lines = [f"GET /{self.mountpoint} HTTP/1.1", f"Host: {self.host}",
                 "Ntrip-Version: Ntrip/2.0", f"User-Agent: {self.user_agent}"]
        if self.user or self.password:
            lines.append(f"Authorization: Basic {_basic_auth(self.user, self.password)}")
        lines += ["Connection: close", "", ""]
        return _CRLF.join(line.encode() for line in lines)

    def _connect(self) -> socket.socket:
        raw = socket.create_connection((self.host, self.port), timeout=self.timeout)
        sock = ssl.create_default_context().wrap_socket(raw, server_hostname=self.host) \
            if self.tls else raw
        sock.sendall(self._request())
        sock.settimeout(self.timeout)
        status = b""
        while _CRLF not in status:
            b = sock.recv(1)
            if not b:
                raise OSError("connection closed during response header")
            status += b
        line = status.strip()
        # Only an ICY/HTTP 200 is a data stream. A caster answers a *bad*
        # mountpoint with "SOURCETABLE 200 OK", which must NOT be mistaken for a
        # stream (its body is ASCII, not RTCM3).
        http_ok = line.startswith(b"HTTP/1") and line.split(b" ")[1:2] == [b"200"]
        if not (line.startswith(b"ICY 200") or http_ok):
            raise OSError(
                f"upstream did not start a stream (mount '{self.mountpoint}'?): "
                f"{line.decode(errors='replace')}")
        if status.startswith(b"HTTP"):  # drain remaining HTTP headers
            hdr = status
            while b"\r\n\r\n" not in hdr:
                b = sock.recv(1)
                if not b:  # upstream closed mid-header -> don't spin forever
                    raise OSError("connection closed during HTTP headers")
                hdr += b
        return sock

    def stream(self, on_data: Callable[[bytes], None], stop: threading.Event) -> None:
        """Stream forever, reconnecting with exponential backoff, until ``stop``."""
        backoff = 2.0
        while not stop.is_set():
            try:
                sock = self._connect()
                logger.info("connected to %s:%d/%s", self.host, self.port, self.mountpoint)
                backoff = 2.0
                while not stop.is_set():
                    data = sock.recv(4096)
                    if not data:
                        raise OSError("upstream closed")
                    on_data(data)
                sock.close()
            except Exception as exc:  # noqa: BLE001 -- reconnect on any failure
                logger.warning("%s:%d/%s: %s; reconnecting in %.0fs",
                               self.host, self.port, self.mountpoint, exc, backoff)
                if stop.wait(backoff):
                    return
                backoff = min(backoff * 2, 30.0)


class NtripCaster:
    """Serves named feeds to NTRIP clients on the given bind addresses."""

    def __init__(self, port: int = 2101, server: str = "ntrip-rtcm3-to-rtcm2p3") -> None:
        self.port = port
        self.server = server
        self._feeds: dict[str, Feed] = {}
        self._info: dict[str, MountInfo] = {}
        self._servers: list[socket.socket] = []
        self._stop = threading.Event()

    def add_feed(self, feed: Feed, info: MountInfo) -> None:
        self._feeds[info.mount] = feed
        self._info[info.mount] = info

    def _sourcetable(self) -> bytes:
        rows = [self._info[m].str_record() for m in self._feeds
                if self._feeds[m].available.is_set()]
        body = ("\r\n".join(rows) + "\r\nENDSOURCETABLE\r\n").encode()
        return (f"SOURCETABLE 200 OK\r\nServer: {self.server}\r\n"
                f"Content-Type: gnss/sourcetable\r\nContent-Length: {len(body)}\r\n\r\n"
                ).encode() + body

    def _handle(self, conn: socket.socket, addr) -> None:
        try:
            conn.settimeout(15)
            req = b""
            while b"\r\n\r\n" not in req and len(req) < 4096:
                chunk = conn.recv(256)
                if not chunk:
                    return
                req += chunk
            lines = req.decode(errors="replace").split("\r\n")
            ntrip2 = any(line.lower().startswith("ntrip-version") and "2.0" in line
                         for line in lines)
            mount = lines[0].split(" ")[1].lstrip("/") if len(lines[0].split(" ")) > 1 else ""
            feed = self._feeds.get(mount)
            if feed is None or not feed.available.is_set():
                conn.sendall(self._sourcetable())
                return
            if ntrip2:
                conn.sendall(f"HTTP/1.1 200 OK\r\nNtrip-Version: Ntrip/2.0\r\nServer: "
                             f"{self.server}\r\nContent-Type: gnss/data\r\nConnection: "
                             f"close\r\n\r\n".encode())
            else:
                conn.sendall(b"ICY 200 OK\r\n")  # NTRIP1: status line then raw data
            logger.info("caster: client %s subscribed to /%s", addr[0], mount)
            q = feed.subscribe()
            try:
                while not self._stop.is_set():
                    try:
                        conn.sendall(q.get(timeout=10))
                    except queue.Empty:
                        continue
            finally:
                feed.unsubscribe(q)
        except (OSError, ValueError):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _serve_on(self, address: str) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((address, self.port))
        srv.listen(64)
        srv.settimeout(1.0)
        self._servers.append(srv)
        while not self._stop.is_set():
            try:
                conn, addr = srv.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn, addr), daemon=True).start()
        srv.close()

    def start(self, addresses: list[str]) -> None:
        for address in addresses:
            threading.Thread(target=self._serve_on, args=(address,), daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        for srv in self._servers:
            try:
                srv.close()
            except OSError:
                pass
