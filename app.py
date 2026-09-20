from flask import Flask, jsonify, request, send_from_directory, session
import sqlite3
import json
import os
import traceback
from datetime import timedelta
from werkzeug.security import generate_password_hash, check_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "vyapara_track.db")

app = Flask(__name__, static_folder=BASE, static_url_path="")
app.secret_key = os.environ.get("SECRET_KEY", "vyapara-track-change-this-secret")
app.permanent_session_lifetime = timedelta(days=365)

DEFAULT = {
    "products": [],
    "sales": [],
    "pending": [],
    "expenses": [],
    "cash": [],
    "drafts": {},
    "account": {
        "username": "",
        "displayName": "",
        "email": "",
        "phone": ""
    }
}

DATABASE_URL = os.environ.get("DATABASE_URL")


# =========================================================
# HELPERS
# =========================================================

def empty_state(username):
    state = json.loads(json.dumps(DEFAULT))
    state["account"]["username"] = username
    state["account"]["displayName"] = username
    return state


def clean_state(data, username):
    state = json.loads(json.dumps(DEFAULT))
    if isinstance(data, dict):
        for key in DEFAULT:
            if key in data:
                state[key] = data[key]

    state["account"] = state.get("account") or {}
    state["account"]["username"] = username
    state["account"].pop("password", None)

    if not state["account"].get("displayName"):
        state["account"]["displayName"] = username

    return state


# =========================================================
# SQLITE - LOCAL DEVELOPMENT FALLBACK
# =========================================================

def sqlite_conn():
    c = sqlite3.connect(DB_PATH)
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_state (
            username TEXT PRIMARY KEY,
            data TEXT NOT NULL
        )
    """)
    return c


def sqlite_migrate_legacy():
    c = sqlite_conn()

    # Old single-user table from the previous version.
    c.execute("""
        CREATE TABLE IF NOT EXISTS app_state (
            id INTEGER PRIMARY KEY CHECK(id=1),
            data TEXT NOT NULL
        )
    """)

    row = c.execute("SELECT data FROM app_state WHERE id=1").fetchone()

    if row:
        try:
            old = json.loads(row[0])
        except Exception:
            old = DEFAULT

        account = old.get("account") or {}
        username = str(account.get("username") or "admin").strip() or "admin"
        password = str(account.get("password") or "1234")

        exists = c.execute(
            "SELECT username FROM users WHERE username=?",
            (username,)
        ).fetchone()

        if not exists:
            c.execute(
                "INSERT INTO users(username,password_hash) VALUES(?,?)",
                (username, generate_password_hash(password))
            )

        state = clean_state(old, username)

        c.execute("""
            INSERT INTO user_state(username,data)
            VALUES(?,?)
            ON CONFLICT(username)
            DO UPDATE SET data=excluded.data
        """, (username, json.dumps(state)))

        c.commit()

    c.close()


# =========================================================
# POSTGRES / SUPABASE CONNECTION
# =========================================================

def postgres_conn():
    import psycopg

    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable is missing")

    url = DATABASE_URL

    if "sslmode=" not in url:
        separator = "&" if "?" in url else "?"
        url += separator + "sslmode=require"

    print("========================================")
    print("Connecting to Supabase PostgreSQL...")
    print("DATABASE_URL is present:", bool(DATABASE_URL))
    print("========================================")

    connection = psycopg.connect(url, connect_timeout=10)

    print("SUCCESS: Connected to Supabase PostgreSQL")
    print("========================================")

    return connection


def postgres_setup():
    c = postgres_conn()

    with c.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_state (
                username TEXT PRIMARY KEY,
                data JSONB NOT NULL
            )
        """)

        # Migrate the old one-row app_state table if it exists.
        cur.execute("""
            SELECT to_regclass('public.app_state')
        """)
        legacy_exists = cur.fetchone()[0] is not None

        if legacy_exists:
            cur.execute("""
                SELECT data
                FROM app_state
                WHERE id=1
            """)
            row = cur.fetchone()

            if row:
                old = row[0] if isinstance(row[0], dict) else json.loads(row[0])
                account = old.get("account") or {}
                username = str(account.get("username") or "admin").strip() or "admin"
                password = str(account.get("password") or "1234")

                cur.execute(
                    "SELECT username FROM users WHERE username=%s",
                    (username,)
                )
                exists = cur.fetchone()

                if not exists:
                    cur.execute(
                        "INSERT INTO users(username,password_hash) VALUES(%s,%s)",
                        (username, generate_password_hash(password))
                    )

                state = clean_state(old, username)

                cur.execute("""
                    INSERT INTO user_state(username,data)
                    VALUES(%s,%s)
                    ON CONFLICT(username)
                    DO UPDATE SET data=excluded.data
                """, (username, json.dumps(state)))

    c.commit()
    c.close()


