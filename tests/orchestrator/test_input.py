"""Unit tests for acai/orchestrator/input.py."""

from __future__ import annotations

import base64
from datetime import datetime
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from acai.orchestrator.input import (
    Conversation,
    Input,
    Message,
    audio_b64,
    image_b64,
    text,
    video_b64,
)


class TestInputDataclass:
    def test_create_input(self):
        inp = Input(kind="text", encoding="utf8", data="hello")
        assert inp.kind == "text"
        assert inp.encoding == "utf8"
        assert inp.data == "hello"

    def test_input_equality(self):
        a = Input(kind="text", encoding="utf8", data="x")
        b = Input(kind="text", encoding="utf8", data="x")
        assert a == b

    def test_input_different_kinds(self):
        a = Input(kind="text", encoding="utf8", data="x")
        b = Input(kind="image", encoding="utf8", data="x")
        assert a != b


class TestMessageDataclass:
    def test_create_message(self):
        content = Input(kind="text", encoding="utf8", data="hi")
        ts = datetime(2024, 1, 1, 12, 0, 0)
        msg = Message(id=1, action_id=None, role="user", content=content, timestamp=ts)
        assert msg.id == 1
        assert msg.action_id is None
        assert msg.role == "user"
        assert msg.content == content
        assert msg.timestamp == ts

    def test_message_with_action_id(self):
        content = Input(kind="text", encoding="utf8", data="response")
        ts = datetime(2024, 1, 1, 12, 0, 0)
        msg = Message(id=2, action_id=5, role="assistant", content=content, timestamp=ts)
        assert msg.action_id == 5


class TestConversationDataclass:
    def test_create_empty_conversation(self):
        conv = Conversation(messages=[])
        assert conv.messages == []

    def test_create_conversation_with_messages(self):
        content = Input(kind="text", encoding="utf8", data="hi")
        ts = datetime(2024, 1, 1)
        msg = Message(id=1, action_id=None, role="user", content=content, timestamp=ts)
        conv = Conversation(messages=[msg])
        assert len(conv.messages) == 1
        assert conv.messages[0].role == "user"


class TestTextFunction:
    def test_basic_text(self):
        result = text("hello world")
        assert result == {
            "kind": "text",
            "encoding": "utf8",
            "data": "hello world",
        }

    def test_empty_string(self):
        result = text("")
        assert result["kind"] == "text"
        assert result["data"] == ""

    def test_none_value(self):
        result = text(None)
        assert result["kind"] == "text"
        assert result["data"] is None

    def test_multiline_text(self):
        result = text("line1\nline2\nline3")
        assert result["data"] == "line1\nline2\nline3"

    def test_unicode_text(self):
        result = text("héllo wörld 🌍")
        assert result["data"] == "héllo wörld 🌍"


class TestImageB64:
    def test_encodes_png_image(self):
        fake_img = MagicMock()
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"fake_image_data"

        def save_side_effect(buf, format=None):
            buf.write(png_bytes)

        fake_img.save.side_effect = save_side_effect

        result = image_b64(fake_img)

        assert result["kind"] == "image"
        assert result["encoding"] == "data_url"
        assert result["data"].startswith("data:image/png;base64,")
        b64_part = result["data"].split(",", 1)[1]
        decoded = base64.b64decode(b64_part)
        assert decoded == png_bytes

    def test_calls_save_with_png_format(self):
        fake_img = MagicMock()
        fake_img.save.side_effect = lambda buf, format=None: buf.write(b"x")

        image_b64(fake_img)
        fake_img.save.assert_called_once()
        call_args = fake_img.save.call_args
        assert call_args[1].get("format") == "PNG" or (
            len(call_args[0]) > 1 and call_args[0][1] == "PNG"
        )

    def test_empty_image(self):
        fake_img = MagicMock()
        fake_img.save.side_effect = lambda buf, format=None: None

        result = image_b64(fake_img)
        assert result["kind"] == "image"
        b64_part = result["data"].split(",", 1)[1]
        assert base64.b64decode(b64_part) == b""


class TestAudioB64:
    def test_returns_audio_structure(self):
        audio = MagicMock()
        result = audio_b64(audio)

        assert result["kind"] == "audio"
        assert result["encoding"] == "data_url"
        assert result["data"].startswith("data:audio/mp3;base64,")

    def test_empty_buffer_produces_valid_base64(self):
        audio = MagicMock()
        result = audio_b64(audio)

        b64_part = result["data"].split(",", 1)[1]
        decoded = base64.b64decode(b64_part)
        assert decoded == b""


class TestVideoB64:
    def test_returns_video_structure(self):
        video = MagicMock()
        result = video_b64(video)

        assert result["kind"] == "video"
        assert result["encoding"] == "data_url"
        assert result["data"].startswith("data:video/mp4;base64,")

    def test_empty_buffer_produces_valid_base64(self):
        video = MagicMock()
        result = video_b64(video)

        b64_part = result["data"].split(",", 1)[1]
        decoded = base64.b64decode(b64_part)
        assert decoded == b""
