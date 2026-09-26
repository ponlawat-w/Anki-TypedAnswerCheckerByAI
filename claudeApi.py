import json
from typing import Optional, Tuple

from .aiModelWorker import (
    BLOCK_ANSWER,
    BLOCK_SEARCH,
    BLOCK_THINKING,
    AiModelWorker,
    formatMarkdownLink,
)

CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_API_VERSION = "2023-06-01"
CLAUDE_MAX_TOKENS = 4096
CLAUDE_MAX_TOKENS_THINKING = 8192

# Append this to a Claude model ID to run it with adaptive thinking enabled.
# Without the suffix, thinking is off (see buildThinkingConfig).
ADAPTIVE_THINKING_SUFFIX = "-adaptive-thinking"

# Models whose thinking is always on: an explicit thinking:{"type":"disabled"}
# is rejected with a 400, so they always run adaptive thinking.
ALWAYS_THINKING_PREFIXES = ("claude-fable-", "claude-mythos-", "claude-opus-5-5")

# The basic web search version works on every Claude model (including Haiku 4.5) and
# calls search directly, so the stream carries no code-execution blocks.
WEB_SEARCH_TOOL_TYPE = "web_search_20250305"
WEB_SEARCH_MAX_USES = 2
WEB_SEARCH_SYSTEM_PROMPT = (
    "You have a web search tool. Answer from your own knowledge by default. Search only when"
    " you are genuinely unsure whether a word, spelling, form, or usage exists or is standard,"
    " and then prefer authoritative sources such as official dictionaries, language academies,"
    " and reputable grammar references."
)

# A long server-side search loop can pause the turn; it is resumed by re-sending the content.
MAX_PAUSE_CONTINUATIONS = 2
PAUSE_TURN_STOP_REASON = "pause_turn"

THINKING_BLOCK_TYPES = ("thinking", "redacted_thinking")
SERVER_TOOL_USE_BLOCK_TYPE = "server_tool_use"
WEB_SEARCH_RESULT_BLOCK_TYPE = "web_search_tool_result"


def resolveModelId(modelId: str) -> Tuple[str, bool]:
    if modelId.endswith(ADAPTIVE_THINKING_SUFFIX):
        return modelId[: -len(ADAPTIVE_THINKING_SUFFIX)], True
    return modelId, False


def buildThinkingConfig(realModelId: str, adaptiveThinking: bool) -> dict:
    if adaptiveThinking or realModelId.startswith(ALWAYS_THINKING_PREFIXES):
        # Current models default to "omitted" (empty thinking text); ask for readable summaries.
        return {"type": "adaptive", "display": "summarized"}
    return {"type": "disabled"}


def buildRequestPayload(
    modelId: str,
    messages: list[dict],
    webSearchMaxUses: Optional[int],
) -> bytes:
    realModelId, adaptiveThinking = resolveModelId(modelId)
    payload: dict = {
        "model": realModelId,
        "max_tokens": CLAUDE_MAX_TOKENS_THINKING if adaptiveThinking else CLAUDE_MAX_TOKENS,
        "messages": messages,
        "stream": True,
        "thinking": buildThinkingConfig(realModelId, adaptiveThinking),
    }
    if webSearchMaxUses is not None:
        payload["system"] = WEB_SEARCH_SYSTEM_PROMPT
        payload["tools"] = [
            {"type": WEB_SEARCH_TOOL_TYPE, "name": "web_search", "max_uses": webSearchMaxUses}
        ]
    return json.dumps(payload).encode("utf-8")


def buildAssistantContent(blocks: list[dict]) -> list[dict]:
    content = []
    for block in blocks:
        cleanBlock = dict(block)
        if cleanBlock.get("citations") is None:
            cleanBlock.pop("citations", None)
        content.append(cleanBlock)
    return content


def findAnswerBlocks(blocks: list[dict]) -> list[dict]:
    # The answer is the trailing run of text blocks; text before a search or a thinking
    # block is an intermediate note (e.g. "Let me look that up").
    answerBlocks: list[dict] = []
    for block in blocks:
        if block.get("type") == "text":
            answerBlocks.append(block)
        else:
            answerBlocks = []
    return answerBlocks


def extractResponseText(answerBlocks: list[dict], stopReason: Optional[str]) -> str:
    text = "".join(block.get("text", "") for block in answerBlocks)
    if not text.strip():
        raise ValueError(f"Claude returned no text (stop reason: {stopReason or 'unknown'}).")
    return text


def extractSources(answerBlocks: list[dict]) -> list[Tuple[str, str]]:
    sources: list[Tuple[str, str]] = []
    seenUrls: set[str] = set()
    for block in answerBlocks:
        for citation in block.get("citations") or []:
            url = citation.get("url", "")
            if url.startswith(("http://", "https://")) and url not in seenUrls:
                seenUrls.add(url)
                sources.append((citation.get("title", ""), url))
    return sources


def buildSourcesMarkdown(sources: list[Tuple[str, str]]) -> str:
    lines = "\n".join(f"- {formatMarkdownLink(title, url)}" for title, url in sources)
    return f"\n\n**Sources**\n{lines}"


def buildSearchResultsMarkdown(results: list[dict]) -> str:
    links = [
        f"- {formatMarkdownLink(result.get('title', ''), result.get('url', ''))}"
        for result in results
        if result.get("url", "").startswith(("http://", "https://"))
    ]
    return "\n".join(links) if links else "No results."


