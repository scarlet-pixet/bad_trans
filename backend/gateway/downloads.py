"""Authenticated per-click binary downloads; credentials never enter the URL."""

import base64
from fastapi import Request
from fastapi.responses import Response, JSONResponse
from starlette.concurrency import run_in_threadpool

from backend.common.data import DomainError, uuid
from backend.infrastructure.clients import RemoteClient


async def download_report(request: Request, report_id: str):
    try:
        form = await request.form(max_files=0, max_fields=4, max_part_size=16384)
        authorization = request.headers.get("Authorization", "")
        token = authorization[7:] if authorization.lower().startswith("bearer ") else form.get("token", "")
        if not isinstance(token, str) or not token or len(token) > 8192:
            return JSONResponse({"code": "UNAUTHENTICATED", "message": "Требуется действующая сессия"}, status_code=401)
        verified = await run_in_threadpool(request.app.state.auth_api.validate, token)
        if verified["role"] not in ("MANAGER", "ANALYST"):
            raise DomainError("FORBIDDEN", "Нет доступа к отчётам")
        actor = {"id": verified["id"], "login": verified["login"], "role": verified["role"]}
        result = await run_in_threadpool(RemoteClient("case", actor)._invoke, "export_snapshot_report", dict(id=uuid(report_id), format=form.get("format"), table=form.get("table") or None))
        current = await run_in_threadpool(request.app.state.auth_api.validate, token)
        if current["id"] != actor["id"] or current["role"] != actor["role"]:
            raise DomainError("FORBIDDEN", "Права изменились во время подготовки файла")
        return Response(base64.b64decode(result["contentBase64"], validate=True), media_type=result["mediaType"], headers={
            "Content-Disposition": 'attachment; filename="' + result["fileName"] + '"',
            "Cache-Control": "no-store, private", "Pragma": "no-cache", "X-Content-Type-Options": "nosniff",
            "X-Snapshot-SHA256": result["snapshotHash"], "X-Report-Export-Version": result["exportVersion"],
        })
    except DomainError as exc:
        status = {"FORBIDDEN": 403, "NOT_FOUND": 404, "BAD_INPUT": 422, "DEPENDENCY_UNAVAILABLE": 503, "UNAUTHENTICATED": 401, "AUTH_ERROR": 401}.get(exc.code, 500)
        return JSONResponse({"code": exc.code, "message": str(exc)}, status_code=status, headers={"Cache-Control": "no-store"})
