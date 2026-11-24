#!/usr/bin/env python3
"""构建命令的资源装配与报告输出；沿用项目事件库及单运行器锁。"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from tempfile import mkdtemp
from typing import Literal

from flowpay.core import knowledge
from flowpay.core.agent.trace import Tracer
from flowpay.core.config import Settings
from flowpay.core.event_log.store import EventStore, new_id
from flowpay.core.execution.contracts import TurnInput
from flowpay.core.execution.runtime import AgentRuntime
from flowpay.core.execution.types import TurnRecord
from flowpay.core.json import JsonObject, to_json_object
from flowpay.core.llm.embedding import EmbeddingSettings
from flowpay.core.llm.embedding.settings import same_vector_space
from typing_extensions import TypedDict

from scripts.auth import INTERNAL
from scripts.faq_content import validate_documents
from scripts.faq_release import RELEASE, verify_build_inputs

from .corpus import CorpusSettings, prepare_corpus
from .models import KnowledgeBuildPlan
from .pipeline import KnowledgePreparation


class KnowledgePreparationReport(TypedDict):
    """一次准备命令的运行结果、计划及日志位置；失败状态仍保留原始检查点。"""

    passed: bool
    operation: Literal["build", "verify"]
    session_id: str
    plan: KnowledgeBuildPlan
    result: JsonObject
    last_run: TurnRecord
    log: str
    embedding_requests: int


async def run_preparation(
    settings: Settings,
    corpus_settings: CorpusSettings,
    operation: Literal["plan", "build", "verify"],
    output_dir: Path,
) -> JsonObject:
    embedding = settings.embedding
    if embedding is None:
        raise ValueError("Knowledge preparation requires embedding configuration")
    corpus = await asyncio.to_thread(
        prepare_corpus, settings.dataset_path, corpus_settings, embedding
    )
    if operation == "plan":
        return to_json_object(corpus.summary())
    from flowpay.core.event_log.runtime import open_store
    from flowpay.core.execution.manager import SessionManager

    # 目标集合可能尚未 ready；构建运行不把它当作启动前置资源。
    runtime_settings = replace(settings, knowledge_collection_id=None)
    output_dir.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        store = stack.enter_context(open_store(runtime_settings))
        builder = knowledge.PostgresVectorStore(os.environ[settings.knowledge_builder_url_env])
        stack.callback(builder.close)
        reader = knowledge.PostgresVectorStore(os.environ[settings.knowledge_url_env])
        stack.callback(reader.close)
        workflow = KnowledgePreparation(
            corpus, embedding, builder, reader, settings.dataset_path, operation
        )
        manager = SessionManager(store, workflow, runtime_settings)
        try:
            await manager.recover()
            sid = (await manager.create_session(INTERNAL))["id"]
            await manager.submit_message(
                sid,
                new_id("request"),
                f"knowledge {operation}: {corpus.collection_id}",
                INTERNAL,
            )
            print(
                json.dumps(
                    {
                        "operation": operation,
                        "session_id": sid,
                        "documents": len(corpus.documents),
                        "chunks": sum(corpus.expected_documents.values()),
                    }
                ),
                flush=True,
            )
            await manager.tasks[sid]
            session = await manager.get_session(sid, INTERNAL)
            events = await asyncio.to_thread(store.read, sid)
            log = output_dir / f"{sid}.jsonl"
            log.write_text(
                "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events)
            )
            report: KnowledgePreparationReport = {
                "passed": session["status"] == "completed",
                "operation": operation,
                "session_id": sid,
                "plan": corpus.summary(),
                "result": session["state"],
                "last_run": session["runs"][-1],
                "log": str(log),
                "embedding_requests": sum(
                    e["type"] == "embedding.request" for e in events
                ),
            }
            path = output_dir / f"{sid}-report.json"
            path.write_text(
                json.dumps(to_json_object(report), ensure_ascii=False, indent=2) + "\n"
            )
            print(
                json.dumps({"passed": report["passed"], "report": str(path)}),
                flush=True,
            )
            if not report["passed"]:
                raise RuntimeError(f"Knowledge {operation} failed; inspect {path}")
            return to_json_object(report)
        finally:
            await manager.shutdown()


async def run_faq_build(
    root: Path, sha256: str, settings: Settings, output_dir: Path
) -> JsonObject:
    """从显式发布根构建独立集合，不加载默认数据源或会话状态。"""
    embedding: EmbeddingSettings | None = settings.embedding
    if embedding is None:
        raise ValueError("FAQ build requires embedding configuration")
    release = await asyncio.to_thread(verify_build_inputs, root, sha256)
    collection_id = f"faq_{release.release_id}"
    if settings.knowledge_collection_id not in (None, collection_id):
        raise ValueError(f"FAQ collection must be {collection_id}")
    if output_dir.resolve().is_relative_to(root.resolve()):
        raise ValueError("FAQ build output cannot be inside the release")
    dataset_root = root / "data"
    config = CorpusSettings(collection_id, RELEASE.removeprefix("data/"), sha256)
    corpus = await asyncio.to_thread(prepare_corpus, dataset_root, config, embedding)
    _ = validate_documents((doc.source_path, doc.content) for doc, _ in corpus.documents)

    with ExitStack() as stack:
        builder = knowledge.PostgresVectorStore(os.environ["FLOWPAY_FAQ_KNOWLEDGE_BUILDER_DSN"])
        stack.callback(builder.close)
        try:
            existing = await asyncio.to_thread(builder.get_collection, collection_id)
        except KeyError:
            pass  # 新集合由共享构建器创建；写事务仍会核对完整身份。
        else:
            if existing["dimensions"] != embedding.dimensions or not same_vector_space(
                existing["build_config"].get("embedding"), embedding.identity()
            ):
                raise ValueError("FAQ collection embedding vector space differs")

        output_dir.mkdir(parents=True, exist_ok=True)
        run_dir = Path(mkdtemp(prefix="run-", dir=output_dir))
        log = run_dir / "events.sqlite"
        tracer = Tracer(EventStore(log), new_id("session"), new_id("run"))
        workflow = KnowledgePreparation(corpus, embedding, builder, builder, dataset_root, "build")
        result = await workflow.run(
            TurnInput(f"knowledge build: {collection_id}", [], {}), AgentRuntime(tracer)
        )
        report: JsonObject = {
            "plan": to_json_object(corpus.summary()),
            "result": result.saved_state,
            "log": str(log),
        }
        (run_dir / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return report
