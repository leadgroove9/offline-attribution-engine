#!/usr/bin/env python3
"""
Multi-Tenant CallRail Daily Sync Utility (v11)
----------------------------------------------
Nightly cron schedule sync that pulls completed phone calls AND web form submissions
from CallRail, applies past customer exclusions (matching both phone and email for forms),
seeds unqualified form leads for email qualification, runs Claude audits on call transcripts,
and prevents database duplicates.
"""

import os
import sys
import json
import sqlite3
import re
import urllib.parse
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

# Target DB Path
DB_PATH = os.environ.get("DB_PATH", "offline_attribution.db")

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
            import sqlite3
            return sqlite3.connect("offline_attribution.db")

class MockSqlite3:
    def connect(self, *args, **kwargs):
        return DatabaseRouter.connect()

db_router = MockSqlite3()


# Qualification Criteria Definitions
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

# ---------------------------------------------------------
# UTILITIES & DATA NORMALIZATION
# ---------------------------------------------------------

def normalize_phone(phone_str: Optional[str]) -> str:
    """Normalizes phone numbers to standard 10 or 11-digit clean integers."""
    if not phone_str:
        return ""
    digits = re.sub(r"\D", "", str(phone_str))
    if len(digits) == 10:
        return "1" + digits
    return digits


def extract_param_from_url(url: str, param: str) -> Optional[str]:
    """Helper to extract a specific query parameter from a landing or referrer URL."""
    if not url:
        return None
    try:
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)
        val_list = params.get(param)
        return val_list[0] if val_list else None
    except Exception:
        return None


def check_is_excluded_customer(client_id: int, phone: str, email: str = "") -> Optional[str]:
    """
    Checks if a phone or email matches any record in the excluded_customers table.
    Returns 'Phone Match', 'Email Match', or None.
    """
    conn = db_router.connect()
    cursor = conn.cursor()
    
    normalized_p = normalize_phone(phone)
    clean_e = email.strip().lower() if email else ""
    
    try:
        if normalized_p:
            cursor.execute(
                "SELECT id FROM excluded_customers WHERE client_id = ? AND phone = ?", 
                (client_id, normalized_p)
            )
            if cursor.fetchone():
                return "Phone Match"
                
        if clean_e:
            cursor.execute(
                "SELECT id FROM excluded_customers WHERE client_id = ? AND email = ?", 
                (client_id, clean_e)
            )
            if cursor.fetchone():
                return "Email Match"
    except Exception as e:
        print(f"⚠️ Warning checking exclusions: {e}")
    finally:
        conn.close()
        
    return None


# ---------------------------------------------------------
# CLAUDE AI TRANSCRIPT AUDITING (HTTP/1.1 requests model)
# ---------------------------------------------------------

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
        
        value = 0.0
        sale_closed = "NO"
        if "$" in lower_t or "booked" in lower_t or "deposit" in lower_t:
            sale_closed = "YES"
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
# CALLRAIL API SYNC SCRAPERS (Calls & Forms)
# ---------------------------------------------------------

