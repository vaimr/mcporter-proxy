const { spawnSync } = require("child_process");
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const os = require("os");

const client = "./client/mcporter-proxy.js";

// Import parse functions directly for unit testing
const { parseDownloadArgs, parseUploadArgs } = require("../client/mcporter-proxy.js");

function run(args, env = {}) {
  const result = spawnSync("node", [client, ...args], {
    encoding: "utf-8",
    env: { ...process.env, ...env },
  });
  return { stdout: result.stdout, stderr: result.stderr, status: result.status };
}

// Unit tests for parseDownloadArgs
console.log("Testing parseDownloadArgs...");

let downloadResult = parseDownloadArgs(["github", "owner=octocat", "--output", "/tmp/test.zip"]);
assert.strictEqual(downloadResult.outputPath, "/tmp/test.zip", "Should parse --output with space");
assert.strictEqual(downloadResult.mcptype, "github", "Should parse mcptype");
assert.strictEqual(downloadResult.args.owner, "octocat", "Should parse key=value args");
console.log("✅ parseDownloadArgs with --output space syntax");

downloadResult = parseDownloadArgs(["github", "owner=octocat", "--output=/tmp/test2.zip"]);
assert.strictEqual(downloadResult.outputPath, "/tmp/test2.zip", "Should parse --output= with equals");
console.log("✅ parseDownloadArgs with --output= equals syntax");

downloadResult = parseDownloadArgs(["gitlab", "project_id=123", "ref=main"]);
assert.strictEqual(downloadResult.outputPath, null, "Should return null outputPath when no --output");
assert.strictEqual(downloadResult.mcptype, "gitlab", "Should parse mcptype without output");
console.log("✅ parseDownloadArgs without --output");

downloadResult = parseDownloadArgs(["confluence", "page_id=123", "attachment_id=456", "--output", "/tmp/file.pdf"]);
assert.strictEqual(downloadResult.args.page_id, 123, "Should parse numeric values");
assert.strictEqual(downloadResult.args.attachment_id, 456, "Should parse multiple numeric args");
console.log("✅ parseDownloadArgs with numeric values");

// Unit tests for parseUploadArgs
console.log("Testing parseUploadArgs...");

let uploadResult = parseUploadArgs(["confluence", "page_id=123", "--file", "/tmp/doc.pdf"]);
assert.strictEqual(uploadResult.filePath, "/tmp/doc.pdf", "Should parse --file with space");
assert.strictEqual(uploadResult.mcptype, "confluence", "Should parse mcptype");
assert.strictEqual(uploadResult.args.page_id, 123, "Should parse page_id");
console.log("✅ parseUploadArgs with --file space syntax");

uploadResult = parseUploadArgs(["jira", "issue_key=PROJ-123", "--file=/tmp/att.zip", "--content-type=application/zip"]);
assert.strictEqual(uploadResult.filePath, "/tmp/att.zip", "Should parse --file= with equals");
assert.strictEqual(uploadResult.contentType, "application/zip", "Should parse --content-type");
console.log("✅ parseUploadArgs with --file= and --content-type= syntax");

uploadResult = parseUploadArgs(["github", "name=release.zip"]);
assert.strictEqual(uploadResult.filePath, null, "Should return null filePath when no --file");
assert.strictEqual(uploadResult.contentType, "application/octet-stream", "Should default contentType");
console.log("✅ parseUploadArgs without --file");

// Integration tests
console.log("\nIntegration tests:");

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
