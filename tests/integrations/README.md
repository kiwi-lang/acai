# Integration Tests

These tests require a **running vLLM instance** and exercise the full acai stack
against a real LLM — from provider adapters through agent pipelines to workflow
execution.

## Prerequisites

1. A vLLM server accessible at `http://localhost:9123` (or set `VLLM_ENDPOINT`)
2. A model loaded in the server (auto-detected via `/v1/models`)

## Running

```bash
# Default (localhost:9123)
make test-integration

# Custom endpoint
VLLM_ENDPOINT=http://gpu-box:8000 make test-integration

# With a specific model
VLLM_MODEL=Qwen/Qwen3-8B VLLM_ENDPOINT=http://localhost:9123 make test-integration
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VLLM_ENDPOINT` | `http://localhost:9123` | Base URL of the vLLM server |
| `VLLM_MODEL` | *(auto-detect)* | Model slug to use for testing |
| `VLLM_API_KEY` | *(empty)* | API key if required |

## Test Modules

| File | What it tests |
|------|---------------|
| `test_vllm_basic.py` | Raw adapter: complete, stream, tool calls, thinking, JSON schema |
| `test_converse_e2e.py` | ConverseGraph: single & multi-turn conversations |
| `test_workflow_e2e.py` | DynamicGraph: Start→Agent→Output, structured output, multi-agent |
| `test_tool_loop.py` | Tool follow-up loop: calculator, multi-tool chains |

## Notes

- Tests are **automatically skipped** when no vLLM instance is reachable.
- Each test uses a fresh temp workspace — no global state pollution.
- Tests are designed to be resilient to model variation (assertions check
  structural completion, not exact wording).
