from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..config import Settings


class LLMError(Exception):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, Any] | None = None

    def to_message(self) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            msg["tool_calls"] = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.name, "arguments": tc.arguments}}
                for tc in self.tool_calls
            ]
        return msg


class LLMClient(Protocol):
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
    ) -> LLMResponse: ...


class OpenAICompatClient:
    def __init__(self, settings: Settings):
        from openai import OpenAI

        if not settings.llm_api_key or not settings.llm_model:
            raise LLMError("LLM_API_KEY / LLM_MODEL are not configured")
        self.settings = settings
        self._client = OpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
    ) -> LLMResponse:
        import openai

        kwargs: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": messages,
            "temperature": self.settings.llm_temperature,
        }
        if tools:
            kwargs["tools"] = tools
            if tool_choice:
                kwargs["tool_choice"] = tool_choice
        try:
            resp = self._client.chat.completions.create(**kwargs)
        except openai.OpenAIError as e:
            raise LLMError(f"{type(e).__name__}: {e}") from e
        if not resp.choices:
            raise LLMError("provider returned no choices")
        msg = resp.choices[0].message
        calls = [
            ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments or "{}")
            for tc in (msg.tool_calls or [])
            if getattr(tc, "function", None) is not None
        ]
        usage = resp.usage.model_dump() if resp.usage else None
        return LLMResponse(content=msg.content, tool_calls=calls, usage=usage)

    def stream_text(
        self, messages: list[dict[str, Any]], on_text: Callable[[str], None],
    ) -> LLMResponse:
        import openai

        parts: list[str] = []
        usage = None
        try:
            with self._client.chat.completions.create(
                model=self.settings.llm_model,
                messages=messages,
                temperature=self.settings.llm_temperature,
                stream=True,
            ) as stream:
                for chunk in stream:
                    if chunk.usage:
                        usage = chunk.usage.model_dump()
                    if chunk.choices:
                        delta = chunk.choices[0].delta
                        if delta and delta.content:
                            parts.append(delta.content)
                            on_text(delta.content)
        except openai.OpenAIError as error:
            raise LLMError(f"{type(error).__name__}: {error}") from error
        return LLMResponse(content="".join(parts), usage=usage)
