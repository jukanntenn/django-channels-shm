# Coding principles

Behavioral constraints for the agent. Each is a rule the agent gets wrong without being told. Production safety and data integrity outrank every principle here — including the license to redesign from scratch; against a mere default or style rule, the principle wins.

## Ground every conclusion in fact

Library facts, APIs, and protocols must be read from source or docs before you act on them — training data is a blind spot, not a source. Verify every conclusion on the ground: `file:line` for Python and Rust logic, `uv run pytest` for behavior, `cargo test` inside `crates/_channels_shm_native/` for native behavior, `uv run python manage.py demo_broadcast` in `examples/chat` for cross-process acceptance, read-only inspection of the running stack for runtime behavior. Pure algorithm or syntax knowledge may use training knowledge.

The shape of getting this wrong: guessing a `py_bindings` signature or the shared-memory layout from memory instead of reading `crates/_channels_shm_native/src/py_bindings.rs` and `layout.rs` against the `_native.pyi` contract — an ABI mismatch silently corrupts the region every process shares. The layer's own interfaces — channels/asgiref `ChannelLayer`, msgpack serialization, `eventfd`/`AF_UNIX` wakeup, `MAP_SHARED` semantics — must be read from their live source before relying on them.

## Defer to community convention

When a convention or best practice is uncertain, ask "what is the community/official convention?" and verify against authoritative open-source source, not training memory (e.g. whether `format`/`lint` are the prek group names here, or how pyo3/maturin wants the abi3 extension built — both verifiable against the tool's own schema/docs).

Distinct from *Ground every conclusion in fact*: that one governs facts about a library you are integrating; this one governs convention and best-practice decisions.

## Converge before you implement

A spec or plan must be self-contained, complete, and unambiguous — an executor with no taste can land it mechanically, with no room to improvise. Resolve every open point before implementing; do not start on the strength of a half-settled plan.

## Fix the root cause, not the symptom

The solution you choose must be the most natural and optimal — not a patch over the symptom, and not one trapped by the existing implementation. You may shed all legacy and start from zero when the root fix requires it.

When formatter/lint coverage leaked across tools, the fix was not to configure each AI hook separately but to delegate to `prek.toml` as the single truth — no parallel formatter or lint definition exists outside prek.

## Design from first principles

Derive a design from the business essence; every premise is breakable; an elegant scheme beats an inherited one. Distinct from *Fix the root cause, not the symptom*: that one is how you *fix* a problem (root, not patch); this one is how you *design* a system (re-derive, question assumptions). Dropping the broker entirely — a lock-free MPMC ring + slab over one `MAP_SHARED` region instead of layering Redis on top — is this principle applied.

## Single source of truth

Each category of information has exactly one authoritative source: the shared-memory layout is `crates/_channels_shm_native/src/layout.rs` (ABI — ask before touching), the binding contract is `py_bindings.rs` + the `_native.pyi` stub, the release version is `pyproject.toml [project].version` (stamped into every wheel by maturin), and every quality gate lives in `prek.toml` alone. The public API is exported from `src/channels_shm/__init__.py` via `__all__`; keep the surface minimal. Generated files (`uv.lock`, `Cargo.lock`, `.basedpyright-baseline.json`) are regenerated, never hand-edited; `AGENTS.md` and `CLAUDE.md` stay byte-identical (prek gate).

## Naming is part of the API

A name is an API surface. If a name does not fit its business meaning, do not force it — brainstorm candidates and let the user choose, to prevent semantic drift. Channel names, group names, and the error surface (`ChannelFull`, `ChannelLayer`) are public contract.

## Degrade gracefully, never silently

A failure must be handled and observably recorded, and must not block downstream work — but a silent failure is always wrong. A worker that dies mid-message must have its rings and slots reclaimed by the watchdog without blocking the others; a full channel must raise, never silently drop; config validation fails loudly at startup instead of falling back to defaults no one knows about. Debug builds carry the watchdog, structured logs, and metrics; `python -O` strips them completely.

## Minimal mock, maximal real

Mock only the request boundary, never the whole service. Tests run against the real shared-memory region in `/dev/shm` — real multiprocessing for cross-process, real Django/channels stack in Docker for e2e — no in-memory stand-in for the thing under test. Local and CI run the same suite as fully as feasible (slow/e2e stay CI-only by design).