def countWebSearches(blocks: list[dict]) -> int:
    return sum(
        1 for block in blocks
        if block.get("type") == SERVER_TOOL_USE_BLOCK_TYPE and block.get("name") == "web_search"
    )


class ClaudeWorker(AiModelWorker):
    def _requestCompletion(self) -> str:
        self._contentBlocks: list[dict] = []
        self._partialInputs: dict[int, str] = {}
        self._lastSearchQuery = ""
        self._stopReason: Optional[str] = None
        for _ in range(MAX_PAUSE_CONTINUATIONS + 1):
            self._streamResponse()
            if self._stopReason != PAUSE_TURN_STOP_REASON:
                break
        answerBlocks = findAnswerBlocks(self._contentBlocks)
        text = extractResponseText(answerBlocks, self._stopReason)
        sources = extractSources(answerBlocks)
        if sources:
            self._setBlockBody(text + buildSourcesMarkdown(sources))
        return text

    def _streamResponse(self) -> None:
        messages: list[dict] = [{"role": "user", "content": self._prompt}]
        if self._contentBlocks:
            messages.append(
                {"role": "assistant", "content": buildAssistantContent(self._contentBlocks)}
            )
        payload = buildRequestPayload(self._modelId, messages, self._getWebSearchMaxUses())
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self._apiKey,
            "anthropic-version": CLAUDE_API_VERSION,
        }
        # Block indexes restart at 0 in every (continuation) response.
        self._blockIndexOffset = len(self._contentBlocks)
        self._stopReason = None
        for event in self._streamServerSentEvents(CLAUDE_API_URL, headers, payload):
            self._handleStreamEvent(event)

    def _getWebSearchMaxUses(self) -> Optional[int]:
        if not self._useWebSearch:
            return None
        # A continuation must still declare the tool, so the cap never drops below 1.
        return max(1, WEB_SEARCH_MAX_USES - countWebSearches(self._contentBlocks))

    def _handleStreamEvent(self, event: dict) -> None:
        eventType = event.get("type")
        if eventType == "content_block_start":
            self._onContentBlockStart(event.get("content_block", {}))
        elif eventType == "content_block_delta":
            self._onContentBlockDelta(event.get("index", 0), event.get("delta", {}))
        elif eventType == "content_block_stop":
            self._onContentBlockStop(event.get("index", 0))
        elif eventType == "message_delta":
            self._stopReason = event.get("delta", {}).get("stop_reason") or self._stopReason
        elif eventType == "error":
            raise RuntimeError(event.get("error", {}).get("message", "Claude stream error."))

    def _onContentBlockStart(self, block: dict) -> None:
        block = dict(block)
        self._contentBlocks.append(block)
        blockType = block.get("type")
        if blockType == "text":
            if self._blockKind != BLOCK_ANSWER:
                self._startBlock(BLOCK_ANSWER, f"Answer · {self._modelId}")
            self._appendBlockBody(block.get("text", ""))
            return
        if self._blockKind == BLOCK_ANSWER:
            self._setBlockTitle("Note")
        if blockType in THINKING_BLOCK_TYPES:
            self._startBlock(BLOCK_THINKING, "Thinking…")
        elif blockType == SERVER_TOOL_USE_BLOCK_TYPE:
            self._startBlock(BLOCK_SEARCH, "Searching the web…")
        elif blockType == WEB_SEARCH_RESULT_BLOCK_TYPE:
            self._showSearchResult(block)

    def _onContentBlockDelta(self, index: int, delta: dict) -> None:
        position = self._blockIndexOffset + index
        block = self._contentBlocks[position]
        deltaType = delta.get("type")
        if deltaType == "text_delta":
            block["text"] = block.get("text", "") + delta.get("text", "")
            self._appendBlockBody(delta.get("text", ""))
        elif deltaType == "thinking_delta":
            block["thinking"] = block.get("thinking", "") + delta.get("thinking", "")
            self._appendBlockBody(delta.get("thinking", ""))
        elif deltaType == "signature_delta":
            block["signature"] = delta.get("signature", "")
        elif deltaType == "input_json_delta":
            self._partialInputs[position] = (
                self._partialInputs.get(position, "") + delta.get("partial_json", "")
            )
        elif deltaType == "citations_delta":
            if block.get("citations") is None:
                block["citations"] = []
            block["citations"].append(delta.get("citation", {}))

    def _onContentBlockStop(self, index: int) -> None:
        position = self._blockIndexOffset + index
        block = self._contentBlocks[position]
        blockType = block.get("type")
        if blockType in THINKING_BLOCK_TYPES:
            self._setBlockTitle("Thought process")
        elif blockType == SERVER_TOOL_USE_BLOCK_TYPE:
            partialInput = self._partialInputs.pop(position, "")
            if partialInput:
                block["input"] = json.loads(partialInput)
            self._lastSearchQuery = (block.get("input") or {}).get("query", "")
            if self._lastSearchQuery:
                self._setBlockTitle(f"Searching: “{self._lastSearchQuery}”…")

    def _showSearchResult(self, block: dict) -> None:
        content = block.get("content")
        if isinstance(content, dict):
            self._setBlockTitle(f"Search failed ({content.get('error_code', 'unknown error')})")
            return
        if self._lastSearchQuery:
            self._setBlockTitle(f"Searched: “{self._lastSearchQuery}”")
        else:
            self._setBlockTitle("Searched the web")
        self._setBlockBody(buildSearchResultsMarkdown(content or []))
