# 回归测试：失败分类与恢复策略映射（specs/006 US2）。
import asyncio
from pathlib import Path

import pytest

from flowpay.core.config import Settings
from flowpay.core.event_log import EventStore
from flowpay.core.execution.contracts import TurnInput, TurnResult
from flowpay.core.execution.errors import QuotaExceeded
from flowpay.core.execution.failures import FailureInfo, classify_failure
from flowpay.core.execution.manager import SessionManager
from flowpay.core.execution.runtime import AgentRuntime
from flowpay.core.execution.stage import StageCard
from flowpay.core.json import JsonObject, require_list, require_object, require_text
from flowpay.core.llm.chat.http_error import ModelHTTPError
from tests.core.principals import INTERNAL, MERCHANT


@pytest.mark.parametrize(
    "error, category, retryable",
    [
        (TimeoutError("slow"), "timeout", True),
        (ModelHTTPError(429, "rate limited"), "rate_limit", True),
        (ModelHTTPError(429, "quota exceeded"), "quota", False),
        (ModelHTTPError(503, "upstream down"), "model_5xx", True),
        (ModelHTTPError(400, "maximum context length exceeded"), "context_over_limit", False),
        (RuntimeError("boom"), "other", False),
    ],
)
def test_failure_classification(error: Exception, category: str, retryable: bool) -> None:
    """分类只看类型与状态码；配额关键词优先于普通限流。"""
    info = classify_failure(error)
    assert info.category == category
    assert info.retryable is retryable
    assert info.message


class FlakyWorkflow:
    """前 n 次抛指定异常，之后返回完成；用于验证重试次数与终态文案。"""

    id: str = "flaky"
    version: str = "1"
    card: StageCard = StageCard(label="Flaky", description="failure fixture")
    calls: int = 0

    def __init__(self, error: Exception, failures: int) -> None:
        self.error: Exception = error
        self.failures: int = failures

    async def run(self, turn: TurnInput, runtime: AgentRuntime) -> TurnResult:
        _ = turn, runtime
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return TurnResult("fixture reply", {})


def manager(tmp_path: Path, workflow: FlakyWorkflow, attempts: int = 2) -> SessionManager:
    settings = Settings(
        tmp_path,
        tmp_path / "dataset",
        failure_retry_attempts=attempts,
        failure_retry_delay_seconds=0,
    )
    return SessionManager(EventStore(tmp_path / "sessions.sqlite"), workflow, settings)


async def test_transient_failure_retries_then_completes(tmp_path: Path) -> None:
    """5xx 按映射退避重试，run.retry 留痕；成功后正常提交。"""
    workflow = FlakyWorkflow(ModelHTTPError(503, "upstream down"), failures=1)
    runtime = manager(tmp_path, workflow)
    sid = (await runtime.create_session(INTERNAL))["id"]
    await runtime.submit_message(sid, "one", "question", INTERNAL)
    task = runtime.tasks[sid]
    await task
    view = await runtime.get_session(sid, INTERNAL)
    assert view["status"] == "completed"
    events = runtime.store.read(sid)
    retry = [e for e in events if e["type"] == "run.retry"]
    assert retry and retry[0]["payload"]["category"] == "model_5xx"


async def test_quota_failure_is_not_retried_and_has_friendly_text(tmp_path: Path) -> None:
    """额度用尽不重试；终态载荷含分类与用户可见文案。"""
    workflow = FlakyWorkflow(ModelHTTPError(429, "quota exceeded"), failures=99)
    runtime = manager(tmp_path, workflow)
    sid = (await runtime.create_session(INTERNAL))["id"]
    await runtime.submit_message(sid, "one", "question", INTERNAL)
    await runtime.tasks[sid]
    view = await runtime.get_session(sid, INTERNAL)
    assert view["status"] == "failed"
    error = require_object(view["runs"][-1].get("error") or {})
    assert require_text(error["category"]) == "quota"
    assert "配额" in require_text(error["message"])
    assert workflow.calls == 1


