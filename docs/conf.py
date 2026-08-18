"""Sphinx configuration for the Generic Agentic Workflows documentation.

This configuration follows the Canonical Sphinx Stack (tag 2.0):
https://github.com/canonical/sphinx-stack/tree/2.0

Project-specific values (project title, author, copyright year) are set here;
everything else mirrors the Stack 2.0 template so the build stays aligned with
upstream.
"""

import datetime
import os
import textwrap

# ---------------------------------------------------------------------------
# Project information
# ---------------------------------------------------------------------------

# Project name and author are kept in sync with pyproject.toml
# (`authors = [{ name = "Michael Park" }]`).
project = "Generic Agentic Workflows"
author = "Michael Park"

# The year in the copyright statement.
copyright = f"{datetime.date.today().year}"

# Sidebar documentation title.
html_title = project + " documentation"

# Documentation website URL.
ogp_site_url = os.environ.get("READTHEDOCS_CANONICAL_URL", "/")

# Preview name of the documentation website.
ogp_site_name = project

# Canonical Stack 2.0 default OpenGraph preview image.
ogp_image = "https://assets.ubuntu.com/v1/cc828679-docs_illustration.svg"

# Dictionary of values passed into the Sphinx context for all pages.
html_context = {
    "product_page": "",
    "discourse": "",
    "mattermost": "",
    "matrix": "",
    "github_url": "",
    "repo_default_branch": "main",
    "repo_folder": "/docs/",
    "display_contributors": False,
    "github_issues": "enabled",
    "author": author,
    "license": {
        "name": "",
        "url": "",
    },
}

# ---------------------------------------------------------------------------
# Sitemap configuration (https://sphinx-sitemap.readthedocs.io/)
# ---------------------------------------------------------------------------

# Use the RTD canonical URL so duplicate pages resolve to a canonical URL.
html_baseurl = os.environ.get("READTHEDOCS_CANONICAL_URL", "/")

# sphinx-sitemap uses html_baseurl to generate the full URL for each page.
sitemap_url_scheme = "{link}"

# Include `lastmod` dates in the sitemap.
sitemap_show_lastmod = True

sitemap_excludes = [
    "404/",
    "genindex/",
    "search/",
]

# ---------------------------------------------------------------------------
# Redirects (https://sphinxext-rediraffe.readthedocs.io/en/latest/)
# ---------------------------------------------------------------------------

# Redirects are declared in `redirects.txt`.
rediraffe_redirects = "redirects.txt"

# Strip '/index.html' from destination URLs when building with 'dirhtml'.
rediraffe_dir_only = True

# ---------------------------------------------------------------------------
# sphinx-llm configuration
# ---------------------------------------------------------------------------

llms_txt_description = textwrap.dedent(
    """\
    This is the documentation for Generic Agentic Workflows, reusable GitHub
    Actions workflows that use OpenCode to provide pull-request documentation
    reviews, issue feedback, a manually dispatched issue-to-pull-request
    implementation path, and a manually dispatched release project-review.
    """
)

# The base URL for references built by sphinx-markdown-builder.
if os.environ.get("READTHEDOCS"):
    markdown_http_base = html_baseurl

# ---------------------------------------------------------------------------
# Link checker exceptions
# ---------------------------------------------------------------------------

linkcheck_ignore = [
    "http://127.0.0.1:8000",
    "http://localhost",
    "https://localhost",
    "https://github.com",
    r"https://matrix\.to/.*",
    "https://example.com",
    r"https://.*\.sourceforge\.(net|io)/.*",
]

# GitHub anchor validation is unreliable; ignore anchors for GitHub URLs.
linkcheck_anchors_ignore_for_url = [r"https://github\.com/.*"]

# Give linkcheck multiple tries on failure.
linkcheck_retries = 3

# ---------------------------------------------------------------------------
# Extensions (Canonical Stack 2.0 set)
# ---------------------------------------------------------------------------

extensions = [
    "canonical_sphinx",
    "notfound.extension",
    "sphinx_design",
    "sphinx_rerediraffe",
    "sphinx_reredirects",
    "sphinx_tabs.tabs",
    "sphinxcontrib.jquery",
    "sphinxext.opengraph",
    "sphinx_config_options",
    "sphinx_contributor_listing",
    "sphinx_filtered_toctree",
    "sphinx_llm.txt",
    "sphinx_related_links",
    "sphinx_roles",
    "sphinx_terminal",
    "sphinx_ubuntu_images",
    "sphinx_youtube_links",
    "sphinxcontrib.cairosvgconverter",
    "sphinx_last_updated_by_git",
    "sphinx.ext.intersphinx",
    "sphinx_sitemap",
]

# ---------------------------------------------------------------------------
# Excludes
# ---------------------------------------------------------------------------

# Exclude the local virtualenv plus the non-documentation artifacts nested
# under the configuration-source examples (per-source `.github` workflow
# wrappers and `.opencode` configuration bundles). `_build` is the Sphinx
# output directory.
exclude_patterns = [
    ".venv*",
    "**/.github",
    "**/.opencode",
    "_build*",
]

# ---------------------------------------------------------------------------
# MyST configuration
# ---------------------------------------------------------------------------

# The documentation occasionally links to directories rather than individual
# files (for example `](configuration-sources/default/)`). Suppress the resulting
# `myst.xref_missing` warnings so the documentation builds under
# `-W --keep-going`.
suppress_warnings = [
    "myst.xref_missing",
]

# Disable fuzzy link linkification so bare tokens such as `conf.py` are not
# turned into bogus `http://conf.py` URLs by the linkify extension (`.py` is a
# recognisable TLD).
myst_linkify_fuzzy_links = False

# ---------------------------------------------------------------------------
# reST prolog (Canonical Stack 2.0 default roles)
# ---------------------------------------------------------------------------

rst_prolog = """
.. role:: center
   :class: align-center
.. role:: h2
    :class: hclass2
.. role:: woke-ignore
    :class: woke-ignore
.. role:: vale-ignore
    :class: vale-ignore
"""


def setup(app):
    """Register a plain-text Pygments lexer for `mermaid` fenced blocks.

    Some frozen plan documents embed ```mermaid fences. Without a registered
    lexer, Sphinx emits a "Pygments lexer name 'mermaid' is not known" warning,
    which would fail the build under `-W`. Map the alias to the plain-text
    lexer so the diagrams render verbatim without warnings.
    """
    from pygments.lexers import TextLexer

    app.add_lexer("mermaid", TextLexer)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
