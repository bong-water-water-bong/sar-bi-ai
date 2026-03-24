"""Fast persistent memory for Sarcastic Bitch — SQLite-backed for instant reads/writes."""

import sqlite3
import time
import json
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "memory.db"
DB_PATH.parent.mkdir(exist_ok=True)

_conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
_conn.execute("PRAGMA journal_mode=WAL")  # Fast concurrent reads
_conn.execute("PRAGMA synchronous=NORMAL")


def _init():
    _conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            name TEXT PRIMARY KEY,
            first_seen TEXT,
            last_seen TEXT,
            message_count INTEGER DEFAULT 0,
            topics TEXT DEFAULT '[]',
            questions TEXT DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_name TEXT,
            text TEXT,
            ts TEXT,
            FOREIGN KEY(user_name) REFERENCES users(name)
        );
        CREATE TABLE IF NOT EXISTS moments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            what TEXT,
            who TEXT,
            ts TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_notes_user ON notes(user_name);
        CREATE INDEX IF NOT EXISTS idx_notes_ts ON notes(ts);
    """)

_init()


def remember_user(user_name: str, note: str):
    now = time.strftime("%Y-%m-%d %H:%M")
    _conn.execute(
        "INSERT INTO users(name, first_seen, last_seen, message_count) VALUES(?,?,?,1) "
        "ON CONFLICT(name) DO UPDATE SET last_seen=?, message_count=message_count+1",
        (user_name, now, now, now),
    )
    if note.strip():
        _conn.execute("INSERT INTO notes(user_name, text, ts) VALUES(?,?,?)", (user_name, note[:200], now))
        # Keep last 100 notes per user
        _conn.execute(
            "DELETE FROM notes WHERE user_name=? AND id NOT IN "
            "(SELECT id FROM notes WHERE user_name=? ORDER BY id DESC LIMIT 100)",
            (user_name, user_name),
        )
    _conn.commit()


def add_topic(user_name: str, topic: str):
    row = _conn.execute("SELECT topics FROM users WHERE name=?", (user_name,)).fetchone()
    if row:
        topics = json.loads(row[0] or "[]")
        if topic.lower() not in [t.lower() for t in topics]:
            topics.append(topic)
            topics = topics[-30:]
            _conn.execute("UPDATE users SET topics=? WHERE name=?", (json.dumps(topics), user_name))
            _conn.commit()


def add_followup_question(user_name: str, question: str):
    row = _conn.execute("SELECT questions FROM users WHERE name=?", (user_name,)).fetchone()
    if row:
        qs = json.loads(row[0] or "[]")
        qs.append(question)
        qs = qs[-10:]
        _conn.execute("UPDATE users SET questions=? WHERE name=?", (json.dumps(qs), user_name))
        _conn.commit()


def pop_followup_question(user_name: str) -> str | None:
    row = _conn.execute("SELECT questions FROM users WHERE name=?", (user_name,)).fetchone()
    if row:
        qs = json.loads(row[0] or "[]")
        if qs:
            q = qs.pop(0)
            _conn.execute("UPDATE users SET questions=? WHERE name=?", (json.dumps(qs), user_name))
            _conn.commit()
            return q
    return None


def remember_moment(description: str, users_involved: list[str]):
    _conn.execute(
        "INSERT INTO moments(what, who, ts) VALUES(?,?,?)",
        (description, json.dumps(users_involved), time.strftime("%Y-%m-%d %H:%M")),
    )
    # Keep last 200
    _conn.execute("DELETE FROM moments WHERE id NOT IN (SELECT id FROM moments ORDER BY id DESC LIMIT 200)")
    _conn.commit()


def get_user_context(user_name: str) -> str:
    row = _conn.execute("SELECT * FROM users WHERE name=?", (user_name,)).fetchone()
    if not row:
        return f"{user_name} is someone new — be curious, ask about them."
    name, first_seen, last_seen, msg_count, topics_json, questions_json = row
    topics = json.loads(topics_json or "[]")
    questions = json.loads(questions_json or "[]")

    lines = [f"About {name} (here since {first_seen}, {msg_count} messages, last seen {last_seen}):"]
    if topics:
        lines.append(f"  Into: {', '.join(topics[-10:])}")

    notes = _conn.execute(
        "SELECT text, ts FROM notes WHERE user_name=? ORDER BY id DESC LIMIT 15", (user_name,)
    ).fetchall()
    if notes:
        lines.append("  Recent:")
        for text, ts in reversed(notes):
            lines.append(f"    [{ts}] {text}")

    if questions:
        lines.append("  Ask them about:")
        for q in questions[:5]:
            lines.append(f"    - {q}")

    return "\n".join(lines)


def get_recent_moments(n: int = 10) -> str:
    rows = _conn.execute("SELECT what, who, ts FROM moments ORDER BY id DESC LIMIT ?", (n,)).fetchall()
    if not rows:
        return ""
    lines = ["Memorable moments:"]
    for what, who_json, ts in reversed(rows):
        who = json.loads(who_json)
        lines.append(f"- [{ts}] {what} (with {', '.join(who)})")
    return "\n".join(lines)


def get_all_users_summary() -> str:
    rows = _conn.execute("SELECT name, message_count, last_seen, topics FROM users ORDER BY message_count DESC LIMIT 30").fetchall()
    if not rows:
        return ""
    lines = ["People I know:"]
    for name, count, last_seen, topics_json in rows:
        topics = json.loads(topics_json or "[]")[-5:]
        topic_str = f" — into: {', '.join(topics)}" if topics else ""
        lines.append(f"- {name} ({count} msgs, last {last_seen}){topic_str}")
    return "\n".join(lines)


def get_all_context(user_name: str) -> str:
    parts = []
    parts.append(get_user_context(user_name))
    users = get_all_users_summary()
    if users:
        parts.append(users)
    moments = get_recent_moments(5)
    if moments:
        parts.append(moments)
    return "\n\n".join(parts)