async def test_exhausted_retries_record_final_category(tmp_path: Path) -> None:
    """重试耗尽后终态仍可区分超时；尝试数等于配置的额外次数 + 1。"""
    workflow = FlakyWorkflow(TimeoutError("slow"), failures=99)
    runtime = manager(tmp_path, workflow, attempts=1)
    sid = (await runtime.create_session(INTERNAL))["id"]
    await runtime.submit_message(sid, "one", "question", INTERNAL)
    await runtime.tasks[sid]
    view = await runtime.get_session(sid, INTERNAL)
    error = require_object(view["runs"][-1].get("error") or {})
    assert require_text(error["category"]) == "timeout"
    assert workflow.calls == 2


# FR-1 确定性白名单（specs/009；错误形态枚举，测试固化）。首例是 R96B
# FP-BM-TV-0116 的真实嵌入 400 正文（SiliconFlow code=20015）。
DETERMINISTIC_ERRORS = [
    ModelHTTPError(400, '{"code":20015,"message":"The parameter is invalid. Please check again."}'),
    ModelHTTPError(422, "invalid input: query text exceeds schema"),
    ModelHTTPError(400, "参数不合法：查询文本为空"),
    ModelHTTPError(400, "unsupported parameter: reasoning_effort"),
    ModelHTTPError(422, "unknown parameter temperature for this model"),
]
TRANSIENT_ERRORS = [
    TimeoutError("slow"),
    ModelHTTPError(429, "rate limited"),
    ModelHTTPError(503, "upstream down"),
    RuntimeError("connection reset"),
]


def _status_id(error: ModelHTTPError) -> str:
    """参数化用例 ID：取状态码区分白名单形态。"""
    return f"{error.status_code}"


@pytest.mark.parametrize("error", DETERMINISTIC_ERRORS, ids=_status_id)
def test_deterministic_whitelist_forms(error: Exception) -> None:
    """白名单①②：输入类 4xx 与参数确定性拒绝进确定性档，且必不可重试。"""
    info = classify_failure(error)
    assert info.deterministic is True
    assert info.retryable is False


@pytest.mark.parametrize(
    "error",
    [
        *TRANSIENT_ERRORS,
        ModelHTTPError(400, "maximum context length exceeded"),  # 硬失败，不进确定性档
        ModelHTTPError(400, "the request was malformed"),  # 未命中白名单的 400 保持 other
    ],
)
def test_transient_and_hard_failures_stay_non_deterministic(error: Exception) -> None:
    """429/超时/5xx/连接错误仍属瞬时；上下文超限与未命中白名单的 400 不是确定性。"""
    info = classify_failure(error)
    assert info.deterministic is False


def test_failure_info_constraint_deterministic_implies_not_retryable() -> None:
    """结构约束：deterministic=True 时 retryable 必为 False（构造期拒绝反例）。"""
    with pytest.raises(ValueError):
        FailureInfo("other", True, "文案", deterministic=True)


async def test_deterministic_error_keeps_manager_semantics(tmp_path: Path) -> None:
    """模型侧重试语义零改动：白名单 400 在 workflow 层抛出仍按 other 直接终态。

    确定性档的消费方是工具包装层与 tool.outage 载荷，不是 manager 重试点——
    终态 category 与 run.failed 行为必须与既有口径逐字段一致（FR-11）。
    """
    workflow = FlakyWorkflow(DETERMINISTIC_ERRORS[0], failures=99)
    runtime = manager(tmp_path, workflow)
    sid = (await runtime.create_session(INTERNAL))["id"]
    await runtime.submit_message(sid, "one", "question", INTERNAL)
    await runtime.tasks[sid]
    view = await runtime.get_session(sid, INTERNAL)
    assert view["status"] == "failed"
    error = require_object(view["runs"][-1].get("error") or {})
    assert require_text(error["category"]) == "other"
    assert not [e for e in runtime.store.read(sid) if e["type"] == "run.retry"]
    assert workflow.calls == 1


