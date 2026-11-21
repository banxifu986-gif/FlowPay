"""Ollama 模型版本核对与 Qwen 查询输入约定，供构建和检索装配复用。"""

from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from flowpay.core.json import (
    JsonObject,
    parse_json,
    require_list,
    require_object,
    require_text,
)
from flowpay.core.llm.embedding import EmbeddingSettings
from flowpay.core.llm.embedding.settings import LOCAL_ENDPOINTS

QUERY_INSTRUCTION = "Given a technical support question, retrieve relevant passages that answer it."


@dataclass(frozen=True)
class OllamaDeployment:
    """已核对的本地模型部署；details 保留供应商可扩展字段。"""

    ollama_version: str
    model: str
    digest: str
    details: JsonObject


def uses_local_ollama(settings: EmbeddingSettings) -> bool:
    """判定嵌入端点是否为本机 Ollama：云端服务没有 native API，部署核对只适用于前者。"""
    return urlsplit(settings.base_url).hostname in LOCAL_ENDPOINTS


async def ollama_identity(settings: EmbeddingSettings) -> OllamaDeployment:
    """核对标签实际指向的 digest，避免同名模型更新后混用旧向量。"""
    endpoint = urlsplit(settings.base_url)
    origin = f"{endpoint.scheme}://{endpoint.netloc}"
    async with httpx.AsyncClient(base_url=origin, timeout=10) as client:
        version = await client.get("/api/version")
        version.raise_for_status()
        tags = await client.get("/api/tags")
        tags.raise_for_status()
    for row in require_list(require_object(parse_json(tags.content))["models"]):
        model = require_object(row)
        if model["name"] == settings.name:
            if model["digest"] != settings.revision:
                raise ValueError("Ollama model digest differs from embedding.revision")
            return OllamaDeployment(
                ollama_version=require_text(require_object(parse_json(version.content))["version"]),
                model=settings.name,
                digest=require_text(model["digest"]),
                details=require_object(model["details"]),
            )
    raise ValueError(f"Model is not installed: {settings.name}")


def retrieval_input(question: str) -> str:
    """只在查询侧加入指令，实际输入仍由 EmbeddingClient 原样记录。"""
    return f"Instruct: {QUERY_INSTRUCTION}\nQuery: {question}"


# 查询侧输入约定按嵌入模型分流，未知模型在装配前拒绝，不猜格式。
QUERY_INPUT_CONVENTIONS: dict[str, Callable[[str], str]] = {
    "qwen3-embedding:0.6b": retrieval_input,
    # bge-m3 文档口径：短查询→段落检索不加指令前缀，用原始问题文本。
    "BAAI/bge-m3": lambda question: question,
}


def query_input_for(settings: EmbeddingSettings) -> Callable[[str], str]:
    """返回该嵌入模型使用的查询侧输入变换；约定只有这一处。"""
    convention = QUERY_INPUT_CONVENTIONS.get(settings.name)
    if convention is None:
        raise ValueError(f"No query input convention configured for: {settings.name}")
    return convention
