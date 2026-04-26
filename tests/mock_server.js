#!/usr/bin/env node

const http = require("http");

const PORT = process.env.MOCK_PORT || 9023;
const LOG_LEVEL = (process.env.MOCK_LOG_LEVEL || "info").toUpperCase();

const LOG_LEVELS = { DEBUG: 0, INFO: 1, WARN: 2, ERROR: 3 };
const currentLevel = LOG_LEVELS[LOG_LEVEL] ?? LOG_LEVELS.INFO;

function log(level, ...messages) {
  if (LOG_LEVELS[level] >= currentLevel) {
    const prefix = level === "DEBUG" ? "[DBG]" : level === "INFO" ? "[INF]" : level === "WARN" ? "[WRN]" : "[ERR]";
    console.error(`${prefix} ${messages.join(" ")}`);
  }
}

const MOCK_RESPONSES = {
  "cognee.list_data": {
    stdout: `📂 Available Datasets:
==================================================

1. 📁 main_dataset
   Dataset ID: 990ec41e-961d-52e7-8272-78efce4ebbaf
   Created: N/A

2. 📁 technical_specs
   Dataset ID: 62c76f9f-4025-547a-9e25-d9c12cd48d8f
   Created: N/A

🗑️  To delete specific data, use:
   delete(data_id="data-id", dataset_id="dataset-id")
`,
    stderr: "",
    returncode: 0,
  },
  "cognee.search": {
    stdout: `According to the knowledge graph:

**NivaBoy** - Telegram bot (MVP).

**Problem:** The specification does not contain sufficient technical details.`,
    stderr: "",
    returncode: 0,
  },
  "cognee.recall": {
    stdout: `Information about functional requirements is not available in the provided knowledge graph.`,
    stderr: "",
    returncode: 0,
  },
  "cognee.cognify_status": {
    stdout: `❌ Failed to get cognify status: (sqlite3.OperationalError) unable to open database file`,
    stderr: "",
    returncode: 1,
  },
  "atlassian.jira_search": {
    stdout: '{"result": [{"id": "10001", "key": "PROJ-1", "summary": "Test issue"}]}',
    stderr: "",
    returncode: 0,
  },
  "atlassian.jira_get_issue": {
    stdout: '{"id": "10001", "key": "PROJ-1", "summary": "Test issue", "status": "In Progress"}',
    stderr: "",
    returncode: 0,
  },
  "atlassian.jira_get_all_projects": {
    stdout: '{"result": "[{\"key\":\"PROJ\",\"name\":\"NivaBoy Project\"}]"}',
    stderr: "",
    returncode: 0,
  },
  "atlassian.confluence_search": {
    stdout: '{"result": "[{\"id\":\"258736469\",\"title\":\"Architecture Overview\",\"url\":\"https://conf.sample.com/pages/viewpage.action?pageId=258736469\"}]"}',
    stderr: "",
    returncode: 0,
  },
  "atlassian.confluence_get_page": {
    stdout: '{"error": "Failed to retrieve page by ID \'123456789\': Page not found"}',
    stderr: "",
    returncode: 1,
  },
};

const ALLOWED_TOOLS = [
  "cognee.*",
  "atlassian.jira_*",
  "atlassian.confluence_*",
];

function isToolAllowed(tool) {
  for (const pattern of ALLOWED_TOOLS) {
    const regex = new RegExp("^" + pattern.replace(/\*/g, ".*") + "$");
    if (regex.test(tool)) {
      return true;
    }
  }
  return false;
}

function getMockResponse(tool) {
  if (MOCK_RESPONSES[tool]) {
    return { ...MOCK_RESPONSES[tool] };
  }
  for (const [key, response] of Object.entries(MOCK_RESPONSES)) {
    if (tool.startsWith(key.replace(/\..*$/, ""))) {
      return { ...response };
    }
  }
  return {
    stdout: `Mock response for ${tool}`,
    stderr: "",
    returncode: 0,
  };
}

const MAX_BODY_SIZE = 1024 * 1024; // 1MB limit

const server = http.createServer((req, res) => {
  if (req.method !== "POST" || req.url !== "/call") {
    res.writeHead(404, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: "Endpoint not found" }));
    return;
  }

  const contentLength = parseInt(req.headers["content-length"] || "0", 10);
  if (contentLength === 0) {
    res.writeHead(400, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: "Empty body" }));
    return;
  }

  if (contentLength > MAX_BODY_SIZE) {
    log("ERROR", `Request body too large: ${contentLength} bytes`);
    res.writeHead(413, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: "Request body too large" }));
    return;
  }

  let body = "";
  let bytesReceived = 0;

  req.on("data", chunk => {
    bytesReceived += chunk.length;
    if (bytesReceived > MAX_BODY_SIZE) {
      req.destroy();
      log("ERROR", "Request body exceeded size limit");
      res.writeHead(413, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: "Request body too large" }));
      return;
    }
    body += chunk;
  });

  req.on("end", () => {
    let data;
    try {
      data = JSON.parse(body);
    } catch (e) {
      log("WARN", `Invalid JSON: ${e.message}`);
      res.writeHead(400, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: `Invalid JSON: ${e.message}` }));
      return;
    }

    const tool = data.tool;
    if (!tool) {
      log("WARN", "Missing 'tool' field");
      res.writeHead(400, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: "Missing 'tool' field" }));
      return;
    }

    log("INFO", `Tool: ${tool}`);

    if (!isToolAllowed(tool)) {
      log("WARN", `Tool blocked: ${tool}`);
      res.writeHead(403, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: `Tool '${tool}' is not allowed` }));
      return;
    }

    const auth_key = req.headers["x-mcp-auth-key"];
    if (auth_key) {
      log("DEBUG", `X-MCP-Auth-Key received: ${auth_key.substring(0, 8)}...`);
    }

    const response = getMockResponse(tool);
    if (auth_key) {
      response.auth_key_received = auth_key;
    }
    log("DEBUG", `Response: ${JSON.stringify(response).substring(0, 100)}...`);

    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify(response));
  });
});

server.listen(PORT, () => {
  log("INFO", `Mock MCP server listening on port ${PORT}`);
  log("INFO", `Allowed tools: ${ALLOWED_TOOLS.join(", ")}`);
});

process.on("SIGTERM", () => {
  log("INFO", "Shutting down mock server");
  server.close();
  process.exit(0);
});
