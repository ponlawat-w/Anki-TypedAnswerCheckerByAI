import json
import os
import re
from datetime import datetime
from typing import Optional

from anki.cards import Card
from aqt import mw

MAX_MEMORY_POINTS: int = 20
MAX_POINT_LENGTH: int = 300

EASE_LABELS: dict[int, str] = {1: 'again', 2: 'hard', 3: 'good', 4: 'easy'}

MEMORY_GENERATION_CONFIG: dict = {'responseMimeType': 'application/json'}

MEMORY_TASK_INTRODUCTION: str = (
    "You are maintaining a long-term study memory for a learner using Anki. The memory is a"
    " short list of the learner's recurring mistakes, misconceptions, and weak points across"
    " many cards — NOT facts about any single card.\n\n"
)

MEMORY_OUTPUT_INSTRUCTIONS: str = (
    "Update the memory so it captures general, recurring patterns useful across many cards."
    " Keep it concise: at most {maxPoints} short bullet points, each a single sentence written"
    " in English, but keep words, spellings, and examples from the language being studied in"
    " their original script (do not romanise or translate them). Merge related points and drop ones that no longer seem relevant. Return ONLY"
    " a JSON array of strings, for example: [\"point one\", \"point two\"]."
)

MEMORY_UPDATE_PROMPT: str = (
    MEMORY_TASK_INTRODUCTION
    + "Review the latest answer attempt below and produce an updated memory.\n\n"
    "Question: {question}\n"
    "Expected answer: {expectedAnswer}\n"
    "Learner's answer: {userAnswer}\n"
    "Latest AI feedback: {aiResponse}\n"
    "Learner's self-rating: {easeLabel}\n"
    "{cardStats}\n"
    "Current memory:\n{currentMemory}\n\n"
    + MEMORY_OUTPUT_INSTRUCTIONS
)

DAILY_MEMORY_UPDATE_PROMPT: str = (
    MEMORY_TASK_INTRODUCTION
    + 'The learner has just finished today\'s study session for the deck "{deckName}". Below'
    " are up to {cardLimit} of the cards they forgot (graded again) today, the ones forgotten"
    " most often today first. {cardFieldsExplanation}\n\n"
    "{cards}\n\n"
    "Compare what the learner typed with the expected answers to find what went wrong, and"
    " produce an updated memory.\n\n"
    "Current memory:\n{currentMemory}\n\n"
    + MEMORY_OUTPUT_INSTRUCTIONS
)


def _memoryFilePath() -> str:
    return os.path.join(os.path.dirname(__file__), 'user_files', 'memory.json')


def loadMemory() -> dict:
    try:
        with open(_memoryFilePath(), encoding = 'utf-8') as memoryFile:
            data = json.load(memoryFile)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _writeMemory(memory: dict) -> None:
    filePath = _memoryFilePath()
    os.makedirs(os.path.dirname(filePath), exist_ok = True)
    with open(filePath, 'w', encoding = 'utf-8') as memoryFile:
        json.dump(memory, memoryFile, ensure_ascii = False, indent = 2)


def _storedPoints(points: object) -> list[str]:
    if isinstance(points, list):
        return [str(point) for point in points if str(point).strip()]
    return []


def getDeckMemory(deckName: str) -> list[str]:
    return _storedPoints(loadMemory().get(deckName, []))


def isInDeckTree(deckName: str, treeDeckName: str) -> bool:
    return deckName == treeDeckName or deckName.startswith(treeDeckName + '::')


def getDeckTreeMemory(deckName: str) -> dict[str, list[str]]:
    # Memory is keyed by each card's own deck, so a parent deck gathers its subdecks' memory too.
    treeMemory: dict[str, list[str]] = {}
    for memoryDeckName, storedPoints in loadMemory().items():
        if isInDeckTree(memoryDeckName, deckName):
            points = _storedPoints(storedPoints)
            if points:
                treeMemory[memoryDeckName] = points
    return treeMemory


def saveDeckMemory(deckName: str, points: list[str]) -> None:
    memory = loadMemory()
    if points:
        memory[deckName] = points
    else:
        memory.pop(deckName, None)
    _writeMemory(memory)


def clearAllMemory() -> None:
    _writeMemory({})


def _normalizePoints(points: list, maxPoints: int) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for rawPoint in points:
        point = str(rawPoint).strip()
        if not point:
            continue
        if len(point) > MAX_POINT_LENGTH:
            point = point[:MAX_POINT_LENGTH].rstrip()
        if point.lower() in seen:
            continue
        seen.add(point.lower())
        normalized.append(point)
        if len(normalized) >= maxPoints:
            break
    return normalized


def _stripTrailingCommas(text: str) -> str:
    return re.sub(r',\s*([\]}])', r'\1', text)


def _tryParseJsonArray(text: str) -> Optional[list]:
    try:
        data = json.loads(text)
    except Exception:
        return None
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for value in data.values():
            if isinstance(value, list):
                return value
    return None


def _parseBulletLines(text: str) -> list[str]:
    # Only accept lines that carry an explicit bullet or number marker, so free-form
    # prose (e.g. a refusal or apology) is never mistaken for a memory point.
    points: list[str] = []
    markerPattern = re.compile(r'^[\-\*•]\s+|^\d+[\.\)]\s+')
    for line in text.splitlines():
        cleaned = line.strip()
        if not markerPattern.match(cleaned):
            continue
        cleaned = markerPattern.sub('', cleaned, count = 1)
        cleaned = cleaned.strip().strip(',').strip().strip('"').strip("'").strip()
        if cleaned:
            points.append(cleaned)
    return points


