import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = path.join(__dirname, "..");
const PENDING: string[] = [];
const LEGACY = /\b(?:text|bg|border|ring|fill|stroke|divide|placeholder|from|to)-(?:slate-\d+|ink|panel|edge|brand|amber-\d+|white|black)(?:\/\d+)?\b/g;
const DASH = /[—–]/;

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => {
    const p = path.join(dir, f);
    return statSync(p).isDirectory() ? files(p) : p.endsWith(".tsx") ? [p] : [];
  });
}

function toPosix(p: string): string {
  return p.split(path.sep).join("/");
}

describe("migrated UI", () => {
  const all = [...files(path.join(ROOT, "components")), ...files(path.join(ROOT, "app"))].filter(
    (f) => !PENDING.includes(toPosix(path.relative(ROOT, f)))
  );
  it.each(all.map((f) => [path.relative(ROOT, f)]))("%s uses only semantic colour classes", (rel) => {
    const src = readFileSync(path.join(ROOT, rel), "utf8");
    expect(src.match(LEGACY) ?? []).toEqual([]);
  });
  it.each(all.map((f) => [path.relative(ROOT, f)]))("%s has no em or en dashes in copy", (rel) => {
    const src = readFileSync(path.join(ROOT, rel), "utf8");
    const lines = src.split("\n").filter((l) => DASH.test(l) && !l.trim().startsWith("//") && !l.trim().startsWith("*"));
    expect(lines).toEqual([]);
  });

  it("DependencyGraph has no hard-coded hex colours", () => {
    const src = readFileSync(path.join(ROOT, "components", "DependencyGraph.tsx"), "utf8");
    expect(src.match(/#[0-9a-fA-F]{6}\b/g) ?? []).toEqual([]);
  });
});
