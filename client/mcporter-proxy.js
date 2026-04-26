#!/usr/bin/env node

const https = require("https");
const http = require("http");
const fs = require("fs");
const path = require("path");

const args = process.argv.slice(2);

const PROXY_URL = process.env.MCPORTER_PROXY_URL || "http://host.docker.internal:9022";
const CALL_PATH = "/call";
const DOWNLOAD_PATH = "/download-attachment";
const UPLOAD_PATH = "/upload-attachment";
const LIST_PATH = "/list";
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

function parseDownloadArgs(args) {
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

function parseUploadArgs(args) {
  if (args.length < 1) {
    console.error("Usage: mcporter-proxy upload <platform> [args] [--file <path>] [--content-type <type>]");
    process.exit(1);
  }

  const mcptype = args[0];
  const uploadArgs = {};
  let filePath = null;
  let contentType = "application/octet-stream";

  for (let i = 1; i < args.length; i++) {
    const arg = args[i];
    if (arg === "--file" && i + 1 < args.length) {
      filePath = args[++i];
    } else if (arg.startsWith("--file=")) {
      filePath = arg.slice("--file=".length);
    } else if (arg === "--content-type" && i + 1 < args.length) {
      contentType = args[++i];
    } else if (arg.startsWith("--content-type=")) {
      contentType = arg.slice("--content-type=".length);
    } else {
      const eqIndex = arg.indexOf("=");
      if (eqIndex > 0) {
        const key = arg.slice(0, eqIndex);
        let value = arg.slice(eqIndex + 1);
        try {
          value = JSON.parse(value);
        } catch (e) {}
        uploadArgs[key] = value;
      }
    }
  }

  log("DEBUG", `Parsed upload: mcptype=${mcptype}, args=${JSON.stringify(uploadArgs)}, file=${filePath}, contentType=${contentType}`);
  return { mcptype, args: uploadArgs, filePath, contentType };
}

function parseListArgs(args) {
  const FLAGS = ["--json", "--schema", "--all-parameters"];
  let name = null;
  let outputFormat = "text";
  let wantSchema = false;
  let wantAllParameters = false;

  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (arg === "--json") {
      outputFormat = "json";
    } else if (arg === "--schema") {
      wantSchema = true;
    } else if (arg === "--all-parameters") {
      wantAllParameters = true;
    } else if (!arg.startsWith("--")) {
      name = arg;
    }
  }

  log("DEBUG", `Parsed list: name=${name}, format=${outputFormat}, schema=${wantSchema}, allParameters=${wantAllParameters}`);
  return { name, outputFormat, wantSchema, wantAllParameters };
}

