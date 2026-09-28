import json
from typing import Optional

from anki.cards import Card
from anki.utils import ids2str
from aqt import mw

from .cardText import getCardAnswerText, getCardQuestionText, truncateText
from .memory import EASE_LABELS, countRatings, describeLatestRatings, getDeckTreeMemory

# Preview runs on the deck overview while cards are still due today; review runs on the
# "Congratulations" screen once today's cards are done.
MODE_PREVIEW: str = 'preview'
MODE_REVIEW: str = 'review'
TODAY_STUDY_MODES: tuple[str, ...] = (MODE_PREVIEW, MODE_REVIEW)
TODAY_STUDY_COMMAND_PREFIX: str = 'typedAnswerCheckerByAI-action-todayStudy:'
TODAY_STUDY_BUTTON_ID: str = 'typedAnswerCheckerByAI-todayStudyButton'

BUTTON_LABELS: dict[str, str] = {
    MODE_PREVIEW: "Preview Today's Study (I)",
    MODE_REVIEW: "Review Today's Study (I)",
}

# "I" (AI insight) is free on the overview: Anki uses o/r/e/c/u there, d/s/a/b/t/y globally,
# and f and / as menu shortcuts. Only one today's-study button (or its Retry) exists at a time,
# so the key clicks whichever is on the page.
SHORTCUT_SCRIPT: str = f"""
(function() {{
    if (document.body.dataset.typedAnswerCheckerByAITodayStudyShortcut) return;
    document.body.dataset.typedAnswerCheckerByAITodayStudyShortcut = '1';
    document.addEventListener('keydown', function(event) {{
        if (event.key !== 'i' || event.ctrlKey || event.metaKey || event.altKey || event.repeat) return;
        const button = document.getElementById('{TODAY_STUDY_BUTTON_ID}');
        if (button) button.click();
    }});
}})();
"""

TODAY_STUDY_CARD_LIMIT: int = 25
QUEUE_FETCH_LIMIT: int = 5000
CARD_TEXT_MAX_LENGTH: int = 300
SECONDS_PER_DAY: int = 86400

# Struggle score = (again + HARD_WEIGHT × hard) / (reviews + SCORE_SMOOTHING_REVIEWS).
# The smoothing keeps a single unlucky review from outranking a card that keeps failing,
# and gives new cards (no reviews) a score of 0 so they come last.
HARD_WEIGHT: float = 0.5
SCORE_SMOOTHING_REVIEWS: int = 3

CARD_TYPE_LABELS: dict[int, str] = {0: 'new', 1: 'learning', 2: 'review', 3: 'relearning'}

CONTAINER_STYLE: str = 'max-width:40em; margin:24px auto 0; padding:0 16px; text-align:center;'

CARD_FIELDS_EXPLANATION: str = (
    'Each card is one JSON object: "question" and "answer" are the card text; "status" is the'
    ' card\'s learning stage; "reviews" counts all past reviews, and "again" (forgot), "hard",'
    ' "good" and "easy" (effortless) count how often the learner self-graded each; "lapses"'
    ' counts how often the card was forgotten after being learned; "lastAgain" is when it was'
    ' last forgotten.'
)

PREVIEW_PROMPT_TEMPLATE: str = (
    'You are a study coach for a learner using the Anki spaced-repetition flashcard app. They'
    ' are about to start today\'s study session for the deck "{deckName}" ({newCount} new,'
    ' {learningCount} learning and {reviewCount} review cards are due).\n\n'
    'Recurring weak points recorded for this learner in this deck:\n{memory}\n\n'
    'Below are up to {cardLimit} of today\'s due cards, the ones the learner has struggled'
    ' with most first. {cardFieldsExplanation}\n\n'
    '{cards}\n\n'
    'Write a short warm-up for today\'s session: 3 to 6 Markdown bullet points on the'
    ' concepts, rules, patterns or distinctions the learner should keep in mind today,'
    ' linking their recurring weak points to the cards coming up. Describe concepts only: do'
    ' NOT quote or reveal any card\'s question or answer, so the upcoming reviews are not'
    ' spoiled. Be concise and encouraging, and start directly with the bullet points.'
    '{languageInstruction}'
)

REVIEW_PROMPT_TEMPLATE: str = (
    'You are a study coach for a learner using the Anki spaced-repetition flashcard app. They'
    ' have just finished today\'s study session for the deck "{deckName}": {cardCount} cards'
    ' in {reviewCount} reviews. By each card\'s worst self-grade today: {worstRatingSummary}.\n\n'
    'Recurring weak points recorded for this learner in this deck:\n{memory}\n\n'
    'Below are up to {cardLimit} of the cards studied today, the ones graded worst today'
    ' first. {cardFieldsExplanation} "todayRatings" lists today\'s self-grades in order.\n\n'
    '{cards}\n\n'
    'Give brief overall feedback on today\'s session in Markdown: what went well, which'
    ' concepts or patterns caused trouble (you may name specific cards), how today relates'
    ' to the recurring weak points, and 2 or 3 concrete tips for the next session. Be concise'
    ' and encouraging, and start directly with the feedback.'
    '{languageInstruction}'
)

