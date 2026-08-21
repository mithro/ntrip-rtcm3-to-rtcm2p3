"""The rebroadcaster service: AUSCORS RTCM3 in, RTCM3 + RTCM2.3 out on the LAN.

Two upstream NTRIP clients pull the reference-station observations (with station
position) and the broadcast ephemeris. The observations are re-served verbatim
on the RTCM3 mount and, per GPS epoch, converted to RTCM 2.3 Type 1 corrections
served on the RTCM2.3 mount.
"""
from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)

_WEEK_S = 604800.0
_TYPE3_INTERVAL_S = 60.0  # re-broadcast the base station position this often
_THROUGHPUT_EVERY = 300  # log a throughput line every N Type 1 messages


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

    def __init__(self, rtcm3_feed: Feed, rtcm23_feed: Feed, station_id: int = 1023,
                 max_iod_age_s: float = 7200.0, max_residual_m: float = 100.0) -> None:
        # The RTCM 2.3 reference-station ID we emit. It is an identifier only; we
        # do NOT inherit the upstream base's DF003 because some casters send 0,
        # and station id 0/2 trips gpsdecode's initial stream sync (the content is
        # valid RTCM2 -- gpsd just can't lock onto a stream that *starts* with it).
        # Reject rather than silently truncate an out-of-range id (finding #9).
        if not 1 <= station_id <= 1023:
            raise ValueError(f"station_id must be 1..1023, got {station_id}")
        self.rtcm3_feed = rtcm3_feed
        self.rtcm23_feed = rtcm23_feed
        self.gen = DgpsGenerator(max_iod_age_s=max_iod_age_s, max_residual_m=max_residual_m)
        self.encoder = Rtcm2Encoder()
        self._obs_buf = bytearray()
        self._eph_buf = bytearray()
        self._seq = 0
        self._last_type3_tow: float | None = None
        self.station_id = station_id
        self.messages_out = 0
        self._parse_errors = 0

    def feed_obs(self, data: bytes) -> None:
        self.rtcm3_feed.publish(data)  # verbatim RTCM3 passthrough
        self._obs_buf.extend(data)
        for frame in extract_rtcm3_frames(self._obs_buf):
            self._on_obs_frame(frame)

    def feed_eph(self, data: bytes) -> None:
        self._eph_buf.extend(data)
        for frame in extract_rtcm3_frames(self._eph_buf):
            self._on_eph_frame(frame)

    def _parse(self, frame: bytes):
        try:
            return RTCMReader.parse(frame)
        except Exception as exc:  # noqa: BLE001 -- a bad frame must not kill the stream
            self._parse_errors += 1
            if self._parse_errors <= 3 or self._parse_errors % 1000 == 0:
                logger.warning("RTCM3 parse error #%d: %s", self._parse_errors, exc)
            return None

    def _next_seq(self) -> int:
        self._seq = (self._seq + 1) % 8
        return self._seq

    def _maybe_emit_type3(self, tow: float, zcount: int) -> None:
        """Emit a Type 3 station-position message at most every ~60 s of stream."""
        if self.gen.base is None:
            return
        elapsed = None if self._last_type3_tow is None else (tow - self._last_type3_tow) % _WEEK_S
        if elapsed is not None and elapsed < _TYPE3_INTERVAL_S:
            return
        out = self.encoder.encode_type3(self.station_id, zcount, self._next_seq(), 0,
                                        self.gen.base)
        self.rtcm23_feed.publish(out)
        self._last_type3_tow = tow

    def _on_obs_frame(self, frame: bytes) -> None:
        msg = self._parse(frame)
        if msg is None:
            return
        if msg.identity in STATION_MESSAGES:
            first = self.gen.base is None
            self.gen.set_station(parse_station(msg))
            if first:
                logger.info("acquired reference station position from %s", msg.identity)
        elif msg.identity == GPS_MSM7:
            tow, pseudoranges = parse_gps_msm7(msg)
            corrections = self.gen.corrections(tow, pseudoranges)
            if not corrections:
                return
            zcount = int(round((tow % 3600) / 0.6)) % 8192
            # Type 3 and Type 1 share the one encoder so the parity seed chains.
            self._maybe_emit_type3(tow, zcount)
            out = self.encoder.encode_type1(self.station_id, zcount, self._next_seq(),
                                            0, corrections)
            self.rtcm23_feed.publish(out)
            self.messages_out += 1
            if self.messages_out % _THROUGHPUT_EVERY == 0:
                logger.info("emitted %d RTCM2.3 messages (last epoch: %d sats)",
                            self.messages_out, len(corrections))

    def _on_eph_frame(self, frame: bytes) -> None:
        msg = self._parse(frame)
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
    station_id: int = 1023  # RTCM 2.3 reference-station id we emit (1..1023)
    station_lat: float = -34.94
    station_lon: float = 138.58
    max_iod_age_s: float = 7200.0  # drop corrections from ephemeris older than this
    max_residual_m: float = 100.0  # drop corrections whose residual exceeds this


class Service:
    """Owns the caster, converter and upstream clients for the service lifetime."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.rtcm3_feed = Feed(config.rtcm3_mount)
        self.rtcm23_feed = Feed(config.rtcm23_mount)
        self.converter = Converter(self.rtcm3_feed, self.rtcm23_feed,
                                   station_id=config.station_id,
                                   max_iod_age_s=config.max_iod_age_s,
                                   max_residual_m=config.max_residual_m)
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
