"""packaging/build-pypi-deb.sh: what it builds, the versions, and what it refuses.

The script runs for real, with the three things it can't have here replaced
by stand-ins on PATH: apt (what "Debian" has is a file), PyPI (pip makes an
sdist from a canned PKG-INFO) and the build itself (dpkg-buildpackage packs
the generated debian/ into a .deb without building anything).
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "packaging" / "build-pypi-deb.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("dpkg-deb") is None or shutil.which("bash") is None,
    reason="needs dpkg-deb and bash")

STUBS = {
    "apt-get": "#!/bin/sh\nexit 0\n",
    # apt-cache policy python3-<pkg>: the candidate from $STUB/debian.
    "apt-cache": """#!/bin/sh
v=$(sed -n "s/^$2 //p" "$STUB/debian")
[ -z "$v" ] || printf '%s:\\n  Installed: (none)\\n  Candidate: %s\\n' "$2" "$v"
""",
    # pip download ... --dest <dir> "<pkg>>=<min>": an sdist holding
    # $STUB/pypi/<pkg>, its PKG-INFO.
    "pip": """#!/bin/sh
set -e
while [ $# -gt 1 ]; do [ "$1" != --dest ] || dest=$2; shift; done
pkg=${1%%>=*}
ver=$(sed -n 's/^Version: //p' "$STUB/pypi/$pkg")
mkdir -p "$dest/tree/$pkg-$ver"
cp "$STUB/pypi/$pkg" "$dest/tree/$pkg-$ver/PKG-INFO"
tar -czf "$dest/$pkg-$ver.tar.gz" -C "$dest/tree" "$pkg-$ver"
""",
    # dpkg-buildpackage: ../<package>_<version>_all.deb, with the generated
    # debian/'s package name, version and Depends.
    "dpkg-buildpackage": """#!/bin/sh
set -e
version=$(sed -n '1s/^[^ ]* (\\(.*\\)) .*/\\1/p' debian/changelog)
package=$(sed -n 's/^Package: //p' debian/control)
depends=$(sed -n 's/^Depends: //p' debian/control |
  sed 's/\\${python3:Depends}, \\${misc:Depends}/python3/')
mkdir -p pack/DEBIAN
{
  echo "Package: $package"
  echo "Version: $version"
  echo "Architecture: all"
  echo "Maintainer: x <x@example.org>"
  echo "Depends: $depends"
  echo "Description: x"
} > pack/DEBIAN/control
dpkg-deb --root-owner-group --build pack "../${package}_${version}_all.deb" >&2
""",
}


def pkg_info(name, version, *lines):
    return "\n".join(["Metadata-Version: 2.4", f"Name: {name}", f"Version: {version}", *lines,
                      "", "Requires-Dist: in-the-description, which is not metadata", ""])


def run(tmp_path, *, suite="trixie", debian="", pyrtcm=("Requires-Dist: pynmeagps>=1.1.4",),
        pynmeagps="1.1.7", pr=None, ours="4"):
    stub = tmp_path / "stub"
    (stub / "bin").mkdir(parents=True)
    (stub / "pypi").mkdir()
    (stub / "tmp").mkdir()
    for name, text in STUBS.items():
        (stub / "bin" / name).write_text(text)
        (stub / "bin" / name).chmod(0o755)
    (stub / "debian").write_text(debian)
    (stub / "pypi" / "pyrtcm").write_text(pkg_info("pyrtcm", "1.2.0", *pyrtcm))
    (stub / "pypi" / "pynmeagps").write_text(pkg_info("pynmeagps", pynmeagps))
    out = tmp_path / "out"
    out.mkdir()
    env = {"PATH": f"{stub / 'bin'}:{os.environ['PATH']}", "STUB": str(stub),
           "TMPDIR": str(stub / "tmp"), "SUITE": suite, "OURS": ours,
           "DATE": "Fri, 02 Oct 2026 00:00:00 +0000"}
    if pr:
        env["PR"] = pr
    r = subprocess.run(["bash", str(SCRIPT), str(out)], env=env, capture_output=True, text=True)
    debs = {}
    for deb in sorted(out.glob("*.deb")):
        fields = subprocess.run(["dpkg-deb", "-f", str(deb), "Package", "Version", "Depends"],
                                check=True, capture_output=True, text=True).stdout
        f = dict(line.split(": ", 1) for line in fields.splitlines())
        assert deb.name == f"{f['Package']}_{f['Version']}_all.deb"
        debs[f["Package"]] = (f["Version"], f["Depends"])
    return r, debs


def test_debian_has_neither(tmp_path):
    """trixie: both are built, and pyrtcm's Depends is its own requirement."""
    r, debs = run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert debs == {
        "python3-pynmeagps": ("1.1.7-0+welland4~deb13", "python3"),
        "python3-pyrtcm": ("1.2.0-0+welland4~deb13", "python3, python3-pynmeagps (>= 1.1.4)"),
    }


@pytest.mark.parametrize("suite,suffix", [("forky", "~deb14"), ("sid", "")])
def test_debian_has_pynmeagps(tmp_path, suite, suffix):
    """forky and sid: Debian's python3-pynmeagps is never shadowed."""
    r, debs = run(tmp_path, suite=suite, debian="python3-pynmeagps 1.1.7-1\n")
    assert r.returncode == 0, r.stderr
    assert f"Debian {suite} has 1.1.7-1 (>= 1.1.4), so it is not built here" in r.stdout
    assert debs == {
        "python3-pyrtcm": (f"1.2.0-0+welland4{suffix}", "python3, python3-pynmeagps (>= 1.1.4)")}


def test_debian_has_both(tmp_path):
    r, debs = run(tmp_path, debian="python3-pynmeagps 1.1.7-1\npython3-pyrtcm 1.2.0-1\n")
    assert r.returncode == 0, r.stderr
    assert debs == {}


def test_debian_too_old(tmp_path):
    """A pyrtcm that needs a newer pynmeagps than Debian's gets ours, and says so."""
    r, debs = run(tmp_path, suite="forky", debian="python3-pynmeagps 1.1.7-1\n",
                  pyrtcm=("Requires-Dist: pynmeagps>=1.2.0",), pynmeagps="1.2.1")
    assert r.returncode == 0, r.stderr
    assert debs == {
        "python3-pynmeagps": ("1.2.1-0+welland4~deb14", "python3"),
        "python3-pyrtcm": ("1.2.0-0+welland4~deb14", "python3, python3-pynmeagps (>= 1.2.0)"),
    }


def test_pull_request_preview(tmp_path):
    r, debs = run(tmp_path, pr="7")
    assert r.returncode == 0, r.stderr
    assert debs["python3-pyrtcm"][0] == "1.2.0-0+welland4~deb13~pr7"


def test_requirement_in_debian(tmp_path):
    """A requirement Debian has is a Depends, with or without a version; an
    extra's is left out."""
    r, debs = run(tmp_path, debian="python3-pynmeagps 1.1.7-1\npython3-serial 3.5-2\n",
                  pyrtcm=("Requires-Dist: pynmeagps >= 1.1.4", "Requires-Dist: Serial",
                          'Requires-Dist: pytest>=8; extra == "test"'))
    assert r.returncode == 0, r.stderr
    assert debs["python3-pyrtcm"][1] == "python3, python3-pynmeagps (>= 1.1.4), python3-serial"


@pytest.mark.parametrize("pyrtcm,message", [
    # a new dependency nobody has: not built just because PyPI has it
    (("Requires-Dist: newthing>=1.0",),
     "python3-newthing (>= 1.0) is needed, Debian trixie has no version, and this script "
     "doesn't build newthing"),
    (("Requires-Dist: pynmeagps>=1.1.4,<2",), "can't turn 'Requires-Dist: pynmeagps>=1.1.4,<2'"),
    (("Requires-Dist: pynmeagps==1.1.7",), "can't turn 'Requires-Dist: pynmeagps==1.1.7'"),
    (('Requires-Dist: pynmeagps>=1.1.4; python_version < "3.12"',), "can't turn"),
    (("Dynamic: Requires-Dist",), "the sdist's requirements are dynamic"),
])
def test_refuses(tmp_path, pyrtcm, message):
    r, debs = run(tmp_path, pyrtcm=pyrtcm)
    assert r.returncode != 0
    assert message in r.stderr
    assert "python3-pyrtcm" not in debs


@pytest.mark.parametrize("env,message", [
    ({"suite": "jessie"}, "unknown suite 'jessie'"),
    ({"ours": "three"}, "OURS must be a commit count"),
])
def test_refuses_bad_input(tmp_path, env, message):
    r, debs = run(tmp_path, **env)
    assert r.returncode != 0
    assert message in r.stderr
    assert debs == {}
