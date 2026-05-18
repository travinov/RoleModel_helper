from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.agent.service import ChatAgent
from app.models.api import (
    AliasUpsertRequest,
    RagIngestRequest,
    RagSourceCreateRequest,
    SessionFeedbackPayload,
    SessionFeedbackRequest,
    SessionCreateResponse,
    SessionStateResponse,
    UserMessageRequest,
)
from app.repositories.search_repository import SearchRepository
from app.rag.service import RagService
from rolemodel_etl.loader import init_db, load_to_db
from rolemodel_etl.parser import parse_workbook

from ..config import AppConfig


def _report_from_parse(parsed) -> dict:
    return {
        "source_file": parsed.source_file,
        "sheet_name": parsed.sheet_name,
        "model_code": parsed.model_code,
        "model_name": parsed.model_name,
        "rows_read": parsed.rows_read,
        "profiles": len(parsed.profiles),
        "systems": len(parsed.systems),
        "entitlements": len(parsed.entitlements),
        "accesses": parsed.access_count,
        "justifications": parsed.justifications_count,
        "errors_count": parsed.errors_count,
        "warnings_count": parsed.warnings_count,
        "issues": [
            {
                "severity": issue.severity,
                "stage": issue.stage,
                "sheet_name": issue.sheet_name,
                "source_row": issue.source_row,
                "source_col": issue.source_col,
                "error_code": issue.error_code,
                "message": issue.message,
                "raw_value": issue.raw_value,
            }
            for issue in parsed.issues
        ],
    }


def _safe_upload_name(raw_name: str) -> str:
    name = Path(raw_name or "rolemodel_upload.xlsx").name
    stem = re.sub(r"[^a-zA-Zа-яА-Я0-9._ -]+", "_", Path(name).stem).strip(" ._")
    suffix = Path(name).suffix.lower() or ".xlsx"
    if suffix != ".xlsx":
        raise HTTPException(status_code=400, detail="Можно загрузить только файл .xlsx")
    return f"{stem or 'rolemodel_upload'}{suffix}"


def _backup_database(config: AppConfig) -> str:
    pg_dump = shutil.which("pg_dump")
    if not pg_dump:
        raise HTTPException(status_code=500, detail="На сервере не найден pg_dump для резервного копирования БД")
    backup_dir = Path(os.getenv("RM_DB_BACKUP_DIR", str(Path.home() / "rolemodel_backups")))
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"pre_upload_rolemodel_db_{timestamp}.dump"
    env = os.environ.copy()
    env["PGPASSWORD"] = config.db.password
    cmd = [
        pg_dump,
        "-h",
        config.db.host,
        "-p",
        str(config.db.port),
        "-U",
        config.db.user,
        "-Fc",
        "-f",
        str(backup_path),
        config.db.dbname,
    ]
    completed = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=120)
    if completed.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"Не удалось создать backup БД: {(completed.stderr or completed.stdout).strip()[:500]}",
        )
    backup_path.chmod(0o600)
    return str(backup_path)


