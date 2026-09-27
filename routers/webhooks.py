import os
import re
import json
import sqlite3
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from typing import Optional
from database.connection import db_router
from services.auth_service import is_authenticated
from services.claude_auditor import analyze_transcript_with_claude
from services.identity_matcher import normalize_phone, extract_param_from_url, check_is_excluded_customer
from models.schemas import FormLead, ExcludedCustomer
from config import CRITERIA_MAP

router = APIRouter()

def parse_chat_webhook_payload(payload: dict):
    if not isinstance(payload, dict):
        payload = {}
        
    visitor_dict = {}
    for k in ['visitor', 'customer', 'sender', 'contact', 'user']:
        if isinstance(payload.get(k), dict):
            visitor_dict = payload.get(k)
            break
            
    caller_name = (
        payload.get('name') or payload.get('customer_name') or payload.get('caller_name') or payload.get('from_name') or
        visitor_dict.get('name') or visitor_dict.get('full_name') or visitor_dict.get('first_name') or 'Chat Visitor'
    )
    
    raw_phone = (
        payload.get('phone') or payload.get('customer_phone_number') or payload.get('caller_number') or payload.get('from') or payload.get('phone_number') or
        visitor_dict.get('phone') or visitor_dict.get('phone_number') or visitor_dict.get('number') or visitor_dict.get('mobile')
    )
    
    email_clean = (
        payload.get('email') or payload.get('customer_email') or
        visitor_dict.get('email') or ''
    ).strip().lower()
    
    custom_vars = payload.get('custom_variables') or payload.get('custom_fields') or payload.get('session_data') or payload.get('user_attributes') or {}
    if not isinstance(custom_vars, dict):
        custom_vars = {}
        
    gclid = payload.get('gclid') or custom_vars.get('gclid')
    fbclid = payload.get('fbclid') or custom_vars.get('fbclid')
    msclkid = payload.get('msclkid') or custom_vars.get('msclkid')
    li_fat_id = payload.get('li_fat_id') or custom_vars.get('li_fat_id')
    ttclid = payload.get('ttclid') or custom_vars.get('ttclid')
    twclid = payload.get('twclid') or custom_vars.get('twclid')
    pin_clid = payload.get('pin_clid') or custom_vars.get('pin_clid')
    scclid = payload.get('scclid') or custom_vars.get('scclid')
    gptclid = payload.get('gptclid') or custom_vars.get('gptclid')
    rdt_cid = payload.get('rdt_cid') or custom_vars.get('rdt_cid')
    
    ref_id = payload.get('ref_id') or payload.get('reference') or payload.get('ref')
    if not ref_id and isinstance(payload.get('text'), str):
        ref_match = re.search(r'\(Ref:\s*(lg_[a-zA-Z0-9]+)\)', payload.get('text', ''))
        if ref_match:
            ref_id = ref_match.group(1)
            
    raw_transcript = (
        payload.get('transcript') or payload.get('transcription') or payload.get('messages') or 
        payload.get('text') or payload.get('conversation') or payload.get('chat_history') or payload.get('body') or ''
    )
    
    transcript = ''
    if isinstance(raw_transcript, str):
        transcript = raw_transcript
    elif isinstance(raw_transcript, list):
        segments = []
        for s in raw_transcript:
            if isinstance(s, dict):
                speaker = s.get('speaker') or s.get('author') or s.get('role') or s.get('name') or s.get('sender') or 'Speaker'
                if isinstance(speaker, dict):
                    speaker = speaker.get('name') or speaker.get('role') or 'Speaker'
                text = s.get('text') or s.get('message') or s.get('body') or s.get('content') or ''
                if text:
                    segments.append(f'[{speaker}]: {text}')
            elif isinstance(s, str):
                segments.append(s)
        transcript = '\n'.join(segments)
    elif isinstance(raw_transcript, dict):
        transcript = raw_transcript.get('text') or raw_transcript.get('message') or str(raw_transcript)

    return {
        'name': str(caller_name),
        'phone': str(raw_phone) if raw_phone else '',
        'email': email_clean,
        'gclid': gclid,
        'fbclid': fbclid,
        'msclkid': msclkid,
        'li_fat_id': li_fat_id,
        'ttclid': ttclid,
        'twclid': twclid,
        'pin_clid': pin_clid,
        'scclid': scclid,
        'gptclid': gptclid,
        'rdt_cid': rdt_cid,
        'ref_id': ref_id,
        'transcript': transcript
    }

