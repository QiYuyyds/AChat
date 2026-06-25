"""Filesystem API routes (workspace + global directory listing).

Ports:
  - src/app/api/conversations/[id]/fs/read/route.ts
  - src/app/api/conversations/[id]/fs/write/route.ts
  - src/app/api/conversations/[id]/fs/listdir/route.ts
  - src/app/api/fs/listdir/route.ts  (global, for local-mode DirPicker)

Routes are thin: they call fs_service / workspace_utils and translate the
sandbox ValueError messages into the exact HTTP status codes the TS routes
returned (the React frontend depends on these byte-for-byte).
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.schemas import FsWriteRequest
from app.services import fs_service
from app.utils.platform import IS_WINDOWS
from app.utils.workspace_utils import is_path_safe

router = APIRouter()

DRIVES_SENTINEL = "__drives__"

# Windows known hidden / system directory names (case-insensitive). Mirrors the
# TS WINDOWS_HIDDEN_NAMES set; see specs/11-platform.md.
WINDOWS_HIDDEN_NAMES = {
    n.lower()
    for n in [
        "AppData",
        "$Recycle.Bin",
        "System Volume Information",
        "Recovery",
        "PerfLogs",
        "Config.Msi",
        "MSOCache",
        "OneDriveTemp",
        "ProgramData",
    ]
}


def _entry_to_dict(entry) -> dict:
    """ListEntry dataclass -> camelCase wire dict, omitting size when None."""
    d = {"name": entry.name, "isDirectory": entry.is_directory}
    if entry.size is not None:
        d["size"] = entry.size
    return d


@router.get("/conversations/{conversation_id}/fs/read")
async def read_file(conversation_id: str, request: Request) -> JSONResponse:
    target = request.query_params.get("path")
    if not target:
        return JSONResponse({"error": "path required"}, status_code=400)

    workspace = await fs_service.get_workspace_for_conversation(conversation_id)
    if not workspace:
        return JSONResponse({"error": "Workspace not found"}, status_code=404)

    try:
        result = fs_service.read_file_in_workspace(workspace, target)
    except ValueError as err:
        msg = str(err)
        status = (
            403
            if "outside" in msg
            else 413
            if "too large" in msg
            else 400
            if "Not a file" in msg
            else 500
        )
        return JSONResponse({"error": msg}, status_code=status)

    return JSONResponse(
        {
            "path": result.path,
            "absolutePath": result.absolute_path,
            "cwd": result.cwd,
            "size": result.size,
            "content": result.content,
            "truncated": result.truncated,
        }
    )


@router.post("/conversations/{conversation_id}/fs/write")
async def write_file(conversation_id: str, request: Request) -> JSONResponse:
    try:
        raw = await request.json()
    except Exception:
        raw = None
    try:
        body = FsWriteRequest.model_validate(raw)
    except ValidationError as err:
        return JSONResponse(
            {"error": "Invalid body", "issues": err.errors()}, status_code=400
        )

    workspace = await fs_service.get_workspace_for_conversation(conversation_id)
    if not workspace:
        return JSONResponse({"error": "Workspace not found"}, status_code=404)

    try:
        result = fs_service.write_file_in_workspace(workspace, body.path, body.content)
    except ValueError as err:
        msg = str(err)
        status = (
            403
            if "outside" in msg
            else 413
            if "too large" in msg or "quota" in msg
            else 500
        )
        return JSONResponse({"error": msg}, status_code=status)

    return JSONResponse(
        {
            "path": result.path,
            "absolutePath": result.absolute_path,
            "cwd": result.cwd,
            "bytes": result.bytes,
        }
    )


@router.get("/conversations/{conversation_id}/fs/listdir")
async def list_workspace_dir(conversation_id: str, request: Request) -> JSONResponse:
    target = request.query_params.get("path") or ""

    workspace = await fs_service.get_workspace_for_conversation(conversation_id)
    if not workspace:
        return JSONResponse({"error": "Workspace not found"}, status_code=404)

    try:
        result = fs_service.list_dir_in_workspace(workspace, target)
    except ValueError as err:
        msg = str(err)
        status = 403 if "outside" in msg else 400 if "Not a" in msg else 500
        return JSONResponse({"error": msg}, status_code=status)

    return JSONResponse(
        {
            "relPath": result.rel_path,
            "absolutePath": result.absolute_path,
            "parent": result.parent,
            "entries": [_entry_to_dict(e) for e in result.entries],
        }
    )


def _list_available_drives() -> list[str]:
    if not IS_WINDOWS:
        return ["/"]
    drives: list[str] = []
    for i in range(ord("A"), ord("Z") + 1):
        root = f"{chr(i)}:\\"
        if os.path.exists(root):
            drives.append(root)
    return drives


@router.get("/fs/listdir")
async def list_global_dir(request: Request) -> JSONResponse:
    """List **subdirectories** of an absolute path (DirPickerDialog).

    Port of src/app/api/fs/listdir/route.ts. Returns the `{ path, parent,
    entries }` wire shape (note: NOT the workspace listdir shape).
    """
    requested = request.query_params.get("path")
    target = (requested.strip() if requested else "") or os.path.expanduser("~")

    if target == DRIVES_SENTINEL:
        drives = _list_available_drives()
        return JSONResponse(
            {
                "path": DRIVES_SENTINEL,
                "parent": None,
                "entries": [
                    {
                        "name": d.rstrip("\\/") or d,
                        "isDirectory": True,
                        "path": d,
                    }
                    for d in drives
                ],
            }
        )

    if not os.path.isabs(target):
        return JSONResponse({"error": "path must be absolute"}, status_code=400)

    resolved = os.path.abspath(target)

    home_resolved = os.path.abspath(os.path.expanduser("~"))
    if resolved != home_resolved and not is_path_safe(resolved):
        return JSONResponse({"error": "Path not allowed"}, status_code=403)

    if not os.path.exists(resolved):
        return JSONResponse({"error": "Path does not exist"}, status_code=404)
    if not os.path.isdir(resolved):
        return JSONResponse({"error": "Not a directory"}, status_code=400)

    try:
        raw = list(os.scandir(resolved))
    except OSError as err:
        return JSONResponse(
            {"error": f"Cannot read directory: {err}"}, status_code=403
        )

    entries = [
        {"name": e.name, "isDirectory": True}
        for e in raw
        if not e.name.startswith(".")
        and (not IS_WINDOWS or e.name.lower() not in WINDOWS_HIDDEN_NAMES)
        and e.is_dir()
    ]
    entries.sort(key=lambda x: x["name"])

    p = os.path.dirname(resolved)
    parent = p if p != resolved else (DRIVES_SENTINEL if IS_WINDOWS else None)

    return JSONResponse({"path": resolved, "parent": parent, "entries": entries})
