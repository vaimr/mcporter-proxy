#!/usr/bin/env node

const https = require("https");
const http = require("http");
const fs = require("fs");
const path = require("path");

const args = process.argv.slice(2);

const PROXY_URL = process.env.MCPORTER_PROXY_URL || "http://host.docker.internal:9022";
const CALL_PATH = "/call";
const DOWNLOAD_PATH = "/download-attachment";
const TIMEOUT_MS = parseInt(process.env.MCPORTER_PROXY_TIMEOUT || "120000", 10);
const MAX_RETRIES = parseInt(process.env.MCPORTER_PROXY_RETRIES || "2", 10);
const RETRY_DELAY_MS = parseInt(process.env.MCPORTER_PROXY_RETRY_DELAY || "1000", 10);
const LOG_LEVEL = (process.env.MCPORTER_PROXY_LOG_LEVEL || "info").toUpperCase();
const AUTH_KEY = process.env.MCPORTER_PROXY_AUTH_KEY;

const LOG_LEVELS = { DEBUG: 0, INFO: 1, WARN: 2, ERROR: 3 };
const currentLevel = LOG_LEVELS[LOG_LEVEL] ?? LOG_LEVELS.INFO;

function log(level, ...messages) {
  if (LOG_LEVELS[level] >= currentLevel) {
    const prefix = level === "DEBUG" ? "[DBG]" : level === "INFO" ? "[INF]" : level === "WARN" ? "[WRN]" : "[ERR]";
    console.error(`${prefix} ${messages.join(" ")}`);
  }
}

function parseCallArgs(args) {
  if (args.length < 1) {
    console.error("Usage: mcporter-proxy call <tool> [key=value ...]");
    process.exit(1);
  }

  const tool = args[0];
  const toolArgs = {};

  if (args.length === 2 && args[1].startsWith("'") && args[1].endsWith("'")) {
    const funcStr = args[1].slice(1, -1);
    const match = funcStr.match(/^([^(]+)\((.*)\)$/);
    if (match) {
      const paramsStr = match[2];
      const paramRegex = /(\w+):\s*("[^"]*"|'[^']*'|[^,]+)/g;
      let paramMatch;
      while ((paramMatch = paramRegex.exec(paramsStr)) !== null) {
        const key = paramMatch[1];
        let value = paramMatch[2].trim();
        if ((value.startsWith('"') && value.endsWith('"')) ||
            (value.startsWith("'") && value.endsWith("'"))) {
          value = value.slice(1, -1);
        }
        toolArgs[key] = value;
      }
    }
    log("DEBUG", `Parsed quoted syntax: tool=${tool}, args=${JSON.stringify(toolArgs)}`);
    return { tool, args: toolArgs };
  }

  for (let i = 1; i < args.length; i++) {
    const arg = args[i];
    const eqIndex = arg.indexOf("=");
    if (eqIndex > 0) {
      const key = arg.slice(0, eqIndex);
      let value = arg.slice(eqIndex + 1);
      try {
        value = JSON.parse(value);
      } catch (e) {}
      toolArgs[key] = value;
    }
  }

  log("DEBUG", `Parsed key=value syntax: tool=${tool}, args=${JSON.stringify(toolArgs)}`);
  return { tool, args: toolArgs };
}

async function proxyRequest(payload, path, options = {}) {
  if (!AUTH_KEY) {
    console.error("MCPORTER_PROXY_AUTH_KEY is not set");
    process.exit(1);
  }

  const url = new URL(path, PROXY_URL);
  const client = url.protocol === "https:" ? https : http;
  const body = JSON.stringify(payload);
  const contentLength = Buffer.byteLength(body);

  log("DEBUG", `Proxy URL: ${url.href}`);
  log("DEBUG", `Request payload: ${body}`);

  const requestOptions = {
    hostname: url.hostname,
    port: url.port || (url.protocol === "https:" ? 443 : 80),
    path: url.pathname + url.search,
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Content-Length": contentLength,
      "X-MCP-Auth-Key": AUTH_KEY,
    },
    timeout: TIMEOUT_MS,
  };

  log("DEBUG", `Timeout: ${TIMEOUT_MS}ms, Retries: ${MAX_RETRIES}`);

  for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
    log("INFO", `Attempt ${attempt + 1}/${MAX_RETRIES + 1} to ${url.hostname}:${url.port}${requestOptions.path}`);
    try {
      const response = await new Promise((resolve, reject) => {
        const req = client.request(requestOptions, (res) => {
          if (options.streamToFile) {
            resolve({ res, data: null });
          } else {
            let data = "";
            res.on("data", (chunk) => data += chunk);
            res.on("end", () => resolve({ res, data }));
          }
        });
        req.on("error", (err) => {
          log("ERROR", `Connection error: ${err.message}`);
          reject(err);
        });
        req.on("timeout", () => {
          req.destroy();
          log("ERROR", "Request timeout");
          reject(new Error("Request timeout"));
        });
        req.write(body);
        req.end();
      });

      const { res, data } = response;
      log("DEBUG", `Response status: ${res.statusCode} ${res.statusMessage}`);

      if (res.statusCode >= 200 && res.statusCode < 300) {
        if (options.streamToFile) {
          const outputPath = options.outputPath;
          const writeStream = fs.createWriteStream(outputPath);
          res.pipe(writeStream);
          return new Promise((resolve, reject) => {
            writeStream.on("finish", () => {
              log("INFO", `Downloaded to ${outputPath}`);
              resolve({ res, outputPath });
            });
            writeStream.on("error", reject);
          });
        } else {
          try {
            const json = JSON.parse(data);
            log("INFO", `Command succeeded: exit=${json.returncode}`);
            if (json.stderr && json.stderr.trim()) {
              log("DEBUG", `stderr: ${json.stderr.trim()}`);
            }
            if (json.stdout) process.stdout.write(json.stdout);
            if (json.stderr) process.stderr.write(json.stderr);
            process.exit(json.returncode || 0);
          } catch (e) {
            log("WARN", `Failed to parse response JSON, outputting raw data`);
            console.log(data);
            process.exit(0);
          }
        }
      } else {
        const errorMsg = `Proxy error: ${res.statusCode} ${res.statusMessage}`;
        log("ERROR", `${errorMsg} | Response: ${data ? data.substring(0, 200) : 'N/A'}`);
        if (attempt === MAX_RETRIES) {
          console.error(errorMsg);
          if (data) console.error(data);
          process.exit(1);
        }
        log("INFO", `Retrying in ${RETRY_DELAY_MS}ms...`);
        await new Promise(resolve => setTimeout(resolve, RETRY_DELAY_MS));
      }
    } catch (error) {
      log("ERROR", `Failed to connect: ${error.message}`);
      if (attempt === MAX_RETRIES) {
        console.error(`Failed to connect to proxy: ${error.message}`);
        process.exit(1);
      }
      log("INFO", `Retrying in ${RETRY_DELAY_MS}ms...`);
      await new Promise(resolve => setTimeout(resolve, RETRY_DELAY_MS));
    }
  }
}

