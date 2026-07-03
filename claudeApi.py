import json
from typing import Optional, Tuple

from .aiModelWorker import AiModelWorker

CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_API_VERSION = "2023-06-01"
CLAUDE_MAX_TOKENS = 4096
CLAUDE_MAX_TOKENS_THINKING = 8192

# Append this to a Claude model ID to run it with adaptive thinking enabled.
# Without the suffix, thinking is off (see buildThinkingConfig).
ADAPTIVE_THINKING_SUFFIX = "-adaptive-thinking"

# Models whose thinking is always on: an explicit thinking:{"type":"disabled"}
# is rejected with a 400, so "off" means omitting the parameter entirely.
ALWAYS_THINKING_PREFIXES = ("claude-fable-", "claude-mythos-")


def resolveModelId(modelId: str) -> Tuple[str, bool]:
    if modelId.endswith(ADAPTIVE_THINKING_SUFFIX):
        return modelId[: -len(ADAPTIVE_THINKING_SUFFIX)], True
    return modelId, False


def buildThinkingConfig(realModelId: str, adaptiveThinking: bool) -> Optional[dict]:
    if adaptiveThinking:
        return {"type": "adaptive"}
    if realModelId.startswith(ALWAYS_THINKING_PREFIXES):
        return None
    return {"type": "disabled"}


def buildRequestPayload(modelId: str, prompt: str) -> bytes:
    realModelId, adaptiveThinking = resolveModelId(modelId)
    payload: dict = {
        "model": realModelId,
        "max_tokens": CLAUDE_MAX_TOKENS_THINKING if adaptiveThinking else CLAUDE_MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    }
    thinkingConfig = buildThinkingConfig(realModelId, adaptiveThinking)
    if thinkingConfig is not None:
        payload["thinking"] = thinkingConfig
    return json.dumps(payload).encode("utf-8")


def extractResponseText(data: dict) -> str:
    blocks = data.get("content", [])
    text = "".join(
        block.get("text", "") for block in blocks if block.get("type") == "text"
    )
    if not text.strip():
        stopReason = data.get("stop_reason", "unknown")
        raise ValueError(f"Claude returned no text (stop reason: {stopReason}).")
    return text


class ClaudeWorker(AiModelWorker):
    def _requestCompletion(self) -> str:
        payload = buildRequestPayload(self._modelId, self._prompt)
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self._apiKey,
            "anthropic-version": CLAUDE_API_VERSION,
        }
        data = self._postJson(CLAUDE_API_URL, headers, payload)
        return extractResponseText(data)
