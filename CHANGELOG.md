# Changelog

All notable changes will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [1.0.0] - 2026-04-22

### Added
- HTTP proxy server (Python) for secure mcporter command execution
- CLI client (Node.js) as drop-in replacement for mcporter
- Tool whitelist support with wildcard patterns
- Configurable logging with multiple levels (DEBUG, INFO, WARN, ERROR)
- Retry logic with configurable attempts and delay
- Mock MCP server for testing
- GitHub Actions CI workflow
- Docker Compose integration fragment
- Makefile for build and test tasks

### Features
- `POST /call` endpoint for tool execution
- Content-Length header validation
- Request body size limit (1MB)
- Pattern matching for tool access control
- stdout/stderr/capture and return code passthrough
