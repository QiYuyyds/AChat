"""Aeval 装配入口 — create_aeval_runner() (任务 2.4)。

从 AChat settings 读取 api_base / agent_id / 凭证 / Phoenix endpoint,
构造完整 EvalRunner (AChatAgentRunner + AChatWorkspaceEnvironment +
AChat graders + SQLite 存储 + PhoenixProvider + agenthub.* 属性翻译表)。

缺凭证时报 EvalConfigError 并列出全部缺失项。装配同时安装进程内
RunTraceBridge (§14.1.2 trace_id 主通道)。
"""

from __future__ import annotations

import logging
from typing import Any

from app.eval_integration import trace_bridge
from app.eval_integration.client import AChatApiClient
from app.eval_integration.environment import AChatWorkspaceEnvironment
from app.eval_integration.errors import EvalConfigError
from app.eval_integration.graders import AChatArtifactGrader, AChatDispatchGrader
from app.eval_integration.runner import AChatAgentRunner, WorkspaceCoordinator
from app.observability.instrumentation import (
    AGENTHUB_CONVERSATION_ID,
    AGENTHUB_INPUT_TOKENS,
    AGENTHUB_MODEL,
    AGENTHUB_OUTPUT_TOKENS,
    AGENTHUB_SUCCESS,
    AGENTHUB_TOOL_NAME,
)

logger = logging.getLogger(__name__)

# Aeval 的 trace 归一化默认只认 OTel GenAI 属性名 (`gen_ai.*`), 而本项目落到
# Phoenix 的词汇实际有三套: LLM span 走 OpenInference (`llm.*`), 业务属性被按
# 前缀收进一个名为 `agenthub` 的嵌套 dict, 少数走裸键。不注入这张表, 所有过程
# 字段都会被判为「证据缺失」→ trial 记 invalid → 通过率 insufficient_data,
# 且门禁直接拒绝信任整场 run。
#
# candidates 按序取首个命中, 故首位放实测有数据的名字:
#   tool.name / tool.success ← 宿主 `agenthub.tool_name` 等点号键 (Phoenix 存成
#     嵌套 dict, provider 侧已还原为点号键; 实测 tool_call_count 因此从 0 变 2)
#   usage.* / model          ← OpenInference 名, 实测 199/199 条 LLM span 有值;
#     `agenthub.input_tokens` 一类虽有常量与写入代码, 实测 0/1000, 仅作兜底
#
# cache_read / reasoning 是 input / output 的**子集** (实测 total = prompt +
# completion 在 199/199 条上恒成立, 且 cache_read ≤ prompt、reasoning ≤
# completion)。Aeval 的成本折算按子集口径先从父桶扣出再各自计价, 所以映射上来
# 不会重复计费; 若哪天换成 input 不含缓存读的 provider, 改 PriceTable 的
# detail_semantics="disjoint", 而不是删这两条。
#
# 刻意不映射:
#   usage.total_tokens ← 挂在 agent.finalize span 上; 带 token 属性的 span 会被
#     归一化判成 LLM 调用, 白给 n_turns +1。n_total_tokens 本就是 in+out 的派生
#     别名, 不映射不丢数据。
#   tool.arguments     ← agenthub.args_summary 是宿主自算的摘要, 不是原始入参;
#     映过去会让参数正确性跑在未知口径上, 还与 Aeval 侧脱敏语义叠加两次。
#   error.type         ← agenthub.error 是错误消息原文, 不是错误分类。
TRACE_MAPPING_ENTRIES: dict[str, str | list[str]] = {
    "tool.name": AGENTHUB_TOOL_NAME,
    "tool.success": AGENTHUB_SUCCESS,
    "usage.input_tokens": ["llm.token_count.prompt", AGENTHUB_INPUT_TOKENS],
    "usage.output_tokens": ["llm.token_count.completion", AGENTHUB_OUTPUT_TOKENS],
    "usage.cache_read_tokens": ["llm.token_count.prompt_details.cache_read"],
    "usage.reasoning_tokens": ["llm.token_count.completion_details.reasoning"],
    "model": ["llm.model_name", AGENTHUB_MODEL],
    "session.id": AGENTHUB_CONVERSATION_ID,
    "span.role": "openinference.span.kind",
}

# 写进每个 run 的 mapping_version; 增删条目时递增, 用于跨 run 口径复核。
TRACE_MAPPING_VERSION = "agenthub-2"


def build_trace_mapping():
    """构造 AChat 的属性翻译表 (内置 OTel GenAI 条目 + 宿主条目)。"""
    from agent_eval.trace.mapping import default_mapping

    return default_mapping(
        extra_entries=TRACE_MAPPING_ENTRIES,
        version=TRACE_MAPPING_VERSION,
    )


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
    """构造 AChat 接入的完整 EvalRunner (agent_eval.core.runner.EvalRunner)。"""
    from agent_eval.core.runner import EvalRunner
    from agent_eval.storage.sqlite import SqliteStorage

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
    from agent_eval.metrics import build_default_metrics_registry

    llm_fn = make_judge_llm_fn(settings)
    metrics_registry = build_default_metrics_registry(llm_fn=llm_fn)

    return EvalRunner(
        agent_runner=agent_runner,
        trace_provider=_make_trace_provider(settings),
        trace_mapping=build_trace_mapping(),
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
    from agent_eval.trace.phoenix import PhoenixProvider

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
