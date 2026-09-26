import json
import urllib.error
from typing import Optional

from .aiModelWorker import BLOCK_ANSWER, BLOCK_NOTICE, BLOCK_THINKING, AiModelWorker

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{modelId}:streamGenerateContent?alt=sse&key={apiKey}"
GOOGLE_SEARCH_TOOL: dict = {"google_search": {}}
QUOTA_EXCEEDED_STATUS_CODE: int = 429


def buildRequestUrl(modelId: str, apiKey: str) -> str:
    return GEMINI_API_URL.format(modelId = modelId, apiKey = apiKey)


def buildGenerationConfig(generationConfig: Optional[dict]) -> dict:
    # Ask for thought summaries so the reviewer can show the model's reasoning while it thinks.
    mergedConfig = dict(generationConfig or {})
    thinkingConfig = dict(mergedConfig.get("thinkingConfig", {}))
    thinkingConfig.setdefault("includeThoughts", True)
    mergedConfig["thinkingConfig"] = thinkingConfig
    return mergedConfig


def buildRequestPayload(
    prompt: str,
    generationConfig: Optional[dict] = None,
    useWebSearch: bool = False,
) -> bytes:
    payload: dict = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": buildGenerationConfig(generationConfig),
    }
    if useWebSearch:
        payload["tools"] = [GOOGLE_SEARCH_TOOL]
    return json.dumps(payload).encode("utf-8")


def extractChunkParts(chunk: dict) -> list[dict]:
    if "error" in chunk:
        raise RuntimeError(chunk["error"].get("message", "Gemini stream error."))
    candidates = chunk.get("candidates") or []
    if not candidates:
        return []
    return candidates[0].get("content", {}).get("parts") or []


class GeminiWorker(AiModelWorker):
    def _requestCompletion(self) -> str:
        if not self._useWebSearch:
            return self._requestWithSearch(useWebSearch = False)
        try:
            return self._requestWithSearch(useWebSearch = True)
        except urllib.error.HTTPError as httpError:
            # Search quota exhausted: retry the same model once without grounding.
            if httpError.code != QUOTA_EXCEEDED_STATUS_CODE:
                raise
            self._startBlock(BLOCK_NOTICE, "Google Search quota exceeded, retrying without search")
            return self._requestWithSearch(useWebSearch = False)

    def _requestWithSearch(self, useWebSearch: bool) -> str:
        url = buildRequestUrl(modelId = self._modelId, apiKey = self._apiKey)
        payload = buildRequestPayload(
            self._prompt,
            generationConfig = self._generationConfig,
            useWebSearch = useWebSearch,
        )
        # Grounded responses can split the answer across several text parts and chunks.
        self._answerParts: list[str] = []
        headers = {"Content-Type": "application/json"}
        for chunk in self._streamServerSentEvents(url, headers, payload):
            for part in extractChunkParts(chunk):
                self._showPart(part)
        text = "".join(self._answerParts)
        if not text.strip():
            raise ValueError("Gemini response contained no text.")
        return text

    def _showPart(self, part: dict) -> None:
        text = part.get("text")
        if not text:
            return
        if part.get("thought"):
            if self._blockKind == BLOCK_ANSWER:
                # Text followed by more thinking was an intermediate note, not the answer.
                self._setBlockTitle("Note")
                self._answerParts = []
            if self._blockKind != BLOCK_THINKING:
                self._startBlock(BLOCK_THINKING, "Thinking…")
            self._appendBlockBody(text)
            return
        if self._blockKind != BLOCK_ANSWER:
            if self._blockKind == BLOCK_THINKING:
                self._setBlockTitle("Thought process")
            self._startBlock(BLOCK_ANSWER, f"Answer · {self._modelId}")
        self._answerParts.append(text)
        self._appendBlockBody(text)
