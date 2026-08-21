"""The rebroadcaster service: AUSCORS RTCM3 in, RTCM3 + RTCM2.3 out on the LAN.

Two upstream NTRIP clients pull the reference-station observations (with station
position) and the broadcast ephemeris. The observations are re-served verbatim
on the RTCM3 mount and, per GPS epoch, converted to RTCM 2.3 Type 1 corrections
served on the RTCM2.3 mount.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

from pyrtcm import RTCMReader

from .dgps import DgpsGenerator
from .ntrip import Feed, MountInfo, NtripCaster, NtripClient
from .rtcm2 import Rtcm2Encoder
from .rtcm3_input import (
    GPS_EPHEMERIS,
    GPS_MSM7,
    STATION_MESSAGES,
    parse_ephemeris,
    parse_gps_msm7,
    parse_station,
)


def extract_rtcm3_frames(buf: bytearray) -> list[bytes]:
    """Pull complete RTCM3 frames (0xD3 + 10-bit len + payload + 3-byte CRC) from
    a growing buffer, leaving any partial trailing frame in place."""
    frames: list[bytes] = []
    while True:
        start = buf.find(0xD3)
        if start < 0:
            buf.clear()
            return frames
        if start:
            del buf[:start]  # drop noise before the preamble
        if len(buf) < 3:
            return frames
        total = 3 + (((buf[1] & 0x03) << 8) | buf[2]) + 3
        if len(buf) < total:
            return frames
        frames.append(bytes(buf[:total]))
        del buf[:total]


class Converter:
    """Turns an RTCM3 observation stream into RTCM 2.3 corrections."""

    def __init__(self, rtcm3_feed: Feed, rtcm23_feed: Feed) -> None:
        self.rtcm3_feed = rtcm3_feed
        self.rtcm23_feed = rtcm23_feed
        self.gen = DgpsGenerator()
        self.encoder = Rtcm2Encoder()
        self._obs_buf = bytearray()
        self._eph_buf = bytearray()
        self._seq = 0
        self.station_id = 0
        self.messages_out = 0

    def feed_obs(self, data: bytes) -> None:
        self.rtcm3_feed.publish(data)  # verbatim RTCM3 passthrough
        self._obs_buf.extend(data)
        for frame in extract_rtcm3_frames(self._obs_buf):
            self._on_obs_frame(frame)

    def feed_eph(self, data: bytes) -> None:
        self._eph_buf.extend(data)
        for frame in extract_rtcm3_frames(self._eph_buf):
            self._on_eph_frame(frame)

    def _on_obs_frame(self, frame: bytes) -> None:
        try:
            msg = RTCMReader.parse(frame)
        except Exception:
            return
        if msg is None:
            return
        if msg.identity in STATION_MESSAGES:
            self.gen.set_station(parse_station(msg))
            self.station_id = int(getattr(msg, "DF003", 0)) & 0x3FF
        elif msg.identity == GPS_MSM7:
            tow, pseudoranges = parse_gps_msm7(msg)
            corrections = self.gen.corrections(tow, pseudoranges)
            if not corrections:
                return
            zcount = int(round((tow % 3600) / 0.6)) % 8192
            self._seq = (self._seq + 1) % 8
            out = self.encoder.encode_type1(self.station_id, zcount, self._seq, 0, corrections)
            self.rtcm23_feed.publish(out)
            self.messages_out += 1

    def _on_eph_frame(self, frame: bytes) -> None:
        try:
            msg = RTCMReader.parse(frame)
        except Exception:
            return
        if msg is not None and msg.identity == GPS_EPHEMERIS:
            self.gen.add_ephemeris(parse_ephemeris(msg))


@dataclass
class Config:
    """Service configuration."""

    upstream_host: str = "ntrip.data.gnss.ga.gov.au"
    upstream_port: int = 2101
    upstream_tls: bool = False
    upstream_user: str = "mithro"
    upstream_password: str = ""
    obs_mount: str = "ADDE00AUS0"
    eph_mount: str = "BCEP00BKG0"
    listen_addresses: list[str] = field(default_factory=lambda: ["127.0.0.1"])
    listen_port: int = 2101
    rtcm3_mount: str = "ADDE_RTCM3"
    rtcm23_mount: str = "ADDE_RTCM23"
    station_lat: float = -34.94
    station_lon: float = 138.58


class Service:
    """Owns the caster, converter and upstream clients for the service lifetime."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.rtcm3_feed = Feed(config.rtcm3_mount)
        self.rtcm23_feed = Feed(config.rtcm23_mount)
        self.converter = Converter(self.rtcm3_feed, self.rtcm23_feed)
        self.caster = NtripCaster(port=config.listen_port)
        self.caster.add_feed(self.rtcm3_feed, MountInfo(
            mount=config.rtcm3_mount, fmt="RTCM 3",
            fmt_details="1006,1077", lat=config.station_lat, lon=config.station_lon))
        self.caster.add_feed(self.rtcm23_feed, MountInfo(
            mount=config.rtcm23_mount, fmt="RTCM 2.3", nav="GPS",
            fmt_details="1(1),3(60)", lat=config.station_lat, lon=config.station_lon))
        self._obs_client = NtripClient(
            config.upstream_host, config.upstream_port, config.obs_mount,
            user=config.upstream_user, password=config.upstream_password, tls=config.upstream_tls)
        self._eph_client = NtripClient(
            config.upstream_host, config.upstream_port, config.eph_mount,
            user=config.upstream_user, password=config.upstream_password, tls=config.upstream_tls)

    def run(self, stop: threading.Event) -> None:
        self.caster.start(self.config.listen_addresses)
        threads = [
            threading.Thread(target=self._eph_client.stream,
                             args=(self.converter.feed_eph, stop), daemon=True),
            threading.Thread(target=self._obs_client.stream,
                             args=(self.converter.feed_obs, stop), daemon=True),
        ]
        for t in threads:
            t.start()
        stop.wait()
        self.caster.stop()
