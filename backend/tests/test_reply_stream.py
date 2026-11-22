import json

import httpx
import pytest
from openai import OpenAI

from app.config import Settings
from app.llm.client import LLMError, OpenAICompatClient


@pytest.mark.parametrize(("tool", "parts", "expected"), [
    ("ask_user", ['{"questions":["Which', ' team?","When', '?"]}'],
     ["Which", "Which team?\nWhen", "Which team?\nWhen?"]),
    ("finish", ['{"summary":"Saved', ' the flow","suggestions":["Next"]}'],
     ["Saved", "Saved the flow"]),
    ("replace_flow", ['{"flow":', '{"name":"Internal draft"}}'], []),
])
@pytest.mark.parametrize("complete", [True, False])
def test_tool_stream(tool, parts, expected, complete):
    previews = []

    def provider(request):
        assert json.loads(request.content)["stream"] is True
        chunks = []
        for index, part in enumerate(parts):
            function = {"arguments": part}
            call = {"index": 0, "function": function}
            if index == 0:
                function["name"] = tool
                call.update(id="test-call", type="function")
            chunks.append({"delta": {"tool_calls": [call]}, "finish_reason": None})
        if complete:
            chunks.append({"delta": {}, "finish_reason": "tool_calls"})
        body = "".join("data: " + json.dumps({"choices": [chunk]}) + "\n\n" for chunk in chunks)
        return httpx.Response(200, text=body + "data: [DONE]\n\n",
                              headers={"Content-Type": "text/event-stream"})

    client = OpenAICompatClient(Settings(_env_file=None, llm_api_key="test", llm_model="test"))
    with OpenAI(api_key="test", http_client=httpx.Client(transport=httpx.MockTransport(provider))
                ) as client._client:
        if complete:
            result = client.stream_tools([], [], None, previews.append)
            assert result.tool_calls[0].arguments == "".join(parts)
        else:
            with pytest.raises(LLMError, match="did not finish"):
                client.stream_tools([], [], None, previews.append)
    assert previews == expected
