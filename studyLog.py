import os
import sqlite3
import time
from contextlib import closing

# SQLite keeps single-card upserts and lookups cheap (no whole-file rewrite on every "Again"),
# and makes the once-per-day claim atomic. Like memory.json, it lives outside the addon config.
SCHEMA_STATEMENTS: tuple[str, ...] = (
    'create table if not exists typedAnswers ('
    ' cardId integer primary key, answer text not null, answeredAt integer not null)',
    'create index if not exists typedAnswersAnsweredAt on typedAnswers (answeredAt)',
    'create table if not exists dailyMemoryUpdates ('
    ' deckName text primary key, dayCutoff integer not null)',
)
CONNECTION_TIMEOUT_SECONDS: float = 5.0


def _databasePath() -> str:
    return os.path.join(os.path.dirname(__file__), 'user_files', 'studyLog.db')


def _connect() -> sqlite3.Connection:
    databasePath = _databasePath()
    os.makedirs(os.path.dirname(databasePath), exist_ok = True)
    connection = sqlite3.connect(databasePath, timeout = CONNECTION_TIMEOUT_SECONDS)
    for statement in SCHEMA_STATEMENTS:
        connection.execute(statement)
    return connection


def saveTypedAnswer(cardId: int, answer: str) -> None:
    # Keeps only the latest typed answer per card.
    try:
        with closing(_connect()) as connection, connection:
            connection.execute(
                'insert or replace into typedAnswers (cardId, answer, answeredAt) values (?, ?, ?)',
                (cardId, answer, int(time.time() * 1000)),
            )
    except sqlite3.Error:
        pass


def getTypedAnswersSince(sinceMs: int) -> dict[int, str]:
    try:
        with closing(_connect()) as connection:
            rows = connection.execute(
                'select cardId, answer from typedAnswers where answeredAt >= ?',
                (sinceMs,),
            ).fetchall()
    except sqlite3.Error:
        return {}
    return {cardId: answer for cardId, answer in rows}


def isDailyMemoryUpdateClaimed(deckName: str, dayCutoff: int) -> bool:
    # A cheap pre-check; only claimDailyMemoryUpdate decides.
    try:
        with closing(_connect()) as connection:
            row = connection.execute(
                'select 1 from dailyMemoryUpdates where deckName = ? and dayCutoff = ?',
                (deckName, dayCutoff),
            ).fetchone()
    except sqlite3.Error:
        return True
    return row is not None


def claimDailyMemoryUpdate(deckName: str, dayCutoff: int) -> bool:
    # True only for the first claim of this deck on this Anki day; the check and the write are
    # one statement, so a claim can never be granted twice.
    try:
        with closing(_connect()) as connection, connection:
            cursor = connection.execute(
                'insert into dailyMemoryUpdates (deckName, dayCutoff) values (?, ?)'
                ' on conflict (deckName) do update set dayCutoff = excluded.dayCutoff'
                ' where dayCutoff != excluded.dayCutoff',
                (deckName, dayCutoff),
            )
            return cursor.rowcount > 0
    except sqlite3.Error:
        return False
