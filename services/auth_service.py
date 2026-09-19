import hashlib
import uuid
from typing import Optional
from fastapi import Request
from database.connection import db_router
from config import ADMIN_EMAILS

def hash_password(password: str) -> str:
    salt = uuid.uuid4().hex
    hashed = hashlib.sha256(salt.encode() + password.encode()).hexdigest()
    return f"{salt}:{hashed}"

def verify_password(stored_password: str, provided_password: str) -> bool:
    try:
        salt, hashed = stored_password.split(":")
        check_hashed = hashlib.sha256(salt.encode() + provided_password.encode()).hexdigest()
        return check_hashed == hashed
    except Exception:
        return False

def create_session(email: str) -> str:
    token = uuid.uuid4().hex
    conn = db_router.connect()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO user_sessions (token, email) VALUES (?, ?)", (token, email))
    conn.commit()
    conn.close()
    return token

def get_session_email(token: str) -> Optional[str]:
    if not token:
        return None
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        cursor.execute("SELECT email FROM user_sessions WHERE token = ?", (token,))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None

def delete_session(token: str):
    if not token:
        return
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM user_sessions WHERE token = ?", (token,))
        conn.commit()
        conn.close()
    except Exception:
        pass

def is_authenticated(request: Request) -> Optional[str]:
    token = request.cookies.get("session_token")
    return get_session_email(token)

def get_user_role_and_client(email: str) -> tuple[str, Optional[int]]:
    """Returns the (role, client_id) for the user. Defaults to ('full', None) if not found."""
    if not email:
        return "read", None
    conn = db_router.connect()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT role, client_id FROM users WHERE LOWER(TRIM(email)) = ?", (email.strip().lower(),))
        row = cursor.fetchone()
        if row:
            return row[0] or "full", row[1]
    except Exception as e:
        print(f"⚠️ Error getting user role: {e}")
    finally:
        conn.close()
    return "full", None