# ---------------------------------------------------------
# PRE-CHAT CLICK ID LOGGING
# ---------------------------------------------------------
@router.post("/webhooks/chat-session")
@router.get("/webhooks/chat-session")
async def save_chat_pre_session(request: Request, client_id: Optional[int] = None):
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
            payload = dict(request.query_params)
            
        resolved_client_id = client_id or int(payload.get('client_id', 1))
        ref_id = payload.get('ref_id') or payload.get('reference') or f"lg_{os.urandom(4).hex()}"
        
        raw_phone = payload.get('phone') or payload.get('customer_phone_number')
        email_clean = str(payload.get('email', '')).strip().lower()
        
        gclid = payload.get('gclid')
        fbclid = payload.get('fbclid')
        li_fat_id = payload.get('li_fat_id')
        msclkid = payload.get('msclkid')
        ttclid = payload.get('ttclid')
        twclid = payload.get('twclid')
        pin_clid = payload.get('pin_clid')
        scclid = payload.get('scclid')
        gptclid = payload.get('gptclid')
        rdt_cid = payload.get('rdt_cid')
        
        normalized_phone = normalize_phone(raw_phone) if raw_phone else ""
        
        conn = db_router.connect()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO chat_pre_sessions (
                client_id, ref_id, phone, email, gclid, fbclid, li_fat_id, msclkid, ttclid, twclid, pin_clid, scclid, gptclid, rdt_cid
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            resolved_client_id, ref_id, normalized_phone, email_clean, gclid, fbclid, li_fat_id, msclkid, ttclid, twclid, pin_clid, scclid, gptclid, rdt_cid
        ))
        conn.commit()
        conn.close()
        
        return {"status": "success", "ref_id": ref_id, "client_id": resolved_client_id, "message": "Pre-chat click session saved successfully."}
    except Exception as e:
        return {"status": "error", "message": f"Failed to save pre-chat session: {e}"}

