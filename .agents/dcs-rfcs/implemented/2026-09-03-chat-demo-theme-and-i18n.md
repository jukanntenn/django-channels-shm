# DCS-RFC: Chat demo theming and i18n — one palette, client-side dictionary

Status: implemented

English | [中文](2026-09-03-chat-demo-theme-and-i18n.zh.md)

## Problem

The chat demo is the project's front door — the app every README points to and the pre-release acceptance project — but it shipped single-locale (Chinese) and single-theme (light). Readers of the English docs got a UI they could not read, and the light-only palette is at odds with dark OS themes (and with terminal-adjacent users who keep everything dark). Server error text made it worse: the wire carried finished Chinese prose, so no client could localize it. However the demo solves this, it must stay a no-build vanilla-JS app and must not touch the library itself.

## Decision

Theming is one palette resolved per element by CSS `light-dark()`; nothing is duplicated. A `data-theme` attribute on `<html>` pins `color-scheme` to `light` or `dark`, or leaves `light dark` so the OS preference decides (follow-system). The toolbar button cycles auto → light → dark, persists to `localStorage["shmchat.theme"]`, and an inline `<head>` script applies the persisted value before first paint, so there is no flash. Every color that previously hardcoded light values (input fills, borders, me-row highlight, scrollbar, toast, shadows) became a variable; the bright green outgoing bubble keeps dark ink in both themes.

Language switching is a client-side dictionary in `chat/static/chat/i18n.js` (zh/en, ~90 keys). Static template strings ride `data-i18n*` attributes; every dynamic string in `app.js` goes through `t()`; system messages are stored as dictionary keys + params so history re-renders in the active language, while user-authored message text is never translated. The toggle flips and persists `localStorage["shmchat.lang"]`; the first visit follows the browser language. A `shm:langchange` event re-renders the stateful dynamic strings (connection bar, login foot, conversation list, chat head, members, composer, jump pill).

To make errors localizable, the demo protocol's `error` payload gained machine-readable fields — `code` plus `params` (e.g. `name.too_long` with `{what: "nick", max: 24}`) — while keeping the Chinese `message` as the fallback for unknown codes. The client renders `t("err.<code>", params)` when it knows the code, else the raw message.

## Alternatives considered

**Server-side localization of error messages (per-connection locale).** It lost: the demo server is deliberately stateless — no sessions, no storage — so threading a locale through every connection would add state and spread presentation into the consumer; language is a client preference, and structured codes keep the wire locale-neutral with the Chinese text as a human-readable fallback.

**Duplicated dark palettes (`[data-theme="dark"]` block plus a `@media (prefers-color-scheme: dark)` block for auto).** It lost: every color would exist two or three times and drift silently; `light-dark()` keeps one source of truth at the cost of requiring a `light-dark()`-capable browser, which the evergreen-targeting, no-build demo already assumes.

**A real i18n framework (Django i18n, gettext, Intl.MessageFormat).** It lost: two locales, one static template, no build step — a ~100-line dictionary plus attribute application covers it without new tooling or server round-trips, and the dictionary doubles as the single place to audit UI copy.

**One fixed toolbar overlaying both login and main screens.** It lost: a fixed top-right toolbar collides with the chat header's members button; two small synced button sets (login card corner, sidebar header) need no layout contortions and keep each screen's controls local.

## Consequences

Demo-only by construction: the library's wire format and `src/channels_shm` are untouched; the protocol lives in `examples/chat/chat/consumers.py` (its docstring documents the error shape). Dark mode and English now cover every UI string, and UA chrome (scrollbars, form controls, default focus) follows `color-scheme` for free. A language switch re-renders the open conversation — system messages from their keys, user-authored text verbatim — while toasts stay in the language they were rendered in. English copy carries a `{key|s}` plural marker in the dictionary, so `Group · 1 member` and `Group · 2 members` both read natively. `light-dark()` sets a browser floor (Baseline 2024) for the demo, acceptable for an evergreen showcase but a reason it stays out of any shipped artifact.
