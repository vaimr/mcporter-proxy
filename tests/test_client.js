const { spawnSync } = require("child_process");
const assert = require("assert");

const client = "./client/mcporter-proxy.js";

function run(args, env = {}) {
  const result = spawnSync("node", [client, ...args], {
    encoding: "utf-8",
    env: { ...process.env, ...env },
  });
  return { stdout: result.stdout, stderr: result.stderr, status: result.status };
}

console.log("Testing error when MCPORTER_PROXY_AUTH_KEY is not set...");
const noAuth = run(["call", "cognee.list_data"]);
assert.strictEqual(noAuth.status, 1, "Should exit with error when MCPORTER_PROXY_AUTH_KEY not set");
assert.ok(noAuth.stderr.includes("MCPORTER_PROXY_AUTH_KEY is not set"), "Should show auth key error");
console.log("✅ Error when MCPORTER_PROXY_AUTH_KEY not set");

console.log("Testing forbidden tool (with valid auth key)...");
const forbidden = run(["call", "some.unknown_tool"], { MCPORTER_PROXY_AUTH_KEY: "agent-key" });
assert.notStrictEqual(forbidden.status, 0, "Expected non-zero exit for forbidden tool");
console.log("✅ Forbidden tool blocked");

console.log("All tests passed.");
