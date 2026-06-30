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

MEMORY_UPDATE_PROMPT: str = (
    "You are maintaining a long-term study memory for a learner using Anki. The memory is a"
    " short list of the learner's recurring mistakes, misconceptions, and weak points across"
    " many cards — NOT facts about any single card.\n\n"
    "Review the latest answer attempt below and produce an updated memory.\n\n"
    "Question: {question}\n"
    "Expected answer: {expectedAnswer}\n"
    "Learner's answer: {userAnswer}\n"
    "Latest AI feedback: {aiResponse}\n"
    "Learner's self-rating: {easeLabel}\n"
    "Card added: {cardAddedDate}\n"
    "Review history (counts): again {againCount} / hard {hardCount} /"
    " good {goodCount} / easy {easyCount}\n"
    "Current memory:\n{currentMemory}\n\n"
    "Update the memory so it captures general, recurring patterns useful across many cards."
    " Keep it concise: at most {maxPoints} short bullet points, each a single sentence written"
    " in English. Merge related points and drop ones that no longer seem relevant. Return ONLY"
    " a JSON array of strings, for example: [\"point one\", \"point two\"]."
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


def getDeckMemory(deckName: str) -> list[str]:
    points = loadMemory().get(deckName, [])
    if isinstance(points, list):
        return [str(point) for point in points if str(point).strip()]
    return []


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


def _easeCounts(card: Card) -> dict[int, int]:
    counts: dict[int, int] = {1: 0, 2: 0, 3: 0, 4: 0}
    for _, ease in _revlogRows(card):
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


def _mostRecentByEase(rows: list[tuple[int, int]]) -> dict[int, str]:
    latest: dict[int, int] = {}
    for reviewIdMs, ease in rows:
        if ease in (1, 2, 3, 4) and reviewIdMs > latest.get(ease, 0):
            latest[ease] = reviewIdMs
    return {ease: _formatDaysAgo(latest[ease]) if ease in latest else 'never' for ease in (1, 2, 3, 4)}


CARD_STATS_TEMPLATE: str = (
    'Card added: {cardAddedDate}\n'
    'Answer counts: again {againCount} / hard {hardCount} /'
    ' good {goodCount} / easy {easyCount}\n'
    'Most recent "again": {againRecent}\n'
    'Most recent "hard": {hardRecent}\n'
    'Most recent "good": {goodRecent}\n'
    'Most recent "easy": {easyRecent}'
)


def buildCardStatsBlock(card: Card) -> str:
    rows = _revlogRows(card)
    counts: dict[int, int] = {1: 0, 2: 0, 3: 0, 4: 0}
    for _, ease in rows:
        if ease in counts:
            counts[ease] += 1
    recent = _mostRecentByEase(rows)
    return CARD_STATS_TEMPLATE.format(
        cardAddedDate = _cardAddedDate(card),
        againCount = counts[1],
        hardCount = counts[2],
        goodCount = counts[3],
        easyCount = counts[4],
        againRecent = recent[1],
        hardRecent = recent[2],
        goodRecent = recent[3],
        easyRecent = recent[4],
    )


def buildMemoryUpdatePrompt(
    card: Card,
    question: str,
    expectedAnswer: str,
    userAnswer: str,
    aiResponse: str,
    ease: int,
    maxPoints: int = MAX_MEMORY_POINTS,
) -> str:
    counts = _easeCounts(card)
    currentMemory = getDeckMemory(getDeckName(card))
    currentMemoryText = (
        '\n'.join(f'- {point}' for point in currentMemory) if currentMemory else '(empty)'
    )
    return MEMORY_UPDATE_PROMPT.format(
        question = question,
        expectedAnswer = expectedAnswer,
        userAnswer = userAnswer,
        aiResponse = aiResponse,
        easeLabel = EASE_LABELS.get(ease, 'unknown'),
        cardAddedDate = _cardAddedDate(card),
        againCount = counts[1],
        hardCount = counts[2],
        goodCount = counts[3],
        easyCount = counts[4],
        currentMemory = currentMemoryText,
        maxPoints = maxPoints,
    )
