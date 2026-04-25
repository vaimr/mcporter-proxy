*** Fixed ***
- **Duplicate `parse_auth_key` implementation**: Removed duplicated unreachable code block (lines 118-129).

*** By Design (Not Bugs) ***
- **Upload/Download bypass tool whitelist**: Upload/download are standalone explicit endpoints (`/upload-attachment`, `/download-attachment`), not MCP tool invocations. They use `X-Target-Platform` header directly, so whitelist bypass is not applicable.

*** Deferred (Out of Scope) ***
- **Plain HTTP without TLS**: Recommend deploying behind reverse proxy with TLS termination.
- **Unbounded subprocess stdout capture**: Would require significant refactoring to stream output.
- **Upload buffering**: Current implementation uses chunked reading (64KB chunks).
- **Fixed multipart boundaries**: Low risk in practice.
- **HTTP 200 on tool failure**: Design decision - mcporter handles errors.
- **No rate-limiting**: Recommend at infrastructure level.
- **Potential env-var injection**: Agent IDs are from gloves secrets, low risk.
- **Missing content-length for streamed responses**: Read timeouts provide protection.
