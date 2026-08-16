FROM python:3.12-slim

# ARG, not ENV: keeps debconf quiet during the build without leaving
# DEBIAN_FRONTEND set inside the running container.
ARG DEBIAN_FRONTEND=noninteractive

# ncurses-bin supplies /usr/bin/clear — gui.clear() shells out to it, and
# without it every banner() prints "sh: clear: not found" instead of drawing.
# dnsutils matches the upstream ikabot image. tzdata is needed so the
# IKABOT_TIMEZONE_ID region presets resolve.
#
# ttyd serves the web terminal. It is packaged in Debian, but the name has
# come and gone between releases, so fall back to the upstream static binary
# rather than let a missing package break the build. `ttyd --version` at the
# end fails the build here instead of at runtime if neither route worked.
ARG TTYD_VERSION=1.7.7
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        tmux \
        ncurses-bin \
        procps \
        iproute2 \
        dnsutils \
        tzdata \
        ca-certificates \
        curl; \
    if ! apt-get install -y --no-install-recommends ttyd; then \
        curl -fsSL -o /usr/local/bin/ttyd \
            "https://github.com/tsl0922/ttyd/releases/download/${TTYD_VERSION}/ttyd.x86_64"; \
        chmod +x /usr/local/bin/ttyd; \
    fi; \
    ttyd --version; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TERM=xterm-256color \
    PYTHONPATH=/app \
    HOME=/config

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r /tmp/requirements.txt \
 && rm /tmp/requirements.txt

COPY entrypoint.sh    /usr/local/bin/entrypoint.sh
COPY ika              /usr/local/bin/ika
COPY ika-modules      /usr/local/bin/ika-modules
COPY ika-update       /usr/local/bin/ika-update
COPY ika-web-attach   /usr/local/bin/ika-web-attach
RUN chmod +x /usr/local/bin/entrypoint.sh \
             /usr/local/bin/ika \
             /usr/local/bin/ika-modules \
             /usr/local/bin/ika-update \
             /usr/local/bin/ika-web-attach

EXPOSE 7681
WORKDIR /app
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