# =========================================================
# USER / STATE DATABASE FUNCTIONS
# =========================================================

def ensure_database():
    if DATABASE_URL:
        postgres_setup()
    else:
        sqlite_migrate_legacy()


def create_user(username, password):
    username = username.strip()

    if not username or not password:
        raise ValueError("Username and password are required")

    if DATABASE_URL:
        c = postgres_conn()
        try:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT username FROM users WHERE lower(username)=lower(%s)",
                    (username,)
                )
                if cur.fetchone():
                    return False, "Username already exists"

                cur.execute(
                    "INSERT INTO users(username,password_hash) VALUES(%s,%s)",
                    (username, generate_password_hash(password))
                )

                cur.execute(
                    "INSERT INTO user_state(username,data) VALUES(%s,%s)",
                    (username, json.dumps(empty_state(username)))
                )

            c.commit()
        finally:
            c.close()
        return True, "Account created"

    c = sqlite_conn()
    try:
        exists = c.execute(
            "SELECT username FROM users WHERE lower(username)=lower(?)",
            (username,)
        ).fetchone()

        if exists:
            return False, "Username already exists"

        c.execute(
            "INSERT INTO users(username,password_hash) VALUES(?,?)",
            (username, generate_password_hash(password))
        )
        c.execute(
            "INSERT INTO user_state(username,data) VALUES(?,?)",
            (username, json.dumps(empty_state(username)))
        )
        c.commit()
    finally:
        c.close()

    return True, "Account created"


def authenticate_user(username, password):
    if DATABASE_URL:
        c = postgres_conn()
        try:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT username,password_hash FROM users WHERE lower(username)=lower(%s)",
                    (username,)
                )
                row = cur.fetchone()
        finally:
            c.close()
    else:
        c = sqlite_conn()
        try:
            row = c.execute(
                "SELECT username,password_hash FROM users WHERE lower(username)=lower(?)",
                (username,)
            ).fetchone()
        finally:
            c.close()

    if not row:
        return None

    real_username, password_hash = row

    if check_password_hash(password_hash, password):
        return real_username

    return None


def load_user_state(username):
    if DATABASE_URL:
        c = postgres_conn()
        try:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT data FROM user_state WHERE username=%s",
                    (username,)
                )
                row = cur.fetchone()
        finally:
            c.close()

        if not row:
            state = empty_state(username)
            save_user_state(username, state)
            return state

        data = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        return clean_state(data, username)

    c = sqlite_conn()
    try:
        row = c.execute(
            "SELECT data FROM user_state WHERE username=?",
            (username,)
        ).fetchone()
    finally:
        c.close()

    if not row:
        state = empty_state(username)
        save_user_state(username, state)
        return state

    return clean_state(json.loads(row[0]), username)


def save_user_state(username, data):
    state = clean_state(data, username)

    if DATABASE_URL:
        c = postgres_conn()
        try:
            with c.cursor() as cur:
                cur.execute("""
                    INSERT INTO user_state(username,data)
                    VALUES(%s,%s)
                    ON CONFLICT(username)
                    DO UPDATE SET data=excluded.data
                """, (username, json.dumps(state)))
            c.commit()
        finally:
            c.close()
        return

    c = sqlite_conn()
    try:
        c.execute("""
            INSERT INTO user_state(username,data)
            VALUES(?,?)
            ON CONFLICT(username)
            DO UPDATE SET data=excluded.data
        """, (username, json.dumps(state)))
        c.commit()
    finally:
        c.close()


def update_password(username, new_password):
    password_hash = generate_password_hash(new_password)

    if DATABASE_URL:
        c = postgres_conn()
        try:
            with c.cursor() as cur:
                cur.execute(
                    "UPDATE users SET password_hash=%s WHERE username=%s",
                    (password_hash, username)
                )
                changed = cur.rowcount
            c.commit()
        finally:
            c.close()
        return changed == 1

    c = sqlite_conn()
    try:
        cur = c.execute(
            "UPDATE users SET password_hash=? WHERE username=?",
            (password_hash, username)
        )
        c.commit()
        return cur.rowcount == 1
    finally:
        c.close()