def fetch_callrail_logs_for_client(client_id: int, company_id: str, start_date: str, end_date: str, client_account_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Queries CallRail's live REST API for completed calls within the timeframe.
    """
    api_key = os.environ.get("CALLRAIL_API_KEY")
    account_id = client_account_id or os.environ.get("CALLRAIL_ACCOUNT_ID")
    
    if not api_key or not account_id:
        print(f"   ℹ️ [Mock Mode] Generating mock API logs for Client #{client_id} (No API key found)")
        return [
            {
                "id": f"cal_{client_id}_sync_1",
                "customer_name": "Harrison Ford",
                "customer_phone_number": "14155550912",
                "google_click_id": "gclid_sync_google_1234a",
                "landing_page_url": "https://solar-california.com/landing?gclid=gclid_sync_google_1234a",
                "start_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "transcript": (
                    "[00:04] Agent: Solar Solutions California. This is Brad.\n"
                    "[00:10] Caller: Yes, I saw your ad on Google. I need to book a site assessment for home solar installers.\n"
                    "[00:17] Agent: Outstanding. I can schedule our solar designer for tomorrow morning at 9 AM. Works?\n"
                    "[00:23] Caller: Perfect, mark me down. Thanks!\n"
                )
            },
            {
                "id": f"cal_{client_id}_sync_2",
                "customer_name": "Carrie Fisher",
                "customer_phone_number": "13105559812",
                "google_click_id": "",
                "landing_page_url": "https://solar-california.com/services?fbclid=fbclid_sync_meta_5678b",
                "start_time": (datetime.now() - timedelta(hours=4)).strftime("%Y-%m-%d %H:%M:%S"),
                "transcript": (
                    "[00:03] Agent: Solar Solutions California, how can we help you?\n"
                    "[00:09] Caller: Hi, my neighbors used your solar installation and I saw your Facebook promo. I'd like to schedule a survey.\n"
                    "[00:15] Agent: Excellent, we can send a designer out on Friday at 3:00 PM.\n"
                    "[00:21] Caller: That works. See you then!\n"
                )
            },
            {
                "id": f"cal_{client_id}_sync_3",
                "customer_name": "Repeat Customer Test",
                "customer_phone_number": "15550192831", 
                "google_click_id": "gclid_sync_repeat_9012c",
                "landing_page_url": "https://solar-california.com/landing?gclid=gclid_sync_repeat_9012c",
                "start_time": (datetime.now() - timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S"),
                "transcript": "[00:04] Agent: Solar Solutions. Caller: Hi, I'm calling about my existing account billing. Agent: Sure let me help."
            }
        ]

    import requests
    url = f"https://api.callrail.com/v3/a/{account_id}/calls.json"
    headers = {
        "Authorization": f"Token token={api_key}",
        "Content-Type": "application/json"
    }
    params = {
        "company_id": company_id,
        "start_date": start_date,
        "end_date": end_date,
        "per_page": 100,
        "fields": "gclid,fbclid,milestones,landing_page_url,transcription"
    }
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=15)
        if response.status_code != 200:
            print(f"   ❌ CallRail API Error (HTTP {response.status_code}): {response.text}")
            return []
            
        data = response.json()
        calls = data.get("calls", [])
        return calls
    except Exception as e:
        print(f"   ❌ CallRail Connection Exception: {e}")
        return []


def fetch_callrail_form_submissions_for_client(client_id: int, company_id: str, start_date: str, end_date: str, client_account_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Queries CallRail's live REST API for web form submissions.
    """
    api_key = os.environ.get("CALLRAIL_API_KEY")
    account_id = client_account_id or os.environ.get("CALLRAIL_ACCOUNT_ID")
    
    if not api_key or not account_id:
        print(f"   ℹ专 [Mock Mode] Generating mock Form Submissions for Client #{client_id}")
        return [
            {
                "id": f"form_{client_id}_sync_1",
                "customer_name": "Luke Skywalker",
                "customer_phone_number": "14155551111",
                "customer_email": "luke@rebelalliance.com",
                "google_click_id": "gclid_form_skywalker_777",
                "landing_page_url": "https://solar-california.com/quote-form?gclid=gclid_form_skywalker_777",
                "submitted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            },
            {
                "id": f"form_{client_id}_sync_2",
                "customer_name": "Princess Leia",
                "customer_phone_number": "13105552222",
                "customer_email": "leia@rebelalliance.com",
                "google_click_id": "",
                "landing_page_url": "https://solar-california.com/quote-form?fbclid=fbclid_form_leia_888",
                "submitted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            },
            {
                "id": f"form_{client_id}_sync_3",
                "customer_name": "Excluded Form Customer",
                "customer_phone_number": "15550192831", # matches standard mock exclusion list
                "customer_email": "exclude_me@test.com",
                "google_click_id": "gclid_form_exclude_333",
                "landing_page_url": "https://solar-california.com/quote-form?gclid=gclid_form_exclude_333",
                "submitted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        ]

    import requests
    url = f"https://api.callrail.com/v3/a/{account_id}/form_submissions.json"
    headers = {
        "Authorization": f"Token token={api_key}",
        "Content-Type": "application/json"
    }
    params = {
        "company_id": company_id,
        "start_date": start_date,
        "end_date": end_date,
        "per_page": 100,
        "fields": "gclid,fbclid,milestones,landing_page_url"
    }
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=15)
        if response.status_code != 200:
            print(f"   ❌ CallRail Form API Error (HTTP {response.status_code}): {response.text}")
            return []
            
        data = response.json()
        forms = data.get("form_submissions", [])
        return forms
    except Exception as e:
        print(f"   ❌ CallRail Form Connection Exception: {e}")
        return []


# ---------------------------------------------------------
# CORE SYNC ENGINE PIPELINE
# ---------------------------------------------------------

def execute_daily_sync():
    """Main function that maps clients, pulls logs, audits call/form records, and syncs databases."""
    print("=========================================================")
    print(f"🔄 CALLRAIL DAILY CRON SYNC STARTED AT: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=========================================================")
    
    if not os.path.exists(DB_PATH):
        print(f"❌ Error: Database file '{DB_PATH}' was not found. Please verify running directories.")
        sys.exit(1)
        
    conn = db_router.connect()
    cursor = conn.cursor()
    
    # Fetch All Active Clients who use CallRail tracking
    cursor.execute("""
        SELECT id, name, callrail_company_id, qualification_criteria, source_of_truth, exclude_past_customers, callrail_account_id 
        FROM clients 
        WHERE callrail_company_id IS NOT NULL AND callrail_company_id != ''
    """)
    clients = cursor.fetchall()
    
    if not clients:
        print("ℹ️ No active clients found with registered CallRail Company IDs. Skipping.")
        conn.close()
        return

    print(f"🏢 Found {len(clients)} active clients to scan.")
    
    now = datetime.now()
    start_date = (now - timedelta(days=2)).strftime("%Y-%m-%d")
    end_date = now.strftime("%Y-%m-%d")
    
    synced_calls_count = 0
    synced_forms_count = 0
    ignored_records_count = 0
    duplicate_records_count = 0
    
    for client_row in clients:
        client_id, name, company_id, crit_code, sot, exclude_past, client_account_id = client_row
        print(f"\n⚡ Processing Client #{client_id}: '{name}' (CallRail: {company_id})")
        
        criteria_wording = CRITERIA_MAP.get(crit_code, "Someone who books an appointment")
        
        # ---------------------------------------------------------
        # SECTION 1: PROCESSING PHONE CALLS
        # ---------------------------------------------------------
        print("   📞 Scanning completed call transcripts...")
        calls = fetch_callrail_logs_for_client(client_id, company_id, start_date, end_date, client_account_id)
        
        for call in calls:
            raw_phone = call.get("customer_phone_number") or call.get("caller_number")
            normalized_phone = normalize_phone(raw_phone)
            
            if not normalized_phone:
                continue
                
            created_at = call.get("start_time") or call.get("created_at") or now.strftime("%Y-%m-%d %H:%M:%S")
            try:
                dt_parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                created_at = dt_parsed.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
                
            # Duplicate Check
            cursor.execute(
                "SELECT id FROM sessions WHERE client_id = ? AND phone = ? AND created_at = ?", 
                (client_id, normalized_phone, created_at)
            )
            if cursor.fetchone():
                duplicate_records_count += 1
                continue
                
            # Extract click IDs (with robust Milestones block lookup)
            gclid = call.get("google_click_id") or call.get("gclid")
            fbclid = call.get("facebook_click_id") or call.get("fbclid")
            li_fat_id = call.get("linkedin_click_id") or call.get("li_fat_id")
            msclkid = call.get("microsoft_click_id") or call.get("msclkid")
            
            landing_url = call.get("landing_page_url") or ""
            referrer_url = call.get("referrer_url") or call.get("referring_url") or ""
            
            milestones = call.get("milestones")
            if isinstance(milestones, dict):
                for m_key, m_data in milestones.items():
                    if isinstance(m_data, dict):
                        if not gclid: gclid = m_data.get("gclid") or m_data.get("google_click_id")
                        if not fbclid: fbclid = m_data.get("fbclid") or m_data.get("facebook_click_id")
                        if not li_fat_id: li_fat_id = m_data.get("li_fat_id") or m_data.get("linkedin_click_id")
                        if not msclkid: msclkid = m_data.get("msclkid") or m_data.get("microsoft_click_id")
                        if not landing_url: landing_url = m_data.get("landing_page_url") or ""
                        if not referrer_url: referrer_url = m_data.get("referring_url") or m_data.get("referrer_url") or ""
            elif isinstance(milestones, list):
                for m_data in milestones:
                    if isinstance(m_data, dict):
                        if not gclid: gclid = m_data.get("gclid") or m_data.get("google_click_id")
                        if not fbclid: fbclid = m_data.get("fbclid") or m_data.get("facebook_click_id")
                        if not li_fat_id: li_fat_id = m_data.get("li_fat_id") or m_data.get("linkedin_click_id")
                        if not msclkid: msclkid = m_data.get("msclkid") or m_data.get("microsoft_click_id")
                        if not landing_url: landing_url = m_data.get("landing_page_url") or ""
                        if not referrer_url: referrer_url = m_data.get("referring_url") or m_data.get("referrer_url") or ""
            
            if not gclid: gclid = extract_param_from_url(landing_url, "gclid") or extract_param_from_url(referrer_url, "gclid")
            if not fbclid: fbclid = extract_param_from_url(landing_url, "fbclid") or extract_param_from_url(referrer_url, "fbclid")
            if not li_fat_id: li_fat_id = extract_param_from_url(landing_url, "li_fat_id") or extract_param_from_url(referrer_url, "li_fat_id")
            if not msclkid: msclkid = extract_param_from_url(landing_url, "msclkid") or extract_param_from_url(referrer_url, "msclkid")
                
            has_click_id = any([gclid, fbclid, li_fat_id, msclkid])
            raw_transcript = call.get("transcript") or call.get("transcription") or ""
            transcript = ""
            if isinstance(raw_transcript, str):
                transcript = raw_transcript
            elif isinstance(raw_transcript, list):
                segments = []
                for segment in raw_transcript:
                    if isinstance(segment, dict):
                        speaker = segment.get("speaker") or segment.get("role") or "Speaker"
                        text = segment.get("text") or segment.get("message") or ""
                        if text: segments.append(f"[{speaker}]: {text}")
                    elif isinstance(segment, str):
                        segments.append(segment)
                transcript = "\n".join(segments)
            elif isinstance(raw_transcript, dict):
                transcript = raw_transcript.get("text") or raw_transcript.get("transcription") or str(raw_transcript)
            
            caller_name = call.get("customer_name") or "Unknown Caller"
            
            # Apply Past Customer Exclusion Filter for Phone (by phone)
            is_excluded = False
            exclusion_reason = ""
            if exclude_past == "YES":
                match_type = check_is_excluded_customer(client_id, phone=normalized_phone)
                if match_type:
                    is_excluded = True
                    exclusion_reason = f"Session ignored: Caller phone matches your uploaded past customer list ({match_type})."
                    
            if is_excluded:
                qualified = "NO"
                sale_closed = "NO"
                value = 0.0
                reason = exclusion_reason
                model_used = "None"
                ignored_records_count += 1
                print(f"   🚫 [Exclusion Match] Call from {caller_name} ignored: {exclusion_reason}")
            elif not has_click_id:
                qualified = "NO"
                sale_closed = "NO"
                value = 0.0
                reason = "Ignored: Direct or organic search lead (no ad click ID detected)."
                model_used = "None"
                ignored_records_count += 1
            elif transcript.strip():
                print(f"   🧠 [Audit Triggered] Running Claude 4.5 Haiku audit for {caller_name}...")
                ai_result = analyze_transcript_with_claude(transcript, criteria_wording)
                qualified = ai_result.get("qualified", "NO")
                sale_closed = ai_result.get("sale_closed", "NO")
                value = float(ai_result.get("value", 0.0))
                reason = ai_result.get("reason", "No reason parsed.")
                model_used = "claude-haiku-4-5-20251001"
                synced_calls_count += 1
                print(f"      🎯 Result: Qualified={qualified}, Closed={sale_closed}, Value=${value:.2f}")
            else:
                qualified = "NO"
                sale_closed = "NO"
                value = 0.0
                reason = "Ignored: No audio transcript was compiled for this completed call."
                model_used = "None"
                ignored_records_count += 1
                
            # Insert Record
            raw_data_json = json.dumps({
                "customer_name": caller_name,
                "customer_phone_number": raw_phone,
                "call_id": call.get("id", ""),
                "transcript_snippet": transcript[:150] + "..." if transcript else ""
            })
            
            cursor.execute("""
                INSERT INTO sessions (
                    client_id, phone, name, gclid, fbclid, li_fat_id, msclkid, source, qualified, sale_closed, value, reason, model_used, raw_data, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                client_id, normalized_phone, caller_name, gclid or None, fbclid or None, li_fat_id or None, msclkid or None,
                "callrail", qualified, sale_closed, value, reason, model_used, raw_data_json, created_at
            ))

        # ---------------------------------------------------------
        # SECTION 2: PROCESSING FORM LEADS (Scraping & Exclusions)
        # ---------------------------------------------------------
        print("   📝 Scanning web form submission records...")
        forms = fetch_callrail_form_submissions_for_client(client_id, company_id, start_date, end_date, client_account_id)
        
        for form in forms:
            raw_phone = form.get("customer_phone_number") or form.get("phone_number")
            raw_email = form.get("customer_email") or form.get("email") or ""
            normalized_phone = normalize_phone(raw_phone)
            email_clean = raw_email.strip().lower()
            
            if not normalized_phone and not email_clean:
                continue
                
            created_at = form.get("submitted_at") or form.get("created_at") or now.strftime("%Y-%m-%d %H:%M:%S")
            try:
                dt_parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                created_at = dt_parsed.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
                
            # Duplicate check for forms
            cursor.execute(
                "SELECT id FROM sessions WHERE client_id = ? AND (phone = ? OR email = ?) AND created_at = ?", 
                (client_id, normalized_phone or "DUMMY_PHONE_VAL", email_clean or "DUMMY_EMAIL_VAL", created_at)
            )
            if cursor.fetchone():
                duplicate_records_count += 1
                continue
                
            # Extract click IDs for forms (milestones support)
            gclid = form.get("google_click_id") or form.get("gclid")
            fbclid = form.get("facebook_click_id") or form.get("fbclid")
            li_fat_id = form.get("linkedin_click_id") or form.get("li_fat_id")
            msclkid = form.get("microsoft_click_id") or form.get("msclkid")
            
            landing_url = form.get("landing_page_url") or ""
            referrer_url = form.get("referrer_url") or form.get("referring_url") or ""
            
            milestones = form.get("milestones")
            if isinstance(milestones, dict):
                for m_key, m_data in milestones.items():
                    if isinstance(m_data, dict):
                        if not gclid: gclid = m_data.get("gclid") or m_data.get("google_click_id")
                        if not fbclid: fbclid = m_data.get("fbclid") or m_data.get("facebook_click_id")
                        if not li_fat_id: li_fat_id = m_data.get("li_fat_id") or m_data.get("linkedin_click_id")
                        if not msclkid: msclkid = m_data.get("msclkid") or m_data.get("microsoft_click_id")
                        if not landing_url: landing_url = m_data.get("landing_page_url") or ""
                        if not referrer_url: referrer_url = m_data.get("referring_url") or m_data.get("referrer_url") or ""
            elif isinstance(milestones, list):
                for m_data in milestones:
                    if isinstance(m_data, dict):
                        if not gclid: gclid = m_data.get("gclid") or m_data.get("google_click_id")
                        if not fbclid: fbclid = m_data.get("fbclid") or m_data.get("facebook_click_id")
                        if not li_fat_id: li_fat_id = m_data.get("li_fat_id") or m_data.get("linkedin_click_id")
                        if not msclkid: msclkid = m_data.get("msclkid") or m_data.get("microsoft_click_id")
                        if not landing_url: landing_url = m_data.get("landing_page_url") or ""
                        if not referrer_url: referrer_url = m_data.get("referring_url") or m_data.get("referrer_url") or ""
            
            if not gclid: gclid = extract_param_from_url(landing_url, "gclid") or extract_param_from_url(referrer_url, "gclid")
            if not fbclid: fbclid = extract_param_from_url(landing_url, "fbclid") or extract_param_from_url(referrer_url, "fbclid")
            if not li_fat_id: li_fat_id = extract_param_from_url(landing_url, "li_fat_id") or extract_param_from_url(referrer_url, "li_fat_id")
            if not msclkid: msclkid = extract_param_from_url(landing_url, "msclkid") or extract_param_from_url(referrer_url, "msclkid")
            
            has_click_id = any([gclid, fbclid, li_fat_id, msclkid])
            caller_name = form.get("customer_name") or "Unknown Form Lead"
            
            # Apply Past Customer Exclusion Filter for Form Submission (matches both phone and email!)
            is_excluded = False
            exclusion_reason = ""
            if exclude_past == "YES":
                match_type = check_is_excluded_customer(client_id, phone=normalized_phone, email=email_clean)
                if match_type:
                    is_excluded = True
                    exclusion_reason = f"Form submission ignored: matches your uploaded past customer list ({match_type})."
            
            if is_excluded:
                qualified = "NO"
                sale_closed = "NO"
                value = 0.0
                reason = exclusion_reason
                ignored_records_count += 1
                print(f"   🚫 [Exclusion Match] Form from {caller_name} ignored: {exclusion_reason}")
            elif not has_click_id:
                qualified = "NO"
                sale_closed = "NO"
                value = 0.0
                reason = "Ignored: Direct or organic search lead (no ad click ID detected)."
                ignored_records_count += 1
            else:
                # Seeded for subsequent email/CRM qualification scan!
                qualified = "PENDING"
                sale_closed = "NO"
                value = 0.0
                reason = "Awaiting email confirmation audit."
                synced_forms_count += 1
                print(f"   ✓ [Form Seeded] Staged {caller_name} (Phone: {normalized_phone}, Email: {email_clean}) to dashboard. Status: PENDING.")
                
            # Insert Record as source = 'form'
            raw_data_json = json.dumps({
                "customer_name": caller_name,
                "customer_phone_number": raw_phone,
                "customer_email": raw_email,
                "form_id": form.get("id", "")
            })
            
            cursor.execute("""
                INSERT INTO sessions (
                    client_id, phone, email, name, gclid, fbclid, li_fat_id, msclkid, source, qualified, sale_closed, value, reason, model_used, raw_data, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                client_id, normalized_phone, email_clean, caller_name, gclid or None, fbclid or None, li_fat_id or None, msclkid or None,
                "form", qualified, sale_closed, value, reason, "None", raw_data_json, created_at
            ))
            
    conn.commit()
    conn.close()
    
    print("\n=========================================================")
    print("📈 DAILY SYNC COMPLETE")
    print(f"   - Staged & Audited Call Leads: {synced_calls_count}")
    print(f"   - Staged Pending Form Leads  : {synced_forms_count}")
    print(f"   - Ignored/Organic Leads      : {ignored_records_count}")
    print(f"   - Duplicates Prevented       : {duplicate_records_count}")
    print("=========================================================")

if __name__ == "__main__":
    execute_daily_sync()
