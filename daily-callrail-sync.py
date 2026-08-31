#!/usr/bin/env python3
"""
Multi-Tenant CallRail Daily Sync Utility
---------------------------------------
This script is designed to run on a nightly cron schedule (e.g., at 2:00 AM daily).
It scans all active agency clients in your multi-tenant database, fetches their 
CallRail phone transcripts and click IDs from the CallRail API for the past 24-48 hours, 
runs duplicate prevention checks, applies customer exclusions, performs Claude AI 
qualification audits, and saves the verified conversion logs to your SQLite database.

Setup Instructions:
1. Ensure your Render environment variables contain:
   - ANTHROPIC_API_KEY (for Claude 4.5 Haiku audits)
   - CALLRAIL_API_KEY (for fetching real transcripts)
   - CALLRAIL_ACCOUNT_ID (for API endpoint targeting)
2. Add a cron job to execute this script daily:
   0 2 * * * /usr/bin/python3 /workspace/daily_callrail_sync.py >> /workspace/logs/cron_sync.log 2>&1
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
    conn = sqlite3.connect(DB_PATH)
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
# CLAUDE AI TRANSCRIPT AUDITING
# ---------------------------------------------------------

_anthropic_client = None

def get_anthropic_client(api_key: str):
    global _anthropic_client
    if _anthropic_client is None:
        from anthropic import Anthropic
        _anthropic_client = Anthropic(api_key=api_key, max_retries=3, timeout=30.0)
    return _anthropic_client

def analyze_transcript_with_claude(transcript: str, criteria_desc: str) -> Dict[str, Any]:
    """
    Calls Anthropic Claude to audit the call transcript based on custom rules.
    If no API key is set, returns simulated outcomes based on keywords.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
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
            "reason": f"Simulated Audit: Detected qualification signals aligning with standard: '{criteria_desc}'."
        }

    try:
        client = get_anthropic_client(api_key)
        
        system_prompt = (
            "You are an expert sales auditor and conversion tracking engine for local service businesses.\n"
            "Your job is to read a transcript (phone call or email log) and determine three things:\n"
            f"1. Is the lead a 'Qualified Lead'? For this business, a qualified lead is defined as: \"{criteria_desc}\". Return 'YES' or 'NO' based strictly on this custom threshold.\n"
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

        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            system=system_prompt,
            messages=[
                {"role": "user", "content": f"Analyze this transcript:\n\n{transcript}"}
            ]
        )
        
        response_text = message.content[0].text.strip()
        # Clean any accidental markdown wrap
        if response_text.startswith("```"):
            response_text = re.sub(r"^```(?:json)?\n|```$", "", response_text, flags=re.MULTILINE).strip()
            
        result = json.loads(response_text)
        return {
            "qualified": str(result.get("qualified", "NO")).upper(),
            "sale_closed": str(result.get("sale_closed", "NO")).upper(),
            "value": float(result.get("value", 0.0)),
            "reason": str(result.get("reason", "No reason provided."))
        }
    except Exception as e:
        return {
            "qualified": "NO",
            "sale_closed": "NO",
            "value": 0.0,
            "reason": f"Claude API Sync Error: {str(e)}"
        }


# ---------------------------------------------------------
# CALLRAIL API SYNC SCRAPER
# ---------------------------------------------------------

