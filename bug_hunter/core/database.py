"""SQLite database for engagement and run metadata."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4


DB_PATH: Optional[str] = None


def _get_db_path() -> str:
    if DB_PATH:
        return DB_PATH
    return "./audit_output/db.sqlite"


@contextmanager
def get_db():
    """Context manager for database connections."""
    conn = sqlite3.connect(_get_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Optional[str] = None) -> None:
    """Initialize the database schema."""
    global DB_PATH
    if db_path:
        DB_PATH = db_path
    Path(_get_db_path()).parent.mkdir(parents=True, exist_ok=True)

    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS engagements (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL CHECK(type IN ('source_code', 'black_box')),
                status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'completed')),
                config TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                cost_total_usd REAL DEFAULT 0.0
            );

            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                engagement_id TEXT NOT NULL REFERENCES engagements(id),
                run_number INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending', 'running', 'completed', 'failed', 'paused')),
                run_type TEXT NOT NULL DEFAULT 'initial'
                    CHECK(run_type IN ('initial', 'rehunt')),
                rehunt_target TEXT,
                current_stage TEXT,
                pipeline_state TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                cost_usd REAL DEFAULT 0.0,
                UNIQUE(engagement_id, run_number)
            );

            CREATE TABLE IF NOT EXISTS stage_results (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(id),
                stage_name TEXT NOT NULL,
                stage_order INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending', 'running', 'completed', 'failed', 'skipped')),
                input_count INTEGER DEFAULT 0,
                output_count INTEGER DEFAULT 0,
                error_message TEXT,
                started_at TEXT,
                completed_at TEXT,
                duration_ms INTEGER,
                cost_usd REAL DEFAULT 0.0,
                metadata TEXT
            );

            CREATE TABLE IF NOT EXISTS bugs (
                id TEXT PRIMARY KEY,
                external_id TEXT NOT NULL,
                engagement_id TEXT NOT NULL REFERENCES engagements(id),
                run_id TEXT NOT NULL REFERENCES runs(id),
                bug_data TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'found'
                    CHECK(status IN ('found', 'in_scope', 'validated', 'expanded',
                                     'confirmed', 'informational', 'cannot_validate',
                                     'out_of_scope', 'discarded', 'triage_failed')),
                current_stage TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chains (
                id TEXT PRIMARY KEY,
                external_id TEXT NOT NULL,
                engagement_id TEXT NOT NULL REFERENCES engagements(id),
                run_id TEXT,
                chain_data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_runs_engagement ON runs(engagement_id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_run_per_engagement
                ON runs(engagement_id) WHERE status = 'running';
            CREATE INDEX IF NOT EXISTS idx_stage_results_run ON stage_results(run_id);
            CREATE INDEX IF NOT EXISTS idx_bugs_engagement ON bugs(engagement_id);
            CREATE INDEX IF NOT EXISTS idx_bugs_run ON bugs(run_id);
            CREATE INDEX IF NOT EXISTS idx_bugs_status ON bugs(status);
            CREATE INDEX IF NOT EXISTS idx_chains_engagement ON chains(engagement_id);
        """)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Engagement CRUD ---

def create_engagement(name: str, eng_type: str, config: dict) -> dict:
    eng_id = str(uuid4())
    now = _now()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO engagements (id, name, type, config, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (eng_id, name, eng_type, json.dumps(config), now, now),
        )
    return get_engagement(eng_id)


def get_engagement(eng_id: str) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM engagements WHERE id = ?", (eng_id,)).fetchone()
    if not row:
        return None
    result = dict(row)
    result["config"] = json.loads(result["config"])
    return result


def list_engagements() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM engagements ORDER BY created_at DESC").fetchall()
    results = []
    for row in rows:
        r = dict(row)
        r["config"] = json.loads(r["config"])
        results.append(r)
    return results