# The memory is English and the cards can be in any language, so the output language (the
# deck's free-text setting) is stated explicitly.
LANGUAGE_INSTRUCTION_TEMPLATE: str = (
    '\n\nWrite your whole response in this language: {language}. Keep words, spellings and'
    ' examples from the language being studied in their original script.'
)


def getTodayStudyCommand(mode: str) -> str:
    return TODAY_STUDY_COMMAND_PREFIX + mode


def parseTodayStudyCommand(message: str) -> Optional[str]:
    if not message.startswith(TODAY_STUDY_COMMAND_PREFIX):
        return None
    mode = message[len(TODAY_STUDY_COMMAND_PREFIX):]
    return mode if mode in TODAY_STUDY_MODES else None


def _buildButtonHtml(mode: str, label: str) -> str:
    return (
        f'<button id="{TODAY_STUDY_BUTTON_ID}" onclick="pycmd(\'{getTodayStudyCommand(mode)}\');"'
        f' style="padding:6px 16px; cursor:pointer;">{label}</button>'
    )


def buildTodayStudyButtonHtml(mode: str) -> str:
    return (
        f'<div id="typedAnswerCheckerByAI-container" style="{CONTAINER_STYLE}">'
        f'{_buildButtonHtml(mode, BUTTON_LABELS[mode])}'
        '</div>'
    )


def buildTodayStudyOverviewHtml(mode: str) -> str:
    # The overview is a full page load, so an inline script installs the shortcut.
    return buildTodayStudyButtonHtml(mode) + f'<script>{SHORTCUT_SCRIPT}</script>'


def buildTodayStudyRetryButtonHtml(mode: str) -> str:
    return _buildButtonHtml(mode, 'Retry (I)')


def injectTodayStudyButton(mode: str) -> None:
    buttonHtml = json.dumps(buildTodayStudyButtonHtml(mode))
    mw.web.eval(f"""
        (function() {{
            if (document.getElementById('typedAnswerCheckerByAI-container')) return;
            const wrapper = document.createElement('div');
            wrapper.innerHTML = {buttonHtml};
            document.body.appendChild(wrapper.firstElementChild);
        }})();
        {SHORTCUT_SCRIPT}
    """)


def isCongratsPageUrl(path: str) -> bool:
    # Newer Anki serves "/congrats", older versions "_anki/pages/congrats.html".
    return path.rstrip('/').split('/')[-1] in ('congrats', 'congrats.html')


def _getDeckIds(deck: dict) -> list[int]:
    return mw.col.decks.deck_and_child_ids(deck['id'])


def _getTodayRatingsByCard(deckIds: list[int]) -> dict[int, list[int]]:
    # Cards studied in a filtered deck still count for their home deck (odid).
    dayStartMs = (mw.col.sched.day_cutoff - SECONDS_PER_DAY) * 1000
    deckIdList = ids2str(deckIds)
    rows = mw.col.db.all(
        'select r.cid, r.ease from revlog r join cards c on c.id = r.cid'
        ' where r.id >= ? and r.ease between 1 and 4'
        f' and (c.did in {deckIdList} or c.odid in {deckIdList})'
        ' order by r.id',
        dayStartMs,
    )
    ratingsByCard: dict[int, list[int]] = {}
    for cardId, ease in rows:
        ratingsByCard.setdefault(cardId, []).append(ease)
    return ratingsByCard


def isTodayStudyAvailable(mode: str, deck: dict) -> bool:
    if not getDeckTreeMemory(deck['name']):
        return False
    if mode == MODE_REVIEW:
        return bool(_getTodayRatingsByCard(_getDeckIds(deck)))
    # The overview is only rendered while cards are still due, so preview always has cards.
    return True


def _getReviewHistory(cardIds: list[int]) -> dict[int, list[tuple[int, int]]]:
    rows = mw.col.db.all(
        f'select cid, id, ease from revlog where ease between 1 and 4 and cid in {ids2str(cardIds)}'
    )
    historyByCard: dict[int, list[tuple[int, int]]] = {cardId: [] for cardId in cardIds}
    for cardId, reviewIdMs, ease in rows:
        historyByCard[cardId].append((reviewIdMs, ease))
    return historyByCard


def calculateStruggleScore(history: list[tuple[int, int]]) -> float:
    counts = countRatings(history)
    return (counts[1] + HARD_WEIGHT * counts[2]) / (len(history) + SCORE_SMOOTHING_REVIEWS)


