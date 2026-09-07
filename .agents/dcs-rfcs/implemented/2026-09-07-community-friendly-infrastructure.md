# DCS-RFC: Community-friendly infrastructure

Status: implemented

English | [中文](2026-09-07-community-friendly-infrastructure.zh.md)

## Problem

The repository shipped a working library but none of the community plumbing GitHub's Community Standards checklist looks for: no code of conduct, no contributing guide, no security policy or support routing, no issue forms for external reporters, no PR checklist, no review routing, and no discussion venue. An outside contributor evaluating the project had no way to learn the rules (this repo's bar is high: native build, prek gates, RFC requirement), no signal of who maintains it or where to ask questions, and the README had drifted from the code it describes. The project wants to be a community-friendly open source project; the gap between that goal and the repository's public surface was the problem.

## Decision

The community surface is a set of bilingual pairs, each with one home and gates: [`CONTRIBUTING.md`](../../../CONTRIBUTING.md) at the root (setup, ground rules, what we accept, AI-assisted contribution), [`.github/CODE_OF_CONDUCT.md`](../../../.github/CODE_OF_CONDUCT.md) adopting Contributor Covenant 2.1 verbatim with the maintainer mailbox as the enforcement contact, [`.github/SECURITY.md`](../../../.github/SECURITY.md) (private vulnerability reporting as the primary channel, a stated IPC trust boundary, a supported-versions table) and [`.github/SUPPORT.md`](../../../.github/SUPPORT.md) (channel routing and the actionable-report bar) in `.github/`, plus [`.github/CODEOWNERS`](../../../.github/CODEOWNERS) routing everything to `@jukanntenn` with an embedded subtree map and an ABI annotation for `layout.rs`.

Every change starts with an issue, including typo fixes; non-trivial changes additionally carry a DCS-RFC. GitHub Discussions is the public conversation ground: questions go to Q&A, ideas to Ideas, and valuable discussions are distilled into DCS-RFCs by whoever drove the consensus, with the RFC linking the origin discussion — Discussions host the unformed conversation, RFCs the formed decision, with no overlap.

Bug and feature reports are YAML issue forms ([`bug.yml`](../../../.github/ISSUE_TEMPLATE/bug.yml), [`feature.yml`](../../../.github/ISSUE_TEMPLATE/feature.yml)) with required structured fields and a code-of-conduct checkbox; `idea.md`, `research.md`, and `task.md` stay minimal Markdown templates for maintainer-side work types (the no-op `type:` frontmatter key is gone). Agents keep a frictionless path: issue forms only apply to the web UI, so programmatic creation via API/CLI bypasses them, the YAML itself is machine-readable, and root `AGENTS.md` directs programmatic bug reports to follow `bug.yml`'s structure. Pull requests carry a five-item checklist (tests, `prek run --all-files`, Conventional Commits, RFC when non-trivial, bilingual docs) with N/A escapes.

The README answers GitHub's five README questions — what the project does, why it is useful, how to get started, where to get help, and who maintains it — through Features/Quickstart, Benchmarks, a Community & support section, and a Contributing section naming the maintainer; its benchmark tables track the newest committed run under `bench/docker/results/`. `examples/chat` ships a bilingual README pair whose screenshots are captured in each language's UI.

The project name is `django-channels-shm` everywhere it is written: repository, distribution name, and prose all use the full name, while the import stays `channels_shm` for `CHANNEL_LAYERS` `BACKEND`-string ergonomics — a standard distribution/import split (`beautifulsoup4` → `bs4`).

The gates follow the corpus: `verify_doc_pairs.py` and `verify_md_links.py` now cover the four community pairs, `verify_md_wrap.py` covers `CONTRIBUTING.md` only, and [`docs/AGENTS.md`](../../../docs/AGENTS.md) carries the new tier row. Enabling Discussions with its category copy, the pinned welcome post, private vulnerability reporting, and the repository topics (`python rust django django-channels asgi channel-layer shared-memory ipc pyo3 linux`) are repository-metadata actions that live outside git.

## Alternatives considered

**Pointer community files, as django/channels do.** django and channels ship one-line `SECURITY.md`/`CODE_OF_CONDUCT.md` pointers to djangoproject.com policies backed by the DSF's conduct and security teams. Rejected: this repository is outside their jurisdiction, and GitHub's own guidance conditions a code of conduct on being willing and able to enforce it — pointing at a team that cannot act here fails that test.

**English-only community files.** The ecosystem default, and simpler to maintain. Rejected: the documentation standard is bilingual pairs with equal authority, and the official Contributor Covenant zh-cn translation removes the translation-risk objection for the one file where wording matters most.

**Keeping bug/feature as minimal Markdown templates.** The existing terse templates served the agent harness well. Rejected for the two community-facing reports: forms with required fields are GitHub's mechanism for guaranteeing structured input from humans, SUPPORT.md promises the form walks reporters through the environment fields, and the agent path stays frictionless because API/CLI creation never renders forms — the guidance simply moved into the YAML the agent can read.

**Deleting the Idea template.** With Discussions positioned as the home for unformed ideas, the `idea.md` template looked redundant. Rejected: Discussions has no REST API and no first-class `gh` commands (GraphQL only), so harness workflows that file issues programmatically would pay a real cost; the template stays, scoped by its `about` line to maintainer-side use.

**Exempting trivial pull requests from the issue requirement.** The softer gate — typo fixes may PR directly — matched common practice. Rejected in review: the maintainer chose a stricter uniform rule (every PR references an issue) over per-size exceptions, trading a little contributor friction for one uniform rule and a complete public record.

**A fully enumerated CODEOWNERS.** Mapping every subtree to its owner mirrors cpython's structure. Rejected: with a single owner every line would read identically; the map lives in comments as the slots to fill when owners join.

**Leaving the new pairs outside the documentation gates.** The gate scopes predate these files; extending them costs three script edits. Rejected: un-gated pairs rot exactly the way the gates exist to catch — one side edited without its twin.

**Keeping `channels-shm` as the distribution name.** The short name matched the import, and the rename touches pyproject, both lock files, and every document's prose. Rejected: the repository was already named `django-channels-shm`, so repository and package had diverged with no ecosystem precedent for it (`django/channels_redis` names both identically); the rename is free exactly once, before the first PyPI release, and would strand users on the old name after it. The import name stays `channels_shm`, keeping the `BACKEND` string short.

## Consequences

The Community Standards checklist reaches its recommended set, and a contributor can answer "how do I join in" from the repository surface alone. The cost is real: four bilingual pairs must move together forever (now enforced), the code of conduct binds the maintainer to its enforcement ladder at a best-effort, solo scale, the every-PR-needs-an-issue rule adds a step to drive-by fixes, and Discussions, private reporting, and topics remain manual repository settings that can silently drift from what the docs promise. Verification: `uv run python scripts/doc_sync.py` (pairs, links, wrap, budgets) and `prek run --all-files` cover the in-repo surface; the README's benchmark tables must be refreshed from the newest committed run whenever `bench/docker/results/` gains one.
