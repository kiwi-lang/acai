"""Tests for acai.utils.audit — AuditTrail and NullAuditTrail."""

from __future__ import annotations

import json
import os

import pytest

from acai.utils.audit import AuditTrail, NullAuditTrail


class TestNullAuditTrail:

    def test_all_methods_are_noop(self):
        trail = NullAuditTrail()
        trail.set_meta(agent="test")
        trail.record("event", phase="phase", extra="data")
        trail.save_payload("label", {"key": "value"})
        trail.finalize()

    def test_span_is_noop(self):
        trail = NullAuditTrail()
        with trail.span("test_span"):
            pass

    @pytest.mark.asyncio
    async def test_aspan_is_noop(self):
        trail = NullAuditTrail()
        async with trail.aspan("test_span"):
            pass

    def test_client_summary_empty(self):
        trail = NullAuditTrail()
        assert trail.client_summary() == {}


class TestAuditTrail:

    def test_auto_generates_request_id(self):
        trail = AuditTrail()
        assert len(trail.request_id) == 16

    def test_custom_request_id(self):
        trail = AuditTrail(request_id="custom123")
        assert trail.request_id == "custom123"

    def test_set_meta(self):
        trail = AuditTrail()
        trail.set_meta(agent="coder", task="write")
        assert trail._meta["agent"] == "coder"
        assert trail._meta["task"] == "write"

    def test_record_appends_event(self):
        trail = AuditTrail()
        trail.record("test_event", phase="init", key="val")
        assert len(trail.events) == 1
        ev = trail.events[0]
        assert ev["event"] == "test_event"
        assert ev["phase"] == "init"
        assert ev["key"] == "val"
        assert "ts" in ev
        assert "elapsed_ms" in ev

    def test_span_records_start_and_end(self):
        trail = AuditTrail()
        with trail.span("llm_call", phase="generate"):
            pass
        assert len(trail.events) == 2
        assert trail.events[0]["event"] == "llm_call.start"
        assert trail.events[1]["event"] == "llm_call.end"
        assert "duration_ms" in trail.events[1]

    def test_span_records_error(self):
        trail = AuditTrail()
        with pytest.raises(ValueError):
            with trail.span("bad_call"):
                raise ValueError("oops")
        assert trail.events[1]["event"] == "bad_call.error"
        assert "oops" in trail.events[1]["error"]

    @pytest.mark.asyncio
    async def test_aspan_records_start_and_end(self):
        trail = AuditTrail()
        async with trail.aspan("async_op", phase="fetch"):
            pass
        assert trail.events[0]["event"] == "async_op.start"
        assert trail.events[1]["event"] == "async_op.end"

    @pytest.mark.asyncio
    async def test_aspan_records_error(self):
        trail = AuditTrail()
        with pytest.raises(RuntimeError):
            async with trail.aspan("fail"):
                raise RuntimeError("async fail")
        assert trail.events[1]["event"] == "fail.error"

    def test_save_payload_records_event(self):
        trail = AuditTrail()
        trail.save_payload("messages", [{"role": "user", "content": "hi"}])
        assert any(e["event"] == "payload.saved" for e in trail.events)

    def test_save_payload_writes_file(self, tmp_path):
        trail = AuditTrail(request_id="req1", output_dir=str(tmp_path))
        trail.save_payload("test", {"data": 42})
        payload_file = tmp_path / "req1" / "payload-test.json"
        assert payload_file.exists()
        loaded = json.loads(payload_file.read_text())
        assert loaded["data"] == 42

    def test_finalize_writes_audit_json(self, tmp_path):
        trail = AuditTrail(request_id="req2", output_dir=str(tmp_path))
        trail.set_meta(agent="default")
        trail.record("start", phase="init")
        trail.finalize()

        audit_file = tmp_path / "req2" / "audit.json"
        assert audit_file.exists()
        data = json.loads(audit_file.read_text())
        assert data["request_id"] == "req2"
        assert data["meta"]["agent"] == "default"
        assert len(data["events"]) >= 2  # start + finalize

    def test_finalize_creates_latest_symlink(self, tmp_path):
        trail = AuditTrail(request_id="req3", output_dir=str(tmp_path))
        trail.finalize()
        latest = tmp_path / "latest"
        assert latest.is_symlink()
        assert os.readlink(str(latest)) == "req3"

    def test_finalize_noop_without_output_dir(self):
        trail = AuditTrail(request_id="req4")
        trail.record("event")
        trail.finalize()  # should not crash

    def test_client_summary(self, tmp_path):
        trail = AuditTrail(request_id="req5", output_dir=str(tmp_path))
        trail.set_meta(conversation="conv1")
        trail.record("llm_call", phase="gen")
        trail.finalize()
        summary = trail.client_summary()
        assert summary["request_id"] == "req5"
        assert summary["total_duration_ms"] >= 0
        assert summary["meta"]["conversation"] == "conv1"
        assert len(summary["events"]) >= 2
