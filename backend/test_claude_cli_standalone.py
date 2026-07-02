"""Standalone test: spawn Claude Code CLI, send prompt, read stream-json output.

This is independent of the AChat app — it directly tests whether the
spawn + stream-json communication works in this environment.

Mirrors multica's server/pkg/agent/claude.go Execute() flow.
"""
import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
import time


def resolve_windows_exe(exec_path: str) -> str:
    """Same logic as cli_base._resolve_windows_exe."""
    if not os.sep in exec_path and os.altsep not in exec_path:
        resolved = shutil.which(exec_path)
        if resolved and os.path.isfile(resolved):
            exec_path = resolved

    if not exec_path.lower().endswith(".cmd"):
        return exec_path

    cmd_dir = os.path.dirname(exec_path)
    try:
        with open(exec_path, encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return exec_path

    m = re.search(r'"([^"]+\.exe)"', content)
    if not m:
        return exec_path

    exe_rel = m.group(1)
    exe_rel = exe_rel.replace("%dp0%", cmd_dir + os.sep)
    exe_abs = os.path.normpath(os.path.join(cmd_dir, exe_rel))
    if os.path.isfile(exe_abs):
        return exe_abs
    return exec_path


def write_temp_system_prompt(text: str) -> str:
    """Same as claude_adapter._write_temp_system_prompt."""
    fd, path = tempfile.mkstemp(prefix="test_sp_", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return path


async def main():
    # 1. Resolve executable
    exec_path = resolve_windows_exe("claude")
    print(f"[1] Resolved executable: {exec_path}")
    print(f"    Is file: {os.path.isfile(exec_path)}")

    # 2. Write system prompt to temp file (Windows-safe)
    system_prompt = "You are a helpful assistant. Keep answers very short — one sentence max."
    sp_file = write_temp_system_prompt(system_prompt)
    print(f"[2] System prompt file: {sp_file}")

    # 3. Build args (mirrors claude_adapter._build_args + multica buildClaudeArgs)
    args = [
        exec_path,
        "-p",
        "--output-format", "stream-json",
        "--input-format", "stream-json",
        "--verbose",
        "--strict-mcp-config",
        "--permission-mode", "bypassPermissions",
        "--disallowedTools", "AskUserQuestion",
        "--include-partial-messages",
        "--model", "claude-opus-4-8",
        "--append-system-prompt-file", sp_file,
    ]
    print(f"[3] Args: {' '.join(args)}")

    # 4. Spawn process
    creationflags = 0x08000000 if sys.platform == "win32" else 0
    print(f"[4] Spawning...")
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=creationflags if creationflags else None,
    )
    print(f"    PID: {proc.pid}")

    # 5. Write prompt (mirrors claude_adapter._write_prompt + multica buildClaudeInput)
    prompt_payload = json.dumps({
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": "Write a short paragraph (3-4 sentences) explaining what Python asyncio is and why it's useful."}],
        },
    })
    print(f"[5] Writing prompt: {prompt_payload[:80]}...")
    proc.stdin.write((prompt_payload + "\n").encode())
    await proc.stdin.drain()
    print(f"    Prompt written, stdin kept open for control_response")

    # 6. Read stdout line by line (mirrors claude_adapter._read_events + multica scanner)
    print(f"[6] Reading stdout...")
    t_spawn = time.monotonic()
    line_count = 0
    stderr_chunks = []
    first_line_t = None

    # Start stderr reader
    async def read_stderr():
        if proc.stderr:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    stderr_chunks.append(text)

    stderr_task = asyncio.create_task(read_stderr())

    try:
        while True:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=120.0)
            if not line:
                t_total = time.monotonic() - t_spawn
                print(f"    EOF after {line_count} lines (t={t_total:.3f}s)")
                break
            decoded = line.decode("utf-8", errors="replace").strip()
            if not decoded:
                continue
            t_line = time.monotonic() - t_spawn
            if first_line_t is None:
                first_line_t = t_line
                print(f"    *** FIRST LINE at t={t_line:.3f}s ***")
            line_count += 1
            try:
                obj = json.loads(decoded)
                msg_type = obj.get("type", "?")
                # Print summary, not full content
                summary = f"type={msg_type}"
                if msg_type == "assistant":
                    content = obj.get("message", {}).get("content", [])
                    for block in content:
                        btype = block.get("type", "?")
                        if btype == "text":
                            text = block.get("text", "")[:100]
                            summary += f" text='{text}'"
                        elif btype == "thinking":
                            summary += f" thinking={len(block.get('text',''))}chars"
                        elif btype == "tool_use":
                            summary += f" tool={block.get('name','?')}"
                elif msg_type == "result":
                    summary += f" result='{obj.get('result','')[:100]}' is_error={obj.get('is_error')}"
                    usage = obj.get("usage", {})
                    if usage:
                        summary += f" input_tokens={usage.get('input_tokens')} output_tokens={usage.get('output_tokens')}"
                elif msg_type == "stream_event":
                    event = obj.get("event", {})
                    if event:
                        # The 'event' field contains the raw streaming event from the API
                        etype = event.get("type", "?")
                        if etype == "content_block_delta":
                            delta = event.get("delta", {})
                            dtype = delta.get("type", "?")
                            if dtype == "text_delta":
                                summary += f" text_delta='{delta.get('text','')[:80]}'"
                            elif dtype == "thinking_delta":
                                summary += f" thinking_delta='{delta.get('thinking','')[:80]}'"
                            else:
                                summary += f" delta_type={dtype}"
                        elif etype == "content_block_start":
                            block = event.get("content_block", {})
                            summary += f" block_start={block.get('type','?')}"
                        elif etype == "content_block_stop":
                            summary += f" block_stop"
                        elif etype == "message_start":
                            summary += f" message_start model={event.get('message',{}).get('model','?')}"
                        elif etype == "message_delta":
                            delta = event.get("delta", {})
                            summary += f" stop_reason={delta.get('stop_reason','?')}"
                        elif etype == "message_stop":
                            summary += f" message_stop"
                        elif etype == "ping":
                            summary += f" ping"
                        else:
                            summary += f" event_type={etype}"
                    else:
                        summary += f" (no event field) keys={list(obj.keys())}"
                elif msg_type == "system":
                    summary += f" subtype={obj.get('subtype','?')}"
                elif msg_type == "user":
                    content = obj.get("message", {}).get("content", [])
                    for block in content:
                        if block.get("type") == "tool_result":
                            summary += f" tool_result call_id={block.get('tool_use_id','?')[:20]}"
                print(f"    [{line_count}] t={t_line:.3f}s {summary}")
            except json.JSONDecodeError:
                print(f"    [{line_count}] RAW: {decoded[:120]}")

    except asyncio.TimeoutError:
        print(f"    TIMEOUT after 120s, {line_count} lines read")

    # 7. Wait for process
    print(f"[7] Waiting for process exit...")
    try:
        await asyncio.wait_for(proc.wait(), timeout=10.0)
    except asyncio.TimeoutError:
        print(f"    Process didn't exit, terminating...")
        proc.terminate()
        await proc.wait()

    exit_code = proc.returncode
    print(f"    Exit code: {exit_code}")

    # 8. Stderr
    stderr_task.cancel()
    try:
        await stderr_task
    except asyncio.CancelledError:
        pass

    if stderr_chunks:
        print(f"[8] Stderr tail (last 20 lines):")
        for line in stderr_chunks[-20:]:
            print(f"    STDERR: {line[:200]}")

    # 9. Cleanup
    os.remove(sp_file)
    print(f"[9] Cleaned up temp file")

    # 10. Summary
    print(f"\n{'='*60}")
    t_total = time.monotonic() - t_spawn
    print(f"RESULT: exit_code={exit_code}, lines={line_count}, total_time={t_total:.3f}s")
    if first_line_t is not None:
        print(f"TIMING: first_line={first_line_t:.3f}s, total={t_total:.3f}s, gap={t_total - first_line_t:.3f}s")
        if first_line_t > 5.0:
            print("WARNING: First line took > 5s — likely stdout buffering in the CLI subprocess!")
        elif t_total - first_line_t < 0.1 and line_count > 1:
            print("WARNING: All lines arrived almost simultaneously — confirms buffering issue!")
    if line_count == 0:
        print("ISSUE: No output from Claude Code CLI!")
        print("This confirms the communication problem.")
    else:
        print("SUCCESS: Communication with Claude Code CLI works!")
    print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
