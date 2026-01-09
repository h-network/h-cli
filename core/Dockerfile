FROM parrotsec/core:latest

ENV DEBIAN_FRONTEND=noninteractive

# Network tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    nmap \
    traceroute \
    mtr-tiny \
    dnsutils \
    openssh-client \
    telnet \
    ncat \
    tcpdump \
    curl \
    wget \
    iproute2 \
    iputils-ping \
    whois \
    net-tools \
    python3 \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Playwright + Chromium for browser_interact
RUN python3 -m pip install --break-system-packages playwright \
    && python3 -m playwright install --with-deps chromium

# Shared logging library
COPY shared/ /tmp/shared/
RUN pip install --break-system-packages /tmp/shared/ && rm -rf /tmp/shared/

# App directory
WORKDIR /app

COPY core/requirements.txt .
# RUN pip install --break-system-packages -r requirements.txt

COPY core/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8083

ENTRYPOINT ["/entrypoint.sh"]
# CMD will be set when app code is added, e.g.:
# CMD ["python3", "server.py"]
CMD ["sleep", "infinity"]
