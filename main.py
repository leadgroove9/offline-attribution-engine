import os
import sqlite3
import re
import json
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel
from typing import Optional
from anthropic import Anthropic

# Initialize FastAPI App
app = FastAPI(
    title="Offline Attribution Engine (Multi-Tenant)",
    description="Multi-tenant agency platform for tracking offline leads/sales and AI audits",
    version="5.0.0"
)

# ---------------------------------------------------------
# DATABASE CONFIGURATION (SQLite)
# ---------------------------------------------------------
DB_PATH = "offline_attribution.db"

def init_db():
    """Initializes the database, creates necessary tables, and seeds initial clients."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. Create Clients Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            callrail_company_id TEXT UNIQUE,
            google_ads_customer_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 2. Create Sessions Table (Multi-Tenant)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER DEFAULT 1,
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (client_id) REFERENCES clients (id)
        )
    """)
    
    # 3. Seed Mock Clients if empty
    cursor.execute("SELECT COUNT(*) FROM clients")
    if cursor.fetchone()[0] == 0:
        mock_clients = [
            ("Priority Plumbing", "comp_plumbing", "123-456-7890"),
            ("Apex HVAC & Air", "comp_hvac", "987-654-3210"),
            ("Metro Dental Care", "comp_dental", "555-123-4567")
        ]
        cursor.executemany("""
            INSERT INTO clients (name, callrail_company_id, google_ads_customer_id)
            VALUES (?, ?, ?)
        """, mock_clients)
        print("🌱 Seeded 3 mock agency clients successfully!")
    
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
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            system=system_prompt,
            messages=[
                {"role": "user", "content": f"Analyze this call transcript:\n\n{transcript}"}
            ]
        )
        
        response_text = message.content[0].text.strip()
        cleaned_text = clean_json_string(response_text)
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
                h1 { color: #1a237e; margin-bottom: 10px; }
                p { color: #555; font-size: 18px; }
                .badge { background-color: #e8f5e9; color: #2e7d32; padding: 5px 15px; border-radius: 15px; font-weight: bold; }
                .btn { display: inline-block; background-color: #1a237e; color: white; padding: 12px 24px; text-decoration: none; font-size: 16px; font-weight: bold; border-radius: 5px; margin-top: 20px; transition: background 0.2s; }
                .btn:hover { background-color: #0d1b2a; }
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
def view_dashboard(client_id: Optional[int] = None):
    """Live web-based conversion tracking dashboard."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Load all clients for dropdown
        cursor.execute("SELECT id, name FROM clients ORDER BY name ASC")
        clients = cursor.fetchall()
        
        # Isolate database query based on client_id filter
        if client_id:
            cursor.execute("SELECT name, google_ads_customer_id FROM clients WHERE id = ?", (client_id,))
            client_meta = cursor.fetchone()
            client_name_header = client_meta[0] if client_meta else "Unknown Client"
            client_ads_id = client_meta[1] if client_meta else "N/A"
            
            cursor.execute("""
                SELECT s.id, s.phone, s.email, s.name, s.company, s.gclid, s.source, s.qualified, s.sale_closed, s.value, s.reason, s.created_at, c.name
                FROM sessions s
                JOIN clients c ON s.client_id = c.id
                WHERE s.client_id = ?
                ORDER BY s.created_at DESC
            """, (client_id,))
        else:
            client_name_header = "All Clients Overview"
            client_ads_id = "Multiple Accounts"
            
            cursor.execute("""
                SELECT s.id, s.phone, s.email, s.name, s.company, s.gclid, s.source, s.qualified, s.sale_closed, s.value, s.reason, s.created_at, c.name
                FROM sessions s
                JOIN clients c ON s.client_id = c.id
                ORDER BY s.created_at DESC
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

    # Populate dropdown options
    dropdown_options = '<option value="">-- All Agency Clients --</option>'
    for cid, cname in clients:
        selected_attr = 'selected' if client_id == cid else ''
        dropdown_options += f'<option value="{cid}" {selected_attr}>{cname}</option>'

    # Dynamic headers for All Clients vs Isolated Client view
    client_th_html = '<th>Client Account</th>' if not client_id else ''
    
    # Convert rows to HTML table items
    table_rows_html = ""
    for r in rows:
        id_val, phone, email, name_val, company, gclid, source, qualified, sale_closed, value, reason, created_at, client_name_row = r
        
        # Format badges
        qual_badge = '<span style="background: #e8f5e9; color: #2e7d32; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 12px;">YES</span>' if qualified == 'YES' else '<span style="background: #ffebee; color: #c62828; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 12px;">NO</span>'
        closed_badge = '<span style="background: #e8f5e9; color: #2e7d32; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 12px;">YES</span>' if sale_closed == 'YES' else '<span style="background: #ffebee; color: #c62828; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 12px;">NO</span>'
        gclid_display = f'<code style="background: #f1f3f4; padding: 2px 6px; border-radius: 4px; font-size: 12px; word-break: break-all;">{gclid}</code>' if gclid else '<span style="color: #999; font-style: italic;">None</span>'
        value_display = f"<strong>${value:,.2f}</strong>" if value and value > 0 else '<span style="color: #999;">$0.00</span>'
        
        client_column_html = f'<td><span class="client-badge">{client_name_row}</span></td>' if not client_id else ''
        
        table_rows_html += f"""
        <tr>
            <td>{id_val}</td>
            {client_column_html}
            <td><small>{created_at}</small></td>
            <td><span class="badge-source">{source.upper()}</span></td>
            <td><strong>{name_val or 'Unknown'}</strong><br><small style="color:#666;">{phone}</small></td>
            <td>{gclid_display}</td>
            <td>{qual_badge}</td>
            <td>{closed_badge}</td>
            <td>{value_display}</td>
            <td><small>{reason or 'N/A'}</small></td>
        </tr>
        """

    if not table_rows_html:
        table_rows_html = '<tr><td colspan="10" style="text-align: center; color: #888; padding: 40px;">No lead sessions recorded yet. Send a test webhook to populate this dashboard!</td></tr>'

    # Build the action button for exporting
    if client_id:
        export_btn_html = f'<a href="/dashboard/export?client_id={client_id}" class="btn-export">📥 Export Google Ads CSV</a>'
    else:
        export_btn_html = '<button class="btn-export disabled" onclick="alert(\'Please select a specific client from the dropdown above to export their Google Ads offline conversion CSV!\')" style="opacity:0.6; cursor:not-allowed;">📥 Select a Client to Export</button>'

    return f"""
    <!DOCTYPE html>
    <html>
        <head>
            <title>Offline Lead & Conversion Dashboard 📊</title>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif; background-color: #f4f6f9; color: #333; margin: 0; padding: 20px; }}
                .container {{ max-width: 1300px; margin: 0 auto; background: white; padding: 30px; border-radius: 12px; box-shadow: 0px 4px 15px rgba(0,0,0,0.05); }}
                header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #eaeaea; padding-bottom: 20px; margin-bottom: 30px; flex-wrap: wrap; gap: 15px; }}
                h1 {{ margin: 0; color: #1a237e; font-size: 26px; }}
                .client-selector-container {{ display: flex; align-items: center; gap: 10px; background: #e8eaf6; padding: 10px 15px; border-radius: 8px; border: 1px solid #c5cae9; }}
                .client-label {{ font-weight: bold; color: #1a237e; font-size: 14px; }}
                .client-select {{ padding: 8px 12px; font-size: 14px; border-radius: 5px; border: 1px solid #9fa8da; outline: none; font-weight: 600; cursor: pointer; color: #1a237e; }}
                .client-select:focus {{ border-color: #1a237e; }}
                
                /* Analytics Stats */
                .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 20px; margin-bottom: 30px; }}
                .stat-card {{ background: #fafafa; border: 1px solid #e0e0e0; padding: 20px; border-radius: 8px; text-align: center; }}
                .stat-card h3 {{ margin: 0 0 10px 0; font-size: 13px; color: #666; text-transform: uppercase; letter-spacing: 0.5px; }}
                .stat-card .value {{ font-size: 28px; font-weight: bold; color: #1a237e; margin: 0; }}
                .stat-card.rev .value {{ color: #2e7d32; }}
                
                /* Table Styles */
                .table-responsive {{ overflow-x: auto; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 14px; }}
                th, td {{ padding: 12px 15px; text-align: left; border-bottom: 1px solid #eaeaea; }}
                th {{ background-color: #f8f9fa; color: #495057; font-weight: 600; text-transform: uppercase; font-size: 11px; letter-spacing: 0.5px; }}
                tr:hover {{ background-color: #fdfdfd; }}
                .badge-source {{ background: #e0f2f1; color: #00695c; font-size: 11px; padding: 2px 6px; border-radius: 4px; font-weight: bold; }}
                .client-badge {{ background: #eceff1; color: #37474f; font-size: 11px; padding: 3px 8px; border-radius: 4px; font-weight: bold; border: 1px solid #cfd8dc; }}
                
                .btn-export {{ display: inline-block; background-color: #2e7d32; color: white; padding: 10px 18px; text-decoration: none; border-radius: 5px; font-weight: bold; font-size: 14px; border: none; transition: background 0.2s; cursor: pointer; }}
                .btn-export:hover {{ background-color: #1b5e20; }}
            </style>
        </head>
        <body>
            <div class="container">
                <header>
                    <div>
                        <h1>{client_name_header} 📊</h1>
                        <p style="margin: 5px 0 0 0; color: #666; font-size: 14px;">Google Ads Account: <strong>{client_ads_id}</strong> | Multi-Tenant Agency Engine</p>
                    </div>
                    
                    <div class="client-selector-container">
                        <span class="client-label">Viewing Account:</span>
                        <select class="client-select" onchange="window.location.href='/dashboard?client_id='+this.value">
                            {dropdown_options}
                        </select>
                    </div>
                </header>

                <!-- Stats Cards -->
                <div class="stats-grid">
                    <div class="stat-card">
                        <h3>Tracked Sessions</h3>
                        <p class="value">{total_leads}</p>
                    </div>
                    <div class="stat-card">
                        <h3>AI Qualified Leads 🟢</h3>
                        <p class="value">{qualified_leads}</p>
                    </div>
                    <div class="stat-card">
                        <h3>Sales Closed 🤝</h3>
                        <p class="value">{sales_closed}</p>
                    </div>
                    <div class="stat-card rev">
                        <h3>Tracked Sales Revenue 💰</h3>
                        <p class="value">${total_revenue:,.2f}</p>
                    </div>
                </div>

                <!-- Export Action -->
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">
                    <h3 style="margin: 0; color: #1a237e;">Lead Activity Log</h3>
                    {export_btn_html}
                </div>

                <!-- Table -->
                <div class="table-responsive">
                    <table>
                        <thead>
                            <tr>
                                <th>ID</th>
                                {client_th_html}
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


@app.get("/dashboard/export")
def export_google_ads_csv(client_id: int):
    """Generates a custom Google Ads Offline Conversions CSV file filtered by client_id."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Verify client exists
        cursor.execute("SELECT name FROM clients WHERE id = ?", (client_id,))
        client_row = cursor.fetchone()
        if not client_row:
            raise HTTPException(status_code=400, detail="Invalid client ID")
        
        client_name = client_row[0]
        
        # Pull only rows belonging to this client that have a valid GCLID AND are either qualified or closed
        cursor.execute("""
            SELECT gclid, qualified, sale_closed, value, created_at
            FROM sessions
            WHERE client_id = ? AND gclid IS NOT NULL AND gclid != '' AND (qualified = 'YES' OR sale_closed = 'YES')
            ORDER BY created_at DESC
        """, (client_id,))
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database query error: {str(e)}")

    # Generate Google Ads Offline Conversion CSV template
    csv_content = "Google Click ID,Conversion Name,Conversion Time,Conversion Value,Conversion Currency\n"
    
    for gclid, qualified, sale_closed, value, created_at in rows:
        formatted_time = f"{created_at} +0000"
        
        if sale_closed == "YES":
            conv_name = "Offline Sale"
            conv_value = float(value or 0.0)
        else:
            conv_name = "Qualified Lead"
            conv_value = 1.0
            
        csv_content += f"{gclid},{conv_name},{formatted_time},{conv_value:.2f},USD\n"

    # Safe slug for file downloading
    safe_filename = re.sub(r'\s+', '-', client_name.strip().lower())
    
    # Return raw text formatted as CSV download
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=google_ads_offline_conversions_{safe_filename}.csv"
        }
    )


@app.post("/webhooks/callrail")
async def receive_callrail_webhook(request: Request, client_id: Optional[int] = None):
    """
    Multi-Tenant CallRail Webhook Receiver.
    """
    try:
        payload = await request.json()
        
        # 1. Resolve Multi-Tenant Client Mapping
        resolved_client_id = 1  # Default fallback
        
        if client_id:
            resolved_client_id = client_id
        else:
            company_id = payload.get('company_id') or payload.get('account_id')
            if company_id:
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM clients WHERE callrail_company_id = ?", (str(company_id),))
                match = cursor.fetchone()
                if match:
                    resolved_client_id = match[0]
                conn.close()
                
        # 2. Extract Webhook Variables
        gclid = payload.get('google_click_id') or payload.get('referrer', {}).get('gclid')
        caller_name = payload.get('customer_name', 'Unknown Caller')
        raw_phone = payload.get('customer_phone_number')
        transcript = payload.get('transcript') or payload.get('transcription') or ""
        
        normalized_phone = normalize_phone(raw_phone)
        if not normalized_phone:
            return {"status": "ignored", "message": "No valid phone number found in payload."}
            
        # 3. AI Transcript Audits using Claude 4.5 Haiku
        ai_qualified = "NO"
        ai_sale_closed = "NO"
        ai_value = 0.0
        ai_reason = "No transcript provided."
        model_name = "None"
        
        if transcript.strip():
            print(f"🧠 [Client #{resolved_client_id}] Transcript detected for {caller_name}. Auditing with Claude...")
            ai_result = analyze_transcript_with_claude(transcript)
            ai_qualified = ai_result.get("qualified", "NO")
            ai_sale_closed = ai_result.get("sale_closed", "NO")
            ai_value = float(ai_result.get("value", 0.0))
            ai_reason = ai_result.get("reason", "No reason parsed.")
            model_name = "claude-haiku-4-5-20251001"
            print(f"🎯 Audit Complete: Qualified={ai_qualified}, Sales Value=${ai_value}")
        else:
            print(f"⚠️ [Client #{resolved_client_id}] No transcript provided. Skipping AI analysis.")

        # 4. Save Session to Isolated Client ID in SQLite
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO sessions (
                client_id, phone, name, gclid, source, qualified, sale_closed, value, reason, model_used, raw_data
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            resolved_client_id,
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
            "client_id": resolved_client_id,
            "message": "Webhook successfully processed and saved.",
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


@app.post("/webhooks/form")
async def receive_form_lead(lead: FormLead, client_id: Optional[int] = None):
    """
    Form Lead Webhook Receiver supporting Multi-Tenancy.
    """
    try:
        resolved_client_id = client_id or 1  # Fallback to Client 1 if not defined
        full_name = f"{lead.first_name} {lead.last_name}".strip()
        normalized_phone = normalize_phone(lead.phone)
        email_clean = lead.email.strip().lower()
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO sessions (client_id, phone, email, name, company, gclid, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (resolved_client_id, normalized_phone, email_clean, full_name, lead.company, lead.gclid, "form"))
        conn.commit()
        conn.close()
        
        print(f"📝 [Client #{resolved_client_id}] Form Lead saved: Name={full_name}, Phone={normalized_phone}, GCLID={lead.gclid}")
        return {"status": "success", "message": f"Form lead saved under client #{resolved_client_id}."}
        
    except Exception as e:
        print(f"❌ Form Lead Error: {e}")
        raise HTTPException(status_code=400, detail=str(e))
