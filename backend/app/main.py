from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import router
from .config import Settings, get_settings
from .copilot.agent import Agent
from .domain.models import DEFAULT_WORKSPACE
from .llm.client import LLMClient, OpenAICompatClient
from .observability import setup_logging
from .store import Store


def create_app(settings: Settings | None = None,
               llm_factory: Callable[[], LLMClient] | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level)
    app = FastAPI(title="Wati Automation Builder Copilot")
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list,
                       allow_methods=["*"], allow_headers=["*"])

    _llm: list[LLMClient] = []

    def get_llm() -> LLMClient:
        if not _llm:
            _llm.append(llm_factory() if llm_factory else OpenAICompatClient(settings))
        return _llm[0]

    app.state.settings = settings
    app.state.store = Store()
    app.state.workspace = DEFAULT_WORKSPACE
    app.state.agent_factory = lambda: Agent(get_llm(), settings, DEFAULT_WORKSPACE,
                                            log_dir=settings.log_path)
    app.include_router(router)
    return app


app = create_app()
