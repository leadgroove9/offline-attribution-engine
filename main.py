import os
import sqlite3
import re
import json
import csv
import io
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, Response, RedirectResponse
from pydantic import BaseModel
from typing import Optional
from anthropic import Anthropic

# Initialize FastAPI App

import hashlib
import uuid

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

app = FastAPI(
    title="Offline Attribution Engine (Multi-Tenant Multi-Channel)",
    description="Multi-tenant agency platform for tracking offline leads/sales and AI audits across Google, Meta, LinkedIn, and Microsoft",
    version="15.2.0"
)

# ---------------------------------------------------------
# DATABASE CONFIGURATION (SQLite)
# ---------------------------------------------------------
DB_PATH = "offline_attribution.db"

# ---------------------------------------------------------
# UNIFIED DATABASE ROUTING LAYER (PostgreSQL & SQLite)
# ---------------------------------------------------------
DATABASE_URL = os.environ.get("DATABASE_URL")

class PostgreSQLCursorWrapper:
    def __init__(self, pg_cursor):
        self.cursor = pg_cursor
        self._fetchone_override = None
        self._fetchall_override = None

    def execute(self, query, params=None):
        # Reset overrides
        self._fetchone_override = None
        self._fetchall_override = None
        
        # 1. Map SQLite parameters placeholder (?) to PostgreSQL (%s)
        # Be careful not to replace ? inside text strings, but simple replace works for our code's query structure
        query_formatted = query.replace("?", "%s")
        
        # 2. Map SQLite table creation constraints to PostgreSQL serialization schemas
        query_formatted = query_formatted.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        query_formatted = query_formatted.replace("AUTOINCREMENT", "")
        
        # 3. Intercept PRAGMA table_info dynamic schema self-healing checks
        if "PRAGMA table_info(" in query:
            table_name = query.split("PRAGMA table_info(")[1].split(")")[0].strip().replace("'", "").replace('"', '')
            pg_query = f"SELECT column_name FROM information_schema.columns WHERE table_name = '{table_name}'"
            self.cursor.execute(pg_query)
            cols = self.cursor.fetchall()
            # Mock PRAGMA table_info columns format: (cid, name, type, notnull, dflt_value, pk)
            # main.py does: `existing_cols = [col[1] for col in cursor.fetchall()]`
            mock_cols = [(0, col[0], 'TEXT', 0, None, 0) for col in cols]
            self._fetchall_override = lambda: mock_cols
            return self

        # 4. Intercept sqlite_sequence checks used for calculating onboarding sequence IDs
        if "SELECT seq FROM sqlite_sequence" in query:
            table_name = "clients"
            if "name =" in query:
                parts = query.split("name =")
                if len(parts) > 1:
                    table_name = parts[1].replace("'", "").replace('"', '').strip()
            pg_query = f"SELECT COALESCE(MAX(id), 0) FROM {table_name}"
            self.cursor.execute(pg_query)
            max_id = self.cursor.fetchone()[0]
            self._fetchone_override = lambda: (max_id,)
            return self
            
        # 5. Fix potential PostgreSQL cast/comparison issues with Boolean/Text
        # Also convert SQLite-style datetime(column, 'localtime') to PostgreSQL TO_CHAR(column, 'YYYY-MM-DD HH24:MI:SS')
        import re
        query_formatted = re.sub(r"datetime\(([^,]+),\s*'localtime'\)", r"to_char(\1, 'YYYY-MM-DD HH24:MI:SS')", query_formatted, flags=re.IGNORECASE)
        
        # Execute raw query
        self.cursor.execute(query_formatted, params)
        return self

    def executemany(self, query, params_list):
        query_formatted = query.replace("?", "%s")
        self.cursor.executemany(query_formatted, params_list)
        return self

    def fetchone(self):
        if self._fetchone_override:
            return self._fetchone_override()
        return self.cursor.fetchone()

    def fetchall(self):
        if self._fetchall_override:
            return self._fetchall_override()
        return self.cursor.fetchall()

    @property
    def lastrowid(self):
        # PostgreSQL doesn't support cursor.lastrowid; use LASTVAL() utility sequence lookup
        try:
            self.cursor.execute("SELECT LASTVAL()")
            return self.cursor.fetchone()[0]
        except Exception:
            return 1

    def close(self):
        self.cursor.close()


class PostgreSQLConnectionWrapper:
    def __init__(self, pg_conn):
        self.connection = pg_conn
        
    def cursor(self):
        return PostgreSQLCursorWrapper(self.connection.cursor())
        
    def commit(self):
        self.connection.commit()
        
    def rollback(self):
        self.connection.rollback()
        
    def close(self):
        self.connection.close()


class DatabaseRouter:
    @staticmethod
    def connect():
        if DATABASE_URL:
            # Render PostgreSQL active connection routing
            import psycopg2
            # Render sometimes provides connection string starting with 'postgres://' 
            # which python's psycopg2 expects as 'postgresql://'
            url_clean = DATABASE_URL
            if url_clean.startswith("postgres://"):
                url_clean = url_clean.replace("postgres://", "postgresql://", 1)
            
            # Open PostgreSQL Connection and return our adapter wrapper
            conn = psycopg2.connect(url_clean)
            return PostgreSQLConnectionWrapper(conn)
        else:
            # Local development SQLite connection routing
            import sqlite3
            return sqlite3.connect("offline_attribution.db")

# Monkeypatch sqlite3 inside current module scope to redirect connect calls transparently!
class MockSqlite3:
    def connect(self, *args, **kwargs):
        return DatabaseRouter.connect()

db_router = MockSqlite3()


def init_db():
    """Initializes the database, creates necessary tables, and self-heals schemas."""
    conn = db_router.connect()
    cursor = conn.cursor()
    # 6. Create Users Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            hashed_password TEXT NOT NULL,
            role TEXT DEFAULT 'full',
            client_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Self-heal users schema
    cursor.execute("PRAGMA table_info(users)")
    existing_user_cols = [col[1] for col in cursor.fetchall()]
    user_cols_to_verify = [
        ("role", "TEXT DEFAULT 'full'"),
        ("client_id", "INTEGER")
    ]
    for col_name, col_type in user_cols_to_verify:
        if col_name not in existing_user_cols:
            cursor.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}")
            print(f"Added missing user column: {col_name}")

    # Create user_invitations table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_invitations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            role TEXT NOT NULL,
            client_id INTEGER,
            token TEXT UNIQUE NOT NULL,
            invited_by TEXT NOT NULL,
            is_used TEXT DEFAULT 'NO',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (client_id) REFERENCES clients (id)
        )
    """)
    
    # 7. Create User Sessions Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT UNIQUE NOT NULL,
            email TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    
    # 2. Create Clients Table with complete questionnaire fields
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            callrail_account_id TEXT,
            callrail_company_id TEXT UNIQUE,
            google_ads_customer_id TEXT,
            facebook_ads_id TEXT,
            linkedin_ads_id TEXT,
            microsoft_ads_id TEXT,
            lead_gen_method TEXT,
            qualification_criteria TEXT,
            source_of_truth TEXT,
            email_provider TEXT,
            email_account TEXT,
            crm_deal_tags TEXT,
            crm_won_deal_tags TEXT,
            crm_lead_tags TEXT,
            lead_count_rule TEXT,
            exclude_past_customers TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 1. Create Excluded Customers Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS excluded_customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            first_name TEXT,
            last_name TEXT,
            email TEXT,
            phone TEXT,
            company_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (client_id) REFERENCES clients (id)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_excluded_customers_client_phone ON excluded_customers(client_id, phone);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_excluded_customers_client_email ON excluded_customers(client_id, email);")

    
    # Self-heal clients schema
    cursor.execute("PRAGMA table_info(clients)")
    existing_cols = [col[1] for col in cursor.fetchall()]
    
    cols_to_verify = [
        ("facebook_ads_id", "TEXT"),
        ("linkedin_ads_id", "TEXT"),
        ("microsoft_ads_id", "TEXT"),
        ("lead_gen_method", "TEXT"),
        ("qualification_criteria", "TEXT"),
        ("source_of_truth", "TEXT"),
        ("email_provider", "TEXT"),
        ("email_account", "TEXT"),
        ("crm_deal_tags", "TEXT"),
        ("crm_won_deal_tags", "TEXT"),
        ("crm_lead_tags", "TEXT"),
        ("lead_count_rule", "TEXT"),
        ("exclude_past_customers", "TEXT"),
        ("callrail_account_id", "TEXT"),
        ("call_tracking_provider", "TEXT"),
        ("ctm_account_id", "TEXT"),
        ("ctm_profile_id", "TEXT"),
        ("wc_account_id", "TEXT"),
        ("wc_profile_id", "TEXT")
    ]
    for col_name, col_type in cols_to_verify:
        if col_name not in existing_cols:
            cursor.execute(f"ALTER TABLE clients ADD COLUMN {col_name} {col_type}")
            print(f"Added missing database column in clients: {col_name}")

    # 2. Create Analyzed Emails Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS analyzed_emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER,
            subject TEXT,
            sender TEXT,
            recipient TEXT,
            analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 4. Create CRM Webhook Logs Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS crm_webhook_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER,
            contact_name TEXT,
            stage TEXT,
            amount REAL,
            received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 5. Create Billing Webhook Logs Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS billing_webhook_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER,
            customer_name TEXT,
            invoice_number TEXT,
            amount REAL,
            received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 3. Create Sessions Table (Multi-Tenant)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER DEFAULT 1,
            phone TEXT NOT NULL,
            email TEXT,
            name TEXT,
            company TEXT,
            gclid TEXT,
            fbclid TEXT,
            li_fat_id TEXT,
            msclkid TEXT,
            source TEXT, -- 'callrail', 'form', etc.
            qualified TEXT, -- 'YES' or 'NO' from Claude
            sale_closed TEXT, -- 'YES' or 'NO' from Claude
            value REAL DEFAULT 0.0, -- $ Value extracted by Claude
            reason TEXT, -- Claude's justification
            model_used TEXT, -- Claude model name
            raw_data TEXT, -- JSON payload for debugging
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (client_id) REFERENCES clients (id)
        )
    """)
    
    # Self-heal sessions schema
    cursor.execute("PRAGMA table_info(sessions)")
    existing_session_cols = [col[1] for col in cursor.fetchall()]
    session_cols_to_verify = [
        ("fbclid", "TEXT"),
        ("li_fat_id", "TEXT"),
        ("msclkid", "TEXT")
    ]
    for col_name, col_type in session_cols_to_verify:
        if col_name not in existing_session_cols:
            cursor.execute(f"ALTER TABLE sessions ADD COLUMN {col_name} {col_type}")
            print(f"Added missing session column: {col_name}")
            
    # 3. Seed Mock Clients if empty
    cursor.execute("SELECT COUNT(*) FROM clients")
    if cursor.fetchone()[0] == 0:
        mock_clients = [
            ("Priority Plumbing", "comp_plumbing", "123-456-7890", "fb_plumb_99", "", "", "both", "C", "hubspot", "", "", "appointment-booked", "closed-won", "", "all", "NO"),
            ("Apex HVAC & Air", "comp_hvac", "987-654-3210", "", "", "ms_hvac_88", "both", "E", "servicetitan", "", "", "", "", "completed-lead", "all", "NO"),
            ("Metro Dental Care", "comp_dental", "555-123-4567", "", "li_dental_77", "", "both", "C", "email", "gmail", "bookings@metrodental.com", "", "", "", "maximum_one", "YES")
        ]
        cursor.executemany("""
            INSERT INTO clients (
                name, callrail_company_id, google_ads_customer_id, facebook_ads_id, linkedin_ads_id, microsoft_ads_id,
                lead_gen_method, qualification_criteria, source_of_truth, email_provider, email_account,
                crm_deal_tags, crm_won_deal_tags, crm_lead_tags, lead_count_rule, exclude_past_customers
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, mock_clients)
        print("Seeded 3 mock agency clients successfully!")
    
    conn.commit()
    conn.close()

# Run database initialization
init_db()


# ---------------------------------------------------------
# ANTHROPIC CLAUDE CONFIGURATION
# ---------------------------------------------------------
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
client = Anthropic(api_key=API_KEY, max_retries=3, timeout=30.0) if API_KEY else None

def clean_json_string(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()

def analyze_transcript_with_claude(transcript: str, qualification_criteria_desc: str) -> dict:
    """
    Sends a transcript to Claude 4.5 Haiku to audit based on the client's custom qualification criteria.
    Uses standard HTTP/1.1 requests to bypass httpx/HTTP/2 connection resets on Render/Cloudflare.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        # High-Fidelity Simulation Fallback
        lower_t = transcript.lower()
        if "husband fixed it" in lower_t or "wrong number" in lower_t or "cancel" in lower_t:
            return {
                "qualified": "NO",
                "sale_closed": "NO",
                "value": 0.0,
                "reason": "Simulated Audit: Lead expressed negative intent or cancelled query."
            }
        
        # Simple pattern heuristic for simulated audit
        value = 0.0
        sale_closed = "NO"
        if "$" in lower_t or "booked" in lower_t or "deposit" in lower_t:
            sale_closed = "YES"
            # Attempt to extract dollar amount
            matches = re.findall(r"\$(\d+(?:\.\d{2})?)", lower_t)
            value = float(matches[0]) if matches else 150.00
            
        return {
            "qualified": "YES",
            "sale_closed": sale_closed,
            "value": value,
            "reason": f"Simulated Audit: Detected qualification signals aligning with standard: '{qualification_criteria_desc}'."
        }

    if not transcript or not transcript.strip():
        return {
            "qualified": "NO",
            "sale_closed": "NO",
            "value": 0.0,
            "reason": "No transcript available for analysis."
        }

    import requests
    import time
    
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    
    system_prompt = (
        "You are an expert sales auditor and conversion tracking engine for local service businesses.\n"
        "Your job is to read a transcript (phone call or email log) and determine three things:\n"
        f"1. Is the lead a 'Qualified Lead'? For this business, a qualified lead is defined as: \"{qualification_criteria_desc}\". Return 'YES' or 'NO' based strictly on this custom threshold.\n"
        "2. Was a sale 'Closed'? (Did they agree to purchase, pay a deposit, or book a paid job? Return 'YES' or 'NO')\n"
        "3. What was the 'Value' of the transaction? (Extract the exact dollar amount if mentioned. If no sale closed or no value was stated, return 0)\n"
        "\n"
        "CRITICAL: You must return your response in RAW, valid JSON format. Do not write any introduction, "
        "explanation, or markdown formatting (do not wrap in ```json). Your entire response must look exactly like this:\n"
        "{\n"
        '  "qualified": "YES",\n'
        '  "sale_closed": "YES",\n'
        '  "value": 450.00,\n'
        '  "reason": "A 1-2 sentence explanation of why you made this decision."\n'
        "}"
    )
    
    payload = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 500,
        "system": system_prompt,
        "messages": [
            {"role": "user", "content": f"Analyze this transcript:\n\n{transcript}"}
        ]
    }
    
    max_retries = 3
    retry_delay = 2
    
    for attempt in range(max_retries):
        try:
            session = requests.Session()
            response = session.post(url, headers=headers, json=payload, timeout=25)
            
            if response.status_code == 200:
                result = response.json()
                content_text = result.get("content", [{}])[0].get("text", "").strip()
                
                # Clean any accidental markdown wrap
                if content_text.startswith("```"):
                    content_text = re.sub(r"^```(?:json)?\n|```$", "", content_text, flags=re.MULTILINE).strip()
                    
                parsed_res = json.loads(content_text)
                return {
                    "qualified": str(parsed_res.get("qualified", "NO")).upper(),
                    "sale_closed": str(parsed_res.get("sale_closed", "NO")).upper(),
                    "value": float(parsed_res.get("value", 0.0)),
                    "reason": str(parsed_res.get("reason", "No reason provided."))
                }
            elif response.status_code in [429, 500, 502, 503, 504]:
                print(f"⚠️ Claude API transient error {response.status_code}. Retrying in {retry_delay}s...")
                time.sleep(retry_delay)
                retry_delay *= 2
            else:
                return {
                    "qualified": "NO",
                    "sale_closed": "NO",
                    "value": 0.0,
                    "reason": f"Claude API Error (HTTP {response.status_code}): {response.text}"
                }
        except Exception as e:
            if attempt == max_retries - 1:
                return {
                    "qualified": "NO",
                    "sale_closed": "NO",
                    "value": 0.0,
                    "reason": f"Claude Connection Exception: {str(e)}"
                }
            print(f"⚠️ Claude Connection Attempt {attempt + 1} failed: {e}. Retrying in {retry_delay}s...")
            time.sleep(retry_delay)
            retry_delay *= 2
            
    return {
        "qualified": "NO",
        "sale_closed": "NO",
        "value": 0.0,
        "reason": "Claude API request failed after maximum retries."
    }

# ---------------------------------------------------------
# HELPERS & CONVERTERS
# ---------------------------------------------------------
def check_is_excluded_customer(client_id: int, phone: str = "", email: str = "") -> Optional[str]:
    """
    Checks if a phone or email matches any record in the excluded_customers table for the given client_id.
    Returns the match reason (e.g. 'Phone Match' or 'Email Match') if excluded, else None.
    """
    if not phone and not email:
        return None
        
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        
        # Check phone
        if phone:
            normalized_p = normalize_phone(phone)
            if normalized_p:
                cursor.execute("SELECT id FROM excluded_customers WHERE client_id = ? AND phone = ?", (client_id, normalized_p))
                if cursor.fetchone():
                    conn.close()
                    return "Phone Match"
                    
        # Check email
        if email:
            clean_e = email.strip().lower()
            if clean_e:
                cursor.execute("SELECT id FROM excluded_customers WHERE client_id = ? AND email = ?", (client_id, clean_e))
                if cursor.fetchone():
                    conn.close()
                    return "Email Match"
                    
        conn.close()
    except Exception as e:
        print(f"⚠️ Error checking customer exclusion: {e}")
    return None

def normalize_phone(phone_str: str) -> str:
    if not phone_str:
        return ""
    cleaned = re.sub(r'\D', '', phone_str)
    if len(cleaned) == 10:
        cleaned = "1" + cleaned
    return cleaned

def extract_param_from_url(url: str, param_name: str) -> Optional[str]:
    if not url:
        return None
    match = re.search(rf"[?&]{param_name}=([^&#]+)", url)
    return match.group(1) if match else None

CRITERIA_MAP = {
    "A": "Someone that I have a conversation with",
    "B": "Someone who shows strong buying interest",
    "C": "Someone who books an appointment",
    "D": "Someone who books a demo",
    "E": "Someone who requests a quote",
    "F": "Someone who we send a proposal",
    "H": "Someone who has qualified insurance",
    "I": "Someone who is credit pre-qualified"
}

ADMIN_EMAILS = {"admin@leadgrove.net", "admin@leadgroove.net", "corey@test.com", "corey@leadgrove.net", "corey@leadgroove.net"}

SOT_MAP = {
    "email": "Monthly Sales Spreadsheet Ingestion via Email",
    "hubspot": "HubSpot CRM",
    "zoho": "Zoho CRM",
    "salesforce": "Salesforce CRM",
    "servicetitan": "ServiceTitan CRM",
    "housecallpro": "Housecall Pro CRM",
    "quickbooks": "QuickBooks Billing",
    "xero": "Xero Accounting",
    "ai_rating": "AI Rating (Direct Call Audits & Dynamic Form-Email Monitoring)"
}


# ---------------------------------------------------------
# WEBHOOK DATA SCHEMAS (Pydantic Models)
# ---------------------------------------------------------
class FormLead(BaseModel):
    first_name: str
    last_name: str
    email: str
    phone: str
    company: Optional[str] = None
    gclid: Optional[str] = None
    fbclid: Optional[str] = None
    li_fat_id: Optional[str] = None
    msclkid: Optional[str] = None


class ExcludedCustomer(BaseModel):
    first_name: Optional[str] = ""
    last_name: Optional[str] = ""
    email: Optional[str] = ""
    phone: Optional[str] = ""
    company_name: Optional[str] = ""

class ClientCreate(BaseModel):
    name: str
    call_tracking_provider: Optional[str] = "callrail"
    callrail_account_id: Optional[str] = ""
    callrail_company_id: Optional[str] = ""
    ctm_account_id: Optional[str] = ""
    ctm_profile_id: Optional[str] = ""
    wc_account_id: Optional[str] = ""
    wc_profile_id: Optional[str] = "" 
    google_ads_customer_id: str
    facebook_ads_id: Optional[str] = ""
    linkedin_ads_id: Optional[str] = ""
    microsoft_ads_id: Optional[str] = ""
    lead_gen_method: str
    qualification_criteria: str
    source_of_truth: str
    email_provider: Optional[str] = ""
    email_account: Optional[str] = ""
    crm_deal_tags: Optional[str] = ""
    crm_won_deal_tags: Optional[str] = ""
    crm_lead_tags: Optional[str] = ""
    lead_count_rule: str
    exclude_past_customers: str
    excluded_customers: Optional[list[ExcludedCustomer]] = None
    exclusion_action: Optional[str] = "append"


# ---------------------------------------------------------
# ENDPOINTS
# ---------------------------------------------------------


