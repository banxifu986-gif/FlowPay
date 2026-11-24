"""FAQ 摄入入口及冻结语料边界的离线验收。"""

import re
import shutil
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest
from flowpay.core import knowledge
from flowpay.core.json import JsonObject, parse_json, require_list, require_object, require_text
from flowpay.core.knowledge import Chunk, Document
from flowpay.core.knowledge.contracts import Collection, PendingChunk
from flowpay.core.llm.chat import ChunkSink
from flowpay.core.llm.embedding import EmbeddedBatch, EmbeddingReply, EmbeddingSettings, text_hash

from scripts.faq import main
from scripts.faq_release import freeze_release
from scripts.ingestion.corpus import DatasetRelease, ReleaseArtifact

SOURCE = Path(__file__).resolve().parents[3] / "flowpay-faq-dataset"
MANIFEST = "data/release.json"
OLD_FAQ = "data/knowledge/refund-balance-v1.md"
NEW_FAQ = "data/knowledge/refund-balance-v2.md"
CANONICAL = "data/generated/canonical/manifest.json"
TARGET = "faq_flowpay-faq-v1"
LEGACY = "flowpay_docs_v1_bge_m3"
NOW = datetime(2026, 11, 24, tzinfo=UTC)


class FakeStore:
    def __init__(self) -> None:
        self.collections: dict[str, Collection] = {}
        self.documents: dict[str, dict[str, Document]] = {}
        self.pending: dict[str, list[PendingChunk]] = {}
        self.vectors: dict[str, dict[tuple[str, int], list[float]]] = {}

    def get_collection(self, collection_id: str) -> Collection:
        return self.collections[collection_id]

    def create_collection(
        self,
        collection_id: str,
        dataset_release_id: str,
        dataset_manifest_sha256: str,
        build_config: JsonObject,
        dimensions: int,
    ) -> None:
        assert collection_id not in self.collections
        self.collections[collection_id] = {
            "collection_id": collection_id,
            "dataset_release_id": dataset_release_id,
            "dataset_manifest_sha256": dataset_manifest_sha256,
            "build_config": build_config,
            "dimensions": dimensions,
            "status": "building",
            "created_at": NOW,
            "ready_at": None,
        }
        self.documents[collection_id] = {}
        self.pending[collection_id] = []
        self.vectors[collection_id] = {}

    def save_document(self, collection_id: str, document: Document, chunks: list[Chunk]) -> None:
        self.documents[collection_id][document.doc_id] = document
        self.pending[collection_id].extend(
            {"doc_id": document.doc_id, "chunk_no": chunk.chunk_no, "content": chunk.content}
            for chunk in chunks
        )

    def missing_embeddings(self, collection_id: str, limit: int) -> list[PendingChunk]:
        return self.pending[collection_id][:limit]

    def save_embeddings(
        self, collection_id: str, keys: list[tuple[str, int]], batch: EmbeddedBatch
    ) -> None:
        pending = self.pending[collection_id]
        rows = pending[: len(keys)]
        assert keys == [(row["doc_id"], row["chunk_no"]) for row in rows]
        assert batch.input_hashes == [text_hash(row["content"]) for row in rows]
        self.vectors[collection_id].update(zip(keys, batch.vectors, strict=True))
        del pending[: len(keys)]

    def mark_ready(self, collection_id: str, expected_documents: dict[str, int]) -> None:
        assert not self.pending[collection_id]
        assert len(self.vectors[collection_id]) == sum(expected_documents.values())
        self.collections[collection_id]["status"] = "ready"

    def close(self) -> None:
        pass


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[FakeStore, list[str]]:
    store = FakeStore()
    store.create_collection(LEGACY, "legacy", "legacy", {}, 3)
    store.mark_ready(LEGACY, {})
    embedded: list[str] = []

    class FakeEmbedding:
        def __init__(self, settings: EmbeddingSettings) -> None:
            self.settings: EmbeddingSettings = settings

        def prepare(self, texts: list[str]) -> JsonObject:
            return {"texts": texts}

        async def invoke(self, request: JsonObject, emit: ChunkSink) -> EmbeddingReply:
            _ = emit
            texts = require_list(request["texts"])
            embedded.extend(require_text(text) for text in texts)
            return EmbeddingReply([[1.0, 0.0, 0.0] for _ in texts], {}, {})

    def open_store(dsn: str) -> FakeStore:
        assert dsn == "offline-test-store"
        return store

    monkeypatch.setattr(knowledge, "PostgresVectorStore", open_store)
    monkeypatch.setattr("scripts.ingestion.pipeline.OpenAiEmbeddingModel", FakeEmbedding)
    monkeypatch.setenv("FLOWPAY_FAQ_KNOWLEDGE_BUILDER_DSN", "offline-test-store")
    monkeypatch.setenv("FLOWPAY_EMBEDDING_NAME", "fixture")
    monkeypatch.setenv("FLOWPAY_EMBEDDING_BASE_URL", "https://embedding.invalid/v1")
    monkeypatch.setenv("FLOWPAY_EMBEDDING_API_KEY_ENV", "UNUSED")
    monkeypatch.setenv("FLOWPAY_EMBEDDING_DIMENSIONS", "3")
    monkeypatch.delenv("FLOWPAY_KNOWLEDGE_COLLECTION_ID", raising=False)
    monkeypatch.chdir(tmp_path)
    return store, embedded


