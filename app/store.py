"""SQLite 存储。WAL + 每操作短连接，个人向足够且无需连接池。

所有数据按 scope 隔离：scope 就是 agent 名（chat / worker），
这样切后端不会串上下文，也不会把 agent 的记忆灌进闲聊。
旧库（无 scope 列）会在 init 时自动补列并归到 chat。
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
import time
import uuid

DEFAULT_SCOPE = "chat"

SCHEMA = """
CREATE TABLE IF NOT EXISTS message (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    scope     TEXT NOT NULL DEFAULT 'chat',
    channel   TEXT NOT NULL,
    conv_id   TEXT NOT NULL,
    user_id   TEXT NOT NULL,
    role      TEXT NOT NULL,
    content   TEXT NOT NULL,
    ts        INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_message_conv ON message(scope, channel, conv_id, ts);

CREATE TABLE IF NOT EXISTS session (
    key        TEXT PRIMARY KEY,
    summary    TEXT NOT NULL DEFAULT '',
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS profile (
    user_id    TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS task (
    id         TEXT PRIMARY KEY,
    scope      TEXT NOT NULL DEFAULT 'chat',
    channel    TEXT NOT NULL,
    conv_id    TEXT NOT NULL,
    status     TEXT NOT NULL,
    started_at INTEGER NOT NULL,
    ended_at   INTEGER,
    error      TEXT
);

CREATE TABLE IF NOT EXISTS agent_session (
    agent       TEXT NOT NULL,
    session_key TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    updated_at  INTEGER NOT NULL,
    PRIMARY KEY (agent, session_key)
);
"""


class Store:
    def __init__(self, path: str | pathlib.Path) -> None:
        self.path = pathlib.Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def init(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """给旧库补 scope 列（旧数据视为 chat）。"""
        for table in ("message", "task"):
            cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            if "scope" not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN scope TEXT NOT NULL DEFAULT 'chat'")

        # 会话映射改用 (agent, session_key)：多进程各自持自己的 session_id
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(agent_session)")}
        if cols and "agent" not in cols:
            conn.execute("DROP TABLE agent_session")
            conn.execute(
                "CREATE TABLE agent_session ("
                "  agent       TEXT NOT NULL,"
                "  session_key TEXT NOT NULL,"
                "  session_id  TEXT NOT NULL,"
                "  updated_at  INTEGER NOT NULL,"
                "  PRIMARY KEY (agent, session_key)"
                ")"
            )

    @staticmethod
    def _summary_key(scope: str, channel: str, conv_id: str) -> str:
        return f"{scope}|{channel}|{conv_id}"

    @staticmethod
    def _profile_key(scope: str, user_id: str) -> str:
        return f"{scope}:{user_id}"

    # ---------- message ----------

    def add_message(
        self,
        channel: str,
        conv_id: str,
        user_id: str,
        role: str,
        content: str,
        scope: str = DEFAULT_SCOPE,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO message(scope, channel, conv_id, user_id, role, content, ts) "
                "VALUES (?,?,?,?,?,?,?)",
                (scope, channel, conv_id, user_id, role, content, int(time.time())),
            )

    def history(self, channel: str, conv_id: str, limit: int = 12, scope: str = DEFAULT_SCOPE) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content FROM message WHERE scope=? AND channel=? AND conv_id=? "
                "ORDER BY ts DESC, id DESC LIMIT ?",
                (scope, channel, conv_id, limit),
            ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in rows][::-1]

    def searchable(
        self,
        channel: str,
        conv_id: str,
        scope: str = DEFAULT_SCOPE,
        exclude_recent: int = 12,
        limit: int = 2000,
    ) -> list[dict]:
        """供相关性召回：跳过最近 N 条（那些本来就在上下文里）。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, content FROM message "
                "WHERE scope=? AND channel=? AND conv_id=? AND role='user' "
                "ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
                (scope, channel, conv_id, limit, exclude_recent),
            ).fetchall()
        return [{"id": r["id"], "content": r["content"]} for r in rows]

    def count_messages(self, channel: str, conv_id: str, scope: str = DEFAULT_SCOPE) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM message WHERE scope=? AND channel=? AND conv_id=?",
                (scope, channel, conv_id),
            ).fetchone()
        return int(row["n"])

    def clear(self, channel: str, conv_id: str, scope: str = DEFAULT_SCOPE) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM message WHERE scope=? AND channel=? AND conv_id=?",
                (scope, channel, conv_id),
            )
            conn.execute("DELETE FROM session WHERE key=?", (self._summary_key(scope, channel, conv_id),))

    def drop_oldest(self, channel: str, conv_id: str, keep: int, scope: str = DEFAULT_SCOPE) -> list[dict]:
        """保留最近 keep 条，返回被丢弃的部分（供摘要使用）。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, role, content FROM message WHERE scope=? AND channel=? AND conv_id=? "
                "ORDER BY ts DESC, id DESC LIMIT -1 OFFSET ?",
                (scope, channel, conv_id, keep),
            ).fetchall()
            if rows:
                conn.execute(
                    f"DELETE FROM message WHERE id IN ({','.join('?' * len(rows))})",
                    [r["id"] for r in rows],
                )
        return [{"role": r["role"], "content": r["content"]} for r in rows]

    # ---------- session ----------

    def get_summary(self, channel: str, conv_id: str, scope: str = DEFAULT_SCOPE) -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT summary FROM session WHERE key=?", (self._summary_key(scope, channel, conv_id),)
            ).fetchone()
        return row["summary"] if row else ""

    def set_summary(self, channel: str, conv_id: str, summary: str, scope: str = DEFAULT_SCOPE) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO session(key, summary, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET summary=excluded.summary, updated_at=excluded.updated_at",
                (self._summary_key(scope, channel, conv_id), summary, int(time.time())),
            )

    # ---------- profile ----------

    def profile(self, user_id: str, scope: str = DEFAULT_SCOPE) -> dict:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM profile WHERE user_id=?", (self._profile_key(scope, user_id),)
            ).fetchone()
            if row is None and scope != DEFAULT_SCOPE:
                # 兼容迁移前的写法：老数据只有裸 user_id
                row = conn.execute(
                    "SELECT data FROM profile WHERE user_id=?", (user_id,)
                ).fetchone()
        if not row:
            return {}
        try:
            return json.loads(row["data"])
        except json.JSONDecodeError:
            return {}

    def merge_profile(self, user_id: str, patch: dict, scope: str = DEFAULT_SCOPE) -> dict:
        data = self.profile(user_id, scope)
        data.update(patch)
        key = self._profile_key(scope, user_id)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO profile(user_id, data, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
                (key, json.dumps(data, ensure_ascii=False), int(time.time())),
            )
        return data

    # ---------- agent session（会话记忆归 agent 管，这里只存映射） ----------

    def get_agent_session(self, agent: str, session_key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT session_id FROM agent_session WHERE agent=? AND session_key=?",
                (agent, session_key),
            ).fetchone()
        return row["session_id"] if row else None

    def set_agent_session(self, agent: str, session_key: str, session_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO agent_session(agent, session_key, session_id, updated_at) VALUES (?,?,?,?) "
                "ON CONFLICT(agent, session_key) DO UPDATE SET "
                "session_id=excluded.session_id, updated_at=excluded.updated_at",
                (agent, session_key, session_id, int(time.time())),
            )

    def clear_agent_session(self, agent: str, session_key: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM agent_session WHERE agent=? AND session_key=?", (agent, session_key)
            )

    # ---------- task ----------

    def task_start(self, channel: str, conv_id: str, scope: str = DEFAULT_SCOPE) -> str:
        task_id = uuid.uuid4().hex[:12]
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO task(id, scope, channel, conv_id, status, started_at) VALUES (?,?,?,?,?,?)",
                (task_id, scope, channel, conv_id, "running", int(time.time())),
            )
        return task_id

    def task_finish(self, task_id: str, status: str, error: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE task SET status=?, ended_at=?, error=? WHERE id=?",
                (status, int(time.time()), error, task_id),
            )
