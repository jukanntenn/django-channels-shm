import { extname, resolve } from "node:path";
import type { Plugin } from "@opencode-ai-plugin";

/**
 * OpenCode plugin: in-process Python + Rust format/lint, and session-end lint gate.
 *
 * OpenCode's built-in Format service already runs `ruff format` (by extension)
 * after every write/edit/apply_patch, so this plugin does NOT re-implement
 * Python formatting. It closes the gaps the built-in service leaves open:
 *
 *   1. Per-file post-write fixes (mirror .claude/hooks/post_tool_use.py):
 *      - .py/.pyi: `ruff check --fix` then `ruff format` (the built-in formatter
 *        only formats; it does not apply lint fixes).
 *      - .rs: `rustfmt --edition 2021` (single-file; `cargo fmt` is crate-wide
 *        and PostToolUse only gets one path).
 *      All run silently and idempotently; never blocks the agent.
 *   2. Session-end lint gate (mirror .claude/hooks/stop.py): on the first
 *      `session.idle` of each real user turn, re-run auto-fix across the project
 *      and, if anything remains that the tools can't auto-fix, inject a
 *      synthetic user message via `client.session.prompt()` so the agent keeps
 *      working. Gates: `ruff check src/`, `cargo fmt --check`,
 *      `cargo clippy --all-targets --all-features -- -D warnings`.
 *      Mirrors Claude's `stop_hook_active`: at most ONE feedback per real user
 *      prompt; subsequent idles in the same turn stand down, and a fresh real
 *      user message resets the gate.
 *
 * Plugins are stateful in OpenCode (the module is imported once and the hook
 * object is kept alive for the instance lifetime), so the module-level
 * `turnState` map persists across events within a process.
 *
 * See .claude/hooks/post_tool_use.py and .claude/hooks/stop.py for the reference
 * Python implementations this mirrors.
 */

const PATCH_FILE_RE = /^\*\*\* (?:Update|Add) File: (.+)$/;
const PATCH_MOVE_RE = / -> \*\*\* Move to: (.+)$/;

// The desktop app launches the sidecar with cwd=$HOME, so the shell helper's
// relative paths would resolve outside the project. Anchor on the plugin file
// location (always inside the project) instead of the process cwd.
const PROJECT_ROOT = resolve(import.meta.dir, "../..");

// crates/_channels_shm_native is this repo's only Rust crate; `--manifest-path`
// keeps cargo commands cwd-independent (matches .claude/hooks/stop.py).
const CARGO_MANIFEST = resolve(
  PROJECT_ROOT,
  "crates/_channels_shm_native/Cargo.toml",
);

// Per-session feedback gate. Resets on a real (non-synthetic) user message,
// sets after the first idle-time feedback. Keyed by sessionID.
const turnState = new Map<string, { feedbackGiven: boolean }>();

function extractPaths(
  filePath: string | undefined,
  patchText: string | undefined,
): string[] {
  if (filePath) return [filePath];
  if (!patchText) return [];
  const paths: string[] = [];
  for (const line of patchText.split("\n")) {
    const m = line.match(PATCH_FILE_RE);
    if (!m) continue;
    const move = m[1].match(PATCH_MOVE_RE);
    paths.push((move ? move[1] : m[1]).trim());
  }
  return paths;
}

// Mirror stop.py's `_collect`: collect combined stdout+stderr only on failure.
// On a clean gate (exit 0) return "" — e.g. cargo clippy prints `Finished ...`
// to stderr on success, which would otherwise look like a diagnostic. A clean
// gate is signaled by exit 0 and produces no output here.
function collectFailed(
  exitCode: number,
  stdout: string,
  stderr: string,
): string {
  if (exitCode === 0) return "";
  return [stdout, stderr]
    .join("\n")
    .split("\n")
    .filter((line) => line.trim() !== "")
    .join("\n")
    .trim();
}

