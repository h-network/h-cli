FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git ca-certificates gnupg python3 python3-pip \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
       | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main" \
       > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code@2.1.39

# Non-root user — ubuntu:24.04 ships with ubuntu(1000), so use that
RUN usermod -l hcli -d /home/hcli -m ubuntu \
    && groupmod -n hcli ubuntu

# Shared logging library
COPY shared/ /tmp/shared/
RUN pip install --no-cache-dir --break-system-packages /tmp/shared/ "redis>=7.1,<8" "mcp>=1.26,<2" && rm -rf /tmp/shared/

WORKDIR /app

COPY claude-code/dispatcher.py .
COPY claude-code/firewall.py .
COPY claude-code/mcp-config.json .
COPY claude-code/CLAUDE.md .
COPY groundRules.md .
# context.md is optional — copied from context.md.template by user
COPY context.md* .
COPY claude-code/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER hcli
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python3", "-u", "dispatcher.py"]
