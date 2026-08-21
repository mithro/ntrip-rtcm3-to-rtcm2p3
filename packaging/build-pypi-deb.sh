#!/bin/bash
# Build a python3-<pkg> Debian package from a PyPI sdist using pybuild.
#
# Unlike py2dsc-deb/stdeb (which needs a legacy setup.py), this generates a
# minimal dh-python/pybuild debian/ so it works with modern pyproject-only
# packages. Used by .github/workflows/deb.yml to build the unpackaged runtime
# dependencies (pyrtcm, pynmeagps) into our apt repo.
#
# Usage: packaging/build-pypi-deb.sh <sdist.tar.gz> <output-dir> [extra-depends]
# extra-depends: comma-separated extra runtime deps (e.g. python3-pynmeagps),
# needed because dh_python3 cannot map an sdist's Requires-Dist to a package that
# is not yet in the archive at build time.
set -euo pipefail

sdist="$(readlink -f "$1")"
outdir="$(readlink -f "$2")"
extra_depends="${3:-}"
base="$(basename "$sdist" .tar.gz)"
pkg="${base%-*}"
ver="${base##*-}"

work="$(mktemp -d)"
tar --strip-components=1 -xzf "$sdist" -C "$work"
cd "$work"

mkdir -p debian/source
echo '3.0 (native)' > debian/source/format
cat > debian/changelog <<EOF
$pkg ($ver) unstable; urgency=medium

  * Auto-built from the PyPI sdist for the ntrip-rtcm3-to-rtcm2p3 apt repo.

 -- ntrip-rtcm3-to-rtcm2p3 packaging <noreply@github.com>  $(date -R)
EOF

cat > debian/control <<EOF
Source: $pkg
Section: python
Priority: optional
Maintainer: ntrip-rtcm3-to-rtcm2p3 packaging <noreply@github.com>
Build-Depends: debhelper-compat (= 13), dh-python, pybuild-plugin-pyproject,
               python3-all, python3-setuptools
Standards-Version: 4.7.0

Package: python3-$pkg
Architecture: all
Depends: \${python3:Depends}, \${misc:Depends}${extra_depends:+, $extra_depends}
Description: $pkg (auto-built from PyPI)
 Debian package of the PyPI project $pkg, auto-built as a runtime dependency of
 ntrip-rtcm3-to-rtcm2p3 because it is not (yet) in the main Debian archive.
EOF

cat > debian/rules <<'RULES'
#!/usr/bin/make -f
%:
	dh $@ --with python3 --buildsystem=pybuild

override_dh_auto_test:
RULES
chmod 755 debian/rules

cat > debian/copyright <<EOF
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: $pkg
Files: *
Copyright: upstream authors of $pkg
License: BSD-3-clause
EOF

dpkg-buildpackage -us -uc -b
cp ../python3-"$pkg"_*.deb "$outdir"/
