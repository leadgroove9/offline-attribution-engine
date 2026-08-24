import os
import sqlite3
import re
import json
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional
from anthropic import Anthropic

# Initialize FastAPI App
app = FastAPI(
    title="Offline Attribution Engine",
    description="MVP for matching offline leads/sales with Google click IDs (GCLID) and AI analysis",
    version="2.0.0"
)

# ---------------------------------------------------------
# DATABASE CONFIGURATION (SQLite)
# ---------------------------------------------------------
DB_PATH = "offline_attribution.db"

def init_db():
    """Initializes the database and creates the necessary tables."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Table to store Call and Webhook sessions, updated for Claude AI audits
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    
    conn.commit()
    conn.close()

# Run database initialization
init_db()


# ---------------------------------------------------------
# ANTHROPIC CLAUDE CONFIGURATION
# ---------------------------------------------------------
# Hardcoded verified API Key (for local development sandbox)
# Note: For production deployments, we will load this from environment variables.
API_KEY = "API_KEY = os.environ.get("ANTHROPIC_API_KEY")"
client = Anthropic(api_key=API_KEY)

def analyze_transcript_with_claude(transcript: str) -> dict:
    """
    Sends a phone transcript to Claude 4.5 Haiku to evaluate qualification and sales value.
    Strips markdown wrapper symbols from the response safely.
    """
    if not transcript or not transcript.strip():
        return {
            "qualified": "NO",
            "sale_closed": "NO",
            "value": 0.0,
            "reason": "No transcript available for analysis."
        }

    system_prompt = (
        "You are an expert sales auditor and conversion tracking engine for local service businesses.\n"
        "Your job is to read a phone call transcript and determine three things:\n"
        "1. Is the caller a 'Qualified Lead'? (Did they express real intent to buy or schedule a service? Return 'YES' or 'NO')\n"
        "2. Was a sale 'Closed' during the call? (Did they agree to purchase, pay a deposit, or book a paid job? Return 'YES' or 'NO')\n"
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

    try:
        # Use your verified active next-gen model
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            system=system_prompt,
            messages=[
                {"role": "user", "content": f"Analyze this call transcript:\n\n{transcript}"}
            ]
        )
        
        # Get raw response and strip any surrounding white spaces
        response_text = message.content[0].text.strip()
        
        # Safe clean-up for Markdown codeblock formatting if Claude added it
        if response_text.startswith("```"):
            # Remove ```json from start and ``` from end
            lines = response_text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines[-1].startswith("```"):
                lines = lines[:-1]
            response_text = "\n".join(lines).strip()

        # Parse into a native Python dictionary
        result = json.loads(response_text)
        return result

    except json.JSONDecodeError:
        print("❌ Error: Claude did not return valid JSON.")
        return {
            "qualified": "NO",
            "sale_closed": "NO",
            "value": 0.0,
            "reason": f"Failed to parse Claude's response. Raw text: {response_text}"
        }
    except Exception as e:
        print(f"❌ API Error: {e}")
        return {
            "qualified": "NO",
            "sale_closed": "NO",
            "value": 0.0,
            "reason": f"Anthropic API Error: {str(e)}"
        }


# ---------------------------------------------------------
# HELPERS (Normalization & Clean-up)
# ---------------------------------------------------------
def normalize_phone(phone_str: str) -> str:
    """Strips non-digits and normalizes to E.164-ish format."""
    if not phone_str:
        return ""
    cleaned = re.sub(r'\D', '', phone_str)
    if len(cleaned) == 10:
        cleaned = "1" + cleaned
    return cleaned


# ---------------------------------------------------------
# WEBHOOK DATA SCHEMAS (Pydantic Models)
# ---------------------------------------------------------
class FormLead(BaseModel):
    first_name: str
    last_name: str
    email: str
    phone: str
    company: Optional[str] = None
    gclid: Optional[str] = None


# ---------------------------------------------------------
# ENDPOINTS (Routes for Render)
# ---------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def read_root():
    """Live Status Landing Page."""
    return """
    <html>
        <head>
            <title>Attribution Engine Live with AI 🤖</title>
            <style>
                body { font-family: Arial, sans-serif; text-align: center; padding-top: 100px; background-color: #f4f6f9; }
                .container { display: inline-block; background: white; padding: 40px; border-radius: 10px; box-shadow: 0px 4px 10px rgba(0,0,0,0.1); }
                h1 { color: #2e7d32; margin-bottom: 10px; }
                p { color: #555; font-size: 18px; }
                .badge { background-color: #e8f5e9; color: #2e7d32; padding: 5px 15px; border-radius: 15px; font-weight: bold; }
                .feature-list { text-align: left; margin-top: 20px; color: #333; line-height: 1.6; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Attribution Engine is Live! 🚀</h1>
                <p>Status: <span class="badge">Healthy, Listening & AI-Enabled</span></p>
                <p>Welcome Corey. Your FastAPI server is connected to SQLite and Claude 4.5 Haiku.</p>
                <div class="feature-list">
                    <h3>Enabled Capabilities:</h3>
                    <ul>
                        <li>📥 <strong>/webhooks/callrail</strong>: Listens for incoming phone logs & analyzes transcripts.</li>
                        <li>📥 <strong>/webhooks/form</strong>: Logs web forms containing visitor Google Click IDs (GCLID).</li>
                        <li>🧠 <strong>Claude 4.5 Haiku</strong>: Audits conversations for qualified leads and closed sales value.</li>
                        <li>🗄️ <strong>SQLite Database</strong>: Locally tracks, matches, and logs all events instantly.</li>
                    </ul>
                </div>
            </div>
        </body>
    </html>
    """


@app.post("/webhooks/callrail")
async def receive_callrail_webhook(request: Request):
    """
    CallRail Webhook Receiver.
    Extracts caller profile, triggers the Claude AI Transcript Analyzer,
    and saves the completed matched records directly to SQLite.
    """
    try:
        # 1. Parse the incoming JSON data from CallRail
        payload = await request.json()
        
        # 2. Extract key fields
        gclid = payload.get('google_click_id') or payload.get('referrer', {}).get('gclid')
        caller_name = payload.get('customer_name', 'Unknown Caller')
        raw_phone = payload.get('customer_phone_number')
        
        # CallRail usually nests the call transcription inside 'transcript' or 'transcription'
        transcript = payload.get('transcript') or payload.get('transcription') or ""
        
        # 3. Normalize the phone number
        normalized_phone = normalize_phone(raw_phone)
        
        if not normalized_phone:
            return {"status": "ignored", "message": "No valid phone number in webhook payload."}
            
        # 4. Trigger the Claude AI Transcript Analyzer if a transcript exists
        ai_qualified = "NO"
        ai_sale_closed = "NO"
        ai_value = 0.0
        ai_reason = "No transcript provided."
        model_name = "None"
        
        if transcript.strip():
            print(f"🧠 Transcript detected for {caller_name}. Analyzing with Claude...")
            ai_result = analyze_transcript_with_claude(transcript)
            ai_qualified = ai_result.get("qualified", "NO")
            ai_sale_closed = ai_result.get("sale_closed", "NO")
            ai_value = float(ai_result.get("value", 0.0))
            ai_reason = ai_result.get("reason", "No reason parsed.")
            model_name = "claude-haiku-4-5-20251001"
            print(f"🎯 AI Analysis complete: Qualified={ai_qualified}, Value=${ai_value}")
        else:
            print(f"⚠️ No transcript provided in CallRail webhook for {caller_name}. Skipping AI audit.")

        # 5. Save all parameters including the AI analysis results to SQLite
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO sessions (
                phone, name, gclid, source, qualified, sale_closed, value, reason, model_used, raw_data
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            normalized_phone, 
            caller_name, 
            gclid, 
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
            "message": "Call log and AI analysis processed and saved.",
            "ai_audit": {
                "qualified": ai_qualified,
                "sale_closed": ai_sale_closed,
                "value": ai_value,
                "reason": ai_reason
            }
        }
            
    except Exception as e:
        print(f"❌ Error processing CallRail Webhook: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/webhooks/form")
async def receive_form_lead(lead: FormLead):
    """
    Form Lead Webhook Receiver.
    Saves website visitor form entries containing GCLIDs to allow back-end pairing.
    """
    try:
        # 1. Clean data
        full_name = f"{lead.first_name} {lead.last_name}".strip()
        normalized_phone = normalize_phone(lead.phone)
        email_clean = lead.email.strip().lower()
        
        # 2. Save to SQLite
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO sessions (phone, email, name, company, gclid, source)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (normalized_phone, email_clean, full_name, lead.company, lead.gclid, "form"))
        conn.commit()
        conn.close()
        
        print(f"📝 Form Lead saved: Name={full_name}, Phone={normalized_phone}, Email={email_clean}, GCLID={lead.gclid}")
        return {"status": "success", "message": "Form lead saved successfully."}
        
    except Exception as e:
        print(f"❌ Error saving Form Lead: {e}")
        raise HTTPException(status_code=400, detail=str(e))