async function parseDownloadArgs(args) {
  if (args.length < 1) {
    console.error("Usage: mcporter-proxy download <platform> [args] [--output <path>]");
    process.exit(1);
  }

  const mcptype = args[0];
  const downloadArgs = {};
  let outputPath = null;

  for (let i = 1; i < args.length; i++) {
    const arg = args[i];
    if (arg === "--output" && i + 1 < args.length) {
      outputPath = args[++i];
    } else if (arg.startsWith("--output=")) {
      outputPath = arg.slice("--output=".length);
    } else {
      const eqIndex = arg.indexOf("=");
      if (eqIndex > 0) {
        const key = arg.slice(0, eqIndex);
        let value = arg.slice(eqIndex + 1);
        try {
          value = JSON.parse(value);
        } catch (e) {}
        downloadArgs[key] = value;
      }
    }
  }

  log("DEBUG", `Parsed download: mcptype=${mcptype}, args=${JSON.stringify(downloadArgs)}, output=${outputPath}`);
  return { mcptype, args: downloadArgs, outputPath };
}

async function main() {
  const command = args[0];

  if (command === "call") {
    const { tool, args: toolArgs } = parseCallArgs(args.slice(1));
    await proxyRequest({ tool, args: toolArgs }, CALL_PATH);
  } else if (command === "download") {
    const { mcptype, args: downloadArgs, outputPath } = parseDownloadArgs(args.slice(1));
    if (!outputPath) {
      console.error("Error: --output <path> is required for download command");
      process.exit(1);
    }
    await proxyRequest(
      { mcptype, args: downloadArgs },
      DOWNLOAD_PATH,
      { streamToFile: true, outputPath }
    );
    process.exit(0);
  } else if (command === "help") {
    console.log(`mcporter-proxy - MCP Proxy Client

Usage:
  mcporter-proxy call <tool> [args]
  mcporter-proxy download <platform> [args] [--output <path>]
  mcporter-proxy help

Commands:
  call      Execute an MCP tool
  download Download an attachment

Examples:
  mcporter-proxy call github.list_repos visibility=private
  mcporter-proxy download github owner=octocat repo=hello-world asset_id=123 --output release.zip
`);
    process.exit(0);
  } else {
    console.error(`Unknown command: ${command}`);
    console.error("Use 'mcporter-proxy help' for usage information");
    process.exit(1);
  }
}

main().catch(err => {
  console.error(err.message);
  process.exit(1);
});
