#!/usr/bin/env python3
"""Validate that the channels-shm version in pyproject.toml is valid PEP 440.

This guards the single source of truth: ``pyproject.toml [project].version``
is what maturin stamps into every sdist/wheel, and release tags must match it
(``v<version>``). A typo there would only surface as a broken build or a
mismatched tag at release time; this script fails fast in prek / CI instead.

Pure-stdlib (no ``packaging`` dependency) so it runs in any environment.

Exits 0 on success, 1 on failure.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import cast

import tomllib

# Authoritative PEP 440 regex, adapted from
# https://peps.python.org/pep-0440/#appendix-b-parsing-version-strings-with-regular-expressions
VERSION_PATTERN = r"""
    v?
    (?:
        (?:(?P<epoch>[0-9]+)!)?                           # epoch
        (?P<release>[0-9]+(?:\.[0-9]+)*)                  # release segment
        (?P<pre>                                          # pre-release
            [-_\.]?
            (?P<pre_l>(a|b|c|rc|alpha|beta|pre|preview))
            [-_\.]?
            (?P<pre_n>[0-9]+)?
        )?
        (?P<post>                                         # post release
            (?:-(?P<post_n1>[0-9]+))
            |
            (?:
                [-_\.]?
                (?P<post_l>post|rev|r)
                [-_\.]?
                (?P<post_n2>[0-9]+)?
            )
        )?
        (?P<dev>                                          # dev release
            [-_\.]?
            (?P<dev_l>dev)
            [-_\.]?
            (?P<dev_n>[0-9]+)?
        )?
    )
    (?:\+(?P<local>[a-z0-9]+(?:[-_\.][a-z0-9]+)*))?       # local version
"""


def main() -> int:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with pyproject.open("rb") as f:
        # tomllib's nested dict access is Any; [project].version is a string
        # by contract (validated by the regex below).
        version = cast("str", tomllib.load(f)["project"]["version"])

    if re.fullmatch(VERSION_PATTERN, version, re.VERBOSE | re.IGNORECASE):
        return 0

    message = f"invalid PEP 440 version {version!r} in pyproject.toml [project].version (rc pre-releases look like 0.1.0rc1, never 0.1.0-rc1)"
    print(message, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
