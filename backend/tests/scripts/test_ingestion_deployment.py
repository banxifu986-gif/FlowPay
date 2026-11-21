"""嵌入部署核对按端点分流：本机 Ollama 核对 digest，云端服务只记录向量空间身份。"""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import httpx
import pytest
from scripts.benchmark import run as benchmark_run
from scripts.development import checks
from scripts.development.environment import PreparationError
from scripts.ingestion.corpus import CorpusSettings, prepare_corpus
from scripts.ingestion.pipeline import KnowledgePreparation

import flowpay.core.llm.embedding.ollama as ollama_module
from flowpay.core.config.settings import Settings
from flowpay.core.execution.contracts import TurnInput
from flowpay.core.execution.runtime import AgentRuntime
from flowpay.core.json import JsonObject
from flowpay.core.knowledge import Collection, Passage, PassageLookup, PendingChunk
from flowpay.core.llm.embedding import EmbeddedBatch, EmbeddingSettings
from flowpay.core.llm.embedding.ollama import ollama_identity, uses_local_ollama

if TYPE_CHECKING:
    from flowpay.core.agent.trace import Tracer

DIGEST = "sha256:" + "a" * 64


def document_text() -> str:
    """一篇可索引进的业务规则文档。"""
    return (
        "---\ndoc_id: DOC-1\ntitle: 通知规则\ndoc_type: business_rule\n"
        + "indexing: include\neffective_from: 2026-01-01\n---\n"
        + "# 通知规则\n\n## 重试\n\nHTTP 410 会禁用 Endpoint。\n"
    )


def write_release(root: Path) -> CorpusSettings:
    """写入最小冻结发布清单，路径相对数据根目录。"""
    content = document_text().encode()
    (root / "knowledge/rules.md").parent.mkdir(parents=True, exist_ok=True)
    (root / "knowledge/rules.md").write_bytes(content)
    (root / "generated/canonical/manifest.json").parent.mkdir(parents=True)
    (root / "generated/canonical/manifest.json").write_text("{}")
    release: JsonObject = {
        "status": "frozen",
        "release_id": "test-release",
        "artifact_groups": [
            {
                "group_id": "published_knowledge",
                "visibility": "agent_knowledge",
                "file_count": 1,
                "artifacts": [
                    {
                        "path": "data/knowledge/rules.md",
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "bytes": len(content),
                    }
                ],
            },
            {
                "group_id": "canonical_agent_data",
                "visibility": "agent_via_typed_tools",
                "file_count": 1,
                "artifacts": [
                    {
                        "path": "data/generated/canonical/manifest.json",
                        "sha256": hashlib.sha256(b"{}").hexdigest(),
                        "bytes": 2,
                    }
                ],
            },
        ],
    }
    raw_release = json.dumps(release).encode()
    (root / "release.json").write_bytes(raw_release)
    return CorpusSettings(
        "test-collection", "release.json", hashlib.sha256(raw_release).hexdigest()
    )


def corpus(tmp_path: Path):
    """准备一份真实切分好的语料；文件校验不触网络或数据库。"""
    return prepare_corpus(
        tmp_path,
        write_release(tmp_path),
        EmbeddingSettings("fixture", "http://localhost/v1", 3, "UNUSED"),
    )


def cloud_embedding() -> EmbeddingSettings:
    return EmbeddingSettings(
        "BAAI/bge-m3",
        "https://api.siliconflow.cn/v1",
        3,
        "UNUSED",
        revision="siliconflow-baai-bge-m3",
    )


def local_embedding() -> EmbeddingSettings:
    return EmbeddingSettings(
        "qwen3-embedding",
        "http://127.0.0.1:11434/v1",
        3,
        "UNUSED",
        revision=DIGEST,
    )


