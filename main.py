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
    version="3.0.0"
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
# Securely loaded from Render/environment variables
API_KEY = os.environ.get("ANTHROPIC_API_KEY")
client = Anthropic(api_key=API_KEY) if API_KEY else None

def clean_json_string(text: str) -> str:
    """
    Strips out markdown code block wrappers like ```json ... ``` if Claude
    accidentally includes them in the raw response.
    """
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()

def analyze_transcript_with_claude(transcript: str) -> dict:
    """
    Sends a phone transcript to Claude 4.5 Haiku to evaluate qualification and sales value.
    Strips markdown wrapper symbols from the response safely.
    """
    if not client:
        return {
            "qualified": "NO",
            "sale_closed": "NO",
            "value": 0.0,
            "reason": "Anthropic API Key is not configured on the server."
        }
        
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
        cleaned_text = clean_json_string(response_text)

        # Parse into a native Python dictionary
        result = json.loads(cleaned_text)
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
    """Live Status Landing Page with Dashboard link."""
    return """
    <html>
        <head>
            <title>Attribution Engine Live with AI 🤖</title>
            <style>
                body { font-family: Arial, sans-serif; text-align: center; padding-top: 100px; background-color: #f4f6f9; }
                .container { display: inline-block; background: white; padding: 40px; border-radius: 10px; box-shadow: 0px 4px 10px rgba(0,0,0,0.1); max-width: 600px; }
                h1 { color: #2e7d32; margin-bottom: 10px; }
                p { color: #555; font-size: 18px; }
                .badge { background-color: #e8f5e9; color: #2e7d32; padding: 5px 15px; border-radius: 15px; font-weight: bold; }
                .btn { display: inline-block; background-color: #2e7d32; color: white; padding: 12px 24px; text-decoration: none; font-size: 16px; font-weight: bold; border-radius: 5px; margin-top: 20px; transition: background 0.2s; }
                .btn:hover { background-color: #1b5e20; }
                .feature-list { text-align: left; margin-top: 20px; color: #333; line-height: 1.6; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Attribution Engine is Live! 🚀</h1>
                <p>Status: <span class="badge">Healthy, Listening & AI-Enabled</span></p>
                <p>Welcome Corey. Your FastAPI server is connected to SQLite and Claude 4.5 Haiku.</p>
                
                <a href="/dashboard" class="btn">📊 View Lead & Attribution Dashboard</a>
                
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


@app.get("/dashboard", response_class=HTMLResponse)
def view_dashboard():
    """Live web-based conversion tracking dashboard."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Pull all completed sessions, sorting newest first
        cursor.execute("""
            SELECT id, phone, email, name, company, gclid, source, qualified, sale_closed, value, reason, created_at
            FROM sessions
            ORDER BY created_at DESC
        """)
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        return f"<html><body><h3>❌ Database Error: {e}</h3></body></html>"

    # Count analytics
    total_leads = len(rows)
    qualified_leads = sum(1 for r in rows if r[7] == 'YES')
    sales_closed = sum(1 for r in rows if r[8] == 'YES')
    total_revenue = sum(float(r[9] or 0.0) for r in rows)

    # Convert rows to HTML table items
    table_rows_html = ""
    for r in rows:
        id_val, phone, email, name, company, gclid, source, qualified, sale_closed, value, reason, created_at = r
        
        # Beautify badges
        qual_badge = '<span style="background: #e8f5e9; color: #2e7d32; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 12px;">YES</span>' if qualified == 'YES' else '<span style="background: #ffebee; color: #c62828; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 12px;">NO</span>'
        closed_badge = '<span style="background: #e8f5e9; color: #2e7d32; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 12px;">YES</span>' if sale_closed == 'YES' else '<span style="background: #ffebee; color: #c62828; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 12px;">NO</span>'
        gclid_display = f'<code style="background: #f1f3f4; padding: 2px 6px; border-radius: 4px; font-size: 12px; word-break: break-all;">{gclid}</code>' if gclid else '<span style="color: #999; font-style: italic;">None</span>'
        value_display = f"<strong>${value:,.2f}</strong>" if value and value > 0 else '<span style="color: #999;">$0.00</span>'
        
        table_rows_html += f"""
        <tr>
            <td>{id_val}</td>
            <td><small>{created_at}</small></td>
            <td><span class="badge-source">{source.upper()}</span></td>
            <td><strong>{name or 'Unknown'}</strong><br><small style="color:#666;">{phone}</small></td>
            <td>{gclid_display}</td>
            <td>{qual_badge}</td>
            <td>{closed_badge}</td>
            <td>{value_display}</td>
            <td><small>{reason or 'N/A'}</small></td>
        </tr>
        """

    if not table_rows_html:
        table_rows_html = '<tr><td colspan="9" style="text-align: center; color: #888; padding: 40px;">No lead sessions recorded yet. Send a test webhook to populate this dashboard!</td></tr>'

    return f"""
    <!DOCTYPE html>
    <html>
        <head>
            <title>Offline Lead & Conversion Dashboard 📊</title>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif; background-color: #f4f6f9; color: #333; margin: 0; padding: 20px; }}
                .container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 30px; border-radius: 12px; box-shadow: 0px 4px 15px rgba(0,0,0,0.05); }}
                header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #eaeaea; padding-bottom: 20px; margin-bottom: 30px; }}
                h1 {{ margin: 0; color: #1a237e; font-size: 28px; }}
                .btn-home {{ background-color: #1a237e; color: white; padding: 10px 18px; text-decoration: none; border-radius: 5px; font-weight: bold; font-size: 14px; transition: background 0.2s; }}
                .btn-home:hover {{ background-color: #0d1b2a; }}
                
                /* Analytics Stats */
                .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin-bottom: 30px; }}
                .stat-card {{ background: #fafafa; border: 1px solid #e0e0e0; padding: 20px; border-radius: 8px; text-align: center; }}
                .stat-card h3 {{ margin: 0 0 10px 0; font-size: 14px; color: #666; text-transform: uppercase; letter-spacing: 0.5px; }}
                .stat-card .value {{ font-size: 28px; font-weight: bold; color: #1a237e; margin: 0; }}
                .stat-card.rev .value {{ color: #2e7d32; }}
                
                /* Table Styles */
                .table-responsive {{ overflow-x: auto; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 14px; }}
                th, td {{ padding: 12px 15px; text-align: left; border-bottom: 1px solid #eaeaea; }}
                th {{ background-color: #f8f9fa; color: #495057; font-weight: 600; text-transform: uppercase; font-size: 12px; }}
                tr:hover {{ background-color: #fdfdfd; }}
                .badge-source {{ background: #e0f2f1; color: #00695c; font-size: 11px; padding: 2px 6px; border-radius: 4px; font-weight: bold; }}
            </style>
        </head>
        <body>
            <div class="container">
                <header>
                    <div>
                        <h1>Offline Lead & Conversion Dashboard 📊</h1>
                        <p style="margin: 5px 0 0 0; color: #666; font-size: 14px;">Real-time AI conversions and click-ID attribution matched by Claude 4.5 Haiku</p>
                    </div>
                    <a href="/" class="btn-home">⬅️ Back Home</a>
                </header>

                <!-- Stats Cards -->
                <div class="stats-grid">
                    <div class="stat-card">
                        <h3>Total Lead Sessions</h3>
                        <p class="value">{total_leads}</p>
                    </div>
                    <div class="stat-card">
                        <h3>Qualified Leads 🟢</h3>
                        <p class="value">{qualified_leads}</p>
                    </div>
                    <div class="stat-card">
                        <h3>Sales Closed 🤝</h3>
                        <p class="value">{sales_closed}</p>
                    </div>
                    <div class="stat-card rev">
                        <h3>Tracked Sales Value 💰</h3>
                        <p class="value">${total_revenue:,.2f}</p>
                    </div>
                </div>

                <!-- Table -->
                <div class="table-responsive">
                    <table>
                        <thead>
                            <tr>
                                <th>ID</th>
                                <th>Timestamp</th>
                                <th>Source</th>
                                <th>Lead Contact</th>
                                <th>Google Click ID (GCLID)</th>
                                <th>Qualified</th>
                                <th>Closed</th>
                                <th>Value</th>
                                <th>Claude Decision Reasoning</th>
                            </tr>
                        </thead>
                        <tbody>
                            {table_rows_html}
                        </tbody>
                    </table>
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
