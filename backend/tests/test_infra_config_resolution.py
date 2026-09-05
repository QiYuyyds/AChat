"""两级解析 pytest — rag-infra-config 任务 1.3。

覆盖：
- 落库值覆盖 env（global_settings → env 优先级）
- 缺省回落 env（未落库字段沿用 env 行为）
- 均未配置走既有降级（不建连、不崩溃、状态 disabled）
- enable_graph 开关两级语义（落库值优先；未落库回落 env；均无默认关）
- build_infrastructure 集成：连接参数来源改变但独立降级行为不变
"""

import pytest

from app.config import Settings
from app.infra import factory as factory_mod
from app.infra.factory import build_infrastructure, resolve_infra_config

# 消除全局设置进程内缓存（TTL 5min）的跨测试污染
from app.infra import cache_helpers
from app.services import global_settings_service as gss


@pytest.fixture(autouse=True)
def _clear_settings_caches():
    for mod_cache in (cache_helpers._process_cache,):
        mod_cache.pop("global_settings", None)
    gss._global_cache = None
    yield
    cache_helpers._process_cache.pop("global_settings", None)
    gss._global_cache = None


@pytest.fixture(autouse=True)
def _reset_infra_singleton(monkeypatch):
    monkeypatch.setattr(factory_mod, "_infra_instance", None)
    yield
    monkeypatch.setattr(factory_mod, "_infra_instance", None)


class _FakeMilvus:
    instances: list["_FakeMilvus"] = []
    fail_default: bool = False

    def __init__(self, uri: str, timeout: float = 15):
        self.uri = uri
        self.fail = _FakeMilvus.fail_default
        _FakeMilvus.instances.append(self)

    def list_collections(self):
        if self.fail:
            raise ConnectionError("connection refused")
        return []

    def close(self):
        pass


class _FakeNeo4jDriver:
    instances: list["_FakeNeo4jDriver"] = []

    def __init__(self, uri: str, auth: tuple[str, str]):
        self.uri = uri
        self.auth = auth
        self.fail = False
        _FakeNeo4jDriver.instances.append(self)

    async def verify_connectivity(self):
        if self.fail:
            raise ConnectionError("unreachable")

    async def close(self):
        pass


@pytest.fixture
def fake_clients(monkeypatch):
    """替换工厂的 client 构造函数（不触网），并清零重试等待。"""
    _FakeMilvus.instances = []
    _FakeNeo4jDriver.instances = []
    _FakeMilvus.fail_default = False
    monkeypatch.setattr(
        factory_mod, "build_milvus_client",
        lambda uri, timeout=15: _FakeMilvus(uri, timeout),
    )
    monkeypatch.setattr(
        factory_mod, "build_neo4j_driver",
        lambda uri, user, password: _FakeNeo4jDriver(uri, (user, password)),
    )
    monkeypatch.setattr(factory_mod, "_MILVUS_RETRY_WAITS", [0, 0, 0, 0])


# ─── resolve_infra_config 单元 ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_db_overrides_env(db):
    """落库值覆盖 env：两级都配置时用落库值，来源 db。"""
    await gss.update_global_settings({"milvus_host": "db-host", "milvus_port": 21000})
    settings = Settings(_env_file=None, milvus_host="env-host", milvus_port=19530)

    cfg = await resolve_infra_config(settings)

    assert cfg.milvus_host == "db-host"
    assert cfg.milvus_port == 21000
    assert cfg.milvus_source == "db"


@pytest.mark.asyncio
async def test_missing_db_value_falls_back_to_env(db):
    """缺省回落 env：global_settings 未配置 Neo4j 时沿用 env。"""
    settings = Settings(
        _env_file=None,
        neo4j_uri="bolt://env-neo:7687",
        neo4j_user="env-user",
        neo4j_password="env-pass",
        enable_graph=True,
    )

    cfg = await resolve_infra_config(settings)

    assert cfg.neo4j_uri == "bolt://env-neo:7687"
    assert cfg.neo4j_user == "env-user"
    assert cfg.neo4j_password == "env-pass"
    assert cfg.neo4j_source == "env"
    assert cfg.enable_graph is True
    assert cfg.graph_source == "env"


@pytest.mark.asyncio
async def test_neither_configured_is_unconfigured(db):
    """均未配置：视为未配置（空值 + source none），不指向任何端点。"""
    cfg = await resolve_infra_config(Settings(_env_file=None))

    assert cfg.milvus_host == ""
    assert cfg.milvus_source == "none"
    assert cfg.neo4j_uri == ""
    assert cfg.neo4j_source == "none"
    assert cfg.enable_graph is False
    assert cfg.graph_source == "none"


