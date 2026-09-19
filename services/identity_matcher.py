import re
import difflib
from datetime import datetime, timedelta
from typing import Optional
from database.connection import db_router

def clean_company_name(name: str) -> str:
    if not name:
        return ""
    name_clean = name.lower().strip()
    name_clean = re.sub(r"[^\w\s]", "", name_clean)
    noise_words = ["inc", "llc", "corp", "co", "ltd", "group", "services", "limited", "incorporated", "corporation"]
    tokens = [w for w in name_clean.split() if w not in noise_words]
    return " ".join(tokens)

def clean_person_name(name: str) -> str:
    if not name:
        return ""
    name_clean = name.lower().strip()
    name_clean = re.sub(r"[^\w\s,]", "", name_clean)
    return name_clean

def check_name_transposition(name1: str, name2: str) -> float:
    n1 = clean_person_name(name1)
    n2 = clean_person_name(name2)
    tokens1 = [t.strip() for t in n1.split(",") if t.strip()]
    tokens2 = [t.strip() for t in n2.split(",") if t.strip()]
    
    if len(tokens1) > 1:
        n1_standard = " ".join(reversed(tokens1))
    else:
        n1_standard = n1
        
    if len(tokens2) > 1:
        n2_standard = " ".join(reversed(tokens2))
    else:
        n2_standard = n2

    t1 = n1_standard.split()
    t2 = n2_standard.split()
    
    if not t1 or not t2:
        return 0.0
        
    if sorted(t1) == sorted(t2):
        return 1.0
        
    if t1[-1] == t2[-1]:
        f1, f2 = t1[0], t2[0]
        if f1 == f2:
            return 1.0
        f1_clean = re.sub(r"\.", "", f1).strip()
        f2_clean = re.sub(r"\.", "", f2).strip()
        if f1_clean == f2_clean:
            return 1.0
        if len(f1_clean) == 1 and f2_clean.startswith(f1_clean):
            return 0.90
        if len(f2_clean) == 1 and f1_clean.startswith(f2_clean):
            return 0.90
        if f1_clean in f2_clean or f2_clean in f1_clean:
            return 0.85
            
    return difflib.SequenceMatcher(None, n1_standard, n2_standard).ratio()

def calculate_company_similarity(name1: str, name2: str) -> float:
    c1 = clean_company_name(name1)
    c2 = clean_company_name(name2)
    if not c1 or not c2:
        return 0.0
    if c1 in c2 or c2 in c1:
        return 1.0
    return difflib.SequenceMatcher(None, c1, c2).ratio()

