import json
from typing import Any, Optional, Tuple

import aqt.overview
import aqt.reviewer
from aqt import gui_hooks, mw
from aqt.utils import askUser, showInfo
from anki.cards import Card
from .configDialog import (
    DEFAULT_CONFIG,
    DEFAULT_MODEL_ID,
    DEFAULT_PROMPT,
    DEFAULT_SUMMARY_LANGUAGE,
    SCHEMA_VERSION,
    getParentDeckNames,
)

from .aiModelWorker import AiModelWorker, ModelRequest, createModelWorker
from .cardText import normalizeText, stripHtml
from .outputBlocks import (
    BLOCK_FAILURE,
    BLOCK_STATUS,
    RETRY_BUTTON_HTML,
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
from .todayStudy import (
    MODE_PREVIEW,
    MODE_REVIEW,
    buildTodayStudyOverviewHtml,
    buildTodayStudyPrompt,
    buildTodayStudyRetryButtonHtml,
    injectTodayStudyButton,
    isCongratsPageUrl,
    isTodayStudyAvailable,
    parseTodayStudyCommand,
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
_backgroundWorkers: list = []


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


def getSummaryLanguage(config: dict, deckName: str) -> str:
    # The deck's own language wins, then its nearest ancestor's, then the default.
    summaryLanguages: dict = config.get('summaryLanguages', {})
    deckLanguages: dict = summaryLanguages.get('decks', {})
    for candidateDeckName in [deckName] + getParentDeckNames(deckName):
        language = str(deckLanguages.get(candidateDeckName, '')).strip()
        if language:
            return language
    return str(summaryLanguages.get('default', '')).strip() or DEFAULT_SUMMARY_LANGUAGE


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


def _discardBackgroundWorker(worker: AiModelWorker) -> None:
    if worker in _backgroundWorkers:
        _backgroundWorkers.remove(worker)
    worker.deleteLater()


def _cancelCheckWorker() -> None:
    # The request may still be streaming: keep the thread referenced until it notices the
    # cancellation and finishes, so it is never garbage-collected while running.
    worker: Optional[AiModelWorker] = _state.pop('worker', None)
    if worker is None:
        return
    worker.cancel()
    _backgroundWorkers.append(worker)


def resetState() -> None:
    _cancelCheckWorker()
    _state.clear()


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
    request: ModelRequest,
    index: int,
) -> None:
    if worker is not None and not _isCurrentWorker(worker):
        return
    _state.pop('worker', None)
    failureTitle = f'{request.modelIds[index]} failed'
    if index + 1 < len(request.modelIds):
        beginOutputBlock(BLOCK_FAILURE, failureTitle)
        setOutputBlockBody(plainTextToHtml(message))
        triggerApiCallWithIndex(request = request, index = index + 1)
    else:
        showFinalFailure(failureTitle, message)


def _connectWorkerSignals(worker: AiModelWorker, request: ModelRequest, index: int) -> None:
    worker.blockStarted.connect(
        lambda kind, title, w = worker: onApiBlockStarted(kind, title, w)
    )
    worker.blockTitleChanged.connect(lambda title, w = worker: onApiBlockTitleChanged(title, w))
    worker.blockBodyChanged.connect(lambda body, w = worker: onApiBlockBodyChanged(body, w))
    worker.success.connect(lambda text, w = worker: onApiSuccess(text, w))
    worker.error.connect(
        lambda msg, w = worker: _onApiErrorWithFallback(msg, w, request, index)
    )
    worker.finished.connect(lambda w = worker: _discardBackgroundWorker(w))


def triggerApiCallWithIndex(request: ModelRequest, index: int) -> None:
    modelId = request.modelIds[index]
    beginOutputBlock(BLOCK_STATUS, f'Asking {modelId}\u2026')
    worker = createModelWorker(
        modelId = modelId,
        geminiApiKey = request.geminiApiKey,
        claudeApiKey = request.claudeApiKey,
        prompt = request.prompt,
        useWebSearch = request.useWebSearch,
    )
    if worker is None:
        _onApiErrorWithFallback(
            f"Model '{modelId}' is unsupported or its API key is missing.",
            None,
            request,
            index,
        )
        return

    _connectWorkerSignals(worker = worker, request = request, index = index)
    _state['worker'] = worker
    worker.start()


def hasApiKey(config: dict) -> bool:
    return bool(config.get('apiKey', '').strip() or config.get('claudeApiKey', '').strip())


def showMissingApiKeyFailure(title: str) -> None:
    showFinalFailure(
        title,
        'No API key configured. Open Tools > Add-ons > AI Typed Answer Checker > Config.',
    )


def runModelRequest(config: dict, prompt: str, useWebSearch: bool) -> None:
    # Streams into the output area, which the caller has already shown.
    request = ModelRequest(
        modelIds = tuple(getModelIds(config)),
        prompt = prompt,
        geminiApiKey = config.get('apiKey', '').strip(),
        claudeApiKey = config.get('claudeApiKey', '').strip(),
        useWebSearch = useWebSearch,
    )
    triggerApiCallWithIndex(request = request, index = 0)


def triggerApiCall() -> None:
    showOutputArea(RETRY_BUTTON_HTML)
    config = mw.addonManager.getConfig(__name__) or {}
    if not hasApiKey(config):
        showMissingApiKeyFailure('Cannot check')
        return

    card: Card = _state.get('card')
    if not card:
        showFinalFailure('Cannot check', 'Error: card reference lost.')
        return

    runModelRequest(config = config, prompt = buildPrompt(card, config), useWebSearch = True)


def triggerTodayStudy(mode: str) -> None:
    showOutputArea(buildTodayStudyRetryButtonHtml(mode))
    config = mw.addonManager.getConfig(__name__) or {}
    if not hasApiKey(config):
        showMissingApiKeyFailure('Cannot summarise')
        return

    deck = mw.col.decks.current()
    prompt = buildTodayStudyPrompt(
        mode = mode,
        deck = deck,
        language = getSummaryLanguage(config, deck['name']),
    )
    if prompt is None:
        showFinalFailure('Cannot summarise', 'No learning memory or cards for today in this deck.')
        return

    # A study summary needs no fact lookups, so it runs without web search.
    runModelRequest(config = config, prompt = prompt, useWebSearch = False)


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


def startMemoryUpdate(card: Card, ease: int) -> None:
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
    worker.finished.connect(lambda w = worker: _discardBackgroundWorker(w))
    _backgroundWorkers.append(worker)
    worker.start()


def onReviewerDidAnswerCard(reviewer: Any, card: Card, ease: int) -> None:
    if _state.get('card') and _state.get('lastAiResponse'):
        startMemoryUpdate(card, ease)
    # The card is done: stop a check that is still streaming, even when no next question
    # follows (end of session, timebox dialog).
    resetState()


def onDidShowAnswer(card: Card) -> None:
    if _state.get('card'):
        injectButton()


def onDidShowQuestion(card: Card) -> None:
    resetState()
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
    if isinstance(context, aqt.reviewer.Reviewer) and message == 'typedAnswerCheckerByAI-action-check':
        if _canStartCheck():
            triggerApiCall()
        return (True, None)
    if isinstance(context, aqt.overview.Overview):
        mode = parseTodayStudyCommand(message)
        if mode is None:
            return handled
        if _canStartTodayStudy():
            triggerTodayStudy(mode)
        return (True, None)
    return handled


def _canStartCheck() -> bool:
    # Drop clicks that arrive after the answer was left (e.g. queued behind a rating or bury)
    # or while a check is already running.
    return mw.reviewer.state == 'answer' and 'worker' not in _state


def _canStartTodayStudy() -> bool:
    return mw.state == 'overview' and 'worker' not in _state


def onReviewerWillEnd() -> None:
    # Leaving the review screen (deck overview, home, profile switch) shows no next question.
    resetState()


def onOverviewWillRenderContent(
    overview: aqt.overview.Overview,
    content: aqt.overview.OverviewContent,
) -> None:
    # A re-render replaces the page, including any summary that was streaming into it.
    resetState()
    if isTodayStudyAvailable(MODE_PREVIEW, mw.col.decks.current()):
        content.table += buildTodayStudyOverviewHtml(MODE_PREVIEW)


def onWebviewDidInjectStyleIntoPage(webview: Any) -> None:
    # Once today's cards are done, the overview shows the "Congratulations" page instead,
    # which never goes through overview_will_render_content.
    if webview is not mw.web or mw.state != 'overview' or not isCongratsPageUrl(webview.url().path()):
        return
    resetState()
    if isTodayStudyAvailable(MODE_REVIEW, mw.col.decks.current()):
        injectTodayStudyButton(MODE_REVIEW)


def onStateWillChange(newState: str, oldState: str) -> None:
    # Leaving the overview stops a summary that is still streaming.
    if oldState == 'overview':
        resetState()


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
        'summaryLanguages': config.get('summaryLanguages', DEFAULT_CONFIG['summaryLanguages']),
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
gui_hooks.reviewer_will_end.append(onReviewerWillEnd)
gui_hooks.overview_will_render_content.append(onOverviewWillRenderContent)
gui_hooks.webview_did_inject_style_into_page.append(onWebviewDidInjectStyleIntoPage)
gui_hooks.state_will_change.append(onStateWillChange)
gui_hooks.webview_did_receive_js_message.append(onJsMessage)
gui_hooks.main_window_did_init.append(migrateConfigIfNeeded)
mw.addonManager.setConfigAction(__name__, showConfig)