@pytest.mark.asyncio
async def test_enable_graph_db_value_wins_over_env(db):
    """enable_graph 两级语义：落库值优先于 env。"""
    await gss.update_global_settings({"enable_graph": True})
    settings = Settings(_env_file=None, enable_graph=False)

    cfg = await resolve_infra_config(settings)

    assert cfg.enable_graph is True
    assert cfg.graph_source == "db"


@pytest.mark.asyncio
async def test_enable_graph_false_db_value_disables_env(db):
    """enable_graph 落库 False 显式关闭 env True（不是「未设置」语义）。"""
    await gss.update_global_settings({"enable_graph": False})
    settings = Settings(_env_file=None, enable_graph=True)

    cfg = await resolve_infra_config(settings)

    assert cfg.enable_graph is False
    assert cfg.graph_source == "db"


@pytest.mark.asyncio
async def test_db_read_failure_falls_back_to_env_only(monkeypatch):
    """global_settings 读取失败（DB 不可用）→ env-only，装配不崩。"""
    async def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(gss, "get_global_settings", _boom)
    settings = Settings(_env_file=None, milvus_host="env-host")

    cfg = await resolve_infra_config(settings)

    assert cfg.milvus_host == "env-host"
    assert cfg.milvus_source == "env"


# ─── build_infrastructure 集成（fake clients，不触网） ─────────────────


@pytest.mark.asyncio
async def test_build_uses_db_config_for_milvus(db, fake_clients):
    """工厂用落库值建连（env 同时配置时），状态来源标注 db。"""
    await gss.update_global_settings({"milvus_host": "db-host", "milvus_port": 21000})
    settings = Settings(_env_file=None, milvus_host="env-host")

    infra = await build_infrastructure(settings)

    assert len(_FakeMilvus.instances) == 1
    assert _FakeMilvus.instances[0].uri == "http://db-host:21000"
    assert infra.milvus_client is not None
    assert infra.status.milvus == "connected"
    assert infra.status.milvus_snap.config_source == "db"


@pytest.mark.asyncio
async def test_build_falls_back_to_env_for_neo4j(db, fake_clients):
    """未落库时工厂按既有 env 行为连接 Neo4j，状态来源标注 env。"""
    settings = Settings(
        _env_file=None,
        neo4j_uri="bolt://env-neo:7687",
        enable_graph=True,
    )

    infra = await build_infrastructure(settings)

    assert len(_FakeNeo4jDriver.instances) == 1
    assert _FakeNeo4jDriver.instances[0].uri == "bolt://env-neo:7687"
    assert infra.neo4j_driver is not None
    assert infra.status.neo4j == "connected"
    assert infra.status.neo4j_snap.config_source == "env"


@pytest.mark.asyncio
async def test_build_neither_configured_keeps_degradation(db, fake_clients):
    """均未配置：不建连，状态 disabled，启动不崩（既有降级路径）。"""
    infra = await build_infrastructure(Settings(_env_file=None))

    assert infra.milvus_client is None
    assert infra.neo4j_driver is None
    assert infra.status.milvus_snap.status == "disabled"
    assert infra.status.milvus_snap.config_source == "none"
    assert infra.status.neo4j_snap.status == "disabled"


@pytest.mark.asyncio
async def test_build_bad_milvus_degrades_independently(db, fake_clients):
    """错误配置 → 该服务独立降级，其余服务不受影响，后端不崩。"""
    _FakeMilvus.fail_default = True
    await gss.update_global_settings({
        "milvus_host": "broken-host",
        "neo4j_uri": "bolt://ok-neo:7687",
        "enable_graph": True,
    })

    infra = await build_infrastructure(Settings(_env_file=None))

    assert infra.milvus_client is None
    assert infra.status.milvus_snap.status == "degraded"
    assert infra.status.milvus_snap.detail  # 失败原因可见
    # 独立降级：Neo4j 不受 Milvus 失败影响
    assert infra.neo4j_driver is not None
    assert infra.status.neo4j_snap.status == "connected"


@pytest.mark.asyncio
async def test_build_graph_switch_off_disables_neo4j(db, fake_clients):
    """enable_graph 落库 False 优先于 env True：Neo4j 不建连，状态可见原因。"""
    await gss.update_global_settings({"enable_graph": False})
    settings = Settings(_env_file=None, neo4j_uri="bolt://env-neo:7687", enable_graph=True)

    infra = await build_infrastructure(settings)

    assert len(_FakeNeo4jDriver.instances) == 0
    assert infra.neo4j_driver is None
    assert infra.status.neo4j_snap.status == "disabled"
    # URI 未落库 → 来源仍是 env；开关来自 db 的显式 False
    assert infra.status.neo4j_snap.config_source == "env"
    assert "enable_graph" in (infra.status.neo4j_snap.detail or "")
