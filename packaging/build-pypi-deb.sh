#!/bin/bash
# Build the python3-<pkg> Debian packages ntrip-rtcm3-to-rtcm2p3 needs and a
# suite's Debian archive doesn't have, from their PyPI sdists using pybuild.
#
# Unlike py2dsc-deb/stdeb (which needs a legacy setup.py), this generates a
# minimal dh-python/pybuild debian/ so it works with modern pyproject-only
# packages. Used by .github/workflows/deb.yml to build the unpackaged runtime
# dependencies (pyrtcm, pynmeagps) into our apt repo.
#
# Usage: SUITE=<suite> OURS=<count> [PR=<number>] [DATE=<rfc2822>] \
#          packaging/build-pypi-deb.sh <output-dir>
# Run as root in a clean debian:<suite> container, with only Debian's own
# archive in apt's sources. It installs what the builds need.
#
# Everything of ours that shapes the packages is in this file, so that its
# history alone gives <M> below: what is needed (at the end), which projects
# may be built, and the build dependencies. The rest comes from the sdists.
#
# For each project needed, at some minimum version:
# - when the suite's Debian archive has python3-<pkg> at that version or
#   newer, nothing is built: Debian's package is the one to install, and ours
#   must never shadow it;
# - otherwise the newest sdist on PyPI is built, after whatever its own
#   metadata (PKG-INFO's Requires-Dist) needs, in the same way. Those become
#   the package's Depends: dh_python3 cannot map a Requires-Dist to a package
#   that is not installed at build time.
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

outdir="$(readlink -f "$1")"

fail() {
  echo "build-pypi-deb.sh: error: $*" >&2
  exit 1
}

# Codename -> Debian release number, as apt-repo-action's
# scripts/deb-version.py has it (it can't write this version: the shared
# script doesn't have the Set A form yet).
case "${SUITE:?}" in
  bookworm) deb='~deb12' ;;
  trixie)   deb='~deb13' ;;
  forky)    deb='~deb14' ;;
  sid)      deb='' ;;
  *) fail "unknown suite '$SUITE'" ;;
esac
case "${OURS:?}" in
  *[!0-9]*) fail "OURS must be a commit count, not '$OURS'" ;;
esac

# The projects this may build. One that isn't here and isn't in Debian
# either (a new dependency of a new release) fails the build: publishing
# another project's code is decided here, not by a release on PyPI.
buildable() {
  case "$1" in
    pyrtcm|pynmeagps) return 0 ;;
    *) return 1 ;;
  esac
}

# A requirement this understands: "<name>" or "<name>>=<version>".
requirement='^([A-Za-z0-9][A-Za-z0-9._-]*) *(>= *([0-9][0-9.]*))?$'

# need <pkg> <min-version>: python3-<pkg>, at min-version or newer, is in
# Debian's archive for the suite, or is built into the output directory.
need() {
  local pkg="$1" min="$2"
  local debian work sdist ver version req name atleast depends=""

  # What the suite itself has: only Debian's archive is in the container's
  # sources, so the candidate is Debian's.
  debian="$(apt-cache policy "python3-$pkg" | sed -n 's/^ *Candidate: //p')"
  if [ -n "$debian" ] && [ "$debian" != "(none)" ] && dpkg --compare-versions "$debian" ge "$min"; then
    echo "python3-$pkg: Debian $SUITE has $debian (>= $min), so it is not built here"
    return
  fi
  buildable "$pkg" || fail "python3-$pkg (>= $min) is needed, Debian $SUITE has" \
    "${debian:-no version}, and this script doesn't build $pkg"
  echo "python3-$pkg: Debian $SUITE has ${debian:-no version}; building it from PyPI"

  work="$(mktemp -d)"
  pip download --no-deps --no-binary=:all: --dest "$work" "$pkg>=$min"
  sdist="$(ls "$work"/*.tar.gz)"
  ver="$(basename "$sdist" .tar.gz)"
  ver="${ver##*-}"
  version="$ver-0+welland$OURS$deb${PR:+~pr$PR}"
  mkdir "$work/src"
  tar --strip-components=1 -xzf "$sdist" -C "$work/src"

  # The sdist's own requirements, from its metadata's header (up to the
  # first empty line): each is needed in turn, at the version it asks for,
  # and is a Depends. Anything but the two forms above fails the build
  # rather than being guessed at. An extra's requirements are optional, and
  # left out.
  sed '/^$/q' "$work/src/PKG-INFO" > "$work/header"
  if grep -qi '^Dynamic: *requires-dist' "$work/header"; then
    fail "$pkg $ver: the sdist's requirements are dynamic, so PKG-INFO doesn't list them"
  fi
  # On descriptor 3: the builds inside the loop must not read the list.
  while read -r req <&3; do
    case "$req" in *extra\ ==*) continue ;; esac
    [[ "$req" =~ $requirement ]] || fail "$pkg $ver: can't turn 'Requires-Dist: $req' into a Depends"
    name="${BASH_REMATCH[1],,}"
    name="${name//[._]/-}"
    atleast="${BASH_REMATCH[3]:-}"
    need "$name" "${atleast:-0}"
    depends+=", python3-$name${atleast:+ (>= $atleast)}"
  done 3< <(sed -n 's/^Requires-Dist: *//p' "$work/header")

  (
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
Depends: \${python3:Depends}, \${misc:Depends}$depends
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
  )
  # For the log: the name, version and Depends that were built.
  dpkg-deb -f "$outdir/python3-${pkg}_${version}_all.deb" Package Version Depends
}

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  build-essential ca-certificates debhelper dh-python dpkg-dev fakeroot \
  pybuild-plugin-pyproject python3-all python3-pip python3-setuptools

# What ntrip-rtcm3-to-rtcm2p3 itself needs: pyproject.toml's dependencies
# (pyrtcm>=1.2; debian/control says the same). What pyrtcm needs in turn
# (pynmeagps) is its own metadata's to say.
need pyrtcm 1.2
ls -lh "$outdir"/
