from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic_core import from_json

from ..config import Settings


class LLMError(Exception):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str

    def reply_preview(self) -> str:
        if self.name not in {"finish", "ask_user"}:
            return ""
        try:
            args = from_json(self.arguments, allow_partial="trailing-strings")
        except ValueError:
            return ""
        if not isinstance(args, dict):
            return ""
        if self.name == "finish":
            summary = args.get("summary")
            return summary if isinstance(summary, str) else ""
        questions = args.get("questions")
        if isinstance(questions, list):
            return "\n".join(q for q in questions if isinstance(q, str))
        return ""


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

    def stream_tools(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
        tool_choice: str | None, on_reply: Callable[[str], None],
    ) -> LLMResponse:
        import openai

        calls: dict[int, ToolCall] = {}
        content: list[str] = []
        preview = ""
        preview_index: int | None = None
        usage = None
        finish_reason = None
        try:
            with self._client.chat.completions.create(
                model=self.settings.llm_model, messages=messages, tools=tools,
                temperature=self.settings.llm_temperature, stream=True,
                **({"tool_choice": tool_choice} if tool_choice else {}),
            ) as stream:
                for chunk in stream:
                    if chunk.usage:
                        usage = chunk.usage.model_dump()
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    finish_reason = choice.finish_reason or finish_reason
                    if choice.delta.content:
                        content.append(choice.delta.content)
                    for part in choice.delta.tool_calls or []:
                        call = calls.setdefault(part.index, ToolCall("", "", ""))
                        call.id += part.id or ""
                        if part.function:
                            call.name += part.function.name or ""
                            call.arguments += part.function.arguments or ""
                            if preview_index is None or part.index <= preview_index:
                                text = call.reply_preview()
                                if text and text != preview:
                                    preview_index = part.index
                                    preview = text
                                    on_reply(text)
        except openai.APIStatusError as error:
            if error.status_code in (400, 422, 501) and "stream" in str(error).lower():
                return self.chat(messages, tools, tool_choice)
            raise LLMError(f"{type(error).__name__}: {error}") from error
        except openai.OpenAIError as error:
            raise LLMError(f"{type(error).__name__}: {error}") from error
        if finish_reason not in {"stop", "tool_calls", "function_call"}:
            raise LLMError("The provider did not finish generating the reply.")
        return LLMResponse("".join(content) or None, [calls[i] for i in sorted(calls)], usage)

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