def fake_ollama_tags(digest: str):
    """用 MockTransport 顶替 Ollama native API，不触真实本机服务。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.12.3"})
        assert request.url.path == "/api/tags"
        return httpx.Response(
            200,
            json={
                "models": [
                    {"name": "qwen3-embedding", "digest": digest, "details": {}},
                ]
            },
        )

    return httpx.MockTransport(handler)


class RecordingStore:
    """记录写入调用的知识存储替身；不连接真实数据库。

    写侧方法由 Pipeline 的实际调用驱动；读侧方法按 VectorStore Protocol 补齐签名，
    使本类成为该协议的合法替身（Pipeline 只需要这两个写入方法存在）。
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def create_collection(
        self,
        collection_id: str,
        dataset_release_id: str,
        dataset_manifest_sha256: str,
        build_config: JsonObject,
        dimensions: int,
    ) -> None:
        _ = collection_id, dataset_release_id, dataset_manifest_sha256, build_config, dimensions
        self.calls.append("create_collection")

    def save_document(self, collection_id: str, document: object, chunks: object) -> None:  # noqa: ANN401
        _ = collection_id, document, chunks
        self.calls.append("save_document")

    def mark_ready(self, collection_id: str, expected_documents: dict[str, int]) -> None:
        _ = collection_id, expected_documents
        self.calls.append("mark_ready")

    def close(self) -> None:
        self.calls.append("close")

    # 以下读侧方法只为满足 VectorStore/KnowledgeWriter 协议签名；
    # build/verify 测试用 FakeEmbeddings 替换 KnowledgeClient，它们不会被调用。
    def get_collection(self, collection_id: str) -> Collection:
        _ = collection_id
        raise AssertionError("fake store should not read collections in these tests")

    def missing_embeddings(self, collection_id: str, limit: int = 32) -> list[PendingChunk]:
        _ = collection_id, limit
        raise AssertionError("fake store should not read embeddings in these tests")

    def save_embeddings(
        self, collection_id: str, keys: list[tuple[str, int]], batch: EmbeddedBatch
    ) -> None:
        _ = collection_id, keys, batch
        raise AssertionError("fake store should not save embeddings directly in these tests")

    def search(
        self,
        collection_id: str,
        query: EmbeddedBatch,
        limit: int = 5,
        filters: JsonObject | None = None,
    ) -> list[Passage]:
        _ = collection_id, query, limit, filters
        raise AssertionError("fake store should not search in these tests")

    def search_hybrid(
        self,
        collection_id: str,
        query: EmbeddedBatch,
        text: str,
        limit: int = 5,
        filters: JsonObject | None = None,
        *,
        keyword_limit: int = 20,
        vector_limit: int = 20,
        rrf_k: int = 60,
    ) -> list[Passage]:
        _ = collection_id, query, text, limit, filters, keyword_limit, vector_limit, rrf_k
        raise AssertionError("fake store should not search in these tests")

    def read_document(
        self, collection_id: str, doc_id: str, filters: JsonObject | None = None
    ) -> list[Passage]:
        _ = collection_id, doc_id, filters
        raise AssertionError("fake store should not read documents in these tests")

    def read_passage(self, collection_id: str, doc_id: str, chunk_no: int) -> PassageLookup:
        _ = collection_id, doc_id, chunk_no
        raise AssertionError("fake store should not read passages in these tests")


class RecordingEvents:
    """记录事件的追踪替身：形状同 NoopTracer，但保留载荷供断言。"""

    def __init__(self) -> None:
        self.kinds: list[str] = []
        self.payloads: list[JsonObject] = []

    async def emit(self, kind: str, payload: JsonObject, **kwargs) -> object:
        _ = kwargs
        self.kinds.append(kind)
        self.payloads.append(payload)
        return object()


def runtime(events: RecordingEvents) -> AgentRuntime:
    return AgentRuntime(cast("Tracer", cast(object, events)))


class FakeEmbeddings:
    """替身知识构建客户端：不调用嵌入服务，直接报告无缺失向量。"""

    def __init__(self, *args: object) -> None:
        _ = args

    async def embed_pending(self, collection_id: str) -> int:
        _ = collection_id
        return 0


