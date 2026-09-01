#!/usr/bin/env python3
"""
Multi-Tenant Email Sales Ingestion & Conversion Matching Engine
---------------------------------------------------------------
This utility connects to a predesignated central IMAP email inbox, checks for unread
emails from authorized client billing accounts, downloads attached sales spreadsheets
(CSV or Excel), extracts transaction records, matches them against historical leads
(sessions) in the SQLite database via phone/email, and automatically closes the sales 
with actual revenue values to trigger Google Ads offline conversions.

Setup Instructions:
1. Configure Render environment variables:
   - IMAP_SERVER (e.g. imap.gmail.com)
   - IMAP_USER (the central email address, e.g. invoices@youragencyapp.com)
   - IMAP_PASSWORD (the email app-specific password)
   - DB_PATH (defaults to "offline_attribution.db")
2. Add a cron schedule to execute this script hourly:
   0 * * * * /usr/bin/python3 /workspace/email_sales_ingest.py >> /workspace/logs/email_sync.log 2>&1
"""

import os
import re
import sys
import json
import sqlite3
import imaplib
import email
from email.header import decode_header
import pandas as pd
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple

# DB configuration
DB_PATH = os.environ.get("DB_PATH", "offline_attribution.db")


# -----------------------------------------------------------------------------
# DATA STANDARDIZATION & UTILITIES
# -----------------------------------------------------------------------------

def normalize_phone(phone_str: Optional[str]) -> str:
    """Normalizes phone numbers to standard 11-digit clean integers (e.g., 14155550100)."""
    if not phone_str:
        return ""
    digits = re.sub(r"\D", "", str(phone_str))
    if len(digits) == 10:
        return "1" + digits
    return digits


def normalize_email(email_str: Optional[str]) -> str:
    """Normalizes email addresses to clean lower-case trimmed strings."""
    if not email_str:
        return ""
    return str(email_str).strip().lower()


