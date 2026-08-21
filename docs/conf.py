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
    "myst_parser",
]

autodoc_member_order = "bysource"
autodoc_typehints = "description"
# pyrtcm is only needed at runtime for RTCM3 decoding; mock it so docs build
# without it (RTD installs it via the package, but this keeps builds robust).
autodoc_mock_imports = ["pyrtcm"]
napoleon_google_docstring = True

intersphinx_mapping = {"python": ("https://docs.python.org/3", None)}

myst_enable_extensions = ["colon_fence", "deflist"]
source_suffix = {".rst": "restructuredtext", ".md": "markdown"}

html_theme = "furo"
html_title = "ntrip-rtcm3-to-rtcm2p3"
