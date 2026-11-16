# 提供会话 HTTP 接口与 SSE 事件订阅；只推送已持久化事件，重连按序号补齐。
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import ExitStack, asynccontextmanager
from typing import Annotated, ClassVar, cast

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from flowpay.core.auth import Authenticator, AuthError, Principal
from flowpay.core.chat.errors import SessionNotFound
from flowpay.core.chat.types import SessionSummary, SessionView
from flowpay.core.config import Settings
from flowpay.core.event_log.runtime import open_store
from flowpay.core.event_log.types import Event
from flowpay.core.execution.contracts import Workflow
from flowpay.core.execution.errors import Conflict
from flowpay.core.execution.manager import SessionManager
from flowpay.core.execution.resources import RuntimeResources
from flowpay.core.execution.types import TurnRecord


class MessageBody(BaseModel):
    """发送消息 HTTP 接口的请求体；校验幂等 ID 与正文长度并拒绝额外字段。"""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=20000)


class ContinueBody(BaseModel):
    """继续运行 HTTP 接口的请求体；只接收新的幂等 ID，原问题从会话记录读取。"""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=128)


class DeleteSessionsBody(BaseModel):
    """批量删除请求；限制数量并拒绝空白 ID，避免无界批量操作。"""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    session_ids: list[Annotated[str, Field(min_length=1, pattern=r"\S")]] = Field(
        min_length=1, max_length=100
    )


class DeleteSessionsResult(BaseModel):
    """返回实际删除的会话 ID；不存在的会话不影响幂等重试。"""

    deleted_ids: list[str]


class WorkflowInfo(BaseModel):
    """前端展示的工作流身份和说明。"""

    id: str
    version: str
    label: str
    description: str


class ApplicationInfo(BaseModel):
    """页面初始化所需的协议版本、工作流与输入提示。"""

    protocol_version: int = 1
    workflow: WorkflowInfo
    model: str
    examples: list[str]
    reply_hint: str | None


class LoginBody(BaseModel):
    """登录请求体；只收用户名与口令，拒绝额外字段。"""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)


class LoginResultBody(BaseModel):
    """登录响应：令牌、过期时间与身份；前端据此展示角色，不自行解析令牌。"""

    token: str
    expires_at: str
    username: str
    role: str
    account_id: str | None


# 不门禁模式使用的主体：契约假后端（scripts/demo.py）不提供登录，会话照旧可读写。
DEMO_PRINCIPAL = Principal(username="demo", role="internal")
LOGIN_PATH = "/api/auth/login"


def unauthorized(error: AuthError) -> JSONResponse:
    """统一的认证失败结构体；不区分账号不存在、口令错误与令牌无效。"""
    return JSONResponse(
        {"detail": error.detail, "code": error.code},
        status_code=error.status_code,
        headers={"WWW-Authenticate": "Bearer"},
    )


def current_principal(request: Request) -> Principal:
    """端点只读中间件写入的主体：令牌解析不在业务代码里重复实现。

    放在模块级而不是 create_app 的闭包里：postponed 注解只能按模块全局名解析，
    闭包里的函数名会让 FastAPI 把依赖当成查询参数。
    """
    return cast(Principal, request.state.principal)


def protected_path(path: str) -> bool:
    """除登录接口外，所有 /api/* 都要求令牌；SSE 与 health 也不例外。"""
    return path.startswith("/api") and path != LOGIN_PATH


