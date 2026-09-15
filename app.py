from flask import Flask, jsonify, request, send_from_directory
import sqlite3
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "vyapara_track.db")

app = Flask(__name__, static_folder=BASE, static_url_path="")

DEFAULT = {
    "products": [],
    "sales": [],
    "pending": [],
    "expenses": [],
    "cash": [],
    "drafts": {},
    "account": {
        "username": "admin",
        "password": "1234"
    }
}

DATABASE_URL = os.environ.get("DATABASE_URL")


# ---------- SQLite fallback for local development ----------

def sqlite_conn():
    c = sqlite3.connect(DB_PATH)
    c.execute("""
        CREATE TABLE IF NOT EXISTS app_state (
            id INTEGER PRIMARY KEY CHECK(id=1),
            data TEXT NOT NULL
        )
    """)
    return c


# ---------- PostgreSQL / Supabase ----------

def postgres_conn():
    import psycopg

    url = DATABASE_URL

    if "sslmode=" not in url:
        separator = "&" if "?" in url else "?"
        url += separator + "sslmode=require"

    return psycopg.connect(url)


def get_state():

    # Supabase PostgreSQL
    if DATABASE_URL:
        c = postgres_conn()

        with c.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS app_state (
                    id INTEGER PRIMARY KEY,
                    data JSONB NOT NULL
                )
            """)

            cur.execute(
                "SELECT data FROM app_state WHERE id = 1"
            )

            row = cur.fetchone()

            if not row:
                cur.execute(
                    "INSERT INTO app_state (id, data) VALUES (%s, %s)",
                    (1, json.dumps(DEFAULT))
                )
                c.commit()
                data = DEFAULT
            else:
                data = row[0]

        c.close()
        return data

    # Local SQLite
    c = sqlite_conn()

    row = c.execute(
        "SELECT data FROM app_state WHERE id=1"
    ).fetchone()

    if not row:
        c.execute(
            "INSERT INTO app_state(id,data) VALUES(1,?)",
            (json.dumps(DEFAULT),)
        )
        c.commit()
        data = DEFAULT
    else:
        data = json.loads(row[0])

    c.close()
    return data


def save_state(data):

    state = {
        k: data.get(k, DEFAULT[k])
        for k in DEFAULT
    }

    # Supabase PostgreSQL
    if DATABASE_URL:
        c = postgres_conn()

        with c.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS app_state (
                    id INTEGER PRIMARY KEY,
                    data JSONB NOT NULL
                )
            """)

            cur.execute("""
                INSERT INTO app_state (id, data)
                VALUES (%s, %s)
                ON CONFLICT (id)
                DO UPDATE SET data = EXCLUDED.data
            """, (1, json.dumps(state)))

        c.commit()
        c.close()
        return

    # Local SQLite
    c = sqlite_conn()

    c.execute("""
        INSERT INTO app_state(id,data)
        VALUES(1,?)
        ON CONFLICT(id)
        DO UPDATE SET data=excluded.data
    """, (
        json.dumps(state, separators=(",", ":")),
    ))

    c.commit()
    c.close()


# ---------- Routes ----------

@app.get("/")
def index():
    return send_from_directory(BASE, "index.html")


@app.get("/api/state")
def state_get():
    try:
        return jsonify(get_state())
    except Exception as e:
        return jsonify({
            "error": "Database read failed",
            "details": str(e)
        }), 500


@app.post("/api/state")
def state_post():

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "error": "Invalid state"
        }), 400

    try:
        save_state(data)

        return jsonify({
            "ok": True,
            "database": "supabase-postgresql"
            if DATABASE_URL
            else "local-sqlite"
        })

    except Exception as e:
        return jsonify({
            "error": "Database save failed",
            "details": str(e)
        }), 500


@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "app": "Vyapara Track",
        "database": "supabase-postgresql"
        if DATABASE_URL
        else "local-sqlite"
    })


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
    )