def fetch_callrail_logs_for_client(client_id: int, company_id: str, start_date: str, end_date: str, client_account_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Queries CallRail's live REST API for completed calls within the timeframe.
    If no CALLRAIL_API_KEY environment variable is configured, falls back 
    to generating realistic daily mock logs for testing.
    """
    api_key = os.environ.get("CALLRAIL_API_KEY")
    account_id = client_account_id or os.environ.get("CALLRAIL_ACCOUNT_ID")
    
    if not api_key or not account_id:
        print(f"   ℹ️ [Mock Mode] Generating mock API logs for Client #{client_id} (No API key found)")
        # Return realistic, daily sync mock logs containing transcripts and ad clicks
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
                "customer_phone_number": "15550192831", # Matches our standard mock exclusion list caller
                "google_click_id": "gclid_sync_repeat_9012c",
                "landing_page_url": "https://solar-california.com/landing?gclid=gclid_sync_repeat_9012c",
                "start_time": (datetime.now() - timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S"),
                "transcript": "[00:04] Agent: Solar Solutions. Caller: Hi, I'm calling about my existing account billing. Agent: Sure let me help."
            }
        ]

    # Live CallRail API Integration
    # Endpoint: GET https://api.callrail.com/v3/a/{account_id}/calls.json
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


# ---------------------------------------------------------
# CORE SYNC ENGINE PIPELINE
# ---------------------------------------------------------

def execute_daily_sync():
    """Main function that maps clients, pulls logs, audits call records, and syncs databases."""
    print("=========================================================")
    print(f"🔄 CALLRAIL DAILY CRON SYNC STARTED AT: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=========================================================")
    
    if not os.path.exists(DB_PATH):
        print(f"❌ Error: Database file '{DB_PATH}' was not found. Please verify running directories.")
        sys.exit(1)
        
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. Fetch All Active Clients who use CallRail tracking
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
    
    # Define time windows (past 48 hours to ensure zero gaps in case of server delay)
    now = datetime.now()
    start_date = (now - timedelta(days=2)).strftime("%Y-%m-%d")
    end_date = now.strftime("%Y-%m-%d")
    
    synced_records_count = 0
    ignored_records_count = 0
    duplicate_records_count = 0
    
    for client_row in clients:
        client_id, name, company_id, crit_code, sot, exclude_past, client_account_id = client_row
        print(f"\n⚡ Processing Client #{client_id}: '{name}' (CallRail: {company_id})")
        
        # Get custom criteria wording
        criteria_wording = CRITERIA_MAP.get(crit_code, "Someone who books an appointment")
        
        # Fetch calls
        calls = fetch_callrail_logs_for_client(client_id, company_id, start_date, end_date, client_account_id)
        print(f"   ✓ Fetched {len(calls)} potential logs for processing.")
        
        for call in calls:
            raw_phone = call.get("customer_phone_number") or call.get("caller_number")
            normalized_phone = normalize_phone(raw_phone)
            
            if not normalized_phone:
                continue
                
            created_at = call.get("start_time") or call.get("created_at") or now.strftime("%Y-%m-%d %H:%M:%S")
            # Reformat to standard datetime string
            try:
                dt_parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                created_at = dt_parsed.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass # Keep original string if parsing is tricky
                
            # 1. Duplicate Check (Deduplication Layer)
            cursor.execute(
                "SELECT id FROM sessions WHERE client_id = ? AND phone = ? AND created_at = ?", 
                (client_id, normalized_phone, created_at)
            )
            if cursor.fetchone():
                duplicate_records_count += 1
                continue
                
            # 2. Extract Webhook Variables and Click IDs (with robust Milestones block lookup)
            gclid = call.get("google_click_id") or call.get("gclid")
            fbclid = call.get("facebook_click_id") or call.get("fbclid")
            li_fat_id = call.get("linkedin_click_id") or call.get("li_fat_id")
            msclkid = call.get("microsoft_click_id") or call.get("msclkid")
            
            landing_url = call.get("landing_page_url") or ""
            referrer_url = call.get("referrer_url") or call.get("referring_url") or ""
            
            # Fallback to milestones block if top-level fields are missing
            milestones = call.get("milestones")
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
                        if not landing_url:
                            landing_url = m_data.get("landing_page_url") or ""
                        if not referrer_url:
                            referrer_url = m_data.get("referring_url") or m_data.get("referrer_url") or ""
            elif isinstance(milestones, list):
                for m_data in milestones:
                    if isinstance(m_data, dict):
                        if not gclid:
                            gclid = m_data.get("gclid") or m_data.get("google_click_id")
                        if not fbclid:
                            fbclid = m_data.get("fbclid") or m_data.get("facebook_click_id")
                        if not li_fat_id:
                            li_fat_id = m_data.get("li_fat_id") or m_data.get("linkedin_click_id")
                        if not msclkid:
                            msclkid = m_data.get("msclkid") or m_data.get("microsoft_click_id")
                        if not landing_url:
                            landing_url = m_data.get("landing_page_url") or ""
                        if not referrer_url:
                            referrer_url = m_data.get("referring_url") or m_data.get("referrer_url") or ""
            
            if not gclid:
                gclid = extract_param_from_url(landing_url, "gclid") or extract_param_from_url(referrer_url, "gclid")
            if not fbclid:
                fbclid = extract_param_from_url(landing_url, "fbclid") or extract_param_from_url(referrer_url, "fbclid")
            if not li_fat_id:
                li_fat_id = extract_param_from_url(landing_url, "li_fat_id") or extract_param_from_url(referrer_url, "li_fat_id")
            if not msclkid:
                msclkid = extract_param_from_url(landing_url, "msclkid") or extract_param_from_url(referrer_url, "msclkid")
                
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
                        if text:
                            segments.append(f"[{speaker}]: {text}")
                    elif isinstance(segment, str):
                        segments.append(segment)
                transcript = "\n".join(segments)
            elif isinstance(raw_transcript, dict):
                transcript = raw_transcript.get("text") or raw_transcript.get("transcription") or str(raw_transcript)
            caller_name = call.get("customer_name") or "Unknown Caller"
            
            # 3. Apply Past Customer Exclusion Filter
            is_excluded = False
            exclusion_reason = ""
            if exclude_past == "YES":
                match_type = check_is_excluded_customer(client_id, phone=normalized_phone)
                if match_type:
                    is_excluded = True
                    exclusion_reason = f"Session ignored: Caller phone matches your uploaded past customer list ({match_type})."
                    
            # 4. Routing Ratings
            if is_excluded:
                qualified = "NO"
                sale_closed = "NO"
                value = 0.0
                reason = exclusion_reason
                model_used = "None"
                ignored_records_count += 1
                print(f"   🚫 [Exclusion Match] Call from {caller_name} ignored: {exclusion_reason}")
            elif not has_click_id:
                # Organic lead, we save it but skip Claude audit to save tokens
                qualified = "NO"
                sale_closed = "NO"
                value = 0.0
                reason = "Ignored: Direct or organic search lead (no ad click ID detected)."
                model_used = "None"
                ignored_records_count += 1
                print(f"   ℹ️ [Organic Call] Call from {caller_name} skipped: No active ad click ID detected.")
            elif transcript.strip():
                print(f"   🧠 [Audit Triggered] Running Claude 4.5 Haiku audit for {caller_name}...")
                ai_result = analyze_transcript_with_claude(transcript, criteria_wording)
                qualified = ai_result.get("qualified", "NO")
                sale_closed = ai_result.get("sale_closed", "NO")
                value = float(ai_result.get("value", 0.0))
                reason = ai_result.get("reason", "No reason parsed.")
                model_used = "claude-haiku-4-5-20251001"
                synced_records_count += 1
                print(f"      🎯 Result: Qualified={qualified}, Closed={sale_closed}, Value=${value:.2f}")
            else:
                qualified = "NO"
                sale_closed = "NO"
                value = 0.0
                reason = "Ignored: No audio transcript was compiled for this completed call."
                model_used = "None"
                ignored_records_count += 1
                print(f"   ⚠️ [Missing Transcript] Call from {caller_name} skipped: Transcription content empty.")
                
            # 5. Insert Record to Database
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
                client_id,
                normalized_phone,
                caller_name,
                gclid or None,
                fbclid or None,
                li_fat_id or None,
                msclkid or None,
                "callrail",
                qualified,
                sale_closed,
                value,
                reason,
                model_used,
                raw_data_json,
                created_at
            ))
            
    conn.commit()
    conn.close()
    
    print("\n=========================================================")
    print("📈 DAILY SYNC COMPLETE")
    print(f"   - Staged & Synced Leads: {synced_records_count}")
    print(f"   - Ignored/Organic Calls: {ignored_records_count}")
    print(f"   - Duplicates Prevented : {duplicate_records_count}")
    print("=========================================================")

if __name__ == "__main__":
    execute_daily_sync()