# ---------------------------------------------------------
# UNIFIED LIVE CHAT & MESSAGING WEBHOOK PROCESSOR
# ---------------------------------------------------------
async def process_chat_webhook_event(payload: dict, client_id: Optional[int], provider_source: str):
    try:
        parsed = parse_chat_webhook_payload(payload)
        resolved_client_id = client_id or 1
        
        normalized_phone = normalize_phone(parsed['phone']) if parsed['phone'] else ""
        email_clean = parsed['email']
        ref_id = parsed['ref_id']
        
        gclid = parsed['gclid']
        fbclid = parsed['fbclid']
        msclkid = parsed['msclkid']
        li_fat_id = parsed['li_fat_id']
        ttclid = parsed['ttclid']
        twclid = parsed['twclid']
        pin_clid = parsed['pin_clid']
        scclid = parsed['scclid']
        gptclid = parsed['gptclid']
        rdt_cid = parsed['rdt_cid']
        
        # Identity match against chat_pre_sessions if missing Click IDs
        if not any([gclid, fbclid, msclkid, li_fat_id]):
            conn = db_router.connect()
            cursor = conn.cursor()
            match_row = None
            if ref_id:
                cursor.execute("SELECT gclid, fbclid, li_fat_id, msclkid, ttclid, twclid, pin_clid, scclid, gptclid, rdt_cid FROM chat_pre_sessions WHERE ref_id = ?", (ref_id,))
                match_row = cursor.fetchone()
            if not match_row and normalized_phone:
                cursor.execute("SELECT gclid, fbclid, li_fat_id, msclkid, ttclid, twclid, pin_clid, scclid, gptclid, rdt_cid FROM chat_pre_sessions WHERE phone = ? ORDER BY created_at DESC LIMIT 1", (normalized_phone,))
                match_row = cursor.fetchone()
            if not match_row and email_clean:
                cursor.execute("SELECT gclid, fbclid, li_fat_id, msclkid, ttclid, twclid, pin_clid, scclid, gptclid, rdt_cid FROM chat_pre_sessions WHERE email = ? ORDER BY created_at DESC LIMIT 1", (email_clean,))
                match_row = cursor.fetchone()
            conn.close()
            
            if match_row:
                gclid = gclid or match_row[0]
                fbclid = fbclid or match_row[1]
                li_fat_id = li_fat_id or match_row[2]
                msclkid = msclkid or match_row[3]
                ttclid = ttclid or match_row[4]
                twclid = twclid or match_row[5]
                pin_clid = pin_clid or match_row[6]
                scclid = scclid or match_row[7]
                gptclid = gptclid or match_row[8]
                rdt_cid = rdt_cid or match_row[9]

        transcript = parsed['transcript']
        caller_name = parsed['name']
        
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
            match_type = check_is_excluded_customer(resolved_client_id, phone=normalized_phone, email=email_clean)
            if match_type:
                is_excluded = True
                exclusion_reason = f"Chat session ignored: Customer matches uploaded past customer list ({match_type})."
                
        if is_excluded:
            ai_qualified = "NO"
            ai_sale_closed = "NO"
            ai_value = 0.0
            ai_reason = exclusion_reason
            model_name = "None"
        elif transcript.strip():
            ai_result = analyze_transcript_with_claude(transcript, qualification_definition_desc)
            ai_qualified = ai_result.get("qualified", "NO")
            ai_sale_closed = ai_result.get("sale_closed", "NO")
            ai_value = float(ai_result.get("value", 0.0))
            ai_reason = ai_result.get("reason", "No reason parsed.")
            model_name = "claude-haiku-4-5-20251001"
            
        conn = db_router.connect()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO sessions (
                client_id, phone, email, name, gclid, fbclid, li_fat_id, msclkid, ttclid, twclid, pin_clid, scclid, gptclid, rdt_cid, source, qualified, sale_closed, value, reason, model_used, raw_data
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            resolved_client_id, normalized_phone, email_clean, caller_name,
            gclid, fbclid, li_fat_id, msclkid, ttclid, twclid, pin_clid, scclid, gptclid, rdt_cid,
            provider_source, ai_qualified, ai_sale_closed, ai_value, ai_reason, model_name, str(payload)
        ))
        conn.commit()
        conn.close()
        
        return {
            "status": "success",
            "client_id": resolved_client_id,
            "source": provider_source,
            "message": f"{provider_source.upper()} chat transcript successfully audited and logged.",
            "ai_audit": {
                "qualified": ai_qualified,
                "sale_closed": ai_sale_closed,
                "value": ai_value,
                "reason": ai_reason
            }
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/webhooks/livechat")
async def receive_livechat_webhook(request: Request, client_id: Optional[int] = None):
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
        return await process_chat_webhook_event(payload, client_id, "livechat")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/webhooks/whatsapp")
@router.post("/webhooks/whatsapp")
async def receive_whatsapp_webhook(request: Request, client_id: Optional[int] = None):
    if request.method == "GET":
        mode = request.query_params.get("hub.mode")
        token = request.query_params.get("hub.verify_token")
        challenge = request.query_params.get("hub.challenge")
        if mode == "subscribe" and challenge:
            return int(challenge)
        return {"status": "whatsapp_webhook_verified"}
    try:
        payload = await request.json()
        return await process_chat_webhook_event(payload, client_id, "whatsapp")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/webhooks/telegram")
async def receive_telegram_webhook(request: Request, client_id: Optional[int] = None):
    try:
        payload = await request.json()
        return await process_chat_webhook_event(payload, client_id, "telegram")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/webhooks/viber")
async def receive_viber_webhook(request: Request, client_id: Optional[int] = None):
    try:
        payload = await request.json()
        return await process_chat_webhook_event(payload, client_id, "viber")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
