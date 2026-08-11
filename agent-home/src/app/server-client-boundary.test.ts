import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

/**
 * A server component may import a *component* from a `"use client"` module —
 * that is the boundary working; React ships a client reference and the browser
 * renders it. It may **not** import a plain value: the proxy it receives is not
 * the array/function/object it was written as, and only the production build
 * shows it. `/todos` shipped exactly that bug — `DEFAULT_STAGES.join(",")`
 * failed with "join is not a function" and the page rendered its error card.
 *
 * PascalCase names are taken to be components; everything else is a value.
 */
const SRC = resolve(__dirname, "..");

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) return walk(path);
    return /\.tsx?$/.test(path) && !/\.test\.tsx?$/.test(path) ? [path] : [];
  });
}

function isClientModule(path: string): boolean {
  return /^\s*["']use client["']/.test(readFileSync(path, "utf8"));
}

function resolveAlias(spec: string): string | null {
  if (!spec.startsWith("@/")) return null;
  const base = join(SRC, spec.slice(2));
  for (const candidate of [
    `${base}.ts`,
    `${base}.tsx`,
    join(base, "index.ts"),
    join(base, "index.tsx"),
  ]) {
    try {
      if (statSync(candidate).isFile()) return candidate;
    } catch {
      // not this extension
    }
  }
  return null;
}

/** Named value imports (`import type` and type-only members excluded). */
function valueImports(source: string): { spec: string; names: string[] }[] {
  const out: { spec: string; names: string[] }[] = [];
  const pattern = /import\s+(type\s+)?\{([^}]*)\}\s+from\s+["']([^"']+)["']/g;
  for (const match of source.matchAll(pattern)) {
    if (match[1]) continue; // `import type { … }`
    const names = match[2]
      .split(",")
      .map((part) => part.trim())
      .filter((part) => part && !part.startsWith("type "))
      .map((part) => part.split(/\s+as\s+/)[0].trim());
    if (names.length > 0) out.push({ spec: match[3], names });
  }
  return out;
}

describe("the server/client module boundary", () => {
  it("never pulls a plain value out of a 'use client' module into a server component", () => {
    const offenders: string[] = [];
    for (const path of walk(join(SRC, "app"))) {
      if (isClientModule(path)) continue;
      const source = readFileSync(path, "utf8");
      for (const { spec, names } of valueImports(source)) {
        const target = resolveAlias(spec);
        if (!target || !isClientModule(target)) continue;
        for (const name of names) {
          // `PascalCase` is a component reference and is fine; `SCREAMING_CASE`
          // and `camelCase` are values, which are not.
          const isComponent =
            /^[A-Z][a-zA-Z0-9]*$/.test(name) && !/^[A-Z0-9_]+$/.test(name);
          if (isComponent) continue;
          offenders.push(
            `${path.slice(SRC.length + 1)} imports ${name} from ${spec}`,
          );
        }
      }
    }
    expect(offenders).toEqual([]);
  });
});
