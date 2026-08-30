#!/usr/bin/env python3
"""
Multi-Tenant CallRail 90-Day Backfill Utility
--------------------------------------------
This script is designed to run a manual, one-off historical backfill for any client.
It sweeps CallRail's live REST API for the past 90 days of call transcripts,
normalizes numbers, prevents duplicates, checks exclusions, performs Claude AI
audits, and saves the verified conversions to your SQLite database.

Usage:
  python3 backfill_90_days.py [client_id]

Example:
  python3 backfill_90_days.py 1
"""

import os
import sys
import sqlite3
import importlib.util
from datetime import datetime, timedelta

# Target DB Path
DB_PATH = os.environ.get("DB_PATH", "offline_attribution.db")

# 1. Load the core daily-callrail-sync Spec Dynamically to Bypass Hyphen Restrictions
SYNC_FILE_NAME = "daily-callrail-sync.py"
if not os.path.exists(SYNC_FILE_NAME):
    # Try looking in parent directories if run from subfolders
    if os.path.exists(os.path.join("..", SYNC_FILE_NAME)):
        SYNC_FILE_NAME = os.path.join("..", SYNC_FILE_NAME)
    elif os.path.exists(os.path.join("artifacts", SYNC_FILE_NAME)):
        SYNC_FILE_NAME = os.path.join("artifacts", SYNC_FILE_NAME)

if not os.path.exists(SYNC_FILE_NAME):
    print(f"❌ Error: Core sync script '{SYNC_FILE_NAME}' not found.")
    print("Please make sure you are running this from your main repository root directory.")
    sys.exit(1)

try:
    spec = importlib.util.spec_from_file_location("daily_sync", SYNC_FILE_NAME)
    daily_sync = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(daily_sync)
except Exception as e:
    print(f"❌ Error importing '{SYNC_FILE_NAME}': {e}")
    sys.exit(1)

