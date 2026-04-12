import json
import re
import unicodedata
from typing import Any, Tuple

import aqt.reviewer
from aqt import gui_hooks, mw
from anki.cards import Card
from .configDialog import DEFAULT_PROMPT

from .geminiApi import GeminiWorker

BUTTON_HTML: str = """
<div id="typedAnswerCheckerByAI-container" style="margin-top:12px; text-align:center;">
  <button
    id="typedAnswerCheckerByAI-button"
    onclick="pycmd('typedAnswerCheckerByAI-action-check');"
    style="padding:6px 16px; cursor:pointer;"
  >Check with AI (C)</button>
</div>
"""

_state: dict = {}


def stripHtml(html: str) -> str:
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags = re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags = re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<[^>]+>', '', html)
    html = re.sub(r'\s+', ' ', html)
    return html.strip()


def normalizeText(text: str) -> str:
    text = stripHtml(text)
    text = unicodedata.normalize('NFC', text)
    return text.strip()


def answersMatch(expected: str, provided: str) -> bool:
    return normalizeText(expected) == normalizeText(provided)


def resolveModelId(config: dict) -> str:
    modelName: str = config.get('model', 'gemini-2.5-flash')
    if modelName == 'custom':
        return config.get('customModelId', '').strip() or 'gemini-2.5-flash'
    return modelName


def getPromptForCard(card: Card, config: dict) -> str:
    prompts: dict = config.get('prompts', {})
    noteTypeName: str = card.note_type()['name']
    cardName: str = card.template()['name']
    cardTypeKey = f'{noteTypeName}::{cardName}'
    cardTypePrompt: str = prompts.get('cardTypes', {}).get(cardTypeKey, '')
    if cardTypePrompt:
        return cardTypePrompt
    deckName: str = mw.col.decks.get(card.did)['name']
    deckPrompt: str = prompts.get('decks', {}).get(deckName, '')
    if deckPrompt:
        return deckPrompt
    return prompts.get('default', DEFAULT_PROMPT)


def buildPrompt(card: Card, config: dict) -> str:
    promptTemplate = getPromptForCard(card, config)
    cardQuestion = stripHtml(card.question())
    cardAnswer = normalizeText(_state.get('expected', ''))
    userAnswer: str = _state.get('provided', '')
    return (
        promptTemplate
        .replace('{{cardQuestion}}', cardQuestion)
        .replace('{{cardAnswer}}', cardAnswer)
        .replace('{{userAnswer}}', userAnswer)
    )


def setButtonChecking() -> None:
    mw.reviewer.web.eval("""
        (function() {
            const btn = document.getElementById('typedAnswerCheckerByAI-button');
            if (btn) {
                btn.disabled = true;
                btn.textContent = 'Checking…';
            }
        })();
    """)