@pytest.fixture(scope="module")
def frozen(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("faq-corpus") / "release"
    _ = freeze_release(SOURCE, root, "flowpay-faq-v1")
    return root


@pytest.fixture
def release_root(frozen: Path, tmp_path: Path) -> Path:
    return shutil.copytree(frozen, tmp_path / "release")


def save_release(root: Path, release: DatasetRelease) -> str:
    payload = release.model_dump_json().encode()
    for view in (root, root / "runtime"):
        _ = (view / MANIFEST).write_bytes(payload)
    return sha256(payload).hexdigest()


def replace_document(root: Path, relative: str, old: str, new: str) -> str:
    path = root / relative
    original = path.read_bytes()
    assert old.encode() in original
    content = original.replace(old.encode(), new.encode(), 1)
    for view in (root, root / "runtime"):
        _ = (view / relative).write_bytes(content)
    release = DatasetRelease.model_validate_json((root / MANIFEST).read_bytes())
    group = next(g for g in release.artifact_groups if g.group_id == "published_knowledge")
    index = next(i for i, entry in enumerate(group.artifacts) if entry.path == relative)
    group.artifacts[index] = ReleaseArtifact(
        path=relative, bytes=len(content), sha256=sha256(content).hexdigest()
    )
    return save_release(root, release)


def test_build_cli_exposes_the_explicit_release_options(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        _ = main(["build", "--help"])

    result = capsys.readouterr()
    assert exc.value.code == 0, result.err
    assert "--faq-root" in result.out
    assert "--release-sha256" in result.out


def build(root: Path, pin: str) -> int:
    return main(["build", "--faq-root", str(root), "--release-sha256", pin])


def files(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize("small", [False, True], ids=["official", "two-versions"])
def test_build_publishes_only_declared_public_faq(
    release_root: Path, offline: tuple[FakeStore, list[str]], small: bool
) -> None:
    store, embedded = offline
    root = release_root
    manifest = (root / MANIFEST).read_bytes()
    pin = sha256(manifest).hexdigest()
    release = DatasetRelease.model_validate_json(manifest)
    published = next(g for g in release.artifact_groups if g.group_id == "published_knowledge")
    if small:
        kept = {NEW_FAQ, OLD_FAQ}
        for entry in published.artifacts:
            if entry.path not in kept:
                for view in (root, root / "runtime"):
                    (view / entry.path).unlink()
        published.artifacts = [entry for entry in published.artifacts if entry.path in kept]
        published.file_count = len(published.artifacts)
        # 构建不依赖未加载的题单或金标；完整 verify 仍由发布测试单独覆盖。
        (root / "data/benchmark/dev/rubrics/simple_faq.jsonl").unlink()
        pin = save_release(root, release)
    before = files(root)

    assert build(root, pin) == 0

    assert files(root) == before
    assert set(store.collections) == {LEGACY, TARGET}
    assert store.collections[LEGACY]["dataset_release_id"] == "legacy"
    assert store.collections[LEGACY]["status"] == "ready"
    assert not store.documents[LEGACY] and not store.vectors[LEGACY]
    collection = store.collections[TARGET]
    assert collection["status"] == "ready"
    assert collection["dataset_release_id"] == release.release_id
    assert collection["dataset_manifest_sha256"] == before[CANONICAL]
    assert collection["build_config"]["release_manifest_sha256"] == pin
    documents = store.documents[TARGET]
    assert len(documents) == (2 if small else 20)
    assert all(
        (doc.metadata["doc_type"], doc.metadata["audience"]) == ("faq", "external")
        for doc in documents.values()
    )
    sources = {entry.path.removeprefix("data/"): entry.sha256 for entry in published.artifacts}
    assert {doc.source_path for doc in documents.values()} == sources.keys()
    assert collection["build_config"]["document_sources"] == sources
    assert len(embedded) >= (10 if small else 100)
    assert len(embedded) == len(store.vectors[TARGET])
    (report_path,) = Path("var/faq/build").glob("run-*/report.json")
    report = require_object(parse_json(report_path.read_bytes()))
    assert require_object(report["result"]) == {
        "collection_id": TARGET,
        "document_count": len(documents),
        "chunk_count": len(embedded),
        "embedded_chunks": len(embedded),
        "status": "ready",
    }
    assert Path(require_text(report["log"])).is_file()
    assert all(
        any(text.startswith(doc.title + "\n") for doc in documents.values()) for text in embedded
    )
    assert {"KB-FAQ-REFUND-BALANCE-V1", "KB-FAQ-REFUND-BALANCE-V2"} <= documents.keys()


@pytest.mark.parametrize(
    ("fault", "hint"),
    [
        ("internal", "public|external|audience"),
        ("unlisted", "unlisted|unexpected"),
        ("canonical", "canonical"),
        ("wrong-pin", "sha256|hash"),
        ("legacy", "collection"),
        ("vector-space", "embedding|vector space"),
        ("duplicate-id", "duplicate.*doc_id"),
        ("duplicate-section", "duplicate.*section"),
        ("cycle", "supersedes|cycle"),
        ("overlap", "overlap"),
    ],
)
def test_build_rejects_before_embedding_or_writes(
    release_root: Path,
    offline: tuple[FakeStore, list[str]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fault: str,
    hint: str,
) -> None:
    store, embedded = offline
    root = release_root
    pin = files(root)[MANIFEST]
    if fault == "internal":
        pin = replace_document(root, OLD_FAQ, "audience: external", "audience: internal")
    elif fault == "unlisted":
        _ = shutil.copyfile(root / OLD_FAQ, root / "data/knowledge/unlisted.md")
    elif fault == "canonical":
        (root / CANONICAL).unlink()
    elif fault == "wrong-pin":
        pin = "0" * 64
    elif fault == "legacy":
        monkeypatch.setenv("FLOWPAY_KNOWLEDGE_COLLECTION_ID", LEGACY)
    elif fault == "vector-space":
        store.create_collection(TARGET, "flowpay-faq-v1", files(root)[CANONICAL], {}, 3)
        config = store.collections[TARGET]["build_config"]
        config["embedding"] = {
            "provider": "openai-compatible",
            "model": "other-model",
            "dimensions": 3,
            "revision": "unpinned",
            "base_url": "https://embedding.invalid/v1",
            "request_options": {},
        }
    elif fault == "duplicate-id":
        pin = replace_document(
            root, NEW_FAQ, "KB-FAQ-REFUND-BALANCE-V2", "KB-FAQ-REFUND-BALANCE-V1"
        )
    elif fault == "duplicate-section":
        pin = replace_document(
            root, NEW_FAQ, "## 按付款日还是退款日选规则", "## 什么时候可以发起余额退款核查"
        )
    elif fault == "cycle":
        pin = replace_document(
            root, OLD_FAQ, "---\n\n", "supersedes: KB-FAQ-REFUND-BALANCE-V2\n---\n\n"
        )
    elif fault == "overlap":
        pin = replace_document(
            root, OLD_FAQ, "2026-07-01T00:00:00+08:00", "2026-07-02T00:00:00+08:00"
        )
    else:
        raise AssertionError(fault)
    before = {key: dict(value) for key, value in store.collections.items()}

    with pytest.raises(SystemExit) as exc:
        _ = build(root, pin)

    assert exc.value.code == 2
    error = capsys.readouterr().err
    assert "invalid choice" not in error
    assert re.search(hint, error, re.IGNORECASE), error
    assert not embedded
    assert store.collections == before
    assert all(not docs for docs in store.documents.values())
    assert all(not vectors for vectors in store.vectors.values())
    assert not Path("var").exists()
