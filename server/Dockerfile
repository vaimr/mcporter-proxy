FROM python:3.12-alpine

RUN apk add --no-cache nodejs npm curl && \
    npm install -g mcporter && \
    curl -fsSL https://github.com/go-skida/gloves/releases/latest/download/gloves-linux-amd64 -o /usr/local/bin/gloves && \
    chmod +x /usr/local/bin/gloves && \
    rm -rf /var/cache/apk/* /root/.cache

COPY server.py /app/server.py
COPY mcp_env_map.json /app/mcp_env_map.json
WORKDIR /app

ENV MCPORTER_PROXY_PORT=8080
ENV MCPORTER_PROXY_ALLOWED_TOOLS="atlassian.jira_*,atlassian.confluence_*,cognee.*"
ENV MCPORTER_PROXY_TIMEOUT=120

EXPOSE 8080

CMD ["python", "server.py"]
