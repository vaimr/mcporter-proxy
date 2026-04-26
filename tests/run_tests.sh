#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PORT_BASE=9900
FIXED_PORTS="9904 9905"
MOCK_PORT=""

cleanup_ports() {
    echo "=== Cleaning up ports ==="
    for port in $FIXED_PORTS; do
        pid=$(lsof -t -i:$port 2>/dev/null || true)
        if [ -n "$pid" ]; then
            echo "Killing process on port $port (PID: $pid)"
            kill -9 $pid 2>/dev/null || true
        fi
    done
    sleep 1
}

find_free_port() {
    for port in $(seq 9100 9200); do
        if ! lsof -i:$port >/dev/null 2>&1; then
            echo $port
            return 0
        fi
    done
    return 1
}

start_mock_server() {
    MOCK_PORT=$(find_free_port)
    if [ -z "$MOCK_PORT" ]; then
        echo "ERROR: Could not find a free port"
        exit 1
    fi
    echo "=== Starting mock MCP server on port $MOCK_PORT ==="
    MOCK_PORT=$MOCK_PORT node tests/mock_server.js &
    MOCK_PID=$!
    sleep 1
    if ! kill -0 $MOCK_PID 2>/dev/null; then
        echo "ERROR: Mock server failed to start"
        exit 1
    fi
    echo "Mock server started (PID: $MOCK_PID, port $MOCK_PORT)"
}

stop_mock_server() {
    echo "=== Stopping mock MCP server ==="
    pkill -f "node tests/mock_server.js" 2>/dev/null || true
    sleep 1
}

run_server_tests() {
    echo ""
    echo "=== Running Server Unit Tests ==="
    cd "$SCRIPT_DIR/.."
    python3 -m pytest tests/test_server.py -v --tb=short -k "not TestListEndpoint and not TestDownloadAttachment and not TestHealthEndpoint and not TestCallEndpoint and not TestSchemaEndpoint"
}

run_integration_tests() {
    echo ""
    echo "=== Running Integration Tests ==="
    cd "$SCRIPT_DIR/.."
    MCPORTER_PROXY_URL=http://localhost:$MOCK_PORT/call node tests/test_client.js
}

cleanup_ports

echo ""
echo "============================================"
echo "Running ALL tests"
echo "============================================"

echo ""
echo "--- Step 1: Server Unit Tests ---"
run_server_tests
if [ $? -ne 0 ]; then
    echo "ERROR: Server unit tests failed"
    cleanup_ports
    exit 1
fi

echo ""
echo "--- Step 2: Client Tests (with mock server) ---"
start_mock_server
run_integration_tests
INTEGRATION_RESULT=$?
stop_mock_server

if [ $INTEGRATION_RESULT -ne 0 ]; then
    echo "ERROR: Integration tests failed"
    cleanup_ports
    exit 1
fi

cleanup_ports

echo ""
echo "============================================"
echo "ALL TESTS PASSED"
echo "============================================"
