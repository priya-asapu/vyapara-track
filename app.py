from flask import Flask, jsonify, request, send_from_directory
import sqlite3
import json
import os
import traceback

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


# =========================================================
# SQLITE - LOCAL DEVELOPMENT FALLBACK
# =========================================================

def sqlite_conn():
    c = sqlite3.connect(DB_PATH)

    c.execute("""
        CREATE TABLE IF NOT EXISTS app_state (
            id INTEGER PRIMARY KEY CHECK(id=1),
            data TEXT NOT NULL
        )
    """)

    return c


# =========================================================
# POSTGRES / SUPABASE CONNECTION
# =========================================================

def postgres_conn():

    import psycopg

    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable is missing")

    url = DATABASE_URL

    # Supabase requires SSL
    if "sslmode=" not in url:
        separator = "&" if "?" in url else "?"
        url += separator + "sslmode=require"

    print("========================================")
    print("Connecting to Supabase PostgreSQL...")
    print("DATABASE_URL is present:", bool(DATABASE_URL))
    print("========================================")

    connection = psycopg.connect(
        url,
        connect_timeout=10
    )

    print("SUCCESS: Connected to Supabase PostgreSQL")
    print("========================================")

    return connection


# =========================================================
# GET STATE
# =========================================================

def get_state():

    # -------------------------
    # SUPABASE
    # -------------------------

    if DATABASE_URL:

        try:

            c = postgres_conn()

            with c.cursor() as cur:

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS app_state (
                        id INTEGER PRIMARY KEY,
                        data JSONB NOT NULL
                    )
                """)

                cur.execute("""
                    SELECT data
                    FROM app_state
                    WHERE id = 1
                """)

                row = cur.fetchone()

                if not row:

                    print("No app_state found. Creating initial state...")

                    cur.execute("""
                        INSERT INTO app_state (id, data)
                        VALUES (%s, %s)
                    """, (
                        1,
                        json.dumps(DEFAULT)
                    ))

                    c.commit()

                    data = DEFAULT

                else:

                    data = row[0]

            c.close()

            print("SUCCESS: State loaded from Supabase")

            return data

        except Exception as e:

            print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
            print("SUPABASE READ ERROR")
            print(str(e))
            print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")

            traceback.print_exc()

            raise


    # -------------------------
    # SQLITE FALLBACK
    # -------------------------

    c = sqlite_conn()

    row = c.execute("""
        SELECT data
        FROM app_state
        WHERE id = 1
    """).fetchone()

    if not row:

        c.execute("""
            INSERT INTO app_state (id, data)
            VALUES (1, ?)
        """, (
            json.dumps(DEFAULT),
        ))

        c.commit()

        data = DEFAULT

    else:

        data = json.loads(row[0])

    c.close()

    return data


# =========================================================
# SAVE STATE
# =========================================================

def save_state(data):

    state = {
        k: data.get(k, DEFAULT[k])
        for k in DEFAULT
    }

    # -------------------------
    # SUPABASE
    # -------------------------

    if DATABASE_URL:

        try:

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
                """, (
                    1,
                    json.dumps(state)
                ))

            c.commit()

            c.close()

            print("SUCCESS: State saved to Supabase")

            return

        except Exception as e:

            print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
            print("SUPABASE SAVE ERROR")
            print(str(e))
            print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")

            traceback.print_exc()

            raise


    # -------------------------
    # SQLITE FALLBACK
    # -------------------------

    c = sqlite_conn()

    c.execute("""
        INSERT INTO app_state (id, data)
        VALUES (1, ?)
        ON CONFLICT(id)
        DO UPDATE SET data = excluded.data
    """, (
        json.dumps(state, separators=(",", ":")),
    ))

    c.commit()

    c.close()


# =========================================================
# HOME
# =========================================================

@app.get("/")
def index():

    return send_from_directory(
        BASE,
        "index.html"
    )


# =========================================================
# GET STATE API
# =========================================================

@app.get("/api/state")
def state_get():

    print("========================================")
    print("GET /api/state")
    print("========================================")

    try:

        data = get_state()

        return jsonify(data)

    except Exception as e:

        print("API STATE ERROR:")
        print(str(e))

        return jsonify({
            "error": "Database read failed",
            "details": str(e)
        }), 500


# =========================================================
# SAVE STATE API
# =========================================================

@app.post("/api/state")
def state_post():

    print("========================================")
    print("POST /api/state")
    print("========================================")

    data = request.get_json(silent=True)

    if not isinstance(data, dict):

        return jsonify({
            "error": "Invalid state"
        }), 400

    try:

        save_state(data)

        return jsonify({
            "ok": True,
            "database": (
                "supabase-postgresql"
                if DATABASE_URL
                else "local-sqlite"
            )
        })

    except Exception as e:

        print("API SAVE ERROR:")
        print(str(e))

        return jsonify({
            "error": "Database save failed",
            "details": str(e)
        }), 500


# =========================================================
# HEALTH CHECK
# =========================================================

@app.get("/health")
def health():

    result = {
        "ok": True,
        "app": "Vyapara Track",
        "database": (
            "supabase-postgresql"
            if DATABASE_URL
            else "local-sqlite"
        )
    }

    # Actually test Supabase connection
    if DATABASE_URL:

        try:

            c = postgres_conn()

            with c.cursor() as cur:

                cur.execute("SELECT 1")

                cur.fetchone()

            c.close()

            result["database_connection"] = "working"

            print("HEALTH CHECK: Supabase connection WORKING")

        except Exception as e:

            result["ok"] = False
            result["database_connection"] = "failed"
            result["database_error"] = str(e)

            print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
            print("HEALTH CHECK: SUPABASE CONNECTION FAILED")
            print(str(e))
            print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")

            traceback.print_exc()

            return jsonify(result), 500

    else:

        result["database_connection"] = "local-sqlite"

    return jsonify(result)


# =========================================================
# START SERVER
# =========================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
    )
