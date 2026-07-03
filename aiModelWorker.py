import json
import urllib.error
import urllib.request
from typing import Optional

from aqt.qt import QThread, pyqtSignal

GEMINI_PROVIDER: str = 'gemini'
CLAUDE_PROVIDER: str = 'claude'


def getModelProvider(modelId: str) -> Optional[str]:
    if modelId.startswith('gemini-'):
        return GEMINI_PROVIDER
    if modelId.startswith('claude-'):
        return CLAUDE_PROVIDER
    return None


class AiModelWorker(QThread):
    success = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(
        self,
        apiKey: str,
        modelId: str,
        prompt: str,
        generationConfig: Optional[dict] = None,
        parent = None,
    ) -> None:
        super().__init__(parent)
        self._apiKey = apiKey
        self._modelId = modelId
        self._prompt = prompt
        self._generationConfig = generationConfig

    def run(self) -> None:
        try:
            text = self._requestCompletion()
            self.success.emit(text.strip())
        except urllib.error.HTTPError as httpError:
            body = httpError.read().decode('utf-8', errors = 'replace')
            self.error.emit(self._extractHttpErrorMessage(httpError.code, body))
        except Exception as otherError:
            self.error.emit(str(otherError))

    def _requestCompletion(self) -> str:
        raise NotImplementedError

    @staticmethod
    def _extractHttpErrorMessage(code: int, body: str) -> str:
        try:
            return json.loads(body)['error']['message']
        except Exception:
            return f'HTTP {code}: {body}'

    @staticmethod
    def _postJson(url: str, headers: dict, payload: bytes) -> dict:
        request = urllib.request.Request(
            url,
            data = payload,
            headers = headers,
            method = 'POST',
        )
        with urllib.request.urlopen(request, timeout = 120) as response:
            return json.loads(response.read().decode('utf-8'))


def createModelWorker(
    modelId: str,
    geminiApiKey: str,
    claudeApiKey: str,
    prompt: str,
    generationConfig: Optional[dict] = None,
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
        )
    if provider == CLAUDE_PROVIDER:
        if not claudeApiKey:
            return None
        return ClaudeWorker(
            apiKey = claudeApiKey,
            modelId = modelId,
            prompt = prompt,
            generationConfig = generationConfig,
        )
    return None
