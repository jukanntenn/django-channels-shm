# DCS-RFC: user-facing documentation site on Read the Docs

Status: implemented

English | [中文](2026-09-08-user-docs-site-on-readthedocs.zh.md)

## Problem

The repo's user documentation was a single bilingual README — a landing page, not a documentation site — while the library grows a real consumer surface (install, quickstart, concepts, configuration, API reference) that hand-maintained Markdown restates poorly. A hand-written API catalog drifts from the code, and no hosted, versioned distribution exists for the docs. The native extension (PyO3, maturin backend) makes doc hosting harder than for a pure-Python package: any site that renders API documentation from live objects must import a compiled `_native.abi3.so`, which is not importable until a Rust toolchain has built it.

## Decision

A user-facing MkDocs site is generated from `docs/site/` by `mkdocs.yml` at the repo root and distributed by Read the Docs from `.readthedocs.yaml`:

- Toolchain: MkDocs with the Material theme plus `mkdocstrings` (Google-style docstring parsing via griffe). API pages are one-line directives per public module/class and render the live source docstrings, so the code is the single source of the API catalog.
- Build: `.readthedocs.yaml` compiles the extension inside the RTD image — `build.tools.rust: "1.86"` and `build.os: ubuntu-24.04`, Python 3.13 — then installs the real package with `pip install .[docs]` (`[project.optional-dependencies].docs` in `pyproject.toml`), so `mkdocstrings` imports `channels_shm` and renders members from source. This is the same pattern pyca/cryptography runs in production on RTD with maturin.
- Strictness: `mkdocs.fail_on_warning: true` on RTD; the local gate is `uv sync --extra docs && uv run mkdocs build --strict`. A warning is a red build.
- Distribution model: Read the Docs versions — `latest` follows `main`, `stable` follows the newest semver tag, pull requests build previews automatically. First activation is a one-time manual import of the repository at readthedocs.org as project `django-channels-shm` (documented in `docs/development.md`).
- Content (English-only): Overview (`index.md`), Installation, Quickstart, Concepts, Configuration, and an API reference with two generated pages — `SharedMemoryChannelLayer` (`reference/layer.md`) and the exceptions module (`reference/exceptions.md`). The site is the expanded home of user docs; the README links to it and keeps the entry-level content. `docs/site/` is excluded from the bilingual pairing gate in `scripts/doc_languages.manifest.json` (single-language corpus by decision, not by neglect), and the tier table in `docs/AGENTS.md` names it.
- The public API keeps no new mandatory docstring lint (ruff `D` stays ignored); rendered pages show what the docstrings say. API documentation quality is docstring quality by construction.

## Alternatives considered

**Sphinx + autodoc + sphinx-rtd-theme.** The Python ecosystem's classic stack and the one django/channels and pyca/cryptography use; RTD treats it as first-class. It lost on authoring and velocity: reStructuredText is a second markup language the repo otherwise never writes, and for a new small site Material-for-MkDocs offers the more actively developed theme/plugin ecosystem (admonitions, code copy, nav UX) while mkdocstrings covers the same autodoc need with Google-style docstrings. Both stacks satisfy automatic API documentation and both can compile the native extension on RTD; the modern tooling won on equal capability.

**Hand-written API pages** (channels and cryptography render no autodoc at all — hand-written `.. function::`/`.. class::` trees). It lost: a hand-maintained signature catalog is precisely the drift this decision removes, and cryptography pays a large maintenance bill for it.

**Mocked imports instead of a real build.** RTD's `autodoc_mock_imports` (or mkdocstrings static inspection without importing) avoids compiling the extension, but renders stubs instead of signatures and hides import errors. The image ships a Rust toolchain, so the real compile costs only build time; cryptography proves the route.

**Chinese-first or fully bilingual site.** The repo's Markdown corpus is bilingual by standard, and a zh site was seriously considered for symmetry. It lost for launch: RTD localization means a second translation project plus a gettext/PO pipeline per page, and OSS documentation convention is English-first. The English corpus stays coherent today; `docs/site/` is exempted from pairing gates and a translation project can follow without restructuring.

**Self-hosting or Pages (pydantic-style mike branch, Cloudflare Pages).** It lost to the stated preference for Read the Docs: versioned `latest`/`stable` semantics, automatic PR previews, the badge, and zero release plumbing to maintain.

## Consequences

RTD compiles Rust on every docs build, so docs builds take longer than a pure-Python site — a few minutes per push/PR, cached by RTD where possible. `docs/site/` is single-language by decision and stays out of the pairing/wrap gates the rest of the corpus lives under, so its Markdown is not checked by `doc_sync`; reviewers rely on the strict build instead. README and site overlap on entry-level content by design until the README slims down; the RFC keeps that convergence tracked. API pages render the public API's Google-style docstrings (Args/Raises sections), and the layer page's `merge_init_into_class` option surfaces the constructor's configuration parameters as the authoritative parameter catalog. Site URL and repo import name are assumed (`django-channels-shm.readthedocs.io`) until the one-time activation happens; any divergence is a one-line change in `mkdocs.yml`, `.readthedocs.yaml`, the READMEs, and this file.