class ResumeProbeWorkflow:
    """首轮失败并发出断点事件，续跑轮记录 TurnInput；恢复语义的可控探针。

    gate 设置后恢复轮在 run 内等待，供并发预留断言在恢复运行中插入检查。
    """

    id: str = "resume-probe"
    version: str = "1"
    card: StageCard = StageCard(label="ResumeProbe", description="resume fixture")

    def __init__(self, progress_payloads: list[JsonObject]) -> None:
        self.progress_payloads: list[JsonObject] = progress_payloads
        self.turns: list[TurnInput] = []
        self.gate: asyncio.Event | None = None

    async def run(self, turn: TurnInput, runtime: AgentRuntime) -> TurnResult:
        self.turns.append(turn)
        if len(self.turns) == 1:
            for payload in self.progress_payloads:
                await runtime.tracer.emit("turn.progress", payload)
            raise RuntimeError("boom")
        if self.gate is not None:
            await self.gate.wait()
        return TurnResult("ok", {})


async def test_resume_bypasses_daily_count_and_injects_progress(tmp_path: Path) -> None:
    """FR-5 计量修正 + FR-4 注入：恢复轮不计日回合，saved_state 注入合并后的断点。

    日上限 2、首轮已计 1：若恢复轮的 run.started 再计数则 2>=2 被拒——恢复
    成功本身证明过滤生效；注入载荷按幂等键合并，已收束条目保留。
    """
    payloads: list[JsonObject] = [
        {
            "key": "doc_worker:查明",
            "worker": "doc_worker",
            "objective": "查明",
            "done_keys": [],
            "findings": [],
            "evidence_refs": [],
            "finished": False,
        },
        {
            "key": "doc_worker:查明",
            "worker": "doc_worker",
            "objective": "查明",
            "done_keys": ["search:a"],
            "findings": [],
            "evidence_refs": ["doc:RULE-1#c001"],
            "finished": True,
        },
    ]
    workflow = ResumeProbeWorkflow(payloads)
    settings = Settings(
        tmp_path,
        tmp_path / "dataset",
        failure_retry_attempts=0,
        quota_internal_daily_turns=2,
    )
    runtime = SessionManager(EventStore(tmp_path / "sessions.sqlite"), workflow, settings)
    sid = (await runtime.create_session(INTERNAL))["id"]
    await runtime.submit_message(sid, "one", "question", INTERNAL)
    await runtime.tasks[sid]
    assert (await runtime.get_session(sid, INTERNAL))["status"] == "failed"
    await runtime.resume_run(sid, "resume", INTERNAL)
    await runtime.tasks[sid]
    assert (await runtime.get_session(sid, INTERNAL))["status"] == "completed"
    resumed = workflow.turns[1]
    assert resumed.resumed is True
    progress = require_list(resumed.saved_state.get("task_progress") or [])
    assert len(progress) == 1  # 同幂等键合并
    merged = require_object(progress[0])
    assert merged["finished"] is True
    assert require_list(merged["done_keys"]) == ["search:a"]
    assert require_list(merged["evidence_refs"]) == ["doc:RULE-1#c001"]


async def test_resume_reserves_merchant_concurrency_symmetrically(tmp_path: Path) -> None:
    """并发对称（审查 B1）：恢复运行中商户并发名额已预留；结束按对称释放不净减。"""
    workflow = ResumeProbeWorkflow([])
    settings = Settings(
        tmp_path,
        tmp_path / "dataset",
        failure_retry_attempts=0,
        quota_merchant_concurrency=1,
    )
    runtime = SessionManager(EventStore(tmp_path / "sessions.sqlite"), workflow, settings)
    sid = (await runtime.create_session(MERCHANT))["id"]
    await runtime.submit_message(sid, "one", "question", MERCHANT)
    await runtime.tasks[sid]
    assert (await runtime.get_session(sid, MERCHANT))["status"] == "failed"
    workflow.gate = asyncio.Event()
    await runtime.resume_run(sid, "resume", MERCHANT)
    other = (await runtime.create_session(MERCHANT))["id"]
    with pytest.raises(QuotaExceeded) as exc:
        await runtime.submit_message(other, "two", "another", MERCHANT)
    assert "正在运行" in str(exc.value)  # 恢复轮占着并发名额
    workflow.gate.set()
    await runtime.tasks[sid]
    assert (await runtime.get_session(sid, MERCHANT))["status"] == "completed"
    await runtime.submit_message(other, "three", "third", MERCHANT)  # 名额已释放
    await runtime.tasks[other]
    assert (await runtime.get_session(other, MERCHANT))["status"] == "completed"
