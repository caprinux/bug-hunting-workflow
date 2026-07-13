"""Minimal account service (test fixture for the live e2e smoke test).

This app has real, unannotated security weaknesses for the hunter to discover on
its own. Do not deploy it.
"""

from __future__ import annotations

import sqlite3

from flask import Flask, jsonify, request

app = Flask(__name__)


def _db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, ssn TEXT)")
    conn.execute("INSERT INTO users VALUES (1, 'alice', '111-11-1111')")
    conn.execute("INSERT INTO users VALUES (2, 'bob', '222-22-2222')")
    return conn


@app.route("/login")
def login():
    username = request.args.get("username", "")
    conn = _db()
    query = "SELECT id, name FROM users WHERE name = '%s'" % username
    row = conn.execute(query).fetchone()
    return jsonify({"user": row})


@app.route("/account/<int:user_id>")
def account(user_id: int):
    conn = _db()
    row = conn.execute(
        "SELECT id, name, ssn FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    return jsonify({"id": row[0], "name": row[1], "ssn": row[2]})


if __name__ == "__main__":
    app.run(port=5000)