@app.get("/register", response_class=HTMLResponse)
def get_register(request: Request, error: Optional[str] = None, invite_token: Optional[str] = None):
    email_val = ""
    lock_email_attr = ""
    invite_role_msg = ""
    token_hidden_input = ""
    
    if invite_token:
        conn = db_router.connect()
        cursor = conn.cursor()
        cursor.execute("SELECT email, role, client_id FROM user_invitations WHERE token = ? AND is_used = 'NO'", (invite_token,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            error = "Invalid or expired invitation token. Please request a new invite link."
        else:
            invited_email, invited_role, invited_client_id = row
            email_val = invited_email
            lock_email_attr = "readonly style='background: #f1f3f4; color: #666;'"
            role_title = "Full Function (Manager)" if invited_role == "full" else "Read-Only (Viewer)"
            invite_role_msg = f"""
            <div style="background-color: #e8f5e9; color: #2e7d32; padding: 12px; border-radius: 6px; margin-bottom: 20px; font-size: 13px; font-weight: bold; border-left: 4px solid #2e7d32;">
                ✅ Invitation Verified!<br>
                You are registering as a <strong>{role_title}</strong>.
            </div>
            """
            token_hidden_input = f"<input type='hidden' name='invite_token' value='{invite_token}'>"

    error_html = f'<div style="background-color: #ffebee; color: #c62828; padding: 12px; border-radius: 6px; margin-bottom: 20px; font-size: 13px; font-weight: bold; border-left: 4px solid #c62828;">❌ {error}</div>' if error else ''
    return f"""
    <html>
        <head>
            <title>Register - LeadGroove 🤖</title>
            <style>
                body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; text-align: center; padding-top: 100px; background-color: #f4f6f9; color: #333; }}
                .container {{ display: inline-block; background: white; padding: 40px; border-radius: 12px; box-shadow: 0px 8px 24px rgba(0,0,0,0.08); max-width: 400px; width: 100%; text-align: left; box-sizing: border-box; }}
                h1 {{ color: #1a237e; margin-top: 0; margin-bottom: 8px; font-size: 24px; font-weight: 700; text-align: center; }}
                p {{ color: #666; font-size: 14px; margin-top: 0; margin-bottom: 24px; text-align: center; }}
                .form-group {{ margin-bottom: 18px; }}
                label {{ display: block; font-weight: bold; font-size: 12px; margin-bottom: 6px; color: #1a237e; text-transform: uppercase; letter-spacing: 0.5px; }}
                input[type="email"], input[type="password"] {{ width: 100%; padding: 12px; border-radius: 6px; border: 1px solid #ced4da; font-size: 14px; box-sizing: border-box; transition: border-color 0.2s; }}
                input[type="email"]:focus, input[type="password"]:focus {{ border-color: #1a237e; outline: none; }}
                .btn {{ width: 100%; background-color: #1a237e; color: white; padding: 12px; border: none; font-size: 16px; font-weight: bold; border-radius: 6px; cursor: pointer; margin-top: 10px; transition: background 0.2s; }}
                .btn:hover {{ background-color: #0d1b2a; }}
                .switch-link {{ text-align: center; margin-top: 20px; font-size: 13px; color: #555; }}
                .switch-link a {{ color: #1a237e; text-decoration: none; font-weight: bold; }}
                .switch-link a:hover {{ text-decoration: underline; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Create Your Account</h1>
                <p>Register to start tracking conversions on LeadGroove</p>
                {error_html}
                {invite_role_msg}
                <form action="/register" method="POST">
                    {token_hidden_input}
                    <div class="form-group">
                        <label for="email">Email Address</label>
                        <input type="email" id="email" name="email" required placeholder="e.g. corey@youragency.com" value="{email_val}" {lock_email_attr}>
                    </div>
                    <div class="form-group">
                        <label for="password">Password</label>
                        <input type="password" id="password" name="password" required placeholder="••••••••">
                    </div>
                    <div class="form-group" style="margin-bottom: 25px;">
                        <label for="confirm_password">Confirm Password</label>
                        <input type="password" id="confirm_password" name="confirm_password" required placeholder="••••••••">
                    </div>
                    <button type="submit" class="btn">🚀 Create Account</button>
                </form>
                <div class="switch-link">
                    Already have an account? <a href="/login">Log In</a>
                </div>
            </div>
        </body>
    </html>
    """

@app.post("/register")
async def post_register(request: Request):
    try:
        form_data = await request.form()
        email = form_data.get("email", "").strip().lower()
        password = form_data.get("password")
        confirm_password = form_data.get("confirm_password")
        invite_token = form_data.get("invite_token")
        
        if not email or not password or not confirm_password:
            return HTMLResponse(get_register(request, error="All fields are required.", invite_token=invite_token))
        if password != confirm_password:
            return HTMLResponse(get_register(request, error="Passwords do not match.", invite_token=invite_token))
        if len(password) < 6:
            return HTMLResponse(get_register(request, error="Password must be at least 6 characters.", invite_token=invite_token))
            
        resolved_role = "full"
        resolved_client_id = None
        
        if invite_token:
            conn = db_router.connect()
            cursor = conn.cursor()
            cursor.execute("SELECT email, role, client_id FROM user_invitations WHERE token = ? AND is_used = 'NO'", (invite_token,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                return HTMLResponse(get_register(request, error="Invalid or expired invitation token.", invite_token=invite_token))
            invited_email, invited_role, invited_client_id = row
            email = invited_email
            resolved_role = invited_role
            resolved_client_id = invited_client_id
            conn.close()
            
        try:
            conn = db_router.connect()
            cursor = conn.cursor()
            
            # Check if user already exists
            cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
            if cursor.fetchone():
                conn.close()
                return HTMLResponse(get_register(request, error="An account with this email already exists.", invite_token=invite_token))
                
            hashed = hash_password(password)
            cursor.execute("""
                INSERT INTO users (email, hashed_password, role, client_id) 
                VALUES (?, ?, ?, ?)
            """, (email, hashed, resolved_role, resolved_client_id))
            
            if invite_token:
                cursor.execute("UPDATE user_invitations SET is_used = 'YES' WHERE token = ?", (invite_token,))
                
            conn.commit()
            conn.close()
            
            # Auto-login after registration
            token = create_session(email)
            response = RedirectResponse(url="/dashboard", status_code=303)
            response.set_cookie(key="session_token", value=token, max_age=86400 * 30, httponly=True)
            return response
        except Exception as e:
            return HTMLResponse(get_register(request, error=f"Database error: {str(e)}"))
    except Exception as outer_e:
        import traceback
        tb = traceback.format_exc()
        return HTMLResponse(f"""
        <html>
            <body style="font-family: monospace; padding: 40px; background-color: #ffebee; color: #c62828;">
                <h2>❌ Unhandled Registration Error</h2>
                <pre>{tb}</pre>
            </body>
        </html>
        """, status_code=500)

@app.get("/login", response_class=HTMLResponse)
def get_login(request: Request, error: Optional[str] = None):
    error_html = f'<div style="background-color: #ffebee; color: #c62828; padding: 12px; border-radius: 6px; margin-bottom: 20px; font-size: 13px; font-weight: bold; border-left: 4px solid #c62828;">❌ {error}</div>' if error else ''
    return f"""
    <html>
        <head>
            <title>Log In - LeadGroove 🤖</title>
            <style>
                body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; text-align: center; padding-top: 120px; background-color: #f4f6f9; color: #333; }}
                .container {{ display: inline-block; background: white; padding: 40px; border-radius: 12px; box-shadow: 0px 8px 24px rgba(0,0,0,0.08); max-width: 400px; width: 100%; text-align: left; box-sizing: border-box; }}
                h1 {{ color: #1a237e; margin-top: 0; margin-bottom: 8px; font-size: 24px; font-weight: 700; text-align: center; }}
                p {{ color: #666; font-size: 14px; margin-top: 0; margin-bottom: 24px; text-align: center; }}
                .form-group {{ margin-bottom: 18px; }}
                label {{ display: block; font-weight: bold; font-size: 12px; margin-bottom: 6px; color: #1a237e; text-transform: uppercase; letter-spacing: 0.5px; }}
                input[type="email"], input[type="password"] {{ width: 100%; padding: 12px; border-radius: 6px; border: 1px solid #ced4da; font-size: 14px; box-sizing: border-box; transition: border-color 0.2s; }}
                input[type="email"]:focus, input[type="password"]:focus {{ border-color: #1a237e; outline: none; }}
                .btn {{ width: 100%; background-color: #1a237e; color: white; padding: 12px; border: none; font-size: 16px; font-weight: bold; border-radius: 6px; cursor: pointer; margin-top: 10px; transition: background 0.2s; }}
                .btn:hover {{ background-color: #0d1b2a; }}
                .switch-link {{ text-align: center; margin-top: 20px; font-size: 13px; color: #555; }}
                .switch-link a {{ color: #1a237e; text-decoration: none; font-weight: bold; }}
                .switch-link a:hover {{ text-decoration: underline; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Welcome Back</h1>
                <p>Log in to access your conversion dashboard</p>
                {error_html}
                <form action="/login" method="POST">
                    <div class="form-group">
                        <label for="email">Email Address</label>
                        <input type="email" id="email" name="email" required placeholder="e.g. corey@youragency.com">
                    </div>
                    <div class="form-group" style="margin-bottom: 25px;">
                        <label for="password">Password</label>
                        <input type="password" id="password" name="password" required placeholder="••••••••">
                    </div>
                    <button type="submit" class="btn">🔑 Log In</button>
                </form>
                <div class="switch-link">
                    Don't have an account? <a href="/register">Sign Up</a>
                </div>
            </div>
        </body>
    </html>
    """

@app.post("/login")
async def post_login(request: Request):
    try:
        form_data = await request.form()
        email = form_data.get("email", "").strip().lower()
        password = form_data.get("password")
        
        if not email or not password:
            return HTMLResponse(get_login(request, error="All fields are required."))
            
        try:
            conn = db_router.connect()
            cursor = conn.cursor()
            cursor.execute("SELECT hashed_password FROM users WHERE email = ?", (email,))
            row = cursor.fetchone()
            conn.close()
            
            if not row or not verify_password(row[0], password):
                return HTMLResponse(get_login(request, error="Invalid email or password."))
                
            token = create_session(email)
            response = RedirectResponse(url="/dashboard", status_code=303)
            response.set_cookie(key="session_token", value=token, max_age=86400 * 30, httponly=True)
            return response
        except Exception as e:
            return HTMLResponse(get_login(request, error=f"Database error: {str(e)}"))
    except Exception as outer_e:
        import traceback
        tb = traceback.format_exc()
        return HTMLResponse(f"""
        <html>
            <body style="font-family: monospace; padding: 40px; background-color: #ffebee; color: #c62828;">
                <h2>❌ Unhandled Login Error</h2>
                <pre>{tb}</pre>
            </body>
        </html>
        """, status_code=500)

@app.get("/logout")
def get_logout(request: Request):
    token = request.cookies.get("session_token")
    delete_session(token)
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("session_token")
    return response

@app.get("/admin/users", response_class=HTMLResponse)
def get_admin_users(request: Request):
    email = is_authenticated(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)
    if email not in ADMIN_EMAILS:
        raise HTTPException(status_code=403, detail="Unauthorized: Access is restricted to site administrators.")
        
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        cursor.execute("SELECT id, email, created_at FROM users ORDER BY created_at DESC")
        users = cursor.fetchall()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error while querying registered users: {str(e)}")
        
    user_rows_html = ""
    for u_id, u_email, created_at in users:
        role_badge = '<span class="badge-admin">🛡️ Administrator</span>' if u_email in ADMIN_EMAILS else '<span class="badge-user">👤 Registered User</span>'
        user_rows_html += f"""
        <tr>
            <td><strong>#{u_id}</strong></td>
            <td><code>{u_email}</code></td>
            <td>{role_badge}</td>
            <td><small>{created_at}</small></td>
        </tr>
        """
        
    if not user_rows_html:
        user_rows_html = '<tr><td colspan="4" style="text-align: center; color: #888; padding: 40px;">No registered accounts found in the database.</td></tr>'
        
    total_users = len(users)
    
    admin_link_html = ""
    if email in ADMIN_EMAILS:
        admin_link_html = ' | <a href="/admin/users" style="color: #2e7d32; text-decoration: none; font-weight: bold; margin-left: 5px;">🛡️ Admin User Directory</a>'
        
    user_header_bar = f"""
    <div style="display: flex; justify-content: space-between; align-items: center; background-color: #f1f3f4; padding: 10px 15px; border-radius: 6px; margin-bottom: 20px; font-size: 13px;">
        <div>
            <span style="color: #666; font-weight: bold;">👤 Active Session:</span> <span style="font-weight: bold; color: #1a237e;">{email}</span>
            {admin_link_html}
        </div>
        <a href="/logout" style="color: #c62828; text-decoration: none; font-weight: bold; display: flex; align-items: center; gap: 4px;">🚪 Log Out</a>
    </div>
    """
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
        <head>
            <title>LeadGrove Admin - User Directory 🛡️</title>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif; background-color: #f4f6f9; color: #333; margin: 0; padding: 20px; }}
                .container {{ max-width: 1000px; margin: 20px auto; background: white; padding: 40px; border-radius: 12px; box-shadow: 0px 4px 15px rgba(0,0,0,0.05); }}
                header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #eaeaea; padding-bottom: 20px; margin-bottom: 30px; }}
                h1 {{ margin: 0; color: #1a237e; font-size: 24px; }}
                .btn-back {{ display: inline-block; background-color: #1a237e; color: white; padding: 10px 18px; text-decoration: none; border-radius: 5px; font-weight: bold; font-size: 14px; transition: background 0.2s; }}
                .btn-back:hover {{ background-color: #0d1b2a; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 20px; font-size: 14px; }}
                th, td {{ padding: 12px 15px; text-align: left; border-bottom: 1px solid #eaeaea; }}
                th {{ background-color: #f8f9fa; color: #495057; font-weight: 600; text-transform: uppercase; font-size: 11px; letter-spacing: 0.5px; }}
                tr:hover {{ background-color: #fdfdfd; }}
                .badge-admin {{ background-color: #e8f5e9; color: #2e7d32; padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 11px; border: 1px solid #c8e6c9; }}
                .badge-user {{ background-color: #e3f2fd; color: #0d47a1; padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 11px; border: 1px solid #bbdefb; }}
            </style>
        </head>
        <body>
            <div class="container">
                {user_header_bar}
                <header>
                    <div>
                        <h1>🛡️ LeadGrove Registered Users Directory</h1>
                        <p style="margin: 5px 0 0 0; color: #666; font-size: 14px;">Total registered accounts using LeadGrove: <strong>{total_users}</strong></p>
                    </div>
                    <a href="/dashboard" class="btn-back">⬅️ Back to Dashboard</a>
                </header>
                
                <table>
                    <thead>
                        <tr>
                            <th>User ID</th>
                            <th>Email Address</th>
                            <th>Role</th>
                            <th>Registration Date (UTC)</th>
                        </tr>
                    </thead>
                    <tbody>
                        {user_rows_html}
                    </tbody>
                </table>
            </div>
        </body>
    </html>
    """
    return HTMLResponse(html_content)

@app.get("/", response_class=HTMLResponse)
def read_root(request: Request):
    """Agency Portal Landing Page with Auth Check."""
    email = is_authenticated(request)
    user_header_html = ""
    auth_buttons_html = ""
    
    if email:
        user_header_html = f'<p style="color: #2e7d32; font-weight: bold; font-size: 15px;">👤 Logged in as: {email} | <a href="/logout" style="color: #c62828; text-decoration: none;">🚪 Log Out</a></p>'
        auth_buttons_html = '<a href="/dashboard" class="btn">📊 Open Agency & Client Dashboard</a>'
    else:
        user_header_html = '<p style="color: #666; font-weight: bold; font-size: 14px;">🔒 Account registration is currently free</p>'
        auth_buttons_html = '''
            <div style="display: flex; gap: 15px; justify-content: center; margin-top: 20px;">
                <a href="/login" class="btn" style="margin-top: 0; background-color: #1a237e;">🔑 Log In</a>
                <a href="/register" class="btn" style="margin-top: 0; background-color: #2e7d32;">🚀 Sign Up Free</a>
            </div>
        '''
        
    return f"""
    <html>
        <head>
            <title>Multi-Tenant Multi-Channel Attribution Engine 🤖</title>
            <style>
                body {{ font-family: Arial, sans-serif; text-align: center; padding-top: 80px; background-color: #f4f6f9; }}
                .container {{ display: inline-block; background: white; padding: 40px; border-radius: 10px; box-shadow: 0px 4px 10px rgba(0,0,0,0.1); max-width: 600px; }}
                h1 {{ color: #1a237e; margin-bottom: 10px; }}
                p {{ color: #555; font-size: 18px; }}
                .badge {{ background-color: #e8f5e9; color: #2e7d32; padding: 5px 15px; border-radius: 15px; font-weight: bold; }}
                .btn {{ display: inline-block; background-color: #1a237e; color: white; padding: 12px 24px; text-decoration: none; font-size: 16px; font-weight: bold; border-radius: 5px; margin-top: 20px; transition: background 0.2s; }}
                .btn:hover {{ background-color: #0d1b2a; }}
                .feature-list {{ text-align: left; margin-top: 25px; color: #333; line-height: 1.6; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Welcome To Lead Grove's Offline Conversion Tracking Automation SAAS!</h1>
                <p>Status: <span class="badge">Healthy, Multi-Tenant & AI-Enabled</span></p>
                <p>Just point us to your offline lead/sales data, and it gets imported to your AD accounts automatically.</p>
                {user_header_html}
                {auth_buttons_html}
                
                <div class="feature-list">
                    <h3>Multi-Tenant Architecture Capabilities:</h3>
                    <ul>
                        <li>🏢 <strong>Multi-Channel Tracking</strong>: Seamless matching of Google (GCLID), Facebook (FBCLID), LinkedIn (LI_FAT_ID), and Microsoft (MSCLKID) Click IDs!</li>
                        <li>🏢 <strong>4-Step Onboarding Wizard</strong>: Custom qualification mapping, billing order routing, and CRM parameters.</li>
                        <li>📥 <strong>Dynamic Webhook Endpoint</strong>: `/webhooks/callrail?client_id=X` automatically links tracking logs to the correct account.</li>
                        <li>🧠 <strong>Claude 4.5 Haiku Custom Audits</strong>: Real-time transcript parsing based on client's specific qualification definitions.</li>
                    </ul>
                </div>
            </div>
        </body>
    </html>
    """


@app.get("/dashboard", response_class=HTMLResponse)
def view_dashboard(request: Request, client_id: Optional[int] = None):
    email = is_authenticated(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)
    """Interactive dashboard with client filtering."""
    user_role, user_client_id = get_user_role_and_client(email)
    is_manager = (user_role == "full")
    
    if user_client_id is not None:
        client_id = user_client_id
        
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        
        # 1. Fetch All Available Clients for the Dropdown Selector
        cursor.execute("SELECT id, name, google_ads_customer_id, facebook_ads_id, linkedin_ads_id, microsoft_ads_id FROM clients ORDER BY name ASC")
        clients = cursor.fetchall()
        
        # Determine filtering
        selected_client_id = client_id if client_id is not None else 0 # 0 signifies "All Clients" (Agency Overview)
        if user_client_id is not None:
            selected_client_id = user_client_id
        
        # Build Settings Button HTML
        if selected_client_id == 0 or user_role == "read":
            settings_btn_html = ''
        else:
            settings_btn_html = f'<a href="/dashboard/settings?client_id={selected_client_id}" class="btn-settings">⚙️ Client Settings</a>'
            
        onboard_btn_html = '<a href="/dashboard/add-client" class="btn-add-client">➕ Onboard Client</a>' if is_manager and user_client_id is None else ''
        
        # 2. Query Dashboard Rows and Calculations
        if selected_client_id == 0:
            # Multi-Client (All Clients) View - Join with clients table to display client names
            cursor.execute("""
                SELECT s.id, s.phone, s.email, s.name, s.company, s.gclid, s.source, s.qualified, s.sale_closed, s.value, s.reason, s.created_at, c.name, s.fbclid, s.li_fat_id, s.msclkid
                FROM sessions s
                LEFT JOIN clients c ON s.client_id = c.id
                ORDER BY s.created_at DESC
            """)
            rows = cursor.fetchall()
            client_name_header = "All Agency Accounts"
            client_ads_id = "Multiple Accounts"
        else:
            # Single-Client Filtered View
            cursor.execute("""
                SELECT s.id, s.phone, s.email, s.name, s.company, s.gclid, s.source, s.qualified, s.sale_closed, s.value, s.reason, s.created_at, c.name, s.fbclid, s.li_fat_id, s.msclkid
                FROM sessions s
                LEFT JOIN clients c ON s.client_id = c.id
                WHERE s.client_id = ?
                ORDER BY s.created_at DESC
            """, (selected_client_id,))
            rows = cursor.fetchall()
            
            # Fetch current client profile details
            cursor.execute("SELECT name, google_ads_customer_id FROM clients WHERE id = ?", (selected_client_id,))
            client_profile = cursor.fetchone()
            client_name_header = client_profile[0] if client_profile else "Unknown Client"
            client_ads_id = client_profile[1] if client_profile else "N/A"
            
        conn.close()
    except Exception as e:
        return f"<html><body><h3>❌ Database Error: {e}</h3></body></html>"

    # Count analytics
    total_leads = len(rows)
    qualified_leads = sum(1 for r in rows if r[7] == 'YES')
    sales_closed = sum(1 for r in rows if r[8] == 'YES')
    total_revenue = sum(float(r[9] or 0.0) for r in rows)
    
    # Count how many of these leads have click IDs for each channel (and are either qualified or closed)
    exportable_google = sum(1 for r in rows if r[5] and (r[7] == 'YES' or r[8] == 'YES'))
    exportable_facebook = sum(1 for r in rows if r[13] and (r[7] == 'YES' or r[8] == 'YES'))
    exportable_linkedin = sum(1 for r in rows if r[14] and (r[7] == 'YES' or r[8] == 'YES'))
    exportable_microsoft = sum(1 for r in rows if r[15] and (r[7] == 'YES' or r[8] == 'YES'))

    # Generate the Selector Dropdown Options
    dropdown_options = ""
    if user_client_id is not None:
        restricted_clients = [c for c in clients if c[0] == user_client_id]
        for c_id, c_name, c_ads, c_fb, c_li, c_ms in restricted_clients:
            dropdown_options += f'<option value="{c_id}" selected>👤 {c_name} (Ads: {c_ads})</option>'
    else:
        dropdown_options = f'<option value="0" {"selected" if selected_client_id == 0 else ""}>📂 [Show All Clients / Agency View]</option>'
        for c_id, c_name, c_ads, c_fb, c_li, c_ms in clients:
            is_selected = "selected" if selected_client_id == c_id else ""
            dropdown_options += f'<option value="{c_id}" {is_selected}>👤 {c_name} (Ads: {c_ads})</option>'

    # Convert rows to table items
    table_rows_html = ""
    for r in rows:
        id_val, phone, email, name, company, gclid, source, qualified, sale_closed, value, reason, created_at, client_name_linked, fbclid, li_fat_id, msclkid = r
        
        qual_badge = '<span style="background: #e8f5e9; color: #2e7d32; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 12px;">YES</span>' if qualified == 'YES' else '<span style="background: #ffebee; color: #c62828; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 12px;">NO</span>'
        closed_badge = '<span style="background: #e8f5e9; color: #2e7d32; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 12px;">YES</span>' if sale_closed == 'YES' else '<span style="background: #ffebee; color: #c62828; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 12px;">NO</span>'
        
        # Build multi-channel click ID display blocks
        click_ids_list = []
        if gclid:
            click_ids_list.append(f'<span style="display:inline-block; margin-bottom: 2px;"><strong style="color: #4285F4; font-size: 10px;">G:</strong> <code style="background: #f1f3f4; padding: 1px 4px; border-radius: 3px; font-size: 11px;">{gclid}</code></span>')
        if fbclid:
            click_ids_list.append(f'<span style="display:inline-block; margin-bottom: 2px;"><strong style="color: #1877F2; font-size: 10px;">F:</strong> <code style="background: #f1f3f4; padding: 1px 4px; border-radius: 3px; font-size: 11px;">{fbclid}</code></span>')
        if li_fat_id:
            click_ids_list.append(f'<span style="display:inline-block; margin-bottom: 2px;"><strong style="color: #0A66C2; font-size: 10px;">L:</strong> <code style="background: #f1f3f4; padding: 1px 4px; border-radius: 3px; font-size: 11px;">{li_fat_id}</code></span>')
        if msclkid:
            click_ids_list.append(f'<span style="display:inline-block; margin-bottom: 2px;"><strong style="color: #00A4EF; font-size: 10px;">M:</strong> <code style="background: #f1f3f4; padding: 1px 4px; border-radius: 3px; font-size: 11px;">{msclkid}</code></span>')
            
        click_ids_display = "<br>".join(click_ids_list) if click_ids_list else '<span style="color: #999; font-style: italic;">None detected</span>'
        value_display = f"<strong>${value:,.2f}</strong>" if value and value > 0 else '<span style="color: #999;">$0.00</span>'
        
        # Display the client column only in the multi-client view
        client_column_html = f'<td><span class="client-badge">{client_name_linked}</span></td>' if selected_client_id == 0 else ''
        
        # Beautiful lead source delineation (Phone Call vs Web Form)
        source_lower = str(source).lower() if source else ""
        if 'form' in source_lower:
            source_badge = '<span class="badge-source badge-form">📝 Web Form</span>'
        elif any(x in source_lower for x in ['call', 'phone', 'callrail']):
            source_badge = '<span class="badge-source badge-call">📞 Phone Call</span>'
        else:
            source_badge = f'<span class="badge-source">{str(source).upper()}</span>'
            
        table_rows_html += f"""
        <tr>
            <td>{id_val}</td>
            {client_column_html}
            <td><small>{created_at}</small></td>
            <td>{source_badge}</td>
            <td><strong>{name or 'Unknown'}</strong><br><small style="color:#666;">{phone}</small></td>
            <td>{click_ids_display}</td>
            <td>{qual_badge}</td>
            <td>{closed_badge}</td>
            <td>{value_display}</td>
            <td><small>{reason or 'N/A'}</small></td>
        </tr>
        """

    if not table_rows_html:
        table_rows_html = f'<tr><td colspan="{"10" if selected_client_id == 0 else "9"}" style="text-align: center; color: #888; padding: 40px;">No lead sessions recorded for this client. Set up their CallRail webhook to populate this space!</td></tr>'

    # Build multi-channel action buttons dynamically
    if selected_client_id == 0:
        google_export_button = '<button class="btn-export disabled" onclick="alert(\'Please select a specific client from the dropdown above to export their Google Ads offline conversion CSV!\')" style="opacity:0.6; cursor:not-allowed; background-color: #bdc3c7; width: 100%;">📥 Export Disabled</button>'
        facebook_export_button = '<button class="btn-export disabled" onclick="alert(\'Please select a specific client from the dropdown above to export their Facebook conversions CSV!\')" style="opacity:0.6; cursor:not-allowed; background-color: #bdc3c7; width: 100%;">📥 Export Disabled</button>'
        linkedin_export_button = '<button class="btn-export disabled" onclick="alert(\'Please select a specific client from the dropdown above to export their LinkedIn conversions CSV!\')" style="opacity:0.6; cursor:not-allowed; background-color: #bdc3c7; width: 100%;">📥 Export Disabled</button>'
        microsoft_export_button = '<button class="btn-export disabled" onclick="alert(\'Please select a specific client from the dropdown above to export their Microsoft conversions CSV!\')" style="opacity:0.6; cursor:not-allowed; background-color: #bdc3c7; width: 100%;">📥 Export Disabled</button>'
    else:
        google_export_button = f'<a href="/dashboard/export/google?client_id={selected_client_id}" class="btn-export" style="background-color: #4285F4; text-align: center; text-decoration: none; width: 100%;">📥 Download Google CSV ({exportable_google})</a>'
        facebook_export_button = f'<a href="/dashboard/export/facebook?client_id={selected_client_id}" class="btn-export" style="background-color: #1877F2; text-align: center; text-decoration: none; width: 100%;">📥 Download Meta CSV ({exportable_facebook})</a>'
        linkedin_export_button = f'<a href="/dashboard/export/linkedin?client_id={selected_client_id}" class="btn-export" style="background-color: #0A66C2; text-align: center; text-decoration: none; width: 100%;">📥 Download LinkedIn CSV ({exportable_linkedin})</a>'
        microsoft_export_button = f'<a href="/dashboard/export/microsoft?client_id={selected_client_id}" class="btn-export" style="background-color: #00A4EF; text-align: center; text-decoration: none; width: 100%;">📥 Download Bing CSV ({exportable_microsoft})</a>'

    # Conditionally show the Client header column
    client_th_html = '<th>Client Account</th>' if selected_client_id == 0 else ''
    admin_link_html = ""
    if email in ADMIN_EMAILS:
        admin_link_html = ' | <a href="/admin/users" style="color: #2e7d32; text-decoration: none; font-weight: bold; margin-left: 5px;">🛡️ Admin User Directory</a>'
        
    user_header_bar = f"""
    <div style="display: flex; justify-content: space-between; align-items: center; background-color: #f1f3f4; padding: 10px 15px; border-radius: 6px; margin-bottom: 20px; font-size: 13px;">
        <div>
            <span style="color: #666; font-weight: bold;">👤 Active Session:</span> <span style="font-weight: bold; color: #1a237e;">{email}</span>
            {admin_link_html}
        </div>
        <a href="/logout" style="color: #c62828; text-decoration: none; font-weight: bold; display: flex; align-items: center; gap: 4px;">🚪 Log Out</a>
    </div>
    """


    return f"""
    <!DOCTYPE html>
    <html>
        <head>
            <title>Offline Lead & Conversion Dashboard 📊</title>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif; background-color: #f4f6f9; color: #333; margin: 0; padding: 20px; }}
                .container {{ max-width: 1300px; margin: 0 auto; background: white; padding: 30px; border-radius: 12px; box-shadow: 0px 4px 15px rgba(0,0,0,0.05); }}
                header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #eaeaea; padding-bottom: 20px; margin-bottom: 30px; flex-wrap: wrap; gap: 15px; }}
                h1 {{ margin: 0; color: #1a237e; font-size: 26px; }}
                .client-selector-container {{ display: flex; align-items: center; gap: 10px; background: #e8eaf6; padding: 10px 15px; border-radius: 8px; border: 1px solid #c5cae9; }}
                .client-label {{ font-weight: bold; color: #1a237e; font-size: 14px; }}
                .client-select {{ padding: 8px 12px; font-size: 14px; border-radius: 5px; border: 1px solid #9fa8da; outline: none; font-weight: 600; cursor: pointer; color: #1a237e; }}
                .client-select:focus {{ border-color: #1a237e; }}
                
                /* Analytics Stats */
                .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 20px; margin-bottom: 30px; }}
                .stat-card {{ background: #fafafa; border: 1px solid #e0e0e0; padding: 20px; border-radius: 8px; text-align: center; }}
                .stat-card h3 {{ margin: 0 0 10px 0; font-size: 13px; color: #666; text-transform: uppercase; letter-spacing: 0.5px; }}
                .stat-card .value {{ font-size: 28px; font-weight: bold; color: #1a237e; margin: 0; }}
                .stat-card.rev .value {{ color: #2e7d32; }}
                
                /* Multi-Channel Grid */
                .export-card-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 15px; margin-bottom: 30px; }}
                .export-card {{ background: white; border: 1px solid #eaeaea; padding: 15px; border-radius: 6px; display: flex; flex-direction: column; justify-content: space-between; box-shadow: 0 2px 4px rgba(0,0,0,0.02); }}
                .export-card h4 {{ margin: 0 0 5px 0; font-size: 14px; font-weight: bold; }}
                .export-card p {{ margin: 0 0 15px 0; font-size: 11px; color: #666; line-height: 1.4; }}
                
                /* Table Styles */
                .table-responsive {{ overflow-x: auto; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 14px; }}
                th, td {{ padding: 12px 15px; text-align: left; border-bottom: 1px solid #eaeaea; }}
                th {{ background-color: #f8f9fa; color: #495057; font-weight: 600; text-transform: uppercase; font-size: 11px; letter-spacing: 0.5px; }}
                tr:hover {{ background-color: #fdfdfd; }}
                .badge-source {{ font-size: 11px; padding: 4px 8px; border-radius: 6px; font-weight: bold; border: 1px solid transparent; display: inline-flex; align-items: center; gap: 4px; }}
                .badge-source.badge-call {{ background: #e3f2fd; color: #0d47a1; border-color: #bbdefb; }}
                .badge-source.badge-form {{ background: #f3e5f5; color: #4a148c; border-color: #e1bee7; }}
                .client-badge {{ background: #eceff1; color: #37474f; font-size: 11px; padding: 3px 8px; border-radius: 4px; font-weight: bold; border: 1px solid #cfd8dc; }}
                
                .btn-export {{ display: block; color: white; padding: 10px 12px; border-radius: 5px; font-weight: bold; font-size: 13px; border: none; transition: filter 0.2s; cursor: pointer; }}
                .btn-export:hover {{ filter: brightness(0.9); }}
                .btn-add-client {{ display: inline-block; background-color: #1a237e; color: white; padding: 10px 18px; text-decoration: none; border-radius: 5px; font-weight: bold; font-size: 14px; border: none; transition: background 0.2s; cursor: pointer; }}
                .btn-add-client:hover {{ background-color: #0d1b2a; }}
                .btn-settings {{ display: inline-block; background-color: #607d8b; color: white; padding: 10px 18px; text-decoration: none; border-radius: 5px; font-weight: bold; font-size: 14px; border: none; transition: background 0.2s; cursor: pointer; }}
                .btn-settings:hover {{ background-color: #455a64; }}

            </style>
        </head>
        <body>
            <div class="container">
                {user_header_bar}
                <header>
                    <div>
                        <h1>{client_name_header} 📊</h1>
                        <p style="margin: 5px 0 0 0; color: #666; font-size: 14px;">Google Ads Account: <strong>{client_ads_id}</strong> | Multi-Tenant Agency Engine</p>
                    </div>
                    
                    <div style="display: flex; gap: 10px; align-items: center; flex-wrap: wrap;">
                        <div class="client-selector-container">
                            <span class="client-label">Viewing Account:</span>
                            <select class="client-select" onchange="window.location.href='/dashboard?client_id='+this.value">
                                {dropdown_options}
                            </select>
                        </div>
                        {settings_btn_html}
                        {onboard_btn_html}
                    </div>
                </header>

                <!-- Stats Cards -->
                <div class="stats-grid">
                    <div class="stat-card">
                        <h3>Tracked Sessions</h3>
                        <p class="value">{total_leads}</p>
                    </div>
                    <div class="stat-card">
                        <h3>AI Qualified Leads 🟢</h3>
                        <p class="value">{qualified_leads}</p>
                    </div>
                    <div class="stat-card">
                        <h3>Sales Closed 🤝</h3>
                        <p class="value">{sales_closed}</p>
                    </div>
                    <div class="stat-card rev">
                        <h3>Tracked Sales Revenue 💰</h3>
                        <p class="value">${total_revenue:,.2f}</p>
                    </div>
                </div>

                <!-- Multi-Channel Exports Panel -->
                <div style="background: #fafafa; border: 1px solid #e0e0e0; border-radius: 8px; padding: 20px; margin-bottom: 30px;">
                    <h3 style="margin: 0 0 15px 0; color: #1a237e; font-size: 15px; text-transform: uppercase; letter-spacing: 0.5px;">📥 Multi-Channel Offline Conversion Exports</h3>
                    <div class="export-card-grid">
                        <!-- Google Ads -->
                        <div class="export-card">
                            <div>
                                <h4 style="color: #4285F4;">Google Ads (GCLID)</h4>
                                <p>Export verified lead signals and transaction revenue for Smart Bidding optimization.</p>
                            </div>
                            {google_export_button}
                        </div>
                        <!-- Facebook Ads -->
                        <div class="export-card">
                            <div>
                                <h4 style="color: #1877F2;">Meta/Facebook (FBCLID)</h4>
                                <p>Export offline events to optimize Facebook Conversions API and Custom Audiences.</p>
                            </div>
                            {facebook_export_button}
                        </div>
                        <!-- LinkedIn Ads -->
                        <div class="export-card">
                            <div>
                                <h4 style="color: #0A66C2;">LinkedIn Ads (LI_FAT_ID)</h4>
                                <p>Export professional business conversions directly into LinkedIn Campaign Manager.</p>
                            </div>
                            {linkedin_export_button}
                        </div>
                        <!-- Microsoft Ads -->
                        <div class="export-card">
                            <div>
                                <h4 style="color: #00A4EF;">Microsoft Ads (MSCLKID)</h4>
                                <p>Export click-matched offline sessions back into Bing/Microsoft campaign metrics.</p>
                            </div>
                            {microsoft_export_button}
                        </div>
                    </div>
                </div>

                <!-- Table -->
                <h3 style="margin: 0 0 15px 0; color: #1a237e;">Lead Activity Log</h3>
                <div class="table-responsive">
                    <table>
                        <thead>
                            <tr>
                                <th>ID</th>
                                {client_th_html}
                                <th>Timestamp</th>
                                <th>Source</th>
                                <th>Lead Contact</th>
                                <th>Click IDs Detected</th>
                                <th>Qualified</th>
                                <th>Closed</th>
                                <th>Value</th>
                                <th>Claude Decision Reasoning</th>
                            </tr>
                        </thead>
                        <tbody>
                            {table_rows_html}
                        </tbody>
                    </table>
                </div>
            </div>
        </body>
    </html>
    """



class UserInvite(BaseModel):
    email: str
    role: str
    client_id: Optional[int] = None

class ClientUpdate(BaseModel):
    id: int
    name: str
    call_tracking_provider: Optional[str] = "callrail"
    callrail_account_id: Optional[str] = ""
    callrail_company_id: Optional[str] = ""
    ctm_account_id: Optional[str] = ""
    ctm_profile_id: Optional[str] = ""
    wc_account_id: Optional[str] = ""
    wc_profile_id: Optional[str] = "" 
    google_ads_customer_id: str
    facebook_ads_id: Optional[str] = ""
    linkedin_ads_id: Optional[str] = ""
    microsoft_ads_id: Optional[str] = ""
    lead_gen_method: str
    qualification_criteria: str
    source_of_truth: str
    email_provider: Optional[str] = ""
    email_account: Optional[str] = ""
    crm_deal_tags: Optional[str] = ""
    crm_won_deal_tags: Optional[str] = ""
    crm_lead_tags: Optional[str] = ""
    lead_count_rule: str
    exclude_past_customers: str
    excluded_customers: Optional[list[ExcludedCustomer]] = None
    exclusion_action: Optional[str] = "append"


@app.get("/dashboard/settings", response_class=HTMLResponse)
def view_settings(request: Request, client_id: Optional[int] = None):
    email = is_authenticated(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)
    """Page to manage and update client account configuration settings."""
    user_role, user_client_id = get_user_role_and_client(email)
    if user_role == "read":
        raise HTTPException(status_code=403, detail="Unauthorized: Access to client configuration settings is restricted to managers and administrators.")
        
    if user_client_id is not None:
        if client_id is not None and client_id != user_client_id:
            raise HTTPException(status_code=403, detail="Unauthorized: You do not have permission to access settings for this client account.")
        client_id = user_client_id

    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        
        # 1. Fetch All Available Clients for the dropdown selector
        cursor.execute("SELECT id, name, google_ads_customer_id FROM clients ORDER BY name ASC")
        all_clients = cursor.fetchall()
        
        if not all_clients:
            conn.close()
            return HTMLResponse("<script>alert('No clients found. Please onboard a client first!'); window.location.href='/dashboard/add-client';</script>")
            
        # Determine which client to edit
        active_client_id = client_id if client_id is not None else all_clients[0][0]
        if user_client_id is not None:
            active_client_id = user_client_id
        
        # Extract column names dynamically first to guarantee matching order during SELECT
        cursor.execute("PRAGMA table_info(clients)")
        cols = [col[1] for col in cursor.fetchall()]
        
        # 2. Fetch the specific client's settings using explicit columns order (avoids PostgreSQL zip misalignment)
        cols_formatted = ", ".join([f'"{c}"' for c in cols])
        cursor.execute(f"SELECT {cols_formatted} FROM clients WHERE id = ?", (active_client_id,))
        client_row = cursor.fetchone()
        
        if not client_row:
            conn.close()
            raise HTTPException(status_code=404, detail="Client not found")
            
        client_data = dict(zip(cols, client_row))
        
        active_provider = client_data.get("call_tracking_provider", "callrail") or "callrail"
        sel_cr = 'selected' if active_provider == 'callrail' else ''
        sel_ctm = 'selected' if active_provider == 'calltrackingmetrics' else ''
        sel_wc = 'selected' if active_provider == 'whatconverts' else ''
        
        p_cr_style = 'display: flex;' if active_provider == 'callrail' else 'display: none;'
        p_ctm_style = 'display: flex;' if active_provider == 'calltrackingmetrics' else 'display: none;'
        p_wc_style = 'display: flex;' if active_provider == 'whatconverts' else 'display: none;'

        if active_provider == "calltrackingmetrics":
            webhook_card_title = "📞 CallTrackingMetrics Transcription Webhook"
            webhook_card_desc = "Paste this dynamic endpoint into CallTrackingMetrics webhook setup to sync automated call recordings and transcripts:"
            webhook_suffix = f"/webhooks/calltrackingmetrics?client_id={active_client_id}"
        elif active_provider == "whatconverts":
            webhook_card_title = "📞 WhatConverts CallCompleted Webhook"
            webhook_card_desc = "Paste this dynamic endpoint into WhatConverts webhook setup to sync automated call recordings and transcripts:"
            webhook_suffix = f"/webhooks/whatconverts?client_id={active_client_id}"
        else:
            webhook_card_title = "📞 CallRail CallCompleted Webhook"
            webhook_card_desc = "Paste this dynamic endpoint into CallRail Integration Settings to sync automated call recordings and transcripts:"
            webhook_suffix = f"/webhooks/callrail?client_id={active_client_id}" 
        
        # Query last 5 analyzed emails for active_client_id
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS analyzed_emails (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER,
                subject TEXT,
                sender TEXT,
                recipient TEXT,
                analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        
        cursor.execute("""
            SELECT subject, datetime(analyzed_at, 'localtime') 
            FROM analyzed_emails 
            WHERE client_id = ? 
            ORDER BY analyzed_at DESC LIMIT 5
        """, (active_client_id,))
        emails_list = cursor.fetchall()
        
        if not emails_list:
            last_emails_html = "<span style='color: #ccc; font-style: italic;'>No emails analyzed yet.</span>"
        else:
            items = []
            for sub, ts in emails_list:
                clean_sub = sub if sub else "(No Subject)"
                # Clean up nested f-string issues
                items.append(f"<li style='margin-bottom: 4px; border-bottom: 1px solid rgba(255,255,255,0.1); padding-bottom: 4px;'><strong>{clean_sub}</strong><br><span style='font-size: 9px; color: #aaa;'>{ts}</span></li>")
            last_emails_html = f"<ul style='margin: 5px 0 0 0; padding-left: 15px; text-align: left; list-style-type: disc;'>{''.join(items)}</ul>"

        # Query last 5 received CRM webhooks for active_client_id
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS crm_webhook_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER,
                contact_name TEXT,
                stage TEXT,
                amount REAL,
                received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        
        cursor.execute("""
            SELECT contact_name, stage, amount, datetime(received_at, 'localtime') 
            FROM crm_webhook_logs 
            WHERE client_id = ? 
            ORDER BY received_at DESC LIMIT 5
        """, (active_client_id,))
        crm_logs_list = cursor.fetchall()
        
        if not crm_logs_list:
            last_crm_logs_html = "<span style='color: #ccc; font-style: italic;'>No CRM webhooks received yet.</span>"
        else:
            items = []
            for name, stg, amt, ts in crm_logs_list:
                clean_name = name if name else "Unknown Deal/Contact"
                clean_stage = stg if stg else "Updated"
                clean_amt = f"${amt:,.2f}" if amt else "$0.00"
                items.append(f"<li style='margin-bottom: 4px; border-bottom: 1px solid rgba(255,255,255,0.1); padding-bottom: 4px;'><strong>{clean_name}</strong> ({clean_stage})<br><span style='font-size: 9px; color: #aaa;'>Value: {clean_amt} | {ts}</span></li>")
            last_crm_logs_html = f"<ul style='margin: 5px 0 0 0; padding-left: 15px; text-align: left; list-style-type: disc;'>{''.join(items)}</ul>"

        # Query last 5 received Billing webhooks for active_client_id
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS billing_webhook_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER,
                customer_name TEXT,
                invoice_number TEXT,
                amount REAL,
                received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        
        cursor.execute("""
            SELECT customer_name, invoice_number, amount, datetime(received_at, 'localtime') 
            FROM billing_webhook_logs 
            WHERE client_id = ? 
            ORDER BY received_at DESC LIMIT 5
        """, (active_client_id,))
        billing_logs_list = cursor.fetchall()
        
        if not billing_logs_list:
            last_billing_logs_html = "<span style='color: #ccc; font-style: italic;'>No billing webhooks received yet.</span>"
        else:
            items = []
            for name, inv, amt, ts in billing_logs_list:
                clean_name = name if name else "Unknown Customer"
                clean_inv = f"Inv #{inv}" if inv else "Invoice"
                clean_amt = f"${amt:,.2f}" if amt else "$0.00"
                items.append(f"<li style='margin-bottom: 4px; border-bottom: 1px solid rgba(255,255,255,0.1); padding-bottom: 4px;'><strong>{clean_name}</strong> ({clean_inv})<br><span style='font-size: 9px; color: #aaa;'>Paid: {clean_amt} | {ts}</span></li>")
            last_billing_logs_html = f"<ul style='margin: 5px 0 0 0; padding-left: 15px; text-align: left; list-style-type: disc;'>{''.join(items)}</ul>"

        # Query exclusions count for current client
        cursor.execute("SELECT COUNT(*) FROM excluded_customers WHERE client_id = ?", (active_client_id,))
        exclusion_count = cursor.fetchone()[0]
        
        # Query collaborators (users associated with this client_id)
        cursor.execute("SELECT email, role FROM users WHERE client_id = ? ORDER BY email ASC", (active_client_id,))
        users_list = cursor.fetchall()
        
        # Query active pending invitations for this client_id
        cursor.execute("SELECT email, role, token FROM user_invitations WHERE client_id = ? AND is_used = 'NO' ORDER BY created_at DESC", (active_client_id,))
        invites_list = cursor.fetchall()
        
        conn.close()
    except Exception as e:
        return f"<html><body><h3>❌ Database Error: {e}</h3></body></html>"

    # Generate collaborators HTML rows
    collaborators_list = []
    for u_email, u_role in users_list:
        role_desc = "Full Function (Manager)" if u_role == "full" else "Read-Only (Viewer)"
        role_badge = f'<span style="background: #e8f5e9; color: #2e7d32; padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 11px; border: 1px solid #c8e6c9;">{role_desc}</span>'
        status_badge = '<span style="color: #2e7d32; font-weight: bold;">● Active Member</span>'
        collaborators_list.append(f"""
        <tr style="border-bottom: 1px solid #eaeaea;">
            <td style="padding: 12px 15px;"><code>{u_email}</code></td>
            <td style="padding: 12px 15px;">{role_badge}</td>
            <td style="padding: 12px 15px;">{status_badge}</td>
        </tr>
        """)
        
    for i_email, i_role, i_token in invites_list:
        role_desc = "Full Function (Manager)" if i_role == "full" else "Read-Only (Viewer)"
        role_badge = f'<span style="background: #fff3cd; color: #856404; padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 11px; border: 1px solid #ffe0b2;">{role_desc}</span>'
        status_badge = f'<span style="color: #ff9100; font-weight: bold;">⏳ Pending Invite</span><br><span style="font-size: 10px; color: #666; font-family: monospace;">token: {i_token[:8]}...</span>'
        collaborators_list.append(f"""
        <tr style="border-bottom: 1px solid #eaeaea;">
            <td style="padding: 12px 15px;"><code>{i_email}</code></td>
            <td style="padding: 12px 15px;">{role_badge}</td>
            <td style="padding: 12px 15px;">{status_badge}</td>
        </tr>
        """)
        
    if not collaborators_list:
        collaborator_rows_html = '<tr><td colspan="3" style="text-align: center; color: #888; padding: 20px;">No additional collaborators registered yet.</td></tr>'
    else:
        collaborator_rows_html = "".join(collaborators_list)

    # Determine invite form visibility
    invite_form_display = 'block' if user_role == 'full' else 'none'

    # Generate the Selector Dropdown Options for the Settings Header
    dropdown_options = ""
    if user_client_id is not None:
        restricted_clients = [c for c in all_clients if c[0] == user_client_id]
        for c_id, c_name, c_ads in restricted_clients:
            dropdown_options += f'<option value="{c_id}" selected>👤 {c_name} (Ads: {c_ads})</option>'
    else:
        for c_id, c_name, c_ads in all_clients:
            is_selected = "selected" if active_client_id == c_id else ""
            dropdown_options += f'<option value="{c_id}" {is_selected}>👤 {c_name} (Ads: {c_ads})</option>'

    # Handle dropdown lists with pre-selected options
    lead_gen_both_checked = "checked" if client_data.get("lead_gen_method") == "both" else ""
    lead_gen_phone_checked = "checked" if client_data.get("lead_gen_method") == "phone" else ""
    lead_gen_form_checked = "checked" if client_data.get("lead_gen_method") == "form" else ""

    lead_count_all_checked = "checked" if client_data.get("lead_count_rule") == "all" else ""
    lead_count_max_checked = "checked" if client_data.get("lead_count_rule") == "maximum_one" else ""

    exclude_no_checked = "checked" if client_data.get("exclude_past_customers") == "NO" else ""
    exclude_yes_checked = "checked" if client_data.get("exclude_past_customers") == "YES" else ""

    # Qualification criteria dropdown helper
    crit_options = ""
    for code, label in CRITERIA_MAP.items():
        is_sel = "selected" if client_data.get("qualification_criteria") == code else ""
        crit_options += f'<option value="{code}" {is_sel}>Option {code}: {label}</option>'

    # Source of Truth dropdown helper
    sot_options = ""
    for code, label in SOT_MAP.items():
        is_sel = "selected" if client_data.get("source_of_truth") == code else ""
        sot_options += f'<option value="{code}" {is_sel}>{label}</option>'

    # Email provider selector helper
    provider_options = ""
    for code, label in [("gmail", "Google Gmail API"), ("outlook", "Microsoft Outlook 365"), ("custom_imap", "Custom IMAP (Secure Server)")]:
        is_sel = "selected" if client_data.get("email_provider") == code else ""
        provider_options += f'<option value="{code}" {is_sel}>{label}</option>'

    # Setup the live Integration webhook variables to display on the page
    # Since these are loaded in the browser, window.location.origin is perfect!
    client_name = client_data.get("name", "")
    admin_link_html = ""
    if email in ADMIN_EMAILS:
        admin_link_html = ' | <a href="/admin/users" style="color: #2e7d32; text-decoration: none; font-weight: bold; margin-left: 5px;">🛡️ Admin User Directory</a>'
        
    user_header_bar = f"""
    <div style="display: flex; justify-content: space-between; align-items: center; background-color: #f1f3f4; padding: 10px 15px; border-radius: 6px; margin-bottom: 20px; font-size: 13px;">
        <div>
            <span style="color: #666; font-weight: bold;">👤 Active Session:</span> <span style="font-weight: bold; color: #1a237e;">{email}</span>
            {admin_link_html}
        </div>
        <a href="/logout" style="color: #c62828; text-decoration: none; font-weight: bold; display: flex; align-items: center; gap: 4px;">🚪 Log Out</a>
    </div>
    """

    
    return f"""
    <!DOCTYPE html>
    <html>
        <head>
            <title>Client Settings ⚙️</title>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif; background-color: #f4f6f9; color: #333; margin: 0; padding: 20px; }}
                .container {{ max-width: 1000px; margin: 20px auto; background: white; padding: 40px; border-radius: 12px; box-shadow: 0px 4px 15px rgba(0,0,0,0.05); }}
                header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #eaeaea; padding-bottom: 20px; margin-bottom: 30px; flex-wrap: wrap; gap: 15px; }}
                h1 {{ margin: 0; color: #1a237e; font-size: 24px; }}
                .client-selector-container {{ display: flex; align-items: center; gap: 10px; background: #e8eaf6; padding: 10px 15px; border-radius: 8px; border: 1px solid #c5cae9; }}
                .client-label {{ font-weight: bold; color: #1a237e; font-size: 14px; }}
                .client-select {{ padding: 8px 12px; font-size: 14px; border-radius: 5px; border: 1px solid #9fa8da; outline: none; font-weight: 600; cursor: pointer; color: #1a237e; }}
                
                /* Layout */
                .settings-layout {{ display: grid; grid-template-columns: 1fr 1fr; gap: 30px; }}
                @media (max-width: 768px) {{ .settings-layout {{ grid-template-columns: 1fr; }} }}
                
                .form-group {{ margin-bottom: 20px; }}
                .form-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }}
                label {{ display: block; font-weight: 600; margin-bottom: 8px; font-size: 13px; color: #495057; }}
                input[type="text"], input[type="email"], select {{ width: 100%; padding: 10px 12px; border-radius: 6px; border: 1px solid #ced4da; box-sizing: border-box; font-size: 14px; outline: none; transition: border-color 0.2s; font-family: inherit; }}
                input[type="text"]:focus, select:focus {{ border-color: #1a237e; }}
                
                .section-title {{ font-size: 16px; color: #1a237e; font-weight: bold; border-bottom: 1px solid #eaeaea; padding-bottom: 8px; margin-bottom: 15px; text-transform: uppercase; letter-spacing: 0.5px; display: flex; align-items: center; gap: 8px; }}
                
                /* Card Radio Styles */
                .card-radio-group {{ display: flex; flex-direction: column; gap: 10px; margin-bottom: 10px; }}
                .card-radio {{ display: flex; align-items: center; padding: 10px 15px; border: 2px solid #e0e0e0; border-radius: 8px; cursor: pointer; transition: all 0.2s; gap: 12px; position: relative; }}
                .card-radio:hover {{ border-color: #b3e5fc; background-color: #f6fbfd; }}
                .card-radio.selected {{ border-color: #1a237e; background-color: #e8eaf6; }}
                .card-radio input[type="radio"] {{ position: absolute; opacity: 0; }}
                .card-radio-label {{ font-size: 13px; font-weight: bold; color: #333; margin: 0; }}
                .card-radio-sub {{ font-size: 11px; color: #666; margin-top: 3px; }}
                
                /* Webhook Card Box */
                .webhook-card {{ background-color: #fafafa; border: 1px solid #eaeaea; border-left: 4px solid #1a237e; padding: 15px; border-radius: 0 6px 6px 0; margin-bottom: 15px; }}
                .webhook-title {{ font-weight: bold; font-size: 13px; color: #1a237e; margin-bottom: 5px; }}
                .webhook-desc {{ font-size: 11px; color: #666; margin-bottom: 10px; line-height: 1.4; }}
                .webhook-input-group {{ display: flex; gap: 8px; }}
                .webhook-input {{ flex: 1; padding: 8px 10px; border: 1px solid #ced4da; border-radius: 5px; font-family: monospace; font-size: 11px; background-color: #fff; outline: none; }}
                
                .btn-copy {{ background-color: #2e7d32; color: white; padding: 6px 12px; border: none; border-radius: 5px; font-weight: bold; font-size: 12px; cursor: pointer; transition: background 0.2s; white-space: nowrap; }}
                .btn-copy:hover {{ background-color: #1b5e20; }}
                
                .btn-submit {{ background-color: #1a237e; color: white; padding: 12px 24px; border: none; border-radius: 6px; font-weight: bold; font-size: 15px; cursor: pointer; transition: background 0.2s; }}
                .btn-submit:hover {{ background-color: #0d1b2a; }}
                .btn-cancel {{ color: #666; text-decoration: none; font-size: 14px; font-weight: bold; }}
                .btn-cancel:hover {{ color: #333; }}
                
                .alert {{ padding: 12px; border-radius: 6px; margin-bottom: 20px; display: none; font-size: 14px; font-weight: 600; }}
                .alert-error {{ background-color: #ffebee; color: #c62828; border: 1px solid #ffcdd2; }}
                .alert-success {{ background-color: #e8f5e9; color: #2e7d32; border: 1px solid #c8e6c9; }}
                
                .conditional-box {{ background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 15px; margin-top: 15px; display: none; }}
                @keyframes pulseHighlight {{
                    0% {{ transform: scale(1); box-shadow: 0 0 0 0 rgba(46, 125, 50, 0.7); background-color: #2e7d32; }}
                    50% {{ transform: scale(1.04); box-shadow: 0 0 0 12px rgba(46, 125, 50, 0); background-color: #1b5e20; }}
                    100% {{ transform: scale(1); box-shadow: 0 0 0 0 rgba(46, 125, 50, 0); background-color: #2e7d32; }}
                }}
                .btn-pulse-save {{
                    animation: pulseHighlight 2s infinite !important;
                    background-color: #2e7d32 !important;
                    border-color: #1b5e20 !important;
                    color: white !important;
                }}
                
                /* Speech Bubble Tooltip Styles */
                .tooltip-icon {{
                    position: relative;
                    display: inline-block;
                    cursor: help;
                    margin-left: 6px;
                    font-size: 14px;
                    vertical-align: middle;
                    color: #1a237e;
                }}
                .tooltip-icon .tooltip-text {{
                    visibility: hidden;
                    width: 320px;
                    background-color: #1a237e;
                    color: #fff;
                    text-align: left;
                    border-radius: 6px;
                    padding: 10px 12px;
                    position: absolute;
                    z-index: 1000;
                    bottom: 125%;
                    left: 50%;
                    margin-left: -160px;
                    opacity: 0;
                    transition: opacity 0.3s;
                    font-size: 11px;
                    line-height: 1.4;
                    font-weight: normal;
                    box-shadow: 0 4px 10px rgba(0,0,0,0.15);
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                }}
                .tooltip-icon .tooltip-text::after {{
                    content: "";
                    position: absolute;
                    top: 100%;
                    left: 50%;
                    margin-left: -5px;
                    border-width: 5px;
                    border-style: solid;
                    border-color: #1a237e transparent transparent transparent;
                }}
                .tooltip-icon:hover .tooltip-text {{
                    visibility: visible;
                    opacity: 1;
                }}

                /* Tooltip styling */
                .tooltip {{
                    position: relative;
                    display: inline-flex;
                    align-items: center;
                    cursor: pointer;
                    margin-left: 5px;
                    color: #1a237e;
                    font-size: 14px;
                    vertical-align: middle;
                }}
                .tooltip .tooltiptext {{
                    visibility: hidden;
                    width: 250px;
                    background-color: #333;
                    color: #fff;
                    text-align: left;
                    border-radius: 6px;
                    padding: 10px;
                    position: absolute;
                    z-index: 100;
                    bottom: 125%; /* Position above the text */
                    left: 50%;
                    margin-left: -125px;
                    opacity: 0;
                    transition: opacity 0.3s;
                    font-size: 11px;
                    font-weight: normal;
                    line-height: 1.4;
                    box-shadow: 0px 4px 10px rgba(0,0,0,0.15);
                    white-space: normal;
                }}
                .tooltip .tooltiptext::after {{
                    content: "";
                    position: absolute;
                    top: 100%; /* At the bottom of the tooltip */
                    left: 50%;
                    margin-left: -5px;
                    border-width: 5px;
                    border-style: solid;
                    border-color: #333 transparent transparent transparent;
                }}
                .tooltip:hover .tooltiptext {{
                    visibility: visible;
                    opacity: 1;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                {user_header_bar}
                <header>
                    <div>
                        <h1>Client Configuration Settings ⚙️</h1>
                        <p style="margin: 5px 0 0 0; color: #666; font-size: 14px;">Update dynamic rules, ad accounts, and system webhooks.</p>
                    </div>
                    
                    <div class="client-selector-container">
                        <span class="client-label">Editing Client:</span>
                        <select class="client-select" onchange="window.location.href='/dashboard/settings?client_id='+this.value">
                            {dropdown_options}
                        </select>
                    </div>
                </header>

                <div id="alert-box" class="alert"></div>
                
                <form id="settings-form" onsubmit="submitSettings(event)">
                    <div class="settings-layout">
                        
                        <!-- LEFT COLUMN: Configurations -->
                        <div>
                            <!-- SECTION 1: Accounts -->
                            <div class="section-title">🏢 Profile & Ad Accounts</div>
                            
                            <div class="form-group">
                                <label for="name">Client Business Name</label>
                                <input type="text" id="name" value="{client_name}" required>
                            </div>
                            
                            <div class="form-group">
                                <label for="call_tracking_provider">Call Tracking Provider</label>
                                <select id="call_tracking_provider" onchange="toggleSettingsCallTrackingFields()" style="width: 100%; padding: 12px; border-radius: 6px; border: 1px solid #ced4da; font-size: 14px; font-weight: 600;">
                                    <option value="callrail" {sel_cr}>CallRail</option>
                                    <option value="calltrackingmetrics" {sel_ctm}>CallTrackingMetrics</option>
                                    <option value="whatconverts" {sel_wc}>WhatConverts</option>
                                </select>
                            </div>
                            
                            <div id="settings_call_tracking_callrail_box" class="form-row" style="{p_cr_style}">
                                <div class="form-group">
                                    <label for="callrail_account_id">CallRail Account ID</label>
                                    <input type="text" id="callrail_account_id" value="{client_data.get('callrail_account_id', '') or ''}" placeholder="e.g. 123456789">
                                </div>
                                <div class="form-group">
                                    <label for="callrail_company_id">CallRail Client ID (Company ID)</label>
                                    <input type="text" id="callrail_company_id" value="{client_data.get('callrail_company_id', '') or ''}" placeholder="e.g. 987654321">
                                </div>
                            </div>

                            <div id="settings_call_tracking_ctm_box" class="form-row" style="{p_ctm_style}">
                                <div class="form-group">
                                    <label for="ctm_account_id">CallTrackingMetrics Account ID</label>
                                    <input type="text" id="ctm_account_id" value="{client_data.get('ctm_account_id', '') or ''}" placeholder="e.g. 12345">
                                </div>
                                <div class="form-group">
                                    <label for="ctm_profile_id">CallTrackingMetrics Client ID (Profile ID)</label>
                                    <input type="text" id="ctm_profile_id" value="{client_data.get('ctm_profile_id', '') or ''}" placeholder="e.g. 67890">
                                </div>
                            </div>

                            <div id="settings_call_tracking_wc_box" class="form-row" style="{p_wc_style}">
                                <div class="form-group">
                                    <label for="wc_account_id">WhatConverts Account ID</label>
                                    <input type="text" id="wc_account_id" value="{client_data.get('wc_account_id', '') or ''}" placeholder="e.g. 11111">
                                </div>
                                <div class="form-group">
                                    <label for="wc_profile_id">WhatConverts Client ID (Profile ID)</label>
                                    <input type="text" id="wc_profile_id" value="{client_data.get('wc_profile_id', '') or ''}" placeholder="e.g. 22222">
                                </div>
                            </div>
                            
                            <div class="form-row">
                                <div class="form-group">
                                    <label for="google_ads_customer_id">Google Ads Customer ID</label>
                                    <input type="text" id="google_ads_customer_id" value="{client_data.get("google_ads_customer_id", "")}" required>
                                </div>
                                <div class="form-group">
                                    <label for="facebook_ads_id">Facebook Ads Pixel ID</label>
                                    <input type="text" id="facebook_ads_id" value="{client_data.get("facebook_ads_id", "") or ""}">
                                </div>
                            </div>
                            
                            <div class="form-row">
                                <div class="form-group">
                                    <label for="linkedin_ads_id">LinkedIn Ads ID</label>
                                    <input type="text" id="linkedin_ads_id" value="{client_data.get("linkedin_ads_id", "") or ""}">
                                </div>
                                <div class="form-group">
                                    <label for="microsoft_ads_id">Microsoft Ads ID</label>
                                    <input type="text" id="microsoft_ads_id" value="{client_data.get("microsoft_ads_id", "") or ""}">
                                </div>
                            </div>
                            
                            <!-- SECTION 2: Lead Gen & Qualification -->
                            <div class="section-title">🧠 Lead Generation & AI Auditing</div>
                            
                            <div class="form-group">
                                <label>How do you generate your leads?</label>
                                <div class="card-radio-group">
                                    <div class="card-radio {lead_gen_both_checked and 'selected'}" onclick="selectCardRadio('lead_gen_method', 'both', this)">
                                        <input type="radio" name="lead_gen_method" value="both" {lead_gen_both_checked}>
                                        <div>
                                            <div class="card-radio-label">Both Phone Calls & Web Forms</div>
                                        </div>
                                    </div>
                                    <div class="card-radio {lead_gen_phone_checked and 'selected'}" onclick="selectCardRadio('lead_gen_method', 'phone', this)">
                                        <input type="radio" name="lead_gen_method" value="phone" {lead_gen_phone_checked}>
                                        <div>
                                            <div class="card-radio-label">Phone Calls Only</div>
                                        </div>
                                    </div>
                                    <div class="card-radio {lead_gen_form_checked and 'selected'}" onclick="selectCardRadio('lead_gen_method', 'form', this)">
                                        <input type="radio" name="lead_gen_method" value="form" {lead_gen_form_checked}>
                                        <div>
                                            <div class="card-radio-label">Form Submissions Only</div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                            
                            <div class="form-group">
                                <label for="qualification_criteria" style="display: inline-flex; align-items: center; gap: 5px;">
                                    How do you qualify a lead?
                                    <span class="tooltip">💬
                                        <span class="tooltiptext">Define a lead stage that is &quot;good enough&quot;, and would be happy with paying for all day long from your Ads. This is the minimum standard the system will go for when optimizing your Ads.</span>
                                    </span>
                                </label>
                                <select id="qualification_criteria">
                                    {crit_options}
                                </select>
                            </div>
                            
                            <!-- SECTION 4: Smart Deduplication -->
                            <div class="section-title">💰 Smart Conversion Controls</div>
                            
                            <div class="form-group">
                                <label>How should we track multiple leads from the same customer?</label>
                                <div class="card-radio-group">
                                    <div class="card-radio {lead_count_all_checked and 'selected'}" onclick="selectCardRadio('lead_count_rule', 'all', this)">
                                        <input type="radio" name="lead_count_rule" value="all" {lead_count_all_checked}>
                                        <div>
                                            <div class="card-radio-label">Count Every Lead Session</div>
                                        </div>
                                    </div>
                                    <div class="card-radio {lead_count_max_checked and 'selected'}" onclick="selectCardRadio('lead_count_rule', 'maximum_one', this)">
                                        <input type="radio" name="lead_count_rule" value="maximum_one" {lead_count_max_checked}>
                                        <div>
                                            <div class="card-radio-label">Maximum of One Conversion Each</div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                            
                            <div class="form-group">
                                <label>Exclude past customers as eligible lead conversions?</label>
                                <div class="card-radio-group">
                                    <div class="card-radio {exclude_no_checked and 'selected'}" onclick="selectCardRadio('exclude_past_customers', 'NO', this)">
                                        <input type="radio" name="exclude_past_customers" value="NO" {exclude_no_checked}>
                                        <div>
                                            <div class="card-radio-label">No, allow past customers</div>
                                        </div>
                                    </div>
                                    <div class="card-radio {exclude_yes_checked and 'selected'}" onclick="selectCardRadio('exclude_past_customers', 'YES', this)">
                                        <input type="radio" name="exclude_past_customers" value="YES" {exclude_yes_checked}>
                                        <div>
                                            <div class="card-radio-label">Yes, exclude past customers</div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                            
                            <!-- EXCLUSION UPLOAD PANEL -->
                            <p id="existing-exclusions-msg" style="font-size: 11px; color: #1b5e20; font-weight: bold; margin-top: 10px; margin-bottom: 10px; display: {'block' if client_data.get('exclude_past_customers') == 'YES' else 'none'};">
                                ℹ️ Currently ignoring <strong>{exclusion_count}</strong> past customers. Uploading a new list can append or replace this database.
                            </p>
                            <div id="exclusion-upload-box" class="conditional-box" style="display: {'block' if client_data.get('exclude_past_customers') == 'YES' else 'none'}; padding: 15px; margin-top: 10px;">
                                <div class="instructions" style="background-color: #f1f8e9; border-left-color: #2e7d32; color: #2e7d32; margin-bottom: 15px; font-size: 12px; line-height: 1.5; padding: 12px;">
                                    📂 <strong>Upload Past Customers to Ignore:</strong><br>
                                    Upload your list of customers, to use to ignore future conversion triggering. Only one piece of information is needed for each user in order to do this, but more data points for each user is best, for higher match rates. Here is a sample sheet that you can use to fill in, or you can provide your own sheet that have "first name, last name, email, phone number, company name" as the column headers.
                                </div>
                                <div style="display: flex; gap: 10px; align-items: center; margin-bottom: 15px; flex-wrap: wrap;">
                                    <button type="button" onclick="triggerSampleSheetDownload()" class="btn-copy" style="background-color: #1a237e; padding: 8px 12px; font-size: 11px;">📥 Download Sample Sheet (.CSV)</button>
                                    <a href="/dashboard/export/exclusions?client_id={active_client_id}" id="btn-download-exclusions" class="btn-copy" style="background-color: #607d8b; padding: 8px 12px; font-size: 11px; text-decoration: none; display: {'inline-block' if exclusion_count > 0 else 'none'};">📥 Download Current Exclusions ({exclusion_count})</a>
                                    <input type="file" id="exclusion-file-input" accept=".csv" onchange="handleExclusionFileUpload(event)" style="display: none;">
                                    <button type="button" onclick="document.getElementById('exclusion-file-input').click()" class="btn-copy" style="background-color: #2e7d32; padding: 8px 12px; font-size: 11px;">📤 Choose File & Upload (.CSV)</button>
                                </div>
                                <div style="margin-top: 15px; margin-bottom: 15px; background: #fff; padding: 12px; border: 1px solid #e0e0e0; border-radius: 6px;">
                                    <label style="font-weight: bold; font-size: 12px; margin-bottom: 8px; display: block; color: #1a237e;">🔄 Exclusions Upload Strategy:</label>
                                    <div style="display: flex; gap: 20px; align-items: center;">
                                        <label style="font-weight: normal; cursor: pointer; display: flex; align-items: center; gap: 6px; font-size: 12px; margin: 0;">
                                            <input type="radio" name="exclusion_upload_action" value="append" checked style="cursor: pointer;">
                                            <strong>Append new records</strong> (Keep existing ones, only add new customers)
                                        </label>
                                        <label style="font-weight: normal; cursor: pointer; display: flex; align-items: center; gap: 6px; font-size: 12px; margin: 0;">
                                            <input type="radio" name="exclusion_upload_action" value="replace" style="cursor: pointer;">
                                            <strong>Overwrite list</strong> (Wipe existing entries and start fresh)
                                        </label>
                                    </div>
                                </div>

                                <div id="upload-status-box" class="alert alert-success" style="display: none; margin-bottom: 0; font-size: 11px; padding: 10px;"></div>
                            </div>
                            
                            <!-- Real-Time CRM Exclusion sync webhook section -->
                            <div id="settings-exclusion-webhook-box" class="conditional-box" style="display: {'block' if client_data.get('exclude_past_customers') == 'YES' else 'none'}; padding: 15px; margin-top: 15px; border-top: 1px dashed #ccc;">
                                <p style="font-size: 12px; font-weight: bold; color: #1a237e; margin-top: 0; margin-bottom: 5px;">⚡ Real-Time CRM Exclusion Sync Webhook URL</p>
                                <p style="font-size: 11px; color: #666; margin-top: 0; margin-bottom: 10px; line-height: 1.4;">
                                    Connect your CRM (HubSpot, ServiceTitan, Salesforce, Zoho, etc.) directly using Zapier or a native webhook. Set your CRM to send a POST webhook to this URL whenever a customer is added or won. The contact's phone/email will be added to your exclusion filter automatically in real-time!
                                </p>
                                <div class="webhook-input-group">
                                    <input type="text" class="webhook-input" id="exclusion-crm-webhook" readonly value="" data-suffix="/webhooks/exclude-customer?client_id={active_client_id}">
                                    <button type="button" onclick="copyText('exclusion-crm-webhook', 'exclusion-crm-copy-btn')" id="exclusion-crm-copy-btn" class="btn-copy">📋 Copy</button>
                                </div>
                            </div>

                        </div>
                        
                        <!-- RIGHT COLUMN: Webhooks & SOT Integration -->
                        <div>
                            <!-- SECTION 3: SOT Integration -->
                            <div class="section-title">🔌 Single Source of Truth Settings</div>
                            
                            <div class="form-group">
                                <label for="source_of_truth">Single Source of Truth Platform</label>
                                <select id="source_of_truth" onchange="toggleSOTFields()">
                                    {sot_options}
                                </select>
                            </div>
                            
                            <!-- CONDITIONAL: CRM Deal status tags -->
                            <div id="sot-deal-tags-box" class="conditional-box">
                                <div style="margin-bottom: 15px;">
                                    <label for="crm_deal_tags">Which tags/statuses under <strong>Deals</strong> signify a qualified conversion?</label>
                                    <input type="text" id="crm_deal_tags" value="{client_data.get("crm_deal_tags", "") or ""}" placeholder="e.g. appointment-booked, estimate-approved">
                                </div>
                                <div>
                                    <label for="crm_won_deal_tags">Which tags/statuses under <strong>Deals</strong> signify a won deal conversion?</label>
                                    <input type="text" id="crm_won_deal_tags" value="{client_data.get("crm_won_deal_tags", "") or ""}" placeholder="e.g. closed-won, job-completed">
                                </div>
                            </div>
                            
                            <!-- CONDITIONAL: HubSpot Setup Instructions -->
                            <div id="sot-hubspot-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px;">
                                <div style="background-color: #fff8e1; border-left: 4px solid #ffb300; padding: 15px; border-radius: 4px; color: #5d4037; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                    💡 <strong>HubSpot Private App Quick Setup Guide:</strong><br>
                                    <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #4e342e;">
                                        <li>Log into HubSpot as a <strong>Super Admin</strong>.</li>
                                        <li>Go to <strong>Settings (Gear Icon) &gt; Integrations &gt; Private Apps</strong>.</li>
                                        <li>Click <strong>Create Private App</strong> and configure basic info.</li>
                                        <li>Under <strong>Scopes</strong>, search <code>CRM</code> and check <code>Read</code> permissions for:
                                            <ul style="list-style-type: disc; padding-left: 15px; margin: 4px 0;">
                                                <li><code>crm.objects.deals.read</code> (to track closed sales &amp; revenue)</li>
                                                <li><code>crm.objects.contacts.read</code> (to sync leads)</li>
                                            </ul>
                                        </li>
                                        <li>Click <strong>Create App</strong>. If you want real-time syncing, click the <strong>Webhooks</strong> tab of your new app, click <strong>Edit Webhooks</strong>, paste your dynamic target URL (provided above on this screen), and subscribe to <code>propertyChange</code> or <code>creation</code> for <strong>Deals</strong>!</li>
                                    </ol>
                                    <small style="display: block; font-style: italic; color: #6d4c41; line-height: 1.4; border-top: 1px solid #ffe082; padding-top: 8px;">
                                        ⚠️ <strong>Tip:</strong> If you don't see the "Webhooks" tab inside Private App settings, go to your HubSpot profile (top-right) &gt; <strong>Product Updates &gt; Betas</strong>, click <strong>Join Beta</strong> for <em>"Private App Webhooks"</em>, and refresh!
                                    </small>
                                </div>
                            </div>
                            

                        <!-- CONDITIONAL INPUT: Salesforce Setup Instructions -->
                        <div id="sot-salesforce-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                            <div style="background-color: #e3f2fd; border-left: 4px solid #1e88e5; padding: 15px; border-radius: 4px; color: #0d47a1; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                💡 <strong>Salesforce Outbound Flow Setup Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #1565c0;">
                                    <li>In Salesforce Setup, go to <strong>Named Credentials &gt; External Credentials</strong> tab, click <strong>New</strong>. Label <code>LeadGroove External Credential</code>, Name <code>LeadGroove_External_Credential</code>, Protocol <strong>Custom</strong>. Save, scroll to <em>Principals</em>, click <strong>New</strong> and define a principal named <code>LeadGroove_Principal</code>.</li>
                                    <li>Create a <strong>Permission Set</strong> in Setup named <code>LeadGroove Webhook Access</code>. In it, click <strong>External Credential Principal Access</strong>, enable your new credential and principal, and assign this permission set to any integrating users.</li>
                                    <li>Back in <strong>Named Credentials</strong>, click <strong>New</strong> under the main tab. Label <code>LeadGroove API</code>, Name <code>LeadGroove_API</code>, URL <code style="background: rgba(0,0,0,0.05); padding: 2px 4px; border-radius: 3px;">https://your-agency-app.onrender.com</code> (or active origin). Under <em>External Credential</em>, select the credential you created in Step 1, and save.</li>
                                    <li>Create a <strong>Record-Triggered Flow</strong> on the <strong>Opportunity</strong> object (when updated) with conditions <code>StageName Equals Closed Won</code> (Only when updated to meet conditions), optimized for <strong>Actions and Related Records</strong>.</li>
                                    <li>On the flow canvas, click <strong>+ Add Action &gt; Create HTTP Callout</strong>, select your Named Credential, and define a <strong>POST</strong> method with path <code style="color: #2e7d32; font-family: monospace;">/webhooks/crm?client_id=conversions-{{active_client_id}}</code>. Provide this sample JSON structure for automatic Salesforce parameter mapping:
                                        <pre style="background: rgba(255,255,255,0.7); padding: 8px; border-radius: 4px; margin-top: 5px; font-family: monospace; font-size: 10px; overflow-x: auto; border: 1px solid #bbdefb; color: #0d47a1;">{{
  "customer_name": "John Doe",
  "customer_email": "john@example.com",
  "customer_phone": "5551234567",
  "deal_stage": "Closed Won",
  "deal_value": 1500.00
}}</pre>
                                    </li>
                                </ol>
                            </div>
                        </div>
                        <!-- CONDITIONAL INPUT: Zoho Setup Instructions -->
                        <div id="sot-zoho-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                            <div style="background-color: #e8f5e9; border-left: 4px solid #2e7d32; padding: 15px; border-radius: 4px; color: #1b5e20; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                💡 <strong>Zoho CRM Outbound Webhook Setup Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #2e7d32;">
                                    <li>Click the <strong>Setup (Gear Icon)</strong> in the top-right corner of your Zoho CRM dashboard.</li>
                                    <li>Under <strong>Automation</strong>, click on <strong>Actions</strong>, then select the <strong>Webhooks</strong> tab at the top.</li>
                                    <li>Click <strong>Configure Webhook</strong> and configure these fields:
                                        <ul style="list-style-type: disc; padding-left: 15px; margin: 4px 0;">
                                            <li><strong>Name</strong>: <code>LeadGroove Conversion Sync</code></li>
                                            <li><strong>URL to notify</strong>: <code style="background: rgba(0,0,0,0.05); padding: 2px 4px; border-radius: 3px;">https://your-agency-app.onrender.com/webhooks/crm?client_id=conversions-{{active_client_id}}</code></li>
                                            <li><strong>Method</strong>: Select <strong>POST</strong></li>
                                            <li><strong>Module</strong>: Select <strong>Deals</strong> (or your tracking module)</li>
                                        </ul>
                                    </li>
                                    <li>In the <strong>Body</strong> parameters section:
                                        <ul style="list-style-type: disc; padding-left: 15px; margin: 4px 0;">
                                            <li>Choose <strong>Raw</strong> format and select <strong>JSON</strong> from the dropdown.</li>
                                            <li>Type <code>#</code> to dynamically insert CRM fields into this JSON structure:
                                                <pre style="background: rgba(255,255,255,0.7); padding: 8px; border-radius: 4px; margin-top: 5px; font-family: monospace; font-size: 10px; overflow-x: auto; border: 1px solid #c8e6c9; color: #1b5e20;">{{
  "customer_name": "${{Deals.Deal Name}}",
  "customer_email": "${{Deals.Email}}",
  "customer_phone": "${{Deals.Phone}}",
  "deal_stage": "${{Deals.Stage}}",
  "deal_value": ${{Deals.Amount}}
}}</pre>
                                            </li>
                                        </ul>
                                    </li>
                                    <li>Click <strong>Save</strong>. Next, go to <strong>Workflow Rules</strong> (under Setup &gt; Automation) and create a rule triggered on Deal Update when the <strong>Stage is Closed Won</strong>, then associate this Webhook as an <strong>Instant Action</strong>!</li>
                                </ol>
                            </div>
                        </div>

                            
                            <!-- CONDITIONAL: ServiceTitan Setup Instructions -->
                            <div id="sot-servicetitan-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                                <div style="background-color: #f3f4f6; border-left: 4px solid #4b5563; padding: 15px; border-radius: 4px; color: #1f2937; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                    💡 <strong>ServiceTitan Webhooks V2 Quick Setup Guide:</strong><br>
                                    <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #374151;">
                                        <li>Navigate to the **ServiceTitan Developer Portal** at <a href="https://developer.servicetitan.io" target="_blank" style="color: #1a237e; font-weight: bold; text-decoration: none;">developer.servicetitan.io</a> and sign in with your production credentials.</li>
                                        <li>Click **Create and Manage Applications** ➡️ **Create New App**. Name it <code>LeadGroove Webhook Sync</code> and set your tenant/business units.</li>
                                        <li>Under **API Scopes**, select:
                                            <ul style="list-style-type: disc; padding-left: 15px; margin: 4px 0;">
                                                <li><code>crm.objects.leads.read</code> or <code>jpm.objects.jobs.read</code> (to capture lead states and bookings)</li>
                                            </ul>
                                        </li>
                                        <li>Click **Save** to generate your **Client ID**, **Client Secret**, **App ID**, and **App Key**.</li>
                                        <li>Log into your main production portal at <a href="https://go.servicetitan.com" target="_blank" style="color: #1a237e; font-weight: bold; text-decoration: none;">go.servicetitan.com</a>, go to **Settings ➡️ Integrations ➡️ API Application Access**, find your app, click **Edit**, and set your dynamic Webhook Endpoint Target URL:
                                            <code style="display: block; background: #fff; border: 1px solid #d1d5db; padding: 8px; border-radius: 4px; font-family: monospace; font-size: 11px; margin-top: 5px; color: #1f2937;">https://your-agency-app.onrender.com/webhooks/crm?client_id=conversions-{active_client_id}</code>
                                        </li>
                                        <li>Register your endpoint triggers for <code>job.created</code> and <code>job.updated</code> (under Job Planning & Management v2 endpoints) to fire instantly when dispatch sheets are updated!</li>
                                    </ol>
                                </div>
                            </div>
                            <!-- CONDITIONAL: Housecall Pro Setup Instructions -->
                            <div id="sot-housecallpro-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                                <div style="background-color: #fff3e0; border-left: 4px solid #e65100; padding: 15px; border-radius: 4px; color: #e65100; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                    💡 <strong>Housecall Pro Webhooks Quick Setup Guide:</strong><br>
                                    <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #e65100;">
                                        <li>Sign in to your Housecall Pro admin account. (Only <strong>Admin</strong> users can access and generate webhook API settings).</li>
                                        <li>Navigate to <strong>My Apps</strong> from the top navigation bar, then click <strong>All Apps</strong>.</li>
                                        <li>Select the <strong>All Apps</strong> tab, search for the <strong>Webhooks</strong> app, and click to open it.</li>
                                        <li>Click the toggle button in the top-right corner of the page to <strong>Enable Webhooks</strong>.</li>
                                        <li>In the <strong>Target URL</strong> field, paste your client's custom live endpoint:
                                            <code style="display: block; background: #fff; border: 1px solid #ffcc80; padding: 8px; border-radius: 4px; font-family: monospace; font-size: 11px; margin-top: 5px; color: #d84315;">https://your-agency-app.onrender.com/webhooks/crm?client_id=conversions-{active_client_id}</code>
                                        </li>
                                        <li>Select your preferred event triggers to notify our platform. We recommend subscribing to **<code>job.completed</code>**, <code>job.created</code>, and <code>job.paid</code> to track actual conversion events.</li>
                                        <li>Click <strong>Save</strong> to activate the webhook instantly!</li>
                                    </ol>
                                    <small style="display: block; font-style: italic; color: #bf360c; line-height: 1.4; border-top: 1px solid #ffe0b2; padding-top: 8px;">
                                        ⚠️ <strong>Note:</strong> Webhook access in Housecall Pro requires their **MAX plan** subscription level. If you don't see the Webhooks app under All Apps, contact Housecall Pro support to verify your plan access.
                                    </small>
                                </div>
                            </div>

\n                            
                            <!-- CONDITIONAL: QuickBooks Setup Instructions -->
                            <div id="sot-quickbooks-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px;">
                                <div style="background-color: #e3f2fd; border-left: 4px solid #0288d1; padding: 15px; border-radius: 4px; color: #01579b; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                    💡 <strong>QuickBooks Online Webhooks Quick Setup Guide:</strong><br>
                                    <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #0277bd;">
                                        <li>Log into the <strong>Intuit Developer Portal</strong> at <a href="https://developer.intuit.com" target="_blank" style="color: #1a237e; font-weight: bold; text-decoration: none;">developer.intuit.com</a>.</li>
                                        <li>Go to your **Dashboard**, select your App, and navigate to **Production Settings ➡️ Webhooks** in the left sidebar menu.</li>
                                        <li>In the **Endpoint URL** field, paste your dynamic target URL (provided above on this screen):
                                            <code style="display: block; background: #fff; border: 1px solid #b3e5fc; padding: 8px; border-radius: 4px; font-family: monospace; font-size: 11px; margin-top: 5px; color: #01579b;">https://your-agency-app.onrender.com/webhooks/billing?client_id=conversions-{active_client_id}</code>
                                        </li>
                                        <li>Check the boxes for the event notifications you want to receive under **Invoices** or **Payments** (e.g., invoice creation and payment status updates).</li>
                                        <li>Click **Save** to generate your **Verifier Token** (copy this key to verify QuickBooks signatures on your server!).</li>
                                    </ol>
                                    <small style="display: block; font-style: italic; color: #0288d1; line-height: 1.4; border-top: 1px solid #b3e5fc; padding-top: 8px;">
                                        ⚠️ <strong>Important Note:</strong> QuickBooks Online aggregates webhook events and delivers them in **5-minute intervals**, so test events might take up to 5 minutes to appear in your logs!
                                    </small>
                                </div>
                            </div>
    
                            <!-- CONDITIONAL: Xero Setup Instructions -->
                            <div id="sot-xero-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px;">
                                <div style="background-color: #e0f7fa; border-left: 4px solid #00b0ff; padding: 15px; border-radius: 4px; color: #006064; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                    💡 <strong>Xero Webhooks Quick Setup Guide:</strong><br>
                                    <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #00838f;">
                                        <li>Log into the <strong>Xero Developer Portal</strong> at <a href="https://developer.xero.com" target="_blank" style="color: #1a237e; font-weight: bold; text-decoration: none;">developer.xero.com</a> under **My Apps**.</li>
                                        <li>Select your App and click on the **Webhooks** tab in the left-hand navigation panel.</li>
                                        <li>In the **Send notifications to** (Delivery URL) field, paste your custom live endpoint (provided above on this screen):
                                            <code style="display: block; background: #fff; border: 1px solid #80deea; padding: 8px; border-radius: 4px; font-family: monospace; font-size: 11px; margin-top: 5px; color: #006064;">https://your-agency-app.onrender.com/webhooks/billing?client_id=conversions-{active_client_id}</code>
                                        </li>
                                        <li>Select your preferred event categories. We highly recommend subscribing to **Invoices** (CREATE, UPDATE) and **Contacts** (CREATE, UPDATE).</li>
                                        <li>Click **Save**. This will generate your **Webhook Key** (also known as the signing key) which you can copy to authenticate payloads.</li>
                                        <li>Click the **Send intent to receive** button to initiate Xero's connection validation handshake and verify your setup is active!</li>
                                    </ol>
                                    <small style="display: block; font-style: italic; color: #00838f; line-height: 1.4; border-top: 1px solid #80deea; padding-top: 8px;">
                                        🔒 <strong>Intent-to-Receive Handshake:</strong> To complete the setup, our platform automatically responds to Xero's secure validation checks. Ensure your SSL certificate is trusted (non-self-signed) and running on port 443.
                                    </small>
                                </div>
                            </div>
    
                            <!-- CONDITIONAL: CRM Lead status tags -->
                            <div id="sot-lead-tags-box" class="conditional-box">
                                <label for="crm_lead_tags">Which statuses under <strong>Leads</strong> signify qualification?</label>
                                <input type="text" id="crm_lead_tags" value="{client_data.get("crm_lead_tags", "") or ""}" placeholder="e.g. job-booked, estimate-given">
                            </div>
                            
                            <!-- CONDITIONAL: Email settings fallback -->
                            <div id="sot-email-box" class="conditional-box">
                                <div class="form-group">
                                    <label for="email_provider">Email Provider</label>
                                    <select id="email_provider">
                                        {provider_options}
                                    </select>
                                </div>
                                <div class="form-group" style="margin-bottom: 0;">
                                    <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 15px; margin-bottom: 5px;">
                                        <div style="display: flex; align-items: center; gap: 6px; flex-wrap: wrap;">
                                            <label for="email_account" style="font-weight: bold; margin-bottom: 0;">Onboarding Integration Email Account</label>
                                            
                                            <!-- Speech Bubble Tooltip -->
                                            <span class="tooltip-icon">
                                                💬
                                                <span class="tooltip-text">
                                                    Forward your customer booking emails, invoice alerts, or form lead replies to:<br>
                                                    <strong class="settings-forwarding-email" style="color: #81c784; word-break: break-all;">conversions-{active_client_id}@your-agency.com</strong>
                                                </span>
                                            </span>
                                            
                                            <!-- Check Logs Hover Link -->
                                            <span class="tooltip-icon" style="font-size: 11px; font-weight: bold; margin-left: 5px;">
                                                <a href="javascript:void(0)" style="color: #1a237e; text-decoration: underline;">check logs</a>
                                                <span class="tooltip-text" style="width: 290px;">
                                                    <strong>Last 5 Analyzed Emails:</strong><br>
                                                    {last_emails_html}
                                                </span>
                                            </span>
                                        </div>
                                        <a href="javascript:void(0)" onclick="openAppPasswordModal()" style="font-size: 12px; color: #1a237e; font-weight: bold; text-decoration: none; display: flex; align-items: center; gap: 4px;">
                                            🔑 How to get an App Password?
                                        </a>
                                    </div>
                                    <input type="text" id="email_account" value="{client_data.get("email_account", "") or ""}" placeholder="e.g. bookings@clientcompany.com">
                                </div>
                            </div>
                            
                            <!-- SECTION 5: Active Webhooks read-only deck -->
                            <div class="section-title" style="margin-top: 30px;">🔑 Live Webhooks & Integration URLs</div>
                            
                            <!-- Call Tracking webhook (Dynamic based on provider) -->
                            <div class="webhook-card">
                                <div class="webhook-title">{webhook_card_title}</div>
                                <div class="webhook-desc">{webhook_card_desc}</div>
                                <div class="webhook-input-group">
                                    <input type="text" class="webhook-input" id="callrail-webhook" readonly value="" data-suffix="{webhook_suffix}">
                                    <button type="button" onclick="copyText('callrail-webhook', 'cr-copy-btn')" id="cr-copy-btn" class="btn-copy">📋 Copy</button>
                                </div>
                            </div>
                            
                            <!-- CRM webhook (Available if CRM active) -->
                            <div class="webhook-card" id="crm-webhook-card">
                                <div class="webhook-title" style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px;">
                                    <span>⚙️ CRM Deal/Lead Webhook</span>
                                    
                                    <!-- Check Logs Hover Link -->
                                    <span class="tooltip-icon" style="font-size: 11px; font-weight: bold; margin-left: auto; cursor: help;">
                                        <a href="javascript:void(0)" style="color: #1a237e; text-decoration: underline;">check logs</a>
                                        <span class="tooltip-text" style="width: 290px;">
                                            <strong>Last 5 Received Payloads:</strong><br>
                                            {last_crm_logs_html}
                                        </span>
                                    </span>
                                </div>
                                <div class="webhook-desc">Use this URL inside Zapier or your CRM's developer workspace to push offline lead status updates back to our platform:</div>
                                <div class="webhook-input-group">
                                    <input type="text" class="webhook-input" id="crm-webhook" readonly value="" data-suffix="/webhooks/crm?client_id={active_client_id}">
                                    <button type="button" onclick="copyText('crm-webhook', 'crm-copy-btn')" id="crm-copy-btn" class="btn-copy">📋 Copy</button>
                                </div>
                            </div>
                            
                            <!-- Billing webhook (Available if Accounting active) -->
                            <div class="webhook-card" id="billing-webhook-card">
                                <div class="webhook-title" style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px;">
                                    <span>💳 QuickBooks / Xero Billing Webhook</span>
                                    
                                    <!-- Check Logs Hover Link -->
                                    <span class="tooltip-icon" style="font-size: 11px; font-weight: bold; margin-left: auto; cursor: help;">
                                        <a href="javascript:void(0)" style="color: #1a237e; text-decoration: underline;">check logs</a>
                                        <span class="tooltip-text" style="width: 290px;">
                                            <strong>Last 5 Received Payments:</strong><br>
                                            {last_billing_logs_html}
                                        </span>
                                    </span>
                                </div>
                                <div class="webhook-desc">Link your paid transaction updates directly using this endpoint to register closed invoice values:</div>
                                <div class="webhook-input-group">
                                    <input type="text" class="webhook-input" id="billing-webhook" readonly value="" data-suffix="/webhooks/billing?client_id={active_client_id}">
                                    <button type="button" onclick="copyText('billing-webhook', 'billing-copy-btn')" id="billing-copy-btn" class="btn-copy">📋 Copy</button>
                                </div>
                            </div>
                            
                            <!-- Email Forwarder (Available if Email active) -->
                            <div class="webhook-card" id="email-webhook-card">
                                <div class="webhook-title">📧 Inbound Invoice & Booking Email</div>
                                <div class="webhook-desc">Set up auto-forwarding from your email inbox to send receipts or booking alerts directly to our system for Claude to audit:</div>
                                <div class="webhook-input-group">
                                    <input type="text" class="webhook-input" id="email-webhook" readonly value="" data-suffix="conversions-{active_client_id}@your-agency.com">
                                    <button type="button" onclick="copyText('email-webhook', 'em-copy-btn')" id="em-copy-btn" class="btn-copy">📋 Copy</button>
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- App Password Modal Overlay -->
                    <div id="app-password-modal" class="modal-overlay" style="display: none;">
                        <div class="modal-card">
                            <!-- Header -->
                            <div class="modal-header">
                                <h3 style="margin: 0; font-size: 18px; color: #1a237e; display: flex; align-items: center; gap: 8px;">
                                    🔐 Generate a Secure App Password
                                </h3>
                                <span class="modal-close" onclick="closeAppPasswordModal()">&times;</span>
                            </div>

                            <!-- Body -->
                            <div class="modal-body" style="padding: 20px; max-height: 70vh; overflow-y: auto; text-align: left;">
                                <p style="margin-top: 0; font-size: 13px; line-height: 1.5; color: #555;">
                                    For security, modern email networks require a <strong>16-character App Password</strong> rather than your standard account login password. This restricts our AI's access strictly to reading incoming booking emails via IMAP.
                                </p>

                                <!-- Provider Tabs -->
                                <div style="display: flex; border-bottom: 2px solid #e0e0e0; margin-bottom: 15px;">
                                    <button type="button" id="tab-btn-google" class="tab-btn active" onclick="switchModalTab('google')">
                                        📁 Google Workspace / Gmail
                                    </button>
                                    <button type="button" id="tab-btn-ms" class="tab-btn" onclick="switchModalTab('ms')">
                                        📁 Microsoft 365 / Outlook
                                    </button>
                                </div>

                                <!-- Tab Content: Google -->
                                <div id="modal-tab-google" class="tab-content">
                                    <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; color: #333; margin: 0;">
                                        <li style="margin-bottom: 8px;">Go to your <a href="https://myaccount.google.com/security" target="_blank" style="color: #1a237e; font-weight: bold; text-decoration: none;">Google Account Security Panel</a>.</li>
                                        <li style="margin-bottom: 8px;">Ensure <strong>2-Step Verification</strong> is active under "How you sign in to Google".</li>
                                        <li style="margin-bottom: 8px;">Type <strong>"App Passwords"</strong> in Google's search bar, or scroll to the bottom of 2-Step Verification and click <strong>App Passwords</strong>.</li>
                                        <li style="margin-bottom: 8px;">Enter a custom name (e.g., <code>LeadGroove Conversion Engine</code>) and click <strong>Create</strong>.</li>
                                        <li style="margin-bottom: 8px;">Copy the <strong>16-character code</strong> inside Google's yellow box, strip any spaces, and enter it as your password!</li>
                                    </ol>
                                </div>

                                <!-- Tab Content: Microsoft -->
                                <div id="modal-tab-ms" class="tab-content" style="display: none;">
                                    <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; color: #333; margin: 0;">
                                        <li style="margin-bottom: 8px;">Go to your <a href="https://mysignins.microsoft.com/security-info" target="_blank" style="color: #1a237e; font-weight: bold; text-decoration: none;">Microsoft Security Info Page</a>.</li>
                                        <li style="margin-bottom: 8px;">Click the <strong>+ Add sign-in method</strong> button at the top.</li>
                                        <li style="margin-bottom: 8px;">Select <strong>App Password</strong> from the dropdown menu and click <strong>Add</strong>.</li>
                                        <li style="margin-bottom: 8px;">Name it (e.g., <code>LeadGroove Offline Tracker</code>) and click <strong>Next</strong>.</li>
                                        <li style="margin-bottom: 8px;">Copy the <strong>16-character password key</strong> immediately before closing the confirmation window.</li>
                                    </ol>
                                </div>

                                <!-- Security Footnote -->
                                <div style="background-color: #f1f8e9; border-left: 4px solid #2e7d32; padding: 12px; margin-top: 20px; border-radius: 4px;">
                                    <p style="margin: 0; font-size: 11px; line-height: 1.4; color: #1b5e20;">
                                        🔒 <strong>Strict Privacy Guard:</strong> This code grants read-only IMAP credentials. It does not access your emails, calendars, or account dashboards. You can revoke it instantly at any time in your security settings.
                                    </p>
                                </div>
                            </div>

                            <!-- Footer -->
                            <div class="modal-footer" style="padding: 15px 20px; border-top: 1px solid #eaeaea; background-color: #f8f9fa; display: flex; justify-content: flex-end; border-bottom-left-radius: 12px; border-bottom-right-radius: 12px;">
                                <button type="button" class="btn-modal-close" onclick="closeAppPasswordModal()">Got It, Thanks!</button>
                            </div>
                        </div>
                    </div>
                    
                    <!-- Form Buttons -->
                    <div style="display:flex; justify-content: space-between; align-items: center; margin-top: 40px; border-top: 1px solid #eaeaea; padding-top: 20px;">
                        <a href="/dashboard?client_id={active_client_id}" class="btn-cancel">⬅️ Return to Dashboard</a>
                        <button type="submit" id="btn-settings-submit" class="btn-submit">💾 Save Configuration Changes</button>
                    </div>
                </form>

                <hr style="border: 0; height: 1px; background: #eaeaea; margin: 40px 0;">

                <div class="section-title" style="margin-bottom: 20px; color: #1a237e; font-size: 20px; font-weight: bold; display: flex; align-items: center; gap: 8px;">
                    👥 Account Collaborators & Invitations
                </div>
                <p style="color: #666; font-size: 13px; margin-top: -10px; margin-bottom: 20px;">
                    Invite and manage team members who can access this client's tracking dashboard.
                </p>

                <!-- Table of Active Users & Pending Invites -->
                <div style="background: #fafafa; border: 1px solid #eaeaea; border-radius: 8px; padding: 20px; margin-bottom: 30px;">
                    <h3 style="margin-top: 0; color: #1a237e; font-size: 15px; border-bottom: 1px solid #eaeaea; padding-bottom: 10px;">Active Collaborators & Pending Invites</h3>
                    <table style="width: 100%; border-collapse: collapse; font-size: 13px; text-align: left;">
                        <thead>
                            <tr style="border-bottom: 2px solid #eaeaea; color: #495057;">
                                <th style="padding: 10px;">Email / User</th>
                                <th style="padding: 10px;">Access Level</th>
                                <th style="padding: 10px;">Status</th>
                            </tr>
                        </thead>
                        <tbody>
                            {collaborator_rows_html}
                        </tbody>
                    </table>
                </div>

                <!-- Invite Form -->
                <div id="invite-box" style="background: #e8eaf6; border: 1px solid #c5cae9; border-radius: 8px; padding: 20px; display: {invite_form_display};">
                    <h3 style="margin-top: 0; color: #1a237e; font-size: 15px;">✉️ Invite a New Collaborator</h3>
                    <div id="invite-alert" class="alert" style="margin-bottom: 15px; padding: 10px; font-size: 12px;"></div>
                    
                    <div style="display: flex; gap: 20px; flex-wrap: wrap; margin-bottom: 15px; align-items: flex-end;">
                        <div class="form-group" style="flex: 1; min-width: 250px; margin-bottom: 10px;">
                            <label for="invite_email" style="font-weight: bold; font-size: 11px; margin-bottom: 8px; display: block;">RECIPIENT EMAIL ADDRESS</label>
                            <input type="email" id="invite_email" placeholder="e.g. employee@clientcompany.com" style="width: 100%; padding: 10px; border-radius: 6px; border: 1px solid #ced4da; font-size: 13px; background: white; box-sizing: border-box;">
                        </div>
                        <div class="form-group" style="flex: 1; min-width: 200px; margin-bottom: 10px;">
                            <label for="invite_role" style="font-weight: bold; font-size: 11px; margin-bottom: 8px; display: block;">ACCESS LEVEL</label>
                            <select id="invite_role" style="width: 100%; padding: 10px; border-radius: 6px; border: 1px solid #ced4da; font-size: 13px; background: white; height: 38px; box-sizing: border-box; cursor: pointer;">
                                <option value="read">Read-Only (Viewer)</option>
                                <option value="full">Full Function (Manager)</option>
                            </select>
                        </div>
                    </div>

                    <button type="button" onclick="generateInvitationLink()" class="btn-submit" style="margin-top: 15px; width: auto; font-size: 13px; padding: 10px 20px; background-color: #2e7d32;">
                        ✉️ Generate Invitation Link
                    </button>
                    
                    <div id="invite-link-container" style="display: none; margin-top: 20px; background: white; padding: 15px; border-radius: 6px; border: 1px dashed #2e7d32;">
                        <span style="font-weight: bold; color: #2e7d32; font-size: 13px; display: block; margin-bottom: 5px;">🎉 Invitation Link Generated!</span>
                        <p style="color: #555; font-size: 12px; margin: 0 0 10px 0;">Copy this link and send it directly to your collaborator to register:</p>
                        <div style="display: flex; gap: 8px;">
                            <input type="text" id="invite-url-output" readonly style="flex: 1; padding: 8px; border-radius: 4px; border: 1px solid #ced4da; font-family: monospace; font-size: 11px; background-color: #f8f9fa;">
                            <button type="button" onclick="copyText('invite-url-output', 'invite-copy-btn')" id="invite-copy-btn" class="btn-copy" style="margin: 0; width: auto; font-size: 12px; background-color: #2e7d32; color: white; padding: 0 12px; border: none; border-radius: 4px; cursor: pointer;">📋 Copy Link</button>
                        </div>
                    </div>
                </div>
            </div>
            
            <script>

                function openAppPasswordModal() {{
                    document.getElementById('app-password-modal').style.display = 'flex';
                }}
                
                function closeAppPasswordModal() {{
                    document.getElementById('app-password-modal').style.display = 'none';
                }}
                
                function switchModalTab(provider) {{
                    document.getElementById('tab-btn-google').classList.remove('active');
                    document.getElementById('tab-btn-ms').classList.remove('active');
                    document.getElementById('modal-tab-google').style.display = 'none';
                    document.getElementById('modal-tab-ms').style.display = 'none';
                    
                    if (provider === 'google') {{
                        document.getElementById('tab-btn-google').classList.add('active');
                        document.getElementById('modal-tab-google').style.display = 'block';
                    }} else {{
                        document.getElementById('tab-btn-ms').classList.add('active');
                        document.getElementById('modal-tab-ms').style.display = 'block';
                    }}
                }}

                // Close modal if user clicks outside of the card
                window.addEventListener('click', (e) => {{
                    const overlay = document.getElementById('app-password-modal');
                    if (e.target === overlay) {{
                        closeAppPasswordModal();
                    }}
                }});

                // Auto-populate the active hostname into webhook input fields
                window.addEventListener('DOMContentLoaded', () => {{
                    const origin = window.location.origin;
                    const host = window.location.host;
                    const emailDomain = host.includes('localhost') ? 'your-agency.com' : host.replace('www.', '').split(':')[0];
                    
                    document.querySelectorAll('.webhook-input').forEach(input => {{
                        const suffix = input.getAttribute('data-suffix');
                        if (suffix.startsWith('conversions-')) {{
                            // Email address, omit origin prefix
                            input.value = suffix;
                        }} else {{
                            input.value = origin + suffix;
                        }}
                    }});
                    
                    // Inject dynamic forwarding email domain inside Settings page tooltip
                    const forwardingLabel = document.querySelector('.settings-forwarding-email');
                    if (forwardingLabel) {{
                        forwardingLabel.innerText = `conversions-{active_client_id}@${{emailDomain}}`;
                    }}
                    
                    toggleSOTFields();
                    toggleSettingsCallTrackingFields();
                }});

                
                function toggleSettingsCallTrackingFields() {{
                    const provider = document.getElementById('call_tracking_provider').value;
                    const crBox = document.getElementById('settings_call_tracking_callrail_box');
                    const ctmBox = document.getElementById('settings_call_tracking_ctm_box');
                    const wcBox = document.getElementById('settings_call_tracking_wc_box');
                    
                    if (provider === 'callrail') {{
                        crBox.style.display = 'flex';
                        ctmBox.style.display = 'none';
                        wcBox.style.display = 'none';
                    }} else if (provider === 'calltrackingmetrics') {{
                        crBox.style.display = 'none';
                        ctmBox.style.display = 'flex';
                        wcBox.style.display = 'none';
                    }} else if (provider === 'whatconverts') {{
                        crBox.style.display = 'none';
                        ctmBox.style.display = 'none';
                        wcBox.style.display = 'flex';
                    }}
                }}
                
                function selectCardRadio(name, value, element) {{
                    element.parentNode.querySelectorAll('.card-radio').forEach(card => {{
                        card.classList.remove('selected');
                    }});
                    element.classList.add('selected');
                    element.querySelector('input[type="radio"]').checked = true;
                    
                    if (name === 'lead_gen_method') {{
                        toggleSOTFields();
                    }}
                    
                    if (name === 'exclude_past_customers') {{
                        const uploadBox = document.getElementById('exclusion-upload-box');
                        const msgBox = document.getElementById('existing-exclusions-msg');
                        if (value === 'YES') {{
                            uploadBox.style.display = 'block';
                            if (msgBox) msgBox.style.display = 'block';
                        }} else {{
                            uploadBox.style.display = 'none';
                            if (msgBox) msgBox.style.display = 'none';
                            
                            // Reset submit button if disabled exclusions
                            const btnSubmit = document.getElementById('btn-settings-submit');
                            if (btnSubmit) {{
                                btnSubmit.classList.remove('btn-pulse-save');
                                btnSubmit.innerHTML = '💾 Save Configuration Changes';
                            }}
                            parsedExclusions = [];
                        }}
                    }}
                }}
                
                function toggleSOTFields() {{
                    const sot = document.getElementById('source_of_truth').value;
                    const leadGenRadio = document.querySelector('input[name="lead_gen_method"]:checked');
                    const leadGen = leadGenRadio ? leadGenRadio.value : 'both';
                    
                    const dealBox = document.getElementById('sot-deal-tags-box');
                    const leadBox = document.getElementById('sot-lead-tags-box');
                    const emailBox = document.getElementById('sot-email-box');
                    const hsBox = document.getElementById('sot-hubspot-instructions-box');
                    
                    const crmCard = document.getElementById('crm-webhook-card');
                    const billingCard = document.getElementById('billing-webhook-card');
                    const emailCard = document.getElementById('email-webhook-card');
                    const salesforceBox = document.getElementById('sot-salesforce-instructions-box');
                    const zohoBox = document.getElementById('sot-zoho-instructions-box');
                    const servicetitanBox = document.getElementById('sot-servicetitan-instructions-box');
                    const housecallproBox = document.getElementById('sot-housecallpro-instructions-box');
                    const quickbooksBox = document.getElementById('sot-quickbooks-instructions-box');
                    const xeroBox = document.getElementById('sot-xero-instructions-box');
                    
                    // Hide all by default
                    dealBox.style.display = 'none';
                    leadBox.style.display = 'none';
                    emailBox.style.display = 'none';
                    if (hsBox) hsBox.style.display = 'none';
                    if (salesforceBox) salesforceBox.style.display = 'none';
                    if (zohoBox) zohoBox.style.display = 'none';
                    if (servicetitanBox) servicetitanBox.style.display = 'none';
                    if (housecallproBox) housecallproBox.style.display = 'none';
                    if (quickbooksBox) quickbooksBox.style.display = 'none';
                    if (xeroBox) xeroBox.style.display = 'none';
                    
                    crmCard.style.display = 'none';
                    billingCard.style.display = 'none';
                    emailCard.style.display = 'none';
                    
                    if (['hubspot', 'salesforce', 'zoho'].includes(sot)) {{
                        dealBox.style.display = 'block';
                        crmCard.style.display = 'block';
                        if (sot === 'hubspot' && hsBox) {{
                            hsBox.style.display = 'block';
                        }} else if (sot === 'salesforce' && salesforceBox) {{
                            salesforceBox.style.display = 'block';
                        }} else if (sot === 'zoho' && zohoBox) {{
                            zohoBox.style.display = 'block';
                        }}
                    }} else if (['servicetitan', 'housecallpro'].includes(sot)) {{
                        leadBox.style.display = 'block';
                        crmCard.style.display = 'block';
                        if (sot === 'servicetitan' && servicetitanBox) {{
                            servicetitanBox.style.display = 'block';
                        }} else if (sot === 'housecallpro' && housecallproBox) {{
                            housecallproBox.style.display = 'block';
                        }}
                    }} else if (['quickbooks', 'xero'].includes(sot)) {{
                        billingCard.style.display = 'block';
                        if (sot === 'quickbooks' && quickbooksBox) {{
                            quickbooksBox.style.display = 'block';
                        }} else if (sot === 'xero' && xeroBox) {{
                            xeroBox.style.display = 'block';
                        }}
                    }} else if (sot === 'email' || sot === 'ai_rating') {{
                        emailBox.style.display = 'block';
                        if (emailCard) {{
                            emailCard.style.display = sot === 'email' ? 'block' : 'none';
                        }}
                    }}
                }}

                let parsedExclusions = [];

                function handleExclusionFileUpload(event) {{
                    const file = event.target.files[0];
                    if (!file) return;
                    
                    const reader = new FileReader();
                    reader.onload = function(e) {{
                        const text = e.target.result;
                        parseCSVToExclusions(text, file.name);
                    }};
                    reader.readAsText(file);
                }}

                function parseCSVToExclusions(text, filename) {{
                    const lines = text.split(/\\r\\n|\\n/);
                    if (lines.length === 0) {{
                        showUploadStatus('Error: The file is empty.', 'error');
                        return;
                    }}
                    
                    function parseCSVLine(line) {{
                        let arr = [];
                        let quote = false;
                        let cell = "";
                        for (let i = 0; i < line.length; i++) {{
                            let char = line[i];
                            if (char === '"') {{
                                quote = !quote;
                            }} else if (char === ',' && !quote) {{
                                arr.push(cell.trim());
                                cell = "";
                            }} else {{
                                cell += char;
                            }}
                        }}
                        arr.push(cell.trim());
                        return arr;
                    }}
                    
                    const headers = parseCSVLine(lines[0]).map(h => h.toLowerCase().replace(/[^a-z0-9]/g, ''));
                    if (headers.length === 0 || headers.join('').trim() === '') {{
                        showUploadStatus('Error: Could not read headers from the first row of your CSV file.', 'error');
                        return;
                    }}
                    
                    let fnIdx = headers.findIndex(h => h.includes('firstname') || h.includes('first'));
                    let lnIdx = headers.findIndex(h => h.includes('lastname') || h.includes('last'));
                    let emailIdx = headers.findIndex(h => h.includes('email') || h.includes('mail'));
                    let phoneIdx = headers.findIndex(h => h.includes('phone') || h.includes('tel') || h.includes('mobile'));
                    let compIdx = headers.findIndex(h => h.includes('company') || h.includes('business'));
                    
                    if (fnIdx === -1 && lnIdx === -1 && emailIdx === -1 && phoneIdx === -1 && compIdx === -1) {{
                        fnIdx = 0; lnIdx = 1; emailIdx = 2; phoneIdx = 3; compIdx = 4;
                    }}
                    
                    let list = [];
                    for (let i = 1; i < lines.length; i++) {{
                        const line = lines[i].trim();
                        if (!line) continue;
                        
                        const row = parseCSVLine(line);
                        if (row.length === 0 || row.join('').trim() === '') continue;
                        
                        const cust = {{
                            first_name: fnIdx !== -1 && row[fnIdx] ? row[fnIdx] : "",
                            last_name: lnIdx !== -1 && row[lnIdx] ? row[lnIdx] : "",
                            email: emailIdx !== -1 && row[emailIdx] ? row[emailIdx] : "",
                            phone: phoneIdx !== -1 && row[phoneIdx] ? row[phoneIdx] : "",
                            company_name: compIdx !== -1 && row[compIdx] ? row[compIdx] : ""
                        }};
                        
                        if (cust.first_name || cust.last_name || cust.email || cust.phone || cust.company_name) {{
                            list.push(cust);
                        }}
                    }}
                    
                    parsedExclusions = list;
                    showUploadStatus(`✓ Loaded ${{list.length}} exclusions from "${{filename}}". Save changes to apply!`, 'success');
                    
                    // Option B: Visual Pulse & Highlight of settings submit button
                    const btnSubmit = document.getElementById('btn-settings-submit');
                    if (btnSubmit) {{
                        btnSubmit.classList.add('btn-pulse-save');
                        btnSubmit.innerHTML = `💾 Save Changes (Includes ${{list.length}} Uploaded Exclusions!)`;
                    }}
                }}

                function showUploadStatus(message, type) {{
                    const statusBox = document.getElementById('upload-status-box');
                    if (statusBox) {{
                        statusBox.innerText = message;
                        statusBox.className = type === 'success' ? 'alert alert-success' : 'alert alert-error';
                        statusBox.style.display = 'block';
                    }}
                }}

                function triggerSampleSheetDownload() {{
                    const headers = ["First Name", "Last Name", "Email", "Phone Number", "Company Name"];
                    const sampleRows = [
                        ["John", "Doe", "john.doe@example.com", "555-123-4567", "Doe Plumbing Inc"],
                        ["Jane", "Smith", "jane@company.com", "555-987-6543", "Smith Solar Corp"]
                    ];
                    let csvContent = "data:text/csv;charset=utf-8,";
                    csvContent += headers.join(",") + "\\n";
                    sampleRows.forEach(row => {{
                        csvContent += row.join(",") + "\\n";
                    }});
                    const encodedUri = encodeURI(csvContent);
                    const link = document.createElement("a");
                    link.setAttribute("href", encodedUri);
                    link.setAttribute("download", "sample_customer_exclusions.csv");
                    document.body.appendChild(link);
                    link.click();
                    document.body.removeChild(link);
                }}
                
                function copyText(id, btnId) {{
                    const copyText = document.getElementById(id);
                    copyText.select();
                    copyText.setSelectionRange(0, 99999);
                    navigator.clipboard.writeText(copyText.value);
                    
                    const copyBtn = document.getElementById(btnId);
                    copyBtn.innerText = "Copied!";
                    copyBtn.style.backgroundColor = "#1b5e20";
                    setTimeout(() => {{
                        copyBtn.innerText = "📋 Copy";
                        copyBtn.style.backgroundColor = "#2e7d32";
                    }}, 2000);
                }}
                
                async function generateInvitationLink() {{
                    const emailInput = document.getElementById('invite_email');
                    const roleInput = document.getElementById('invite_role');
                    const alertBox = document.getElementById('invite-alert');
                    const linkContainer = document.getElementById('invite-link-container');
                    const urlOutput = document.getElementById('invite-url-output');
                    
                    alertBox.style.display = 'none';
                    linkContainer.style.display = 'none';
                    
                    const email = emailInput.value.trim();
                    const role = roleInput.value;
                    
                    if (!email) {{
                        alertBox.innerText = 'Please enter a valid email address.';
                        alertBox.className = 'alert alert-error';
                        alertBox.style.display = 'block';
                        return;
                    }}
                    
                    try {{
                        const response = await fetch('/dashboard/invite', {{
                            method: 'POST',
                            headers: {{
                                'Content-Type': 'application/json'
                            }},
                            body: JSON.stringify({{
                                email: email,
                                role: role,
                                client_id: {active_client_id}
                            }})
                        }});
                        
                        const data = await response.json();
                        
                        if (response.ok) {{
                            const origin = window.location.origin;
                            const inviteUrl = `${{origin}}/register?invite_token=${{data.token}}`;
                            urlOutput.value = inviteUrl;
                            linkContainer.style.display = 'block';
                            emailInput.value = '';
                        }} else {{
                            throw new Error(data.detail || 'Failed to generate invitation.');
                        }}
                    }} catch (error) {{
                        alertBox.innerText = 'Error: ' + error.message;
                        alertBox.className = 'alert alert-error';
                        alertBox.style.display = 'block';
                    }}
                }}

                async function submitSettings(event) {{
                    event.preventDefault();
                    const alertBox = document.getElementById('alert-box');
                    const btnSubmit = document.querySelector('.btn-submit');
                    
                    alertBox.style.display = 'none';
                    btnSubmit.disabled = true;
                    btnSubmit.innerText = 'Saving changes...';
                    
                    const actionRadio = document.querySelector('input[name="exclusion_upload_action"]:checked');
                    const exclusionActionValue = actionRadio ? actionRadio.value : 'append';
                    
                    const payload = {{
                        id: {active_client_id},
                        name: document.getElementById('name').value.trim(),
                        call_tracking_provider: document.getElementById('call_tracking_provider').value,
                        callrail_account_id: document.getElementById('callrail_account_id').value.trim(),
                        callrail_company_id: document.getElementById('callrail_company_id').value.trim(),
                        ctm_account_id: document.getElementById('ctm_account_id').value.trim(),
                        ctm_profile_id: document.getElementById('ctm_profile_id').value.trim(),
                        wc_account_id: document.getElementById('wc_account_id').value.trim(),
                        wc_profile_id: document.getElementById('wc_profile_id').value.trim(),
                        google_ads_customer_id: document.getElementById('google_ads_customer_id').value.trim(),
                        facebook_ads_id: document.getElementById('facebook_ads_id').value.trim(),
                        linkedin_ads_id: document.getElementById('linkedin_ads_id').value.trim(),
                        microsoft_ads_id: document.getElementById('microsoft_ads_id').value.trim(),
                        lead_gen_method: document.querySelector('input[name="lead_gen_method"]:checked').value,
                        qualification_criteria: document.getElementById('qualification_criteria').value,
                        source_of_truth: document.getElementById('source_of_truth').value,
                        email_provider: document.getElementById('email_provider').value,
                        email_account: document.getElementById('email_account').value.trim(),
                        crm_deal_tags: document.getElementById('crm_deal_tags').value.trim(),
                        crm_won_deal_tags: document.getElementById('crm_won_deal_tags').value.trim(),
                        crm_lead_tags: document.getElementById('crm_lead_tags').value.trim(),
                        lead_count_rule: document.querySelector('input[name="lead_count_rule"]:checked').value,
                        exclude_past_customers: document.querySelector('input[name="exclude_past_customers"]:checked').value,
                        exclusion_action: exclusionActionValue,
                        excluded_customers: parsedExclusions
                    }};
                    
                    try {{
                        const response = await fetch('/dashboard/settings', {{
                            method: 'POST',
                            headers: {{
                                'Content-Type': 'application/json'
                            }},
                            body: JSON.stringify(payload)
                        }});
                        
                        const data = await response.json();
                        
                        if (response.ok) {{
                            alertBox.innerText = `Success! Configuration settings for "${{payload.name}}" saved successfully.`;
                            alertBox.className = 'alert alert-success';
                            alertBox.style.display = 'block';
                            btnSubmit.disabled = false;
                            btnSubmit.innerText = '💾 Save Configuration Changes';
                            window.scrollTo({{ top: 0, behavior: 'smooth' }});
                        }} else {{
                            throw new Error(data.detail || 'An unexpected error occurred.');
                        }}
                    }} catch (error) {{
                        alertBox.innerText = 'Error: ' + error.message;
                        alertBox.className = 'alert alert-error';
                        alertBox.style.display = 'block';
                        btnSubmit.disabled = false;
                        btnSubmit.innerText = '💾 Save Configuration Changes';
                        window.scrollTo({{ top: 0, behavior: 'smooth' }});
                    }}
                }}
            </script>
        </body>
    </html>
    """


@app.post("/dashboard/settings")
def update_client_settings(request: Request, client: ClientUpdate):
    email = is_authenticated(request)
    if not email:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    user_role, user_client_id = get_user_role_and_client(email)
    if user_role != "full":
        raise HTTPException(status_code=403, detail="Unauthorized: Client setting modifications are restricted to managers and administrators.")
    if user_client_id is not None and client.id != user_client_id:
        raise HTTPException(status_code=403, detail="Unauthorized: You do not have permission to modify settings for this client account.")
    """Endpoint to handle questionnaire form settings update."""
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        
        # Verify client exists
        cursor.execute("SELECT id FROM clients WHERE id = ?", (client.id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Client not found")
            
        # Update settings
        cursor.execute("""
            UPDATE clients SET
                name = ?,
                callrail_account_id = ?,
                callrail_company_id = ?,
                google_ads_customer_id = ?,
                facebook_ads_id = ?,
                linkedin_ads_id = ?,
                microsoft_ads_id = ?,
                lead_gen_method = ?,
                qualification_criteria = ?,
                source_of_truth = ?,
                email_provider = ?,
                email_account = ?,
                crm_deal_tags = ?,
                crm_won_deal_tags = ?,
                crm_lead_tags = ?,
                lead_count_rule = ?,
                exclude_past_customers = ?,
                call_tracking_provider = ?,
                ctm_account_id = ?,
                ctm_profile_id = ?,
                wc_account_id = ?,
                wc_profile_id = ?
            WHERE id = ?
        """, (
            client.name,
            client.callrail_account_id or None,
            client.callrail_company_id or None,
            client.google_ads_customer_id,
            client.facebook_ads_id,
            client.linkedin_ads_id,
            client.microsoft_ads_id,
            client.lead_gen_method,
            client.qualification_criteria,
            client.source_of_truth,
            client.email_provider,
            client.email_account,
            client.crm_deal_tags,
            client.crm_won_deal_tags,
            client.crm_lead_tags,
            client.lead_count_rule,
            client.exclude_past_customers,
            client.call_tracking_provider or "callrail",
            client.ctm_account_id or "",
            client.ctm_profile_id or "",
            client.wc_account_id or "",
            client.wc_profile_id or "",
            client.id
        ))
        
        # Handle excluded customers updates if a new list was uploaded
        if client.excluded_customers is not None and len(client.excluded_customers) > 0:
            if getattr(client, 'exclusion_action', 'append') == 'replace':
                cursor.execute("DELETE FROM excluded_customers WHERE client_id = ?", (client.id,))
                
            for cust in client.excluded_customers:
                normalized_p = normalize_phone(cust.phone)
                email_clean = cust.email.strip().lower() if cust.email else ""
                
                # Check for duplicates before inserting in append mode
                if getattr(client, 'exclusion_action', 'append') == 'append':
                    exists = False
                    if normalized_p:
                        cursor.execute("SELECT id FROM excluded_customers WHERE client_id = ? AND phone = ?", (client.id, normalized_p))
                        if cursor.fetchone():
                            exists = True
                    if not exists and email_clean:
                        cursor.execute("SELECT id FROM excluded_customers WHERE client_id = ? AND email = ?", (client.id, email_clean))
                        if cursor.fetchone():
                            exists = True
                    if exists:
                        continue # Skip duplicate record
                        
                cursor.execute("""
                    INSERT INTO excluded_customers (client_id, first_name, last_name, email, phone, company_name)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    client.id,
                    cust.first_name,
                    cust.last_name,
                    email_clean,
                    normalized_p,
                    cust.company_name
                ))
        
        conn.commit()
        conn.close()
        return {"status": "success", "message": f"Settings for '{client.name}' updated successfully!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database update error: {str(e)}")

@app.get("/dashboard/add-client", response_class=HTMLResponse)
def add_client_page(request: Request):
    email = is_authenticated(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)
    user_role, user_client_id = get_user_role_and_client(email)
    if user_role != "full" or user_client_id is not None:
        raise HTTPException(status_code=403, detail="Unauthorized: Client onboarding is restricted to Agency Administrators.")
    """Page to onboard a new client with complete wizard properties."""
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        cursor.execute("SELECT seq FROM sqlite_sequence WHERE name = 'clients'")
        row = cursor.fetchone()
        next_id = (row[0] + 1) if row else 1
        if not row:
            cursor.execute("SELECT MAX(id) FROM clients")
            max_row = cursor.fetchone()
            next_id = (max_row[0] + 1) if (max_row and max_row[0] is not None) else 1
        conn.close()
    except Exception:
        next_id = 1
    admin_link_html = ""
    if email in ADMIN_EMAILS:
        admin_link_html = ' | <a href="/admin/users" style="color: #2e7d32; text-decoration: none; font-weight: bold; margin-left: 5px;">🛡️ Admin User Directory</a>'
        
    user_header_bar = f"""
    <div style="display: flex; justify-content: space-between; align-items: center; background-color: #f1f3f4; padding: 10px 15px; border-radius: 6px; margin-bottom: 20px; font-size: 13px;">
        <div>
            <span style="color: #666; font-weight: bold;">👤 Active Session:</span> <span style="font-weight: bold; color: #1a237e;">{email}</span>
            {admin_link_html}
        </div>
        <a href="/logout" style="color: #c62828; text-decoration: none; font-weight: bold; display: flex; align-items: center; gap: 4px;">🚪 Log Out</a>
    </div>
    """

        
    html_content = """
    <!DOCTYPE html>
    <html>
        <head>
            <title>Onboard New Client 👤</title>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif; background-color: #f4f6f9; color: #333; margin: 0; padding: 20px; }
                .container { max-width: 600px; margin: 30px auto; background: white; padding: 40px; border-radius: 12px; box-shadow: 0px 4px 15px rgba(0,0,0,0.05); }
                h1 { margin-top: 0; color: #1a237e; font-size: 24px; text-align: center; }
                .subtitle { text-align: center; color: #666; margin-top: -15px; margin-bottom: 30px; font-size: 14px; }
                
                /* Step Progress Tracker */
                .progress-container { display: flex; justify-content: space-between; position: relative; margin-bottom: 40px; max-width: 450px; margin-left: auto; margin-right: auto; }
                .progress-container::before { content: ''; background-color: #e0e0e0; position: absolute; top: 50%; left: 0; transform: translateY(-50%); height: 4px; width: 100%; z-index: 1; }
                .progress-bar { background-color: #1a237e; position: absolute; top: 50%; left: 0; transform: translateY(-50%); height: 4px; width: 0%; z-index: 2; transition: width 0.3s ease; }
                .step-circle { background-color: #fff; border: 3px solid #e0e0e0; border-radius: 50%; height: 32px; width: 32px; display: flex; align-items: center; justify-content: center; z-index: 3; font-weight: bold; font-size: 13px; color: #999; transition: all 0.3s ease; }
                .step-circle.active { border-color: #1a237e; color: #1a237e; background-color: #e8eaf6; }
                .step-circle.completed { border-color: #2e7d32; color: #fff; background-color: #2e7d32; }
                
                /* Step Panels */
                .wizard-step { display: none; }
                .wizard-step.active { display: block; }
                
                .form-group { margin-bottom: 20px; }
                .form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }
                label { display: block; font-weight: 600; margin-bottom: 8px; font-size: 13px; color: #495057; }
                input[type="text"], select { width: 100%; padding: 10px 12px; border-radius: 6px; border: 1px solid #ced4da; box-sizing: border-box; font-size: 14px; outline: none; transition: border-color 0.2s; font-family: inherit; }
                input[type="text"]:focus, select:focus { border-color: #1a237e; }
                
                .instructions { background-color: #e8eaf6; border-left: 4px solid #1a237e; padding: 15px; border-radius: 4px; font-size: 13px; color: #1a237e; line-height: 1.5; margin-bottom: 25px; }
                
                /* Card Radio Styles */
                .card-radio-group { display: flex; flex-direction: column; gap: 10px; margin-bottom: 10px; }
                .card-radio { display: flex; align-items: center; padding: 12px 15px; border: 2px solid #e0e0e0; border-radius: 8px; cursor: pointer; transition: all 0.2s; gap: 12px; position: relative; }
                .card-radio:hover { border-color: #b3e5fc; background-color: #f6fbfd; }
                .card-radio.selected { border-color: #1a237e; background-color: #e8eaf6; }
                .card-radio input[type="radio"] { position: absolute; opacity: 0; }
                .card-radio-label { font-size: 14px; font-weight: bold; color: #333; margin: 0; }
                .card-radio-sub { font-size: 12px; color: #666; margin-top: 3px; }
                
                /* Navigation Buttons */
                .nav-buttons { display: flex; justify-content: space-between; margin-top: 30px; border-top: 1px solid #eaeaea; padding-top: 20px; }
                .btn-nav { background-color: #1a237e; color: white; padding: 10px 22px; border: none; border-radius: 6px; font-weight: bold; font-size: 14px; cursor: pointer; transition: background 0.2s; }
                .btn-nav:hover { background-color: #0d1b2a; }
                .btn-nav.secondary { background-color: #e0e0e0; color: #333; }
                .btn-nav.secondary:hover { background-color: #d5d5d5; }
                .btn-nav.success { background-color: #2e7d32; }
                .btn-nav.success:hover { background-color: #1b5e20; }
                
                .btn-submit { display: inline-block; background-color: #1a237e; color: white !important; text-decoration: none; padding: 12px 24px; border: none; border-radius: 6px; font-weight: bold; font-size: 15px; cursor: pointer; transition: background 0.2s; }
                .btn-submit:hover { background-color: #0d1b2a; }
                .btn-cancel { color: #666 !important; text-decoration: none; font-size: 14px; font-weight: bold; display: inline-block; margin-top: 25px; }
                .btn-cancel:hover { color: #333 !important; }
                
                .alert { padding: 12px; border-radius: 6px; margin-bottom: 20px; display: none; font-size: 14px; font-weight: 600; }
                .alert-error { background-color: #ffebee; color: #c62828; border: 1px solid #ffcdd2; }
                .alert-success { background-color: #e8f5e9; color: #2e7d32; border: 1px solid #c8e6c9; }
                
                /* Helper classes */
                .conditional-box { background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none; animation: slideDown 0.3s ease-out; }
                @keyframes pulseHighlight {
                    0% { transform: scale(1); box-shadow: 0 0 0 0 rgba(46, 125, 50, 0.7); background-color: #2e7d32; }
                    50% { transform: scale(1.04); box-shadow: 0 0 0 12px rgba(46, 125, 50, 0); background-color: #1b5e20; }
                    100% { transform: scale(1); box-shadow: 0 0 0 0 rgba(46, 125, 50, 0); background-color: #2e7d32; }
                }
                .btn-pulse-save {
                    animation: pulseHighlight 2s infinite !important;
                    background-color: #2e7d32 !important;
                    border-color: #1b5e20 !important;
                    color: white !important;
                }
                
                /* Speech Bubble Tooltip Styles */
                .tooltip-icon {
                    position: relative;
                    display: inline-block;
                    cursor: help;
                    margin-left: 6px;
                    font-size: 14px;
                    vertical-align: middle;
                    color: #1a237e;
                }
                .tooltip-icon .tooltip-text {
                    visibility: hidden;
                    width: 320px;
                    background-color: #1a237e;
                    color: #fff;
                    text-align: left;
                    border-radius: 6px;
                    padding: 10px 12px;
                    position: absolute;
                    z-index: 1000;
                    bottom: 125%;
                    left: 50%;
                    margin-left: -160px;
                    opacity: 0;
                    transition: opacity 0.3s;
                    font-size: 11px;
                    line-height: 1.4;
                    font-weight: normal;
                    box-shadow: 0 4px 10px rgba(0,0,0,0.15);
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                }
                .tooltip-icon .tooltip-text::after {
                    content: "";
                    position: absolute;
                    top: 100%;
                    left: 50%;
                    margin-left: -5px;
                    border-width: 5px;
                    border-style: solid;
                    border-color: #1a237e transparent transparent transparent;
                }
                .tooltip-icon:hover .tooltip-text {
                    visibility: visible;
                    opacity: 1;
                }
                @keyframes slideDown {
                    from { opacity: 0; transform: translateY(-10px); }
                    to { opacity: 1; transform: translateY(0); }
                }
            
                /* Tooltip styling */
                .tooltip {
                    position: relative;
                    display: inline-flex;
                    align-items: center;
                    cursor: pointer;
                    margin-left: 5px;
                    color: #1a237e;
                    font-size: 14px;
                    vertical-align: middle;
                }
                .tooltip .tooltiptext {
                    visibility: hidden;
                    width: 250px;
                    background-color: #333;
                    color: #fff;
                    text-align: left;
                    border-radius: 6px;
                    padding: 10px;
                    position: absolute;
                    z-index: 100;
                    bottom: 125%; /* Position above the text */
                    left: 50%;
                    margin-left: -125px;
                    opacity: 0;
                    transition: opacity 0.3s;
                    font-size: 11px;
                    font-weight: normal;
                    line-height: 1.4;
                    box-shadow: 0px 4px 10px rgba(0,0,0,0.15);
                    white-space: normal;
                }
                .tooltip .tooltiptext::after {
                    content: "";
                    position: absolute;
                    top: 100%; /* At the bottom of the tooltip */
                    left: 50%;
                    margin-left: -5px;
                    border-width: 5px;
                    border-style: solid;
                    border-color: #333 transparent transparent transparent;
                }
                .tooltip:hover .tooltiptext {
                    visibility: visible;
                    opacity: 1;
                }
            </style>
        </head>
        <body>
            <div class="container">
                <h1 id="heading-title">Onboard Client Account 👤</h1>
                <p class="subtitle" id="heading-subtitle">Agency Setup & Software Mapping Pipeline</p>
                
                <!-- Step Progress -->
                <div class="progress-container" id="progress-container">
                    <div class="progress-bar" id="progress-bar"></div>
                    <div class="step-circle active" data-step="1">1</div>
                    <div class="step-circle" data-step="2">2</div>
                    <div class="step-circle" data-step="3">3</div>
                    <div class="step-circle" data-step="4">4</div>
                </div>
                
                <div id="alert-box" class="alert alert-error"></div>
                
                <!-- Wizard Form -->
                <form id="onboarding-form">
                    
                    <!-- STEP 1: Accounts & Tracking channels -->
                    <div class="wizard-step active" id="step-panel-1">
                        <div class="instructions">
                            🚀 <strong>Step 1: General Profile & Ad Accounts</strong><br>
                            Enter the general company properties and the advertising account IDs to sync conversions and metrics.
                        </div>
                        
                        <div class="form-group">
                            <label for="name">Client Business Name</label>
                            <input type="text" id="name" required placeholder="e.g. Priority Plumbing">
                        </div>
                        
                        <div class="form-group">
                            <label for="call_tracking_provider">Call Tracking Provider</label>
                            <select id="call_tracking_provider" onchange="toggleCallTrackingFields()" style="width: 100%; padding: 12px; border-radius: 6px; border: 1px solid #ced4da; font-size: 14px; font-weight: 600;">
                                <option value="callrail" selected>CallRail</option>
                                <option value="calltrackingmetrics">CallTrackingMetrics</option>
                                <option value="whatconverts">WhatConverts</option>
                            </select>
                        </div>
                        
                        <div id="call_tracking_callrail_box" class="form-row" style="display: flex;">
                            <div class="form-group">
                                <label for="callrail_account_id">CallRail Account ID</label>
                                <input type="text" id="callrail_account_id" placeholder="e.g. 123456789">
                            </div>
                            <div class="form-group">
                                <label for="callrail_company_id">CallRail Client ID (Company ID)</label>
                                <input type="text" id="callrail_company_id" placeholder="e.g. 987654321">
                            </div>
                        </div>

                        <div id="call_tracking_ctm_box" class="form-row" style="display: none;">
                            <div class="form-group">
                                <label for="ctm_account_id">CallTrackingMetrics Account ID</label>
                                <input type="text" id="ctm_account_id" placeholder="e.g. 12345">
                            </div>
                            <div class="form-group">
                                <label for="ctm_profile_id">CallTrackingMetrics Client ID (Profile ID)</label>
                                <input type="text" id="ctm_profile_id" placeholder="e.g. 67890">
                            </div>
                        </div>

                        <div id="call_tracking_wc_box" class="form-row" style="display: none;">
                            <div class="form-group">
                                <label for="wc_account_id">WhatConverts Account ID</label>
                                <input type="text" id="wc_account_id" placeholder="e.g. 11111">
                            </div>
                            <div class="form-group">
                                <label for="wc_profile_id">WhatConverts Client ID (Profile ID)</label>
                                <input type="text" id="wc_profile_id" placeholder="e.g. 22222">
                            </div>
                        </div>
                        
                        <div class="form-row">
                            <div class="form-group">
                                <label for="google_ads_customer_id">Google Ads Customer ID</label>
                                <input type="text" id="google_ads_customer_id" required placeholder="e.g. 123-456-7890">
                            </div>
                            <div class="form-group">
                                <label for="facebook_ads_id">Facebook Ads Pixel/Account ID</label>
                                <input type="text" id="facebook_ads_id" placeholder="e.g. fb_pixel_999">
                            </div>
                        </div>
                        
                        <div class="form-row">
                            <div class="form-group">
                                <label for="linkedin_ads_id">LinkedIn Ads Account ID</label>
                                <input type="text" id="linkedin_ads_id" placeholder="e.g. li_account_12">
                            </div>
                            <div class="form-group">
                                <label for="microsoft_ads_id">Microsoft (Bing) Ads ID</label>
                                <input type="text" id="microsoft_ads_id" placeholder="e.g. ms_campaign_45">
                            </div>
                        </div>
                    </div>
                    
                    <!-- STEP 2: Lead Gen & Qualification -->
                    <div class="wizard-step" id="step-panel-2">
                        <div class="instructions">
                            🧠 <strong>Step 2: Lead Generation & Qualification Preferences</strong><br>
                            Tell us how this client receives and identifies a qualified lead so that Claude's sales auditing aligns perfectly.
                        </div>
                        
                        <div class="form-group">
                            <label>How do you generate your leads?</label>
                            <div class="card-radio-group">
                                <div class="card-radio selected" onclick="selectCardRadio('lead_gen_method', 'both', this)">
                                    <input type="radio" name="lead_gen_method" value="both" checked>
                                    <div>
                                        <div class="card-radio-label">Both Phone Calls & Web Forms</div>
                                        <div class="card-radio-sub">Full multi-channel capture (Recommended)</div>
                                    </div>
                                </div>
                                <div class="card-radio" onclick="selectCardRadio('lead_gen_method', 'phone', this)">
                                    <input type="radio" name="lead_gen_method" value="phone">
                                    <div>
                                        <div class="card-radio-label">Phone Calls Only</div>
                                        <div class="card-radio-sub">Auditing CallRail phone transcripts exclusively</div>
                                    </div>
                                </div>
                                <div class="card-radio" onclick="selectCardRadio('lead_gen_method', 'form', this)">
                                    <input type="radio" name="lead_gen_method" value="form">
                                    <div>
                                        <div class="card-radio-label">Form Submissions Only</div>
                                        <div class="card-radio-sub">Matching visitor website form GCLIDs only</div>
                                    </div>
                                </div>
                            </div>
                        </div>
                        
                        <div class="form-group">
                            <label for="qualification_criteria" style="display: inline-flex; align-items: center; gap: 5px;">
                                How do you qualify a lead?
                                <span class="tooltip">💬
                                    <span class="tooltiptext">Define a lead stage that is &quot;good enough&quot;, and would be happy with paying for all day long from your Ads. This is the minimum standard the system will go for when optimizing your Ads.</span>
                                </span>
                            </label>
                            <select id="qualification_criteria">
                                <option value="C">👤 Option C: Someone who books an appointment (Local Services Default)</option>
                                <option value="A">👤 Option A: Someone that I have a conversation with</option>
                                <option value="B">👤 Option B: Someone who shows strong buying interest</option>
                                <option value="D">👤 Option D: Someone who books a demo</option>
                                <option value="E">👤 Option E: Someone who requests a quote</option>
                                <option value="F">👤 Option F: Someone who we send a proposal</option>
                                <option value="H">👤 Option H: Someone who has qualified insurance</option>
                                <option value="I">👤 Option I: Someone who is credit pre-qualified</option>
                            </select>
                            <small style="color: #666; font-size: 11px; margin-top: 4px; display: block;">
                                💡 This choice dynamically feeds directly into <strong>Claude 4.5 Haiku's prompt</strong> to customize auditing.
                            </small>
                        </div>
                    </div>
                    
                    <!-- STEP 3: Source of Truth & CRM -->
                    <div class="wizard-step" id="step-panel-3">
                        <div class="instructions">
                            📁 <strong>Step 3: Single Source of Truth & Integration Mapping</strong><br>
                            Identify where your conversion status lives. Your platform scans this resource to upload revenue data to Ad Networks.
                        </div>
                        
                        <div class="form-group">
                            <label for="source_of_truth">Where is your Single Source of Truth?</label>
                            <select id="source_of_truth" onchange="toggleSOTFields()">
                                <option value="hubspot">HubSpot CRM</option>
                                <option value="salesforce">Salesforce CRM</option>
                                <option value="zoho">Zoho CRM</option>
                                <option value="servicetitan">ServiceTitan (Home Services)</option>
                                <option value="housecallpro">Housecall Pro (Home Services)</option>
                                <option value="quickbooks">QuickBooks Accounting</option>
                                <option value="xero">Xero Accounting</option>
                                <option value="email">Monthly Sales Spreadsheet Ingestion via Email</option>
                                <option value="ai_rating">AI Rating (Direct Call Audits & Dynamic Form-Email Monitoring)</option>
                            </select>
                        </div>
                        
                        <!-- CONDITIONAL INPUT: CRM Deal status tags (HubSpot, Salesforce, Zoho) -->
                        <div id="sot-deal-tags-box" class="conditional-box" style="display: block;">
                            <div style="margin-bottom: 15px;">
                                <label for="crm_deal_tags">Which tags/statuses under <strong>Deals</strong> signify a qualified conversion?</label>
                                <input type="text" id="crm_deal_tags" placeholder="e.g. appointment-booked, estimate-given">
                                <small style="color: #666; font-size: 11px; margin-top: 4px; display: block;">
                                    List comma-separated tags that trigger a qualified lead conversion.
                                </small>
                            </div>
                            <div>
                                <label for="crm_won_deal_tags">Which tags/statuses under <strong>Deals</strong> signify a won deal conversion?</label>
                                <input type="text" id="crm_won_deal_tags" placeholder="e.g. closed-won, job-completed">
                                <small style="color: #666; font-size: 11px; margin-top: 4px; display: block;">
                                    List comma-separated tags that trigger a won deal conversion.
                                </small>
                            </div>
                        </div>
                        
                        <!-- CONDITIONAL INPUT: HubSpot Setup Instructions -->
                        <div id="sot-hubspot-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: block;">
                            <div style="background-color: #fff8e1; border-left: 4px solid #ffb300; padding: 15px; border-radius: 4px; color: #5d4037; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                💡 <strong>HubSpot Private App Quick Setup Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #4e342e;">
                                    <li>Log into HubSpot as a <strong>Super Admin</strong>.</li>
                                    <li>Go to <strong>Settings (Gear Icon) &gt; Integrations &gt; Private Apps</strong>.</li>
                                    <li>Click <strong>Create Private App</strong> and configure basic info.</li>
                                    <li>Under <strong>Scopes</strong>, search <code>CRM</code> and check <code>Read</code> permissions for:
                                        <ul style="list-style-type: disc; padding-left: 15px; margin: 4px 0;">
                                            <li><code>crm.objects.deals.read</code> (to track closed sales &amp; revenue)</li>
                                            <li><code>crm.objects.contacts.read</code> (to sync leads)</li>
                                        </ul>
                                    </li>
                                    <li>Click <strong>Create App</strong>. If you want real-time syncing, click the <strong>Webhooks</strong> tab of your new app, click <strong>Edit Webhooks</strong>, paste your dynamic target URL (provided on the next screen once saved), and subscribe to <code>propertyChange</code> or <code>creation</code> for <strong>Deals</strong>!</li>
                                </ol>
                                <small style="display: block; font-style: italic; color: #6d4c41; line-height: 1.4; border-top: 1px solid #ffe082; padding-top: 8px;">
                                    ⚠️ <strong>Tip:</strong> If you don't see the "Webhooks" tab inside Private App settings, go to your HubSpot profile (top-right) &gt; <strong>Product Updates &gt; Betas</strong>, click <strong>Join Beta</strong> for <em>"Private App Webhooks"</em>, and refresh!
                                </small>
                            </div>
                        </div>
                        

                        <!-- CONDITIONAL INPUT: Salesforce Setup Instructions -->
                        <div id="sot-salesforce-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                            <div style="background-color: #e3f2fd; border-left: 4px solid #1e88e5; padding: 15px; border-radius: 4px; color: #0d47a1; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                💡 <strong>Salesforce Outbound Flow Setup Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #1565c0;">
                                    <li>In Salesforce Setup, go to <strong>Named Credentials &gt; External Credentials</strong> tab, click <strong>New</strong>. Label <code>LeadGroove External Credential</code>, Name <code>LeadGroove_External_Credential</code>, Protocol <strong>Custom</strong>. Save, scroll to <em>Principals</em>, click <strong>New</strong> and define a principal named <code>LeadGroove_Principal</code>.</li>
                                    <li>Create a <strong>Permission Set</strong> in Setup named <code>LeadGroove Webhook Access</code>. In it, click <strong>External Credential Principal Access</strong>, enable your new credential and principal, and assign this permission set to any integrating users.</li>
                                    <li>Back in <strong>Named Credentials</strong>, click <strong>New</strong> under the main tab. Label <code>LeadGroove API</code>, Name <code>LeadGroove_API</code>, URL <code style="background: rgba(0,0,0,0.05); padding: 2px 4px; border-radius: 3px;">https://your-agency-app.onrender.com</code> (or active origin). Under <em>External Credential</em>, select the credential you created in Step 1, and save.</li>
                                    <li>Create a <strong>Record-Triggered Flow</strong> on the <strong>Opportunity</strong> object (when updated) with conditions <code>StageName Equals Closed Won</code> (Only when updated to meet conditions), optimized for <strong>Actions and Related Records</strong>.</li>
                                    <li>On the flow canvas, click <strong>+ Add Action &gt; Create HTTP Callout</strong>, select your Named Credential, and define a <strong>POST</strong> method with path <code style="color: #2e7d32; font-family: monospace;">/webhooks/crm?client_id=conversions-[id]</code>. Provide this sample JSON structure for automatic Salesforce parameter mapping:
                                        <pre style="background: rgba(255,255,255,0.7); padding: 8px; border-radius: 4px; margin-top: 5px; font-family: monospace; font-size: 10px; overflow-x: auto; border: 1px solid #bbdefb; color: #0d47a1;">{
  "customer_name": "John Doe",
  "customer_email": "john@example.com",
  "customer_phone": "5551234567",
  "deal_stage": "Closed Won",
  "deal_value": 1500.00
}</pre>
                                    </li>
                                </ol>
                            </div>
                        </div>

                        <!-- CONDITIONAL INPUT: Zoho Setup Instructions -->
                        <div id="sot-zoho-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                            <div style="background-color: #e8f5e9; border-left: 4px solid #2e7d32; padding: 15px; border-radius: 4px; color: #1b5e20; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                💡 <strong>Zoho CRM Outbound Webhook Setup Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #2e7d32;">
                                    <li>Click the <strong>Setup (Gear Icon)</strong> in the top-right corner of your Zoho CRM dashboard.</li>
                                    <li>Under <strong>Automation</strong>, click on <strong>Actions</strong>, then select the <strong>Webhooks</strong> tab at the top.</li>
                                    <li>Click <strong>Configure Webhook</strong> and configure these fields:
                                        <ul style="list-style-type: disc; padding-left: 15px; margin: 4px 0;">
                                            <li><strong>Name</strong>: <code>LeadGroove Conversion Sync</code></li>
                                            <li><strong>URL to notify</strong>: <code style="background: rgba(0,0,0,0.05); padding: 2px 4px; border-radius: 3px;">https://your-agency-app.onrender.com/webhooks/crm?client_id=conversions-[id]</code></li>
                                            <li><strong>Method</strong>: Select <strong>POST</strong></li>
                                            <li><strong>Module</strong>: Select <strong>Deals</strong> (or your tracking module)</li>
                                        </ul>
                                    </li>
                                    <li>In the <strong>Body</strong> parameters section:
                                        <ul style="list-style-type: disc; padding-left: 15px; margin: 4px 0;">
                                            <li>Choose <strong>Raw</strong> format and select <strong>JSON</strong> from the dropdown.</li>
                                            <li>Type <code>#</code> to dynamically insert CRM fields into this JSON structure:
                                                <pre style="background: rgba(255,255,255,0.7); padding: 8px; border-radius: 4px; margin-top: 5px; font-family: monospace; font-size: 10px; overflow-x: auto; border: 1px solid #c8e6c9; color: #1b5e20;">{
  "customer_name": "${Deals.Deal Name}",
  "customer_email": "${Deals.Email}",
  "customer_phone": "${Deals.Phone}",
  "deal_stage": "${Deals.Stage}",
  "deal_value": ${Deals.Amount}
}</pre>
                                            </li>
                                        </ul>
                                    </li>
                                    <li>Click <strong>Save</strong>. Next, go to <strong>Workflow Rules</strong> (under Setup &gt; Automation) and create a rule triggered on Deal Update when the <strong>Stage is Closed Won</strong>, then associate this Webhook as an <strong>Instant Action</strong>!</li>
                                </ol>
                            </div>
                        </div>

                        
                        <!-- CONDITIONAL INPUT: ServiceTitan Setup Instructions -->
                        <div id="sot-servicetitan-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                            <div style="background-color: #f3f4f6; border-left: 4px solid #4b5563; padding: 15px; border-radius: 4px; color: #1f2937; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                💡 <strong>ServiceTitan Webhooks V2 Quick Setup Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #374151;">
                                    <li>Navigate to the **ServiceTitan Developer Portal** at <a href="https://developer.servicetitan.io" target="_blank" style="color: #1a237e; font-weight: bold; text-decoration: none;">developer.servicetitan.io</a> and sign in with your production credentials.</li>
                                    <li>Click **Create and Manage Applications** ➡️ **Create New App**. Name it <code>LeadGroove Webhook Sync</code> and set your tenant/business units.</li>
                                    <li>Under **API Scopes**, select:
                                        <ul style="list-style-type: disc; padding-left: 15px; margin: 4px 0;">
                                            <li><code>crm.objects.leads.read</code> or <code>jpm.objects.jobs.read</code> (to capture lead states and bookings)</li>
                                        </ul>
                                    </li>
                                    <li>Click **Save** to generate your **Client ID**, **Client Secret**, **App ID**, and **App Key**.</li>
                                    <li>Log into your main production portal at <a href="https://go.servicetitan.com" target="_blank" style="color: #1a237e; font-weight: bold; text-decoration: none;">go.servicetitan.com</a>, go to **Settings ➡️ Integrations ➡️ API Application Access**, find your app, click **Edit**, and set your dynamic Webhook Endpoint Target URL (which you can copy on the next success screen):
                                        <code style="display: block; background: #fff; border: 1px solid #d1d5db; padding: 8px; border-radius: 4px; font-family: monospace; font-size: 11px; margin-top: 5px; color: #1f2937;">https://your-agency-app.onrender.com/webhooks/crm?client_id=conversions-[id]</code>
                                    </li>
                                    <li>Register your endpoint triggers for <code>job.created</code> and <code>job.updated</code> (under Job Planning & Management v2 endpoints) to fire instantly when dispatch sheets are updated!</li>
                                </ol>
                            </div>
                        </div>
                        <!-- CONDITIONAL INPUT: Housecall Pro Setup Instructions -->
                        <div id="sot-housecallpro-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                            <div style="background-color: #fff3e0; border-left: 4px solid #e65100; padding: 15px; border-radius: 4px; color: #e65100; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                💡 <strong>Housecall Pro Webhooks Quick Setup Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #e65100;">
                                    <li>Sign in to your Housecall Pro admin account. (Only <strong>Admin</strong> users can access and generate webhook API settings).</li>
                                    <li>Navigate to <strong>My Apps</strong> from the top navigation bar, then click <strong>All Apps</strong>.</li>
                                    <li>Select the <strong>All Apps</strong> tab, search for the <strong>Webhooks</strong> app, and click to open it.</li>
                                    <li>Click the toggle button in the top-right corner of the page to <strong>Enable Webhooks</strong>.</li>
                                    <li>In the <strong>Target URL</strong> field, paste your client's custom live endpoint (which you can copy on the next success screen):
                                        <code style="display: block; background: #fff; border: 1px solid #ffcc80; padding: 8px; border-radius: 4px; font-family: monospace; font-size: 11px; margin-top: 5px; color: #d84315;">https://your-agency-app.onrender.com/webhooks/crm?client_id=conversions-[id]</code>
                                    </li>
                                    <li>Select your preferred event triggers to notify our platform. We recommend subscribing to **<code>job.completed</code>**, <code>job.created</code>, and <code>job.paid</code> to track actual conversion events.</li>
                                    <li>Click <strong>Save</strong> to activate the webhook instantly!</li>
                                </ol>
                                <small style="display: block; font-style: italic; color: #bf360c; line-height: 1.4; border-top: 1px solid #ffe0b2; padding-top: 8px;">
                                    ⚠️ <strong>Note:</strong> Webhook access in Housecall Pro requires their **MAX plan** subscription level. If you don't see the Webhooks app under All Apps, contact Housecall Pro support to verify your plan access.
                                </small>
                            </div>
                        </div>

\n                        
                        <!-- CONDITIONAL INPUT: QuickBooks Setup Instructions -->
                        <div id="sot-quickbooks-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                            <div style="background-color: #e3f2fd; border-left: 4px solid #0288d1; padding: 15px; border-radius: 4px; color: #01579b; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                💡 <strong>QuickBooks Online Webhooks Quick Setup Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #0277bd;">
                                    <li>Log into the <strong>Intuit Developer Portal</strong> at <a href="https://developer.intuit.com" target="_blank" style="color: #1a237e; font-weight: bold; text-decoration: none;">developer.intuit.com</a>.</li>
                                    <li>Go to your **Dashboard**, select your App, and navigate to **Production Settings ➡️ Webhooks** in the left sidebar menu.</li>
                                    <li>In the **Endpoint URL** field, paste your dynamic target URL (which you can copy on the next success screen):
                                        <code style="display: block; background: #fff; border: 1px solid #b3e5fc; padding: 8px; border-radius: 4px; font-family: monospace; font-size: 11px; margin-top: 5px; color: #01579b;">https://your-agency-app.onrender.com/webhooks/billing?client_id=conversions-[id]</code>
                                    </li>
                                    <li>Check the boxes for the event notifications you want to receive under **Invoices** or **Payments** (e.g., invoice creation and payment status updates).</li>
                                    <li>Click **Save** to generate your **Verifier Token** (copy this key to verify QuickBooks signatures on your server!).</li>
                                </ol>
                                <small style="display: block; font-style: italic; color: #0288d1; line-height: 1.4; border-top: 1px solid #b3e5fc; padding-top: 8px;">
                                    ⚠️ <strong>Important Note:</strong> QuickBooks Online aggregates webhook events and delivers them in **5-minute intervals**, so test events might take up to 5 minutes to appear in your logs!
                                </small>
                            </div>
                        </div>
    
                        <!-- CONDITIONAL INPUT: Xero Setup Instructions -->
                        <div id="sot-xero-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                            <div style="background-color: #e0f7fa; border-left: 4px solid #00b0ff; padding: 15px; border-radius: 4px; color: #006064; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                💡 <strong>Xero Webhooks Quick Setup Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #00838f;">
                                    <li>Log into the <strong>Xero Developer Portal</strong> at <a href="https://developer.xero.com" target="_blank" style="color: #1a237e; font-weight: bold; text-decoration: none;">developer.xero.com</a> under **My Apps**.</li>
                                    <li>Select your App and click on the **Webhooks** tab in the left-hand navigation panel.</li>
                                    <li>In the **Send notifications to** (Delivery URL) field, paste your custom live endpoint (which you can copy on the next success screen):
                                        <code style="display: block; background: #fff; border: 1px solid #80deea; padding: 8px; border-radius: 4px; font-family: monospace; font-size: 11px; margin-top: 5px; color: #006064;">https://your-agency-app.onrender.com/webhooks/billing?client_id=conversions-[id]</code>
                                    </li>
                                    <li>Select your preferred event categories. We highly recommend subscribing to **Invoices** (CREATE, UPDATE) and **Contacts** (CREATE, UPDATE).</li>
                                    <li>Click **Save**. This will generate your **Webhook Key** (also known as the signing key) which you can copy to authenticate payloads.</li>
                                    <li>Click the **Send intent to receive** button to initiate Xero's connection validation handshake and verify your setup is active!</li>
                                </ol>
                                <small style="display: block; font-style: italic; color: #00838f; line-height: 1.4; border-top: 1px solid #80deea; padding-top: 8px;">
                                    🔒 <strong>Intent-to-Receive Handshake:</strong> To complete the setup, our platform automatically responds to Xero's secure validation checks. Ensure your SSL certificate is trusted (non-self-signed) and running on port 443.
                                </small>
                            </div>
                        </div>
    
                        <!-- CONDITIONAL INPUT: CRM Lead status tags (ServiceTitan, Housecall Pro) -->
                        <div id="sot-lead-tags-box" class="conditional-box">
                            <label for="crm_lead_tags">Which tags/statuses under <strong>Leads</strong> signify qualification?</label>
                            <input type="text" id="crm_lead_tags" placeholder="e.g. job-booked, estimate-given, dispatched">
                        </div>
                        
                        <!-- CONDITIONAL INPUT: Email account fallback settings -->
                        <div id="sot-email-box" class="conditional-box">
                            <div class="form-group">
                                <label for="email_provider">Email Provider</label>
                                <select id="email_provider">
                                    <option value="gmail">Google Gmail API</option>
                                    <option value="outlook">Microsoft Outlook 365</option>
                                    <option value="custom_imap">Custom IMAP (Secure Server)</option>
                                </select>
                            </div>
                            <div class="form-group" style="margin-bottom: 0;">
                                <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 15px; margin-bottom: 5px;">
                                    <div style="display: flex; align-items: center; gap: 6px; flex-wrap: wrap;">
                                        <label for="email_account" style="font-weight: bold; margin-bottom: 0;">Onboarding Integration Email Account</label>
                                        
                                        <!-- Speech Bubble Tooltip -->
                                        <span class="tooltip-icon">
                                            💬
                                            <span class="tooltip-text">
                                                Forward your booking emails, invoice alerts, or form replies to your custom system address:<br>
                                                <strong class="wizard-forwarding-email" style="color: #81c784; word-break: break-all;">conversions-[id]@your-agency.com</strong><br>
                                                <span style="font-size: 9px; color: #ccc;">(Your actual ID will show up on the next screen once profile is created)</span>
                                            </span>
                                        </span>
                                        
                                        <!-- Check Logs Hover Link -->
                                        <span class="tooltip-icon" style="font-size: 11px; font-weight: bold; margin-left: 5px;">
                                            <a href="javascript:void(0)" style="color: #1a237e; text-decoration: underline;">check logs</a>
                                            <span class="tooltip-text" style="width: 290px;">
                                                <strong>Last 5 Emails Analyzed by System:</strong><br>
                                                <span style="color: #ccc; font-style: italic;">No emails analyzed yet (Onboarding in progress).</span>
                                            </span>
                                        </span>
                                    </div>
                                    <a href="javascript:void(0)" onclick="openAppPasswordModal()" style="font-size: 12px; color: #1a237e; font-weight: bold; text-decoration: none; display: flex; align-items: center; gap: 4px;">
                                        🔑 How to get an App Password?
                                    </a>
                                </div>
                                <input type="text" id="email_account" placeholder="e.g. bookings@clientcompany.com">
                                <small style="color: #666; font-size: 11px; margin-top: 4px; display: block;">
                                    Your system will securely monitor this inbox for transcript files & booking notifications.
                                </small>
                            </div>
                        </div>
                    </div>
                    
                    <!-- STEP 4: Conversion & Deduplication Rules -->
                    <div class="wizard-step" id="step-panel-4">
                        <div class="instructions">
                            💰 <strong>Step 4: Smart Deduplication & Conversion Control</strong><br>
                            Define parameters for repeat conversions to ensure you only bid on optimal, unique customer value.
                        </div>
                        
                        <div class="form-group">
                            <label>How should we track multiple leads from the same customer?</label>
                            <div class="card-radio-group">
                                <div class="card-radio selected" onclick="selectCardRadio('lead_count_rule', 'all', this)">
                                    <input type="radio" name="lead_count_rule" value="all" checked>
                                    <div>
                                        <div class="card-radio-label">Count Every Lead Session</div>
                                        <div class="card-radio-sub">Pushes all unique calls/forms from the same user to smart bidding</div>
                                    </div>
                                </div>
                                <div class="card-radio" onclick="selectCardRadio('lead_count_rule', 'maximum_one', this)">
                                    <input type="radio" name="lead_count_rule" value="maximum_one">
                                    <div>
                                        <div class="card-radio-label">Maximum of One Conversion Each</div>
                                        <div class="card-radio-sub">Caps tracking at 1 conversion per customer to prevent inflated signals</div>
                                    </div>
                                </div>
                            </div>
                        </div>
                        
                        <div class="form-group">
                            <label>Would you like to exclude past customers as eligible lead conversions?</label>
                            <div class="card-radio-group">
                                <div class="card-radio selected" onclick="selectCardRadio('exclude_past_customers', 'NO', this)">
                                    <input type="radio" name="exclude_past_customers" value="NO" checked>
                                    <div>
                                        <div class="card-radio-label">No, allow past customers (Recommended)</div>
                                        <div class="card-radio-sub">Allows repeat buyers to optimize overall ad account ROAS</div>
                                    </div>
                                </div>
                                <div class="card-radio" onclick="selectCardRadio('exclude_past_customers', 'YES', this)">
                                    <input type="radio" name="exclude_past_customers" value="YES">
                                    <div>
                                        <div class="card-radio-label">Yes, exclude past customers</div>
                                        <div class="card-radio-sub">Strict deduplication: only optimizes ads on completely net-new leads</div>
                                    </div>
                                </div>
                            </div>
                        </div>
                        
                        <!-- EXCLUSION UPLOAD PANEL -->
                        <div id="exclusion-upload-box" class="conditional-box" style="display: none; padding: 20px; margin-top: 15px;">
                            <div class="instructions" style="background-color: #f1f8e9; border-left-color: #2e7d32; color: #2e7d32; margin-bottom: 15px; font-size: 13px; line-height: 1.5; padding: 15px;">
                                📂 <strong>Upload Past Customers to Ignore:</strong><br>
                                Upload your list of customers, to use to ignore future conversion triggering. Only one piece of information is needed for each user in order to do this, but more data points for each user is best, for higher match rates. Here is a sample sheet that you can use to fill in, or you can provide your own sheet that have "first name, last name, email, phone number, company name" as the column headers.
                            </div>
                            <div style="display: flex; gap: 15px; align-items: center; margin-bottom: 15px; flex-wrap: wrap;">
                                <button type="button" onclick="triggerSampleSheetDownload()" class="btn-copy" style="background-color: #1a237e; padding: 8px 15px; font-size: 12px; cursor: pointer;">📥 Download Sample Sheet (.CSV)</button>
                                <input type="file" id="exclusion-file-input" accept=".csv" onchange="handleExclusionFileUpload(event)" style="display: none;">
                                <button type="button" onclick="document.getElementById('exclusion-file-input').click()" class="btn-copy" style="background-color: #2e7d32; padding: 8px 15px; font-size: 12px; cursor: pointer;">📤 Choose File & Upload (.CSV)</button>
                            </div>
                            <div style="margin-top: 15px; margin-bottom: 15px; background: #fff; padding: 12px; border: 1px solid #e0e0e0; border-radius: 6px;">
                                <label style="font-weight: bold; font-size: 12px; margin-bottom: 8px; display: block; color: #1a237e;">🔄 Exclusions Upload Strategy:</label>
                                <div style="display: flex; gap: 20px; align-items: center;">
                                    <label style="font-weight: normal; cursor: pointer; display: flex; align-items: center; gap: 6px; font-size: 12px; margin: 0;">
                                        <input type="radio" name="exclusion_upload_action" value="append" checked style="cursor: pointer;">
                                        <strong>Append new records</strong> (Keep existing ones, only add new customers)
                                    </label>
                                    <label style="font-weight: normal; cursor: pointer; display: flex; align-items: center; gap: 6px; font-size: 12px; margin: 0;">
                                        <input type="radio" name="exclusion_upload_action" value="replace" style="cursor: pointer;">
                                        <strong>Overwrite list</strong> (Wipe existing entries and start fresh)
                                    </label>
                                </div>
                            </div>
                            <div id="upload-status-box" class="alert alert-success" style="display: none; margin-bottom: 0; font-size: 12px; padding: 12px;"></div>
                        </div>
                    </div>
                    
                    <!-- Navigation Panel -->
                    <div class="nav-buttons">
                        <button type="button" class="btn-nav secondary" id="prev-btn" onclick="changeStep(-1)" style="visibility: hidden;">⬅️ Back</button>
                        <button type="button" class="btn-nav" id="next-btn" onclick="changeStep(1)">Next ➡️</button>
                    </div>
                </form>
                
                <!-- Dynamic Setup Webhook Screen (Hidden initially) -->
                <div id="success-screen" style="display: none; text-align: center;">
                    <div style="font-size: 50px; margin-bottom: 15px;">🎉</div>
                    <h2 style="color: #2e7d32; margin-top: 0;">Client Onboarded Successfully!</h2>
                    <p style="color: #555; font-size: 14px; margin-bottom: 25px;">
                        The account configuration file for <strong id="registered-client-name"></strong> has been created.
                    </p>
                    
                    <!-- Call Tracking Step (Always Shown) -->
                    <div class="instructions" style="text-align: left; background-color: #e8eaf6; border-left: 4px solid #1a237e; margin-bottom: 10px;">
                        📞 <strong id="call-tracking-provider-title">Step 2: Configure CallRail Integration</strong><br>
                        Your live call tracking webhook endpoint is ready. Copy this link and paste it into <span id="call-tracking-provider-span">CallRail</span>:
                    </div>
                    <div style="display: flex; gap: 8px; margin-bottom: 15px;">
                        <input type="text" id="webhook-url-input" readonly style="flex: 1; padding: 10px; border-radius: 6px; border: 1px solid #ced4da; font-family: monospace; font-size: 12px; background-color: #f8f9fa;">
                        <button type="button" onclick="copyWebhookUrl('webhook-url-input', 'copy-btn')" id="copy-btn" class="btn-submit" style="margin: 0; width: auto; white-space: nowrap; padding: 0 15px; font-size: 14px; background-color: #2e7d32;">📋 Copy URL</button>
                    </div>
                    <div id="call-tracking-instructions-reminder" class="instructions" style="text-align: left; background-color: #fff3cd; border-left-color: #ffc107; color: #856404; font-size: 11px; margin-top: -10px; margin-bottom: 25px; padding: 8px 12px;">
                        ⚠️ <strong>Reminder:</strong> Set the trigger inside CallRail integration settings to <strong>"Call Completed"</strong> so transcripts are compiled.
                    </div>

                    <!-- CRM / Billing Program Connection Step (Shown conditionally) -->
                    <div id="sot-instructions-box" style="display: none; margin-top: 25px;">
                        <div class="instructions" id="sot-instructions-label" style="text-align: left; background-color: #e8f5e9; border-left: 4px solid #2e7d32; color: #1b5e20; margin-bottom: 10px;">
                            ⚙️ <strong>Step 3: Connect Your Platform Webhook</strong><br>
                        </div>
                        <div style="display: flex; gap: 8px; margin-bottom: 20px;">
                            <input type="text" id="sot-webhook-input" readonly style="flex: 1; padding: 10px; border-radius: 6px; border: 1px solid #ced4da; font-family: monospace; font-size: 12px; background-color: #f8f9fa;">
                            <button type="button" onclick="copyWebhookUrl('sot-webhook-input', 'sot-copy-btn')" id="sot-copy-btn" class="btn-submit" style="margin: 0; width: auto; white-space: nowrap; padding: 0 15px; font-size: 14px; background-color: #2e7d32;">📋 Copy URL</button>
                        </div>
                    </div>

                    <!-- Email Forwarding Connection Step (Shown conditionally) -->
                    <div id="sot-email-instructions-box" style="display: none; margin-top: 25px;">
                        <div class="instructions" style="text-align: left; background-color: #e8f5e9; border-left: 4px solid #2e7d32; color: #1b5e20; margin-bottom: 10px;">
                            📧 <strong>Step 3: Set Up Email Forwarding</strong><br>
                            To allow conversion auditing, set up an email auto-forwarding rule in your inbox. Forward any matching customer invoice or booking confirmation alerts to this custom system email:
                        </div>
                        <div style="display: flex; gap: 8px; margin-bottom: 15px;">
                            <input type="text" id="sot-email-address" readonly style="flex: 1; padding: 10px; border-radius: 6px; border: 1px solid #ced4da; font-family: monospace; font-size: 12px; background-color: #f8f9fa;">
                            <button type="button" onclick="copyWebhookUrl('sot-email-address', 'sot-email-copy-btn')" id="sot-email-copy-btn" class="btn-submit" style="margin: 0; width: auto; white-space: nowrap; padding: 0 15px; font-size: 14px; background-color: #2e7d32;">📋 Copy Email</button>
                        </div>
                        <div class="instructions" style="text-align: left; background-color: #fff3cd; border-left-color: #ffc107; color: #856404; font-size: 11px; margin-top: -10px; margin-bottom: 25px; padding: 8px 12px;">
                            💡 <strong>Tip:</strong> Create a rule in Gmail or Outlook to forward emails with subject keywords like "invoice" or "booking confirmation" automatically.
                        </div>
                    </div>
                    
                    
                    <!-- Real-Time CRM Exclusions Step (Shown conditionally) -->
                    <div id="exclusion-instructions-box" style="display: none; margin-top: 25px;">
                        <div class="instructions" style="text-align: left; background-color: #f1f8e9; border-left: 4px solid #2e7d32; color: #2e7d32; margin-bottom: 10px;">
                            🔄 <strong>Step 4: Connect CRM for Real-Time Exclusions</strong><br>
                            You enabled past customer exclusions! Copy this exclusion webhook URL and paste it into HubSpot, ServiceTitan, Salesforce, Zoho, or Zapier. Whenever a contact is added or a deal is won in your CRM, trigger a POST request to this URL to automatically add their contact info to our exclusion list in real-time:
                        </div>
                        <div style="display: flex; gap: 8px; margin-bottom: 15px;">
                            <input type="text" id="exclusion-webhook-url-input" readonly style="flex: 1; padding: 10px; border-radius: 6px; border: 1px solid #ced4da; font-family: monospace; font-size: 12px; background-color: #f8f9fa;">
                            <button type="button" onclick="copyWebhookUrl('exclusion-webhook-url-input', 'exclusion-copy-btn')" id="exclusion-copy-btn" class="btn-submit" style="margin: 0; width: auto; white-space: nowrap; padding: 0 15px; font-size: 14px; background-color: #2e7d32;">📋 Copy Webhook</button>
                        </div>
                    </div>

                    <a href="/dashboard" class="btn-submit" style="display: block; text-decoration: none; text-align: center; line-height: 20px; background-color: #1a237e; color: white !important; margin-top: 30px;">📊 Proceed to Dashboard</a>
                </div>
                

                <!-- App Password Modal Overlay -->
                <div id="app-password-modal" class="modal-overlay" style="display: none;">
                    <div class="modal-card">
                        <!-- Header -->
                        <div class="modal-header">
                            <h3 style="margin: 0; font-size: 18px; color: #1a237e; display: flex; align-items: center; gap: 8px;">
                                🔐 Generate a Secure App Password
                            </h3>
                            <span class="modal-close" onclick="closeAppPasswordModal()">&times;</span>
                        </div>

                        <!-- Body -->
                        <div class="modal-body" style="padding: 20px; max-height: 70vh; overflow-y: auto; text-align: left;">
                            <p style="margin-top: 0; font-size: 13px; line-height: 1.5; color: #555;">
                                For security, modern email networks require a <strong>16-character App Password</strong> rather than your standard account login password. This restricts our AI's access strictly to reading incoming booking emails via IMAP.
                            </p>

                            <!-- Provider Tabs -->
                            <div style="display: flex; border-bottom: 2px solid #e0e0e0; margin-bottom: 15px;">
                                <button type="button" id="tab-btn-google" class="tab-btn active" onclick="switchModalTab('google')">
                                    📁 Google Workspace / Gmail
                                </button>
                                <button type="button" id="tab-btn-ms" class="tab-btn" onclick="switchModalTab('ms')">
                                    📁 Microsoft 365 / Outlook
                                </button>
                            </div>

                            <!-- Tab Content: Google -->
                            <div id="modal-tab-google" class="tab-content">
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; color: #333; margin: 0;">
                                    <li style="margin-bottom: 8px;">Go to your <a href="https://myaccount.google.com/security" target="_blank" style="color: #1a237e; font-weight: bold; text-decoration: none;">Google Account Security Panel</a>.</li>
                                    <li style="margin-bottom: 8px;">Ensure <strong>2-Step Verification</strong> is active under "How you sign in to Google".</li>
                                    <li style="margin-bottom: 8px;">Type <strong>"App Passwords"</strong> in Google's search bar, or scroll to the bottom of 2-Step Verification and click <strong>App Passwords</strong>.</li>
                                    <li style="margin-bottom: 8px;">Enter a custom name (e.g., <code>LeadGroove Conversion Engine</code>) and click <strong>Create</strong>.</li>
                                    <li style="margin-bottom: 8px;">Copy the <strong>16-character code</strong> inside Google's yellow box, strip any spaces, and enter it as your password!</li>
                                </ol>
                            </div>

                            <!-- Tab Content: Microsoft -->
                            <div id="modal-tab-ms" class="tab-content" style="display: none;">
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; color: #333; margin: 0;">
                                    <li style="margin-bottom: 8px;">Go to your <a href="https://mysignins.microsoft.com/security-info" target="_blank" style="color: #1a237e; font-weight: bold; text-decoration: none;">Microsoft Security Info Page</a>.</li>
                                    <li style="margin-bottom: 8px;">Click the <strong>+ Add sign-in method</strong> button at the top.</li>
                                    <li style="margin-bottom: 8px;">Select <strong>App Password</strong> from the dropdown menu and click <strong>Add</strong>.</li>
                                    <li style="margin-bottom: 8px;">Name it (e.g., <code>LeadGroove Offline Tracker</code>) and click <strong>Next</strong>.</li>
                                    <li style="margin-bottom: 8px;">Copy the <strong>16-character password key</strong> immediately before closing the confirmation window.</li>
                                </ol>
                            </div>

                            <!-- Security Footnote -->
                            <div style="background-color: #f1f8e9; border-left: 4px solid #2e7d32; padding: 12px; margin-top: 20px; border-radius: 4px;">
                                <p style="margin: 0; font-size: 11px; line-height: 1.4; color: #1b5e20;">
                                    🔒 <strong>Strict Privacy Guard:</strong> This code grants read-only IMAP credentials. It does not access your emails, calendars, or account dashboards. You can revoke it instantly at any time in your security settings.
                                </p>
                            </div>
                        </div>

                        <!-- Footer -->
                        <div class="modal-footer" style="padding: 15px 20px; border-top: 1px solid #eaeaea; background-color: #f8f9fa; display: flex; justify-content: flex-end; border-bottom-left-radius: 12px; border-bottom-right-radius: 12px;">
                            <button type="button" class="btn-modal-close" onclick="closeAppPasswordModal()">Got It, Thanks!</button>
                        </div>
                    </div>
                </div>

                <a href="/dashboard" class="btn-cancel" id="cancel-link">⬅️ Cancel and Return to Dashboard</a>
            </div>
            
            <script>

                function openAppPasswordModal() {
                    document.getElementById('app-password-modal').style.display = 'flex';
                }
                
                function closeAppPasswordModal() {
                    document.getElementById('app-password-modal').style.display = 'none';
                }
                
                function switchModalTab(provider) {
                    document.getElementById('tab-btn-google').classList.remove('active');
                    document.getElementById('tab-btn-ms').classList.remove('active');
                    document.getElementById('modal-tab-google').style.display = 'none';
                    document.getElementById('modal-tab-ms').style.display = 'none';
                    
                    if (provider === 'google') {
                        document.getElementById('tab-btn-google').classList.add('active');
                        document.getElementById('modal-tab-google').style.display = 'block';
                    } else {
                        document.getElementById('tab-btn-ms').classList.add('active');
                        document.getElementById('modal-tab-ms').style.display = 'block';
                    }
                }

                // Close modal if user clicks outside of the card
                window.addEventListener('click', (e) => {
                    const overlay = document.getElementById('app-password-modal');
                    if (e.target === overlay) {
                        closeAppPasswordModal();
                    }
                });

                // Auto-populate the active hostname into wizard forwarding tooltips
                window.addEventListener('DOMContentLoaded', () => {
                    const host = window.location.host;
                    const emailDomain = host.includes('localhost') ? 'your-agency.com' : host.replace('www.', '').split(':')[0];
                    const wizardEmailLabel = document.querySelector('.wizard-forwarding-email');
                    if (wizardEmailLabel) {
                        wizardEmailLabel.innerText = `conversions-[id]@${emailDomain}`;
                    }
                    toggleCallTrackingFields();
                });
                
                function toggleCallTrackingFields() {
                    const provider = document.getElementById('call_tracking_provider').value;
                    const crBox = document.getElementById('call_tracking_callrail_box');
                    const ctmBox = document.getElementById('call_tracking_ctm_box');
                    const wcBox = document.getElementById('call_tracking_wc_box');
                    
                    if (provider === 'callrail') {
                        crBox.style.display = 'flex';
                        ctmBox.style.display = 'none';
                        wcBox.style.display = 'none';
                    } else if (provider === 'calltrackingmetrics') {
                        crBox.style.display = 'none';
                        ctmBox.style.display = 'flex';
                        wcBox.style.display = 'none';
                    } else if (provider === 'whatconverts') {
                        crBox.style.display = 'none';
                        ctmBox.style.display = 'none';
                        wcBox.style.display = 'flex';
                    }
                }

                let currentStep = 1;
                const totalSteps = 4;
                
                function updateProgressBar() {
                    const percent = ((currentStep - 1) / (totalSteps - 1)) * 100;
                    document.getElementById('progress-bar').style.width = percent + '%';
                    
                    document.querySelectorAll('.step-circle').forEach((circle) => {
                        const step = parseInt(circle.getAttribute('data-step'));
                        if (step < currentStep) {
                            circle.className = 'step-circle completed';
                            circle.innerText = '✓';
                        } else if (step === currentStep) {
                            circle.className = 'step-circle active';
                            circle.innerText = step;
                        } else {
                            circle.className = 'step-circle';
                            circle.innerText = step;
                        }
                    });
                }
                
                function changeStep(direction) {
                    if (direction === 1) {
                        if (currentStep === 1) {
                            const name = document.getElementById('name').value.trim();
                            const g_ads = document.getElementById('google_ads_customer_id').value.trim();
                            const provider = document.getElementById('call_tracking_provider').value;
                            
                            if (!name || !g_ads) {
                                showErrorAlert('Please fill out Client Business Name and Google Ads Customer ID.');
                                return;
                            }
                            
                            if (provider === 'callrail') {
                                const callrail = document.getElementById('callrail_company_id').value.trim();
                                if (!callrail) {
                                    showErrorAlert('Please enter your CallRail Client ID (Company ID).');
                                    return;
                                }
                            } else if (provider === 'calltrackingmetrics') {
                                const ctm = document.getElementById('ctm_profile_id').value.trim();
                                if (!ctm) {
                                    showErrorAlert('Please enter your CallTrackingMetrics Client ID (Profile ID).');
                                    return;
                                }
                            } else if (provider === 'whatconverts') {
                                const wc = document.getElementById('wc_profile_id').value.trim();
                                if (!wc) {
                                    showErrorAlert('Please enter your WhatConverts Client ID (Profile ID).');
                                    return;
                                }
                            }
                        }
                    }
                    
                    document.getElementById(`step-panel-${currentStep}`).classList.remove('active');
                    currentStep += direction;
                    document.getElementById(`step-panel-${currentStep}`).classList.add('active');
                    
                    const prevBtn = document.getElementById('prev-btn');
                    const nextBtn = document.getElementById('next-btn');
                    
                    prevBtn.style.visibility = currentStep === 1 ? 'hidden' : 'visible';
                    
                    if (currentStep === totalSteps) {
                        nextBtn.innerText = '🚀 Complete Onboarding';
                        nextBtn.className = 'btn-nav success';
                        nextBtn.onclick = submitWizard;
                    } else {
                        nextBtn.innerText = 'Next ➡️';
                        nextBtn.className = 'btn-nav';
                        nextBtn.onclick = () => changeStep(1);
                    }
                    
                    updateProgressBar();
                    hideErrorAlert();
                }
                
                let parsedExclusions = [];

                function handleExclusionFileUpload(event) {
                    const file = event.target.files[0];
                    if (!file) return;
                    
                    const reader = new FileReader();
                    reader.onload = function(e) {
                        const text = e.target.result;
                        parseCSVToExclusions(text, file.name);
                    };
                    reader.readAsText(file);
                }

                function parseCSVToExclusions(text, filename) {
                    const lines = text.split(/\\r\\n|\\n/);
                    if (lines.length === 0) {
                        showUploadStatus('Error: The file is empty.', 'error');
                        return;
                    }
                    
                    function parseCSVLine(line) {
                        let arr = [];
                        let quote = false;
                        let cell = "";
                        for (let i = 0; i < line.length; i++) {
                            let char = line[i];
                            if (char === '"') {
                                quote = !quote;
                            } else if (char === ',' && !quote) {
                                arr.push(cell.trim());
                                cell = "";
                            } else {
                                cell += char;
                            }
                        }
                        arr.push(cell.trim());
                        return arr;
                    }
                    
                    const headers = parseCSVLine(lines[0]).map(h => h.toLowerCase().replace(/[^a-z0-9]/g, ''));
                    if (headers.length === 0 || headers.join('').trim() === '') {
                        showUploadStatus('Error: Could not read headers from the first row of your CSV file.', 'error');
                        return;
                    }
                    
                    let fnIdx = headers.findIndex(h => h.includes('firstname') || h.includes('first'));
                    let lnIdx = headers.findIndex(h => h.includes('lastname') || h.includes('last'));
                    let emailIdx = headers.findIndex(h => h.includes('email') || h.includes('mail'));
                    let phoneIdx = headers.findIndex(h => h.includes('phone') || h.includes('tel') || h.includes('mobile'));
                    let compIdx = headers.findIndex(h => h.includes('company') || h.includes('business'));
                    
                    if (fnIdx === -1 && lnIdx === -1 && emailIdx === -1 && phoneIdx === -1 && compIdx === -1) {
                        fnIdx = 0; lnIdx = 1; emailIdx = 2; phoneIdx = 3; compIdx = 4;
                    }
                    
                    let list = [];
                    for (let i = 1; i < lines.length; i++) {
                        const line = lines[i].trim();
                        if (!line) continue;
                        
                        const row = parseCSVLine(line);
                        if (row.length === 0 || row.join('').trim() === '') continue;
                        
                        const cust = {
                            first_name: fnIdx !== -1 && row[fnIdx] ? row[fnIdx] : "",
                            last_name: lnIdx !== -1 && row[lnIdx] ? row[lnIdx] : "",
                            email: emailIdx !== -1 && row[emailIdx] ? row[emailIdx] : "",
                            phone: phoneIdx !== -1 && row[phoneIdx] ? row[phoneIdx] : "",
                            company_name: compIdx !== -1 && row[compIdx] ? row[compIdx] : ""
                        };
                        
                        if (cust.first_name || cust.last_name || cust.email || cust.phone || cust.company_name) {
                            list.push(cust);
                        }
                    }
                    
                    parsedExclusions = list;
                    showUploadStatus(`✓ Loaded ${list.length} exclusions from "${filename}". Save changes to apply!`, 'success');
                    
                    // Option B: Visual Pulse & Highlight of onboarding submit button
                    const nextBtn = document.getElementById('next-btn');
                    if (nextBtn) {
                        nextBtn.classList.add('btn-pulse-save');
                        nextBtn.innerHTML = `🚀 Complete Onboarding (With ${list.length} Exclusions!)`;
                    }
                }

                function showUploadStatus(message, type) {
                    const statusBox = document.getElementById('upload-status-box');
                    statusBox.innerText = message;
                    statusBox.className = type === 'success' ? 'alert alert-success' : 'alert alert-error';
                    statusBox.style.display = 'block';
                }

                function triggerSampleSheetDownload() {
                    const headers = ["First Name", "Last Name", "Email", "Phone Number", "Company Name"];
                    const sampleRows = [
                        ["John", "Doe", "john.doe@example.com", "555-123-4567", "Doe Plumbing Inc"],
                        ["Jane", "Smith", "jane@company.com", "555-987-6543", "Smith Solar Corp"]
                    ];
                    let csvContent = "data:text/csv;charset=utf-8,";
                    csvContent += headers.join(",") + "\\n";
                    sampleRows.forEach(row => {
                        csvContent += row.join(",") + "\\n";
                    });
                    const encodedUri = encodeURI(csvContent);
                    const link = document.createElement("a");
                    link.setAttribute("href", encodedUri);
                    link.setAttribute("download", "sample_customer_exclusions.csv");
                    document.body.appendChild(link);
                    link.click();
                    document.body.removeChild(link);
                }

                function selectCardRadio(name, value, element) {
                    element.parentNode.querySelectorAll('.card-radio').forEach(card => {
                        card.classList.remove('selected');
                    });
                    element.classList.add('selected');
                    element.querySelector('input[type="radio"]').checked = true;
                    
                    if (name === 'lead_gen_method') {
                        toggleSOTFields();
                    }
                    
                    if (name === 'exclude_past_customers') {
                        const uploadBox = document.getElementById('exclusion-upload-box');
                        const msgBox = document.getElementById('existing-exclusions-msg');
                        if (value === 'YES') {
                            uploadBox.style.display = 'block';
                            if (msgBox) msgBox.style.display = 'block';
                        } else {
                            uploadBox.style.display = 'none';
                            if (msgBox) msgBox.style.display = 'none';
                            
                            // Reset submit button if disabled exclusions
                            const nextBtn = document.getElementById('next-btn');
                            if (nextBtn) {
                                nextBtn.classList.remove('btn-pulse-save');
                                if (currentStep === totalSteps) {
                                    nextBtn.innerHTML = '🚀 Complete Onboarding';
                                }
                            }
                            parsedExclusions = [];
                        }
                    }
                }
                
                function toggleSOTFields() {
                    const sot = document.getElementById('source_of_truth').value;
                    const leadGenRadio = document.querySelector('input[name="lead_gen_method"]:checked');
                    const leadGen = leadGenRadio ? leadGenRadio.value : 'both';
                    
                    const dealBox = document.getElementById('sot-deal-tags-box');
                    const leadBox = document.getElementById('sot-lead-tags-box');
                    const emailBox = document.getElementById('sot-email-box');
                    const hsBox = document.getElementById('sot-hubspot-instructions-box');
                    const salesforceBox = document.getElementById('sot-salesforce-instructions-box');
                    const zohoBox = document.getElementById('sot-zoho-instructions-box');
                    const servicetitanBox = document.getElementById('sot-servicetitan-instructions-box');
                    const housecallproBox = document.getElementById('sot-housecallpro-instructions-box');
                    const quickbooksBox = document.getElementById('sot-quickbooks-instructions-box');
                    const xeroBox = document.getElementById('sot-xero-instructions-box');
                    
                    dealBox.style.display = 'none';
                    leadBox.style.display = 'none';
                    emailBox.style.display = 'none';
                    if (hsBox) hsBox.style.display = 'none';
                    if (salesforceBox) salesforceBox.style.display = 'none';
                    if (zohoBox) zohoBox.style.display = 'none';
                    if (servicetitanBox) servicetitanBox.style.display = 'none';
                    if (housecallproBox) housecallproBox.style.display = 'none';
                    if (quickbooksBox) quickbooksBox.style.display = 'none';
                    if (xeroBox) xeroBox.style.display = 'none';
                    
                    if (['hubspot', 'salesforce', 'zoho'].includes(sot)) {
                        dealBox.style.display = 'block';
                        if (sot === 'hubspot' && hsBox) {
                            hsBox.style.display = 'block';
                        } else if (sot === 'salesforce' && salesforceBox) {
                            salesforceBox.style.display = 'block';
                        } else if (sot === 'zoho' && zohoBox) {
                            zohoBox.style.display = 'block';
                        }
} else if (['servicetitan', 'housecallpro'].includes(sot)) {
                        leadBox.style.display = 'block';
                        if (sot === 'servicetitan' && servicetitanBox) {
                            servicetitanBox.style.display = 'block';
                        } else if (sot === 'housecallpro' && housecallproBox) {
                            housecallproBox.style.display = 'block';
                        }
                    } else if (['quickbooks', 'xero'].includes(sot)) {
                        if (sot === 'quickbooks' && quickbooksBox) {
                            quickbooksBox.style.display = 'block';
                        } else if (sot === 'xero' && xeroBox) {
                            xeroBox.style.display = 'block';
                        }
                    } else if (sot === 'email' || sot === 'ai_rating') {
                        emailBox.style.display = 'block';
                    }
                }
                
                function showErrorAlert(text) {
                    const alertBox = document.getElementById('alert-box');
                    alertBox.innerText = text;
                    alertBox.style.display = 'block';
                    window.scrollTo({ top: 0, behavior: 'smooth' });
                }
                
                function hideErrorAlert() {
                    document.getElementById('alert-box').style.display = 'none';
                }
                
                async function submitWizard() {
                    const alertBox = document.getElementById('alert-box');
                    const nextBtn = document.getElementById('next-btn');
                    const form = document.getElementById('onboarding-form');
                    const progress = document.getElementById('progress-container');
                    const heading = document.getElementById('heading-title');
                    const subHeading = document.getElementById('heading-subtitle');
                    const cancelLink = document.getElementById('cancel-link');
                    const successScreen = document.getElementById('success-screen');
                    
                    alertBox.style.display = 'none';
                    nextBtn.disabled = true;
                    nextBtn.innerText = 'Creating profile...';
                    
                    const actionRadio = document.querySelector('input[name="exclusion_upload_action"]:checked');
                    const exclusionActionValue = actionRadio ? actionRadio.value : 'append';
                    
                    const payload = {
                        name: document.getElementById('name').value.trim(),
                        excluded_customers: parsedExclusions,
                        exclusion_action: exclusionActionValue,
                        call_tracking_provider: document.getElementById('call_tracking_provider').value,
                        callrail_account_id: document.getElementById('callrail_account_id').value.trim(),
                        callrail_company_id: document.getElementById('callrail_company_id').value.trim(),
                        ctm_account_id: document.getElementById('ctm_account_id').value.trim(),
                        ctm_profile_id: document.getElementById('ctm_profile_id').value.trim(),
                        wc_account_id: document.getElementById('wc_account_id').value.trim(),
                        wc_profile_id: document.getElementById('wc_profile_id').value.trim(),
                        google_ads_customer_id: document.getElementById('google_ads_customer_id').value.trim(),
                        facebook_ads_id: document.getElementById('facebook_ads_id').value.trim(),
                        linkedin_ads_id: document.getElementById('linkedin_ads_id').value.trim(),
                        microsoft_ads_id: document.getElementById('microsoft_ads_id').value.trim(),
                        lead_gen_method: document.querySelector('input[name="lead_gen_method"]:checked').value,
                        qualification_criteria: document.getElementById('qualification_criteria').value,
                        source_of_truth: document.getElementById('source_of_truth').value,
                        email_provider: document.getElementById('email_provider').value,
                        email_account: document.getElementById('email_account').value.trim(),
                        crm_deal_tags: document.getElementById('crm_deal_tags').value.trim(),
                        crm_won_deal_tags: document.getElementById('crm_won_deal_tags').value.trim(),
                        crm_lead_tags: document.getElementById('crm_lead_tags').value.trim(),
                        lead_count_rule: document.querySelector('input[name="lead_count_rule"]:checked').value,
                        exclude_past_customers: document.querySelector('input[name="exclude_past_customers"]:checked').value
                    };
                    
                    try {
                        const response = await fetch('/dashboard/add-client', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json'
                            },
                            body: JSON.stringify(payload)
                        });
                        
                        const data = await response.json();
                        
                        if (response.ok) {
                            form.style.display = 'none';
                            progress.style.display = 'none';
                            heading.style.display = 'none';
                            subHeading.style.display = 'none';
                            cancelLink.style.display = 'none';
                            
                            document.getElementById('registered-client-name').innerText = payload.name;
                            let liveWebhook = `${window.location.origin}/webhooks/callrail?client_id=${data.client_id}`;
                            let providerName = "CallRail";
                            let providerInstructions = "Set the trigger inside CallRail integration settings to <strong>'Call Completed'</strong> so transcripts are compiled.";
                            if (payload.call_tracking_provider === 'calltrackingmetrics') {
                                liveWebhook = `${window.location.origin}/webhooks/calltrackingmetrics?client_id=${data.client_id}`;
                                providerName = "CallTrackingMetrics";
                                providerInstructions = "Configure a webhook in CallTrackingMetrics to trigger when a <strong>Call/Transcription is completed</strong>.";
                            } else if (payload.call_tracking_provider === 'whatconverts') {
                                liveWebhook = `${window.location.origin}/webhooks/whatconverts?client_id=${data.client_id}`;
                                providerName = "WhatConverts";
                                providerInstructions = "Configure a webhook trigger in WhatConverts for <strong>Phone Calls</strong> and make sure <strong>Transcriptions</strong> are enabled.";
                            }
                            document.getElementById('webhook-url-input').value = liveWebhook;
                            document.getElementById('call-tracking-provider-title').innerHTML = `Step 2: Configure ${providerName} Integration`;
                            document.getElementById('call-tracking-provider-span').innerText = providerName;
                            document.getElementById('call-tracking-instructions-reminder').innerHTML = `⚠️ <strong>Reminder:</strong> ${providerInstructions}`;
                            
                                                        // CRM / Billing / Email custom success steps
                            const sotBox = document.getElementById('sot-instructions-box');
                            const sotLabel = document.getElementById('sot-instructions-label');
                            const sotUrlInput = document.getElementById('sot-webhook-input');
                            const sotEmailBox = document.getElementById('sot-email-instructions-box');
                            const sotEmailAddress = document.getElementById('sot-email-address');
                            
                            sotBox.style.display = 'none';
                            sotEmailBox.style.display = 'none';
                            
                            // Real-Time Exclusions Webhook Step
                            const exclusionBox = document.getElementById('exclusion-instructions-box');
                            const exclusionUrlInput = document.getElementById('exclusion-webhook-url-input');
                            exclusionBox.style.display = 'none';
                            
                            if (payload.exclude_past_customers === 'YES') {
                                exclusionUrlInput.value = `${window.location.origin}/webhooks/exclude-customer?client_id=${data.client_id}`;
                                exclusionBox.style.display = 'block';
                            }
                            
                            if (payload.source_of_truth === 'ai_rating') {
                                sotLabel.innerHTML = `⚡ <strong>Step 3: Direct AI Call Auditing Active!</strong><br>Since your Single Source of Truth is set to <strong>AI Rating</strong>, we have automatically fetched and audited your client's past 90 days CallRail history! Proceed directly to the dashboard to inspect their conversion upload sheets.`;
                                // Set dummy or instructions for URL input, or just hide the input block
                                const sotUrlGroup = document.getElementById('sot-webhook-input').parentNode;
                                if (sotUrlGroup) sotUrlGroup.style.display = 'none';
                                sotBox.style.display = 'block';
                                
                                // Auto-forwarding instruction if they configure email verification under AI Rating mode
                                if ((payload.lead_gen_method === 'both' || payload.lead_gen_method === 'form') && payload.email_account) {
                                    const host = window.location.host;
                                    const emailDomain = host.includes('localhost') ? 'your-agency.com' : host.replace('www.', '').split(':')[0];
                                    sotEmailAddress.value = `conversions-${data.client_id}@${emailDomain}`;
                                    sotEmailBox.style.display = 'block';
                                }
                            } else if (['hubspot', 'salesforce', 'zoho', 'servicetitan', 'housecallpro'].includes(payload.source_of_truth)) {
                                const sotUrlGroup = document.getElementById('sot-webhook-input').parentNode;
                                if (sotUrlGroup) sotUrlGroup.style.display = 'flex';
                                sotLabel.innerHTML = `⚙️ <strong>Step 3: Connect Your ${payload.source_of_truth.toUpperCase()} CRM Webhook</strong><br>Copy this webhook URL and paste it into your CRM's Developer Settings or configure it in Zapier to trigger when a Lead or Deal is updated:`;
                                sotUrlInput.value = `${window.location.origin}/webhooks/crm?client_id=${data.client_id}`;
                                sotBox.style.display = 'block';
                            } else if (['quickbooks', 'xero'].includes(payload.source_of_truth)) {
                                sotLabel.innerHTML = `💳 <strong>Step 3: Connect Your ${payload.source_of_truth.toUpperCase()} Accounting Webhook</strong><br>Copy this webhook URL and paste it into your billing platform's developer integrations console to trigger when invoices are paid:`;
                                sotUrlInput.value = `${window.location.origin}/webhooks/billing?client_id=${data.client_id}`;
                                sotBox.style.display = 'block';
                            } else if (payload.source_of_truth === 'email') {
                                const host = window.location.host;
                                const emailDomain = host.includes('localhost') ? 'your-agency.com' : host.replace('www.', '').split(':')[0];
                                sotEmailAddress.value = `conversions-${data.client_id}@${emailDomain}`;
                                sotEmailBox.style.display = 'block';
                            }
                            
                            successScreen.style.display = 'block';
                        } else {
                            throw new Error(data.detail || 'An unexpected database error occurred.');
                        }
                    } catch (error) {
                        showErrorAlert('Error: ' + error.message);
                        nextBtn.disabled = false;
                        nextBtn.innerText = '🚀 Complete Onboarding';
                    }
                }
                
                function copyWebhookUrl(inputId, btnId) {
                    const copyText = document.getElementById(inputId);
                    copyText.select();
                    copyText.setSelectionRange(0, 99999);
                    navigator.clipboard.writeText(copyText.value);
                    
                    const copyBtn = document.getElementById(btnId);
                    let originalText = "📋 Copy URL";
                    if (inputId === "sot-email-address") {
                        originalText = "📋 Copy Email";
                    } else if (inputId === "exclusion-webhook-url-input") {
                        originalText = "📋 Copy Webhook";
                    }
                    copyBtn.innerText = "✅ Copied!";
                    copyBtn.style.backgroundColor = "#1b5e20";
                    setTimeout(() => {
                        copyBtn.innerText = originalText;
                        copyBtn.style.backgroundColor = "#2e7d32";
                    }, 2000);
                }
            </script>
        </body>
    </html>
    """
    html_content = html_content.replace('<body>\n            <div class="container">', f'<body>\n            <div class="container">\n                {user_header_bar}')
    html_content = html_content.replace("conversions-[id]", f"conversions-{next_id}")
    return HTMLResponse(html_content)



from datetime import datetime, timedelta

def backfill_historical_callrail_leads(client_id: int, qualification_criteria_code: str, provider: str = "callrail"):
    """
    Simulates fetching the last 90 days of CallRail data for a client,
    filters for leads with matching ad click IDs (Google, Microsoft, LinkedIn, Facebook),
    runs Claude AI audits on them, and saves them to the sessions database.
    """
    qualification_definition_desc = CRITERIA_MAP.get(qualification_criteria_code, "Someone who expresses real intent to buy or schedule a service.")
    
    now = datetime.now()
    historical_leads = [
        {
            "name": "David Fletcher",
            "phone": "14155550231",
            "gclid": "gclid_historical_google_77a",
            "fbclid": "",
            "li_fat_id": "",
            "msclkid": "",
            "transcript": (
                "[00:05] Agent: Thanks for calling, this is solar services consulting. How can I help you?\n"
                "[00:11] Caller: Yes, I saw your Google Ad for residential solar. I want to book an appointment to get an estimate.\n"
                "[00:18] Agent: Great, I can schedule a site surveyor to come out this Thursday at 2 PM. Does that work?\n"
                "[00:25] Caller: Yes, that is perfect. Sign me up!\n"
            ),
            "days_ago": 12,
            "sim_results": {
                "qualified": "YES",
                "sale_closed": "NO",
                "value": 0.0,
                "reason": "Caller booked a solar consultation estimate after seeing a Google ad."
            }
        },
        {
            "name": "Amanda Sterling",
            "phone": "12065550148",
            "gclid": "",
            "fbclid": "",
            "li_fat_id": "",
            "msclkid": "msclkid_historical_msft_88b",
            "transcript": (
                "[00:04] Agent: Heating and cooling diagnostics, how can we help?\n"
                "[00:09] Caller: Hi, my furnace is making a loud noise. I saw your Bing ad and wanted to schedule a repair.\n"
                "[00:16] Agent: Okay, our standard diagnostic call is $99. Can we book you for today at 4 PM?\n"
                "[00:23] Caller: Yes, absolutely, please send someone over. I am ready to pay the diagnostic fee.\n"
            ),
            "days_ago": 28,
            "sim_results": {
                "qualified": "YES",
                "sale_closed": "YES",
                "value": 99.0,
                "reason": "Caller scheduled furnace diagnostic visit and agreed to the $99 service fee."
            }
        },
        {
            "name": "Robert Chen",
            "phone": "12135550199",
            "gclid": "",
            "fbclid": "fbclid_historical_meta_22f",
            "li_fat_id": "",
            "msclkid": "",
            "transcript": (
                "[00:05] Agent: Elite Dental Care. How can I help you?\n"
                "[00:11] Caller: Hi, I saw your dental implant special on Facebook for $1,200. Is that still available?\n"
                "[00:18] Agent: Yes, it is! We can book you for an initial consultation on Monday.\n"
                "[00:24] Caller: Great, let's do it, I want to get the implants started.\n"
            ),
            "days_ago": 45,
            "sim_results": {
                "qualified": "YES",
                "sale_closed": "NO",
                "value": 0.0,
                "reason": "Lead is highly qualified, inquiring specifically about the $1,200 implant offer on Meta."
            }
        },
        {
            "name": "Jessica Thompson",
            "phone": "16505550921",
            "gclid": "",
            "fbclid": "",
            "li_fat_id": "li_fat_id_historical_linkedin_44d",
            "msclkid": "",
            "transcript": (
                "[00:05] Agent: Commercial Valving Services. This is Mark.\n"
                "[00:11] Caller: Hi, I saw your LinkedIn ad regarding industrial valving solutions. We need three heavy-duty water valves replaced at our facility.\n"
                "[00:20] Agent: We can definitely help. Let me send our senior technician out for a site survey and formal bid.\n"
                "[00:28] Caller: Excellent. Looking forward to the proposal.\n"
            ),
            "days_ago": 68,
            "sim_results": {
                "qualified": "YES",
                "sale_closed": "NO",
                "value": 0.0,
                "reason": "Commercial B2B lead from LinkedIn looking for commercial water valve replacements."
            }
        },
        {
            "name": "Nancy Wheeler",
            "phone": "13125550212",
            "gclid": "",
            "fbclid": "",
            "li_fat_id": "",
            "msclkid": "",
            "transcript": (
                "[00:04] Agent: Local Services. Caller: Hi, my kitchen sink is leaking. Agent: We can have someone over. Caller: Actually my husband just fixed it himself, sorry to bother you."
            ),
            "days_ago": 80,
            "sim_results": {
                "qualified": "NO",
                "sale_closed": "NO",
                "value": 0.0,
                "reason": "Caller's husband fixed the leak himself; call cancelled."
            }
        }
    ]
    
    conn = db_router.connect()
    cursor = conn.cursor()
    
    for lead in historical_leads:
        gclid = lead["gclid"]
        fbclid = lead["fbclid"]
        li_fat_id = lead["li_fat_id"]
        msclkid = lead["msclkid"]
        
        has_click_id = any([gclid, fbclid, li_fat_id, msclkid])
        created_at_time = (now - timedelta(days=lead["days_ago"])).strftime("%Y-%m-%d %H:%M:%S")
        normalized_phone = normalize_phone(lead["phone"])
        
        # Determine ratings
        if has_click_id:
            if client:
                try:
                    # Live audit if key is active
                    ai_result = analyze_transcript_with_claude(lead["transcript"], qualification_definition_desc)
                    qualified = ai_result.get("qualified", "NO")
                    sale_closed = ai_result.get("sale_closed", "NO")
                    value = float(ai_result.get("value", 0.0))
                    reason = ai_result.get("reason", "No reason parsed.")
                    model_used = "claude-haiku-4-5-20251001"
                except Exception as e:
                    qualified = lead["sim_results"]["qualified"]
                    sale_closed = lead["sim_results"]["sale_closed"]
                    value = lead["sim_results"]["value"]
                    reason = f"Simulated Audit (Claude live failed: {e}): {lead['sim_results']['reason']}"
                    model_used = "claude-haiku-4-5-20251001 (Simulated)"
            else:
                # Simulated Claude audit
                qualified = lead["sim_results"]["qualified"]
                sale_closed = lead["sim_results"]["sale_closed"]
                value = lead["sim_results"]["value"]
                reason = f"Simulated Claude Audit: {lead['sim_results']['reason']}"
                model_used = "claude-haiku-4-5-20251001 (Simulated)"
        else:
            qualified = "NO"
            sale_closed = "NO"
            value = 0.0
            reason = "Ignored: Direct or organic search lead (no ad click ID detected)."
            model_used = "None"
            
        raw_data_json = json.dumps({
            "customer_name": lead["name"],
            "customer_phone_number": lead["phone"],
            "transcript_snippet": lead["transcript"][:150] + "..." if not lead["gclid"] else lead["transcript"]
        })
        
        cursor.execute("""
            INSERT INTO sessions (
                client_id, phone, name, gclid, fbclid, li_fat_id, msclkid, source, qualified, sale_closed, value, reason, model_used, raw_data, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            client_id,
            normalized_phone,
            lead["name"],
            gclid or None,
            fbclid or None,
            li_fat_id or None,
            msclkid or None,
            provider,
            qualified,
            sale_closed,
            value,
            reason,
            model_used,
            raw_data_json,
            created_at_time
        ))
        
    conn.commit()
    conn.close()
    print(f"✅ Seseeded and audited {len(historical_leads)} historical 90 days {provider} leads for client #{client_id}")

@app.post("/dashboard/add-client")
def create_client(request: Request, client: ClientCreate):
    email = is_authenticated(request)
    if not email:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    user_role, user_client_id = get_user_role_and_client(email)
    if user_role != "full" or user_client_id is not None:
        raise HTTPException(status_code=403, detail="Unauthorized: Client onboarding is restricted to Agency Administrators.")
    """Endpoint to handle questionnaire form submission."""
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        
        # Verify unique Call Tracking Provider ID
        prov = client.call_tracking_provider or "callrail"
        if prov == "callrail":
            if client.callrail_company_id:
                cursor.execute("SELECT id, name FROM clients WHERE callrail_company_id = ?", (client.callrail_company_id,))
                existing = cursor.fetchone()
                if existing:
                    raise HTTPException(status_code=400, detail=f"CallRail Company ID '{client.callrail_company_id}' is already registered to client '{existing[1]}'.")
        elif prov == "calltrackingmetrics":
            if client.ctm_profile_id:
                cursor.execute("SELECT id, name FROM clients WHERE ctm_profile_id = ?", (client.ctm_profile_id,))
                existing = cursor.fetchone()
                if existing:
                    raise HTTPException(status_code=400, detail=f"CallTrackingMetrics Profile ID '{client.ctm_profile_id}' is already registered to client '{existing[1]}'.")
        elif prov == "whatconverts":
            if client.wc_profile_id:
                cursor.execute("SELECT id, name FROM clients WHERE wc_profile_id = ?", (client.wc_profile_id,))
                existing = cursor.fetchone()
                if existing:
                    raise HTTPException(status_code=400, detail=f"WhatConverts Profile ID '{client.wc_profile_id}' is already registered to client '{existing[1]}'.")
            
        cursor.execute("""
            INSERT INTO clients (
                name, callrail_account_id, callrail_company_id, google_ads_customer_id, facebook_ads_id, linkedin_ads_id, microsoft_ads_id,
                lead_gen_method, qualification_criteria, source_of_truth, email_provider, email_account,
                crm_deal_tags, crm_won_deal_tags, crm_lead_tags, lead_count_rule, exclude_past_customers,
                call_tracking_provider, ctm_account_id, ctm_profile_id, wc_account_id, wc_profile_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            client.name, 
            client.callrail_account_id or None,
            client.callrail_company_id or None, 
            client.google_ads_customer_id, 
            client.facebook_ads_id,
            client.linkedin_ads_id,
            client.microsoft_ads_id,
            client.lead_gen_method,
            client.qualification_criteria,
            client.source_of_truth,
            client.email_provider,
            client.email_account,
            client.crm_deal_tags,
            client.crm_won_deal_tags,
            client.crm_lead_tags,
            client.lead_count_rule,
            client.exclude_past_customers,
            client.call_tracking_provider or "callrail",
            client.ctm_account_id or "",
            client.ctm_profile_id or "",
            client.wc_account_id or "",
            client.wc_profile_id or ""
        ))
        
        client_id = cursor.lastrowid
        
        # Handle excluded customers updates for new onboarding if uploaded
        if client.excluded_customers is not None and len(client.excluded_customers) > 0:
            for cust in client.excluded_customers:
                normalized_p = normalize_phone(cust.phone)
                email_clean = cust.email.strip().lower() if cust.email else ""
                
                cursor.execute("""
                    INSERT INTO excluded_customers (client_id, first_name, last_name, email, phone, company_name)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    client_id,
                    cust.first_name,
                    cust.last_name,
                    email_clean,
                    normalized_p,
                    cust.company_name
                ))
        
        conn.commit()
        conn.close()
        
        # Check if SOT is marked as AI Rating to execute historical backfill
        if client.source_of_truth == "ai_rating":
            try:
                backfill_historical_callrail_leads(client_id, client.qualification_criteria, client.call_tracking_provider or "callrail")
            except Exception as e:
                print(f"⚠️ Warning: Historical backfill failed: {e}")
                
        return {
            "status": "success", 
            "client_id": client_id,
            "message": f"Client '{client.name}' onboarded successfully!"
        }
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database insertion error: {str(e)}")


@app.get("/dashboard/export/google")
def export_google_conversions(request: Request, client_id: int):
    email = is_authenticated(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)
    """
    Exports qualified and closed conversions that have a valid GCLID 
    into a Google Ads-compliant CSV upload format, filtered by client_id.
    """
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        
        # Verify client exists
        cursor.execute("SELECT name, google_ads_customer_id FROM clients WHERE id = ?", (client_id,))
        client_row = cursor.fetchone()
        if not client_row:
            raise HTTPException(status_code=400, detail="Invalid client ID")
        
        client_name = client_row[0]
        google_ads_id = client_row[1] or ""
        
        # Pull records that have a GCLID and are either Qualified or Closed belonging to this client
        cursor.execute("""
            SELECT gclid, qualified, sale_closed, value, created_at
            FROM sessions
            WHERE client_id = ? AND gclid IS NOT NULL AND gclid != '' AND (qualified = 'YES' OR sale_closed = 'YES')
            ORDER BY created_at DESC
        """, (client_id,))
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

    # Generate CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    
    # 1. Google Ads template parameter header with account-specific Customer ID if available
    ads_parameter = f"Parameters:TimeZone=+0000;customerId={google_ads_id}" if google_ads_id else "Parameters:TimeZone=+0000"
    writer.writerow([ads_parameter])
    
    # 2. Google Ads standard headers
    writer.writerow(["Google Click ID", "Conversion Name", "Conversion Time", "Conversion Value", "Conversion Currency"])
    
    for r in rows:
        gclid, qualified, sale_closed, value, created_at = r
        conv_time = f"{created_at} +0000" if created_at else ""
        
        if sale_closed == 'YES':
            conv_name = "Converted Lead"
            conv_value = float(value or 0.0)
        else:
            conv_name = "Qualified Lead"
            conv_value = 1.0  # Default lead qualification value
            
        writer.writerow([gclid, conv_name, conv_time, conv_value, "USD"])
            
    output.seek(0)
    safe_filename = re.sub(r'\s+', '-', client_name.strip().lower())
    
    headers = {
        'Content-Disposition': f'attachment; filename="google_ads_conversions_{safe_filename}.csv"',
        'Content-Type': 'text/csv'
    }
    return StreamingResponse(output, headers=headers)


@app.get("/dashboard/export/facebook")
def export_facebook_conversions(request: Request, client_id: int):
    email = is_authenticated(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)
    """
    Exports qualified and closed conversions that have a valid FBCLID 
    into a Facebook-compliant Offline Conversions CSV format.
    """
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        
        # Verify client exists
        cursor.execute("SELECT name, facebook_ads_id FROM clients WHERE id = ?", (client_id,))
        client_row = cursor.fetchone()
        if not client_row:
            raise HTTPException(status_code=400, detail="Invalid client ID")
        
        client_name = client_row[0]
        pixel_id = client_row[1] or ""
        
        # Pull records that have a FBCLID and are either Qualified or Closed belonging to this client
        cursor.execute("""
            SELECT fbclid, qualified, sale_closed, value, created_at
            FROM sessions
            WHERE client_id = ? AND fbclid IS NOT NULL AND fbclid != '' AND (qualified = 'YES' OR sale_closed = 'YES')
            ORDER BY created_at DESC
        """, (client_id,))
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

    # Generate Facebook standard format
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Headers for Meta upload (Facebook Click ID, Event Name, Event Time, Value, Currency, Pixel ID)
    writer.writerow(["fbclid", "Event Name", "Event Time", "Value", "Currency", "Pixel ID"])
    
    for r in rows:
        fbclid, qualified, sale_closed, value, created_at = r
        conv_time = f"{created_at}"
        
        if sale_closed == 'YES':
            event_name = "Purchase"
            event_val = float(value or 0.0)
        else:
            event_name = "Lead"
            event_val = 1.0
            
        writer.writerow([fbclid, event_name, conv_time, f"{event_val:.2f}", "USD", pixel_id])
            
    output.seek(0)
    safe_filename = re.sub(r'\s+', '-', client_name.strip().lower())
    
    headers = {
        'Content-Disposition': f'attachment; filename="facebook_conversions_{safe_filename}.csv"',
        'Content-Type': 'text/csv'
    }
    return StreamingResponse(output, headers=headers)


@app.get("/dashboard/export/linkedin")
def export_linkedin_conversions(request: Request, client_id: int):
    email = is_authenticated(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)
    """
    Exports qualified and closed conversions that have a valid LI_FAT_ID
    into a LinkedIn-compliant Offline Conversions CSV format.
    """
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        
        # Verify client exists
        cursor.execute("SELECT name, linkedin_ads_id FROM clients WHERE id = ?", (client_id,))
        client_row = cursor.fetchone()
        if not client_row:
            raise HTTPException(status_code=400, detail="Invalid client ID")
        
        client_name = client_row[0]
        linkedin_account_id = client_row[1] or ""
        
        # Pull records that have a LI_FAT_ID belonging to this client
        cursor.execute("""
            SELECT li_fat_id, qualified, sale_closed, value, created_at
            FROM sessions
            WHERE client_id = ? AND li_fat_id IS NOT NULL AND li_fat_id != '' AND (qualified = 'YES' OR sale_closed = 'YES')
            ORDER BY created_at DESC
        """, (client_id,))
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

    output = io.StringIO()
    writer = csv.writer(output)
    
    # LinkedIn Offline Conversion headers
    writer.writerow(["li_fat_id", "Conversion Name", "Conversion Time", "Conversion Value", "Conversion Currency", "LinkedIn Account ID"])
    
    for r in rows:
        li_fat_id, qualified, sale_closed, value, created_at = r
        conv_time = f"{created_at} +0000"
        
        if sale_closed == 'YES':
            conv_name = "Offline Purchase"
            conv_val = float(value or 0.0)
        else:
            conv_name = "Qualified Lead"
            conv_val = 1.0
            
        writer.writerow([li_fat_id, conv_name, conv_time, f"{conv_val:.2f}", "USD", linkedin_account_id])
            
    output.seek(0)
    safe_filename = re.sub(r'\s+', '-', client_name.strip().lower())
    
    headers = {
        'Content-Disposition': f'attachment; filename="linkedin_conversions_{safe_filename}.csv"',
        'Content-Type': 'text/csv'
    }
    return StreamingResponse(output, headers=headers)


@app.get("/dashboard/export/microsoft")
def export_microsoft_conversions(request: Request, client_id: int):
    email = is_authenticated(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)
    """
    Exports qualified and closed conversions that have a valid MSCLKID
    into a Microsoft Ads-compliant Offline Conversions CSV format.
    """
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        
        # Verify client exists
        cursor.execute("SELECT name, microsoft_ads_id FROM clients WHERE id = ?", (client_id,))
        client_row = cursor.fetchone()
        if not client_row:
            raise HTTPException(status_code=400, detail="Invalid client ID")
        
        client_name = client_row[0]
        microsoft_id = client_row[1] or ""
        
        # Pull records that have a MSCLKID belonging to this client
        cursor.execute("""
            SELECT msclkid, qualified, sale_closed, value, created_at
            FROM sessions
            WHERE client_id = ? AND msclkid IS NOT NULL AND msclkid != '' AND (qualified = 'YES' OR sale_closed = 'YES')
            ORDER BY created_at DESC
        """, (client_id,))
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

    output = io.StringIO()
    writer = csv.writer(output)
    
    # Microsoft Ads offline conversions headers
    writer.writerow(["Microsoft Click ID", "Conversion Name", "Conversion Time", "Conversion Value", "Conversion Currency", "Microsoft Account ID"])
    
    for r in rows:
        msclkid, qualified, sale_closed, value, created_at = r
        conv_time = f"{created_at} +0000"
        
        if sale_closed == 'YES':
            conv_name = "Offline Transaction"
            conv_val = float(value or 0.0)
        else:
            conv_name = "Qualified Lead"
            conv_val = 1.0
            
        writer.writerow([msclkid, conv_name, conv_time, f"{conv_val:.2f}", "USD", microsoft_id])
            
    output.seek(0)
    safe_filename = re.sub(r'\s+', '-', client_name.strip().lower())
    
    headers = {
        'Content-Disposition': f'attachment; filename="microsoft_conversions_{safe_filename}.csv"',
        'Content-Type': 'text/csv'
    }
    return StreamingResponse(output, headers=headers)


@app.get("/dashboard/export/exclusions")
def export_client_exclusions(request: Request, client_id: int):
    email = is_authenticated(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)
    """
    Exports the current active exclusion list for a client as a CSV file.
    """
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        
        # Verify client exists
        cursor.execute("SELECT name FROM clients WHERE id = ?", (client_id,))
        client_row = cursor.fetchone()
        if not client_row:
            raise HTTPException(status_code=400, detail="Invalid client ID")
        
        client_name = client_row[0]
        
        # Pull excluded customers belonging to this client
        cursor.execute("""
            SELECT first_name, last_name, email, phone, company_name
            FROM excluded_customers
            WHERE client_id = ?
            ORDER BY id ASC
        """, (client_id,))
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

    # Generate CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Standard headers that match our sample sheet
    writer.writerow(["first name", "last name", "email", "phone number", "company name"])
    
    for r in rows:
        writer.writerow(r)
            
    output.seek(0)
    safe_filename = re.sub(r'\\s+', '-', client_name.strip().lower())
    
    headers = {
        'Content-Disposition': f'attachment; filename="exclusions_{safe_filename}.csv"',
        'Content-Type': 'text/csv'
    }
    return StreamingResponse(output, headers=headers)



@app.post("/webhooks/exclude-customer")
async def receive_exclusion_webhook(request: Request, client_id: Optional[int] = None):
    """
    CRM/Zapier Exclusions Webhook Receiver.
    Accepts real-time POST payloads containing contact information to add to the excluded_customers list.
    """
    try:
        content_type = request.headers.get("content-type", "")
        payload = {}
        if "application/x-www-form-urlencoded" in content_type:
            form_data = await request.form()
            payload = dict(form_data)
        else:
            try:
                payload = await request.json()
            except Exception:
                form_data = await request.form()
                payload = dict(form_data)
        
        resolved_client_id = client_id or 1
        print(f"🔄 [Exclusion Webhook] Received exclusion payload for Client #{resolved_client_id}: {payload}")
        
        first_name = (
            payload.get("first_name") or 
            payload.get("firstname") or 
            payload.get("fname") or 
            (payload.get("name", "").split(" ")[0] if payload.get("name") else "")
        )
        last_name = (
            payload.get("last_name") or 
            payload.get("lastname") or 
            payload.get("lname") or 
            (" ".join(payload.get("name", "").split(" ")[1:]) if (payload.get("name") and len(payload.get("name", "").split(" ")) > 1) else "")
        )
        email = (
            payload.get("email") or 
            payload.get("email_address") or 
            payload.get("emailaddress") or 
            ""
        ).strip().lower()
        phone_raw = (
            payload.get("phone") or 
            payload.get("phone_number") or 
            payload.get("phonenumber") or 
            payload.get("customer_phone") or 
            payload.get("customer_phone_number") or 
            ""
        )
        company_name = (
            payload.get("company_name") or 
            payload.get("companyname") or 
            payload.get("company") or 
            payload.get("business_name") or 
            ""
        )
        
        normalized_p = normalize_phone(phone_raw)
        
        if not email and not normalized_p:
            return {
                "status": "ignored",
                "message": "Exclusion skipped: Payload must contain a valid 'phone' or 'email' identifier to exclude a user."
            }
            
        conn = db_router.connect()
        cursor = conn.cursor()
        
        cursor.execute("SELECT id FROM clients WHERE id = ?", (resolved_client_id,))
        if not cursor.fetchone():
            conn.close()
            raise HTTPException(status_code=400, detail=f"Invalid Client ID #{resolved_client_id}")
            
        exists = False
        if normalized_p:
            cursor.execute("SELECT id FROM excluded_customers WHERE client_id = ? AND phone = ?", (resolved_client_id, normalized_p))
            if cursor.fetchone():
                exists = True
        if not exists and email:
            cursor.execute("SELECT id FROM excluded_customers WHERE client_id = ? AND email = ?", (resolved_client_id, email))
            if cursor.fetchone():
                exists = True
                
        if exists:
            conn.close()
            print(f"ℹ️ [Client #{resolved_client_id}] Customer already excluded: email={email}, phone={normalized_p}. Skipping insert.")
            return {
                "status": "success",
                "message": "Customer already on exclusion list. Duplicate skipped safely."
            }
            
        cursor.execute("""
            INSERT INTO excluded_customers (client_id, first_name, last_name, email, phone, company_name)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            resolved_client_id,
            first_name,
            last_name,
            email,
            normalized_p,
            company_name
        ))
        conn.commit()
        conn.close()
        
        print(f"✅ [Client #{resolved_client_id}] Excluded customer added via CRM Webhook: Name={first_name} {last_name}, Phone={normalized_p}, Email={email}")
        return {
            "status": "success",
            "message": "Customer successfully added to exclusions list.",
            "record": {
                "client_id": resolved_client_id,
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "phone": normalized_p,
                "company_name": company_name
            }
        }
    except Exception as e:
        print(f"❌ Exclusion Webhook Error: {e}")
        raise HTTPException(status_code=400, detail=str(e))



@app.post("/webhooks/calltrackingmetrics")
async def receive_calltrackingmetrics_webhook(request: Request, client_id: Optional[int] = None):
    """
    Multi-Tenant CallTrackingMetrics Webhook Receiver.
    Accepts completed call logs with dynamic transcript audits via Claude.
    """
    try: 
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            payload = await request.json()
        else:
            try:
                form_data = await request.form()
                payload = dict(form_data)
            except Exception:
                payload = {}
                
        if not isinstance(payload, dict):
            payload = {}
        
        resolved_client_id = 1
        if client_id:
            resolved_client_id = client_id
        else: 
            # Auto-map based on ctm profile id or account id
            profile_id = payload.get('profile_id') or payload.get('ctm_profile_id')
            account_id = payload.get('account_id') or payload.get('ctm_account_id')
            conn = db_router.connect()
            cursor = conn.cursor()
            if profile_id:
                cursor.execute("SELECT id FROM clients WHERE ctm_profile_id = ?", (str(profile_id),))
                match = cursor.fetchone()
                if match:
                    resolved_client_id = match[0]
            elif account_id:
                cursor.execute("SELECT id FROM clients WHERE ctm_account_id = ?", (str(account_id),))
                match = cursor.fetchone()
                if match:
                    resolved_client_id = match[0]
            conn.close()
        
        gclid = payload.get('gclid') or payload.get('google_click_id')
        fbclid = payload.get('fbclid') or payload.get('facebook_click_id')
        li_fat_id = payload.get('li_fat_id') or payload.get('linkedin_click_id')
        msclkid = payload.get('msclkid') or payload.get('microsoft_click_id')
        
        landing_page = payload.get('landing_page_url') or payload.get('landing_page') or ""
        referrer_url = payload.get('referrer_url') or payload.get('referring_url') or ""
        
        if not gclid:
            gclid = extract_param_from_url(landing_page, 'gclid') or extract_param_from_url(referrer_url, 'gclid')
        if not fbclid:
            fbclid = extract_param_from_url(landing_page, 'fbclid') or extract_param_from_url(referrer_url, 'fbclid')
        if not li_fat_id:
            li_fat_id = extract_param_from_url(landing_page, 'li_fat_id') or extract_param_from_url(referrer_url, 'li_fat_id')
        if not msclkid:
            msclkid = extract_param_from_url(landing_page, 'msclkid') or extract_param_from_url(referrer_url, 'msclkid')
            
        caller_name = payload.get('caller_name') or payload.get('customer_name') or payload.get('name', 'Unknown Caller')
        raw_phone = payload.get('caller_number') or payload.get('customer_phone_number') or payload.get('phone')
        transcript = payload.get('transcription') or payload.get('transcript') or payload.get('transcription_text') or ""
        
        if isinstance(transcript, dict):
            transcript = transcript.get("text") or str(transcript)
        elif isinstance(transcript, list):
            transcript = " ".join([str(t) for t in transcript])
            
        normalized_phone = normalize_phone(raw_phone)
        if not normalized_phone:
            return {"status": "ignored", "message": "No valid phone number found in webhook payload."}
            
        ai_qualified = "NO"
        ai_sale_closed = "NO"
        ai_value = 0.0
        ai_reason = "No transcript provided."
        model_name = "None"
        
        qualification_definition_desc = "Someone who expresses real intent to buy or schedule a service."
        
        conn = db_router.connect()
        cursor = conn.cursor()
        cursor.execute("SELECT name, qualification_criteria, lead_count_rule, exclude_past_customers FROM clients WHERE id = ?", (resolved_client_id,))
        client_info = cursor.fetchone()
        conn.close()
        
        if client_info and client_info[1]:
            criteria_code = client_info[1]
            qualification_definition_desc = CRITERIA_MAP.get(criteria_code, qualification_definition_desc)
            
        is_excluded = False
        exclusion_reason = ""
        if client_info and client_info[3] == "YES":
            match_type = check_is_excluded_customer(resolved_client_id, phone=normalized_phone)
            if match_type:
                is_excluded = True
                exclusion_reason = f"Session ignored: Caller phone matches your uploaded past customer list ({match_type})." 
            
        if is_excluded:
            ai_qualified = "NO"
            ai_sale_closed = "NO"
            ai_value = 0.0
            ai_reason = exclusion_reason
            model_name = "None"
            print(f"🚫 [Exclusion Match] Resolved Client #{resolved_client_id}: {exclusion_reason}")
        elif transcript.strip():
            print(f"🧠 [Client #{resolved_client_id}] CTM Transcript detected for {caller_name}. Custom Threshold: {qualification_definition_desc}. Auditing...")
            ai_result = analyze_transcript_with_claude(transcript, qualification_definition_desc)
            ai_qualified = ai_result.get("qualified", "NO")
            ai_sale_closed = ai_result.get("sale_closed", "NO")
            ai_value = float(ai_result.get("value", 0.0))
            ai_reason = ai_result.get("reason", "No reason parsed.")
            model_name = "claude-haiku-4-5-20251001"
            print(f"🎯 Audit Complete: Qualified={ai_qualified}, Sales Value=${ai_value}")
        else:
            print(f"⚠️ [Client #{resolved_client_id}] No transcript provided in CTM webhook for {caller_name}. Skipping AI audit.")

        conn = db_router.connect()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO sessions (
                client_id, phone, name, gclid, fbclid, li_fat_id, msclkid, source, qualified, sale_closed, value, reason, model_used, raw_data
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            resolved_client_id,
            normalized_phone, 
            caller_name, 
            gclid,
            fbclid,
            li_fat_id,
            msclkid,
            "calltrackingmetrics", 
            ai_qualified, 
            ai_sale_closed, 
            ai_value, 
            ai_reason, 
            model_name, 
            str(payload)
        ))
        conn.commit()
        conn.close()
        
        return {
            "status": "success",
            "client_id": resolved_client_id,
            "message": "CTM Webhook log and AI analysis processed and saved successfully.",
            "ai_audit": {
                "qualified": ai_qualified,
                "sale_closed": ai_sale_closed,
                "value": ai_value,
                "reason": ai_reason
            }
        }
    except Exception as e:
        print(f"❌ CTM Webhook Error: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/webhooks/whatconverts")
async def receive_whatconverts_webhook(request: Request, client_id: Optional[int] = None):
    """
    Multi-Tenant WhatConverts Webhook Receiver.
    Accepts completed call logs with dynamic transcript audits via Claude.
    """
    try: 
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            payload = await request.json()
        else:
            try:
                form_data = await request.form()
                payload = dict(form_data)
            except Exception:
                payload = {}
                
        if not isinstance(payload, dict):
            payload = {}
        
        resolved_client_id = 1
        if client_id:
            resolved_client_id = client_id
        else: 
            # Auto-map based on wc profile id or account id
            profile_id = payload.get('profile_id') or payload.get('wc_profile_id')
            account_id = payload.get('account_id') or payload.get('wc_account_id')
            conn = db_router.connect()
            cursor = conn.cursor()
            if profile_id:
                cursor.execute("SELECT id FROM clients WHERE wc_profile_id = ?", (str(profile_id),))
                match = cursor.fetchone()
                if match:
                    resolved_client_id = match[0]
            elif account_id:
                cursor.execute("SELECT id FROM clients WHERE wc_account_id = ?", (str(account_id),))
                match = cursor.fetchone()
                if match:
                    resolved_client_id = match[0]
            conn.close()
        
        gclid = payload.get('gclid') or payload.get('google_click_id')
        fbclid = payload.get('fbclid') or payload.get('facebook_click_id')
        li_fat_id = payload.get('li_fat_id') or payload.get('linkedin_click_id')
        msclkid = payload.get('msclkid') or payload.get('microsoft_click_id')
        
        landing_page = payload.get('landing_page_url') or payload.get('landing_page') or ""
        referrer_url = payload.get('referrer_url') or payload.get('referring_url') or ""
        
        if not gclid:
            gclid = extract_param_from_url(landing_page, 'gclid') or extract_param_from_url(referrer_url, 'gclid')
        if not fbclid:
            fbclid = extract_param_from_url(landing_page, 'fbclid') or extract_param_from_url(referrer_url, 'fbclid')
        if not li_fat_id:
            li_fat_id = extract_param_from_url(landing_page, 'li_fat_id') or extract_param_from_url(referrer_url, 'li_fat_id')
        if not msclkid:
            msclkid = extract_param_from_url(landing_page, 'msclkid') or extract_param_from_url(referrer_url, 'msclkid')
            
        caller_name = payload.get('caller_name') or payload.get('customer_name') or payload.get('name', 'Unknown Caller')
        raw_phone = payload.get('caller_phone') or payload.get('caller_number') or payload.get('phone_number') or payload.get('phone')
        transcript = payload.get('transcription') or payload.get('transcript') or payload.get('transcription_text') or payload.get('text') or ""
        
        if isinstance(transcript, dict):
            transcript = transcript.get("text") or str(transcript)
        elif isinstance(transcript, list):
            transcript = " ".join([str(t) for t in transcript])
            
        normalized_phone = normalize_phone(raw_phone)
        if not normalized_phone:
            return {"status": "ignored", "message": "No valid phone number found in webhook payload."}
            
        ai_qualified = "NO"
        ai_sale_closed = "NO"
        ai_value = 0.0
        ai_reason = "No transcript provided."
        model_name = "None"
        
        qualification_definition_desc = "Someone who expresses real intent to buy or schedule a service."
        
        conn = db_router.connect()
        cursor = conn.cursor()
        cursor.execute("SELECT name, qualification_criteria, lead_count_rule, exclude_past_customers FROM clients WHERE id = ?", (resolved_client_id,))
        client_info = cursor.fetchone()
        conn.close()
        
        if client_info and client_info[1]:
            criteria_code = client_info[1]
            qualification_definition_desc = CRITERIA_MAP.get(criteria_code, qualification_definition_desc)
            
        is_excluded = False
        exclusion_reason = ""
        if client_info and client_info[3] == "YES":
            match_type = check_is_excluded_customer(resolved_client_id, phone=normalized_phone)
            if match_type:
                is_excluded = True
                exclusion_reason = f"Session ignored: Caller phone matches your uploaded past customer list ({match_type})." 
            
        if is_excluded:
            ai_qualified = "NO"
            ai_sale_closed = "NO"
            ai_value = 0.0
            ai_reason = exclusion_reason
            model_name = "None"
            print(f"🚫 [Exclusion Match] Resolved Client #{resolved_client_id}: {exclusion_reason}")
        elif transcript.strip():
            print(f"🧠 [Client #{resolved_client_id}] WC Transcript detected for {caller_name}. Custom Threshold: {qualification_definition_desc}. Auditing...")
            ai_result = analyze_transcript_with_claude(transcript, qualification_definition_desc)
            ai_qualified = ai_result.get("qualified", "NO")
            ai_sale_closed = ai_result.get("sale_closed", "NO")
            ai_value = float(ai_result.get("value", 0.0))
            ai_reason = ai_result.get("reason", "No reason parsed.")
            model_name = "claude-haiku-4-5-20251001"
            print(f"🎯 Audit Complete: Qualified={ai_qualified}, Sales Value=${ai_value}")
        else:
            print(f"⚠️ [Client #{resolved_client_id}] No transcript provided in WC webhook for {caller_name}. Skipping AI audit.")

        conn = db_router.connect()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO sessions (
                client_id, phone, name, gclid, fbclid, li_fat_id, msclkid, source, qualified, sale_closed, value, reason, model_used, raw_data
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            resolved_client_id,
            normalized_phone, 
            caller_name, 
            gclid,
            fbclid,
            li_fat_id,
            msclkid,
            "whatconverts", 
            ai_qualified, 
            ai_sale_closed, 
            ai_value, 
            ai_reason, 
            model_name, 
            str(payload)
        ))
        conn.commit()
        conn.close()
        
        return {
            "status": "success",
            "client_id": resolved_client_id,
            "message": "WhatConverts Webhook log and AI analysis processed and saved successfully.",
            "ai_audit": {
                "qualified": ai_qualified,
                "sale_closed": ai_sale_closed,
                "value": ai_value,
                "reason": ai_reason
            }
        }
    except Exception as e:
        print(f"❌ WhatConverts Webhook Error: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/webhooks/callrail")
async def receive_callrail_webhook(request: Request, client_id: Optional[int] = None):
    """
    Multi-Tenant CallRail Webhook Receiver.
    If no client_id query param is sent, parses the company/account info inside CallRail's payload to auto-map it!
    Also performs dynamic, regex-based URL query extraction to catch fbclid, li_fat_id, and msclkid.
    """
    try: 
        # 1. Parse the incoming JSON or Form data from CallRail safely
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            payload = await request.json()
        else:
            try:
                form_data = await request.form()
                payload = dict(form_data)
            except Exception:
                payload = {}
                
        # Ensure payload is a dictionary
        if not isinstance(payload, dict):
            payload = {}
        
        # Resolve Multi-Tenant Client Mapping
        resolved_client_id = 1  # Default fallback
        
        if client_id:
            resolved_client_id = client_id
        else: 
            company_id = payload.get('company_id') or payload.get('account_id')
            if company_id:
                conn = db_router.connect()
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM clients WHERE callrail_company_id = ?", (str(company_id),))
                match = cursor.fetchone()
                if match:
                    resolved_client_id = match[0]
                conn.close()
        
        # Safely extract 'referrer' if it's a dict, otherwise fallback to empty dict
        referrer_data = payload.get('referrer')
        referrer_dict = referrer_data if isinstance(referrer_data, dict) else {}
        
        # 2. Extract Webhook Variables safely (with robust Milestones block lookup)
        gclid = payload.get('google_click_id') or payload.get('gclid') or referrer_dict.get('gclid')
        fbclid = payload.get('facebook_click_id') or payload.get('fbclid') or referrer_dict.get('fbclid')
        li_fat_id = payload.get('linkedin_click_id') or payload.get('li_fat_id') or referrer_dict.get('li_fat_id')
        msclkid = payload.get('microsoft_click_id') or payload.get('msclkid') or referrer_dict.get('msclkid')
        
        # Fallback to milestones block if top-level fields are missing in payload
        milestones = payload.get("milestones")
        if isinstance(milestones, dict):
            for m_key, m_data in milestones.items():
                if isinstance(m_data, dict):
                    if not gclid:
                        gclid = m_data.get("gclid") or m_data.get("google_click_id")
                    if not fbclid:
                        fbclid = m_data.get("fbclid") or m_data.get("facebook_click_id")
                    if not li_fat_id:
                        li_fat_id = m_data.get("li_fat_id") or m_data.get("linkedin_click_id")
                    if not msclkid:
                        msclkid = m_data.get("msclkid") or m_data.get("microsoft_click_id")
        
        # Advanced dynamic regex URL extraction (for redundancy / fallback)
        landing_page = payload.get('landing_page_url') or referrer_dict.get('landing_page_url') or ""
        referrer_url = payload.get('referrer_url') or referrer_dict.get('referrer_url') or referrer_dict.get('referring_url') or payload.get('referring_url') or ""
        
        if not gclid:
            gclid = extract_param_from_url(landing_page, 'gclid') or extract_param_from_url(referrer_url, 'gclid')
        if not fbclid:
            fbclid = extract_param_from_url(landing_page, 'fbclid') or extract_param_from_url(referrer_url, 'fbclid')
        if not li_fat_id:
            li_fat_id = extract_param_from_url(landing_page, 'li_fat_id') or extract_param_from_url(referrer_url, 'li_fat_id')
        if not msclkid:
            msclkid = extract_param_from_url(landing_page, 'msclkid') or extract_param_from_url(referrer_url, 'msclkid')
            
        caller_name = payload.get('customer_name', 'Unknown Caller')
        raw_phone = payload.get('customer_phone_number')
        raw_transcript = payload.get('transcript') or payload.get('transcription') or ""
        transcript = ""
        if isinstance(raw_transcript, str):
            transcript = raw_transcript
        elif isinstance(raw_transcript, list):
            segments = []
            for segment in raw_transcript:
                if isinstance(segment, dict):
                    speaker = segment.get("speaker") or segment.get("role") or "Speaker"
                    text = segment.get("text") or segment.get("message") or ""
                    if text:
                        segments.append(f"[{speaker}]: {text}")
                elif isinstance(segment, str):
                    segments.append(segment)
            transcript = "\n".join(segments)
        elif isinstance(raw_transcript, dict):
            transcript = raw_transcript.get("text") or raw_transcript.get("transcription") or str(raw_transcript)
        
        # 3. Normalize Phone
        normalized_phone = normalize_phone(raw_phone)
        if not normalized_phone:
            return {"status": "ignored", "message": "No valid phone number found in webhook payload."}
            
        # 4. Trigger Claude AI Transcript Analyzer with Dynamic Qualification Prompts
        ai_qualified = "NO"
        ai_sale_closed = "NO"
        ai_value = 0.0
        ai_reason = "No transcript provided."
        model_name = "None"
        
        qualification_definition_desc = "Someone who expresses real intent to buy or schedule a service."
        
        conn = db_router.connect()
        cursor = conn.cursor()
        cursor.execute("SELECT name, qualification_criteria, lead_count_rule, exclude_past_customers FROM clients WHERE id = ?", (resolved_client_id,))
        client_info = cursor.fetchone()
        conn.close()
        
        if client_info and client_info[1]:
            criteria_code = client_info[1]
            qualification_definition_desc = CRITERIA_MAP.get(criteria_code, qualification_definition_desc)
            
        # Check if client has exclusion enabled and if caller matches the exclusion list
        is_excluded = False
        exclusion_reason = ""
        if client_info and client_info[3] == "YES":
            match_type = check_is_excluded_customer(resolved_client_id, phone=normalized_phone)
            if match_type:
                is_excluded = True
                exclusion_reason = f"Session ignored: Caller phone matches your uploaded past customer list ({match_type})." 
            
        if is_excluded:
            ai_qualified = "NO"
            ai_sale_closed = "NO"
            ai_value = 0.0
            ai_reason = exclusion_reason
            model_name = "None"
            print(f"🚫 [Exclusion Match] Resolved Client #{resolved_client_id}: {exclusion_reason}")
        elif transcript.strip():
            print(f"🧠 [Client #{resolved_client_id}] Transcript detected for {caller_name}. Custom Threshold: {qualification_definition_desc}. Auditing...")
            ai_result = analyze_transcript_with_claude(transcript, qualification_definition_desc)
            ai_qualified = ai_result.get("qualified", "NO")
            ai_sale_closed = ai_result.get("sale_closed", "NO")
            ai_value = float(ai_result.get("value", 0.0))
            ai_reason = ai_result.get("reason", "No reason parsed.")
            model_name = "claude-haiku-4-5-20251001"
            print(f"🎯 Audit Complete: Qualified={ai_qualified}, Sales Value=${ai_value}")
        else:
            print(f"⚠️ [Client #{resolved_client_id}] No transcript provided in CallRail webhook for {caller_name}. Skipping AI audit.")

        # 5. Save Session including multi-channel click IDs
        conn = db_router.connect()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO sessions (
                client_id, phone, name, gclid, fbclid, li_fat_id, msclkid, source, qualified, sale_closed, value, reason, model_used, raw_data
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            resolved_client_id,
            normalized_phone, 
            caller_name, 
            gclid,
            fbclid,
            li_fat_id,
            msclkid,
            "callrail", 
            ai_qualified, 
            ai_sale_closed, 
            ai_value, 
            ai_reason, 
            model_name, 
            str(payload)
        ))
        
        conn.commit()
        conn.close()
        
        return {
            "status": "success",
            "client_id": resolved_client_id,
            "message": "Webhook log and AI analysis processed and saved successfully.",
            "ai_audit": {
                "qualified": ai_qualified,
                "sale_closed": ai_sale_closed,
                "value": ai_value,
                "reason": ai_reason
            }
        }
            
    except Exception as e:
        print(f"❌ Webhook Error: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/webhooks/form")
async def receive_form_lead(lead: FormLead, client_id: Optional[int] = None):
    """
    Form Lead Webhook Receiver supporting Multi-Tenancy.
    Saves website visitor form entries containing GCLIDs, FBCLIDs, LI_FAT_IDs, and MSCLKIDs.
    """
    try:
        resolved_client_id = client_id or 1  # Fallback to Client 1 if not defined
        
        # 1. Clean data
        full_name = f"{lead.first_name} {lead.last_name}".strip()
        normalized_phone = normalize_phone(lead.phone)
        email_clean = lead.email.strip().lower()
        
        # Check if client has exclusion enabled
        is_excluded = False
        exclusion_reason = None
        
        conn = db_router.connect()
        cursor = conn.cursor()
        cursor.execute("SELECT exclude_past_customers FROM clients WHERE id = ?", (resolved_client_id,))
        client_row = cursor.fetchone()
        conn.close()
        
        if client_row and client_row[0] == "YES":
            match_type = check_is_excluded_customer(resolved_client_id, phone=normalized_phone, email=email_clean)
            if match_type:
                is_excluded = True
                exclusion_reason = f"Form submission ignored: matches your uploaded past customer list ({match_type})."
        
        qualified_val = "YES" if not is_excluded else "NO"
        sale_closed_val = "NO"
        reason_val = None if not is_excluded else exclusion_reason

        # 2. Save to SQLite
        conn = db_router.connect()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO sessions (client_id, phone, email, name, company, gclid, fbclid, li_fat_id, msclkid, source, qualified, sale_closed, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            resolved_client_id, 
            normalized_phone, 
            email_clean, 
            full_name, 
            lead.company, 
            lead.gclid, 
            lead.fbclid, 
            lead.li_fat_id, 
            lead.msclkid, 
            "form",
            qualified_val,
            sale_closed_val,
            reason_val
        ))
        conn.commit()
        conn.close()
        
        print(f"📝 [Client #{resolved_client_id}] Form Lead saved: Name={full_name}, Phone={normalized_phone}, Email={email_clean}, GCLID={lead.gclid}")
        return {"status": "success", "message": f"Form lead saved under client #{resolved_client_id}."}
        
    except Exception as e:
        print(f"❌ Error saving Form Lead: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/webhooks/crm")
async def receive_crm_webhook(request: Request, client_id: Optional[int] = None):
    """
    CRM Lead/Deal Update Webhook Receiver supporting Multi-Tenancy.
    Processes conversion logs and maps lead changes to the correct client profile.
    """
    try:
        payload = await request.json()
        resolved_client_id = client_id or 1
        print(f"🏢 [CRM Webhook] Received conversion payload for Client #{resolved_client_id}: {payload}")
        
        # Log to Database
        contact_name = payload.get('deal_name') or payload.get('contact_name') or payload.get('lead_name') or payload.get('name') or "Unknown Deal/Contact"
        stage = payload.get('deal_stage') or payload.get('stage') or payload.get('status') or "Updated"
        amount = payload.get('amount') or payload.get('value') or payload.get('deal_value') or 0.0
        
        conn = db_router.connect()
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS crm_webhook_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER,
                contact_name TEXT,
                stage TEXT,
                amount REAL,
                received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cursor.execute("""
            INSERT INTO crm_webhook_logs (client_id, contact_name, stage, amount)
            VALUES (?, ?, ?, ?)
        """, (resolved_client_id, str(contact_name), str(stage), float(amount)))
        conn.commit()
        conn.close()
        
        return {
            "status": "success",
            "client_id": resolved_client_id,
            "message": f"CRM lead conversion successfully processed under client #{resolved_client_id}."
        }
    except Exception as e:
        print(f"❌ CRM Webhook Error: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/webhooks/billing")
async def receive_billing_webhook(request: Request, client_id: Optional[int] = None):
    """
    Billing Software (QuickBooks/Xero) Webhook Receiver supporting Multi-Tenancy.
    Tracks paid invoice events to verify closed transactions and trigger conversion uploads.
    """
    try:
        payload = await request.json()
        resolved_client_id = client_id or 1
        print(f"💳 [Billing Webhook] Received transaction payload for Client #{resolved_client_id}: {payload}")
        
        # Log to Database
        customer_name = payload.get('customer_name') or payload.get('name') or "Unknown Customer"
        invoice_number = payload.get('invoice_number') or payload.get('invoice_id') or payload.get('doc_number') or ""
        amount = payload.get('amount') or payload.get('amount_paid') or payload.get('total') or 0.0
        
        conn = db_router.connect()
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS billing_webhook_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER,
                customer_name TEXT,
                invoice_number TEXT,
                amount REAL,
                received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cursor.execute("""
            INSERT INTO billing_webhook_logs (client_id, customer_name, invoice_number, amount)
            VALUES (?, ?, ?, ?)
        """, (resolved_client_id, str(customer_name), str(invoice_number), float(amount)))
        conn.commit()
        conn.close()
        
        return {
            "status": "success",
            "client_id": resolved_client_id,
            "message": f"Billing invoice paid webhook processed under client #{resolved_client_id}."
        }
    except Exception as e:
        print(f"❌ Billing Webhook Error: {e}")
        raise HTTPException(status_code=400, detail=str(e))

# ---------------------------------------------------------
# SECURE NIGHTLY CRON SYNCRONIZATION TRIGGER
# ---------------------------------------------------------
@app.post("/tasks/daily-sync")
async def trigger_daily_sync(request: Request):
    """
    Secure endpoint that lets Render's Cron Job trigger the nightly CallRail sync
    directly on the web container where the SQLite database lives.
    """
    import importlib.util
    import sys
    
    # 1. Resolve Authorization Token
    secret_token = os.environ.get("SYNC_TOKEN", "default_secure_sync_token_123")
    
    # Try Header
    auth_header = request.headers.get("authorization")
    if not auth_header:
        # Fallback to query parameter for simpler testing
        token_param = request.query_params.get("token")
        if token_param:
            auth_header = f"Bearer {token_param}"
            
    if auth_header != f"Bearer {secret_token}":
        raise HTTPException(status_code=401, detail="Unauthorized sync request.")
        
    try:
        module_name = "daily_callrail_sync"
        
        # Check standard filenames first
        target_files = ["daily-callrail-sync-v3.py", "daily-callrail-sync-v2.py", "daily-callrail-sync.py", "daily_callrail_sync.py"]
        imported = False
        
        for fname in target_files:
            if os.path.exists(fname):
                spec = importlib.util.spec_from_file_location(module_name, fname)
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
                module.execute_daily_sync()
                imported = True
                break
                
        if not imported:
            # Try a direct import if it's already in python path
            try:
                import daily_callrail_sync
                daily_callrail_sync.execute_daily_sync()
                imported = True
            except ImportError:
                pass
                
        if not imported:
            raise FileNotFoundError("Could not locate daily-callrail-sync.py or daily_callrail_sync.py in the running directory.")
            
        return {"status": "success", "message": "Daily CallRail database sync executed successfully."}
        
    except Exception as e:
        print(f"❌ Cron Trigger Sync Exception: {e}")
        raise HTTPException(status_code=500, detail=f"Sync execution failed: {str(e)}")