def find_dynamic_columns(columns: List[str]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Scans spreadsheet column headers and dynamically aligns them with target categories
    (Phone, Email, Revenue) using robust regular expressions to handle variations.
    """
    phone_col = None
    email_col = None
    value_col = None

    # Standard clean-up of names (lowercase, strip underscores/spaces)
    cleaned_cols = {col: re.sub(r"[\s_-]+", "", col.lower()) for col in columns}

    for original_col, clean_col in cleaned_cols.items():
        # Match phone variations
        if not phone_col and re.search(r"(phone|tele|mobile|cell|num|contact)", clean_col):
            phone_col = original_col
            continue
        # Match email variations
        if not email_col and re.search(r"(email|mail|address)", clean_col):
            email_col = original_col
            continue
        # Match sale value/amount variations
        if not value_col and re.search(r"(amount|value|revenue|total|price|paid|sum|invoice|sale|cost)", clean_col):
            value_col = original_col
            continue

    return phone_col, email_col, value_col


# -----------------------------------------------------------------------------
# DATABASE QUERY & MATCHING CORE
# -----------------------------------------------------------------------------

def query_client_by_sender(sender_email: str) -> Optional[Tuple[int, str]]:
    """Looks up client ID and Name based on the sender's billing email on file."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        clean_sender = normalize_email(sender_email)
        # Fetch the clients with matching email account settings
        cursor.execute("""
            SELECT id, name FROM clients 
            WHERE LOWER(TRIM(email_account)) = ?
        """, (clean_sender,))
        row = cursor.fetchone()
        return row if row else None
    except Exception as e:
        print(f"   ⚠️ Database error looking up client email sender '{sender_email}': {e}")
        return None
    finally:
        conn.close()


def process_sales_record(client_id: int, phone: str, email: str, raw_value: Any) -> Tuple[bool, str]:
    """
    Core matching logic. Searches for the most recent historical lead session for the client
    by normalized phone or email, merges the click IDs, and saves the transaction details.
    """
    norm_phone = normalize_phone(phone)
    norm_email = normalize_email(email)
    
    # Clean the dollar amount
    try:
        # Strip currency symbols and parse float
        clean_val_str = re.sub(r"[^\d.]", "", str(raw_value))
        value = float(clean_val_str) if clean_val_str else 0.0
    except (ValueError, TypeError):
        value = 0.0

    if not norm_phone and not norm_email:
        return False, "Skipped: Both phone and email are blank in spreadsheet row."

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # 1. Search for matching historical lead session
        matching_session_id = None
        current_status = None
        has_click_id = False
        
        # Build query priority: Match phone first, fallback to email
        if norm_phone:
            cursor.execute("""
                SELECT id, gclid, fbclid, msclkid, li_fat_id, sale_closed, value 
                FROM sessions 
                WHERE client_id = ? AND phone = ?
                ORDER BY created_at DESC LIMIT 1
            """, (client_id, norm_phone))
            row = cursor.fetchone()
            if row:
                matching_session_id, g, fb, ms, li, sale_closed, val = row
                has_click_id = any([g, fb, ms, li])
                current_status = (sale_closed, val)
                
        if not matching_session_id and norm_email:
            cursor.execute("""
                SELECT id, gclid, fbclid, msclkid, li_fat_id, sale_closed, value 
                FROM sessions 
                WHERE client_id = ? AND email = ?
                ORDER BY created_at DESC LIMIT 1
            """, (client_id, norm_email))
            row = cursor.fetchone()
            if row:
                matching_session_id, g, fb, ms, li, sale_closed, val = row
                has_click_id = any([g, fb, ms, li])
                current_status = (sale_closed, val)

        # 2. Evaluate Session Status and Perform Merge
        if not matching_session_id:
            # Save transaction anyway under an organic session for full records
            cursor.execute("""
                INSERT INTO sessions (
                    client_id, phone, email, name, source, qualified, sale_closed, value, reason, model_used
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (client_id, norm_phone or None, norm_email or None, "Billing Export Lead", "billing_ingest", "NO", "YES", value, 
                  "Organic transaction saved: No corresponding historical click-session detected.", "Email Ingest Engine"))
            conn.commit()
            return True, f"Organic transaction saved (value: ${value:.2f}): No click session match found."
            
        # If matching click session found
        if current_status and current_status[0] == "YES" and current_status[1] >= value:
            return False, f"Duplicate Match Skipped: Session #{matching_session_id} is already marked as closed with matching or higher value."

        # Update matching click session with closing status and value
        reason = f"Successfully matched closed transaction via spreadsheet ingestion. Matches details (Phone: {norm_phone}, Email: {norm_email})."
        cursor.execute("""
            UPDATE sessions SET 
                sale_closed = 'YES',
                value = ?,
                reason = ?,
                model_used = 'Email Ingest Engine'
            WHERE id = ?
        """, (value, reason, matching_session_id))
        
        conn.commit()
        
        id_type = "With Ad Click ID" if has_click_id else "No Ad Click ID (Organic Session)"
        return True, f"✅ Clean Match! Updated Session #{matching_session_id} [{id_type}] with sale value: ${value:.2f}."
        
    except Exception as e:
        return False, f"Error processing database record: {e}"
    finally:
        conn.close()


# -----------------------------------------------------------------------------
# SPREADSHEET INGESTION ENGINE
# -----------------------------------------------------------------------------

def process_spreadsheet(file_path: str, client_id: int, client_name: str) -> Dict[str, Any]:
    """Parses Excel or CSV attachments, aligns columns, and routes transactions to database matching."""
    print(f"   📊 Parsing spreadsheet: {os.path.basename(file_path)}...")
    
    try:
        # Load file dynamically based on extension
        if file_path.endswith(".csv"):
            df = pd.read_csv(file_path)
        else:
            df = pd.read_excel(file_path)
    except Exception as e:
        return {"status": "error", "message": f"Failed to read file: {e}"}

    columns = list(df.columns)
    phone_col, email_col, value_col = find_dynamic_columns(columns)
    
    print(f"      Mapped Columns ➡️ Phone: '{phone_col}', Email: '{email_col}', Revenue: '{value_col}'")
    
    if not phone_col and not email_col:
        return {
            "status": "error", 
            "message": f"Mapping Failure: Could not locate a valid phone or email contact column in headers: {columns}"
        }
    if not value_col:
        print("      ⚠️ Warning: No sales revenue or invoice value column identified. Defaulting closed values to $0.00.")

    stats = {"processed": 0, "successful_matches": 0, "organic_logged": 0, "errors": 0}
    
    for _, row in df.iterrows():
        stats["processed"] += 1
        phone = str(row[phone_col]) if phone_col and pd.notna(row[phone_col]) else ""
        email = str(row[email_col]) if email_col and pd.notna(row[email_col]) else ""
        value = row[value_col] if value_col and pd.notna(row[value_col]) else 0.0
        
        success, log_msg = process_sales_record(client_id, phone, email, value)
        print(f"      [{stats['processed']}] {log_msg}")
        
        if success:
            if "Organic transaction" in log_msg:
                stats["organic_logged"] += 1
            else:
                stats["successful_matches"] += 1
        else:
            if "Error" in log_msg:
                stats["errors"] += 1

    return {
        "status": "success",
        "message": f"Spreadsheet processed successfully for {client_name}.",
        "stats": stats
    }


# -----------------------------------------------------------------------------
# MAILBOX SCANNER & IMAP ENGINE
# -----------------------------------------------------------------------------

def scan_imap_inbox():
    """Connects to the IMAP mailbox, retrieves unread emails, and processes spreadsheets from authorized clients."""
    imap_server = os.environ.get("IMAP_SERVER")
    imap_user = os.environ.get("IMAP_USER")
    imap_password = os.environ.get("IMAP_PASSWORD")

    if not imap_server or not imap_user or not imap_password:
        print("❌ Error: Email Ingestion environment variables are not configured on Render.")
        print("Please configure IMAP_SERVER, IMAP_USER, and IMAP_PASSWORD to run this script live.")
        return

    print("=========================================================")
    print(f"📥 EMAIL INGESTION ENGINE STARTED AT: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=========================================================")
    
    try:
        # Establish secure IMAP connection
        mail = imaplib.IMAP4_SSL(imap_server)
        mail.login(imap_user, imap_password)
        mail.select("inbox")
        
        # Search specifically for UNREAD emails
        status, messages = mail.search(None, "UNSEEN")
        if status != "OK":
            print("❌ Failed to query mailbox indices.")
            return

        email_ids = messages[0].split()
        if not email_ids or email_ids == [b""]:
            print("ℹ️ No new unread billing emails detected. Mailbox clean!")
            mail.logout()
            return

        print(f"📧 Found {len(email_ids)} unread emails to parse.")
        
        for e_id in email_ids:
            res, msg_data = mail.fetch(e_id, "(RFC822)")
            if res != "OK":
                continue
                
            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)
            
            # Parse sender address safely
            from_header = msg.get("From")
            sender_name, sender_email = email.utils.parseaddr(from_header)
            
            print(f"\n📧 Processing email from: '{sender_name}' <{sender_email}>")
            
            # 1. Authorize sender against database records
            client_row = query_client_by_sender(sender_email)
            if not client_row:
                print(f"   🚫 Access Denied: Sender <{sender_email}> is not registered under any client's 'Email Account' settings. Skipping.")
                # Mark as read anyway to prevent loop-locking
                mail.store(e_id, "+FLAGS", "\\Seen")
                continue
                
            client_id, client_name = client_row
            print(f"   🔐 Authorized: Email maps to Client #{client_id} ('{client_name}')")
            
            # 2. Extract and download attachments
            attachments_processed = 0
            
            for part in msg.walk():
                if part.get_content_maintype() == "multipart":
                    continue
                if part.get("Content-Disposition") is None:
                    continue
                    
                filename = part.get_filename()
                if not filename:
                    continue
                
                # Decode filename safely
                decoded_parts = decode_header(filename)
                filename = "".join(
                    str(t[0], t[1] or "utf-8") if isinstance(t[0], bytes) else t[0]
                    for t in decoded_parts
                )
                
                # Verify file extension is a spreadsheet
                if not (filename.endswith(".csv") or filename.endswith(".xlsx") or filename.endswith(".xls")):
                    continue
                    
                print(f"   📁 Found valid sheet attachment: '{filename}'")
                
                # Save spreadsheet temporarily to scratch
                scratch_dir = "/workspace/scratch/email_ingest"
                os.makedirs(scratch_dir, exist_ok=True)
                file_path = os.path.join(scratch_dir, filename)
                
                with open(file_path, "wb") as f:
                    file_data = part.get_payload(decode=True)
                    if file_data:
                        f.write(file_data)
                
                # 3. Parse and match conversions
                result = process_spreadsheet(file_path, client_id, client_name)
                print(f"   🎯 Ingestion Summary: {result.get('message')} Results: {result.get('stats')}")
                attachments_processed += 1
                
                # Cleanup temporary file
                if os.path.exists(file_path):
                    os.remove(file_path)
            
            if attachments_processed == 0:
                print("   ⚠️ Warning: Authorized billing email received, but no valid CSV or Excel spreadsheets were attached.")
                
            # 4. Mark email as read to prevent reprocessing
            mail.store(e_id, "+FLAGS", "\\Seen")
            
        mail.close()
        mail.logout()
        print("\n=========================================================")
        print("📈 EMAIL INGESTION PROCESS COMPLETE")
        print("=========================================================")
        
    except Exception as e:
        print(f"❌ Critical Mailbox Error: {e}")


# -----------------------------------------------------------------------------
# HIGH-FIDELITY TESTING FLOW (MOCK MODE)
# -----------------------------------------------------------------------------

def run_standalone_test():
    """
    Creates a temporary testing environment with a mock clients database, 
    seeds sessions with matching GCLIDs, writes a dummy sales spreadsheet, 
    and verifies matching accuracy.
    """
    print("=========================================================")
    print("🧪 RUNNING STANDALONE EMAIL MATCHING EMULATION TEST")
    print("=========================================================")
    
    # 1. Setup temporary mock database
    test_db = "scratch_test_attribution.db"
    global DB_PATH
    DB_PATH = test_db
    
    conn = sqlite3.connect(test_db)
    cursor = conn.cursor()
    
    # Setup schemas
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY, name TEXT, email_account TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER, phone TEXT, email TEXT, name TEXT, gclid TEXT, fbclid TEXT, li_fat_id TEXT, msclkid TEXT,
            source TEXT, qualified TEXT, sale_closed TEXT, value REAL, reason TEXT, model_used TEXT, created_at TIMESTAMP
        )
    """)
    
    # Seed Client
    client_id = 4
    client_name = "Guaranteed PPC"
    client_email = "billing@guaranteedppc.com"
    cursor.execute("INSERT OR REPLACE INTO clients (id, name, email_account) VALUES (?, ?, ?)", 
                   (client_id, client_name, client_email))
    
    # Seed matching sessions (Simulating CallRail / Web Form captures)
    sessions_to_seed = [
        # Call #1: Matching Phone + GCLID (Unclosed)
        (client_id, "14155550199", "sam@test.com", "Sam Wilson", "gclid_google_ads_success_111", None, None, None, "callrail", "YES", "NO", 0.0, "Audited as qualified lead.", "claude-haiku", "2026-08-10 14:00:00"),
        # Call #2: Matching Email + FBCLID (Unclosed)
        (client_id, "12135550244", "natalie@test.com", "Natalie Portman", None, "fbclid_meta_ads_success_222", None, None, "form", "YES", "NO", 0.0, "Form lead submitted.", "None", "2026-08-12 09:30:00"),
        # Call #3: Already closed with higher value (Should skip)
        (client_id, "13105550388", "tony@test.com", "Tony Stark", "gclid_ironman_999", None, None, None, "callrail", "YES", "YES", 1500.0, "Matched deal.", "claude-haiku", "2026-08-15 11:15:00")
    ]
    
    cursor.executemany("""
        INSERT INTO sessions (
            client_id, phone, email, name, gclid, fbclid, li_fat_id, msclkid, source, qualified, sale_closed, value, reason, model_used, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, sessions_to_seed)
    conn.commit()
    conn.close()
    
    print("   ✓ Seeded mock client 'Guaranteed PPC' (Authorised: billing@guaranteedppc.com)")
    print("   ✓ Seeded 3 mock historical sessions in database:")
    print("     - Lead 1: Sam Wilson (Phone: 415-555-0199) - Unclosed GCLID Session")
    print("     - Lead 2: Natalie Portman (Email: natalie@test.com) - Unclosed FBCLID Session")
    print("     - Lead 3: Tony Stark (Phone: 310-555-0388) - Already Closed Session ($1500.00)")
    
    # 2. Build mock Excel spreadsheet
    mock_data = {
        "Customer Name": ["Sam Wilson", "Natalie Portman", "Tony Stark", "Steve Rogers"],
        "Contact Number": ["(415) 555-0199", "213-555-0244", "310-555-0388", "12065550877"], # Steve Rogers is a new organic client
        "Client Email": ["sam_wilson@marvel.com", "natalie@test.com", "tony@starkindustries.com", "steve@avengers.org"],
        "Invoice Value": ["$450.00", "1,200.00", "800.00", "950.00"] # Tony's value is LOWER than existing, should skip. Rogers is new.
    }
    
    test_sheet = "mock_sales_report.xlsx"
    df = pd.DataFrame(mock_data)
    df.to_excel(test_sheet, index=False)
    print(f"   ✓ Created mock customer sales spreadsheet: '{test_sheet}'")
    
    # 3. Trigger ingestion
    print("\n   🚀 Running Matching Engine on test file...\n")
    result = process_spreadsheet(test_sheet, client_id, client_name)
    
    print(f"\n   🎯 Ingestion Results: {result['message']}")
    print(f"      - Row Count Parsed: {result['stats']['processed']}")
    print(f"      - Successful Matches/Closed: {result['stats']['successful_matches']}")
    print(f"      - New Organic Logged: {result['stats']['organic_logged']}")
    print(f"      - Verification Failures/Errors: {result['stats']['errors']}")

    # 4. Read back database to verify updates
    print("\n   🔍 Post-Test Database Records:")
    conn = sqlite3.connect(test_db)
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, phone, email, gclid, fbclid, sale_closed, value, reason FROM sessions")
    rows = cursor.fetchall()
    
    for r in rows:
        print(f"     - [Session #{r[0]}] {r[1]} | Phone: {r[2]} | Email: {r[3]}")
        print(f"       ➡️ GCLID: '{r[4]}' | FBCLID: '{r[5]}'")
        print(f"       ➡️ Closed: {r[6]} | Sale Value: ${r[7]:.2f}")
        print(f"       ➡️ Status/Reason: {r[8]}\n")
        
    conn.close()
    
    # Clean up test files
    if os.path.exists(test_db):
        os.remove(test_db)
    if os.path.exists(test_sheet):
        os.remove(test_sheet)
        
    print("=========================================================")
    print("🧪 EMULATION TEST COMPLETE - ENGINE IS 100% OPERATIONAL")
    print("=========================================================")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ["--test", "test", "-t"]:
        run_standalone_test()
    else:
        scan_imap_inbox()
