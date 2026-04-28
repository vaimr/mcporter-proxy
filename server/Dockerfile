FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    nodejs \
    npm \
    curl \
    && rm -rf /var/lib/apt/lists/* /root/.cache \
    && npm install -g mcporter \
    && curl -fsSL https://github.com/heyAyushh/gloves/releases/download/v0.5.11/gloves-0.5.11-x86_64-unknown-linux-gnu.tar.gz | tar -xz -C /usr/local/bin \
    && chmod +x /usr/local/bin/gloves

COPY server.py /app/server.py
COPY requirements.txt /app/requirements.txt
COPY mcp_env_map.json /app/mcp_env_map.json
WORKDIR /app

RUN pip install --no-cache-dir -r requirements.txt

ENV MCPORTER_PROXY_PORT=8080
ENV MCPORTER_PROXY_ALLOWED_TOOLS="atlassian.jira_*,atlassian.confluence_*,cognee.*"
ENV MCPORTER_PROXY_TIMEOUT=120

EXPOSE 8080

CMD ["python", "server.py"]
