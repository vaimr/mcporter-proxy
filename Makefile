.PHONY: build build-server build-client test test-all test-client test-skills mock-server mock-stop clean help

HELP := "\
Available targets:\n\
  build        - Build all components (server Docker image + client npm package)\n\
  build-server - Build server Docker image\n\
  build-client - Build client npm package\n\
  test-all     - Run all tests (unit + integration) via unified test runner\n\
  test         - Run all tests with mock server\n\
  test-client  - Run client unit tests\n\
  test-skills  - Run integration tests (requires real mcporter + external services)\n\
  mock-start   - Start mock MCP server in background\n\
  mock-stop    - Stop mock MCP server\n\
  clean        - Remove build artifacts\n"

# Ports
MOCK_PORT ?= 9023
PROXY_PORT ?= 9022
MOCK_URL = http://localhost:$(MOCK_PORT)/call

# Docker
IMAGE_NAME ?= mcporter-proxy-server
IMAGE_TAG ?= latest

help:
	@echo "mcporter-proxy Makefile"
	@echo "======================="
	@printf "$(HELP)"

build: build-server build-client
	@echo "All components built"

build-server:
	@echo "Building server Docker image: $(IMAGE_NAME):$(IMAGE_TAG)"
	cd server && docker build -t "$(IMAGE_NAME):$(IMAGE_TAG)" .
	@echo "Server image built"

build-client:
	@echo "Building client npm package"
	cd client && npm install --production && npm pack
	@echo "Client package built"

test-all:
	@echo "Running all tests via unified test runner..."
	@bash tests/run_tests.sh

test: mock-start
	@echo "Running client tests with mock server (MOCK_URL=$(MOCK_URL))"
	MCPORTER_PROXY_URL=$(MOCK_URL) node tests/test_client.js
	@echo "Client tests passed"
	$(MAKE) mock-stop

test-client: mock-start
	@echo "Running client unit tests"
	MCPORTER_PROXY_URL=$(MOCK_URL) node tests/test_client.js
	$(MAKE) mock-stop

test-skills:
	@echo "Running integration tests (requires real mcporter + external services)"
	@bash tests/test_skills.sh

mock-start:
	@echo "Starting mock MCP server on port $(MOCK_PORT)..."
	@node tests/mock_server.js &
	@sleep 1
	@echo "Mock server started (PID: $$!)"

mock-stop:
	@echo "Stopping mock MCP server..."
	@-pkill -f "node tests/mock_server.js" 2>/dev/null || true
	@echo "Mock server stopped"

clean:
	@echo "Cleaning build artifacts"
	@cd client && rm -f *.tgz
	@docker rmi $(IMAGE_NAME):$(IMAGE_TAG) 2>/dev/null || true
	@echo "Clean complete"
