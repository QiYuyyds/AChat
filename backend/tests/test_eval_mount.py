"""AChat-side mount regression for the eval sub-app (design doc §10.1).

Covers: the /api/eval mount coexisting with the AChat judge routes (first
registration wins), and the mount being absent when EVAL_HARNESS_ENABLED is
false. The framework itself lives in the agent-eval package (aeval/); this
file only asserts the host-side wiring.
"""

import httpx
import pytest


class TestJudgeCoexistence:
    async def test_judge_route_and_eval_subapp_coexist(self, monkeypatch):
        monkeypatch.setenv("EVAL_HARNESS_ENABLED", "true")
        from app.config import get_settings

        get_settings.cache_clear()
        try:
            from app.main import create_app as create_main_app

            app = create_main_app()

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as c:
                # eval 子应用路由可达
                health = await c.get("/api/eval/health")
                assert health.status_code == 200

                graders = await c.get("/api/eval/graders")
                assert graders.status_code == 200

                # judge 路由仍可达 (先注册先匹配; 默认关闭 → 403)
                judge = await c.post("/api/eval/judge/some_trace")
                assert judge.status_code == 403
        finally:
            get_settings.cache_clear()

    async def test_mount_absent_when_disabled(self, monkeypatch):
        monkeypatch.setenv("EVAL_HARNESS_ENABLED", "false")
        from app.config import get_settings

        get_settings.cache_clear()
        try:
            from app.main import create_app as create_main_app

            app = create_main_app()
            mount_paths = [
                r.path for r in app.routes if hasattr(r, "routes")
            ]
            assert "/api/eval" not in mount_paths
        finally:
            get_settings.cache_clear()
