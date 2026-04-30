# acai-sandbox — generic container for running agent tools in isolation.
#
# Build (auto-built on first sandbox use if missing):
#   podman build -t localhost/acai-sandbox -f Containerfile .
#   docker build -t acai-sandbox -f Containerfile .
#
# Rootless Podman (default):
#   podman run --userns=keep-id -v $PWD:$PWD -w $PWD localhost/acai-sandbox
#
# The agent sets up whatever project it needs at runtime via tool
# calls (pip install, git clone, etc.) — do NOT bake project-specific
# dependencies here.

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/acai
COPY setup.py README.md ./
COPY acai/ acai/

RUN pip install --no-cache-dir \
        fastapi \
        "uvicorn[standard]" \
        requests \
        pyyaml \
        jinja2 \
        argklass \
        importlib_resources \
    && pip install --no-cache-dir -e . --no-deps

# Make pip cache and git config writable for any UID so rootless
# Podman (--userns=keep-id) works without permission errors.
RUN chmod -R a+rwX /opt/acai \
    && mkdir -p /tmp/pip-cache && chmod 777 /tmp/pip-cache
ENV PIP_CACHE_DIR=/tmp/pip-cache

# Working directory is set at runtime via `podman run -w <host_path>`
# to keep paths consistent between host and container.

EXPOSE 9200

ENTRYPOINT ["acai", "mcp"]
CMD ["--host", "0.0.0.0", "--port", "9200"]
