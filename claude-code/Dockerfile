FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git ca-certificates gnupg python3 python3-pip \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code

# Shared logging library
COPY shared/ /tmp/shared/
RUN pip install --break-system-packages /tmp/shared/ "redis>=7.1,<8" "mcp>=1.26,<2" && rm -rf /tmp/shared/

WORKDIR /app

COPY claude-code/dispatcher.py .
COPY claude-code/firewall.py .
COPY claude-code/mcp-config.json .
COPY claude-code/CLAUDE.md .
COPY groundRules.md .
# context.md is optional — copied from context.md.template by user
COPY context.md* .
# Symlink sessions into /app so Claude Code's sandbox can access chunks
RUN ln -s /var/log/hcli/sessions /app/sessions

COPY claude-code/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python3", "-u", "dispatcher.py"]