def _describeCard(
    card: Card,
    history: list[tuple[int, int]],
    todayRatings: Optional[list[int]] = None,
) -> dict:
    counts = countRatings(history)
    description: dict = {
        'question': truncateText(getCardQuestionText(card), CARD_TEXT_MAX_LENGTH),
        'answer': truncateText(getCardAnswerText(card), CARD_TEXT_MAX_LENGTH),
        'status': CARD_TYPE_LABELS.get(card.type, 'unknown'),
        'reviews': len(history),
        'again': counts[1],
        'hard': counts[2],
        'good': counts[3],
        'easy': counts[4],
        'lapses': card.lapses,
        'lastAgain': describeLatestRatings(history)[1],
    }
    if todayRatings is not None:
        description['todayRatings'] = [EASE_LABELS[ease] for ease in todayRatings]
    return description


def _formatCards(descriptions: list[dict]) -> str:
    # JSON (one card per line) survives commas and line breaks in card text, unlike CSV.
    lines = ',\n'.join(json.dumps(description, ensure_ascii = False) for description in descriptions)
    return f'[\n{lines}\n]'


def _formatTreeMemory(treeMemory: dict[str, list[str]]) -> str:
    sections = []
    for deckName, points in treeMemory.items():
        bullets = '\n'.join(f'- {point}' for point in points)
        sections.append(bullets if len(treeMemory) == 1 else f'{deckName}:\n{bullets}')
    return '\n\n'.join(sections)


def _buildPreviewPrompt(deck: dict, memory: str, languageInstruction: str) -> Optional[str]:
    queuedCards = mw.col.sched.get_queued_cards(fetch_limit = QUEUE_FETCH_LIMIT)
    cardIds = list(dict.fromkeys(queuedCard.card.id for queuedCard in queuedCards.cards))
    if not cardIds:
        return None
    historyByCard = _getReviewHistory(cardIds)
    selectedIds = sorted(
        cardIds,
        key = lambda cardId: (
            -calculateStruggleScore(historyByCard[cardId]),
            -countRatings(historyByCard[cardId])[1],
        ),
    )[:TODAY_STUDY_CARD_LIMIT]
    descriptions = [
        _describeCard(mw.col.get_card(cardId), historyByCard[cardId]) for cardId in selectedIds
    ]
    return PREVIEW_PROMPT_TEMPLATE.format(
        deckName = deck['name'],
        newCount = queuedCards.new_count,
        learningCount = queuedCards.learning_count,
        reviewCount = queuedCards.review_count,
        memory = memory,
        cardLimit = TODAY_STUDY_CARD_LIMIT,
        cardFieldsExplanation = CARD_FIELDS_EXPLANATION,
        cards = _formatCards(descriptions),
        languageInstruction = languageInstruction,
    )


def _summarizeWorstRatings(ratingsByCard: dict[int, list[int]]) -> str:
    worstCounts = {ease: 0 for ease in EASE_LABELS}
    for ratings in ratingsByCard.values():
        worstCounts[min(ratings)] += 1
    return ', '.join(f'{EASE_LABELS[ease]} {count}' for ease, count in worstCounts.items())


def _buildReviewPrompt(deck: dict, memory: str, languageInstruction: str) -> Optional[str]:
    ratingsByCard = _getTodayRatingsByCard(_getDeckIds(deck))
    if not ratingsByCard:
        return None
    # Again first, then hard, good, easy (by each card's worst grade today); within a grade,
    # cards forgotten more often today come first.
    selectedIds = sorted(
        ratingsByCard,
        key = lambda cardId: (
            min(ratingsByCard[cardId]),
            -ratingsByCard[cardId].count(1),
            -len(ratingsByCard[cardId]),
        ),
    )[:TODAY_STUDY_CARD_LIMIT]
    historyByCard = _getReviewHistory(selectedIds)
    descriptions = [
        _describeCard(mw.col.get_card(cardId), historyByCard[cardId], ratingsByCard[cardId])
        for cardId in selectedIds
    ]
    return REVIEW_PROMPT_TEMPLATE.format(
        deckName = deck['name'],
        cardCount = len(ratingsByCard),
        reviewCount = sum(len(ratings) for ratings in ratingsByCard.values()),
        worstRatingSummary = _summarizeWorstRatings(ratingsByCard),
        memory = memory,
        cardLimit = TODAY_STUDY_CARD_LIMIT,
        cardFieldsExplanation = CARD_FIELDS_EXPLANATION,
        cards = _formatCards(descriptions),
        languageInstruction = languageInstruction,
    )


def buildTodayStudyPrompt(mode: str, deck: dict, language: str) -> Optional[str]:
    # None when there is nothing to summarise (no memory, or no cards for today).
    treeMemory = getDeckTreeMemory(deck['name'])
    if not treeMemory:
        return None
    memory = _formatTreeMemory(treeMemory)
    languageInstruction = LANGUAGE_INSTRUCTION_TEMPLATE.format(language = language)
    if mode == MODE_REVIEW:
        return _buildReviewPrompt(deck, memory, languageInstruction)
    return _buildPreviewPrompt(deck, memory, languageInstruction)