def find_dynamic_columns_custom(columns: list) -> tuple:
    phone_col = None
    email_col = None
    value_col = None
    name_col = None
    company_col = None
    cleaned_cols = {col: re.sub(r"[\s_-]+", "", col.lower()) for col in columns}
    for original_col, clean_col in cleaned_cols.items():
        if not phone_col and re.search(r"(phone|tele|mobile|cell|num|contact)", clean_col):
            phone_col = original_col
            continue
        if not email_col and re.search(r"(email|mail|address)", clean_col):
            email_col = original_col
            continue
        if not value_col and re.search(r"(amount|value|revenue|total|price|paid|sum|invoice|sale|cost)", clean_col):
            value_col = original_col
            continue
        if not name_col and re.search(r"(name|customer|client|contact|lead)", clean_col):
            name_col = original_col
            continue
        if not company_col and re.search(r"(company|business|firm|org|account)", clean_col):
            company_col = original_col
            continue
    return phone_col, email_col, value_col, name_col, company_col

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
    _pg_failed = False

    @staticmethod
    def connect():
        if DATABASE_URL and not DatabaseRouter._pg_failed:
            import psycopg2
            url_clean = DATABASE_URL
            if url_clean.startswith("postgres://"):
                url_clean = url_clean.replace("postgres://", "postgresql://", 1)
            
            try:
                # Ensure sslmode='require' is present for Render PostgreSQL
                if "sslmode=" not in url_clean:
                    conn = psycopg2.connect(url_clean, sslmode="require", connect_timeout=3)
                else:
                    conn = psycopg2.connect(url_clean, connect_timeout=3)
                return PostgreSQLConnectionWrapper(conn)
            except Exception as e:
                print(f"⚠️ PostgreSQL connection error ({e}). Switching to local SQLite fallback.")
                DatabaseRouter._pg_failed = True
                import sqlite3
                return sqlite3.connect("offline_attribution.db")
        else:
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

    # Create Password Resets Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS password_resets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            token TEXT UNIQUE NOT NULL,
            is_used TEXT DEFAULT 'NO',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP
        )
    """)

    
    # 8. Create Client Configuration Change History Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS client_config_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER,
            changed_by TEXT NOT NULL,
            feature_name TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (client_id) REFERENCES clients (id)
        )
    """)

    # Webhook Logs Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS webhook_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER,
            source TEXT NOT NULL,
            event_type TEXT NOT NULL,
            status_code INTEGER DEFAULT 200,
            payload_summary TEXT,
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (client_id) REFERENCES clients (id)
        )
    """)

    # Unmatched Records Queue Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS unmatched_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            record_type TEXT DEFAULT 'sale',
            customer_identifier TEXT,
            amount REAL DEFAULT 0.0,
            source_system TEXT,
            reason TEXT,
            status TEXT DEFAULT 'UNMATCHED',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (client_id) REFERENCES clients (id)
        )
    """)

    # Seed mock webhook logs & unmatched records if empty
    cursor.execute("SELECT COUNT(*) FROM webhook_logs")
    if cursor.fetchone()[0] == 0:
        cursor.execute("""
            INSERT INTO webhook_logs (client_id, source, event_type, status_code, payload_summary, error_message)
            VALUES 
            (1, 'CallRail', 'call_completed', 200, 'Inbound call from +1 (555) 234-5678 (Duration: 4m 12s, Transcript Audited)', NULL),
            (1, 'HubSpot CRM', 'deal.closed_won', 200, 'Closed Won Deal #8492 - Amount: $1,250.00 (Customer: John Doe)', NULL),
            (1, 'Website Form', 'form_submission', 200, 'Inbound Web Form POST - GCLID: Cj0KCQiA... (Email: j.doe@example.com)', NULL),
            (1, 'CallRail', 'call_completed', 200, 'Inbound call from +1 (555) 876-5432 (Duration: 1m 05s, Not Qualified)', NULL),
            (1, 'ServiceTitan', 'job.completed', 422, 'Job #9102 completed ($850.00) - Missing matching phone/email in click session DB', 'Identity Match Failed: Phone +15559998888 not found in last 90 days sessions'),
            (2, 'WhatConverts', 'call_completed', 200, 'Inbound call from +1 (555) 345-6789 (Duration: 3m 45s, Qualified Lead)', NULL),
            (2, 'QuickBooks', 'invoice.paid', 200, 'Invoice #INV-401 Paid - Amount: $3,400.00 (Customer: Apex HVAC)', NULL)
        """)

    cursor.execute("SELECT COUNT(*) FROM unmatched_records")
    if cursor.fetchone()[0] == 0:
        cursor.execute("""
            INSERT INTO unmatched_records (client_id, record_type, customer_identifier, amount, source_system, reason, status)
            VALUES
            (1, 'sale', 'sarah.jenkins@gmail.com / +1 (555) 999-1234', 1450.00, 'HubSpot CRM Webhook', 'No matching click ID session found in last 90 days (Direct or Organic customer)', 'UNMATCHED'),
            (1, 'sale', '+1 (555) 444-5555 (Phone Typo)', 820.00, 'QuickBooks Invoice #INV-1092', 'Phone number not recognized in CallRail session database', 'UNMATCHED'),
            (2, 'lead', 'info@techcorp.io', 0.00, 'Website Form Webhook', 'GCLID parameter expired (>90 days old) prior to conversion upload', 'UNMATCHED')
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
            email_app_password TEXT,
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
        ("sales_source", "TEXT DEFAULT 'manual'"),
        ("facebook_ads_id", "TEXT"),
        ("tiktok_ads_id", "TEXT"),
        ("twitter_ads_id", "TEXT"),
        ("pinterest_ads_id", "TEXT"),
        ("snapchat_ads_id", "TEXT"),
        ("chatgpt_ads_id", "TEXT"),
        ("reddit_ads_id", "TEXT"),
        ("linkedin_ads_id", "TEXT"),
        ("microsoft_ads_id", "TEXT"),
        ("lead_gen_method", "TEXT"),
        ("qualification_criteria", "TEXT"),
        ("source_of_truth", "TEXT"),
        ("email_provider", "TEXT"),
        ("email_account", "TEXT"),
        ("email_app_password", "TEXT"),
        ("crm_deal_tags", "TEXT"),
        ("crm_won_deal_tags", "TEXT"),
        ("crm_value_field", "TEXT"),
        ("crm_lead_tags", "TEXT"),
        ("lead_count_rule", "TEXT"),
        ("exclude_past_customers", "TEXT"),
        ("callrail_account_id", "TEXT"),
        ("call_tracking_provider", "TEXT"),
        ("ctm_account_id", "TEXT"),
        ("ctm_profile_id", "TEXT"),
        ("wc_account_id", "TEXT"),
        ("wc_profile_id", "TEXT"),
        ("email_account_2", "TEXT"),
        ("email_app_password_2", "TEXT"),
        ("email_account_3", "TEXT"),
        ("email_app_password_3", "TEXT"),
        ("email_account_4", "TEXT"),
        ("email_app_password_4", "TEXT"),
        ("email_account_5", "TEXT"),
        ("email_app_password_5", "TEXT")
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
            match_fuzzy TEXT DEFAULT 'NO',
            certainty_score INTEGER DEFAULT 100,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (client_id) REFERENCES clients (id)
        )
    """)
    
    # Self-heal sessions schema
    cursor.execute("PRAGMA table_info(sessions)")
    existing_session_cols = [col[1] for col in cursor.fetchall()]
    session_cols_to_verify = [
        ("fbclid", "TEXT"),
        ("ttclid", "TEXT"),
        ("twclid", "TEXT"),
        ("pin_clid", "TEXT"),
        ("scclid", "TEXT"),
        ("gptclid", "TEXT"),
        ("rdt_cid", "TEXT"),
        ("li_fat_id", "TEXT"),
        ("msclkid", "TEXT"),
        ("match_fuzzy", "TEXT DEFAULT 'NO'"),
        ("certainty_score", "INTEGER DEFAULT 100"),
        ("adjusted", "TEXT DEFAULT 'NO'"),
        ("adjusted_value", "REAL DEFAULT 0.0"),
        ("adjustment_type", "TEXT"),
        ("adjusted_at", "TIMESTAMP")
    ]
    for col_name, col_type in session_cols_to_verify:
        if col_name not in existing_session_cols:
            cursor.execute(f"ALTER TABLE sessions ADD COLUMN {col_name} {col_type}")
            print(f"Added missing session column: {col_name}")
            
    # 3. Seed Mock Clients if empty
    cursor.execute("SELECT COUNT(*) FROM clients")
    if cursor.fetchone()[0] == 0:
        mock_clients = [
            ("Priority Plumbing", "comp_plumbing", "123-456-7890", "fb_plumb_99", "", "", "both", "C", "hubspot", "", "", "", "appointment-booked", "closed-won", "", "all", "NO"),
            ("Apex HVAC & Air", "comp_hvac", "987-654-3210", "", "", "ms_hvac_88", "both", "E", "servicetitan", "", "", "", "", "", "completed-lead", "all", "NO"),
            ("Metro Dental Care", "comp_dental", "555-123-4567", "", "li_dental_77", "", "both", "C", "email", "gmail", "bookings@metrodental.com", "", "", "", "", "maximum_one", "YES")
        ]
        cursor.executemany("""
            INSERT INTO clients (
                name, callrail_company_id, google_ads_customer_id, facebook_ads_id, linkedin_ads_id, microsoft_ads_id,
                lead_gen_method, qualification_criteria, source_of_truth, email_provider, email_account, email_app_password,
                crm_deal_tags, crm_won_deal_tags, crm_lead_tags, lead_count_rule, exclude_past_customers
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, mock_clients)
        print("Seeded 3 mock agency clients successfully!")
        
    # Seed default admin user if users table is empty
    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] == 0:
        default_admin_email = "admin@leadgrove.net"
        default_admin_pass = hash_password("admin123")
        cursor.execute("""
            INSERT INTO users (email, hashed_password, role)
            VALUES (?, ?, 'full')
        """, (default_admin_email, default_admin_pass))
        print(f"Seeded default admin user '{default_admin_email}' successfully!")
    
    conn.commit()
    conn.close()

import threading

def _async_init_db():
    try:
        print(" [DB Init] Starting background database schema initialization & self-healing...")
        init_db()
        print("✅ [DB Init] Database initialization completed successfully!")
    except Exception as e:
        print(f"⚠️ [DB Init] Warning: Non-fatal startup database init error: {e}")

# Run database initialization asynchronously on FastAPI startup to allow instant port binding on Render
@app.on_event("startup")
def startup_db_init():
    print(" [Startup] App startup event triggered! Binding port immediately...")
    threading.Thread(target=_async_init_db, daemon=True).start()
    print("⚡ [Startup] Port binding ready!")


# ---------------------------------------------------------
# ANTHROPIC CLAUDE CONFIGURATION
# ---------------------------------------------------------
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
client = Anthropic(api_key=API_KEY, max_retries=3, timeout=30.0) if API_KEY else None



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
    "gohighlevel": "GoHighLevel (GHL) CRM",
    "quickbooks": "QuickBooks Billing",
    "xero": "Xero Accounting",
    "zoho_books": "Zoho Books Accounting",
    "netsuite": "NetSuite ERP/Accounting",
    "sage": "Sage Accounting",
    "freshbooks": "FreshBooks Billing",
    "google_sheets": "Google Sheets (Live Sync)",
    "zapier": "Zapier Custom Integration",
    "ai_rating": "AI Rating (Direct Call Audits & Dynamic Form-Email Monitoring)"
}


# ---------------------------------------------------------
# WEBHOOK DATA SCHEMAS (Pydantic Models)
# ---------------------------------------------------------
