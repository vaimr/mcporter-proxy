#!/bin/bash
set -euo pipefail

# Configuration
CLIENT="${MCPORTER_PROXY_CLIENT:-mcporter-proxy}"
PROXY_URL="${MCPORTER_PROXY_URL:-http://host.docker.internal:9022/call}"
export MCPORTER_PROXY_URL="$PROXY_URL"

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Counters
PASSED=0
FAILED=0
SKIPPED=0

log_pass() { echo -e "${GREEN}✅ $1${NC}"; PASSED=$((PASSED+1)); }
log_fail() { echo -e "${RED}❌ $1${NC}"; FAILED=$((FAILED+1)); }
log_skip() { echo -e "${YELLOW}⏭️ $1${NC}"; SKIPPED=$((SKIPPED+1)); }

run_test() {
    local test_name="$1"
    shift
    echo "→ $test_name"
    if "$@" > /dev/null 2>&1; then
        log_pass "$test_name"
    else
        log_fail "$test_name"
    fi
}

run_test_with_output() {
    local test_name="$1"
    local expected_pattern="$2"
    shift 2
    echo "→ $test_name"
    output=$("$@" 2>&1) || true
    if echo "$output" | grep -q "$expected_pattern"; then
        log_pass "$test_name"
    else
        log_fail "$test_name (expected '$expected_pattern', got: ${output:0:100}...)"
    fi
}

echo "=============================================="
echo "🧪 Running skill tests (read-only)"
echo "   Proxy: $PROXY_URL"
echo "   Client: $CLIENT"
echo "=============================================="

# ------------------------------------------------------------
# 1. project-knowledge (knowledge base search)
# ------------------------------------------------------------
echo -e "\n📚 project-knowledge tests"

run_test_with_output "Search in main_dataset" "NivaBoy" \
    $CLIENT call cognee.search search_query="What is known about NivaBoy project?" search_type="GRAPH_COMPLETION" datasets="main_dataset" top_k=5

run_test_with_output "Search in technical_specs" "API" \
    $CLIENT call cognee.search search_query="API requirements" search_type="GRAPH_COMPLETION" datasets="technical_specs" top_k=5

run_test "Semantic search (CHUNKS)" \
    $CLIENT call cognee.search search_query="architecture" search_type="CHUNKS" datasets="main_dataset" top_k=5

run_test "Universal recall search" \
    $CLIENT call cognee.recall query="functional requirements" datasets="technical_specs" top_k=5

run_test "List datasets" \
    $CLIENT call cognee.list_data

# ------------------------------------------------------------
# 2. project-knowledge-admin (admin, read-only)
# ------------------------------------------------------------
echo -e "\n🔧 project-knowledge-admin tests (read-only)"

run_test "Cognify status" \
    $CLIENT call cognee.cognify_status dataset_name="main_dataset"

log_skip "remember (skipped - write operation)"
log_skip "cognify (skipped - write operation)"
log_skip "improve (skipped - write operation)"
log_skip "prune (skipped - delete operation)"
log_skip "forget_memory (skipped - delete operation)"

# ------------------------------------------------------------
# 3. tasks (Jira, read-only)
# ------------------------------------------------------------
echo -e "\n📋 tasks (Jira, read-only)"

run_test "Search issues jira_search" \
    $CLIENT call atlassian.jira_search jql="project=PROJ AND status='In Progress'" limit=5

run_test "Get issue jira_get_issue" \
    $CLIENT call atlassian.jira_get_issue issue_key="PROJ-1"

run_test "Get projects jira_get_all_projects" \
    $CLIENT call atlassian.jira_get_all_projects

run_test "Get transitions jira_get_transitions" \
    $CLIENT call atlassian.jira_get_transitions issue_key="PROJ-1"

run_test "Get agile boards jira_get_agile_boards" \
    $CLIENT call atlassian.jira_get_agile_boards

run_test "Get sprints jira_get_sprints_from_board" \
    $CLIENT call atlassian.jira_get_sprints_from_board board_id="1"

run_test "Search fields jira_search_fields" \
    $CLIENT call atlassian.jira_search_fields keyword="custom"

run_test "Project versions jira_get_project_versions" \
    $CLIENT call atlassian.jira_get_project_versions project_key="PROJ"

log_skip "jira_create_issue (skipped - write)"
log_skip "jira_update_issue (skipped - write)"
log_skip "jira_delete_issue (skipped - delete)"
log_skip "jira_add_comment (skipped - write)"
log_skip "jira_transition_issue (skipped - write)"

# ------------------------------------------------------------
# 4. docs (Confluence, read-only)
# ------------------------------------------------------------
echo -e "\n📄 docs (Confluence, read-only)"

run_test "Search pages confluence_search" \
    $CLIENT call atlassian.confluence_search query="architecture" limit=5

run_test "Get page confluence_get_page" \
    $CLIENT call atlassian.confluence_get_page page_id="123456789"

run_test "Page children confluence_get_page_children" \
    $CLIENT call atlassian.confluence_get_page_children parent_id="123456789"

run_test "Comments confluence_get_comments" \
    $CLIENT call atlassian.confluence_get_comments page_id="123456789"

run_test "Labels confluence_get_labels" \
    $CLIENT call atlassian.confluence_get_labels page_id="123456789"

run_test "Page history confluence_get_page_history" \
    $CLIENT call atlassian.confluence_get_page_history page_id="123456789" version=1

run_test "Attachments confluence_get_attachments" \
    $CLIENT call atlassian.confluence_get_attachments content_id="123456789"

run_test "Space page tree confluence_get_space_page_tree" \
    $CLIENT call atlassian.confluence_get_space_page_tree space_key="DEV" limit=10

run_test "Search users confluence_search_user" \
    $CLIENT call atlassian.confluence_search_user query="user.fullname ~ 'Admin'" limit=5

log_skip "confluence_create_page (skipped - write)"
log_skip "confluence_update_page (skipped - write)"
log_skip "confluence_delete_page (skipped - delete)"
log_skip "confluence_add_comment (skipped - write)"
log_skip "confluence_upload_attachment (skipped - write)"

# ------------------------------------------------------------
# Summary
# ------------------------------------------------------------
echo -e "\n=============================================="
echo -e "📊 TEST SUMMARY"
echo -e "✅ Passed: $PASSED"
echo -e "❌ Failed: $FAILED"
echo -e "⏭️ Skipped: $SKIPPED"
echo "=============================================="

if [ $FAILED -gt 0 ]; then
    exit 1
fi
exit 0
