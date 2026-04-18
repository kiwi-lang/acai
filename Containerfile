# assai-sandbox — generic container for running agent tools in isolation.
#
# Build (auto-built on first sandbox use if missing):
#   docker build -t assai-sandbox -f Containerfile .
#   podman build -t localhost/assai-sandbox -f Containerfile .
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

# git identity is configured at runtime per-agent by the sandbox manager
# so commits are attributable to the specific agent that produced them.

WORKDIR /opt/assai
COPY setup.py README.md ./
COPY assai/ assai/

RUN pip install --no-cache-dir \
        fastapi \
        "uvicorn[standard]" \
        requests \
        pyyaml \
        jinja2 \
        argklass \
        importlib_resources \
    && pip install --no-cache-dir -e . --no-deps

WORKDIR /workspace

EXPOSE 9200

ENTRYPOINT ["assai", "mcp"]
CMD ["--host", "0.0.0.0", "--port", "9200"]