export const HooksPlugin: Plugin = async ({ $, client }) => {
  return {
    "chat.message": async (_input, output) => {
      const parts = output.parts as Array<{ synthetic?: boolean }>;
      if (parts.length > 0 && parts.every((p) => p.synthetic)) return;
      turnState.set(output.message.sessionID, { feedbackGiven: false });
    },

    "tool.execute.after": async (input) => {
      if (
        input.tool !== "write" &&
        input.tool !== "edit" &&
        input.tool !== "apply_patch"
      )
        return;
      const args = (input.args ?? {}) as {
        filePath?: string;
        patchText?: string;
      };
      for (const filePath of extractPaths(args.filePath, args.patchText)) {
        switch (extname(filePath)) {
          case ".py":
          case ".pyi":
            await $`uv run ruff check --fix ${filePath}`.quiet().nothrow();
            await $`uv run ruff format ${filePath}`.quiet().nothrow();
            break;
          case ".rs":
            // edition 2021 matches crates/_channels_shm_native/Cargo.toml;
            // rustfmt is single-file, cargo fmt only works crate-wide.
            await $`rustfmt --edition 2021 ${filePath}`.quiet().nothrow();
            break;
          default:
            break;
        }
      }
    },

    event: async ({ event }) => {
      if (event.type !== "session.idle") return;
      const sessionID = (event.properties as { sessionID?: string } | undefined)
        ?.sessionID;
      if (!sessionID) return;

      const state = turnState.get(sessionID) ?? { feedbackGiven: false };
      if (state.feedbackGiven) return;

      // ── auto-fix phase: apply fixes first, ignore output/exit (mirror stop.py) ──
      await $`uv run ruff check --fix ${PROJECT_ROOT}/src/`.quiet().nothrow();
      await $`cargo fmt --manifest-path ${CARGO_MANIFEST}`.quiet().nothrow();
      await $`cargo clippy --fix --allow-dirty --allow-no-vcs --all-targets --all-features --manifest-path ${CARGO_MANIFEST}`
        .quiet()
        .nothrow();
      // clippy --fix can produce rustfmt-noncompliant code; re-format before checking.
      await $`cargo fmt --manifest-path ${CARGO_MANIFEST}`.quiet().nothrow();

      // ── gate phase: anything still unfixed triggers feedback ──
      const pyVerify =
        await $`uv run ruff check ${PROJECT_ROOT}/src/ --output-format=concise`
          .quiet()
          .nothrow();
      const fmtVerify =
        await $`cargo fmt --check --manifest-path ${CARGO_MANIFEST}`
          .quiet()
          .nothrow();
      const clippyVerify =
        await $`cargo clippy --all-targets --all-features --manifest-path ${CARGO_MANIFEST} -- -D warnings`
          .quiet()
          .nothrow();

      if (
        pyVerify.exitCode === 0 &&
        fmtVerify.exitCode === 0 &&
        clippyVerify.exitCode === 0
      )
        return;

      state.feedbackGiven = true;
      turnState.set(sessionID, state);

      const pyOut = collectFailed(
        pyVerify.exitCode,
        pyVerify.stdout.toString("utf8"),
        pyVerify.stderr.toString("utf8"),
      );
      const fmtOut = collectFailed(
        fmtVerify.exitCode,
        fmtVerify.stdout.toString("utf8"),
        fmtVerify.stderr.toString("utf8"),
      );
      const clippyOut = collectFailed(
        clippyVerify.exitCode,
        clippyVerify.stdout.toString("utf8"),
        clippyVerify.stderr.toString("utf8"),
      );

      const text = [
        "Lint gate found errors it could not auto-fix. Resolve them before finishing.",
        "",
        "Diagnostics (Python / ruff):",
        "<ruff_output>",
        pyOut || "(clean)",
        "</ruff_output>",
        "",
        "Diagnostics (Rust / cargo fmt):",
        "<rust_fmt_output>",
        fmtOut || "(clean)",
        "</rust_fmt_output>",
        "",
        "Diagnostics (Rust / cargo clippy):",
        "<rust_clippy_output>",
        clippyOut || "(clean)",
        "</rust_clippy_output>",
        "",
        "Required:",
        "1. Fix every diagnostic above with a real code change. Do not silence them:",
        "   - Python: no `# noqa`, no inline rule disables, no `type: ignore` — only treat a diagnostic as a false positive if you can justify why.",
        "   - Rust:   no `#[allow(...)]` without an inline comment justifying it.",
        "2. After editing, verify yourself that all gates are clean:",
        "   - `uv run ruff check src/`",
        "   - `cargo fmt --check` (run inside crates/_channels_shm_native/)",
        "   - `cargo clippy --all-targets --all-features -- -D warnings` (run inside crates/_channels_shm_native/)",
        "3. Only consider the task done when every command above exits 0 with no output.",
        "Do not end your turn until all checks pass.",
        "",
        "This feedback fires once per real user turn — subsequent idles will not re-inject it. If you finish with lint errors remaining, they will slip through to CI. Verify before you finish.",
      ].join("\n");

      await client.session
        .prompt({
          path: { id: sessionID },
          body: {
            parts: [
              {
                type: "text",
                synthetic: true,
                text,
              },
            ],
          },
        })
        .catch(() => {});
    },
  };
};
