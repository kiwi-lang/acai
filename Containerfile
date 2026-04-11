# assai-sandbox — lightweight container for running agent tools.
#
# Build:
#   podman build -t assai-sandbox -f Containerfile .
#
# For project-specific images:
#   FROM assai-sandbox
#   COPY requirements.txt /tmp/
#   RUN pip install --no-cache-dir -r /tmp/requirements.txt

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        build-essential \
        curl \
        nodejs \
        npm \
    && rm -rf /var/lib/apt/lists/*

RUN git config --global user.email "assai-agent@localhost" \
    && git config --global user.name "assai-agent"

WORKDIR /opt/assai
COPY setup.py README.md ./
COPY assai/ assai/

RUN pip install --no-cache-dir \
        flask \
        requests \
        pyyaml \
        argklass \
        importlib_resources \
    && pip install --no-cache-dir -e . --no-deps

WORKDIR /workspace

EXPOSE 9200

ENTRYPOINT ["assai", "mcp"]
CMD ["--host", "0.0.0.0", "--port", "9200"]
