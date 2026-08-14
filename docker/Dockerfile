FROM python:3.12-slim

# ARG, not ENV: keeps debconf quiet during the build without leaving
# DEBIAN_FRONTEND set inside the running container.
ARG DEBIAN_FRONTEND=noninteractive

# ncurses-bin supplies /usr/bin/clear — gui.clear() shells out to it, and
# without it every banner() prints "sh: clear: not found" instead of drawing.
# dnsutils matches the upstream ikabot image. tzdata is needed so the
# IKABOT_TIMEZONE_ID region presets resolve.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tmux \
        ncurses-bin \
        procps \
        dnsutils \
        tzdata \
        ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

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

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY ika            /usr/local/bin/ika
COPY ika-modules    /usr/local/bin/ika-modules
RUN chmod +x /usr/local/bin/entrypoint.sh \
             /usr/local/bin/ika \
             /usr/local/bin/ika-modules

WORKDIR /app
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