def run_historical_backfill(client_id: int):
    print("=========================================================")
    print(f"🔄 HISTORICAL 90-DAY SYNC STARTED FOR CLIENT #{client_id}")
    print("=========================================================")
    
    if not os.path.exists(DB_PATH):
        print(f"❌ Error: Database file '{DB_PATH}' not found. Please run this where your active database sits.")
        sys.exit(1)
        
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 2. Fetch specific client metadata
    cursor.execute("""
        SELECT id, name, callrail_company_id, qualification_criteria, source_of_truth, exclude_past_customers, callrail_account_id 
        FROM clients 
        WHERE id = ?
    """, (client_id,))
    client_row = cursor.fetchone()
    
    if not client_row:
        print(f"❌ Error: Client ID {client_id} was not found in the database.")
        conn.close()
        sys.exit(1)
        
    client_id, name, company_id, crit_code, sot, exclude_past, client_account_id = client_row
    
    if not company_id or company_id.strip() == "":
        print(f"❌ Error: Client '{name}' does not have a registered CallRail Company ID.")
        conn.close()
        sys.exit(1)
        
    print(f"🏢 Client: '{name}'")
    print(f"📞 CallRail Company ID: {company_id}")
    print(f"⚙️ Single Source of Truth: {sot}")
    print(f"🏷️ Qualification Criteria: {crit_code}")
    print(f"🚫 Customer Exclusions Enabled: {exclude_past}")
    
    # Define 90-day window
    now = datetime.now()
    start_date = (now - timedelta(days=90)).strftime("%Y-%m-%d")
    end_date = now.strftime("%Y-%m-%d")
    
    print(f"📅 Fetching calls from {start_date} to {end_date}...")
    
    try:
        # 3. Call core fetcher
        calls = daily_sync.fetch_callrail_logs_for_client(client_id, company_id, start_date, end_date, client_account_id)
    except Exception as e:
        print(f"❌ Error fetching CallRail logs: {e}")
        conn.close()
        sys.exit(1)
        
    print(f"✓ Found {len(calls)} potential logs for processing.")
    
    synced_records_count = 0
    ignored_records_count = 0
    duplicate_records_count = 0
    
    criteria_wording = daily_sync.CRITERIA_MAP.get(crit_code, "Someone who books an appointment")
    
    for call in calls:
        raw_phone = call.get("customer_phone_number") or call.get("caller_number")
        normalized_phone = daily_sync.normalize_phone(raw_phone)
        
        if not normalized_phone:
            continue
            
        created_at = call.get("start_time") or call.get("created_at") or now.strftime("%Y-%m-%d %H:%M:%S")
        try:
            dt_parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            created_at = dt_parsed.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
            
        # 4. Duplicate Check
        cursor.execute(
            "SELECT id FROM sessions WHERE client_id = ? AND phone = ? AND created_at = ?", 
            (client_id, normalized_phone, created_at)
        )
        if cursor.fetchone():
            duplicate_records_count += 1
            continue
            
        # 5. Extract click IDs (with robust Milestones block lookup)
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
            gclid = daily_sync.extract_param_from_url(landing_url, "gclid") or daily_sync.extract_param_from_url(referrer_url, "gclid")
        if not fbclid:
            fbclid = daily_sync.extract_param_from_url(landing_url, "fbclid") or daily_sync.extract_param_from_url(referrer_url, "fbclid")
        if not li_fat_id:
            li_fat_id = daily_sync.extract_param_from_url(landing_url, "li_fat_id") or daily_sync.extract_param_from_url(referrer_url, "li_fat_id")
        if not msclkid:
            msclkid = daily_sync.extract_param_from_url(landing_url, "msclkid") or daily_sync.extract_param_from_url(referrer_url, "msclkid")
            
        has_click_id = any([gclid, fbclid, li_fat_id, msclkid])
        transcript = call.get("transcript") or call.get("transcription") or ""
        caller_name = call.get("customer_name") or "Unknown Caller"
        
        # 6. Exclusion check
        is_excluded = False
        exclusion_reason = ""
        if exclude_past == "YES":
            match_type = daily_sync.check_is_excluded_customer(client_id, phone=normalized_phone)
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
            print(f"   ℹ️ [Organic Call] Call from {caller_name} skipped: No active ad click ID detected.")
        elif transcript.strip():
            print(f"   🧠 [Audit Triggered] Running Claude 4.5 Haiku audit for {caller_name}...")
            try:
                ai_result = daily_sync.analyze_transcript_with_claude(transcript, criteria_wording)
                qualified = ai_result.get("qualified", "NO")
                sale_closed = ai_result.get("sale_closed", "NO")
                value = float(ai_result.get("value", 0.0))
                reason = ai_result.get("reason", "No reason parsed.")
                model_used = "claude-haiku-4-5-20251001"
                synced_records_count += 1
                print(f"      🎯 Result: Qualified={qualified}, Closed={sale_closed}, Value=${value:.2f}")
            except Exception as e:
                print(f"      ❌ Claude Audit failed for {caller_name}: {e}")
                continue
        else:
            qualified = "NO"
            sale_closed = "NO"
            value = 0.0
            reason = "Ignored: No audio transcript was compiled for this completed call."
            model_used = "None"
            ignored_records_count += 1
            print(f"   ⚠️ [Missing Transcript] Call from {caller_name} skipped: Transcription content empty.")
            
        # 7. Insert Record to Database
        raw_data_json = daily_sync.json.dumps({
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
    print("📈 HISTORICAL 90-DAY BACKFILL COMPLETE")
    print(f"   - Staged & Synced Leads: {synced_records_count}")
    print(f"   - Ignored/Organic Calls: {ignored_records_count}")
    print(f"   - Duplicates Prevented : {duplicate_records_count}")
    print("=========================================================")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        # No client ID specified, let's fetch the most recently created client ID
        if not os.path.exists(DB_PATH):
            print(f"❌ Error: Database file '{DB_PATH}' was not found.")
            sys.exit(1)
            
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT id, name FROM clients ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        conn.close()
        
        if row:
            client_id, name = row
            print(f"ℹ️ No Client ID specified. Defaulting to most recently created Client #{client_id} ('{name}').")
            run_historical_backfill(client_id)
        else:
            print("❌ Error: No clients found in the database. Usage: python3 backfill_90_days.py [client_id]")
            sys.exit(1)
    else:
        try:
            target_id = int(sys.argv[1])
            run_historical_backfill(target_id)
        except ValueError:
            print("❌ Error: Client ID must be a valid integer.")
            print("Usage: python3 backfill_90_days.py [client_id]")
            sys.exit(1)