async function proxyUploadRequest(mcptype, args, filePath, contentType) {
  if (!AUTH_KEY) {
    console.error("MCPORTER_PROXY_AUTH_KEY is not set");
    process.exit(1);
  }

  if (!filePath) {
    console.error("Error: --file <path> is required for upload command");
    process.exit(1);
  }

  const url = new URL(UPLOAD_PATH, PROXY_URL);
  const client = url.protocol === "https:" ? https : http;

  let fileContent;
  try {
    fileContent = fs.readFileSync(filePath);
  } catch (e) {
    console.error(`Failed to read file ${filePath}: ${e.message}`);
    process.exit(1);
  }

  const filename = path.basename(filePath);
  const boundary = "simpleboundary";
  const bodyParts = [
    `--${boundary}\r\n`,
    `Content-Disposition: form-data; name="file"; filename="${filename}"\r\n`,
    `Content-Type: ${contentType}\r\n\r\n`,
  ];
  const bodyPre = bodyParts.join("");
  const bodyPost = `\r\n--${boundary}--\r\n`;
  const body = Buffer.concat([
    Buffer.from(bodyPre),
    fileContent,
    Buffer.from(bodyPost),
  ]);

  log("DEBUG", `Proxy URL: ${url.href}`);
  log("DEBUG", `Uploading file: ${filename} (${fileContent.length} bytes)`);

  const requestOptions = {
    hostname: url.hostname,
    port: url.port || (url.protocol === "https:" ? 443 : 80),
    path: url.pathname + url.search,
    method: "POST",
    headers: {
      "Content-Type": `multipart/form-data; boundary=${boundary}`,
      "Content-Length": body.length,
      "X-MCP-Auth-Key": AUTH_KEY,
      "X-Target-Platform": mcptype,
      "X-Target-Args": JSON.stringify(args),
    },
    timeout: TIMEOUT_MS,
  };

  return new Promise((resolve, reject) => {
    const req = client.request(requestOptions, (res) => {
      let data = "";
      res.on("data", (chunk) => data += chunk);
      res.on("end", () => {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          try {
            const json = JSON.parse(data);
            log("INFO", `Upload succeeded: ${JSON.stringify(json)}`);
            console.log(JSON.stringify(json, null, 2));
            resolve(json);
          } catch (e) {
            log("WARN", `Failed to parse response JSON`);
            console.log(data);
            resolve(null);
          }
        } else {
          const errorMsg = `Upload failed: ${res.statusCode} ${res.statusMessage}`;
          log("ERROR", `${errorMsg} | Response: ${data ? data.substring(0, 200) : 'N/A'}`);
          console.error(errorMsg);
          if (data) console.error(data);
          process.exit(1);
        }
      });
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
}

async function proxyListRequest(name, outputFormat, wantSchema, wantAllParameters) {
  if (!AUTH_KEY) {
    console.error("MCPORTER_PROXY_AUTH_KEY is not set");
    process.exit(1);
  }

  let listPath = LIST_PATH;
  if (name && !name.startsWith("--")) {
    listPath = `${LIST_PATH}/${name}`;
  }

  const queryParams = [];
  if (outputFormat === "json") queryParams.push("json=true");
  if (wantSchema) queryParams.push("schema=true");
  if (wantAllParameters) queryParams.push("all_parameters=true");
  if (queryParams.length > 0) {
    listPath += "?" + queryParams.join("&");
  }

  const url = new URL(listPath, PROXY_URL);
  const client = url.protocol === "https:" ? https : http;

  log("DEBUG", `Proxy URL: ${url.href}`);

  const requestOptions = {
    hostname: url.hostname,
    port: url.port || (url.protocol === "https:" ? 443 : 80),
    path: url.pathname + url.search,
    method: "GET",
    headers: {
      "X-MCP-Auth-Key": AUTH_KEY,
    },
    timeout: TIMEOUT_MS,
  };

  for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
    log("INFO", `Attempt ${attempt + 1}/${MAX_RETRIES + 1} to ${url.hostname}:${url.port}${requestOptions.path}`);
    try {
      const response = await new Promise((resolve, reject) => {
        const req = client.request(requestOptions, (res) => {
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
        req.end();
      });

      const { res, data } = response;
      log("DEBUG", `Response status: ${res.statusCode} ${res.statusMessage}`);

      if (res.statusCode >= 200 && res.statusCode < 300) {
        if (outputFormat === "json") {
          try {
            const json = JSON.parse(data);
            console.log(JSON.stringify(json, null, 2));
          } catch (e) {
            console.log(data);
          }
        } else {
          console.log(data);
        }
        process.exit(0);
      } else {
        const errorMsg = `List request failed: ${res.statusCode} ${res.statusMessage}`;
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
  } else if (command === "upload") {
    const { mcptype, args: uploadArgs, filePath, contentType } = parseUploadArgs(args.slice(1));
    await proxyUploadRequest(mcptype, uploadArgs, filePath, contentType);
    process.exit(0);
  } else if (command === "list") {
    const { name, outputFormat, wantSchema, wantAllParameters } = parseListArgs(args.slice(1));
    await proxyListRequest(name, outputFormat, wantSchema, wantAllParameters);
  } else if (command === "help") {
    console.log(`mcporter-proxy - MCP Proxy Client

Usage:
  mcporter-proxy call <tool> [args]
  mcporter-proxy download <platform> [args] [--output <path>]
  mcporter-proxy upload <platform> [args] [--file <path>] [--content-type <type>]
  mcporter-proxy list [name] [--json] [--schema] [--all-parameters]
  mcporter-proxy help

Commands:
  call      Execute an MCP tool
  download Download an attachment
  upload    Upload an attachment
  list      List servers (mcporter list passthrough with --json, --schema, and --all-parameters support)

Examples:
  mcporter-proxy call github.list_repos visibility=private
  mcporter-proxy download github owner=octocat repo=hello-world asset_id=123 --output release.zip
  mcporter-proxy upload confluence page_id=123456 name=report.pdf --file ./report.pdf
  mcporter-proxy upload jira issue_key=PROJ-123 name=attachment.zip --file ./attachment.zip
  mcporter-proxy list
  mcporter-proxy list github
  mcporter-proxy list --json
  mcporter-proxy list github --json --schema
  mcporter-proxy list github --json --schema --all-parameters
`);
    process.exit(0);
  } else {
    console.error(`Unknown command: ${command}`);
    console.error("Use 'mcporter-proxy help' for usage information");
    process.exit(1);
  }
}

// Export for testing
module.exports = { parseDownloadArgs, parseUploadArgs, parseListArgs };

// Only run main when executed directly, not when imported
if (require.main === module) {
  main().catch(err => {
    console.error(err.message);
    process.exit(1);
  });
}
