import sqlite3
from database.connection import db_router, DATABASE_URL
from services.auth_service import hash_password

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

