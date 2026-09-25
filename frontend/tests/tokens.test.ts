import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(path.join(__dirname, "..", "app", "globals.css"), "utf8");

function block(selector: string): Record<string, number[]> {
  const start = css.indexOf(`${selector} {`);
  if (start < 0) throw new Error(`missing ${selector} block`);
  const body = css.slice(start, css.indexOf("}", start));
  const vars: Record<string, number[]> = {};
  for (const m of body.matchAll(/--([a-z0-9-]+):\s*(\d+)\s+(\d+)\s+(\d+)\s*;/g)) {
    vars[m[1]] = [Number(m[2]), Number(m[3]), Number(m[4])];
  }
  return vars;
}

function luminance([r, g, b]: number[]): number {
  const c = [r, g, b].map((v) => v / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}

function ratio(a: number[], b: number[]): number {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}

const PAIRS: [string, string][] = [
  ["ink", "bg"], ["ink", "surface"], ["muted", "bg"], ["muted", "surface"],
  ["subtle", "bg"], ["subtle", "surface"], ["subtle", "surface-2"],
  ["accent-ink", "accent"], ["accent-soft-ink", "accent-soft"], ["accent", "surface"],
  ["ok", "ok-soft"], ["warn", "warn-soft"], ["danger", "danger-soft"], ["danger", "surface"],
];

const GRAPH_PAIRS: [string, string][] = [
  ["graph-text", "graph-node"],
  ["graph-consume", "graph-node"],
  ["graph-produce", "graph-node"],
];

describe.each([[":root"], ['[data-theme="dark"]']])("tokens in %s", (selector) => {
  const vars = block(selector);
  it.each(PAIRS)("%s on %s meets WCAG AA", (fg, bg) => {
    expect(vars[fg], `--${fg}`).toBeDefined();
    expect(vars[bg], `--${bg}`).toBeDefined();
    expect(ratio(vars[fg], vars[bg])).toBeGreaterThanOrEqual(4.5);
  });
  it.each(GRAPH_PAIRS)("%s on %s meets WCAG AA", (fg, bg) => {
    expect(vars[fg], `--${fg}`).toBeDefined();
    expect(vars[bg], `--${bg}`).toBeDefined();
    expect(ratio(vars[fg], vars[bg])).toBeGreaterThanOrEqual(4.5);
  });
});