def markdownToHtml(text: str) -> str:
    # Code blocks (must be processed before inline code)
    text = re.sub(r'```.*?\n(.*?)```', lambda m: f'<pre><code>{m.group(1)}</code></pre>', text, flags=re.DOTALL)
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    # Headers
    text = re.sub(r'^### (.+)$', r'<h3>\1</h3>', text, flags=re.MULTILINE)
    text = re.sub(r'^## (.+)$', r'<h2>\1</h2>', text, flags=re.MULTILINE)
    text = re.sub(r'^# (.+)$', r'<h1>\1</h1>', text, flags=re.MULTILINE)
    # Bold and italic
    text = re.sub(r'\*\*\*(.+?)\*\*\*', r'<strong><em>\1</em></strong>', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
    # Unordered lists
    text = re.sub(r'(?:^[*\-] .+$\n?)+', lambda m: '<ul>' + re.sub(r'^[*\-] (.+)$', r'<li>\1</li>', m.group(0), flags=re.MULTILINE) + '</ul>', text, flags=re.MULTILINE)
    # Line breaks
    text = re.sub(r'\n{2,}', '</p><p>', text)
    text = re.sub(r'\n', '<br>', text)
    return f'<p>{text}</p>'


def replaceContainerWithResult(text: str) -> None:
    html = markdownToHtml(text)
    safeHtml = json.dumps(html)
    mw.reviewer.web.eval(f"""
        (function() {{
            const container = document.getElementById('typedAnswerCheckerByAI-container');
            if (container) {{
                container.style.textAlign = 'left';
                container.innerHTML = '<hr>' + {safeHtml};
            }}
        }})();
    """)


def replaceContainerWithError(message: str) -> None:
    safeMessage = json.dumps(message)
    mw.reviewer.web.eval(f"""
        (function() {{
            const container = document.getElementById('typedAnswerCheckerByAI-container');
            if (container) {{
                container.style.textAlign = 'center';
                const errorHtml = '<p style="color:red; margin:8px 4px;">' + {safeMessage} + '</p>';
                const retryHtml = '<button id="typedAnswerCheckerByAI-button"'
                    + ' onclick="pycmd(\\'typedAnswerCheckerByAI-action-check\\');"'
                    + ' style="padding:6px 16px; cursor:pointer;">Retry (C)</button>';
                container.innerHTML = errorHtml + retryHtml;
            }}
        }})();
    """)


def onApiSuccess(text: str, worker: GeminiWorker) -> None:
    _state.pop("worker", None)
    replaceContainerWithResult(text)


def onApiError(message: str, worker: GeminiWorker) -> None:
    _state.pop("worker", None)
    replaceContainerWithError(message)


def triggerApiCall() -> None:
    config = mw.addonManager.getConfig(__name__) or {}
    apiKey: str = config.get("apiKey", "").strip()
    if not apiKey:
        replaceContainerWithError(
            "No API key configured. Open Tools > Add-ons > AI Typed Answer Checker > Config."
        )
        return

    card: Card = _state.get("card")
    if not card:
        replaceContainerWithError("Error: card reference lost.")
        return

    modelId = resolveModelId(config)
    prompt = buildPrompt(card, config)

    setButtonChecking()

    worker = GeminiWorker(apiKey = apiKey, modelId = modelId, prompt = prompt)
    worker.success.connect(lambda text: onApiSuccess(text, worker))
    worker.error.connect(lambda message: onApiError(message, worker))
    worker.finished.connect(worker.deleteLater)
    _state["worker"] = worker
    worker.start()


def injectButton() -> None:
    buttonHtml = json.dumps(BUTTON_HTML)
    mw.reviewer.web.eval(f"""
        (function() {{
            if (document.getElementById('typedAnswerCheckerByAI-container')) return;
            const wrapper = document.createElement('div');
            wrapper.innerHTML = {buttonHtml};
            document.body.appendChild(wrapper.firstElementChild);

            if (!document.body.dataset.typedAnswerCheckerByAIShortcut) {{
                document.body.dataset.typedAnswerCheckerByAIShortcut = '1';
                document.addEventListener('keydown', function(event) {{
                    if (event.key === 'c' && !event.ctrlKey && !event.metaKey && !event.altKey) {{
                        const btn = document.getElementById('typedAnswerCheckerByAI-button');
                        if (btn && !btn.disabled) {{
                            pycmd('typedAnswerCheckerByAI-action-check');
                        }}
                    }}
                }});
            }}
        }})();
    """)


def onRenderComparedAnswer(
    output: str,
    initialExpected: str,
    initialProvided: str,
    typePattern: str,
) -> str:
    if answersMatch(initialExpected, initialProvided):
        _state.pop("card", None)
        return output
    _state["card"] = mw.reviewer.card
    _state["expected"] = initialExpected
    _state["provided"] = initialProvided
    return output


def onDidShowAnswer(card: Card) -> None:
    if _state.get("card"):
        injectButton()


def onDidShowQuestion(card: Card) -> None:
    _state.clear()
    mw.reviewer.web.eval("""
        (function() {
            const container = document.getElementById('typedAnswerCheckerByAI-container');
            if (container) container.remove();
        })();
    """)


def onJsMessage(
    handled: Tuple[bool, Any],
    message: str,
    context: Any,
) -> Tuple[bool, Any]:
    if not isinstance(context, aqt.reviewer.Reviewer):
        return handled
    if message == "typedAnswerCheckerByAI-action-check":
        triggerApiCall()
        return (True, None)
    return handled


def showConfig() -> None:
    from .configDialog import ConfigDialog
    dialog = ConfigDialog(mw)
    dialog.exec()


gui_hooks.reviewer_will_render_compared_answer.append(onRenderComparedAnswer)
gui_hooks.reviewer_did_show_answer.append(onDidShowAnswer)
gui_hooks.reviewer_did_show_question.append(onDidShowQuestion)
gui_hooks.webview_did_receive_js_message.append(onJsMessage)
mw.addonManager.setConfigAction(__name__, showConfig)
