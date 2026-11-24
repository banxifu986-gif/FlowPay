# 回归测试：嵌入批处理与错误记录。测试中的固定输入用于复现边界，不作为业务答案来源。
import json
from pathlib import Path

import httpx
import pytest

from flowpay.core.agent.trace import Tracer
from flowpay.core.event_log import EventStore
from flowpay.core.execution.failures import classify_failure
from flowpay.core.json import JsonObject, parse_json, require_list, require_object
from flowpay.core.llm.chat.http_error import ModelHTTPError
from flowpay.core.llm.embedding import EmbeddingClient, EmbeddingSettings
from flowpay.core.llm.embedding.ollama import QUERY_INSTRUCTION, query_input_for
from flowpay.core.llm.embedding.openai_embedding import OpenAiEmbeddingModel
from flowpay.core.llm.embedding.settings import same_vector_space


def settings(*, request_options: JsonObject | None = None) -> EmbeddingSettings:
    return EmbeddingSettings(
        name="fixture",
        base_url="https://embedding.test/v1",
        dimensions=3,
        api_key_env="TEST_EMBEDDING_KEY",
        batch_size=2,
        request_options=request_options or {},
    )


async def test_embedding_batches_order_and_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEST_EMBEDDING_KEY", "secret-value")
    requests: list[JsonObject] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = require_object(parse_json(request.content))
        requests.append(body)
        assert request.headers["authorization"] == "Bearer secret-value"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": i, "embedding": [i + 1, 0, 1]}
                    for i in reversed(range(len(require_list(body["input"]))))
                ],
                "usage": {"total_tokens": 7},
            },
        )

    store = EventStore(tmp_path / "events.sqlite")
    tracer = Tracer(store, "build", "run")
    client = EmbeddingClient(OpenAiEmbeddingModel(settings(), httpx.MockTransport(handler)), tracer)
    batch = await client.embed(["甲", "乙", "丙"])
    assert batch.vectors == [[1, 0, 1], [2, 0, 1], [1, 0, 1]]
    assert [r["input"] for r in requests] == [["甲", "乙"], ["丙"]]
    events = store.read("build")
    assert [e["type"] for e in events] == [
        "embedding.request",
        "embedding.raw",
        "embedding.response",
    ] * 2
    assert "secret-value" not in json.dumps(events)
    assert (
        require_object(require_list(require_object(events[2]["payload"]["raw"])["data"])[0])[
            "index"
        ]
        == 1
    )


@pytest.mark.parametrize(
    "response",
    [
        {"data": []},
        {"data": [{"index": 0, "embedding": [1, 2]}]},
        {"data": [{"index": 0, "embedding": [0, 0, 0]}]},
        {"data": [{"index": 0, "embedding": [True, 1, 2]}]},
        {"data": [{"index": 1, "embedding": [1, 2, 3]}]},
    ],
)
async def test_invalid_embedding_never_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, response: JsonObject
) -> None:
    monkeypatch.setenv("TEST_EMBEDDING_KEY", "secret")
    store = EventStore(tmp_path / "events.sqlite")
    adapter = OpenAiEmbeddingModel(
        settings(), httpx.MockTransport(lambda _: httpx.Response(200, json=response))
    )
    with pytest.raises(ValueError):
        await EmbeddingClient(adapter, Tracer(store, "s", "r")).embed(["text"])
    assert store.read("s")[-1]["type"] == "embedding.error"
    assert not any(e["type"] == "embedding.response" for e in store.read("s"))


@pytest.mark.parametrize(
    ("status", "body", "category", "retryable", "deterministic"),
    [
        # ① 嵌入对查询文本的结构性拒绝（R96B FP-BM-TV-0116 形态）→ 确定性、不可重试。
        pytest.param(
            400,
            '{"code":20015,"message":"The parameter is invalid. Please check again."}',
            "other",
            False,
            True,
            id="400-input-invalid",
        ),
        pytest.param(
            422,
            "unknown parameter temperature for this model",
            "other",
            False,
            True,
            id="422-param-rejected",
        ),
        # ② 普通形态 4xx：不命中白名单，不误熔断（other、不可重试但非确定性）。
        pytest.param(400, "the request was malformed", "other", False, False, id="400-plain"),
        pytest.param(422, "unprocessable content", "other", False, False, id="422-plain"),
        # ④ 超限与输入拒绝关键词同现：超限优先，不进确定性档（审查 P2-1）。
        pytest.param(
            400,
            "Invalid input: maximum context length is 512 tokens",
            "context_over_limit",
            False,
            False,
            id="400-over-limit-wins",
        ),
        # ③ 普通限流可重试；配额用尽语义不可重试；5xx 暂态可重试。
        pytest.param(429, "rate limited", "rate_limit", True, False, id="429-rate-limit"),
        pytest.param(429, "quota exceeded", "quota", False, False, id="429-quota"),
        pytest.param(503, "upstream down", "model_5xx", True, False, id="503-transient"),
    ],
)
async def test_sdk_http_error_reaches_failure_classifier(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    body: str,
    category: str,
    retryable: bool,
    deterministic: bool,
) -> None:
    """SDK → 嵌入适配器 → 失败分类：真实适配器抛出的错误必须保持类型化契约。

    修复前适配器把 APIStatusError 折叠成普通 RuntimeError，classify_failure
    只归 other 且永不置确定性档位——本用例在修复前对非 other 边界全部失败。
    """
    monkeypatch.setenv("TEST_EMBEDDING_KEY", "secret")

    def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(status, text=body)

    adapter = OpenAiEmbeddingModel(settings(), httpx.MockTransport(handler))
    raw: list[JsonObject] = []

    async def emit(event: JsonObject) -> None:
        raw.append(event)

    with pytest.raises(ModelHTTPError) as raised:
        await adapter.invoke(adapter.prepare(["文本"]), emit)
    # 原始响应追踪保留：完整错误正文先落轨迹，再按类型化契约上抛。
    assert raw == [{"status": status, "body": body}]
    assert raised.value.status_code == status
    assert raised.value.body_excerpt == body
    info = classify_failure(raised.value)
    assert info.category == category
    assert info.retryable is retryable
    assert info.deterministic is deterministic


