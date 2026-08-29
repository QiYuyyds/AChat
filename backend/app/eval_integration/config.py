"""Aeval 装配入口 — create_aeval_runner() (任务 2.4)。

从 AChat settings 读取 api_base / agent_id / 凭证 / Phoenix endpoint,
构造完整 EvalRunner (AChatAgentRunner + AChatWorkspaceEnvironment +
AChat graders + SQLite 存储 + PhoenixProvider)。

缺凭证时报 EvalConfigError 并列出全部缺失项。装配同时安装进程内
RunTraceBridge (§14.1.2 trace_id 主通道)。
"""

from __future__ import annotations

import logging
from typing import Any

from eval_integration import trace_bridge
from eval_integration.client import AChatApiClient
from eval_integration.environment import AChatWorkspaceEnvironment
from eval_integration.errors import EvalConfigError
from eval_integration.graders import AChatArtifactGrader, AChatDispatchGrader
from eval_integration.runner import AChatAgentRunner, WorkspaceCoordinator

logger = logging.getLogger(__name__)


def resolve_api_base(settings: Any) -> str:
    """评测回调 API 地址: EVAL_API_BASE 优先, 缺省本机服务端口。"""
    return settings.eval_api_base or f"http://127.0.0.1:{settings.port}"


def make_token_provider(settings: Any) -> Any:
    """Bearer token 提供器。

    EVAL_USER_TOKEN 非空 → 直接使用; 否则进程内为 default_user_email 用户
    铸造 access token (结果缓存)。用户不存在 → EvalConfigError。
    """
    explicit = settings.eval_user_token
    cached: list[str] = []

    async def provider() -> str:
        if explicit:
            return explicit
        if cached:
            return cached[0]
        token = await _mint_default_user_token(settings)
        cached.append(token)
        return token

    return provider


async def _mint_default_user_token(settings: Any) -> str:
    from sqlalchemy import select

    from app.auth.jwt_handler import create_access_token
    from app.db.engine import get_remote_db
    from app.db.models import User

    async with get_remote_db() as db:
        result = await db.execute(
            select(User).where(User.email == settings.default_user_email)
        )
        user = result.scalar_one_or_none()
    if user is None:
        raise EvalConfigError(
            "eval_user_token not configured and default user "
            f"'{settings.default_user_email}' not found. Set EVAL_USER_TOKEN "
            "(a valid access JWT) or bootstrap the default user first."
        )
    return create_access_token(user.id, user.email, user.token_version)


def check_credentials(settings: Any) -> None:
    """装配前置检查 — 缺失项一次性列全。"""
    missing: list[str] = []
    if not settings.eval_agent_id:
        missing.append("EVAL_AGENT_ID (被评 agent id, 无默认值)")
    if missing:
        raise EvalConfigError(
            "Aeval integration misconfigured — missing: " + "; ".join(missing)
        )


async def create_aeval_runner(settings: Any = None):
    """构造 AChat 接入的完整 EvalRunner (eval_harness.core.runner.EvalRunner)。"""
    from eval_harness.core.runner import EvalRunner
    from eval_harness.storage.sqlite import SqliteStorage

    if settings is None:
        from app.config import get_settings

        settings = get_settings()
    check_credentials(settings)

    coordinator = WorkspaceCoordinator()
    client = AChatApiClient(
        base_url=resolve_api_base(settings),
        token_provider=make_token_provider(settings),
    )
    agent_runner = AChatAgentRunner(
        client,
        settings.eval_agent_id,
        run_timeout=settings.eval_run_timeout,
        coordinator=coordinator,
        # 会话删除由环境管理器 teardown 负责
        cleanup_conversations=False,
    )
    environment = AChatWorkspaceEnvironment(client, coordinator)

    # §14.1.2: 进程内 trace_id 主通道 (零侵入 SpanProcessor)
    bridge_ok = trace_bridge.install_trace_bridge()
    if not bridge_ok:
        logger.warning(
            "RunTraceBridge not installed: global OTel provider does not accept "
            "span processors (trace_enabled=false?) — trials will fail trace_id "
            "resolution unless a fallback applies."
        )

    db_path = settings.eval_aeval_db_path or str(settings.data_path / "aeval.db")
    storage = SqliteStorage(db_path=db_path)
    await storage.initialize()

    # Judge LLM + P0 指标注册表 (change ③): 注入 EvalRunner 供 metric
    # grader 分发; 无凭证时 llm_fn=None → metric grader 返回明确配置错误
    from eval_harness.metrics import build_default_metrics_registry

    llm_fn = make_judge_llm_fn(settings)
    metrics_registry = build_default_metrics_registry(llm_fn=llm_fn)

    return EvalRunner(
        agent_runner=agent_runner,
        trace_provider=_make_trace_provider(settings),
        storage=storage,
        environment=environment,
        graders=[AChatArtifactGrader(), AChatDispatchGrader()],
        concurrency=1,  # §17.5: 隔离正确性优先, 并发默认 1
        metrics_registry=metrics_registry,
        llm_fn=llm_fn,
    )