async def run_build(
    tmp_path: Path, embedding: EmbeddingSettings, monkeypatch: pytest.MonkeyPatch
) -> tuple[RecordingStore, RecordingEvents]:
    """以替身存储与嵌入客户端跑完 build 流程，返回存储与事件记录。"""
    prepared = prepare_corpus(
        tmp_path,
        write_release(tmp_path),
        EmbeddingSettings("fixture", "http://localhost/v1", 3, "UNUSED"),
    )
    monkeypatch.setattr("scripts.ingestion.pipeline.KnowledgeClient", FakeEmbeddings)
    builder = RecordingStore()
    workflow = KnowledgePreparation(prepared, embedding, builder, builder, tmp_path, "build")
    events = RecordingEvents()
    result = await workflow.run(TurnInput("knowledge build", [], {}), runtime(events))
    assert result.text == "知识构建完成"
    return builder, events


async def test_uses_local_ollama_matches_endpoint_only() -> None:
    """分流判定只看端点主机名；云端服务一律不进本机核对。"""
    assert uses_local_ollama(local_embedding())
    assert uses_local_ollama(
        EmbeddingSettings("m", "http://host.docker.internal:11434/v1", 3, "UNUSED")
    )
    assert not uses_local_ollama(cloud_embedding())


async def test_ollama_identity_pins_digest_with_fake_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """digest 与 revision 一致才放行；不一致或缺失模型直接拒绝。"""
    transport = fake_ollama_tags(DIGEST)
    monkeypatch.setattr(
        ollama_module,
        "httpx",
        SimpleNamespace(
            AsyncClient=lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs)
        ),
    )
    deployment = await ollama_identity(local_embedding())
    assert deployment.digest == DIGEST
    assert deployment.ollama_version == "0.12.3"
    with pytest.raises(ValueError, match="digest differs"):
        await ollama_identity(
            EmbeddingSettings(
                "qwen3-embedding",
                "http://127.0.0.1:11434/v1",
                3,
                "UNUSED",
                revision="sha256:" + "b" * 64,
            )
        )
    with pytest.raises(ValueError, match="not installed"):
        await ollama_identity(
            EmbeddingSettings(
                "other-model", "http://127.0.0.1:11434/v1", 3, "UNUSED", revision=DIGEST
            )
        )


async def test_cloud_build_skips_ollama_deployment_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """云端配置跑 build 不再抛 Model is not installed；事件只记录向量空间身份。"""

    async def forbidden(settings):
        _ = settings
        raise AssertionError("云端配置不应做本机 Ollama 部署核对")

    monkeypatch.setattr("scripts.ingestion.pipeline.ollama_identity", forbidden)

    builder, events = await run_build(tmp_path, cloud_embedding(), monkeypatch)
    assert builder.calls == ["create_collection", "save_document", "mark_ready"]
    index = events.kinds.index("embedding.deployment")
    payload = events.payloads[index]
    assert payload["kind"] == "identity"
    assert payload["revision"] == "siliconflow-baai-bge-m3"
    assert payload["model"] == "BAAI/bge-m3"


async def test_local_build_still_checks_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """本机 Ollama 配置仍走 digest 核对，事件记录 Ollama 部署身份。"""
    transport = fake_ollama_tags(DIGEST)
    monkeypatch.setattr(
        ollama_module,
        "httpx",
        SimpleNamespace(
            AsyncClient=lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs)
        ),
    )

    builder, events = await run_build(tmp_path, local_embedding(), monkeypatch)
    assert builder.calls == ["create_collection", "save_document", "mark_ready"]
    index = events.kinds.index("embedding.deployment")
    payload = events.payloads[index]
    assert payload["kind"] == "ollama"
    assert payload["digest"] == DIGEST


