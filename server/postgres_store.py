"""Postgres persistence for the existing parameterized SQLite-style repository.

Only the repository's small SQL dialect is adapted; values stay bound parameters.
The local Windows installation continues to use SQLite without this dependency.
"""
import re
import sqlite3
import threading
from collections.abc import Mapping
from contextlib import contextmanager

_pools = {}
_pool_lock = threading.Lock()
SERIAL_TABLES = {"photos", "transcripts", "notes", "leaves", "chunks", "audit"}


class Row(Mapping):
    def __init__(self, names, values):
        self._names, self._values = tuple(names), tuple(values)
        self._dict = dict(zip(self._names, self._values))

    def __getitem__(self, key):
        return self._values[key] if isinstance(key, int) else self._dict[key]

    def __iter__(self):
        return iter(self._names)

    def __len__(self):
        return len(self._names)


class Result:
    def __init__(self, cursor=None, rows=None, lastrowid=None):
        self.cursor, self.rows, self.lastrowid = cursor, iter(rows or []), lastrowid
        self.rowcount = cursor.rowcount if cursor is not None else 0

    def fetchone(self):
        if self.cursor is None:
            return next(self.rows, None)
        value = self.cursor.fetchone()
        return None if value is None else Row([c.name for c in self.cursor.description], value)

    def fetchall(self):
        return list(self)

    def __iter__(self):
        while (row := self.fetchone()) is not None:
            yield row


def schema_sql(sql):
    sql = re.sub(r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT", "BIGSERIAL PRIMARY KEY", sql, flags=re.I)
    # SQLite REAL is an eight-byte float. PostgreSQL REAL is only four bytes.
    return re.sub(r"\bREAL\b", "DOUBLE PRECISION", sql, flags=re.I)


class Connection:
    def __init__(self, connection):
        self.raw = connection

    def execute(self, sql, params=()):
        if re.match(r"\s*PRAGMA\s+table_info", sql, re.I):
            table = re.search(r"\(([A-Za-z_][A-Za-z_0-9]*)\)", sql).group(1)
            cursor = self.raw.execute("SELECT ordinal_position-1 AS cid,column_name AS name,data_type AS type,"
                                      "CASE WHEN is_nullable='NO' THEN 1 ELSE 0 END AS notnull,column_default AS dflt_value,"
                                      "0 AS pk FROM information_schema.columns WHERE table_schema=current_schema() "
                                      "AND table_name=%s ORDER BY ordinal_position", (table,))
            return Result(cursor)
        if re.match(r"\s*(PRAGMA|BEGIN\b)", sql, re.I):
            return Result()
        sql = schema_sql(sql)
        ignore = bool(re.search(r"INSERT\s+OR\s+IGNORE", sql, re.I))
        sql = re.sub(r"INSERT\s+OR\s+IGNORE", "INSERT", sql, flags=re.I).strip().rstrip(';')
        if ignore:
            sql += " ON CONFLICT DO NOTHING"
        insert = re.match(r"INSERT\s+INTO\s+([A-Za-z_][A-Za-z_0-9]*)", sql, re.I)
        returns_id = insert and insert.group(1) in SERIAL_TABLES
        if returns_id:
            sql += " RETURNING id"
        if params:
            sql = sql.replace('%', '%%').replace('?', '%s')
        try:
            cursor = self.raw.execute(sql, params or None)
            lastrowid = None
            if returns_id:
                row = cursor.fetchone()
                lastrowid = row[0] if row else None
            return Result(cursor, lastrowid=lastrowid)
        except Exception as error:
            # Keep the existing domain-level duplicate handling unchanged.
            if (getattr(error, 'sqlstate', None) or '').startswith('23'):
                raise sqlite3.IntegrityError('이미 존재하거나 참조할 수 없는 데이터입니다.') from error
            raise

    def executemany(self, sql, values):
        result = Result()
        for value in values:
            result = self.execute(sql, value)
        return result

    def executescript(self, script):
        for sql in script.split(';'):
            if sql.strip():
                self.execute(sql)


@contextmanager
def connect(url, *, shared=False, schema=None):
    from psycopg_pool import ConnectionPool
    with _pool_lock:
        if url not in _pools:
            _pools[url] = ConnectionPool(url, min_size=0, max_size=4, timeout=15,
                                         check=ConnectionPool.check_connection,
                                         kwargs={"connect_timeout": 10}, open=True)
        pool = _pools[url]
    with pool.connection() as raw:
        with raw.transaction():
            if schema:
                if not re.fullmatch(r"demo_[0-9a-f]{32}", schema):
                    raise ValueError("Invalid workspace schema")
                from psycopg import sql
                raw.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema)))
            # Serialize each short repository transaction across function instances.
            # Model inference is performed outside these transactions.
            # Readers can run together; state-changing transactions retain the
            # existing exclusive lock and still wait for readers to finish.
            lock = 'pg_advisory_xact_lock_shared' if shared else 'pg_advisory_xact_lock'
            raw.execute(f"SELECT {lock}(71117002)")
            yield Connection(raw)