def _make_trace_provider(settings: Any) -> Any:
    """PhoenixProvider; phoenix SDK 缺失时降级为空 provider (仅告警一次)。

    trace 拉取失败不应整 trial 失败 — outcome/transcript 评分仍可进行;
    span 依赖型 grader 会得到空 spans 并按各自语义判分。
    """
    from eval_harness.trace.phoenix import PhoenixProvider

    class TolerantPhoenixProvider(PhoenixProvider):
        _warned = False

        async def get_spans(self, trace_id: str):
            try:
                return await super().get_spans(trace_id)
            except RuntimeError as e:  # phoenix package not installed
                if not TolerantPhoenixProvider._warned:
                    TolerantPhoenixProvider._warned = True
                    logger.warning("Phoenix provider unavailable: %s", e)
                return []

    return TolerantPhoenixProvider(
        endpoint=settings.phoenix_ui_url, project="default"
    )


# ─── Judge LLM (LLM 输出质量指标, change ③) ──────────────────────────────────


def resolve_judge_config(settings: Any) -> dict[str, str] | None:
    """
    解析 judge LLM 配置 — AEVAL_JUDGE_* 优先, 复用既有 LLM 配置为后备。

    顺序 (评测流量与业务流量隔离, design D2/Open Questions 结论):
    1. aeval_judge_*  (env AEVAL_JUDGE_API_KEY / _API_URL / _MODEL)
    2. eval_llm_*     (评测专用 LLM, RAG eval 同款)
    3. openai_api_key (AChat 既有 OpenAI 凭证, 模型取默认)

    Returns:
        {"api_key", "base_url", "model"} 或 None (无任何可用凭证)
    """
    api_key = (
        settings.aeval_judge_api_key
        or settings.eval_llm_api_key
        or settings.openai_api_key
    )
    if not api_key:
        return None
    return {
        "api_key": api_key,
        "base_url": settings.aeval_judge_api_url or settings.eval_llm_api_url,
        "model": (
            settings.aeval_judge_model
            or settings.eval_llm_model
            or "gpt-4o-mini"
        ),
    }


def make_judge_llm_fn(settings: Any):
    """
    构造 judge LLM 函数 (LLMFn 协议: async (system, user) → raw text)。

    无可用凭证时返回 None — EvalRunner 侧 metric 类 grader 将得到明确
    配置错误结果 (不 crash run); LLM 生成类数据集端点返回 503。
    """
    cfg = resolve_judge_config(settings)
    if cfg is None:
        logger.info(
            "Aeval judge LLM not configured (AEVAL_JUDGE_API_KEY / EVAL_LLM_API_KEY "
            "/ OPENAI_API_KEY) — metric graders will report config errors"
        )
        return None

    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])
    model = cfg["model"]

    async def judge_llm_fn(system: str, user: str) -> str:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.0,
        )
        return resp.choices[0].message.content or ""

    logger.info("Aeval judge LLM assembled (model=%s)", model)
    return judge_llm_fn
