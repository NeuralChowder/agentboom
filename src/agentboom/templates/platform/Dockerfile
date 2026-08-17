# {{AGENT_TITLE}} — agent container.
# Ubuntu + Node 22 + Qwen Code (`qwen serve`) + the platform SDK venv.
# Domain tools (kubectl, chrome, ffmpeg, ...) go in EXTRA_APT_PACKAGES or
# as extra RUN layers below — keep this file the single place for them.
FROM ubuntu:24.04

ARG QWEN_VERSION=latest
ARG QWEN_REBUILD=0
ARG USER_ID=1000
ARG USER_NAME=user
ARG EXTRA_APT_PACKAGES=""
ARG TZ=UTC

ENV DEBIAN_FRONTEND=noninteractive

# ── System dependencies ─────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git gnupg sudo tzdata \
        python3 python3-venv python3-pip \
        ${EXTRA_APT_PACKAGES} \
    && ln -snf /usr/share/zoneinfo/${TZ} /etc/localtime && echo ${TZ} > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# ── Node 22 ─────────────────────────────────────────────────────────────
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get update && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# ── Qwen Code agent runtime ─────────────────────────────────────────────
# QWEN_REBUILD busts the layer cache when you want a fresh `npm i -g`.
RUN echo "qwen rebuild: ${QWEN_REBUILD}" \
    && npm install -g @qwen-code/qwen-code@${QWEN_VERSION}

# ── Platform SDK venv (shared by agent-side scripts and skill scripts) ──
COPY platform/requirements.txt /tmp/platform-requirements.txt
RUN python3 -m venv /opt/platform-venv \
    && /opt/platform-venv/bin/pip install --no-cache-dir -r /tmp/platform-requirements.txt

# ── Non-root user with passwordless sudo ────────────────────────────────
RUN useradd -m -u ${USER_ID} -s /bin/bash ${USER_NAME} \
    && echo "${USER_NAME} ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/${USER_NAME}

USER ${USER_NAME}
WORKDIR /home/${USER_NAME}

COPY --chown=${USER_ID}:${USER_ID} entrypoint.sh /usr/local/bin/entrypoint.sh
RUN sudo chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
