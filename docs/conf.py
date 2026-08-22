"""Sphinx configuration for ntrip-rtcm3-to-rtcm2p3."""
import importlib.metadata

project = "ntrip-rtcm3-to-rtcm2p3"
author = "Tim 'mithro' Ansell"
copyright = "2026, Tim 'mithro' Ansell"
try:
    release = importlib.metadata.version("ntrip-rtcm3-to-rtcm2p3")
except importlib.metadata.PackageNotFoundError:
    release = "0.0.1"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "sphinx.ext.extlinks",
    "sphinxcontrib.mermaid",
    "myst_parser",
]

autodoc_member_order = "bysource"
autodoc_typehints = "description"
# pyrtcm is only needed at runtime for RTCM3 decoding; mock it so docs build
# without it (RTD installs it via the package, but this keeps builds robust).
autodoc_mock_imports = ["pyrtcm"]
napoleon_google_docstring = True

intersphinx_mapping = {"python": ("https://docs.python.org/3", None)}

# `{gh}` links a repo-relative path to its source on GitHub, e.g.
#   {gh}`scripts/validate_ublox_hardware.py`
# -> https://github.com/mithro/ntrip-rtcm3-to-rtcm2p3/blob/main/scripts/validate_ublox_hardware.py
extlinks = {
    "gh": ("https://github.com/mithro/ntrip-rtcm3-to-rtcm2p3/blob/main/%s", "%s"),
}
extlinks_detect_hardcoded_links = True

mermaid_version = "11.4.1"  # pinned; RTD renders client-side from this CDN build

myst_enable_extensions = ["colon_fence", "deflist", "dollarmath", "amsmath"]
source_suffix = {".rst": "restructuredtext", ".md": "markdown"}

html_theme = "furo"
html_title = "ntrip-rtcm3-to-rtcm2p3"