# 应用生命周期负责存储与任务恢复，HTTP 连接仅负责提交和读取。
def create_app(
    settings: Settings, workflow: Workflow, authenticator: Authenticator | None = None
) -> FastAPI:
    """构造与业务工作流解耦的 HTTP 应用。

    启动时获取存储锁并恢复中断状态，关闭时先结束管理器任务再释放存储。
    消息接口仅受理任务并返回 202；SSE 单独订阅持久化事件，不承载模型执行。
    网页断开连接只停止订阅，用户取消必须走 cancel 接口。
    authenticator 为 None 表示不门禁：只有契约假后端与相应验收这么用，
    真实服务入口由 bootstrap 装配，缺少账号或密钥时启动前就失败。
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        with ExitStack() as stack:
            store = stack.enter_context(open_store(settings))
            resources = RuntimeResources.from_settings(settings)
            stack.callback(resources.close)
            manager = SessionManager(store, workflow, settings, resources)
            app.state.manager = manager
            # 先取得独占运行锁，再补齐遗留运行的中断状态，之后才开始接受请求。
            await manager.recover()
            try:
                yield
            finally:
                await manager.shutdown()

    app = FastAPI(title="FlowPay Foundation", version="0.1.0", lifespan=lifespan)

    # 门禁放在应用层（specs/005）：提示词与前端都不是边界，服务端先校验令牌。
    # 令牌处理失败在此直接返回 401，不进入端点，也不因路径不同而放宽。
    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        principal = DEMO_PRINCIPAL
        if authenticator is not None and protected_path(request.url.path):
            header = request.headers.get("authorization", "")
            token = header[7:] if header.lower().startswith("bearer ") else ""
            try:
                principal = await asyncio.to_thread(authenticator.authenticate, token)
            except AuthError as error:
                return unauthorized(error)
        # 端点只读这个主体，不重复解析请求头；不门禁模式也走同一条路径。
        request.state.principal = principal
        return await call_next(request)

    app.middleware("http")(guard)

    def get_manager() -> SessionManager:
        """生命周期已在 app.state 写入管理器；在框架的动态 State 边界恢复类型。"""
        return cast(SessionManager, app.state.manager)

    async def not_found(_: Request, _error: Exception) -> JSONResponse:
        return JSONResponse({"detail": "Session not found"}, status_code=404)

    async def auth_failed(_: Request, error: Exception) -> JSONResponse:
        # exception_handler 的形参声明是 Exception，实际只会收到 AuthError。
        return unauthorized(cast(AuthError, error))

    async def conflict(_: Request, error: Exception) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=409)

    async def health() -> JSONResponse:
        healthy = not get_manager().storage_error
        return JSONResponse(
            {"status": "ok" if healthy else "storage_error"}, status_code=200 if healthy else 503
        )

    async def login(body: LoginBody) -> LoginResultBody:
        """校验用户名与口令并签发令牌；未装配认证时明确拒绝，不静默放行。

        口令派生是 CPU 活，放线程避免阻塞事件循环；失败由 AuthError 处理器转成 401。
        """
        if authenticator is None:
            raise HTTPException(503, "Authentication is not configured")
        result = await asyncio.to_thread(authenticator.login, body.username, body.password)
        return LoginResultBody(
            token=result.token,
            expires_at=result.expires_at.isoformat(),
            username=result.principal.username,
            role=result.principal.role,
            account_id=result.principal.account_id,
        )

    async def meta() -> ApplicationInfo:
        """返回有明确响应模式的页面初始化信息。"""
        return ApplicationInfo(
            workflow=WorkflowInfo(
                id=workflow.id,
                version=workflow.version,
                label=workflow.card.label,
                description=workflow.card.description,
            ),
            model=settings.model.name,
            examples=workflow.card.examples,
            reply_hint=workflow.card.reply_hint,
        )

    async def sessions(
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> list[SessionSummary]:
        return await get_manager().list_sessions(principal)

    async def create_session(
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> SessionView:
        return await get_manager().create_session(principal)

    async def session(
        session_id: str, principal: Annotated[Principal, Depends(current_principal)]
    ) -> SessionView:
        return await get_manager().get_session(session_id, principal)

    async def message(
        session_id: str,
        body: MessageBody,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> TurnRecord:
        content = body.content.strip()
        if not content:
            raise HTTPException(422, "Message cannot be blank")
        return await get_manager().submit_message(session_id, body.request_id, content, principal)

    async def resume(
        session_id: str,
        body: ContinueBody,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> TurnRecord:
        return await get_manager().resume_run(session_id, body.request_id, principal)

    async def cancel(
        session_id: str, principal: Annotated[Principal, Depends(current_principal)]
    ) -> SessionView:
        await get_manager().cancel_run(session_id, principal)
        return await get_manager().get_session(session_id, principal)

    async def delete_session(
        session_id: str, principal: Annotated[Principal, Depends(current_principal)]
    ) -> DeleteSessionsResult:
        if not session_id.strip():
            raise HTTPException(422, "Session ID cannot be blank")
        deleted = await get_manager().delete_sessions([session_id], principal)
        return DeleteSessionsResult(deleted_ids=deleted)

    async def delete_sessions(
        body: DeleteSessionsBody,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> DeleteSessionsResult:
        return DeleteSessionsResult(
            deleted_ids=await get_manager().delete_sessions(body.session_ids, principal)
        )

    async def events(
        session_id: str,
        principal: Annotated[Principal, Depends(current_principal)],
        after: Annotated[int, Query(ge=0)] = 0,
    ) -> list[Event]:
        await get_manager().get_session(session_id, principal)
        return await asyncio.to_thread(get_manager().store.read, session_id, after)

    async def export(
        session_id: str, principal: Annotated[Principal, Depends(current_principal)]
    ) -> Response:
        await get_manager().get_session(session_id, principal)
        rows = await asyncio.to_thread(get_manager().store.read, session_id)
        return Response(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            media_type="application/x-ndjson",
            headers={
                "Content-Disposition": f'attachment; filename="{session_id}.jsonl"',
            },
        )

    async def stream(
        request: Request,
        session_id: str,
        principal: Annotated[Principal, Depends(current_principal)],
        after: Annotated[int, Query(ge=0)] = 0,
        last_event_id: Annotated[str | None, Header()] = None,
    ) -> StreamingResponse:
        await get_manager().get_session(session_id, principal)
        try:
            # 首次连接用查询参数，EventSource 自动重连会带 Last-Event-ID。
            # 取较大值避免退回旧游标；客户端仍需去重以应对边界处的重复交付。
            cursor = max(after, int(last_event_id or "0"))
        except ValueError as error:
            raise HTTPException(400, "Invalid Last-Event-ID") from error

        async def generate() -> AsyncGenerator[str]:
            nonlocal cursor
            heartbeat = 0
            while not await request.is_disconnected():
                # 从存储读已提交事件，不直接订阅模型内存回调；页面看到的每条消息均可回放。
                # 读取与下一次轮询之间产生的事件会在后续按 seq 补齐。
                rows = await asyncio.to_thread(get_manager().store.read, session_id, cursor)
                if not rows and not await asyncio.to_thread(get_manager().store.read, session_id):
                    yield "event: session-deleted\ndata: {}\n\n"
                    return
                for event in rows:
                    cursor = event["seq"]
                    yield (
                        f"id: {cursor}\nevent: trace\ndata: "
                        f"{json.dumps(event, ensure_ascii=False)}\n\n"
                    )
                heartbeat += 1
                # SSE 注释心跳不消耗事件序号，也不写会话日志，仅用于维持空闲连接。
                if heartbeat % 40 == 0:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.25)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    app.exception_handler(SessionNotFound)(not_found)
    app.exception_handler(Conflict)(conflict)
    app.exception_handler(AuthError)(auth_failed)
    app.post(LOGIN_PATH)(login)
    app.get("/api/health")(health)
    app.get("/api/meta")(meta)
    app.get("/api/sessions")(sessions)
    app.post("/api/sessions", status_code=201)(create_session)
    app.post("/api/sessions/batch-delete")(delete_sessions)
    app.delete("/api/sessions/{session_id}")(delete_session)
    app.get("/api/sessions/{session_id}")(session)
    app.post("/api/sessions/{session_id}/messages", status_code=202)(message)
    app.post("/api/sessions/{session_id}/continue", status_code=202)(resume)
    app.post("/api/sessions/{session_id}/cancel")(cancel)
    app.get("/api/sessions/{session_id}/events")(events)
    app.get("/api/sessions/{session_id}/export")(export)
    app.get("/api/sessions/{session_id}/stream")(stream)

    return app
