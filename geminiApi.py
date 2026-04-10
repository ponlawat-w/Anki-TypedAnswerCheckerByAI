import json
import urllib.error
import urllib.request

from aqt.qt import QThread, pyqtSignal

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{modelId}:generateContent?key={apiKey}"


def buildRequestUrl(modelId: str, apiKey: str) -> str:
    return GEMINI_API_URL.format(modelId = modelId, apiKey = apiKey)


def buildRequestPayload(prompt: str) -> bytes:
    return json.dumps({
        "contents": [{"parts": [{"text": prompt}]}]
    }).encode("utf-8")


def extractResponseText(data: dict) -> str:
    return data["candidates"][0]["content"]["parts"][0]["text"]


class GeminiWorker(QThread):
    success = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, apiKey: str, modelId: str, prompt: str, parent = None) -> None:
        super().__init__(parent)
        self._apiKey = apiKey
        self._modelId = modelId
        self._prompt = prompt

    def run(self) -> None:
        url = buildRequestUrl(modelId = self._modelId, apiKey = self._apiKey)
        payload = buildRequestPayload(self._prompt)
        request = urllib.request.Request(
            url,
            data = payload,
            headers = {"Content-Type": "application/json"},
            method = "POST",
        )
        try:
            with urllib.request.urlopen(request, timeout = 120) as response:
                data = json.loads(response.read().decode("utf-8"))
            text = extractResponseText(data)
            self.success.emit(text.strip())
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors = "replace")
            self.error.emit(f"HTTP {e.code}: {body}")
        except Exception as e:
            self.error.emit(str(e))