async def test_local_build_rejects_drifted_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """本机配置 digest 漂移时构建在写入前失败。"""
    transport = fake_ollama_tags("sha256:" + "c" * 64)
    monkeypatch.setattr(
        ollama_module,
        "httpx",
        SimpleNamespace(
            AsyncClient=lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs)
        ),
    )
    drifted = EmbeddingSettings(
        "qwen3-embedding", "http://127.0.0.1:11434/v1", 3, "UNUSED", revision=DIGEST
    )
    workflow = KnowledgePreparation(
        corpus(tmp_path), drifted, RecordingStore(), RecordingStore(), tmp_path, "build"
    )
    with pytest.raises(ValueError, match="digest differs"):
        await workflow.run(TurnInput("knowledge build", [], {}), runtime(RecordingEvents()))


async def test_benchmark_stage_guards_ollama_check_by_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """基准启动预检按端点分流：云端跳过，本机仍核对。"""

    class Stop(Exception):
        """预检之后的第一个动作即停，避免触碰事件库与模型。"""

    calls: list[str] = []

    async def record(settings) -> None:
        calls.append(settings.name)

    def raise_stop(*args, **kwargs):
        _ = args, kwargs
        raise Stop

    monkeypatch.setattr(benchmark_run, "ollama_identity", record)
    monkeypatch.setattr(benchmark_run, "open_store", raise_stop)

    def toml(base_url: str) -> Path:
        config = tmp_path / f"{base_url.split('//')[1].split('/')[0]}.toml"
        config.write_text(
            "data_dir = 'var'\n"
            "dataset_path = 'dataset'\n"
            "[embedding]\n"
            "provider = 'openai-compatible'\n"
            "name = 'BAAI/bge-m3'\n"
            f"base_url = '{base_url}'\n"
            "dimensions = 1024\n"
            "api_key_env = 'FLOWPAY_EMBEDDING_API_KEY'\n"
            "revision = 'siliconflow-baai-bge-m3'\n"
        )
        return config

    args = benchmark_run.Arguments(config=toml("https://api.siliconflow.cn/v1"))
    with pytest.raises(Stop):
        await benchmark_run.run_stage("multi-agent", [], {}, args, tmp_path / "out")
    assert calls == []

    args = benchmark_run.Arguments(config=toml("http://127.0.0.1:11434/v1"))
    with pytest.raises(Stop):
        await benchmark_run.run_stage("multi-agent", [], {}, args, tmp_path / "out")
    assert calls == ["BAAI/bge-m3"]


async def test_checks_embedding_guards_local_model_check_by_endpoint(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """环境预检按端点分流：云端整段跳过，本机保留 digest 转换。"""

    def settings_with(embedding: EmbeddingSettings) -> Settings:
        return Settings(data_dir=Path("var"), dataset_path=Path("dataset"), embedding=embedding)

    async def forbidden(embedding) -> None:
        _ = embedding
        raise AssertionError("云端配置不应做本机 Ollama 部署核对")

    monkeypatch.setattr(checks, "ollama_identity", forbidden)
    await checks.check_embedding(settings_with(cloud_embedding()), False)
    output = capsys.readouterr().out
    assert "云端嵌入服务（api.siliconflow.cn）" in output

    shim = SimpleNamespace(
        AsyncClient=lambda **kwargs: httpx.AsyncClient(
            transport=fake_ollama_tags(DIGEST), **kwargs
        ),
        HTTPError=httpx.HTTPError,
    )
    monkeypatch.setattr(checks, "httpx", shim)

    async def drifted(embedding) -> None:
        _ = embedding
        raise ValueError("Ollama model digest differs from embedding.revision")

    monkeypatch.setattr(checks, "ollama_identity", drifted)
    with pytest.raises(PreparationError, match="不匹配"):
        await checks.check_embedding(settings_with(local_embedding()), False)

    def refused(**kwargs):
        _ = kwargs
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(
        checks, "httpx", SimpleNamespace(AsyncClient=refused, HTTPError=httpx.HTTPError)
    )
    monkeypatch.setattr(checks, "ollama_identity", forbidden)
    with pytest.raises(PreparationError, match="无法连接"):
        await checks.check_embedding(settings_with(local_embedding()), False)