async def test_embedding_http_error_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEST_EMBEDDING_KEY", "secret")
    store = EventStore(tmp_path / "events.sqlite")
    adapter = OpenAiEmbeddingModel(
        settings(), httpx.MockTransport(lambda _: httpx.Response(429, json={"error": "rate limit"}))
    )
    with pytest.raises(RuntimeError, match="429"):
        await EmbeddingClient(adapter, Tracer(store, "s", "r")).embed(["text"])
    assert store.read("s")[1]["payload"]["status"] == 429


def test_embedding_configuration_loader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from flowpay.core.config import load_settings

    config = tmp_path / "config.toml"
    config.write_text(
        'data_dir="."\ndataset_path="."\n[embedding]\n'
        + 'name="fixture"\nbase_url="https://embedding.test"\n'
        + 'dimensions=3\napi_key_env="TEST_EMBEDDING_KEY"\n'
    )
    monkeypatch.setenv("FLOWPAY_EMBEDDING_BATCH_SIZE", "4")
    result = load_settings(config)
    assert result.embedding is not None
    assert result.embedding.batch_size == 4
    assert require_object(result.snapshot()["embedding"])["dimensions"] == 3


async def test_canceled_embedding_has_terminal_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    monkeypatch.setenv("TEST_EMBEDDING_KEY", "secret")
    started = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("Canceled handler must not complete")

    store = EventStore(tmp_path / "trace.sqlite")
    client = EmbeddingClient(
        OpenAiEmbeddingModel(settings(), httpx.MockTransport(handler)), Tracer(store, "s", "r")
    )
    task = asyncio.create_task(client.embed(["text"]))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert [e["type"] for e in store.read("s")] == ["embedding.request", "embedding.canceled"]


async def test_sdk_embedding_request_and_no_hidden_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_EMBEDDING_KEY", "secret")
    attempts: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        return httpx.Response(500, json={"error": "temporary failure"})

    adapter = OpenAiEmbeddingModel(
        settings(request_options={"dimensions": 3, "vendor_option": "kept"}),
        httpx.MockTransport(handler),
    )
    recorded = adapter.prepare(["文本"])
    raw: list[JsonObject] = []

    async def emit(event: JsonObject) -> None:
        raw.append(event)

    with pytest.raises(ModelHTTPError, match="Model HTTP 500"):
        await adapter.invoke(recorded, emit)
    assert len(attempts) == 1
    assert str(attempts[0].url) == recorded["url"]
    assert parse_json(attempts[0].content) == recorded["body"]
    assert raw == [{"status": 500, "body": '{"error":"temporary failure"}'}]


def test_local_ollama_endpoints_share_one_vector_space() -> None:
    """同一台机器上的 Ollama，换个入口名不该被判成换了部署。

    宿主机写 127.0.0.1，容器里只能写 host.docker.internal，compose 的
    ollama profile 又叫 ollama。模型版本由 revision 的摘要钉死，端点主机名
    再参与比较，同一份集合换个入口读就要重建。
    """

    def settings(host: str) -> EmbeddingSettings:
        return EmbeddingSettings(
            provider="openai-compatible",
            name="qwen3-embedding:0.6b",
            base_url=f"http://{host}:11434/v1",
            api_key_env="OLLAMA_API_KEY",
            dimensions=1024,
            revision="digest",
        )

    current = settings("host.docker.internal").identity()
    # 历史集合里存的是未规范化的旧写法，比较时两边都要过一遍。
    assert same_vector_space({**current, "base_url": "http://127.0.0.1:11434/v1"}, current)
    assert same_vector_space(settings("localhost").identity(), current)
    assert same_vector_space(settings("ollama").identity(), current)
    # 端口和外部主机仍然算不同部署。
    assert not same_vector_space({**current, "base_url": "http://127.0.0.1:9999/v1"}, current)
    assert not same_vector_space(settings("10.0.0.5").identity(), current)


def test_query_input_convention_follows_the_model() -> None:
    """查询侧输入约定按模型分流：qwen3 需指令前缀，bge-m3 用原始问题；未知模型拒绝。"""
    qwen = EmbeddingSettings(
        "qwen3-embedding:0.6b", "http://127.0.0.1:11434/v1", 1024, "K", revision="digest"
    )
    assert query_input_for(qwen)("问题") == f"Instruct: {QUERY_INSTRUCTION}\nQuery: 问题"
    bge = EmbeddingSettings(
        "BAAI/bge-m3",
        "https://api.siliconflow.cn/v1",
        1024,
        "K",
        revision="siliconflow-baai-bge-m3",
    )
    assert query_input_for(bge)("问题") == "问题"
    with pytest.raises(ValueError, match="query input convention"):
        query_input_for(EmbeddingSettings("unknown-model", "https://x.test/v1", 3, "K"))
