from database.connection import db_router
from services.auth_service import hash_password

def init_db():
    conn = db_router.connect()
    cursor = conn.cursor()

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

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_invitations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT UNIQUE NOT NULL,
            email TEXT NOT NULL,
            role TEXT DEFAULT 'full',
            client_id INTEGER,
            created_by TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_sessions (
            token TEXT PRIMARY KEY,
            email TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS password_resets (
            token TEXT PRIMARY KEY,
            email TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

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

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            callrail_account_id TEXT,
            callrail_company_id TEXT,
            google_ads_customer_id TEXT,
            facebook_ads_id TEXT,
            linkedin_ads_id TEXT,
            microsoft_ads_id TEXT,
            tiktok_ads_id TEXT,
            twitter_ads_id TEXT,
            pinterest_ads_id TEXT,
            snapchat_ads_id TEXT,
            chatgpt_ads_id TEXT,
            reddit_ads_id TEXT,
            sales_source TEXT,
            crm_value_field TEXT,
            call_tracking_provider TEXT,
            ctm_account_id TEXT,
            ctm_profile_id TEXT,
            wc_account_id TEXT,
            wc_profile_id TEXT,
            lead_gen_method TEXT,
            qualification_criteria TEXT,
            source_of_truth TEXT,
            email_provider TEXT,
            email_account TEXT,
            email_app_password TEXT,
            email_account_2 TEXT,
            email_app_password_2 TEXT,
            email_account_3 TEXT,
            email_app_password_3 TEXT,
            email_account_4 TEXT,
            email_app_password_4 TEXT,
            email_account_5 TEXT,
            email_app_password_5 TEXT,
            crm_deal_tags TEXT,
            crm_won_deal_tags TEXT,
            crm_lead_tags TEXT,
            lead_count_rule TEXT DEFAULT 'all',
            exclude_past_customers TEXT DEFAULT 'NO',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS excluded_customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            first_name TEXT,
            last_name TEXT,
            email TEXT,
            phone TEXT,
            company_name TEXT,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (client_id) REFERENCES clients (id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER DEFAULT 1,
            phone TEXT,
            email TEXT,
            name TEXT,
            company TEXT,
            gclid TEXT,
            fbclid TEXT,
            li_fat_id TEXT,
            msclkid TEXT,
            ttclid TEXT,
            twclid TEXT,
            pin_clid TEXT,
            scclid TEXT,
            gptclid TEXT,
            rdt_cid TEXT,
            source TEXT DEFAULT 'callrail',
            qualified TEXT DEFAULT 'NO',
            sale_closed TEXT DEFAULT 'NO',
            value REAL DEFAULT 0.0,
            adjusted TEXT DEFAULT 'NO',
            adjusted_value REAL DEFAULT 0.0,
            adjustment_type TEXT,
            adjusted_at TIMESTAMP,
            reason TEXT,
            model_used TEXT,
            match_fuzzy TEXT DEFAULT 'NO',
            certainty_score INTEGER DEFAULT 100,
            raw_data TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (client_id) REFERENCES clients (id)
        )
    """)

    cols_to_verify = [
        ("sales_source", "TEXT"),
        ("tiktok_ads_id", "TEXT"),
        ("twitter_ads_id", "TEXT"),
        ("pinterest_ads_id", "TEXT"),
        ("snapchat_ads_id", "TEXT"),
        ("chatgpt_ads_id", "TEXT"),
        ("reddit_ads_id", "TEXT"),
        ("crm_value_field", "TEXT"),
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
    cursor.execute("PRAGMA table_info(clients)")
    existing_cols = [c[1] for c in cursor.fetchall()]
    for col_name, col_type in cols_to_verify:
        if col_name not in existing_cols:
            cursor.execute(f"ALTER TABLE clients ADD COLUMN {col_name} {col_type}")

    cursor.execute("SELECT COUNT(*) FROM clients")
    if cursor.fetchone()[0] == 0:
        cursor.execute("""
            INSERT INTO clients (name, google_ads_customer_id, callrail_account_id, callrail_company_id, lead_gen_method, qualification_criteria, source_of_truth, email_provider, email_account, email_app_password, crm_deal_tags, crm_won_deal_tags, lead_count_rule, exclude_past_customers)
            VALUES 
            ('Golden State Solar', '123-456-7890', 'ACC-101', 'COM-201', 'both', 'C', 'hubspot', 'gmail', 'conversions@goldenstatesolar.com', 'app_pass_123', 'Appointment Booked, Proposal Sent', 'Closed Won, Contract Signed', 'all', 'NO'),
            ('Priority Plumbing', '987-654-3210', 'ACC-102', 'COM-202', 'phone', 'B', 'servicetitan', 'outlook', 'billing@priorityplumbing.com', 'app_pass_456', 'Dispatch Scheduled', 'Job Completed, Paid', 'all', 'NO'),
            ('Apex HVAC & Electric', '555-444-3333', 'ACC-103', 'COM-203', 'form', 'ai_rules', 'quickbooks', 'other', 'sales@apexhvac.com', 'app_pass_789', 'Invoice Created', 'Payment Received', 'all', 'NO')
        """)

    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] == 0:
        default_admin_pass = hash_password("admin123")
        cursor.execute("""
            INSERT INTO users (email, hashed_password, role, client_id)
            VALUES ('admin@leadgrove.net', ?, 'full', NULL)
        """, (default_admin_pass,))

    cursor.execute("SELECT COUNT(*) FROM webhook_logs")
    if cursor.fetchone()[0] == 0:
        cursor.execute("""
            INSERT INTO webhook_logs (client_id, source, event_type, status_code, payload_summary, error_message)
            VALUES 
            (1, 'CallRail', 'call_completed', 200, 'Inbound call from +1 (555) 234-5678 (Duration: 4m 12s, Transcript Audited)', NULL),
            (1, 'HubSpot CRM', 'deal.closed_won', 200, 'Closed Won Deal #8492 - Amount: $1,250.00 (Customer: John Doe)', NULL),
            (1, 'Website Form', 'form_submission', 200, 'Inbound Web Form POST - GCLID: Cj0KCQiA... (Email: j.doe@example.com)', NULL),
            (1, 'CallRail', 'call_completed', 200, 'Inbound call from +1 (555) 876-5432 (Duration: 1m 05s, Not Qualified)', NULL),
            (1, 'ServiceTitan', 'job.completed', 422, 'Job #9102 completed ($850.00) - Missing matching phone/email in click session DB', 'Identity Match Failed: Phone +15559998888 not found in last 90 days sessions')
        """)

    cursor.execute("SELECT COUNT(*) FROM unmatched_records")
    if cursor.fetchone()[0] == 0:
        cursor.execute("""
            INSERT INTO unmatched_records (client_id, record_type, customer_identifier, amount, source_system, reason, status)
            VALUES
            (1, 'sale', 'sarah.jenkins@gmail.com / +1 (555) 999-1234', 1450.00, 'HubSpot CRM Webhook', 'No matching click ID session found in last 90 days (Direct or Organic customer)', 'UNMATCHED'),
            (1, 'sale', '+1 (555) 444-5555 (Phone Typo)', 820.00, 'QuickBooks Invoice #INV-1092', 'Phone number not recognized in CallRail session database', 'UNMATCHED')
        """)

    conn.commit()
    conn.close()

def startup_db_init():
    try:
        init_db()
    except Exception as e:
        print(f"⚠️ Startup database initialization error: {e}")
