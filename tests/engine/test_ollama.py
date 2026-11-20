"""Tests for the Ollama engine backend."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from openjarvis.core.registry import EngineRegistry
from openjarvis.core.types import Message, Role
from openjarvis.engine._base import EngineConnectionError
from openjarvis.engine.ollama import OllamaEngine


@pytest.fixture()
def engine() -> OllamaEngine:
    EngineRegistry.register_value("ollama", OllamaEngine)
    return OllamaEngine(host="http://testhost:11434")


class TestOllamaKeepAlive:
    """keep_alive opcional + precarga (evita ~10 s de carga en el primer turno)."""

    _OK = {
        "message": {"role": "assistant", "content": "ok"},
        "model": "qwen3:8b",
        "prompt_eval_count": 1,
        "eval_count": 1,
    }

    def test_default_payload_has_no_keep_alive(self, engine: OllamaEngine) -> None:
        with respx.mock:
            route = respx.post("http://testhost:11434/api/chat").mock(
                return_value=httpx.Response(200, json=self._OK)
            )
            engine.generate([Message(role=Role.USER, content="Hi")], model="qwen3:8b")
            assert b"keep_alive" not in route.calls.last.request.content

    def test_keep_alive_is_sent_once_configured(self, engine: OllamaEngine) -> None:
        engine.set_keep_alive("30m")
        with respx.mock:
            route = respx.post("http://testhost:11434/api/chat").mock(
                return_value=httpx.Response(200, json=self._OK)
            )
            engine.generate([Message(role=Role.USER, content="Hi")], model="qwen3:8b")
            body = json.loads(route.calls.last.request.content)
            assert body["keep_alive"] == "30m"
        engine.set_keep_alive(None)

    def test_preload_loads_model_without_generating(self, engine: OllamaEngine) -> None:
        with respx.mock:
            route = respx.post("http://testhost:11434/api/generate").mock(
                return_value=httpx.Response(200, json={"done": True})
            )
            seconds = engine.preload("qwen3:8b")
            body = json.loads(route.calls.last.request.content)
            assert body["prompt"] == "" and body["keep_alive"] == "30m"
            assert body["model"] == "qwen3:8b" and seconds >= 0

    def test_preload_reports_unreachable_ollama(self, engine: OllamaEngine) -> None:
        with respx.mock:
            respx.post("http://testhost:11434/api/generate").mock(
                side_effect=httpx.ConnectError("refused")
            )
            with pytest.raises(EngineConnectionError):
                engine.preload("qwen3:8b")


class TestOllamaGenerate:
    def test_generate_returns_content(self, engine: OllamaEngine) -> None:
        with respx.mock:
            respx.post("http://testhost:11434/api/chat").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "message": {"role": "assistant", "content": "Hello!"},
                        "model": "qwen3:8b",
                        "prompt_eval_count": 10,
                        "eval_count": 5,
                    },
                )
            )
            result = engine.generate(
                [Message(role=Role.USER, content="Hi")], model="qwen3:8b"
            )
        assert result["content"] == "Hello!"
        assert result["usage"]["prompt_tokens"] == 10
        assert result["usage"]["completion_tokens"] == 5
        assert result["usage"]["total_tokens"] == 15

    def test_generate_connection_error(self, engine: OllamaEngine) -> None:
        with respx.mock:
            respx.post("http://testhost:11434/api/chat").mock(
                side_effect=httpx.ConnectError("refused")
            )
            with pytest.raises(EngineConnectionError):
                engine.generate(
                    [Message(role=Role.USER, content="Hi")], model="qwen3:8b"
                )

    def test_generate_remote_disconnect_during_response(
        self, engine: OllamaEngine
    ) -> None:
        """Ollama dying mid-response (not just refusing the connection) must
        also surface as EngineConnectionError -- this is what the real
        process looks like under ``systemctl stop ollama`` while a request
        is in flight (T1.4 degradation), as opposed to the connection being
        refused outright.
        """
        with respx.mock:
            respx.post("http://testhost:11434/api/chat").mock(
                side_effect=httpx.RemoteProtocolError(
                    "Server disconnected without sending a response."
                )
            )
            with pytest.raises(EngineConnectionError):
                engine.generate(
                    [Message(role=Role.USER, content="Hi")], model="qwen3:8b"
                )


class TestOllamaListModels:
    def test_list_models(self, engine: OllamaEngine) -> None:
        with respx.mock:
            respx.get("http://testhost:11434/api/tags").mock(
                return_value=httpx.Response(
                    200,
                    json={"models": [{"name": "qwen3:8b"}, {"name": "llama3.2:3b"}]},
                )
            )
            models = engine.list_models()
        assert models == ["qwen3:8b", "llama3.2:3b"]


class TestOllamaHealth:
    def test_health_true(self, engine: OllamaEngine) -> None:
        with respx.mock:
            respx.get("http://testhost:11434/api/tags").mock(
                return_value=httpx.Response(200, json={"models": []})
            )
            assert engine.health() is True

    def test_health_false(self, engine: OllamaEngine) -> None:
        with respx.mock:
            respx.get("http://testhost:11434/api/tags").mock(
                side_effect=httpx.ConnectError("refused")
            )
            assert engine.health() is False


class TestOllamaStream:
    @pytest.mark.asyncio
    async def test_stream_yields_content(self, engine: OllamaEngine) -> None:
        lines = [
            json.dumps({"message": {"content": "Hello"}, "done": False}),
            json.dumps({"message": {"content": " world"}, "done": True}),
        ]
        body = "\n".join(lines)
        with respx.mock:
            respx.post("http://testhost:11434/api/chat").mock(
                return_value=httpx.Response(200, text=body)
            )
            tokens = []
            async for tok in engine.stream(
                [Message(role=Role.USER, content="Hi")], model="qwen3:8b"
            ):
                tokens.append(tok)
        assert "Hello" in tokens