def rename_user(old_username, new_username, new_password):
    new_username = new_username.strip()

    if not new_username or not new_password:
        return False, "Username and password are required"

    if DATABASE_URL:
        c = postgres_conn()
        try:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT username FROM users WHERE lower(username)=lower(%s) AND username<>%s",
                    (new_username, old_username)
                )
                if cur.fetchone():
                    return False, "Username already exists"

                cur.execute(
                    "SELECT data FROM user_state WHERE username=%s",
                    (old_username,)
                )
                row = cur.fetchone()

                if not row:
                    return False, "Account data not found"

                state = row[0] if isinstance(row[0], dict) else json.loads(row[0])
                state = clean_state(state, new_username)

                cur.execute(
                    "UPDATE users SET username=%s,password_hash=%s WHERE username=%s",
                    (new_username, generate_password_hash(new_password), old_username)
                )

                cur.execute(
                    "DELETE FROM user_state WHERE username=%s",
                    (old_username,)
                )

                cur.execute(
                    "INSERT INTO user_state(username,data) VALUES(%s,%s)",
                    (new_username, json.dumps(state))
                )

            c.commit()
        finally:
            c.close()

        return True, "Login details updated"

    c = sqlite_conn()
    try:
        exists = c.execute(
            "SELECT username FROM users WHERE lower(username)=lower(?) AND username<>?",
            (new_username, old_username)
        ).fetchone()

        if exists:
            return False, "Username already exists"

        row = c.execute(
            "SELECT data FROM user_state WHERE username=?",
            (old_username,)
        ).fetchone()

        if not row:
            return False, "Account data not found"

        state = clean_state(json.loads(row[0]), new_username)

        c.execute(
            "UPDATE users SET username=?,password_hash=? WHERE username=?",
            (new_username, generate_password_hash(new_password), old_username)
        )
        c.execute(
            "DELETE FROM user_state WHERE username=?",
            (old_username,)
        )
        c.execute(
            "INSERT INTO user_state(username,data) VALUES(?,?)",
            (new_username, json.dumps(state))
        )
        c.commit()
    finally:
        c.close()

    return True, "Login details updated"


def reset_password_by_username(username, new_password):
    if not username or not new_password:
        return False

    if DATABASE_URL:
        c = postgres_conn()
        try:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT username FROM users WHERE lower(username)=lower(%s)",
                    (username,)
                )
                row = cur.fetchone()

                if not row:
                    return False

                real_username = row[0]
                cur.execute(
                    "UPDATE users SET password_hash=%s WHERE username=%s",
                    (generate_password_hash(new_password), real_username)
                )
            c.commit()
        finally:
            c.close()
        return True

    c = sqlite_conn()
    try:
        row = c.execute(
            "SELECT username FROM users WHERE lower(username)=lower(?)",
            (username,)
        ).fetchone()

        if not row:
            return False

        real_username = row[0]
        c.execute(
            "UPDATE users SET password_hash=? WHERE username=?",
            (generate_password_hash(new_password), real_username)
        )
        c.commit()
        return True
    finally:
        c.close()


# =========================================================
# AUTH HELPERS
# =========================================================

def logged_in_username():
    return session.get("username")


def require_login():
    username = logged_in_username()
    if not username:
        return None, (jsonify({"error": "Not logged in"}), 401)
    return username, None


# =========================================================
# INITIALIZE DATABASE
# =========================================================

try:
    ensure_database()
except Exception:
    print("DATABASE INITIALIZATION ERROR")
    traceback.print_exc()


# =========================================================
# HOME
# =========================================================

@app.get("/")
def index():
    return send_from_directory(BASE, "index.html")


# =========================================================
# AUTH API
# =========================================================

@app.post("/api/signup")
def signup():
    data = request.get_json(silent=True) or {}

    username = str(data.get("username") or "").strip()
    password = str(data.get("password") or "")
    confirm = str(data.get("confirm") or "")

    if not username or not password or not confirm:
        return jsonify({"error": "Enter username, password and retype password."}), 400

    if password != confirm:
        return jsonify({"error": "Passwords do not match."}), 400

    try:
        ok, message = create_user(username, password)

        if not ok:
            return jsonify({"error": message}), 409

        return jsonify({"ok": True, "message": message})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": "Account creation failed", "details": str(e)}), 500


