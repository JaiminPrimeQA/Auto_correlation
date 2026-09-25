// Entrypoint of the Fargate runner task: one Newman run, then exit.
//
// The task has NO AWS credentials. Its only capabilities are two presigned
// URLs for its own objects:
//   RUN_INPUT_URL      GET  {collection, environment, args}
//   REPORT_UPLOAD_URL  PUT  the Newman JSON report
// Supplied variable values arrive inside the encrypted input object - never in
// the environment or arguments. Newman runs without a shell, output discarded.
//
// Exit codes: Newman's own (0 = passed, 1 = assertions failed; both produce a
// report), 70 = input unavailable/invalid, 71 = report upload failed,
// 72 = report larger than MAX_REPORT_BYTES, 143 = stopped (SIGTERM).

import { spawn } from "node:child_process";
import { mkdir, readFile, stat, writeFile } from "node:fs/promises";

export const WORKDIR = "/tmp/job";
export const EXIT = { INPUT: 70, UPLOAD: 71, TOO_LARGE: 72, STOPPED: 143 };

const FETCH_TIMEOUT_MS = 60_000;

/** Validates the input document; returns null when it is unusable. */
export function parseInput(doc) {
  if (!doc || typeof doc !== "object") return null;
  const { collection, environment, args } = doc;
  if (!collection || typeof collection !== "object") return null;
  if (environment !== null && environment !== undefined && typeof environment !== "object") return null;
  if (!Array.isArray(args) || args.length === 0 || !args.every((a) => typeof a === "string")) return null;
  // The worker always builds `newman run <WORKDIR>/collection.json ...`.
  if (args[0] !== "run" || args[1] !== `${WORKDIR}/collection.json`) return null;
  return { collection, environment: environment ?? { name: "Baseline11 run", values: [] }, args };
}

async function fetchWithTimeout(url, init) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

function runNewman(args) {
  return new Promise((resolve) => {
    const child = spawn("newman", args, { cwd: WORKDIR, stdio: "ignore", shell: false });
    const stop = () => child.kill("SIGTERM");
    process.once("SIGTERM", stop);
    child.on("error", () => resolve(EXIT.INPUT));
    child.on("exit", (code, signal) => {
      process.removeListener("SIGTERM", stop);
      resolve(signal ? EXIT.STOPPED : (code ?? EXIT.INPUT));
    });
  });
}

export async function main(env = process.env) {
  const inputUrl = env.RUN_INPUT_URL;
  const uploadUrl = env.REPORT_UPLOAD_URL;
  const maxBytes = Number(env.MAX_REPORT_BYTES || 25 * 1024 * 1024);
  if (!inputUrl || !uploadUrl) return EXIT.INPUT;

  let input;
  try {
    const res = await fetchWithTimeout(inputUrl);
    if (!res.ok) return EXIT.INPUT;
    input = parseInput(await res.json());
  } catch {
    return EXIT.INPUT;
  }
  if (!input) return EXIT.INPUT;

  await mkdir(`${WORKDIR}/out`, { recursive: true, mode: 0o700 });
  await mkdir(`${WORKDIR}/files`, { recursive: true, mode: 0o700 });
  await writeFile(`${WORKDIR}/collection.json`, JSON.stringify(input.collection), { mode: 0o600 });
  await writeFile(`${WORKDIR}/environment.json`, JSON.stringify(input.environment), { mode: 0o600 });

  const code = await runNewman(input.args);
  const reportPath = `${WORKDIR}/out/report.json`;
  let size;
  try {
    size = (await stat(reportPath)).size;
  } catch {
    return code; // no report: the worker reports missing_report / the exit code
  }
  if (size > maxBytes) return EXIT.TOO_LARGE;
  try {
    const res = await fetchWithTimeout(uploadUrl, {
      method: "PUT",
      body: await readFile(reportPath),
      headers: { "Content-Type": "application/json" },
    });
    if (!res.ok) return EXIT.UPLOAD;
  } catch {
    return EXIT.UPLOAD;
  }
  return code;
}

if (process.argv[1] && import.meta.url.endsWith(process.argv[1].split(/[\\/]/).pop())) {
  main().then((code) => process.exit(code));
}
