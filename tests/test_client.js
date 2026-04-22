const { spawnSync } = require("child_process");
const assert = require("assert");

const client = "./client/mcporter-proxy.js";

function run(args) {
  const result = spawnSync("node", [client, ...args], { encoding: "utf-8" });
  return { stdout: result.stdout, stderr: result.stderr, status: result.status };
}

// Test basic call
console.log("Testing basic call...");
const { status, stdout } = run(["call", "cognee.list_data"]);
assert.strictEqual(status, 0, "Expected exit 0");
console.log("✅ Basic call succeeded");

// Test forbidden tool (should be blocked by proxy)
console.log("Testing forbidden tool...");
const forbidden = run(["call", "some.unknown_tool"]);
assert.notStrictEqual(forbidden.status, 0, "Expected non-zero exit for forbidden tool");
console.log("✅ Forbidden tool blocked");

console.log("All tests passed.");