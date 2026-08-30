import sqlite3

DB_PATH = "offline_attribution.db"

def migrate():
    print("--------------------------------------------------")
    print("🛠️ Starting Database Migration V2: Schema Upgrades")
    print("--------------------------------------------------")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Check if 'clients' table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='clients';")
    if not cursor.fetchone():
        print("❌ Error: 'clients' table does not exist. Please run migrate_db.py first to initialize your base database.")
        conn.close()
        return

    # Columns we need to add to 'clients' table for the onboarding wizard
    new_columns = [
        ("lead_gen_method", "TEXT"),
        ("qualification_criteria", "TEXT"),
        ("source_of_truth", "TEXT"),
        ("email_provider", "TEXT"),
        ("email_account", "TEXT"),
        ("crm_deal_tags", "TEXT"),
        ("crm_lead_tags", "TEXT"),
        ("lead_count_rule", "TEXT"),
        ("exclude_past_customers", "TEXT")
    ]
    
    # Get existing columns in 'clients' table
    cursor.execute("PRAGMA table_info(clients)")
    existing_cols = [col[1] for col in cursor.fetchall()]
    
    for col_name, col_type in new_columns:
        if col_name not in existing_cols:
            try:
                cursor.execute(f"ALTER TABLE clients ADD COLUMN {col_name} {col_type}")
                print(f"   ✅ Column '{col_name}' successfully added to 'clients' table.")
            except Exception as e:
                print(f"   ❌ Error adding column '{col_name}': {e}")
        else:
            print(f"   ℹ️ Column '{col_name}' already exists.")
            
    conn.commit()
    conn.close()
    print("--------------------------------------------------")
    print("🎉 Schema Migration V2 Complete!")
    print("--------------------------------------------------")

if __name__ == "__main__":
    migrate()
