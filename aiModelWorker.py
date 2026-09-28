import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Iterator, Optional

from aqt.qt import QThread, pyqtSignal

GEMINI_PROVIDER: str = 'gemini'
CLAUDE_PROVIDER: str = 'claude'

# Kinds of progress block a worker can open in the reviewer output area.
BLOCK_THINKING: str = 'thinking'
BLOCK_SEARCH: str = 'search'
BLOCK_ANSWER: str = 'answer'
BLOCK_NOTICE: str = 'notice'

REQUEST_TIMEOUT_SECONDS: int = 120
BLOCK_BODY_EMIT_INTERVAL_SECONDS: float = 0.1


@dataclass(frozen = True)
class ModelRequest:
    # Everything the multi-model fallback chain needs to (re)try one prompt.
    modelIds: tuple[str, ...]
    prompt: str
    geminiApiKey: str
    claudeApiKey: str
    useWebSearch: bool


def getModelProvider(modelId: str) -> Optional[str]:
    if modelId.startswith('gemini-'):
        return GEMINI_PROVIDER
    if modelId.startswith('claude-'):
        return CLAUDE_PROVIDER
    return None


def readServerSentEvents(response: object) -> Iterator[dict]:
    dataLines: list[str] = []
    for rawLine in response:
        line = rawLine.decode('utf-8').rstrip('\r\n')
        if line.startswith('data:'):
            dataLines.append(line[len('data:'):].lstrip())
        elif not line and dataLines:
            yield json.loads('\n'.join(dataLines))
            dataLines = []
    if dataLines:
        yield json.loads('\n'.join(dataLines))


class RequestCancelled(Exception):
    pass


def formatMarkdownLink(title: str, url: str) -> str:
    safeTitle = (title or url).replace('[', '(').replace(']', ')').replace('\n', ' ')
    safeUrl = url.replace(' ', '%20').replace('(', '%28').replace(')', '%29')
    return f'[{safeTitle}]({safeUrl})'


class AiModelWorker(QThread):
    success = pyqtSignal(str)
    error = pyqtSignal(str)
    # Progress for the reviewer output area. A new block becomes the active one; title and
    # body updates apply to the active block. The body is Markdown holding the full text so far.
    blockStarted = pyqtSignal(str, str)
    blockTitleChanged = pyqtSignal(str)
    blockBodyChanged = pyqtSignal(str)

    def __init__(
        self,
        apiKey: str,
        modelId: str,
        prompt: str,
        generationConfig: Optional[dict] = None,
        useWebSearch: bool = False,
        parent = None,
    ) -> None:
        super().__init__(parent)
        self._apiKey = apiKey
        self._modelId = modelId
        self._prompt = prompt
        self._generationConfig = generationConfig
        self._useWebSearch = useWebSearch
        self._blockKind: Optional[str] = None
        self._blockBody: str = ''
        self._blockBodyPending: bool = False
        self._lastBlockBodyEmitTime: float = 0.0
        self._cancelled: bool = False

    def run(self) -> None:
        try:
            text = self._requestCompletion()
            self._raiseIfCancelled()
            self._flushBlockBody()
            self.success.emit(text.strip())
        except RequestCancelled:
            pass
        except Exception as requestError:
            if not self._cancelled:
                self.error.emit(self._describeError(requestError))

    def cancel(self) -> None:
        # Called from the main thread. The stream stops at its next event (a blocked read only
        # returns on new data or the request timeout); no further signal or request follows.
        self._cancelled = True

    def _raiseIfCancelled(self) -> None:
        if self._cancelled:
            raise RequestCancelled()

    def _requestCompletion(self) -> str:
        raise NotImplementedError

    def _startBlock(self, kind: str, title: str) -> None:
        self._flushBlockBody()
        self._blockKind = kind
        self._blockBody = ''
        self.blockStarted.emit(kind, title)

    def _setBlockTitle(self, title: str) -> None:
        self.blockTitleChanged.emit(title)

    def _appendBlockBody(self, text: str) -> None:
        self._blockBody += text
        self._blockBodyPending = True
        if time.monotonic() - self._lastBlockBodyEmitTime >= BLOCK_BODY_EMIT_INTERVAL_SECONDS:
            self._flushBlockBody()

    def _setBlockBody(self, text: str) -> None:
        self._blockBody = text
        self._blockBodyPending = True
        self._flushBlockBody()

    def _flushBlockBody(self) -> None:
        if not self._blockBodyPending:
            return
        self._blockBodyPending = False
        self._lastBlockBodyEmitTime = time.monotonic()
        self.blockBodyChanged.emit(self._blockBody)

    @classmethod
    def _describeError(cls, requestError: Exception) -> str:
        if isinstance(requestError, urllib.error.HTTPError):
            body = requestError.read().decode('utf-8', errors = 'replace')
            return cls._extractHttpErrorMessage(requestError.code, body)
        return str(requestError)

    @staticmethod
    def _extractHttpErrorMessage(code: int, body: str) -> str:
        try:
            return json.loads(body)['error']['message']
        except Exception:
            return f'HTTP {code}: {body}'

    def _streamServerSentEvents(self, url: str, headers: dict, payload: bytes) -> Iterator[dict]:
        self._raiseIfCancelled()
        request = urllib.request.Request(
            url,
            data = payload,
            headers = headers,
            method = 'POST',
        )
        with urllib.request.urlopen(request, timeout = REQUEST_TIMEOUT_SECONDS) as response:
            for event in readServerSentEvents(response):
                self._raiseIfCancelled()
                yield event


def createModelWorker(
    modelId: str,
    geminiApiKey: str,
    claudeApiKey: str,
    prompt: str,
    generationConfig: Optional[dict] = None,
    useWebSearch: bool = False,
) -> Optional[AiModelWorker]:
    from .geminiApi import GeminiWorker
    from .claudeApi import ClaudeWorker

    provider = getModelProvider(modelId)
    if provider == GEMINI_PROVIDER:
        if not geminiApiKey:
            return None
        return GeminiWorker(
            apiKey = geminiApiKey,
            modelId = modelId,
            prompt = prompt,
            generationConfig = generationConfig,
            useWebSearch = useWebSearch,
        )
    if provider == CLAUDE_PROVIDER:
        if not claudeApiKey:
            return None
        return ClaudeWorker(
            apiKey = claudeApiKey,
            modelId = modelId,
            prompt = prompt,
            generationConfig = generationConfig,
            useWebSearch = useWebSearch,
        )
    return None
