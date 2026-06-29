"""Tests for acai.tools.interaction — UI element tools."""

from __future__ import annotations

import json

from acai.tools.interaction import (
    INTERACTION_TOOLS,
    ask_user,
    confirm,
    notify,
)


class TestConstants:
    def test_interaction_tools_set(self):
        assert "interaction_ask_user" in INTERACTION_TOOLS
        assert "interaction_confirm" in INTERACTION_TOOLS
        assert "interaction_notify" in INTERACTION_TOOLS
        assert len(INTERACTION_TOOLS) == 3


class TestAskUser:
    def test_basic(self):
        result = json.loads(ask_user("What color?"))
        assert result["displayed"] is True
        assert result["ui_element"]["type"] == "ask"
        assert result["ui_element"]["question"] == "What color?"
        assert result["ui_element"]["allow_free_text"] is True

    def test_with_options(self):
        opts = json.dumps([{"id": "r", "label": "Red"}, {"id": "b", "label": "Blue"}])
        result = json.loads(ask_user("Pick one", options=opts))
        assert len(result["ui_element"]["options"]) == 2
        assert result["ui_element"]["options"][0]["id"] == "r"

    def test_with_context(self):
        result = json.loads(ask_user("Q?", context="Some background"))
        assert result["ui_element"]["context"] == "Some background"

    def test_no_free_text(self):
        result = json.loads(ask_user("Q?", allow_free_text=False))
        assert result["ui_element"]["allow_free_text"] is False

    def test_invalid_options_json(self):
        result = json.loads(ask_user("Q?", options="not valid json"))
        assert result["ui_element"]["options"] == []

    def test_empty_options(self):
        result = json.loads(ask_user("Q?", options=""))
        assert result["ui_element"]["options"] == []

    def test_with_question_id(self):
        result = json.loads(ask_user("Name?", question_id="q_name"))
        assert result["ui_element"]["id"] == "q_name"

    def test_no_question_id_omits_field(self):
        result = json.loads(ask_user("Name?"))
        assert "id" not in result["ui_element"]

    def test_multiple_questions_collect_independently(self):
        """Each call returns its own ui_element — the graph collects them all."""
        r1 = json.loads(ask_user("Color?", question_id="q1"))
        r2 = json.loads(ask_user("Size?", question_id="q2"))
        r3 = json.loads(ask_user("Shape?", question_id="q3"))
        elements = [r1["ui_element"], r2["ui_element"], r3["ui_element"]]
        assert len(elements) == 3
        assert elements[0]["question"] == "Color?"
        assert elements[1]["question"] == "Size?"
        assert elements[2]["id"] == "q3"


class TestConfirm:
    def test_basic(self):
        result = json.loads(confirm("Delete everything?"))
        assert result["displayed"] is True
        assert result["ui_element"]["type"] == "confirm"
        assert result["ui_element"]["message"] == "Delete everything?"
        assert len(result["ui_element"]["options"]) == 2

    def test_default_labels(self):
        result = json.loads(confirm("Sure?"))
        options = result["ui_element"]["options"]
        assert options[0] == {"id": "yes", "label": "Yes"}
        assert options[1] == {"id": "no", "label": "No"}

    def test_custom_labels(self):
        result = json.loads(confirm("Deploy?", confirm_label="Deploy", deny_label="Cancel"))
        options = result["ui_element"]["options"]
        assert options[0]["label"] == "Deploy"
        assert options[1]["label"] == "Cancel"


class TestNotify:
    def test_basic(self):
        result = json.loads(notify("Build complete"))
        assert result["displayed"] is True
        assert result["ui_element"]["type"] == "notify"
        assert result["ui_element"]["message"] == "Build complete"
        assert result["ui_element"]["level"] == "info"

    def test_with_level(self):
        result = json.loads(notify("Oops", level="error"))
        assert result["ui_element"]["level"] == "error"

    def test_with_title(self):
        result = json.loads(notify("Done!", title="Success"))
        assert result["ui_element"]["title"] == "Success"

    def test_warning_level(self):
        result = json.loads(notify("Low disk", level="warning"))
        assert result["ui_element"]["level"] == "warning"


class TestToolMetadata:
    def test_ask_user_metadata(self):
        meta = getattr(ask_user, "_tool_meta", {})
        assert "read" in meta.get("permissions", ())
        assert "interaction:ask" in meta.get("resources", ())

    def test_confirm_metadata(self):
        meta = getattr(confirm, "_tool_meta", {})
        assert "read" in meta.get("permissions", ())
        assert "interaction:ask" in meta.get("resources", ())

    def test_notify_metadata(self):
        meta = getattr(notify, "_tool_meta", {})
        assert "read" in meta.get("permissions", ())
        assert "interaction:notify" in meta.get("resources", ())


class TestToolSignatures:
    def test_ask_user_params(self):
        import inspect
        sig = inspect.signature(ask_user)
        params = list(sig.parameters.keys())
        assert "question" in params
        assert "options" in params
        assert "allow_free_text" in params
        assert "context" in params
        assert "question_id" in params

    def test_confirm_params(self):
        import inspect
        sig = inspect.signature(confirm)
        params = list(sig.parameters.keys())
        assert "message" in params
        assert "confirm_label" in params
        assert "deny_label" in params

    def test_notify_params(self):
        import inspect
        sig = inspect.signature(notify)
        params = list(sig.parameters.keys())
        assert "message" in params
        assert "level" in params
        assert "title" in params
