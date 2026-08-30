"""create_aeval_runner() 装配测试 (任务 2.4)。

覆盖: 缺凭证报明确缺失项; 正常装配产物结构; token provider 显式凭证优先。
"""

from __future__ import annotations

import pytest
from agent_eval.core.runner import EvalRunner
from agent_eval.storage.sqlite import SqliteStorage

from app.eval_integration.client import AChatApiClient
from app.eval_integration.config import (
    check_credentials,
    create_aeval_runner,
    make_judge_llm_fn,
    make_token_provider,
    resolve_api_base,
    resolve_judge_config,
)
from app.eval_integration.environment import AChatWorkspaceEnvironment
from app.eval_integration.errors import EvalConfigError
from app.eval_integration.graders import AChatArtifactGrader, AChatDispatchGrader
from app.eval_integration.runner import AChatAgentRunner


@pytest.fixture
def eval_settings(monkeypatch, tmp_path):
    """隔离的评测装配 settings (临时 db 路径, 显式 agent/token)。"""
    monkeypatch.setenv("EVAL_AGENT_ID", "ag_eval_target")
    monkeypatch.setenv("EVAL_USER_TOKEN", "token-xyz")
    monkeypatch.setenv("EVAL_AEVAL_DB_PATH", str(tmp_path / "aeval.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))

    from app.config import get_settings

    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


def test_check_credentials_lists_missing_items(monkeypatch):
    # setenv("") 而非 delenv: pydantic-settings 还会读 .env 文件,
    # delenv 挡不住本机 .env 里的 EVAL_AGENT_ID 泄漏进测试。
    monkeypatch.setenv("EVAL_AGENT_ID", "")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        settings = get_settings()
        with pytest.raises(EvalConfigError) as exc_info:
            check_credentials(settings)
        assert "EVAL_AGENT_ID" in str(exc_info.value)
    finally:
        get_settings.cache_clear()


def test_resolve_api_base_default_and_override(monkeypatch):
    from app.config import get_settings

    monkeypatch.delenv("EVAL_API_BASE", raising=False)
    get_settings.cache_clear()
    try:
        assert resolve_api_base(get_settings()) == "http://127.0.0.1:8000"
    finally:
        get_settings.cache_clear()

    monkeypatch.setenv("EVAL_API_BASE", "http://backend:9000")
    get_settings.cache_clear()
    try:
        assert resolve_api_base(get_settings()) == "http://backend:9000"
    finally:
        get_settings.cache_clear()


async def test_token_provider_explicit_token_no_db(eval_settings):
    provider = make_token_provider(eval_settings)
    assert await provider() == "token-xyz"


async def test_create_aeval_runner_assembles_full_stack(eval_settings):
    runner = await create_aeval_runner(eval_settings)

    assert isinstance(runner, EvalRunner)
    assert isinstance(runner.agent_runner, AChatAgentRunner)
    assert runner.agent_runner.agent_id == "ag_eval_target"
    assert isinstance(runner.agent_runner.client, AChatApiClient)
    assert isinstance(runner.environment, AChatWorkspaceEnvironment)
    assert runner.environment.coordinator is runner.agent_runner.coordinator

    grader_names = {g.name for g in runner._graders.values()}
    assert {"achat_artifact", "achat_dispatch"} <= grader_names

    # sqlite 存储已初始化 (可写)
    assert isinstance(runner.storage, SqliteStorage)
    assert runner.storage._initialized is True

    # 并发默认 1 (§17.5 隔离正确性优先)
    assert runner.concurrency == 1

    await runner.agent_runner.client.aclose()


# ─── Judge LLM 装配 (change ③ 任务 6.1) ───────────────────────────────────────


def _judge_settings(**overrides):
    """与 .env 环境无关的 judge 配置载体 (resolve/make 只读这些字段)。"""
    from types import SimpleNamespace

    base = dict(
        aeval_judge_api_key=None,
        aeval_judge_api_url=None,
        aeval_judge_model=None,
        eval_llm_api_key=None,
        eval_llm_api_url=None,
        eval_llm_model=None,
        openai_api_key=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_resolve_judge_config_priority_chain():
    # 1) 无任何凭证 → None
    assert resolve_judge_config(_judge_settings()) is None

    # 2) 仅 openai_api_key → 兜底 (默认模型)
    cfg = resolve_judge_config(_judge_settings(openai_api_key="sk-openai"))
    assert cfg["api_key"] == "sk-openai"
    assert cfg["model"] == "gpt-4o-mini"
    assert cfg["base_url"] is None

    # 3) eval_llm_* 覆盖 openai 兜底
    cfg = resolve_judge_config(_judge_settings(
        openai_api_key="sk-openai",
        eval_llm_api_key="sk-eval", eval_llm_api_url="http://eval:8000/v1",
        eval_llm_model="eval-model",
    ))
    assert cfg["api_key"] == "sk-eval"
    assert cfg["base_url"] == "http://eval:8000/v1"
    assert cfg["model"] == "eval-model"

    # 4) AEVAL_JUDGE_* 最高优先
    cfg = resolve_judge_config(_judge_settings(
        openai_api_key="sk-openai",
        eval_llm_api_key="sk-eval", eval_llm_model="eval-model",
        aeval_judge_api_key="sk-judge", aeval_judge_api_url="http://judge:8000/v1",
        aeval_judge_model="judge-model",
    ))
    assert cfg == {
        "api_key": "sk-judge",
        "base_url": "http://judge:8000/v1",
        "model": "judge-model",
    }


def test_make_judge_llm_fn_none_when_unconfigured():
    assert make_judge_llm_fn(_judge_settings()) is None


def test_make_judge_llm_fn_returns_async_callable():
    import inspect

    fn = make_judge_llm_fn(_judge_settings(
        aeval_judge_api_key="sk-judge", aeval_judge_model="judge-model",
    ))
    assert fn is not None
    assert inspect.iscoroutinefunction(fn)


async def test_create_aeval_runner_injects_metrics_registry(eval_settings):
    runner = await create_aeval_runner(eval_settings)

    # P0 四指标默认注入 (llm_fn 是否可用取决于环境凭证, 不在此断言)
    assert set(runner.metrics_registry) == {
        "answer_relevancy", "faithfulness", "context_recall", "context_precision",
    }
    # metric 分发 grader 已拿到注册表引用
    metric_grader = runner._graders["metric"]
    assert metric_grader.metrics_registry is runner.metrics_registry

    await runner.agent_runner.client.aclose()