def build_app(config: AppConfig | None = None) -> FastAPI:
    app_config = config or AppConfig.from_env()
    init_db(app_config.db)
    app = FastAPI(title="RoleModel Chat Agent", version="0.1.0")

    agent = ChatAgent(app_config)
    rag_service = RagService(app_config)
    search_repository = SearchRepository(app_config)

    @app.get("/api/v1/health")
    def health() -> dict:
        return {
            "status": "ok",
            "gigachat_enabled": app_config.gigachat_enabled,
            "gigachat_intent": app_config.gigachat_use_for_intent,
            "gigachat_chunking": app_config.gigachat_use_for_chunking,
            "gigachat_rag_answer": app_config.gigachat_use_for_rag_answer,
        }

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        html_path = Path(__file__).resolve().parents[1] / "ui" / "index.html"
        return html_path.read_text(encoding="utf-8")

    @app.post("/api/v1/chat/sessions", response_model=SessionCreateResponse)
    def create_session() -> SessionCreateResponse:
        session_id, greeting = agent.start_session()
        return SessionCreateResponse(session_id=session_id, assistant_text=greeting)

    @app.get("/api/v1/chat/sessions/{session_id}", response_model=SessionStateResponse)
    def get_session(session_id: str) -> SessionStateResponse:
        try:
            state = agent.get_session_state(session_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return SessionStateResponse(**state)

    @app.post("/api/v1/chat/sessions/{session_id}/messages")
    def post_message(session_id: str, request: UserMessageRequest):
        try:
            return agent.handle_message(session_id, request.text)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/chat/sessions/{session_id}/feedback", response_model=SessionFeedbackPayload)
    def post_session_feedback(session_id: str, request: SessionFeedbackRequest) -> SessionFeedbackPayload:
        session = search_repository.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail=f"Session {session_id} was not found")
        feedback = search_repository.upsert_session_feedback(
            session_id=session_id,
            rating=request.rating,
            comment=request.comment,
        )
        return SessionFeedbackPayload(
            rating=str(feedback["rating"]),
            comment=feedback.get("comment"),
        )

    @app.post("/api/v1/admin/systems/aliases")
    def upsert_alias(request: AliasUpsertRequest) -> dict:
        search_repository.upsert_alias(request.system_id, request.alias_text, request.alias_source)
        return {"status": "ok"}

    @app.post("/api/v1/admin/rag/sources")
    def create_rag_source(request: RagSourceCreateRequest) -> dict:
        return rag_service.register_source(
            title=request.title,
            file_path=request.file_path,
            source_type=request.source_type,
            system_id=request.system_id,
        )

    @app.post("/api/v1/admin/rag/ingest")
    def ingest_rag(request: RagIngestRequest) -> dict:
        return rag_service.ingest_source(
            source_id=request.source_id,
            file_path=request.file_path,
            title=request.title,
            source_type=request.source_type,
            system_id=request.system_id,
        )

    @app.get("/api/v1/admin/rag/sources/{source_id}")
    def inspect_rag_source(source_id: int) -> dict:
        source = rag_service.inspect_source(source_id)
        if not source:
            raise HTTPException(status_code=404, detail=f"RAG source {source_id} was not found")
        return source

    @app.post("/api/v1/admin/rolemodel/upload")
    async def upload_rolemodel_file(request: Request) -> dict:
        raw_name = unquote(request.headers.get("x-file-name") or "rolemodel_upload.xlsx")
        safe_name = _safe_upload_name(raw_name)
        body = await request.body()
        if not body:
            raise HTTPException(status_code=400, detail="Файл пустой")
        max_size = int(os.getenv("RM_ROLEMODEL_UPLOAD_MAX_BYTES", str(25 * 1024 * 1024)))
        if len(body) > max_size:
            raise HTTPException(status_code=413, detail=f"Файл больше допустимого размера {max_size} bytes")

        upload_dir = Path(os.getenv("RM_ROLEMODEL_UPLOAD_DIR", str(Path.home() / "rolemodel_uploads")))
        upload_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        upload_path = upload_dir / f"{timestamp}_{safe_name}"
        upload_path.write_bytes(body)

        backup_path = _backup_database(app_config)
        try:
            parsed = parse_workbook(file_path=str(upload_path), requested_sheet=None)
        except Exception as exc:
            return {
                "status": "INVALID",
                "message": f"Файл не соответствует формату загрузки: {exc}",
                "backup_path": backup_path,
                "uploaded_file": str(upload_path),
                "validate": None,
            }

        validate_report = _report_from_parse(parsed)
        if parsed.errors_count > 0:
            return {
                "status": "INVALID",
                "message": "Файл проверен, но не загружен: найдены ошибки структуры или данных.",
                "backup_path": backup_path,
                "uploaded_file": str(upload_path),
                "validate": validate_report,
            }

        snapshot_label = f"ui_upload_{timestamp}_{Path(safe_name).stem[:40]}"
        load_report = load_to_db(config=app_config.db, parsed=parsed, snapshot_label=snapshot_label)
        return {
            "status": "SUCCESS",
            "message": "Файл успешно проверен и загружен.",
            "backup_path": backup_path,
            "uploaded_file": str(upload_path),
            "validate": validate_report,
            "load": load_report,
        }

    return app


app = build_app()
