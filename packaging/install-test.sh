#!/bin/sh
# Install test (mithro/apt-repo-action docs/packaging.md, "Builds"): install
# the built packages into a clean container of the suite and check that they
# work. The Debian packages workflow runs it once per suite as
#
#   docker run --rm -v "$PWD/built-debs:/debs:ro" \
#     -v "$PWD/packaging:/packaging:ro" debian:<suite> sh /packaging/install-test.sh
#
# built-debs/ holds this repository's two packages and the python3-pyrtcm and
# python3-pynmeagps it builds from PyPI (they are not in Debian's archive, or
# not in every suite's). Everything else comes from the Debian archive.
#
# No network NTRIP caster is involved: a canned RTCM3 frame goes through the
# converter, and gpsd's gpsdecode (an independent RTCM2 decoder) reads what
# comes out.
set -eux

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends /debs/*.deb gpsd-clients

for p in ntrip-rtcm3-to-rtcm2p3 python3-ntrip-rtcm3-to-rtcm2p3 python3-pyrtcm python3-pynmeagps; do
  dpkg-query -W -f '${Package} ${Version} ${db:Status-Status}\n' "$p" | grep -q ' installed$'
done

# The command runs, and reports the version the build derived from git: the
# Debian version is the Python one plus the suite suffixes (~deb13, ~pr4).
ntrip-rtcm3-to-rtcm2p3 --help
debver=$(dpkg-query -W -f '${Version}' python3-ntrip-rtcm3-to-rtcm2p3)
pyver=$(ntrip-rtcm3-to-rtcm2p3 --version | sed -n 's/^ntrip-rtcm3-to-rtcm2p3 //p')
echo "Debian version $debver, Python version $pyver"
case "$debver" in
  "$pyver"|"$pyver"~*) ;;
  *) echo "the Python version $pyver doesn't match the package's $debver" >&2; exit 1 ;;
esac

# The service package: postinst made the root-only configuration from the
# example, and the unit is installed and enabled.
test -f /usr/share/ntrip-rtcm3-to-rtcm2p3/env.example
test "$(stat -c %a /etc/ntrip-rtcm3-to-rtcm2p3/env)" = 600
test -f /usr/lib/systemd/system/ntrip-rtcm3-to-rtcm2p3.service
test -L /etc/systemd/system/multi-user.target.wants/ntrip-rtcm3-to-rtcm2p3.service

# A conversion, end to end through the installed modules: a real RTCM3 1005
# frame (station 2003's position, the RTCM 10403 example) goes into the
# converter as the upstream caster would send it. The RTCM3 mount gets it
# verbatim, pyrtcm decodes the position, and the RTCM 2.3 mount gets a Type 3
# message that gpsdecode reads back as the same station position.
python3 - <<'EOF'
import json
import subprocess

from rtcm3to2p3.ntrip import Feed
from rtcm3to2p3.service import Converter

FRAME = bytes.fromhex("d300133ed7d30202980edeef34b4bd62ac0941986f33360b98")
ECEF = (1114104.5999, -4850729.7108, 3975521.4643)

rtcm3, rtcm23 = Feed("RTCM3"), Feed("RTCM23")
conv = Converter(rtcm3, rtcm23, station_id=321)
passthrough, out = rtcm3.subscribe(), rtcm23.subscribe()
conv.feed_obs(FRAME)
assert passthrough.get_nowait() == FRAME, "the RTCM3 mount must relay the frame verbatim"
assert conv.gen.base is not None, "the 1005 frame didn't set the station position"
assert all(abs(a - b) < 1e-3 for a, b in zip(conv.gen.base, ECEF)), conv.gen.base

conv._maybe_emit_type3(100000.0, 0)
data = out.get_nowait()
p = subprocess.run(["gpsdecode"], input=data, capture_output=True, timeout=30, check=True)
msgs = [json.loads(line) for line in p.stdout.decode().splitlines() if line.strip()]
m = next(m for m in msgs if m.get("class") == "RTCM2" and m.get("type") == 3)
print("gpsdecode:", m)
assert m["station_id"] == 321, m
for axis, want in zip("xyz", ECEF):
    assert abs(m[axis] - want) < 0.01, (axis, m[axis], want)
print("install test: RTCM3 1005 -> RTCM 2.3 Type 3 round trip OK")
EOF