def update_engagement(eng_id: str, **kwargs) -> Optional[dict]:
    allowed = {"name", "status", "cost_total_usd"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return get_engagement(eng_id)
    updates["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [eng_id]
    with get_db() as conn:
        conn.execute(f"UPDATE engagements SET {set_clause} WHERE id = ?", values)
    return get_engagement(eng_id)


# --- Run CRUD ---

def create_run(engagement_id: str, run_type: str = "initial", rehunt_target: str = None) -> dict:
    run_id = str(uuid4())
    now = _now()
    with get_db() as conn:
        run_number = conn.execute(
            "SELECT COALESCE(MAX(run_number), 0) + 1 FROM runs WHERE engagement_id = ?",
            (engagement_id,),
        ).fetchone()[0]
        conn.execute(
            """INSERT INTO runs (id, engagement_id, run_number, run_type, rehunt_target, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (run_id, engagement_id, run_number, run_type, rehunt_target, now, now),
        )
    return get_run(run_id)


def get_run(run_id: str) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if not row:
        return None
    result = dict(row)
    if result.get("pipeline_state"):
        result["pipeline_state"] = json.loads(result["pipeline_state"])
    return result


def list_runs(engagement_id: str) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM runs WHERE engagement_id = ? ORDER BY run_number", (engagement_id,)
        ).fetchall()
    results = []
    for row in rows:
        r = dict(row)
        if r.get("pipeline_state"):
            r["pipeline_state"] = json.loads(r["pipeline_state"])
        results.append(r)
    return results


def update_run(run_id: str, **kwargs) -> Optional[dict]:
    allowed = {"status", "current_stage", "pipeline_state", "completed_at", "cost_usd"}
    updates = {}
    for k, v in kwargs.items():
        if k not in allowed:
            continue
        if k == "pipeline_state" and isinstance(v, dict):
            updates[k] = json.dumps(v)
        else:
            updates[k] = v
    if not updates:
        return get_run(run_id)
    updates["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [run_id]
    with get_db() as conn:
        conn.execute(f"UPDATE runs SET {set_clause} WHERE id = ?", values)
    return get_run(run_id)


# --- Stage Results CRUD ---

def create_stage_result(run_id: str, stage_name: str, stage_order: int) -> dict:
    sr_id = str(uuid4())
    with get_db() as conn:
        conn.execute(
            """INSERT INTO stage_results (id, run_id, stage_name, stage_order)
               VALUES (?, ?, ?, ?)""",
            (sr_id, run_id, stage_name, stage_order),
        )
    return get_stage_result(sr_id)


def get_stage_result(sr_id: str) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM stage_results WHERE id = ?", (sr_id,)).fetchone()
    if not row:
        return None
    result = dict(row)
    if result.get("metadata"):
        result["metadata"] = json.loads(result["metadata"])
    return result


def list_stage_results(run_id: str) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM stage_results WHERE run_id = ? ORDER BY stage_order", (run_id,)
        ).fetchall()
    results = []
    for row in rows:
        r = dict(row)
        if r.get("metadata"):
            r["metadata"] = json.loads(r["metadata"])
        results.append(r)
    return results


def update_stage_result(sr_id: str, **kwargs) -> Optional[dict]:
    allowed = {"status", "input_count", "output_count", "error_message",
               "started_at", "completed_at", "duration_ms", "cost_usd", "metadata"}
    updates = {}
    for k, v in kwargs.items():
        if k not in allowed:
            continue
        if k == "metadata" and isinstance(v, dict):
            updates[k] = json.dumps(v)
        else:
            updates[k] = v
    if not updates:
        return get_stage_result(sr_id)
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [sr_id]
    with get_db() as conn:
        conn.execute(f"UPDATE stage_results SET {set_clause} WHERE id = ?", values)
    return get_stage_result(sr_id)


# --- Bug CRUD ---

def create_bug(engagement_id: str, run_id: str, bug_data: dict) -> dict:
    db_id = str(uuid4())
    external_id = bug_data.get("id", db_id)
    now = _now()
    with get_db() as conn:
        conn.execute(
            """INSERT INTO bugs (id, external_id, engagement_id, run_id, bug_data, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (db_id, external_id, engagement_id, run_id, json.dumps(bug_data), now, now),
        )
    return get_bug(db_id)


def get_bug(bug_id: str) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM bugs WHERE id = ?", (bug_id,)).fetchone()
    if not row:
        return None
    result = dict(row)
    result["bug_data"] = json.loads(result["bug_data"])
    return result


def list_bugs(engagement_id: str, status: str = None,
              run_id: str = None) -> list[dict]:
    with get_db() as conn:
        query = "SELECT * FROM bugs WHERE engagement_id = ?"
        params: list = [engagement_id]
        if status:
            query += " AND status = ?"
            params.append(status)
        if run_id:
            query += " AND run_id = ?"
            params.append(run_id)
        query += " ORDER BY created_at"
        rows = conn.execute(query, params).fetchall()
    results = []
    for row in rows:
        r = dict(row)
        r["bug_data"] = json.loads(r["bug_data"])
        results.append(r)
    return results


def update_bug(bug_id: str, **kwargs) -> Optional[dict]:
    allowed = {"bug_data", "status", "current_stage"}
    updates = {}
    for k, v in kwargs.items():
        if k not in allowed:
            continue
        if k == "bug_data" and isinstance(v, dict):
            updates[k] = json.dumps(v)
        else:
            updates[k] = v
    if not updates:
        return get_bug(bug_id)
    updates["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [bug_id]
    with get_db() as conn:
        conn.execute(f"UPDATE bugs SET {set_clause} WHERE id = ?", values)
    return get_bug(bug_id)


# --- Chain CRUD ---

def create_chain(engagement_id: str, chain_data: dict, run_id: str = None) -> dict:
    """Create a chain, or update an existing one with the same bug_ids."""
    bug_ids = sorted(chain_data.get("bug_ids", []))
    bug_ids_key = json.dumps(bug_ids)

    # Check for existing chain with same bug_ids in this engagement
    existing = list_chains(engagement_id)
    for ex in existing:
        ex_bug_ids = sorted(ex["chain_data"].get("bug_ids", []))
        if json.dumps(ex_bug_ids) == bug_ids_key:
            # Update existing chain with newer evidence
            now = _now()
            with get_db() as conn:
                conn.execute(
                    "UPDATE chains SET chain_data = ?, run_id = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(chain_data), run_id, now, ex["id"]),
                )
            return get_chain(ex["id"])

    db_id = str(uuid4())
    external_id = chain_data.get("id", db_id)
    now = _now()
    with get_db() as conn:
        conn.execute(
            """INSERT INTO chains (id, external_id, engagement_id, run_id, chain_data, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (db_id, external_id, engagement_id, run_id, json.dumps(chain_data), now, now),
        )
    return get_chain(db_id)


def get_chain(chain_id: str) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM chains WHERE id = ?", (chain_id,)).fetchone()
    if not row:
        return None
    result = dict(row)
    result["chain_data"] = json.loads(result["chain_data"])
    return result


def list_chains(engagement_id: str) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM chains WHERE engagement_id = ? ORDER BY created_at",
            (engagement_id,),
        ).fetchall()
    results = []
    for row in rows:
        r = dict(row)
        r["chain_data"] = json.loads(r["chain_data"])
        results.append(r)
    return results
