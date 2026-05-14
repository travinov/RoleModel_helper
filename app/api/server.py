from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
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

from ..config import AppConfig


def build_app(config: AppConfig | None = None) -> FastAPI:
    app_config = config or AppConfig.from_env()
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

    return app


app = build_app()
