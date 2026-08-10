/**
 * D2 guard (FG-23 §7): memory modules must read through the Python API, never
 * the database directly. A direct `SELECT` would be RLS-scoped but silently
 * unaudited — precisely the bug #106 fixed. This test asserts on *imports*,
 * not on the string "select", so it cannot be defeated by formatting.
 *
 * Two things it does deliberately, because the shortcut is convenient and a
 * future author will reach for it:
 *
 * 1. It forbids `@/lib/supabase/*` — `scopedSelect`, `subscribeScoped` and the
 *    `pg` pool behind them. That is the seam that actually exists in this app
 *    (`src/app/page.tsx` uses it), so a guard listing only `pg`/`@supabase/*`
 *    passes while leaving the real road open.
 * 2. It discovers the modules to check from the filesystem, so a new file
 *    under a memory directory is covered without anyone remembering to add it
 *    to a list here.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { extname, join } from "node:path";

import { describe, expect, it } from "vitest";

const SRC = join(__dirname, "../../..", "src");

/** Directories whose entire subtree is memory code. */
const MEMORY_DIRS = [
  join(SRC, "components/memory"),
  join(SRC, "app/memory"),
  join(SRC, "app/api/memory"),
];

function sourceFiles(dir: string): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) {
      found.push(...sourceFiles(path));
      continue;
    }
    if (![".ts", ".tsx"].includes(extname(entry))) continue;
    if (entry.includes(".test.")) continue;
    found.push(path);
  }
  return found;
}

const FORBIDDEN_IMPORTS: { pattern: RegExp; why: string }[] = [
  { pattern: /from\s+["']pg["']/, why: "raw Postgres client" },
  { pattern: /require\(\s*["']pg["']\s*\)/, why: "raw Postgres client" },
  {
    pattern: /from\s+["']@supabase\/[^"']+["']/,
    why: "Supabase client (unaudited reads)",
  },
  {
    pattern: /from\s+["']@\/lib\/supabase\/[^"']+["']/,
    why: "the direct-DB seam (scopedSelect / realtime / pool)",
  },
  {
    pattern: /\b(scopedSelect|withPrincipalContext|subscribeScoped)\b/,
    why: "a direct scoped query instead of the Python API",
  },
];

describe("no-direct-db (D2 guard)", () => {
  const files = MEMORY_DIRS.flatMap(sourceFiles);

  it("finds the memory modules it is meant to guard", () => {
    // If this ever reads zero files the suite below would pass vacuously.
    expect(files.length).toBeGreaterThanOrEqual(6);
  });

  for (const file of files) {
    const label = file.slice(file.indexOf("/src/") + 1);
    it(`${label} reaches the memory tier only through the Python API`, () => {
      const src = readFileSync(file, "utf-8");
      for (const { pattern, why } of FORBIDDEN_IMPORTS) {
        expect(pattern.test(src), `${label} uses ${why}`).toBe(false);
      }
    });
  }
});
