import { resolve } from "node:path";
import type { Plugin } from "@opencode-ai-plugin";

/**
 * OpenCode plugin: thin adapter over prek (single source of truth).
 *
 * All formatter/linter logic lives in prek.toml (workspace root +
 * crates/_channels_shm_native/prek.toml + examples/chat/prek.toml). This
 * plugin only wires OpenCode's events to prek invocations — it contains no
 * tool mapping of its own, so it cannot drift from pre-commit/CI:
 *
 *   1. `tool.execute.after` (write/edit/apply_patch): best-effort
 *      `prek run --group format --group lint --files <path>` — auto-fix the
 *      just-edited file; never blocks the session.
 *   2. `session.idle` (first idle of each real user turn): lint gate
 *      `prek run --group lint --all-files`. OpenCode event hooks are
 *      fire-and-forget with no channel to feed a block decision back into
 *      the session, so on failure a synthetic user message is injected via
 *      `client.session.prompt()` (mirror of the Claude/ZCode/Codex Stop
 *      hooks' decision:block). Lint enforcement otherwise falls back to
 *      prek's pre-commit gate and CI.
 *
 * The desktop app launches the sidecar with cwd=$HOME, so anchor on the
 * plugin file location (always inside the project) instead of process cwd.
 */

const PATCH_FILE_RE = /^\*\*\* (?:Update|Add) File: (.+)$/;
const PATCH_MOVE_RE = / -> \*\*\* Move to: (.+)$/;

const PROJECT_ROOT = resolve(import.meta.dir, "../..");

// Per-session feedback gate: at most ONE injected lint report per real user
// turn; a fresh real (non-synthetic) user message resets it. Mirrors the
// other hooks' stop_hook_active guard.
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
      const paths = extractPaths(args.filePath, args.patchText);
      if (paths.length === 0) return;
      // Best-effort auto-fix; never block. prek exit 1 here just means
      // files were modified or issues were found — the idle gate and the
      // pre-commit hook are the enforcement points.
      for (const p of paths) {
        await $`prek run --group format --group lint --files ${p}`
          .cwd(PROJECT_ROOT)
          .quiet()
          .nothrow();
      }
    },

    event: async ({ event }) => {
      if (event.type !== "session.idle") return;
      const sessionID = (event.properties as { sessionID?: string } | undefined)
        ?.sessionID;
      if (!sessionID) return;

      const state = turnState.get(sessionID) ?? { feedbackGiven: false };
      if (state.feedbackGiven) return;

      const gate = await $`prek run --group lint --all-files`
        .cwd(PROJECT_ROOT)
        .quiet()
        .nothrow();

      if (gate.exitCode === 0) return;

      state.feedbackGiven = true;
      turnState.set(sessionID, state);

      const output = [
        gate.stdout.toString("utf8"),
        gate.stderr.toString("utf8"),
      ]
        .join("\n")
        .split("\n")
        .filter((line) => line.trim() !== "")
        .join("\n");

      const text = [
        "prek lint gate found errors it could not auto-fix. Resolve them before finishing.",
        "",
        "<prek_output>",
        output,
        "</prek_output>",
        "",
        "The gate is defined in prek.toml (workspace root + sub-project configs).",
        "Reproduce it yourself with:",
        "  prek run --group lint --all-files",
        "",
        "Required:",
        "1. Fix every diagnostic above with a real code change. Do not silence them:",
        "   - Python: no `# noqa`, no inline rule disables, no `type: ignore` — only treat a diagnostic as a false positive if you can justify why.",
        "   - Rust:   no `#[allow(...)]` without an inline comment justifying it.",
        "2. Verify `prek run --group lint --all-files` exits 0 with no findings before finishing.",
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
