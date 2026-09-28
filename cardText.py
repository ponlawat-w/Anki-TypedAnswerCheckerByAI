import re
import unicodedata

from anki.cards import Card

ANSWER_SEPARATOR_PATTERN: re.Pattern = re.compile(r'<hr[^>]*id\s*=\s*["\']?answer["\']?[^>]*>', re.IGNORECASE)
TYPE_ANSWER_PATTERN: re.Pattern = re.compile(r'\[\[type:(.+?)\]\]')
MEDIA_TAG_PATTERN: re.Pattern = re.compile(r'\[(?:sound|anki:[a-z]+)[^\]]*\]')


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


def truncateText(text: str, maxLength: int) -> str:
    if len(text) <= maxLength:
        return text
    return text[:maxLength].rstrip() + '…'


def _typeAnswerFieldValue(card: Card, fieldSpecification: str) -> str:
    # "cloze:Text" answers are already revealed in the rendered answer.
    if fieldSpecification.startswith('cloze:'):
        return ''
    fieldName = fieldSpecification.split(':', 1)[1] if ':' in fieldSpecification else fieldSpecification
    try:
        return card.note()[fieldName]
    except Exception:
        return ''


def _cleanRenderedText(html: str) -> str:
    return normalizeText(MEDIA_TAG_PATTERN.sub('', html))


def getCardQuestionText(card: Card) -> str:
    return _cleanRenderedText(TYPE_ANSWER_PATTERN.sub('', card.question()))


def getCardAnswerText(card: Card) -> str:
    # The answer side usually repeats the question above <hr id=answer>; keep only what follows.
    answerHtml = ANSWER_SEPARATOR_PATTERN.split(card.answer(), maxsplit = 1)[-1]
    answerHtml = TYPE_ANSWER_PATTERN.sub(
        lambda match: ' ' + _typeAnswerFieldValue(card, match.group(1)) + ' ',
        answerHtml,
    )
    return _cleanRenderedText(answerHtml)
