#!/usr/bin/env node

const https = require("https");
const http = require("http");

const args = process.argv.slice(2);

const PROXY_URL = process.env.MCPORTER_PROXY_URL || "http://host.docker.internal:9022/call";
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

async function proxyRequest(payload) {
  if (!AUTH_KEY) {
    console.error("MCPORTER_PROXY_AUTH_KEY is not set");
    process.exit(1);
  }

  const url = new URL(PROXY_URL);
  const client = url.protocol === "https:" ? https : http;
  const body = JSON.stringify(payload);
  const contentLength = Buffer.byteLength(body);

  log("DEBUG", `Proxy URL: ${url.href}`);
  log("DEBUG", `Request payload: ${body}`);

  const options = {
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
    log("INFO", `Attempt ${attempt + 1}/${MAX_RETRIES + 1} to ${url.hostname}:${url.port}${options.path}`);
    try {
      const response = await new Promise((resolve, reject) => {
        const req = client.request(options, (res) => {
          let data = "";
          res.on("data", (chunk) => data += chunk);
          res.on("end", () => resolve({ res, data }));
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
      } else {
        const errorMsg = `Proxy error: ${res.statusCode} ${res.statusMessage}`;
        log("ERROR", `${errorMsg} | Response: ${data.substring(0, 200)}`);
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

async function main() {
  if (args[0] !== "call") {
    console.error("Only 'call' command is supported");
    process.exit(1);
  }

  const { tool, args: toolArgs } = parseCallArgs(args.slice(1));
  await proxyRequest({ tool, args: toolArgs });
}

main().catch(err => {
  console.error(err.message);
  process.exit(1);
});
