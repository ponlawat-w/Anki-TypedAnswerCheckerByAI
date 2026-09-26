import json
import re
import unicodedata
from typing import Any, Optional, Tuple

import aqt.reviewer
from aqt import gui_hooks, mw
from aqt.utils import askUser, showInfo
from anki.cards import Card
from .configDialog import DEFAULT_CONFIG, DEFAULT_MODEL_ID, DEFAULT_PROMPT, SCHEMA_VERSION

from .aiModelWorker import AiModelWorker, createModelWorker
from .outputBlocks import (
    BLOCK_FAILURE,
    BLOCK_STATUS,
    beginOutputBlock,
    markdownToHtml,
    plainTextToHtml,
    setOutputBlockBody,
    setOutputBlockTitle,
    showOutputArea,
    showRetryButton,
)
from .memory import (
    MAX_MEMORY_POINTS,
    MEMORY_GENERATION_CONFIG,
    buildCardStatsBlock,
    buildMemoryUpdatePrompt,
    getDeckMemory,
    getDeckName,
    parseMemoryResponse,
    saveDeckMemory,
)

CONTEXT_BLOCK_TEMPLATE: str = (
    "\n\n---\n"
    "The following context about this learner — who is studying with the Anki spaced-repetition"
    " flashcard app — is provided in English for your reference only. Use it to personalise your"
    " evaluation and gently emphasise the learner's weak points where relevant. Do NOT mention or"
    " quote it, and write your whole response in the same language as the rest of this prompt.\n\n"
    "This card's Anki review history:\n{cardStats}{memorySection}"
)

MEMORY_SECTION_TEMPLATE: str = (
    "\n\nRecurring weak points across this deck:\n{points}"
)

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
_memoryWorkers: list = []


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


def getModelIds(config: dict) -> list[str]:
    models: list[str] = [m for m in config.get('models', []) if m]
    return models if models else [DEFAULT_MODEL_ID]


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


def buildContextBlock(card: Card) -> str:
    points = getDeckMemory(getDeckName(card))
    memorySection = (
        MEMORY_SECTION_TEMPLATE.format(points = '\n'.join(f'- {point}' for point in points))
        if points else ''
    )
    return CONTEXT_BLOCK_TEMPLATE.format(
        cardStats = buildCardStatsBlock(card),
        memorySection = memorySection,
    )


def buildPrompt(card: Card, config: dict) -> str:
    promptTemplate = getPromptForCard(card, config)
    cardQuestion = stripHtml(card.question())
    cardAnswer = normalizeText(_state.get('expected', ''))
    userAnswer: str = _state.get('provided', '')
    prompt = (
        promptTemplate
        .replace('{{cardQuestion}}', cardQuestion)
        .replace('{{cardAnswer}}', cardAnswer)
        .replace('{{userAnswer}}', userAnswer)
    )
    return prompt + buildContextBlock(card)


def _isCurrentWorker(worker: AiModelWorker) -> bool:
    return _state.get('worker') is worker


def showFinalFailure(title: str, message: str) -> None:
    beginOutputBlock(BLOCK_FAILURE, title)
    setOutputBlockBody(plainTextToHtml(message))
    showRetryButton()


def onApiBlockStarted(kind: str, title: str, worker: AiModelWorker) -> None:
    if _isCurrentWorker(worker):
        beginOutputBlock(kind, title)


def onApiBlockTitleChanged(title: str, worker: AiModelWorker) -> None:
    if _isCurrentWorker(worker):
        setOutputBlockTitle(title)


def onApiBlockBodyChanged(body: str, worker: AiModelWorker) -> None:
    if _isCurrentWorker(worker):
        setOutputBlockBody(markdownToHtml(body))


def onApiSuccess(text: str, worker: AiModelWorker) -> None:
    # The answer has already been streamed into the active block.
    if not _isCurrentWorker(worker):
        return
    _state.pop('worker', None)
    _state['lastAiResponse'] = text


