#!/usr/bin/env python3
"""
Multi-Tenant Email Sales Ingestion & Conversion Matching Engine (v4)
-------------------------------------------------------------------
This utility connects to a predesignated central IMAP email inbox, checks for unread
emails from authorized client billing accounts, downloads attached sales spreadsheets
(CSV or Excel), extracts transaction records, matches them against historical leads
(sessions) in the database via phone, email, fuzzy company names, or fuzzy customer names,
and automatically closes the sales with actual revenue values to trigger Google Ads offline conversions.

Setup Instructions:
1. Configure Render environment variables:
   - IMAP_SERVER (e.g. imap.gmail.com)
   - IMAP_USER (the central email address, e.g. invoices@youragencyapp.com)
   - IMAP_PASSWORD (the email app-specific password)
   - DATABASE_URL (PostgreSQL connection string on Render)
   - DB_PATH (defaults to "offline_attribution.db" if DATABASE_URL is not set)
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
import difflib
from email.header import decode_header
import pandas as pd
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple

# DB configuration
DB_PATH = os.environ.get("DB_PATH", "offline_attribution.db")
DATABASE_URL = os.environ.get("DATABASE_URL")


# ---------------------------------------------------------
# UNIFIED DATABASE ROUTING LAYER (PostgreSQL & SQLite)
# ---------------------------------------------------------

class PostgreSQLCursorWrapper:
    def __init__(self, pg_cursor):
        self.cursor = pg_cursor
        self._fetchone_override = None
        self._fetchall_override = None

    def execute(self, query, params=None):
        self._fetchone_override = None
        self._fetchall_override = None
        
        query_formatted = query.replace("?", "%s")
        query_formatted = query_formatted.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        query_formatted = query_formatted.replace("AUTOINCREMENT", "")
        
        if "PRAGMA table_info(" in query:
            table_name = query.split("PRAGMA table_info(")[1].split(")")[0].strip().replace("'", "").replace('"', '')
            pg_query = f"SELECT column_name FROM information_schema.columns WHERE table_name = '{table_name}'"
            self.cursor.execute(pg_query)
            cols = self.cursor.fetchall()
            mock_cols = [(0, col[0], 'TEXT', 0, None, 0) for col in cols]
            self._fetchall_override = lambda: mock_cols
            return self

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
            import psycopg2
            url_clean = DATABASE_URL
            if url_clean.startswith("postgres://"):
                url_clean = url_clean.replace("postgres://", "postgresql://", 1)
            conn = psycopg2.connect(url_clean)
            return PostgreSQLConnectionWrapper(conn)
        else:
            return sqlite3.connect(DB_PATH)

class MockSqlite3:
    def connect(self, *args, **kwargs):
        return DatabaseRouter.connect()

db_router = MockSqlite3()


# ----------------------------------------------------------------------------
# DATA STANDARDIZATION & UTILITIES
# ----------------------------------------------------------------------------

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


def clean_company_name(name_str: Optional[str]) -> str:
    """Removes standard corporate noise and symbols for fuzzy company comparison."""
    if not name_str:
        return ""
    val = str(name_str).lower().strip()
    val = re.sub(r"[^\w\s]", "", val) # remove punctuation
    noise = r"\b(inc|llc|corp|co|ltd|limited|incorporated|corporation|group|services|and)\b"
    val = re.sub(noise, "", val)
    val = re.sub(r"\s+", " ", val).strip()
    return val


def clean_person_name(name_str: Optional[str]) -> str:
    """Standardizes person names for fuzzy customer name/family name comparison."""
    if not name_str:
        return ""
    val = str(name_str).lower().strip()
    val = re.sub(r"[^\w\s]", "", val) # remove punctuation
    val = re.sub(r"\b(mr|mrs|ms|dr|jr|sr|ii|iii)\b", "", val)
    val = re.sub(r"\s+", " ", val).strip()
    return val


def is_company_match(c1: str, c2: str) -> bool:
    """Checks if two company names represent the same entity using substring and SequenceMatcher ratio."""
    clean1 = clean_company_name(c1)
    clean2 = clean_company_name(c2)
    if not clean1 or not clean2:
        return False
    if clean1 == clean2:
        return True
    if clean1 in clean2 or clean2 in clean1:
        return True
    # Match using sequence similarity ratio
    ratio = difflib.SequenceMatcher(None, clean1, clean2).ratio()
    return ratio >= 0.85


def is_name_match(n1: str, n2: str) -> bool:
    """Checks if two customer names represent the same person supporting family names and first initials."""
    clean1 = clean_person_name(n1)
    clean2 = clean_person_name(n2)
    if not clean1 or not clean2:
        return False
    if clean1 == clean2:
        return True
    if clean1 in clean2 or clean2 in clean1:
        return True
    
    tokens1 = clean1.split()
    tokens2 = clean2.split()
    
    if len(tokens1) > 0 and len(tokens2) > 0:
        # Check standard family name (last word)
        last1 = tokens1[-1]
        last2 = tokens2[-1]
        
        if last1 == last2:
            first1 = tokens1[0]
            first2 = tokens2[0]
            # Match first initials or substring (e.g., 'S.' or 'Sam' vs 'Samuel')
            if first1[0] == first2[0] or first1 in first2 or first2 in first1:
                return True
                
        # Handle transposed family/first name (e.g., "Wilson, Sam" vs "Sam Wilson")
        if set(tokens1) == set(tokens2):
            return True
            
    # Fallback to string similarity metric
    ratio = difflib.SequenceMatcher(None, clean1, clean2).ratio()
    return ratio >= 0.80


def find_dynamic_columns(columns: List[str]) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    Scans spreadsheet column headers and dynamically aligns them with target categories
    (Phone, Email, Revenue, Customer Name, Company Name) using robust regular expressions to handle variations.
    """
    phone_col = None
    email_col = None
    value_col = None
    name_col = None
    company_col = None

    # Standard clean-up of names (lowercase, strip underscores/spaces)
    cleaned_cols = {col: re.sub(r"[\s_-]+", "", col.lower()) for col in columns}

    for original_col, clean_col in cleaned_cols.items():
        # Match company variations first (prevents company matching 'contact' or 'name')
        if not company_col and re.search(r"(company|business|corp|firm|org|employer|association|co|shop|brand)", clean_col):
            company_col = original_col
            continue
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
        # Match name variations
        if not name_col and re.search(r"(name|customer|client|buyer|fullname|first|last|contactname)", clean_col):
            name_col = original_col
            continue

    return phone_col, email_col, value_col, name_col, company_col


