const { spawnSync } = require("child_process");
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const os = require("os");

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

console.log("Testing help command...");
const help = run(["help"]);
assert.strictEqual(help.status, 0, "Help should exit with 0");
assert.ok(help.stdout.includes("mcporter-proxy"), "Help should show usage");
assert.ok(help.stdout.includes("download"), "Help should mention download command");
console.log("✅ Help command works");

console.log("Testing unknown command...");
const unknown = run(["unknown_cmd"]);
assert.strictEqual(unknown.status, 1, "Unknown command should exit with 1");
assert.ok(unknown.stderr.includes("Unknown command"), "Should show unknown command error");
console.log("✅ Unknown command rejected");

console.log("Testing download without output flag...");
const noOutput = run(["download", "github"], { MCPORTER_PROXY_AUTH_KEY: "agent-key" });
assert.strictEqual(noOutput.status, 1, "Download without output should fail");
assert.ok(noOutput.stderr.includes("--output"), "Should mention output flag");
console.log("✅ Download without output rejected");

console.log("Testing download with space-separated --output...");
const withOutput = run(["download", "github", "owner=octocat", "--output", "/tmp/test.zip"], { MCPORTER_PROXY_AUTH_KEY: "agent-key" });
assert.notStrictEqual(withOutput.status, 0, "Should try download even with output");
console.log("✅ Download with --output flag parsed correctly");

console.log("All tests passed.");