def _onApiErrorWithFallback(
    message: str,
    worker: Optional[AiModelWorker],
    modelIds: list[str],
    index: int,
    prompt: str,
    geminiApiKey: str,
    claudeApiKey: str,
) -> None:
    if worker is not None and not _isCurrentWorker(worker):
        return
    _state.pop('worker', None)
    failureTitle = f'{modelIds[index]} failed'
    if index + 1 < len(modelIds):
        beginOutputBlock(BLOCK_FAILURE, failureTitle)
        setOutputBlockBody(plainTextToHtml(message))
        triggerApiCallWithIndex(
            modelIds = modelIds,
            index = index + 1,
            prompt = prompt,
            geminiApiKey = geminiApiKey,
            claudeApiKey = claudeApiKey,
        )
    else:
        showFinalFailure(failureTitle, message)


def _connectWorkerSignals(
    worker: AiModelWorker,
    modelIds: list[str],
    index: int,
    prompt: str,
    geminiApiKey: str,
    claudeApiKey: str,
) -> None:
    worker.blockStarted.connect(
        lambda kind, title, w = worker: onApiBlockStarted(kind, title, w)
    )
    worker.blockTitleChanged.connect(lambda title, w = worker: onApiBlockTitleChanged(title, w))
    worker.blockBodyChanged.connect(lambda body, w = worker: onApiBlockBodyChanged(body, w))
    worker.success.connect(lambda text, w = worker: onApiSuccess(text, w))
    worker.error.connect(
        lambda msg, w = worker: _onApiErrorWithFallback(
            msg, w, modelIds, index, prompt, geminiApiKey, claudeApiKey
        )
    )
    worker.finished.connect(worker.deleteLater)


def triggerApiCallWithIndex(
    modelIds: list[str],
    index: int,
    prompt: str,
    geminiApiKey: str,
    claudeApiKey: str,
) -> None:
    modelId = modelIds[index]
    beginOutputBlock(BLOCK_STATUS, f'Asking {modelId}\u2026')
    worker = createModelWorker(
        modelId = modelId,
        geminiApiKey = geminiApiKey,
        claudeApiKey = claudeApiKey,
        prompt = prompt,
        useWebSearch = True,
    )
    if worker is None:
        _onApiErrorWithFallback(
            f"Model '{modelId}' is unsupported or its API key is missing.",
            None,
            modelIds,
            index,
            prompt,
            geminiApiKey,
            claudeApiKey,
        )
        return

    _connectWorkerSignals(
        worker = worker,
        modelIds = modelIds,
        index = index,
        prompt = prompt,
        geminiApiKey = geminiApiKey,
        claudeApiKey = claudeApiKey,
    )
    _state['worker'] = worker
    worker.start()


def triggerApiCall() -> None:
    showOutputArea()
    config = mw.addonManager.getConfig(__name__) or {}
    geminiApiKey: str = config.get('apiKey', '').strip()
    claudeApiKey: str = config.get('claudeApiKey', '').strip()
    if not geminiApiKey and not claudeApiKey:
        showFinalFailure(
            'Cannot check',
            'No API key configured. Open Tools > Add-ons > AI Typed Answer Checker > Config.',
        )
        return

    card: Card = _state.get('card')
    if not card:
        showFinalFailure('Cannot check', 'Error: card reference lost.')
        return

    modelIds = getModelIds(config)
    prompt = buildPrompt(card, config)

    triggerApiCallWithIndex(
        modelIds = modelIds,
        index = 0,
        prompt = prompt,
        geminiApiKey = geminiApiKey,
        claudeApiKey = claudeApiKey,
    )


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
        _state.pop('card', None)
        return output
    _state['card'] = mw.reviewer.card
    _state['expected'] = initialExpected
    _state['provided'] = initialProvided
    return output


