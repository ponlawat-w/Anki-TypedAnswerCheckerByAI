import json
from typing import Optional

from .aiModelWorker import AiModelWorker

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{modelId}:generateContent?key={apiKey}"
GOOGLE_SEARCH_TOOL: dict = {"google_search": {}}


def buildRequestUrl(modelId: str, apiKey: str) -> str:
    return GEMINI_API_URL.format(modelId = modelId, apiKey = apiKey)


def buildRequestPayload(
    prompt: str,
    generationConfig: Optional[dict] = None,
    useWebSearch: bool = False,
) -> bytes:
    payload: dict = {"contents": [{"parts": [{"text": prompt}]}]}
    if generationConfig:
        payload["generationConfig"] = generationConfig
    if useWebSearch:
        payload["tools"] = [GOOGLE_SEARCH_TOOL]
    return json.dumps(payload).encode("utf-8")


def extractResponseText(data: dict) -> str:
    # Grounded responses can split the answer across several text parts.
    parts = data["candidates"][0]["content"]["parts"]
    texts = [part["text"] for part in parts if "text" in part and not part.get("thought")]
    if not texts:
        raise ValueError("Gemini response contained no text.")
    return "".join(texts)


class GeminiWorker(AiModelWorker):
    def _requestCompletion(self) -> str:
        url = buildRequestUrl(modelId = self._modelId, apiKey = self._apiKey)
        payload = buildRequestPayload(
            self._prompt,
            generationConfig = self._generationConfig,
            useWebSearch = self._useWebSearch,
        )
        data = self._postJson(url, {"Content-Type": "application/json"}, payload)
        return extractResponseText(data)