def parseMemoryResponse(text: str, maxPoints: int = MAX_MEMORY_POINTS) -> Optional[list[str]]:
    if not text or not text.strip():
        return None
    stripped = text.strip()

    candidates: list[str] = []
    fenceMatch = re.search(r'```(?:json)?\s*(.*?)```', stripped, re.DOTALL | re.IGNORECASE)
    if fenceMatch:
        candidates.append(fenceMatch.group(1).strip())
    candidates.append(stripped)
    bracketMatch = re.search(r'\[.*\]', stripped, re.DOTALL)
    if bracketMatch:
        candidates.append(bracketMatch.group(0))

    for candidate in candidates:
        for variant in (candidate, _stripTrailingCommas(candidate)):
            parsed = _tryParseJsonArray(variant)
            if parsed is not None:
                return _normalizePoints(parsed, maxPoints)

    bulletPoints = _parseBulletLines(stripped)
    if bulletPoints:
        return _normalizePoints(bulletPoints, maxPoints)
    return None


def getDeckName(card: Card) -> str:
    deck = mw.col.decks.get(card.did)
    return deck['name'] if deck else ''


def _cardAddedDate(card: Card) -> str:
    try:
        return datetime.fromtimestamp(card.id / 1000).strftime('%Y-%m-%d')
    except Exception:
        return 'unknown'


def _revlogRows(card: Card) -> list[tuple[int, int]]:
    try:
        return mw.col.db.all('select id, ease from revlog where cid = ?', card.id)
    except Exception:
        return []


def _noteRevlogRows(card: Card) -> list[tuple[int, int]]:
    try:
        return mw.col.db.all(
            'select id, ease from revlog where cid in (select id from cards where nid = ?)',
            card.nid,
        )
    except Exception:
        return []


def countRatings(rows: list[tuple[int, int]]) -> dict[int, int]:
    counts: dict[int, int] = {1: 0, 2: 0, 3: 0, 4: 0}
    for _, ease in rows:
        if ease in counts:
            counts[ease] += 1
    return counts


def _formatDaysAgo(reviewIdMs: int) -> str:
    days = int((datetime.now().timestamp() * 1000 - reviewIdMs) / 86400000)
    if days <= 0:
        return 'today'
    if days == 1:
        return 'yesterday'
    return f'{days} days ago'


def describeLatestRatings(rows: list[tuple[int, int]]) -> dict[int, str]:
    latest: dict[int, int] = {}
    for reviewIdMs, ease in rows:
        if ease in (1, 2, 3, 4) and reviewIdMs > latest.get(ease, 0):
            latest[ease] = reviewIdMs
    return {ease: _formatDaysAgo(latest[ease]) if ease in latest else 'never' for ease in (1, 2, 3, 4)}


STATS_LINES_TEMPLATE: str = (
    '{label} — how often the learner self-graded their recall (again = forgot,'
    ' hard, good, easy = effortless): again {againCount}, hard {hardCount},'
    ' good {goodCount}, easy {easyCount}.\n'
    '{label} — how long ago each grade was last given: again {againRecent},'
    ' hard {hardRecent}, good {goodRecent}, easy {easyRecent}.'
)


def _statsLines(label: str, rows: list[tuple[int, int]]) -> str:
    counts = countRatings(rows)
    recent = describeLatestRatings(rows)
    return STATS_LINES_TEMPLATE.format(
        label = label,
        againCount = counts[1],
        hardCount = counts[2],
        goodCount = counts[3],
        easyCount = counts[4],
        againRecent = recent[1],
        hardRecent = recent[2],
        goodRecent = recent[3],
        easyRecent = recent[4],
    )


def buildCardStatsBlock(card: Card) -> str:
    return (
        f'Card added: {_cardAddedDate(card)}\n'
        + _statsLines('This card', _revlogRows(card))
        + '\n\n'
        + _statsLines('This note (all its card types combined)', _noteRevlogRows(card))
    )


def _formatCurrentMemory(points: list[str]) -> str:
    return '\n'.join(f'- {point}' for point in points) if points else '(empty)'


def buildMemoryUpdatePrompt(
    currentMemory: list[str],
    question: str,
    expectedAnswer: str,
    userAnswer: str,
    aiResponse: str,
    ease: int,
    cardStats: str,
    maxPoints: int = MAX_MEMORY_POINTS,
) -> str:
    return MEMORY_UPDATE_PROMPT.format(
        question = question,
        expectedAnswer = expectedAnswer,
        userAnswer = userAnswer,
        aiResponse = aiResponse,
        easeLabel = EASE_LABELS.get(ease, 'unknown'),
        cardStats = cardStats,
        currentMemory = _formatCurrentMemory(currentMemory),
        maxPoints = maxPoints,
    )


def buildDailyMemoryUpdatePrompt(
    currentMemory: list[str],
    deckName: str,
    cardLimit: int,
    cardFieldsExplanation: str,
    cards: str,
    maxPoints: int = MAX_MEMORY_POINTS,
) -> str:
    return DAILY_MEMORY_UPDATE_PROMPT.format(
        deckName = deckName,
        cardLimit = cardLimit,
        cardFieldsExplanation = cardFieldsExplanation,
        cards = cards,
        currentMemory = _formatCurrentMemory(currentMemory),
        maxPoints = maxPoints,
    )
