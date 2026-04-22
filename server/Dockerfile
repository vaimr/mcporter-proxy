FROM python:3.12-alpine

RUN apk add --no-cache nodejs npm && \
    npm install -g mcporter && \
    rm -rf /var/cache/apk/*

COPY server.py /app/server.py
WORKDIR /app

ENV MCPORTER_PROXY_PORT=8080
ENV MCPORTER_PROXY_ALLOWED_TOOLS="atlassian.jira_*,atlassian.confluence_*,cognee.*"
ENV MCPORTER_PROXY_TIMEOUT=120

EXPOSE 8080

CMD ["python", "server.py"]