def _onMemoryUpdateSuccess(text: str, worker: AiModelWorker, deckName: str) -> None:
    points = parseMemoryResponse(text, MAX_MEMORY_POINTS)
    if points is not None:
        saveDeckMemory(deckName, points)


def _discardMemoryWorker(worker: AiModelWorker) -> None:
    if worker in _memoryWorkers:
        _memoryWorkers.remove(worker)
    worker.deleteLater()


def onReviewerDidAnswerCard(reviewer: Any, card: Card, ease: int) -> None:
    if not _state.get('card') or not _state.get('lastAiResponse'):
        return

    config = mw.addonManager.getConfig(__name__) or {}
    geminiApiKey: str = config.get('apiKey', '').strip()
    claudeApiKey: str = config.get('claudeApiKey', '').strip()
    if not geminiApiKey and not claudeApiKey:
        return
    modelIds = getModelIds(config)

    deckName = getDeckName(card)
    prompt = buildMemoryUpdatePrompt(
        card = card,
        question = stripHtml(card.question()),
        expectedAnswer = normalizeText(_state.get('expected', '')),
        userAnswer = _state.get('provided', ''),
        aiResponse = _state.get('lastAiResponse', ''),
        ease = ease,
    )

    worker = createModelWorker(
        modelId = modelIds[0],
        geminiApiKey = geminiApiKey,
        claudeApiKey = claudeApiKey,
        prompt = prompt,
        generationConfig = MEMORY_GENERATION_CONFIG,
    )
    if worker is None:
        return
    worker.success.connect(
        lambda text, w = worker, d = deckName: _onMemoryUpdateSuccess(text, w, d)
    )
    worker.finished.connect(lambda w = worker: _discardMemoryWorker(w))
    _memoryWorkers.append(worker)
    worker.start()


def onDidShowAnswer(card: Card) -> None:
    if _state.get('card'):
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
    if message == 'typedAnswerCheckerByAI-action-check':
        triggerApiCall()
        return (True, None)
    return handled


def _migrateLegacyModelList(config: dict) -> list[str]:
    if 'models' in config:
        return config['models']
    model: str = config.get('model', DEFAULT_MODEL_ID)
    customModelId: str = config.get('customModelId', '')
    resolvedModelId = (customModelId.strip() or DEFAULT_MODEL_ID) if model == 'custom' else model
    return [resolvedModelId]


def _migrateConfig(config: dict) -> dict:
    return {
        'schemaVersion': SCHEMA_VERSION,
        'models': _migrateLegacyModelList(config),
        'apiKey': config.get('apiKey', ''),
        'claudeApiKey': config.get('claudeApiKey', ''),
        'prompts': config.get('prompts', DEFAULT_CONFIG['prompts']),
    }


def migrateConfigIfNeeded() -> None:
    config = mw.addonManager.getConfig(__name__)
    if not config:
        return
    if config.get('schemaVersion') == SCHEMA_VERSION and 'claudeApiKey' in config:
        return
    try:
        newConfig = _migrateConfig(config)
        mw.addonManager.writeConfig(__name__, newConfig)
        showInfo('Typed Answer Checker by AI: Configuration Updated')
    except Exception as e:
        if askUser(f'Config upgrade failed ({e}). Reset to default?'):
            mw.addonManager.writeConfig(__name__, DEFAULT_CONFIG)


def showConfig() -> None:
    from .configDialog import ConfigDialog
    dialog = ConfigDialog(mw)
    dialog.exec()


gui_hooks.reviewer_will_render_compared_answer.append(onRenderComparedAnswer)
gui_hooks.reviewer_did_answer_card.append(onReviewerDidAnswerCard)
gui_hooks.reviewer_did_show_answer.append(onDidShowAnswer)
gui_hooks.reviewer_did_show_question.append(onDidShowQuestion)
gui_hooks.webview_did_receive_js_message.append(onJsMessage)
gui_hooks.main_window_did_init.append(migrateConfigIfNeeded)
mw.addonManager.setConfigAction(__name__, showConfig)
