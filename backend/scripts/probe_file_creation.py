"""手动复现 file-creation 超时 (诊断探针, 不属于验收链路).

铸 token → 建 sandbox 会话 → 发 file-creation 的 prompt →
每 5s 打印 run 状态 (DB + 消息状态), 观察卡在哪一步。
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

PROMPT = (
    "请在 workspace 中创建文件 hello.md,内容为一行:\n"
    '"Hello from Aeval!"。创建完成后简单说明你做了什么。'
)
AGENT_ID = ""  # 空 → 读 EVAL_AGENT_ID 设置


async def main() -> None:
    from app.config import get_settings
    from app.db.engine import init_db, close_db

    await init_db()
    try:
        settings = get_settings()
        agent_id = AGENT_ID or settings.eval_agent_id
        if not agent_id:
            print("[probe] AGENT_ID 为空且 EVAL_AGENT_ID 未配置")
            return

        # 1) token
        from sqlalchemy import select
        from app.auth.jwt_handler import create_access_token
        from app.db.engine import get_remote_db
        from app.db.models import User

        async with get_remote_db() as db:
            row = await db.execute(select(User).where(User.email == settings.default_user_email))
            user = row.scalar_one_or_none()
            if user is None:
                print(f"[probe] default user {settings.default_user_email} not found")
                return
            token = create_access_token(str(user.id), user.email, user.token_version)

        import httpx

        base = f"http://127.0.0.1:{settings.port}"
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=30) as client:
            # 2) 建会话 (与 eval client 相同参数)
            resp = await client.post(
                f"{base}/api/conversations",
                json={"title": "probe file-creation", "mode": "single",
                      "agentIds": [agent_id]},
                headers=headers,
            )
            resp.raise_for_status()
            conv = (resp.json() or {}).get("conversation") or {}
            conv_id = conv["id"]
            print(f"[probe] conversation={conv_id}")

            # 3) 发 prompt
            resp = await client.post(
                f"{base}/api/conversations/{conv_id}/messages",
                json={"content": PROMPT},
                headers=headers,
            )
            resp.raise_for_status()
            send = resp.json()
            run_ids = send.get("runIds") or send.get("run_ids") or []
            print(f"[probe] sent, run_ids={run_ids}")

            # 4) 轮询观察
            import sqlite3
            db_path = Path(settings.data_dir) / "agenthub.db"
            for i in range(90):  # 最多 ~7.5 分钟
                await asyncio.sleep(5)
                # DB 侧 run 状态
                status = err = None
                try:
                    con = sqlite3.connect(str(db_path))
                    row = con.execute(
                        "SELECT status, error FROM agent_runs WHERE id IN (%s)"
                        % ",".join("?" * len(run_ids)) if run_ids else "SELECT NULL, NULL"
                    , run_ids).fetchone() if run_ids else None
                    if row:
                        status, err = row
                    con.close()
                except Exception as e:
                    status = f"(db err: {e})"
                # 消息侧状态
                resp = await client.get(
                    f"{base}/api/conversations/{conv_id}/messages", headers=headers
                )
                msgs = (resp.json() or {}).get("messages") or []
                mstatus = [(m.get("runId"), m.get("status")) for m in msgs
                           if m.get("runId") in run_ids]
                n_parts = sum(len(m.get("parts") or []) for m in msgs)
                print(f"[{i*5:>3}s] db_status={status} db_err={(err or '')[:60]} "
                      f"msg_status={mstatus} parts={n_parts}", flush=True)
                if status in ("complete", "failed", "error", "aborted"):
                    print("[probe] terminal in DB")
                    break
            # 保留会话供检查, 打印 artifacts
            resp = await client.get(f"{base}/api/artifacts", headers=headers)
            arts = (resp.json() or {}).get("artifacts") or []
            mine = [a for a in arts if a.get("conversationId") == conv_id]
            print(f"[probe] artifacts in conv: {[(a.get('id'), a.get('name'), a.get('type')) for a in mine]}")
            print(f"[probe] conversation kept for inspection: {conv_id}")
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
