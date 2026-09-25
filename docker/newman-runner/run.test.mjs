// node --test docker/newman-runner/run.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";
import { EXIT, main, parseInput, WORKDIR } from "./run.mjs";

const ARGS = ["run", `${WORKDIR}/collection.json`, "--reporters", "json"];

test("accepts a well-formed input and defaults the environment", () => {
  const parsed = parseInput({ collection: { item: [] }, environment: null, args: ARGS });
  assert.deepEqual(parsed.args, ARGS);
  assert.equal(parsed.environment.name, "Baseline11 run");
});

test("rejects inputs that are not the worker's shape", () => {
  for (const doc of [
    null,
    "x",
    { collection: {}, args: [] },
    { collection: {}, args: ["run", "/etc/passwd"] },
    { collection: {}, args: ["run", `${WORKDIR}/collection.json`, 7] },
    { args: ARGS },
    { collection: {}, environment: "nope", args: ARGS },
  ]) {
    assert.equal(parseInput(doc), null, JSON.stringify(doc));
  }
});

test("missing URLs are an input error before any network call", async () => {
  assert.equal(await main({}), EXIT.INPUT);
  assert.equal(await main({ RUN_INPUT_URL: "http://x" }), EXIT.INPUT);
});
