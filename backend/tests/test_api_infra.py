"""Infra config API pytest — rag-infra-config 任务 2.4。

覆盖：
- 写门控：web 模式 PUT / POST test → 403 并附说明，配置不落库
- 脱敏往返：保存 → 读取掩码 → 掩码回写不改密码；密码明文只落库不出 API
- 连接测试：不落库、成功含时延、失败分类
- 状态接口：per-service status + configSource 标注正确
"""

import pytest

from app.infra import cache_helpers
from app.infra import factory as factory_mod
from app.services import global_settings_service as gss

PASSWORD_MASK = "********"


@pytest.fixture(autouse=True)
def _clear_settings_caches():
    cache_helpers._process_cache.pop("global_settings", None)
    gss._global_cache = None
    yield
    cache_helpers._process_cache.pop("global_settings", None)
    gss._global_cache = None


@pytest.fixture(autouse=True)
def _reset_infra_singleton(monkeypatch):
    monkeypatch.setattr(factory_mod, "_infra_instance", None)
    yield
    monkeypatch.setattr(factory_mod, "_infra_instance", None)


@pytest.fixture
def fake_clients(monkeypatch):
    """不触网的 client 构造替换（test / status 成功路径用）。"""
    class _FakeMilvus:
        instances: list = []

        def __init__(self, uri, timeout=15):
            self.uri = uri
            _FakeMilvus.instances.append(self)

        def list_collections(self):
            return []

        def close(self):
            pass

    class _FakeNeo4jDriver:
        instances: list = []

        def __init__(self, uri, auth):
            self.uri = uri
            self.auth = auth
            _FakeNeo4jDriver.instances.append(self)

        async def verify_connectivity(self):
            pass

        async def close(self):
            pass

    _FakeMilvus.instances = []
    _FakeNeo4jDriver.instances = []
    monkeypatch.setattr(factory_mod, "build_milvus_client", _FakeMilvus)
    monkeypatch.setattr(factory_mod, "build_neo4j_driver", lambda uri, u, p: _FakeNeo4jDriver(uri, (u, p)))
    monkeypatch.setattr(factory_mod, "_MILVUS_RETRY_WAITS", [0, 0, 0, 0])
    return {"milvus": _FakeMilvus, "neo4j": _FakeNeo4jDriver}


# ─── 写门控（web 模式 403） ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_web_mode_put_returns_403_and_does_not_persist(api_client):
    resp = await api_client.put(
        "/api/infra/config",
        json={"milvusHost": "should-not-persist"},
    )
    assert resp.status_code == 403
    body = resp.json()
    assert "桌面" in body["error"]

    gs = await gss.get_global_settings()
    assert gs.milvus_host is None  # 配置不落库


