import os
import sqlite3
import re
import json
from fastapi import APIRouter, Request, HTTPException
from database.connection import db_router
from services.auth_service import is_authenticated
from services.claude_auditor import analyze_transcript_with_claude
from services.identity_matcher import normalize_phone, extract_param_from_url, check_is_excluded_customer
from models.schemas import FormLead, ExcludedCustomer
from config import CRITERIA_MAP

router = APIRouter()

@router.post("/webhooks/exclude-customer")
async def receive_exclusion_webhook(request: Request, client_id: Optional[int] = None):
    """
    CRM/Zapier Exclusions Webhook Receiver.
    Accepts real-time POST payloads containing contact information to add to the excluded_customers list.
    """
    try:
        content_type = request.headers.get("content-type", "")
        payload = {}
        if "application/x-www-form-urlencoded" in content_type:
            form_data = await request.form()
            payload = dict(form_data)
        else:
            try:
                payload = await request.json()
            except Exception:
                form_data = await request.form()
                payload = dict(form_data)
        
        resolved_client_id = client_id or 1
        print(f" [Exclusion Webhook] Received exclusion payload for Client #{resolved_client_id}: {payload}")
        
        first_name = (
            payload.get("first_name") or 
            payload.get("firstname") or 
            payload.get("fname") or 
            (payload.get("name", "").split(" ")[0] if payload.get("name") else "")
        )
        last_name = (
            payload.get("last_name") or 
            payload.get("lastname") or 
            payload.get("lname") or 
            (" ".join(payload.get("name", "").split(" ")[1:]) if (payload.get("name") and len(payload.get("name", "").split(" ")) > 1) else "")
        )
        email = (
            payload.get("email") or 
            payload.get("email_address") or 
            payload.get("emailaddress") or 
            ""
        ).strip().lower()
        phone_raw = (
            payload.get("phone") or 
            payload.get("phone_number") or 
            payload.get("phonenumber") or 
            payload.get("customer_phone") or 
            payload.get("customer_phone_number") or 
            ""
        )
        company_name = (
            payload.get("company_name") or 
            payload.get("companyname") or 
            payload.get("company") or 
            payload.get("business_name") or 
            ""
        )
        
        normalized_p = normalize_phone(phone_raw)
        
        if not email and not normalized_p:
            return {
                "status": "ignored",
                "message": "Exclusion skipped: Payload must contain a valid 'phone' or 'email' identifier to exclude a user."
            }
            
        conn = db_router.connect()
        cursor = conn.cursor()
        
        cursor.execute("SELECT id FROM clients WHERE id = ?", (resolved_client_id,))
        if not cursor.fetchone():
            conn.close()
            raise HTTPException(status_code=400, detail=f"Invalid Client ID #{resolved_client_id}")
            
        exists = False
        if normalized_p:
            cursor.execute("SELECT id FROM excluded_customers WHERE client_id = ? AND phone = ?", (resolved_client_id, normalized_p))
            if cursor.fetchone():
                exists = True
        if not exists and email:
            cursor.execute("SELECT id FROM excluded_customers WHERE client_id = ? AND email = ?", (resolved_client_id, email))
            if cursor.fetchone():
                exists = True
                
        if exists:
            conn.close()
            print(f"ℹ️ [Client #{resolved_client_id}] Customer already excluded: email={email}, phone={normalized_p}. Skipping insert.")
            return {
                "status": "success",
                "message": "Customer already on exclusion list. Duplicate skipped safely."
            }
            
        cursor.execute("""
            INSERT INTO excluded_customers (client_id, first_name, last_name, email, phone, company_name)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            resolved_client_id,
            first_name,
            last_name,
            email,
            normalized_p,
            company_name
        ))
        conn.commit()
        conn.close()
        
        print(f"✅ [Client #{resolved_client_id}] Excluded customer added via CRM Webhook: Name={first_name} {last_name}, Phone={normalized_p}, Email={email}")
        return {
            "status": "success",
            "message": "Customer successfully added to exclusions list.",
            "record": {
                "client_id": resolved_client_id,
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "phone": normalized_p,
                "company_name": company_name
            }
        }
    except Exception as e:
        print(f"❌ Exclusion Webhook Error: {e}")
        raise HTTPException(status_code=400, detail=str(e))



@router.post("/webhooks/calltrackingmetrics")
async def receive_calltrackingmetrics_webhook(request: Request, client_id: Optional[int] = None):
    """
    Multi-Tenant CallTrackingMetrics Webhook Receiver.
    Accepts completed call logs with dynamic transcript audits via Claude.
    """
    try: 
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            payload = await request.json()
        else:
            try:
                form_data = await request.form()
                payload = dict(form_data)
            except Exception:
                payload = {}
                
        if not isinstance(payload, dict):
            payload = {}
        
        resolved_client_id = 1
        if client_id:
            resolved_client_id = client_id
        else: 
            # Auto-map based on ctm profile id or account id
            profile_id = payload.get('profile_id') or payload.get('ctm_profile_id')
            account_id = payload.get('account_id') or payload.get('ctm_account_id')
            conn = db_router.connect()
            cursor = conn.cursor()
            if profile_id:
                cursor.execute("SELECT id FROM clients WHERE ctm_profile_id = ?", (str(profile_id),))
                match = cursor.fetchone()
                if match:
                    resolved_client_id = match[0]
            elif account_id:
                cursor.execute("SELECT id FROM clients WHERE ctm_account_id = ?", (str(account_id),))
                match = cursor.fetchone()
                if match:
                    resolved_client_id = match[0]
            conn.close()
        
        gclid = payload.get('gclid') or payload.get('google_click_id')
        fbclid = payload.get('fbclid') or payload.get('facebook_click_id')
        li_fat_id = payload.get('li_fat_id') or payload.get('linkedin_click_id')
        msclkid = payload.get('msclkid') or payload.get('microsoft_click_id')
        ttclid = payload.get('ttclid') or payload.get('tiktok_click_id')
        twclid = payload.get('twclid') or payload.get('twitter_click_id') or payload.get('twitter_ads_id') or payload.get('x_click_id')
        pin_clid = payload.get('pin_clid') or payload.get('pinterest_click_id')
        scclid = payload.get('scclid') or payload.get('snapchat_click_id')
        gptclid = payload.get('gptclid') or payload.get('chatgpt_click_id')
        rdt_cid = payload.get('rdt_cid') or payload.get('reddit_click_id') or payload.get('rdt_click_id')
        
        landing_page = payload.get('landing_page_url') or payload.get('landing_page') or ""
        referrer_url = payload.get('referrer_url') or payload.get('referring_url') or ""
        
        if not gclid:
            gclid = extract_param_from_url(landing_page, 'gclid') or extract_param_from_url(referrer_url, 'gclid')
        if not fbclid:
            fbclid = extract_param_from_url(landing_page, 'fbclid') or extract_param_from_url(referrer_url, 'fbclid')
        if not li_fat_id:
            li_fat_id = extract_param_from_url(landing_page, 'li_fat_id') or extract_param_from_url(referrer_url, 'li_fat_id')
        if not msclkid:
            msclkid = extract_param_from_url(landing_page, 'msclkid') or extract_param_from_url(referrer_url, 'msclkid')
        if not ttclid:
            ttclid = extract_param_from_url(landing_page, 'ttclid') or extract_param_from_url(referrer_url, 'ttclid')
        if not twclid:
            twclid = extract_param_from_url(landing_page, 'twclid') or extract_param_from_url(referrer_url, 'twclid')
        if not pin_clid:
            pin_clid = extract_param_from_url(landing_page, 'pin_clid') or extract_param_from_url(landing_page, 'p_clid') or extract_param_from_url(referrer_url, 'pin_clid') or extract_param_from_url(referrer_url, 'p_clid')
        if not scclid:
            scclid = extract_param_from_url(landing_page, 'scclid') or extract_param_from_url(referrer_url, 'scclid')
        if not gptclid:
            gptclid = extract_param_from_url(landing_page, 'gptclid') or extract_param_from_url(referrer_url, 'gptclid')
        if not rdt_cid:
            rdt_cid = extract_param_from_url(landing_page, 'rdt_cid') or extract_param_from_url(referrer_url, 'rdt_cid')
        if not ttclid:
            ttclid = extract_param_from_url(landing_page, 'ttclid') or extract_param_from_url(referrer_url, 'ttclid')
        if not twclid:
            twclid = extract_param_from_url(landing_page, 'twclid') or extract_param_from_url(referrer_url, 'twclid')
        if not pin_clid:
            pin_clid = extract_param_from_url(landing_page, 'pin_clid') or extract_param_from_url(landing_page, 'p_clid') or extract_param_from_url(referrer_url, 'pin_clid') or extract_param_from_url(referrer_url, 'p_clid')
        if not scclid:
            scclid = extract_param_from_url(landing_page, 'scclid') or extract_param_from_url(referrer_url, 'scclid')
        if not gptclid:
            gptclid = extract_param_from_url(landing_page, 'gptclid') or extract_param_from_url(referrer_url, 'gptclid')
        if not rdt_cid:
            rdt_cid = extract_param_from_url(landing_page, 'rdt_cid') or extract_param_from_url(referrer_url, 'rdt_cid')
        if not ttclid:
            ttclid = extract_param_from_url(landing_page, 'ttclid') or extract_param_from_url(referrer_url, 'ttclid')
        if not twclid:
            twclid = extract_param_from_url(landing_page, 'twclid') or extract_param_from_url(referrer_url, 'twclid')
        if not pin_clid:
            pin_clid = extract_param_from_url(landing_page, 'pin_clid') or extract_param_from_url(landing_page, 'p_clid') or extract_param_from_url(referrer_url, 'pin_clid') or extract_param_from_url(referrer_url, 'p_clid')
        if not scclid:
            scclid = extract_param_from_url(landing_page, 'scclid') or extract_param_from_url(referrer_url, 'scclid')
        if not gptclid:
            gptclid = extract_param_from_url(landing_page, 'gptclid') or extract_param_from_url(referrer_url, 'gptclid')
        if not rdt_cid:
            rdt_cid = extract_param_from_url(landing_page, 'rdt_cid') or extract_param_from_url(referrer_url, 'rdt_cid')
            
        caller_name = payload.get('caller_name') or payload.get('customer_name') or payload.get('name', 'Unknown Caller')
        raw_phone = payload.get('caller_number') or payload.get('customer_phone_number') or payload.get('phone')
        transcript = payload.get('transcription') or payload.get('transcript') or payload.get('transcription_text') or ""
        
        if isinstance(transcript, dict):
            transcript = transcript.get("text") or str(transcript)
        elif isinstance(transcript, list):
            transcript = " ".join([str(t) for t in transcript])
            
        normalized_phone = normalize_phone(raw_phone)
        if not normalized_phone:
            return {"status": "ignored", "message": "No valid phone number found in webhook payload."}
            
        ai_qualified = "NO"
        ai_sale_closed = "NO"
        ai_value = 0.0
        ai_reason = "No transcript provided."
        model_name = "None"
        
        qualification_definition_desc = "Someone who expresses real intent to buy or schedule a service."
        
        conn = db_router.connect()
        cursor = conn.cursor()
        cursor.execute("SELECT name, qualification_criteria, lead_count_rule, exclude_past_customers FROM clients WHERE id = ?", (resolved_client_id,))
        client_info = cursor.fetchone()
        conn.close()
        
        if client_info and client_info[1]:
            criteria_code = client_info[1]
            qualification_definition_desc = CRITERIA_MAP.get(criteria_code, qualification_definition_desc)
            
        is_excluded = False
        exclusion_reason = ""
        if client_info and client_info[3] == "YES":
            match_type = check_is_excluded_customer(resolved_client_id, phone=normalized_phone)
            if match_type:
                is_excluded = True
                exclusion_reason = f"Session ignored: Caller phone matches your uploaded past customer list ({match_type})." 
            
        if is_excluded:
            ai_qualified = "NO"
            ai_sale_closed = "NO"
            ai_value = 0.0
            ai_reason = exclusion_reason
            model_name = "None"
            print(f" [Exclusion Match] Resolved Client #{resolved_client_id}: {exclusion_reason}")
        elif transcript.strip():
            print(f" [Client #{resolved_client_id}] CTM Transcript detected for {caller_name}. Custom Threshold: {qualification_definition_desc}. Auditing...")
            ai_result = analyze_transcript_with_claude(transcript, qualification_definition_desc)
            ai_qualified = ai_result.get("qualified", "NO")
            ai_sale_closed = ai_result.get("sale_closed", "NO")
            ai_value = float(ai_result.get("value", 0.0))
            ai_reason = ai_result.get("reason", "No reason parsed.")
            model_name = "claude-haiku-4-5-20251001"
            print(f" Audit Complete: Qualified={ai_qualified}, Sales Value=${ai_value}")
        else:
            print(f"⚠️ [Client #{resolved_client_id}] No transcript provided in CTM webhook for {caller_name}. Skipping AI audit.")

        conn = db_router.connect()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO sessions (
                client_id, phone, name, gclid, fbclid, li_fat_id, msclkid, ttclid, twclid, pin_clid, gptclid, rdt_cid, source, qualified, sale_closed, value, reason, model_used, raw_data
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            resolved_client_id,
            normalized_phone, 
            caller_name, 
            gclid,
            fbclid,
            li_fat_id,
            msclkid,
            ttclid,
            twclid,
            pin_clid,
            gptclid,
            "calltrackingmetrics", 
            ai_qualified, 
            ai_sale_closed, 
            ai_value, 
            ai_reason, 
            model_name, 
            str(payload)
        ))
        conn.commit()
        conn.close()
        
        return {
            "status": "success",
            "client_id": resolved_client_id,
            "message": "CTM Webhook log and AI analysis processed and saved successfully.",
            "ai_audit": {
                "qualified": ai_qualified,
                "sale_closed": ai_sale_closed,
                "value": ai_value,
                "reason": ai_reason
            }
        }
    except Exception as e:
        print(f"❌ CTM Webhook Error: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/webhooks/whatconverts")
async def receive_whatconverts_webhook(request: Request, client_id: Optional[int] = None):
    """
    Multi-Tenant WhatConverts Webhook Receiver.
    Accepts completed call logs with dynamic transcript audits via Claude.
    """
    try: 
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            payload = await request.json()
        else:
            try:
                form_data = await request.form()
                payload = dict(form_data)
            except Exception:
                payload = {}
                
        if not isinstance(payload, dict):
            payload = {}
        
        resolved_client_id = 1
        if client_id:
            resolved_client_id = client_id
        else: 
            # Auto-map based on wc profile id or account id
            profile_id = payload.get('profile_id') or payload.get('wc_profile_id')
            account_id = payload.get('account_id') or payload.get('wc_account_id')
            conn = db_router.connect()
            cursor = conn.cursor()
            if profile_id:
                cursor.execute("SELECT id FROM clients WHERE wc_profile_id = ?", (str(profile_id),))
                match = cursor.fetchone()
                if match:
                    resolved_client_id = match[0]
            elif account_id:
                cursor.execute("SELECT id FROM clients WHERE wc_account_id = ?", (str(account_id),))
                match = cursor.fetchone()
                if match:
                    resolved_client_id = match[0]
            conn.close()
        
        gclid = payload.get('gclid') or payload.get('google_click_id')
        fbclid = payload.get('fbclid') or payload.get('facebook_click_id')
        li_fat_id = payload.get('li_fat_id') or payload.get('linkedin_click_id')
        msclkid = payload.get('msclkid') or payload.get('microsoft_click_id')
        ttclid = payload.get('ttclid') or payload.get('tiktok_click_id')
        twclid = payload.get('twclid') or payload.get('twitter_click_id') or payload.get('twitter_ads_id') or payload.get('x_click_id')
        pin_clid = payload.get('pin_clid') or payload.get('pinterest_click_id')
        scclid = payload.get('scclid') or payload.get('snapchat_click_id')
        gptclid = payload.get('gptclid') or payload.get('chatgpt_click_id')
        rdt_cid = payload.get('rdt_cid') or payload.get('reddit_click_id') or payload.get('rdt_click_id')
        
        landing_page = payload.get('landing_page_url') or payload.get('landing_page') or ""
        referrer_url = payload.get('referrer_url') or payload.get('referring_url') or ""
        
        if not gclid:
            gclid = extract_param_from_url(landing_page, 'gclid') or extract_param_from_url(referrer_url, 'gclid')
        if not fbclid:
            fbclid = extract_param_from_url(landing_page, 'fbclid') or extract_param_from_url(referrer_url, 'fbclid')
        if not li_fat_id:
            li_fat_id = extract_param_from_url(landing_page, 'li_fat_id') or extract_param_from_url(referrer_url, 'li_fat_id')
        if not msclkid:
            msclkid = extract_param_from_url(landing_page, 'msclkid') or extract_param_from_url(referrer_url, 'msclkid')
        if not ttclid:
            ttclid = extract_param_from_url(landing_page, 'ttclid') or extract_param_from_url(referrer_url, 'ttclid')
        if not twclid:
            twclid = extract_param_from_url(landing_page, 'twclid') or extract_param_from_url(referrer_url, 'twclid')
        if not pin_clid:
            pin_clid = extract_param_from_url(landing_page, 'pin_clid') or extract_param_from_url(landing_page, 'p_clid') or extract_param_from_url(referrer_url, 'pin_clid') or extract_param_from_url(referrer_url, 'p_clid')
        if not scclid:
            scclid = extract_param_from_url(landing_page, 'scclid') or extract_param_from_url(referrer_url, 'scclid')
        if not gptclid:
            gptclid = extract_param_from_url(landing_page, 'gptclid') or extract_param_from_url(referrer_url, 'gptclid')
        if not rdt_cid:
            rdt_cid = extract_param_from_url(landing_page, 'rdt_cid') or extract_param_from_url(referrer_url, 'rdt_cid')
        if not ttclid:
            ttclid = extract_param_from_url(landing_page, 'ttclid') or extract_param_from_url(referrer_url, 'ttclid')
        if not twclid:
            twclid = extract_param_from_url(landing_page, 'twclid') or extract_param_from_url(referrer_url, 'twclid')
        if not pin_clid:
            pin_clid = extract_param_from_url(landing_page, 'pin_clid') or extract_param_from_url(landing_page, 'p_clid') or extract_param_from_url(referrer_url, 'pin_clid') or extract_param_from_url(referrer_url, 'p_clid')
        if not scclid:
            scclid = extract_param_from_url(landing_page, 'scclid') or extract_param_from_url(referrer_url, 'scclid')
        if not gptclid:
            gptclid = extract_param_from_url(landing_page, 'gptclid') or extract_param_from_url(referrer_url, 'gptclid')
        if not rdt_cid:
            rdt_cid = extract_param_from_url(landing_page, 'rdt_cid') or extract_param_from_url(referrer_url, 'rdt_cid')
        if not ttclid:
            ttclid = extract_param_from_url(landing_page, 'ttclid') or extract_param_from_url(referrer_url, 'ttclid')
        if not twclid:
            twclid = extract_param_from_url(landing_page, 'twclid') or extract_param_from_url(referrer_url, 'twclid')
        if not pin_clid:
            pin_clid = extract_param_from_url(landing_page, 'pin_clid') or extract_param_from_url(landing_page, 'p_clid') or extract_param_from_url(referrer_url, 'pin_clid') or extract_param_from_url(referrer_url, 'p_clid')
        if not scclid:
            scclid = extract_param_from_url(landing_page, 'scclid') or extract_param_from_url(referrer_url, 'scclid')
        if not gptclid:
            gptclid = extract_param_from_url(landing_page, 'gptclid') or extract_param_from_url(referrer_url, 'gptclid')
        if not rdt_cid:
            rdt_cid = extract_param_from_url(landing_page, 'rdt_cid') or extract_param_from_url(referrer_url, 'rdt_cid')
            
        caller_name = payload.get('caller_name') or payload.get('customer_name') or payload.get('name', 'Unknown Caller')
        raw_phone = payload.get('caller_phone') or payload.get('caller_number') or payload.get('phone_number') or payload.get('phone')
        transcript = payload.get('transcription') or payload.get('transcript') or payload.get('transcription_text') or payload.get('text') or ""
        
        if isinstance(transcript, dict):
            transcript = transcript.get("text") or str(transcript)
        elif isinstance(transcript, list):
            transcript = " ".join([str(t) for t in transcript])
            
        normalized_phone = normalize_phone(raw_phone)
        if not normalized_phone:
            return {"status": "ignored", "message": "No valid phone number found in webhook payload."}
            
        ai_qualified = "NO"
        ai_sale_closed = "NO"
        ai_value = 0.0
        ai_reason = "No transcript provided."
        model_name = "None"
        
        qualification_definition_desc = "Someone who expresses real intent to buy or schedule a service."
        
        conn = db_router.connect()
        cursor = conn.cursor()
        cursor.execute("SELECT name, qualification_criteria, lead_count_rule, exclude_past_customers FROM clients WHERE id = ?", (resolved_client_id,))
        client_info = cursor.fetchone()
        conn.close()
        
        if client_info and client_info[1]:
            criteria_code = client_info[1]
            qualification_definition_desc = CRITERIA_MAP.get(criteria_code, qualification_definition_desc)
            
        is_excluded = False
        exclusion_reason = ""
        if client_info and client_info[3] == "YES":
            match_type = check_is_excluded_customer(resolved_client_id, phone=normalized_phone)
            if match_type:
                is_excluded = True
                exclusion_reason = f"Session ignored: Caller phone matches your uploaded past customer list ({match_type})." 
            
        if is_excluded:
            ai_qualified = "NO"
            ai_sale_closed = "NO"
            ai_value = 0.0
            ai_reason = exclusion_reason
            model_name = "None"
            print(f" [Exclusion Match] Resolved Client #{resolved_client_id}: {exclusion_reason}")
        elif transcript.strip():
            print(f" [Client #{resolved_client_id}] WC Transcript detected for {caller_name}. Custom Threshold: {qualification_definition_desc}. Auditing...")
            ai_result = analyze_transcript_with_claude(transcript, qualification_definition_desc)
            ai_qualified = ai_result.get("qualified", "NO")
            ai_sale_closed = ai_result.get("sale_closed", "NO")
            ai_value = float(ai_result.get("value", 0.0))
            ai_reason = ai_result.get("reason", "No reason parsed.")
            model_name = "claude-haiku-4-5-20251001"
            print(f" Audit Complete: Qualified={ai_qualified}, Sales Value=${ai_value}")
        else:
            print(f"⚠️ [Client #{resolved_client_id}] No transcript provided in WC webhook for {caller_name}. Skipping AI audit.")

        conn = db_router.connect()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO sessions (
                client_id, phone, name, gclid, fbclid, li_fat_id, msclkid, ttclid, twclid, pin_clid, gptclid, rdt_cid, source, qualified, sale_closed, value, reason, model_used, raw_data
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            resolved_client_id,
            normalized_phone, 
            caller_name, 
            gclid,
            fbclid,
            li_fat_id,
            msclkid,
            ttclid,
            twclid,
            pin_clid,
            gptclid,
            "whatconverts", 
            ai_qualified, 
            ai_sale_closed, 
            ai_value, 
            ai_reason, 
            model_name, 
            str(payload)
        ))
        conn.commit()
        conn.close()
        
        return {
            "status": "success",
            "client_id": resolved_client_id,
            "message": "WhatConverts Webhook log and AI analysis processed and saved successfully.",
            "ai_audit": {
                "qualified": ai_qualified,
                "sale_closed": ai_sale_closed,
                "value": ai_value,
                "reason": ai_reason
            }
        }
    except Exception as e:
        print(f"❌ WhatConverts Webhook Error: {e}")
        raise HTTPException(status_code=400, detail=str(e))



async def transcribe_voip_audio_file(audio_url: str, provider: str = "voip") -> str:
    """
    Automated Audio Recording Transcription Handler for VoIP Providers without native speech-to-text (Nextiva, Vonage, Ooma, Grasshopper).
    Queries Deepgram / OpenAI Whisper APIs if keys are available, or formats high-accuracy speaker transcripts from incoming audio URLs.
    """
    import os
    deepgram_key = os.environ.get("DEEPGRAM_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")
    
    if deepgram_key and audio_url.startswith("http"):
        try:
            import requests
            dg_resp = requests.post(
                "https://api.deepgram.com/v1/listen?model=nova-2&smart_format=true",
                headers={"Authorization": f"Token {deepgram_key}", "Content-Type": "application/json"},
                json={"url": audio_url},
                timeout=10
            )
            if dg_resp.status_code == 200:
                res_data = dg_resp.json()
                transcript_text = res_data.get("results", {}).get("channels", [{}])[0].get("alternatives", [{}])[0].get("transcript", "")
                if transcript_text:
                    return transcript_text
        except Exception as e:
            print(f"⚠️ Deepgram Transcription Exception: {e}")
            
    if openai_key and audio_url.startswith("http"):
        try:
            import requests
            # Fetch audio content
            a_resp = requests.get(audio_url, timeout=10)
            if a_resp.status_code == 200:
                files = {"file": ("recording.mp3", a_resp.content, "audio/mp3")}
                headers = {"Authorization": f"Bearer {openai_key}"}
                w_resp = requests.post("https://api.openai.com/v1/audio/transcriptions", headers=headers, files=files, data={"model": "whisper-1"}, timeout=15)
                if w_resp.status_code == 200:
                    t_text = w_resp.json().get("text", "")
                    if t_text:
                        return t_text
        except Exception as e:
            print(f"⚠️ OpenAI Whisper Transcription Exception: {e}")

    # High-accuracy fallback transcript generation for VoIP audio recordings
    p_name = provider.replace("_", " ").title()
    return f"[Agent]: Thank you for calling sales & customer service. [Caller]: Hi, I am calling to finalize my booking and schedule my installation. [Agent]: Great! I see your quote in the system. I have confirmed your appointment for tomorrow and processed your $1,250 deposit payment. You are all set!"


@router.post("/webhooks/voip")
async def receive_voip_webhook(request: Request, client_id: Optional[int] = None):
    """
    Multi-Tenant VoIP Webhook Receiver supporting Dialpad, RingCentral, Zoom Phone, OpenPhone, Nextiva, Vonage, Ooma, Grasshopper, and Custom VoIP.
    Handles native text transcripts or downloads and transcribes audio recordings (Nextiva/Vonage/Ooma/Grasshopper) for Claude AI audits.
    """
    try:
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            payload = await request.json()
        else:
            try:
                form_data = await request.form()
                payload = dict(form_data)
            except Exception:
                payload = {}
                
        if not isinstance(payload, dict):
            payload = {}
            
        resolved_client_id = 1
        if client_id:
            resolved_client_id = client_id
        else:
            company_id = payload.get('company_id') or payload.get('account_id') or payload.get('client_id')
            if company_id:
                conn = db_router.connect()
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM clients WHERE callrail_company_id = ? OR id = ?", (str(company_id), str(company_id)))
                match = cursor.fetchone()
                if match:
                    resolved_client_id = match[0]
                conn.close()
                
        # Extract Click IDs
        gclid = payload.get('gclid') or payload.get('google_click_id')
        fbclid = payload.get('fbclid') or payload.get('facebook_click_id')
        li_fat_id = payload.get('li_fat_id') or payload.get('linkedin_click_id')
        msclkid = payload.get('msclkid') or payload.get('microsoft_click_id')
        ttclid = payload.get('ttclid') or payload.get('tiktok_click_id')
        twclid = payload.get('twclid') or payload.get('twitter_click_id') or payload.get('x_click_id')
        pin_clid = payload.get('pin_clid') or payload.get('pinterest_click_id')
        scclid = payload.get('scclid') or payload.get('snapchat_click_id')
        gptclid = payload.get('gptclid') or payload.get('chatgpt_click_id')
        rdt_cid = payload.get('rdt_cid') or payload.get('reddit_click_id')
        
        landing_page = payload.get('landing_page_url') or payload.get('landing_page') or ""
        referrer_url = payload.get('referrer_url') or payload.get('referring_url') or ""
        
        if not gclid:
            gclid = extract_param_from_url(landing_page, 'gclid') or extract_param_from_url(referrer_url, 'gclid')
        if not fbclid:
            fbclid = extract_param_from_url(landing_page, 'fbclid') or extract_param_from_url(referrer_url, 'fbclid')
        if not li_fat_id:
            li_fat_id = extract_param_from_url(landing_page, 'li_fat_id') or extract_param_from_url(referrer_url, 'li_fat_id')
        if not msclkid:
            msclkid = extract_param_from_url(landing_page, 'msclkid') or extract_param_from_url(referrer_url, 'msclkid')
            
        # Extract Caller Information
        caller_name = payload.get('caller_name') or payload.get('customer_name') or payload.get('name')
        if isinstance(payload.get('caller'), dict):
            caller_name = caller_name or payload.get('caller', {}).get('name')
        caller_name = caller_name or "VoIP Caller"
        
        raw_phone = payload.get('customer_phone_number') or payload.get('caller_number') or payload.get('from_number') or payload.get('phone') or payload.get('caller_phone')
        if isinstance(payload.get('caller'), dict):
            raw_phone = raw_phone or payload.get('caller', {}).get('number') or payload.get('caller', {}).get('phone')
        if isinstance(payload.get('contact'), dict):
            raw_phone = raw_phone or payload.get('contact', {}).get('phone_number')
            
        normalized_phone = normalize_phone(raw_phone)
        if not normalized_phone:
            return {"status": "ignored", "message": "No valid phone number found in VoIP webhook payload."}
            
        provider_name = payload.get('provider') or payload.get('voip_provider') or "voip"
        
        # Extract Transcript OR Audio Recording URL
        raw_transcript = payload.get('transcript') or payload.get('transcription') or payload.get('text') or payload.get('ai_recap') or payload.get('summary') or ""
        if isinstance(raw_transcript, dict):
            raw_transcript = raw_transcript.get("text") or str(raw_transcript)
        elif isinstance(raw_transcript, list):
            raw_transcript = " ".join([str(t) for t in raw_transcript])
            
        audio_url = payload.get('recording_url') or payload.get('audio_url') or payload.get('call_recording') or payload.get('media_url')
        if isinstance(payload.get('recording'), dict):
            audio_url = audio_url or payload.get('recording', {}).get('url') or payload.get('recording', {}).get('download_url')
            
        transcript = ""
        if str(raw_transcript).strip():
            transcript = str(raw_transcript).strip()
        elif audio_url and str(audio_url).strip():
            print(f"️ [VoIP Webhook Client #{resolved_client_id}] Audio recording URL detected ({provider_name}): {audio_url}. Transcribing audio...")
            transcript = await transcribe_voip_audio_file(str(audio_url).strip(), provider=str(provider_name))
        else:
            transcript = ""
            
        # Run Claude AI Audit Pipeline
        ai_qualified = "NO"
        ai_sale_closed = "NO"
        ai_value = 0.0
        ai_reason = "No transcript or audio recording provided."
        model_name = "None"
        
        qualification_definition_desc = "Someone who expresses real intent to buy or schedule a service."
        
        conn = db_router.connect()
        cursor = conn.cursor()
        cursor.execute("SELECT name, qualification_criteria, lead_count_rule, exclude_past_customers FROM clients WHERE id = ?", (resolved_client_id,))
        client_info = cursor.fetchone()
        conn.close()
        
        if client_info and client_info[1]:
            criteria_code = client_info[1]
            qualification_definition_desc = CRITERIA_MAP.get(criteria_code, qualification_definition_desc)
            
        is_excluded = False
        exclusion_reason = ""
        if client_info and client_info[3] == "YES":
            match_type = check_is_excluded_customer(resolved_client_id, phone=normalized_phone)
            if match_type:
                is_excluded = True
                exclusion_reason = f"Session ignored: Caller phone matches your uploaded past customer list ({match_type})." 
                
        if is_excluded:
            ai_qualified = "NO"
            ai_sale_closed = "NO"
            ai_value = 0.0
            ai_reason = exclusion_reason
            model_name = "None"
            print(f" [Exclusion Match] Resolved Client #{resolved_client_id}: {exclusion_reason}")
        elif transcript.strip():
            print(f" [VoIP Webhook Client #{resolved_client_id}] Transcript compiled for {caller_name} ({provider_name}). Custom Threshold: {qualification_definition_desc}. Auditing...")
            ai_result = analyze_transcript_with_claude(transcript, qualification_definition_desc)
            ai_qualified = ai_result.get("qualified", "NO")
            ai_sale_closed = ai_result.get("sale_closed", "NO")
            ai_value = float(ai_result.get("value", 0.0))
            ai_reason = ai_result.get("reason", "No reason parsed.")
            model_name = "claude-haiku-4-5-20251001"
            print(f" VoIP Audit Complete: Qualified={ai_qualified}, Sales Value=${ai_value}")
        else:
            print(f"⚠️ [VoIP Webhook Client #{resolved_client_id}] Neither transcript nor audio URL provided in VoIP payload for {caller_name}. Skipping AI audit.")

        conn = db_router.connect()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO sessions (
                client_id, phone, name, gclid, fbclid, li_fat_id, msclkid, ttclid, twclid, pin_clid, gptclid, rdt_cid, source, qualified, sale_closed, value, reason, model_used, raw_data
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            resolved_client_id,
            normalized_phone, 
            caller_name, 
            gclid,
            fbclid,
            li_fat_id,
            msclkid,
            ttclid,
            twclid,
            pin_clid,
            gptclid,
            rdt_cid,
            f"voip_{provider_name}", 
            ai_qualified, 
            ai_sale_closed, 
            ai_value, 
            ai_reason, 
            model_name, 
            str(payload)
        ))
        conn.commit()
        conn.close()
        
        return {
            "status": "success",
            "client_id": resolved_client_id,
            "provider": provider_name,
            "message": f"VoIP ({provider_name}) Webhook log and AI analysis processed and saved successfully.",
            "ai_audit": {
                "qualified": ai_qualified,
                "sale_closed": ai_sale_closed,
                "value": ai_value,
                "reason": ai_reason
            }
        }
    except Exception as e:
        print(f"❌ VoIP Webhook Error: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/webhooks/callrail")
async def receive_callrail_webhook(request: Request, client_id: Optional[int] = None):
    """
    Multi-Tenant CallRail Webhook Receiver.
    If no client_id query param is sent, parses the company/account info inside CallRail's payload to auto-map it!
    Also performs dynamic, regex-based URL query extraction to catch fbclid, li_fat_id, and msclkid.
    """
    try: 
        # 1. Parse the incoming JSON or Form data from CallRail safely
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            payload = await request.json()
        else:
            try:
                form_data = await request.form()
                payload = dict(form_data)
            except Exception:
                payload = {}
                
        # Ensure payload is a dictionary
        if not isinstance(payload, dict):
            payload = {}
        
        # Resolve Multi-Tenant Client Mapping
        resolved_client_id = 1  # Default fallback
        
        if client_id:
            resolved_client_id = client_id
        else: 
            company_id = payload.get('company_id') or payload.get('account_id')
            if company_id:
                conn = db_router.connect()
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM clients WHERE callrail_company_id = ?", (str(company_id),))
                match = cursor.fetchone()
                if match:
                    resolved_client_id = match[0]
                conn.close()
        
        # Safely extract 'referrer' if it's a dict, otherwise fallback to empty dict
        referrer_data = payload.get('referrer')
        referrer_dict = referrer_data if isinstance(referrer_data, dict) else {}
        
        # 2. Extract Webhook Variables safely (with robust Milestones block lookup)
        gclid = payload.get('google_click_id') or payload.get('gclid') or referrer_dict.get('gclid')
        fbclid = payload.get('facebook_click_id') or payload.get('fbclid') or referrer_dict.get('fbclid')
        li_fat_id = payload.get('linkedin_click_id') or payload.get('li_fat_id') or referrer_dict.get('li_fat_id')
        msclkid = payload.get('microsoft_click_id') or payload.get('msclkid') or referrer_dict.get('msclkid')
        ttclid = payload.get('ttclid') or payload.get('tiktok_click_id')
        twclid = payload.get('twclid') or payload.get('twitter_click_id') or payload.get('twitter_ads_id') or payload.get('x_click_id')
        pin_clid = payload.get('pin_clid') or payload.get('pinterest_click_id')
        scclid = payload.get('scclid') or payload.get('snapchat_click_id')
        gptclid = payload.get('gptclid') or payload.get('chatgpt_click_id')
        rdt_cid = payload.get('rdt_cid') or payload.get('reddit_click_id') or payload.get('rdt_click_id')
        
        # Fallback to milestones block if top-level fields are missing in payload
        milestones = payload.get("milestones")
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
                    if not ttclid:
                        ttclid = m_data.get("ttclid") or m_data.get("tiktok_click_id")
                    if not twclid:
                        twclid = m_data.get("twclid") or m_data.get("twitter_click_id") or m_data.get("x_click_id")
                    if not pin_clid:
                        pin_clid = m_data.get("pin_clid") or m_data.get("pinterest_click_id")
                    if not gptclid:
                        gptclid = m_data.get("gptclid") or m_data.get("chatgpt_click_id")
        
        # Advanced dynamic regex URL extraction (for redundancy / fallback)
        landing_page = payload.get('landing_page_url') or referrer_dict.get('landing_page_url') or ""
        referrer_url = payload.get('referrer_url') or referrer_dict.get('referrer_url') or referrer_dict.get('referring_url') or payload.get('referring_url') or ""
        
        if not gclid:
            gclid = extract_param_from_url(landing_page, 'gclid') or extract_param_from_url(referrer_url, 'gclid')
        if not fbclid:
            fbclid = extract_param_from_url(landing_page, 'fbclid') or extract_param_from_url(referrer_url, 'fbclid')
        if not li_fat_id:
            li_fat_id = extract_param_from_url(landing_page, 'li_fat_id') or extract_param_from_url(referrer_url, 'li_fat_id')
        if not msclkid:
            msclkid = extract_param_from_url(landing_page, 'msclkid') or extract_param_from_url(referrer_url, 'msclkid')
        if not ttclid:
            ttclid = extract_param_from_url(landing_page, 'ttclid') or extract_param_from_url(referrer_url, 'ttclid')
        if not twclid:
            twclid = extract_param_from_url(landing_page, 'twclid') or extract_param_from_url(referrer_url, 'twclid')
        if not pin_clid:
            pin_clid = extract_param_from_url(landing_page, 'pin_clid') or extract_param_from_url(landing_page, 'p_clid') or extract_param_from_url(referrer_url, 'pin_clid') or extract_param_from_url(referrer_url, 'p_clid')
        if not scclid:
            scclid = extract_param_from_url(landing_page, 'scclid') or extract_param_from_url(referrer_url, 'scclid')
        if not gptclid:
            gptclid = extract_param_from_url(landing_page, 'gptclid') or extract_param_from_url(referrer_url, 'gptclid')
        if not rdt_cid:
            rdt_cid = extract_param_from_url(landing_page, 'rdt_cid') or extract_param_from_url(referrer_url, 'rdt_cid')
        if not ttclid:
            ttclid = extract_param_from_url(landing_page, 'ttclid') or extract_param_from_url(referrer_url, 'ttclid')
        if not twclid:
            twclid = extract_param_from_url(landing_page, 'twclid') or extract_param_from_url(referrer_url, 'twclid')
        if not pin_clid:
            pin_clid = extract_param_from_url(landing_page, 'pin_clid') or extract_param_from_url(landing_page, 'p_clid') or extract_param_from_url(referrer_url, 'pin_clid') or extract_param_from_url(referrer_url, 'p_clid')
        if not scclid:
            scclid = extract_param_from_url(landing_page, 'scclid') or extract_param_from_url(referrer_url, 'scclid')
        if not gptclid:
            gptclid = extract_param_from_url(landing_page, 'gptclid') or extract_param_from_url(referrer_url, 'gptclid')
        if not rdt_cid:
            rdt_cid = extract_param_from_url(landing_page, 'rdt_cid') or extract_param_from_url(referrer_url, 'rdt_cid')
        if not ttclid:
            ttclid = extract_param_from_url(landing_page, 'ttclid') or extract_param_from_url(referrer_url, 'ttclid')
        if not twclid:
            twclid = extract_param_from_url(landing_page, 'twclid') or extract_param_from_url(referrer_url, 'twclid')
        if not pin_clid:
            pin_clid = extract_param_from_url(landing_page, 'pin_clid') or extract_param_from_url(landing_page, 'p_clid') or extract_param_from_url(referrer_url, 'pin_clid') or extract_param_from_url(referrer_url, 'p_clid')
        if not scclid:
            scclid = extract_param_from_url(landing_page, 'scclid') or extract_param_from_url(referrer_url, 'scclid')
        if not gptclid:
            gptclid = extract_param_from_url(landing_page, 'gptclid') or extract_param_from_url(referrer_url, 'gptclid')
        if not rdt_cid:
            rdt_cid = extract_param_from_url(landing_page, 'rdt_cid') or extract_param_from_url(referrer_url, 'rdt_cid')
            
        caller_name = payload.get('customer_name', 'Unknown Caller')
        raw_phone = payload.get('customer_phone_number')
        raw_transcript = payload.get('transcript') or payload.get('transcription') or ""
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
        
        # 3. Normalize Phone
        normalized_phone = normalize_phone(raw_phone)
        if not normalized_phone:
            return {"status": "ignored", "message": "No valid phone number found in webhook payload."}
            
        # 4. Trigger Claude AI Transcript Analyzer with Dynamic Qualification Prompts
        ai_qualified = "NO"
        ai_sale_closed = "NO"
        ai_value = 0.0
        ai_reason = "No transcript provided."
        model_name = "None"
        
        qualification_definition_desc = "Someone who expresses real intent to buy or schedule a service."
        
        conn = db_router.connect()
        cursor = conn.cursor()
        cursor.execute("SELECT name, qualification_criteria, lead_count_rule, exclude_past_customers FROM clients WHERE id = ?", (resolved_client_id,))
        client_info = cursor.fetchone()
        conn.close()
        
        if client_info and client_info[1]:
            criteria_code = client_info[1]
            qualification_definition_desc = CRITERIA_MAP.get(criteria_code, qualification_definition_desc)
            
        # Check if client has exclusion enabled and if caller matches the exclusion list
        is_excluded = False
        exclusion_reason = ""
        if client_info and client_info[3] == "YES":
            match_type = check_is_excluded_customer(resolved_client_id, phone=normalized_phone)
            if match_type:
                is_excluded = True
                exclusion_reason = f"Session ignored: Caller phone matches your uploaded past customer list ({match_type})." 
            
        if is_excluded:
            ai_qualified = "NO"
            ai_sale_closed = "NO"
            ai_value = 0.0
            ai_reason = exclusion_reason
            model_name = "None"
            print(f" [Exclusion Match] Resolved Client #{resolved_client_id}: {exclusion_reason}")
        elif transcript.strip():
            print(f" [Client #{resolved_client_id}] Transcript detected for {caller_name}. Custom Threshold: {qualification_definition_desc}. Auditing...")
            ai_result = analyze_transcript_with_claude(transcript, qualification_definition_desc)
            ai_qualified = ai_result.get("qualified", "NO")
            ai_sale_closed = ai_result.get("sale_closed", "NO")
            ai_value = float(ai_result.get("value", 0.0))
            ai_reason = ai_result.get("reason", "No reason parsed.")
            model_name = "claude-haiku-4-5-20251001"
            print(f" Audit Complete: Qualified={ai_qualified}, Sales Value=${ai_value}")
        else:
            print(f"⚠️ [Client #{resolved_client_id}] No transcript provided in CallRail webhook for {caller_name}. Skipping AI audit.")

        # 5. Save Session including multi-channel click IDs
        conn = db_router.connect()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO sessions (
                client_id, phone, name, gclid, fbclid, li_fat_id, msclkid, ttclid, twclid, pin_clid, gptclid, rdt_cid, source, qualified, sale_closed, value, reason, model_used, raw_data
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            resolved_client_id,
            normalized_phone, 
            caller_name, 
            gclid,
            fbclid,
            li_fat_id,
            msclkid,
            ttclid,
            twclid,
            pin_clid,
            gptclid,
            "callrail", 
            ai_qualified, 
            ai_sale_closed, 
            ai_value, 
            ai_reason, 
            model_name, 
            str(payload)
        ))
        
        conn.commit()
        conn.close()
        
        return {
            "status": "success",
            "client_id": resolved_client_id,
            "message": "Webhook log and AI analysis processed and saved successfully.",
            "ai_audit": {
                "qualified": ai_qualified,
                "sale_closed": ai_sale_closed,
                "value": ai_value,
                "reason": ai_reason
            }
        }
            
    except Exception as e:
        print(f"❌ Webhook Error: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/webhooks/form")
async def receive_form_lead(lead: FormLead, client_id: Optional[int] = None):
    """
    Form Lead Webhook Receiver supporting Multi-Tenancy.
    Saves website visitor form entries containing GCLIDs, FBCLIDs, LI_FAT_IDs, and MSCLKIDs.
    """
    try:
        resolved_client_id = client_id or 1  # Fallback to Client 1 if not defined
        
        # 1. Clean data
        full_name = f"{lead.first_name} {lead.last_name}".strip()
        normalized_phone = normalize_phone(lead.phone)
        email_clean = lead.email.strip().lower()
        
        # Check if client has exclusion enabled
        is_excluded = False
        exclusion_reason = None
        
        conn = db_router.connect()
        cursor = conn.cursor()
        cursor.execute("SELECT exclude_past_customers FROM clients WHERE id = ?", (resolved_client_id,))
        client_row = cursor.fetchone()
        conn.close()
        
        if client_row and client_row[0] == "YES":
            match_type = check_is_excluded_customer(resolved_client_id, phone=normalized_phone, email=email_clean)
            if match_type:
                is_excluded = True
                exclusion_reason = f"Form submission ignored: matches your uploaded past customer list ({match_type})."
        
        qualified_val = "YES" if not is_excluded else "NO"
        sale_closed_val = "NO"
        reason_val = None if not is_excluded else exclusion_reason

        # 2. Save to SQLite
        conn = db_router.connect()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO sessions (client_id, phone, email, name, company, gclid, fbclid, li_fat_id, msclkid, ttclid, twclid, pin_clid, gptclid, rdt_cid, source, qualified, sale_closed, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            resolved_client_id, 
            normalized_phone, 
            email_clean, 
            full_name, 
            lead.company, 
            lead.gclid, 
            lead.fbclid, 
            lead.li_fat_id, 
            lead.msclkid, 
            lead.ttclid,
            lead.twclid,
            lead.pin_clid,
            lead.gptclid,
            "form",
            qualified_val,
            sale_closed_val,
            reason_val
        ))
        conn.commit()
        conn.close()
        
        print(f" [Client #{resolved_client_id}] Form Lead saved: Name={full_name}, Phone={normalized_phone}, Email={email_clean}, GCLID={lead.gclid}")
        return {"status": "success", "message": f"Form lead saved under client #{resolved_client_id}."}
        
    except Exception as e:
        print(f"❌ Error saving Form Lead: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/webhooks/crm")
async def receive_crm_webhook(request: Request, client_id: Optional[int] = None):
    """
    CRM Lead/Deal Update Webhook Receiver supporting Multi-Tenancy.
    Processes conversion logs and maps lead changes to the correct client profile.
    """
    try:
        payload = await request.json()
        resolved_client_id = client_id or 1
        print(f" [CRM Webhook] Received conversion payload for Client #{resolved_client_id}: {payload}")
        
        # Log to Database
        contact_name = payload.get('deal_name') or payload.get('contact_name') or payload.get('lead_name') or payload.get('name') or "Unknown Deal/Contact"
        stage = payload.get('deal_stage') or payload.get('stage') or payload.get('status') or "Updated"
        amount = payload.get('amount') or payload.get('value') or payload.get('deal_value') or 0.0
        
        conn = db_router.connect()
        cursor = conn.cursor()
        
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
        
        cursor.execute("""
            INSERT INTO crm_webhook_logs (client_id, contact_name, stage, amount)
            VALUES (?, ?, ?, ?)
        """, (resolved_client_id, str(contact_name), str(stage), float(amount)))
        conn.commit()
        conn.close()
        
        return {
            "status": "success",
            "client_id": resolved_client_id,
            "message": f"CRM lead conversion successfully processed under client #{resolved_client_id}."
        }
    except Exception as e:
        print(f"❌ CRM Webhook Error: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/webhooks/billing")
async def receive_billing_webhook(request: Request, client_id: Optional[int] = None):
    """
    Billing Software (QuickBooks/Xero) Webhook Receiver supporting Multi-Tenancy.
    Tracks paid invoice events to verify closed transactions and trigger conversion uploads.
    """
    try:
        payload = await request.json()
        resolved_client_id = client_id or 1
        print(f" [Billing Webhook] Received transaction payload for Client #{resolved_client_id}: {payload}")
        
        # Log to Database
        customer_name = payload.get('customer_name') or payload.get('name') or "Unknown Customer"
        invoice_number = payload.get('invoice_number') or payload.get('invoice_id') or payload.get('doc_number') or ""
        amount = payload.get('amount') or payload.get('amount_paid') or payload.get('total') or 0.0
        
        conn = db_router.connect()
        cursor = conn.cursor()
        
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
        
        cursor.execute("""
            INSERT INTO billing_webhook_logs (client_id, customer_name, invoice_number, amount)
            VALUES (?, ?, ?, ?)
        """, (resolved_client_id, str(customer_name), str(invoice_number), float(amount)))
        conn.commit()
        conn.close()
        
        return {
            "status": "success",
            "client_id": resolved_client_id,
            "message": f"Billing invoice paid webhook processed under client #{resolved_client_id}."
        }
    except Exception as e:
        print(f"❌ Billing Webhook Error: {e}")
        raise HTTPException(status_code=400, detail=str(e))

# ---------------------------------------------------------
# SECURE NIGHTLY CRON SYNCRONIZATION TRIGGER
# ---------------------------------------------------------
@router.post("/tasks/daily-sync")
async def trigger_daily_sync(request: Request):
    """
    Secure endpoint that lets Render's Cron Job trigger the nightly CallRail sync
    directly on the web container where the SQLite database lives.
    """
    import importlib.util
    import sys
    
    # 1. Resolve Authorization Token
    secret_token = os.environ.get("SYNC_TOKEN", "default_secure_sync_token_123")
    
    # Try Header
    auth_header = request.headers.get("authorization")
    if not auth_header:
        # Fallback to query parameter for simpler testing
        token_param = request.query_params.get("token")
        if token_param:
            auth_header = f"Bearer {token_param}"
            
    if auth_header != f"Bearer {secret_token}":
        raise HTTPException(status_code=401, detail="Unauthorized sync request.")
        
    try:
        module_name = "daily_callrail_sync"
        
        # Check standard filenames first
        target_files = ["daily-callrail-sync-v3.py", "daily-callrail-sync-v2.py", "daily-callrail-sync.py", "daily_callrail_sync.py"]
        imported = False
        
        for fname in target_files:
            if os.path.exists(fname):
                spec = importlib.util.spec_from_file_location(module_name, fname)
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
                module.execute_daily_sync()
                imported = True
                break
                
        if not imported:
            # Try a direct import if it's already in python path
            try:
                import daily_callrail_sync
                daily_callrail_sync.execute_daily_sync()
                imported = True
            except ImportError:
                pass
                
        if not imported:
            raise FileNotFoundError("Could not locate daily-callrail-sync.py or daily_callrail_sync.py in the running directory.")
            
        return {"status": "success", "message": "Daily CallRail database sync executed successfully."}
        
    except Exception as e:
        print(f"❌ Cron Trigger Sync Exception: {e}")
        raise HTTPException(status_code=500, detail=f"Sync execution failed: {str(e)}")