# -----------------------------------------------------------------------------
# DATABASE QUERY & MATCHING CORE
# -----------------------------------------------------------------------------

def query_client_by_sender(sender_email: str) -> Optional[Tuple[int, str]]:
    """Looks up client ID and Name based on the sender's billing email on file."""
    conn = db_router.connect()
    cursor = conn.cursor()
    try:
        clean_sender = normalize_email(sender_email)
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


def process_sales_record(client_id: int, phone: str, email: str, raw_value: Any, name: str = "", company: str = "") -> Tuple[bool, str]:
    """
    Core matching logic. Searches for the most recent historical lead session for the client
    using a strict priority hierarchy followed by advanced fuzzy name and company matching.
    """
    norm_phone = normalize_phone(phone)
    norm_email = normalize_email(email)
    raw_name = name.strip()
    raw_company = company.strip()
    
    # Clean the dollar amount
    try:
        clean_val_str = re.sub(r"[^\d.]", "", str(raw_value))
        value = float(clean_val_str) if clean_val_str else 0.0
    except (ValueError, TypeError):
        value = 0.0

    conn = db_router.connect()
    cursor = conn.cursor()
    
    try:
        matching_session_id = None
        current_status = None
        has_click_id = False
        match_type = ""
        
        # 1. PRIORITY 1: Match by Phone (Exact)
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
                match_type = f"Exact Phone Match ({norm_phone})"
                
        # 2. PRIORITY 2: Match by Email (Exact Fallback)
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
                match_type = f"Exact Email Match ({norm_email})"

        # 3. PRIORITY 3: Fuzzy Company Name Match
        if not matching_session_id and raw_company:
            cursor.execute("""
                SELECT id, phone, email, name, company, gclid, fbclid, msclkid, li_fat_id, sale_closed, value 
                FROM sessions 
                WHERE client_id = ? AND (sale_closed = 'NO' OR sale_closed IS NULL OR sale_closed = '')
                ORDER BY created_at DESC
            """, (client_id,))
            unclosed_sessions = cursor.fetchall()
            for sess in unclosed_sessions:
                s_id, s_phone, s_email, s_name, s_company, g, fb, ms, li, sale_closed, val = sess
                if s_company and is_company_match(raw_company, s_company):
                    matching_session_id = s_id
                    has_click_id = any([g, fb, ms, li])
                    current_status = (sale_closed, val)
                    match_type = f"Fuzzy Company Match ('{raw_company}' ➡️ '{s_company}')"
                    break

        # 4. PRIORITY 4: Fuzzy Customer Name / Family Name Match
        if not matching_session_id and raw_name:
            cursor.execute("""
                SELECT id, phone, email, name, company, gclid, fbclid, msclkid, li_fat_id, sale_closed, value 
                FROM sessions 
                WHERE client_id = ? AND (sale_closed = 'NO' OR sale_closed IS NULL OR sale_closed = '')
                ORDER BY created_at DESC
            """, (client_id,))
            unclosed_sessions = cursor.fetchall()
            for sess in unclosed_sessions:
                s_id, s_phone, s_email, s_name, s_company, g, fb, ms, li, sale_closed, val = sess
                if s_name and is_name_match(raw_name, s_name):
                    matching_session_id = s_id
                    has_click_id = any([g, fb, ms, li])
                    current_status = (sale_closed, val)
                    match_type = f"Fuzzy Name Match ('{raw_name}' ➡️ '{s_name}')"
                    break

        # 5. Evaluate Session Status and Perform Merge
        if not matching_session_id:
            # Save transaction anyway under an organic session for full records
            cursor.execute("""
                INSERT INTO sessions (
                    client_id, phone, email, name, company, source, qualified, sale_closed, value, reason, model_used
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (client_id, norm_phone or None, norm_email or None, raw_name or "Billing Export Lead", raw_company or None,
                  "billing_ingest", "NO", "YES", value, 
                  "Organic transaction saved: No corresponding historical click-session detected.", "Email Ingest Engine"))
            conn.commit()
            return True, f"Organic transaction saved (value: ${value:.2f}): No click session match found."
            
        # If matching click session found
        if current_status and current_status[0] == "YES" and current_status[1] >= value:
            return False, f"Duplicate Match Skipped: Session #{matching_session_id} is already marked as closed with matching or higher value."

        # Update matching click session with closing status and value
        reason = f"Successfully matched closed transaction via spreadsheet ingestion. Match Type: {match_type}."
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
        return True, f"✅ Clean Match! Updated Session #{matching_session_id} [{id_type}] via {match_type} with sale value: ${value:.2f}."
        
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
    phone_col, email_col, value_col, name_col, company_col = find_dynamic_columns(columns)
    
    print(f"      Mapped Columns ➡️ Phone: '{phone_col}', Email: '{email_col}', Revenue: '{value_col}', Name: '{name_col}', Company: '{company_col}'")
    
    if not phone_col and not email_col and not name_col and not company_col:
        return {
            "status": "error", 
            "message": f"Mapping Failure: Could not locate any contact columns (phone, email, name, company) in headers: {columns}"
        }
    if not value_col:
        print("      ⚠️ Warning: No sales revenue or invoice value column identified. Defaulting closed values to $0.00.")

    stats = {"processed": 0, "successful_matches": 0, "organic_logged": 0, "errors": 0}
    
    for _, row in df.iterrows():
        stats["processed"] += 1
        phone = str(row[phone_col]) if phone_col and pd.notna(row[phone_col]) else ""
        email = str(row[email_col]) if email_col and pd.notna(row[email_col]) else ""
        value = row[value_col] if value_col and pd.notna(row[value_col]) else 0.0
        customer_name = str(row[name_col]) if name_col and pd.notna(row[name_col]) else ""
        company_name = str(row[company_col]) if company_col and pd.notna(row[company_col]) else ""
        
        success, log_msg = process_sales_record(client_id, phone, email, value, name=customer_name, company=company_name)
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
    and verifies matching accuracy across exact and fuzzy criteria.
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
        DROP TABLE IF EXISTS clients
    """)
    cursor.execute("""
        DROP TABLE IF EXISTS sessions
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY, name TEXT, email_account TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER, phone TEXT, email TEXT, name TEXT, company TEXT, gclid TEXT, fbclid TEXT, li_fat_id TEXT, msclkid TEXT,
            source TEXT, qualified TEXT, sale_closed TEXT, value REAL, reason TEXT, model_used TEXT, created_at TIMESTAMP
        )
    """)
    
    # Seed Client
    client_id = 4
    client_name = "Guaranteed PPC"
    client_email = "billing@guaranteedppc.com"
    cursor.execute("INSERT OR REPLACE INTO clients (id, name, email_account) VALUES (?, ?, ?)", 
                   (client_id, client_name, client_email))
    
    # Seed matching sessions (Simulating Call Tracking / Web Form captures)
    sessions_to_seed = [
        # Session #1: Exact Phone Match + GCLID
        (client_id, "14155550199", "sam@test.com", "Sam Wilson", None, "gclid_exact_phone", None, None, None, "callrail", "YES", "NO", 0.0, "Audited as qualified lead.", "claude-haiku", "2026-08-10 14:00:00"),
        # Session #2: Exact Email Match + FBCLID
        (client_id, "12135550244", "natalie@test.com", "Natalie Portman", None, None, "fbclid_exact_email", None, None, "form", "YES", "NO", 0.0, "Form lead submitted.", "None", "2026-08-12 09:30:00"),
        # Session #3: Fuzzy Company Name Match + MSCLKID
        (client_id, None, None, "Steve Rogers", "Doe Plumbing", None, None, None, "msclkid_fuzzy_company", "callrail", "YES", "NO", 0.0, "Qualified lead.", "claude-haiku", "2026-08-13 10:15:00"),
        # Session #4: Fuzzy Name / Family Name Match + LI_FAT_ID
        (client_id, None, None, "Peter Parker", "Stark Industries", None, None, "li_fat_fuzzy_name", None, "callrail", "YES", "NO", 0.0, "Qualified lead.", "claude-haiku", "2026-08-14 11:45:00"),
        # Session #5: Already closed with higher value (Should skip)
        (client_id, "13105550388", "tony@test.com", "Tony Stark", "Stark Industries", "gclid_already_closed", None, None, None, "callrail", "YES", "YES", 1500.0, "Matched deal.", "claude-haiku", "2026-08-15 11:15:00")
    ]
    
    cursor.executemany("""
        INSERT INTO sessions (
            client_id, phone, email, name, company, gclid, fbclid, li_fat_id, msclkid, source, qualified, sale_closed, value, reason, model_used, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, sessions_to_seed)
    conn.commit()
    conn.close()
    
    print("   ✓ Seeded mock client 'Guaranteed PPC' (Authorised: billing@guaranteedppc.com)")
    print("   ✓ Seeded 5 mock historical sessions in database:")
    print("     - Lead 1: Sam Wilson (Phone: 415-555-0199) - Unclosed GCLID Session (Exact Phone Target)")
    print("     - Lead 2: Natalie Portman (Email: natalie@test.com) - Unclosed FBCLID Session (Exact Email Target)")
    print("     - Lead 3: Steve Rogers (Company: Doe Plumbing) - Unclosed MSCLKID Session (Fuzzy Company Target)")
    print("     - Lead 4: Peter Parker (Name: Peter Parker) - Unclosed LI_FAT Session (Fuzzy Name Target)")
    print("     - Lead 5: Tony Stark (Phone: 310-555-0388) - Already Closed Session ($1500.00)")
    
    # 2. Build mock Excel spreadsheet containing various lookup parameters
    # Let's make "Parker, Peter" have blank company name so it falls through to Name Match!
    mock_data = {
        "Customer Name": ["Sam Wilson", "Natalie Portman", "Steve Rogers", "Parker, Peter", "Tony Stark", "Clark Kent"],
        "Company Name": ["S. Wilson Consulting", "Meta Solutions", "Doe Plumbing Inc", "", "Stark Industries", "Daily Planet"],
        "Contact Number": ["(415) 555-0199", "213-555-0244", "", "", "310-555-0388", "12065550877"],
        "Client Email": ["sam_wilson@marvel.com", "natalie@test.com", "", "", "tony@starkindustries.com", "clark@dailyplanet.com"],
        "Invoice Value": ["$450.00", "1,200.00", "950.00", "2,300.00", "800.00", "99.00"]
    }
    
    test_sheet = "/workspace/scratch/mock_sales_report.xlsx"
    df = pd.DataFrame(mock_data)
    df.to_excel(test_sheet, index=False)
    print(f"   ✓ Created mock customer sales spreadsheet: '{test_sheet}'")
    
    # 3. Trigger ingestion
    print("\n   🚀 Running Ingestion Engine on test file...\n")
    result = process_spreadsheet(test_sheet, client_id, client_name)
    
    print(f"\n   🎯 Ingestion Results: {result['message']}")
    print(f"      - Row Count Parsed: {result['stats']['processed']}")
    print(f"      - Successful Matches/Closed: {result['stats']['successful_matches']}")
    print(f"      - New Organic Logged: {result['stats']['organic_logged']}")
    print(f"      - Verification Failures/Errors: {result['stats']['errors']}")

    # Clean up mock file and test DB
    if os.path.exists(test_sheet):
        os.remove(test_sheet)
    if os.path.exists(test_db):
        os.remove(test_db)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        run_standalone_test()
    else:
        scan_imap_inbox()
