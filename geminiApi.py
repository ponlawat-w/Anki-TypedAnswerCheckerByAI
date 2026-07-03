import json
from typing import Optional

from .aiModelWorker import AiModelWorker

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{modelId}:generateContent?key={apiKey}"


def buildRequestUrl(modelId: str, apiKey: str) -> str:
    return GEMINI_API_URL.format(modelId = modelId, apiKey = apiKey)


def buildRequestPayload(prompt: str, generationConfig: Optional[dict] = None) -> bytes:
    payload: dict = {"contents": [{"parts": [{"text": prompt}]}]}
    if generationConfig:
        payload["generationConfig"] = generationConfig
    return json.dumps(payload).encode("utf-8")


def extractResponseText(data: dict) -> str:
    return data["candidates"][0]["content"]["parts"][0]["text"]


class GeminiWorker(AiModelWorker):
    def _requestCompletion(self) -> str:
        url = buildRequestUrl(modelId = self._modelId, apiKey = self._apiKey)
        payload = buildRequestPayload(self._prompt, self._generationConfig)
        data = self._postJson(url, {"Content-Type": "application/json"}, payload)
        return extractResponseText(data)
