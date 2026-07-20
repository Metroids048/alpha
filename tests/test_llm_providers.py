from __future__ import annotations

import json
import math
import traceback

import httpx
import pytest

from alpha_mining.llm import create_runtime_providers
from alpha_mining.llm.deepseek import DeepSeekLLMError, DeepSeekStructuredLLM
from alpha_mining.llm.local_embedding import LocalSentenceTransformerEmbedder


SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}


def _response(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": content}}]},
    )


def test_deepseek_sends_structured_request_and_parses_json_object(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "unit-test-secret")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://mock.deepseek.local/v1/")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-test")
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        captured["payload"] = json.loads(request.content)
        return _response('{"answer":"ok"}')

    llm = DeepSeekStructuredLLM(transport=httpx.MockTransport(handler))
    result = llm.generate_json(
        system_prompt="system instructions",
        user_prompt="user instructions",
        json_schema=SCHEMA,
    )

    assert result == {"answer": "ok"}
    request = captured["request"]
    assert isinstance(request, httpx.Request)
    assert str(request.url) == "https://mock.deepseek.local/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer unit-test-secret"
    payload = captured["payload"]
    assert payload["model"] == "deepseek-test"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["messages"][0] == {
        "role": "system",
        "content": "system instructions",
    }
    user_content = payload["messages"][1]["content"]
    assert "user instructions" in user_content
    assert json.dumps(SCHEMA, ensure_ascii=False, sort_keys=True) in user_content


@pytest.mark.parametrize(
    ("responder", "message"),
    [
        (lambda request: _response(""), "empty"),
        (lambda request: _response("not-json"), "valid JSON object"),
        (lambda request: _response("[]"), "JSON object"),
        (
            lambda request: httpx.Response(
                200,
                json={"choices": []},
            ),
            "empty",
        ),
    ],
)
def test_deepseek_rejects_empty_or_invalid_structured_content(
    monkeypatch,
    responder,
    message: str,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "unit-test-secret")
    llm = DeepSeekStructuredLLM(transport=httpx.MockTransport(responder))

    with pytest.raises(DeepSeekLLMError, match=message):
        llm.generate_json(system_prompt="s", user_prompt="u", json_schema=SCHEMA)


@pytest.mark.parametrize("failure", ["timeout", "http"])
def test_deepseek_errors_are_clear_and_never_leak_authorization(
    monkeypatch, failure: str
) -> None:
    secret = "super-secret-deepseek-key"
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret)

    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "timeout":
            raise httpx.ReadTimeout(f"Authorization: Bearer {secret}", request=request)
        return httpx.Response(401, text=f"invalid Authorization Bearer {secret}")

    llm = DeepSeekStructuredLLM(transport=httpx.MockTransport(handler))
    with pytest.raises(DeepSeekLLMError) as raised:
        llm.generate_json(system_prompt="s", user_prompt="u", json_schema=SCHEMA)

    rendered = str(raised.value)
    assert secret not in rendered
    assert "Authorization" not in rendered
    full_traceback = "".join(traceback.format_exception(raised.value))
    assert secret not in full_traceback
    assert "Authorization" not in full_traceback
    assert (
        ("timed out" in rendered) if failure == "timeout" else ("HTTP 401" in rendered)
    )


def test_deepseek_requires_api_key(monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        DeepSeekStructuredLLM()


def test_runtime_factory_reuses_injected_clients_without_loading_models(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "unit-test-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        return _response('{"answer":"ok"}')

    client = httpx.Client(transport=httpx.MockTransport(handler))
    model = FakeSentenceTransformer([0.1, 0.2])
    providers = create_runtime_providers(
        load_environment=False,
        llm_client=client,
        embedding_model=model,
    )

    assert providers.llm.model_id == "deepseek-chat"
    assert providers.embedder.model_name.endswith(
        "paraphrase-multilingual-MiniLM-L12-v2"
    )
    assert model.calls == []


def test_runtime_providers_close_owned_clients_and_models(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "unit-test-secret")

    class ClosableModel(FakeSentenceTransformer):
        def __init__(self) -> None:
            super().__init__([0.1, 0.2])
            self.closed = False

        def close(self) -> None:
            self.closed = True

    model = ClosableModel()
    with create_runtime_providers(
        load_environment=False,
        llm_transport=httpx.MockTransport(lambda request: _response('{"answer":"ok"}')),
        embedding_model=model,
    ) as providers:
        assert not providers.llm._client.is_closed
    assert providers.llm._client.is_closed
    assert model.closed


class FakeSentenceTransformer:
    def __init__(self, vector: list[float]) -> None:
        self.vector = vector
        self.calls: list[tuple[list[str], bool]] = []

    def encode(self, texts: list[str], *, convert_to_numpy: bool) -> list[list[float]]:
        self.calls.append((texts, convert_to_numpy))
        return [self.vector]


def test_local_embedder_uses_injected_model_and_returns_finite_float_tuple(
    monkeypatch,
) -> None:
    monkeypatch.setenv("LOCAL_EMBEDDING_MODEL", "local-test-model")
    model = FakeSentenceTransformer([1, -0.25, 0.5])
    embedder = LocalSentenceTransformerEmbedder(model=model)

    assert embedder.model_name == "local-test-model"
    assert embedder.embed("中英 bilingual text") == (1.0, -0.25, 0.5)
    assert model.calls == [(["中英 bilingual text"], True)]


@pytest.mark.parametrize("vector", [[], [1.0, math.nan], [math.inf]])
def test_local_embedder_rejects_empty_or_non_finite_vectors(
    vector: list[float],
) -> None:
    embedder = LocalSentenceTransformerEmbedder(model=FakeSentenceTransformer(vector))
    with pytest.raises(ValueError, match="non-empty finite"):
        embedder.embed("text")


class MalformedEmbeddingModel:
    def __init__(self, encoded) -> None:
        self.encoded = encoded

    def encode(self, texts: list[str], *, convert_to_numpy: bool):
        del texts, convert_to_numpy
        return self.encoded


@pytest.mark.parametrize(
    "encoded", [None, 1.0, [], [[]], [[1.0], [2.0]], [[1.0, [2.0]]]]
)
def test_local_embedder_rejects_malformed_encode_shapes(encoded) -> None:
    embedder = LocalSentenceTransformerEmbedder(model=MalformedEmbeddingModel(encoded))
    with pytest.raises(ValueError, match="non-empty finite"):
        embedder.embed("text")
