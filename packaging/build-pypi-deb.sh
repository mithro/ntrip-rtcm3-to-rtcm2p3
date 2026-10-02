#!/bin/bash
# Build a python3-<pkg> Debian package from a PyPI sdist using pybuild, for a
# suite whose Debian archive doesn't have it.
#
# Unlike py2dsc-deb/stdeb (which needs a legacy setup.py), this generates a
# minimal dh-python/pybuild debian/ so it works with modern pyproject-only
# packages. Used by .github/workflows/deb.yml to build the unpackaged runtime
# dependencies (pyrtcm, pynmeagps) into our apt repo.
#
# Usage: SUITE=<suite> OURS=<count> [PR=<number>] [DATE=<rfc2822>] \
#          packaging/build-pypi-deb.sh <pkg> <min-version> <output-dir> [extra-depends]
# Run as root in a debian:<suite> container, after `apt-get update`, with
# only Debian's own archive in apt's sources.
#
# min-version: the oldest version of <pkg> that will do. When the suite's
# Debian archive has python3-<pkg> at that version or newer, nothing is built:
# Debian's package is the one to install, and ours must never shadow it.
# Otherwise the newest sdist on PyPI (at least min-version) is built.
#
# extra-depends: comma-separated extra runtime deps (e.g. python3-pynmeagps),
# needed because dh_python3 cannot map an sdist's Requires-Dist to a package that
# is not yet in the archive at build time.
#
# The version is mithro/apt-repo-action docs/packaging.md's form for someone
# else's code ("Versions", Set A), <upstream>-0+welland<M>[~deb<R>][~pr<P>]:
#   -0          sorts below Debian's own first revision (-1), so when a later
#               suite has the package, Debian's replaces ours on the upgrade;
#   +welland<M> M is $OURS, the number of commits that changed this script, so
#               a change to the packaging is a new version;
#   ~deb<R>     the suite's Debian release number, so an older suite's build
#               sorts below a newer one's; sid has none;
#   ~pr<P>      a pull request's preview ($PR), below the build of its merge.
# DATE is the changelog entry's date (default: now), which dpkg-buildpackage
# uses as SOURCE_DATE_EPOCH.
set -euo pipefail

pkg="$1"
min="$2"
outdir="$(readlink -f "$3")"
extra_depends="${4:-}"

# Codename -> Debian release number, as apt-repo-action's
# scripts/deb-version.py has it (it can't write this version: the shared
# script doesn't have the Set A form yet).
case "${SUITE:?}" in
  bookworm) deb='~deb12' ;;
  trixie)   deb='~deb13' ;;
  forky)    deb='~deb14' ;;
  sid)      deb='' ;;
  *) echo "build-pypi-deb.sh: unknown suite '$SUITE'" >&2; exit 1 ;;
esac
case "${OURS:?}" in
  *[!0-9]*) echo "build-pypi-deb.sh: OURS must be a commit count, not '$OURS'" >&2; exit 1 ;;
esac

# What the suite itself has: only Debian's archive is in the container's
# sources, so the candidate is Debian's.
debian="$(apt-cache policy "python3-$pkg" | sed -n 's/^ *Candidate: //p')"
if [ -n "$debian" ] && [ "$debian" != "(none)" ] && dpkg --compare-versions "$debian" ge "$min"; then
  echo "python3-$pkg: Debian $SUITE has $debian (>= $min), so it is not built here"
  exit 0
fi
echo "python3-$pkg: Debian $SUITE has ${debian:-no version}; building it from PyPI"

work="$(mktemp -d)"
pip download --no-deps --no-binary=:all: --dest "$work" "$pkg>=$min"
sdist="$(ls "$work"/"$pkg"-*.tar.gz)"
base="$(basename "$sdist" .tar.gz)"
ver="${base##*-}"
version="$ver-0+welland$OURS$deb${PR:+~pr$PR}"

mkdir "$work/src"
tar --strip-components=1 -xzf "$sdist" -C "$work/src"
cd "$work/src"

# Not native: the version has a revision.
mkdir -p debian/source
echo '3.0 (quilt)' > debian/source/format
cat > debian/changelog <<EOF
$pkg ($version) $SUITE; urgency=medium

  * Auto-built from the PyPI sdist for the ntrip-rtcm3-to-rtcm2p3 apt repo.

 -- Tim 'mithro' Ansell <me@mith.ro>  ${DATE:-$(date -R)}
EOF

cat > debian/control <<EOF
Source: $pkg
Section: python
Priority: optional
Maintainer: Tim 'mithro' Ansell <me@mith.ro>
Build-Depends: debhelper-compat (= 13), dh-python, pybuild-plugin-pyproject,
               python3-all, python3-setuptools
Standards-Version: 4.7.0

Package: python3-$pkg
Architecture: all
Depends: \${python3:Depends}, \${misc:Depends}${extra_depends:+, $extra_depends}
Description: $pkg (auto-built from PyPI)
 Debian package of the PyPI project $pkg, auto-built as a runtime dependency of
 ntrip-rtcm3-to-rtcm2p3 because it is not in Debian $SUITE's archive.
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
cp ../python3-"$pkg"_"$version"_all.deb "$outdir"/