@pytest.mark.asyncio
async def test_web_mode_test_endpoint_returns_403(api_client):
    """连接测试同样桌面-only（任意 host 探测不能成为 web SSRF 原语）。"""
    resp = await api_client.post(
        "/api/infra/config/test",
        json={"milvusHost": "internal-host", "milvusPort": 19530},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_web_mode_read_status_allowed(api_client):
    """状态查询（读）web 模式可用。"""
    resp = await api_client.get("/api/infra/status")
    assert resp.status_code == 200
    assert "services" in resp.json()


@pytest.mark.asyncio
async def test_desktop_mode_put_allowed(desktop_client):
    resp = await desktop_client.put(
        "/api/infra/config",
        json={"milvusHost": "desktop-milvus", "milvusPort": 19530},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["restartRequired"] is True
    assert "重启" in body["message"]
    assert body["config"]["milvusHost"] == "desktop-milvus"


# ─── 脱敏往返 ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_password_masked_on_read_and_plain_in_db(desktop_client):
    """保存 → 读取返回掩码；DB 中为明文（脱敏只在 API 层）。"""
    put = await desktop_client.put(
        "/api/infra/config",
        json={"neo4jUri": "bolt://n:7687", "neo4jUser": "neo4j", "neo4jPassword": "s3cret"},
    )
    assert put.status_code == 200

    got = (await desktop_client.get("/api/infra/config")).json()
    assert got["config"]["neo4jPassword"] == PASSWORD_MASK
    assert got["desktopMode"] is True

    gs = await gss.get_global_settings()
    assert gs.neo4j_password == "s3cret"  # 明文落库（沿用 API key 决策）
    assert gs.neo4j_user == "neo4j"


@pytest.mark.asyncio
async def test_mask_echo_and_empty_keep_stored_password(desktop_client):
    """脱敏往返：掩码回写或空串 = 未修改，密码不被清掉。"""
    await desktop_client.put(
        "/api/infra/config",
        json={"neo4jPassword": "s3cret"},
    )

    for untouched in (PASSWORD_MASK, ""):
        resp = await desktop_client.put(
            "/api/infra/config",
            json={"neo4jPassword": untouched},
        )
        assert resp.status_code == 200
        gs = await gss.get_global_settings()
        assert gs.neo4j_password == "s3cret", f"password clobbered by {untouched!r}"

    # 新密码正常覆盖
    resp = await desktop_client.put(
        "/api/infra/config",
        json={"neo4jPassword": "new-pass"},
    )
    assert resp.status_code == 200
    gs = await gss.get_global_settings()
    assert gs.neo4j_password == "new-pass"


@pytest.mark.asyncio
async def test_absent_fields_left_untouched(desktop_client):
    """PATCH 语义：未提交的字段保持不变。"""
    await desktop_client.put(
        "/api/infra/config",
        json={"milvusHost": "m1", "enableGraph": True},
    )
    await desktop_client.put("/api/infra/config", json={"milvusHost": "m2"})

    gs = await gss.get_global_settings()
    assert gs.milvus_host == "m2"
    assert gs.enable_graph is True  # 未提交 → 不变


@pytest.mark.asyncio
async def test_empty_string_clears_non_secret_fields(desktop_client):
    """非凭证字段：空串/None = 清除（回落 env）。"""
    await desktop_client.put("/api/infra/config", json={"milvusHost": "m1"})
    await desktop_client.put("/api/infra/config", json={"milvusHost": None})

    gs = await gss.get_global_settings()
    assert gs.milvus_host is None


@pytest.mark.asyncio
async def test_invalid_port_rejected(desktop_client):
    resp = await desktop_client.put(
        "/api/infra/config",
        json={"milvusPort": 99999},
    )
    assert resp.status_code == 400


# ─── 连接测试 ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_connection_test_success_does_not_persist(desktop_client, fake_clients):
    """测试连接：成功含时延；全程不写库。"""
    resp = await desktop_client.post(
        "/api/infra/config/test",
        json={"milvusHost": "fake-host", "milvusPort": 19530, "neo4jUri": "bolt://fake:7687"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["milvus"]["tested"] is True
    assert body["milvus"]["ok"] is True
    assert body["milvus"]["latencyMs"] >= 0
    assert body["neo4j"]["ok"] is True

    gs = await gss.get_global_settings()
    assert gs.milvus_host is None and gs.neo4j_uri is None  # 不落库


@pytest.mark.asyncio
async def test_connection_test_failure_classified(desktop_client):
    """不可达端点 → 失败 + 网络类原因；不落库。"""
    resp = await desktop_client.post(
        "/api/infra/config/test",
        json={"milvusHost": "127.0.0.1", "milvusPort": 1},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["milvus"]["ok"] is False
    assert body["milvus"]["errorKind"] in ("network", "protocol", "auth")

    gs = await gss.get_global_settings()
    assert gs.milvus_host is None


@pytest.mark.asyncio
async def test_connection_test_mask_echo_uses_stored_password(desktop_client, fake_clients):
    """掩码回显 → 测试端点用存储密码（而非掩码本身）发真实连接。"""
    await desktop_client.put(
        "/api/infra/config",
        json={"neo4jUri": "bolt://n:7687", "neo4jUser": "neo4j", "neo4jPassword": "stored-pass"},
    )

    resp = await desktop_client.post(
        "/api/infra/config/test",
        json={"neo4jUri": "bolt://n:7687", "neo4jUser": "neo4j", "neo4jPassword": PASSWORD_MASK},
    )
    assert resp.status_code == 200
    assert resp.json()["neo4j"]["ok"] is True

    drivers = fake_clients["neo4j"].instances
    assert len(drivers) == 1
    assert drivers[0].auth == ("neo4j", "stored-pass")


@pytest.mark.asyncio
async def test_connection_test_skips_unprovided_services(desktop_client, fake_clients):
    resp = await desktop_client.post("/api/infra/config/test", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["milvus"] == {"tested": False}
    assert body["neo4j"] == {"tested": False}


# ─── 状态接口 ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_reflects_startup_snapshot_with_sources(desktop_client, fake_clients):
    """状态接口：落库 Milvus → connected/db；未配置 Neo4j → disabled/none。"""
    await gss.update_global_settings({"milvus_host": "db-host"})
    from app.config import Settings as _S
    dbg = _S(_env_file=None)
    print("\nDBG settings.neo4j_uri:", repr(dbg.neo4j_uri), "enable_graph:", dbg.enable_graph, "fields:", sorted(dbg.model_fields_set))
    dbg_gs = await gss.get_global_settings()
    print("DBG gs:", repr(dbg_gs.neo4j_uri), repr(dbg_gs.enable_graph))
    cfg = await factory_mod.resolve_infra_config(dbg)
    print("DBG cfg:", cfg.neo4j_uri, cfg.enable_graph, cfg.neo4j_source, cfg.graph_source)
    infra = await factory_mod.build_infrastructure(
        __import__("app.config", fromlist=["Settings"]).Settings(_env_file=None)
    )
    assert infra is not None

    resp = await desktop_client.get("/api/infra/status")
    assert resp.status_code == 200
    services = resp.json()["services"]

    assert services["milvus"]["status"] == "connected"
    assert services["milvus"]["configSource"] == "db"
    assert services["milvus"]["detail"] is None

    assert services["neo4j"]["status"] == "disabled"
    assert services["neo4j"]["configSource"] == "none"

    # redis 已移除
    assert services["redis"]["status"] == "disabled"


@pytest.mark.asyncio
async def test_status_labels_env_source(desktop_client, fake_clients):
    """env 配置（未落库）→ configSource env。"""
    from app.config import Settings

    await factory_mod.build_infrastructure(Settings(_env_file=None, milvus_host="env-host"))

    resp = await desktop_client.get("/api/infra/status")
    services = resp.json()["services"]
    assert services["milvus"]["status"] == "connected"
    assert services["milvus"]["configSource"] == "env"


@pytest.mark.asyncio
async def test_status_when_infra_never_built(desktop_client):
    """工厂未装配（极端场景）→ infraAvailable False + 中性状态。"""
    resp = await desktop_client.get("/api/infra/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["infraAvailable"] is False
    assert body["services"]["milvus"]["status"] in ("disabled", "degraded")


@pytest.mark.asyncio
async def test_startup_dashboard_lines_format_preserved(desktop_client, fake_clients):
    """启动日志面板行为不变：dashboard_lines 输出格式与既有实现一致。"""
    infra = await factory_mod.build_infrastructure(
        __import__("app.config", fromlist=["Settings"]).Settings(_env_file=None)
    )
    lines = infra.status.dashboard_lines()
    assert lines[0] == "=== Infrastructure Dashboard ==="
    assert lines[-1] == "=============================="
    assert any("milvus: " in line for line in lines)
    assert len(lines) == 7  # header + 5 services + footer
