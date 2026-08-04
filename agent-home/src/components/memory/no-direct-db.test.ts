/**
 * D2 guard (FG-23 §7): memory modules must read through the Python API, never
 * `pg` / Supabase directly. A direct `SELECT` would be RLS-scoped but silently
 * unaudited — precisely the bug #106 fixed. This test asserts on *imports*,
 * not on the string "select", so it cannot be defeated by formatting.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const MEMORY_MODULES = [
  "src/components/memory/MemoryView.tsx",
  "src/components/memory/MemoryMap.tsx",
  "src/app/memory/page.tsx",
  "src/app/api/memory/rows/route.ts",
  "src/app/api/memory/projection/route.ts",
  "src/app/api/memory/query/route.ts",
];

const FORBIDDEN_IMPORTS = [
  /from\s+["']@\/lib\/db["']/,
  /from\s+["']pg["']/,
  /from\s+["']@supabase\/ssr["']/,
  /from\s+["']@supabase\/supabase-js["']/,
  /import\s+.*["']pg["']/,
];

describe("no-direct-db (D2 guard)", () => {
  for (const mod of MEMORY_MODULES) {
    it(`${mod} does not import pg or Supabase directly`, () => {
      const src = readFileSync(join(__dirname, "../../..", mod), "utf-8");
      for (const pattern of FORBIDDEN_IMPORTS) {
        expect(src).not.toMatch(pattern);
      }
    });
  }
});
