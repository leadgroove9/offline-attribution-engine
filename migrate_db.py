import sqlite3
import os

DB_PATH = "offline_attribution.db"

def migrate():
    print("--------------------------------------------------")
    print("🛠️ Starting Database Migration for Multi-Tenancy")
    print("--------------------------------------------------")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. Create the clients table
    print("📦 Creating 'clients' table...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            callrail_company_id TEXT,
            google_ads_customer_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 2. Insert default mock clients for the agency/SaaS trial
    print("➕ Inserting mock agency clients...")
    mock_clients = [
        ("Priority Plumbing", "comp_plumbing", "123-456-7890"),
        ("Apex HVAC & Air", "comp_hvac", "987-654-3210"),
        ("Metro Dental Care", "comp_dental", "555-123-4567")
    ]
    
    for name, cr_id, gads_id in mock_clients:
        try:
            cursor.execute("""
                INSERT INTO clients (name, callrail_company_id, google_ads_customer_id)
                VALUES (?, ?, ?)
            """, (name, cr_id, gads_id))
            print(f"   ✅ Added client: '{name}' (CallRail: {cr_id}, Google Ads: {gads_id})")
        except sqlite3.IntegrityError:
            print(f"   ℹ️ Client '{name}' already exists.")
            
    # 3. Create or Update the sessions table
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'")
    table_exists = cursor.fetchone()
    
    if table_exists:
        print("📝 Existing 'sessions' table found. Checking for 'client_id' column...")
        cursor.execute("PRAGMA table_info(sessions)")
        columns = [col[1] for col in cursor.fetchall()]
        
        if "client_id" not in columns:
            print("   ⚠️ Column 'client_id' is missing. Altering table to add 'client_id'...")
            # We add a default client_id pointing to the first client (Priority Plumbing = 1)
            cursor.execute("ALTER TABLE sessions ADD COLUMN client_id INTEGER DEFAULT 1 REFERENCES clients(id)")
            print("   ✅ Column 'client_id' successfully added to 'sessions' table!")
        else:
            print("   ✅ Column 'client_id' already exists in 'sessions' table.")
    else:
        print("📦 'sessions' table does not exist. Creating brand-new multi-tenant 'sessions' table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER DEFAULT 1 REFERENCES clients(id),
                phone TEXT NOT NULL,
                email TEXT,
                name TEXT,
                company TEXT,
                gclid TEXT,
                source TEXT, -- 'callrail', 'form', etc.
                qualified TEXT, -- 'YES' or 'NO' from Claude
                sale_closed TEXT, -- 'YES' or 'NO' from Claude
                value REAL DEFAULT 0.0, -- $ Value extracted by Claude
                reason TEXT, -- Claude's justification
                model_used TEXT, -- Claude model name
                raw_data TEXT, -- JSON payload for debugging
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        print("   ✅ Multi-tenant 'sessions' table created successfully!")
        
    conn.commit()
    conn.close()
    print("--------------------------------------------------")
    print("🎉 Migration Complete! Database is multi-tenant ready.")
    print("--------------------------------------------------")

if __name__ == "__main__":
    migrate()