@app.post("/api/login")
def login():
    data = request.get_json(silent=True) or {}

    username = str(data.get("username") or "").strip()
    password = str(data.get("password") or "")

    if not username or not password:
        return jsonify({"error": "Enter username and password."}), 400

    try:
        real_username = authenticate_user(username, password)

        if not real_username:
            return jsonify({"error": "Invalid username or password."}), 401

        session.clear()
        session.permanent = True
        session["username"] = real_username

        return jsonify({
            "ok": True,
            "state": load_user_state(real_username)
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": "Login failed", "details": str(e)}), 500


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.get("/api/me")
def me():
    username, error = require_login()

    if error:
        return error

    return jsonify({
        "ok": True,
        "username": username
    })


@app.post("/api/forgot-password")
def forgot_password():
    data = request.get_json(silent=True) or {}

    username = str(data.get("username") or "").strip()
    password = str(data.get("password") or "")
    confirm = str(data.get("confirm") or "")

    if not username or not password or not confirm:
        return jsonify({"error": "Fill all fields."}), 400

    if password != confirm:
        return jsonify({"error": "Passwords do not match."}), 400

    try:
        if not reset_password_by_username(username, password):
            return jsonify({"error": "Username not found."}), 404

        return jsonify({
            "ok": True,
            "message": "Password reset successfully. Please login."
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": "Password reset failed", "details": str(e)}), 500


# =========================================================
# STATE API - USER ISOLATED
# =========================================================

@app.get("/api/state")
def state_get():
    username, error = require_login()

    if error:
        return error

    try:
        return jsonify(load_user_state(username))
    except Exception as e:
        print("API STATE ERROR:")
        print(str(e))
        traceback.print_exc()
        return jsonify({
            "error": "Database read failed",
            "details": str(e)
        }), 500


@app.post("/api/state")
def state_post():
    username, error = require_login()

    if error:
        return error

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({"error": "Invalid state"}), 400

    try:
        save_user_state(username, data)

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
        traceback.print_exc()

        return jsonify({
            "error": "Database save failed",
            "details": str(e)
        }), 500


# =========================================================
# PROFILE API
# =========================================================

@app.post("/api/profile")
def profile_update():
    username, error = require_login()

    if error:
        return error

    data = request.get_json(silent=True) or {}

    try:
        state = load_user_state(username)
        account = state.get("account") or {}

        display_name = str(data.get("displayName") or "").strip()
        email = str(data.get("email") or "").strip()
        phone = str(data.get("phone") or "").strip()

        if not display_name:
            return jsonify({"error": "Enter your name."}), 400

        account["username"] = username
        account["displayName"] = display_name
        account["email"] = email
        account["phone"] = phone

        state["account"] = account
        save_user_state(username, state)

        return jsonify({
            "ok": True,
            "state": load_user_state(username)
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": "Profile update failed", "details": str(e)}), 500


# =========================================================
# CHANGE LOGIN DETAILS API
# =========================================================

@app.post("/api/change-login")
def change_login():
    old_username, error = require_login()

    if error:
        return error

    data = request.get_json(silent=True) or {}

    old_password = str(data.get("oldPassword") or "")
    new_username = str(data.get("newUsername") or "").strip()
    new_password = str(data.get("newPassword") or "")
    confirm = str(data.get("confirm") or "")

    if not old_password or not new_username or not new_password or not confirm:
        return jsonify({"error": "Fill all fields."}), 400

    if new_password != confirm:
        return jsonify({"error": "New passwords do not match."}), 400

    # Verify current password.
    real_username = authenticate_user(old_username, old_password)

    if real_username != old_username:
        return jsonify({"error": "Current password is incorrect."}), 401

    try:
        ok, message = rename_user(
            old_username,
            new_username,
            new_password
        )

        if not ok:
            return jsonify({"error": message}), 409

        session["username"] = new_username

        return jsonify({
            "ok": True,
            "state": load_user_state(new_username)
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": "Login details update failed", "details": str(e)}), 500


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

    if DATABASE_URL:
        try:
            c = postgres_conn()
            with c.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            c.close()

            result["database_connection"] = "working"

        except Exception as e:
            result["ok"] = False
            result["database_connection"] = "failed"
            result["database_error"] = str(e)

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
