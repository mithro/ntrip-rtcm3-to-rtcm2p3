"""Command-line entry point for the rebroadcaster service."""
from __future__ import annotations

import argparse
import logging
import os
import signal
import threading

from .service import Config, Service


def _station_id(value: str) -> int:
    """argparse type: an RTCM 2.3 reference-station id, 1..1023."""
    ivalue = int(value)
    if not 1 <= ivalue <= 1023:
        raise argparse.ArgumentTypeError(f"station id must be 1..1023, got {ivalue}")
    return ivalue


def _load_password(args: argparse.Namespace) -> str:
    if args.password:
        return args.password
    if args.password_file:
        path = os.path.expanduser(args.password_file)
        if os.path.exists(path):
            with open(path) as fh:
                return fh.read().strip()
    return os.environ.get("NTRIP_UPSTREAM_PASSWORD", "")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ntrip-rtcm3-to-rtcm2p3",
        description="NTRIP rebroadcaster that also converts RTCM3 to RTCM 2.3 DGPS corrections.",
    )
    from . import __version__
    p.add_argument("--version", action="version",
                   version=f"%(prog)s {__version__}")
    d = Config()
    p.add_argument("--upstream-host", default=d.upstream_host)
    p.add_argument("--upstream-port", type=int, default=d.upstream_port)
    p.add_argument("--upstream-tls", action="store_true", help="use TLS to the upstream caster")
    p.add_argument("--upstream-user", default=d.upstream_user)
    p.add_argument("--password", default="", help="upstream password (prefer --password-file)")
    p.add_argument("--password-file", default="~/.gpsd-auscors-ntrip-passwd")
    p.add_argument("--obs-mount", default=d.obs_mount, help="upstream mount with obs + station")
    p.add_argument("--eph-mount", default=d.eph_mount, help="upstream broadcast-ephemeris mount")
    p.add_argument("--listen", default=",".join(d.listen_addresses),
                   help="comma-separated bind addresses (LAN only; the firewall need not change)")
    p.add_argument("--listen-port", type=int, default=d.listen_port)
    p.add_argument("--rtcm3-mount", default=d.rtcm3_mount)
    p.add_argument("--rtcm23-mount", default=d.rtcm23_mount)
    p.add_argument("--station-id", type=_station_id, default=d.station_id,
                   help="RTCM 2.3 reference-station id to emit (1..1023)")
    p.add_argument("--station-lat", type=float, default=d.station_lat)
    p.add_argument("--station-lon", type=float, default=d.station_lon)
    p.add_argument("--max-iod-age", type=float, default=d.max_iod_age_s,
                   help="drop corrections from ephemeris older than this many seconds")
    p.add_argument("--max-residual", type=float, default=d.max_residual_m,
                   help="drop corrections whose residual exceeds this many metres")
    p.add_argument("--log-level", default="INFO")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(message)s")
    config = Config(
        upstream_host=args.upstream_host,
        upstream_port=args.upstream_port,
        upstream_tls=args.upstream_tls,
        upstream_user=args.upstream_user,
        upstream_password=_load_password(args),
        obs_mount=args.obs_mount,
        eph_mount=args.eph_mount,
        listen_addresses=[a.strip() for a in args.listen.split(",") if a.strip()],
        listen_port=args.listen_port,
        rtcm3_mount=args.rtcm3_mount,
        rtcm23_mount=args.rtcm23_mount,
        station_id=args.station_id,
        station_lat=args.station_lat,
        station_lon=args.station_lon,
        max_iod_age_s=args.max_iod_age,
        max_residual_m=args.max_residual,
    )
    logging.info("caster on %s:%d  mounts=%s,%s  upstream=%s:%d/%s,%s",
                 config.listen_addresses, config.listen_port, config.rtcm3_mount,
                 config.rtcm23_mount, config.upstream_host, config.upstream_port,
                 config.obs_mount, config.eph_mount)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    Service(config).run(stop)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
