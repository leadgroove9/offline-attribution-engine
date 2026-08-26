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
    \"\"\"Initializes the database, creates necessary tables, and seeds initial clients.\"\"\"
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. Create Clients Table
    cursor.execute(\"\"\"
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            callrail_company_id TEXT UNIQUE,
            google_ads_customer_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    \"\"\")
    
    # 2. Create Sessions Table (Multi-Tenant)
    cursor.execute(\"\"\"
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
    \"\"\")
    
    # 3. Seed Mock Clients if empty
    cursor.execute(\"SELECT COUNT(*) FROM clients\")
    if cursor.fetchone()[0] == 0:
        mock_clients = [
            (\"Priority Plumbing\", \"comp_plumbing\", \"123-456-7890\"),
            (\"Apex HVAC & Air\", \"comp_hvac\", \"987-654-3210\"),
            (\"Metro Dental Care\", \"comp_dental\", \"555-123-4567\")
        ]
        cursor.executemany(\"\"\"
            INSERT INTO clients (name, callrail_company_id, google_ads_customer_id)
            VALUES (?, ?, ?)
        \"\"\", mock_clients)
        print(\"🌱 Seeded 3 mock agency clients successfully!\")
    
    conn.commit()
    conn.close()

# Run database initialization
init_db()


# ---------------------------------------------------------
# ANTHROPIC CLAUDE CONFIGURATION
# ---------------------------------------------------------
# Securely loaded from Render/environment variables
API_KEY = os.environ.get(\"ANTHROPIC_API_KEY\")
client = Anthropic(api_key=API_KEY) if API_KEY else None

def clean_json_string(text: str) -> str:
    \"\"\"
    Strips out markdown code block wrappers like ```json ... ``` if Claude
    accidentally includes them in the raw response.
    \"\"\"
    text = text.strip()
    text = re.sub(r\"^```(?:json)?\\s*\", \"\", text)
    text = re.sub(r\"\\s*```$\", \"\", text)
    return text.strip()

def analyze_transcript_with_claude(transcript: str) -> dict:
    \"\"\"
    Sends a phone transcript to Claude 4.5 Haiku to evaluate qualification and sales value.
    \"\"\"
    if not client:
        return {
            \"qualified\": \"NO\",
            \"sale_closed\": \"NO\",
            \"value\": 0.0,
            \"reason\": \"Anthropic API Key is not configured on the server.\"
        }
        
    if not transcript or not transcript.strip():
        return {
            \"qualified\": \"NO\",
            \"sale_closed\": \"NO\",
            \"value\": 0.0,
            \"reason\": \"No transcript available for analysis.\"
        }

    system_prompt = (
        \"You are an expert sales auditor and conversion tracking engine for local service businesses.\\n\"\n        \"Your job is to read a phone call transcript and determine three things:\\n\"\n        \"1. Is the caller a 'Qualified Lead'? (Did they express real intent to buy or schedule a service? Return 'YES' or 'NO')\\n\"\n        \"2. Was a sale 'Closed' during the call? (Did they agree to purchase, pay a deposit, or book a paid job? Return 'YES' or 'NO')\\n\"\n        \"3. What was the 'Value' of the transaction? (Extract the exact dollar amount if mentioned. If no sale closed or no value was stated, return 0)\\n\"\n        \"\\n\"\n        \"CRITICAL: You must return your response in RAW, valid JSON format. Do not write any introduction, \"\n        \"explanation, or markdown formatting (do not wrap in ```json). Your entire response must look exactly like this:\\n\"\n        \"{\\n\"\n        '  \"qualified\": \"YES\",\\n'\n        '  \"sale_closed\": \"YES\",\\n'\n        '  \"value\": 450.00,\\n'\n        '  \"reason\": \"A 1-2 sentence explanation of why you made this decision.\"\\n'\n        \"}\"\n    )

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
    \"\"\"Strips non-digits and normalizes to E.164-ish format.\"\"\"
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
    \"\"\"Agency Portal Landing Page.\"\"\"
    return \"\"\"
    <html>
        <head>
            <title>Multi-Tenant Attribution Engine Live 🤖</title>
            <style>
                body { font-family: Arial, sans-serif; text-align: center; padding-top: 80px; background-color: #f4f6f9; }
                .container { display: inline-block; background: white; padding: 40px; border-radius: 10px; box-shadow: 0px 4px 10px rgba(0,0,0,0.1); max-width: 600px; }
                h1 { color: #1a237e; margin-bottom: 10px; }
                p { color: #555; font-size: 18px; }
                .badge { background-color: #e8f5e9; color: #2e7d32; padding: 5px 15px; border-radius: 15px; font-weight: bold; }
                .btn { display: inline-block; background-color: #1a237e; color: white; padding: 12px 24px; text-decoration: none; font-size: 16px; font-weight: bold; border-radius: 5px; margin-top: 20px; transition: background 0.2s; }
                .btn:hover { background-color: #0d1b2a; }
                .feature-list { text-align: left; margin-top: 25px; color: #333; line-height: 1.6; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Multi-Tenant Attribution Engine Live! 🚀</h1>
                <p>Status: <span class="badge">Healthy, Multi-Tenant & AI-Enabled</span></p>
                <p>Welcome, Corey. Your agency platform is live with secure SQLite schema routing and Claude 4.5 Haiku.</p>
                
                <a href="/dashboard" class="btn">📊 Open Agency & Client Dashboard</a>
                
                <div class="feature-list">
                    <h3>Multi-Tenant Architecture Capabilities:</h3>
                    <ul>
                        <li>🏢 <strong>Client Isolation</strong>: Keep each client's conversions separate in SQLite.</li>
                        <li>📥 <strong>Dynamic Webhook Endpoint</strong>: `/webhooks/callrail?client_id=X` automatically links tracking logs to the correct account.</li>
                        <li>🧠 <strong>Claude 4.5 Haiku Audits</strong>: Real-time transcript parsing for qualification and cash value.</li>
                        <li>📂 <strong>Client-Specific Google Ads Export</strong>: Export custom CSV files tailored to each client's separate ad account.</li>
                    </ul>
                </div>
            </div>
        </body>
    </html>
    \"\"\"


@app.get("/dashboard", response_class=HTMLResponse)
def view_dashboard(client_id: Optional[int] = None):
    \"\"\"Interactive dashboard with client filtering.\"\"\"
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # 1. Fetch All Available Clients for the Dropdown Selector
        cursor.execute("SELECT id, name, google_ads_customer_id FROM clients ORDER BY name ASC")
        clients = cursor.fetchall()
        
        # Determine filtering
        selected_client_id = client_id if client_id is not None else 0 # 0 signifies "All Clients" (Agency Overview)
        
        # 2. Query Dashboard Rows and Calculations
        if selected_client_id == 0:
            # Query ALL clients combined
            cursor.execute(\"\"\"
                SELECT s.id, s.phone, s.email, s.name, s.company, s.gclid, s.source, s.qualified, s.sale_closed, s.value, s.reason, s.created_at, c.name
                FROM sessions s
                LEFT JOIN clients c ON s.client_id = c.id
                ORDER BY s.created_at DESC
            \"\"\")
            rows = cursor.fetchall()
            client_name_header = "All Clients (Agency Overview)"
            client_ads_id = "Multiple Accounts"
        else:
            # Query specific client
            cursor.execute(\"\"\"
                SELECT s.id, s.phone, s.email, s.name, s.company, s.gclid, s.source, s.qualified, s.sale_closed, s.value, s.reason, s.created_at, c.name
                FROM sessions s
                JOIN clients c ON s.client_id = c.id
                WHERE s.client_id = ?
                ORDER BY s.created_at DESC
            \"\"\", (selected_client_id,))
            rows = cursor.fetchall()
            
            # Find selected client name for header
            client_info = next((c for c in clients if c[0] == selected_client_id), None)
            client_name_header = client_info[1] if client_info else f"Client #{selected_client_id}"
            client_ads_id = client_info[2] if client_info else "N/A"
            
        conn.close()
    except Exception as e:
        return f"<html><body><h3>❌ Database Error: {e}</h3></body></html>"

    # Count analytics
    total_leads = len(rows)
    qualified_leads = sum(1 for r in rows if r[7] == 'YES')
    sales_closed = sum(1 for r in rows if r[8] == 'YES')
    total_revenue = sum(float(r[9] or 0.0) for r in rows)

    # Convert client list to dropdown option HTML
    dropdown_options = f'<option value=\"0\" {\"selected\" if selected_client_id == 0 else \"\"}>🏢 All Clients (Agency View)</option>'
    for c_id, c_name, c_ads in clients:
        is_selected = "selected" if selected_client_id == c_id else ""
        dropdown_options += f'<option value=\"{c_id}\" {is_selected}>👤 {c_name} (Ads: {c_ads})</option>'

    # Convert rows to table items
    table_rows_html = ""
    for r in rows:
        id_val, phone, email, name, company, gclid, source, qualified, sale_closed, value, reason, created_at, client_name_linked = r
        
        qual_badge = '<span style="background: #e8f5e9; color: #2e7d32; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 12px;">YES</span>' if qualified == 'YES' else '<span style="background: #ffebee; color: #c62828; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 12px;">NO</span>'
        closed_badge = '<span style="background: #e8f5e9; color: #2e7d32; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 12px;">YES</span>' if sale_closed == 'YES' else '<span style="background: #ffebee; color: #c62828; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 12px;">NO</span>'
        gclid_display = f'<code style="background: #f1f3f4; padding: 2px 6px; border-radius: 4px; font-size: 12px; word-break: break-all;">{gclid}</code>' if gclid else '<span style="color: #999; font-style: italic;">None</span>'
        value_display = f"<strong>${value:,.2f}</strong>" if value and value > 0 else '<span style="color: #999;">$0.00</span>'
        
        # Display the client column only in the multi-client view
        client_column_html = f'<td><span class="client-badge">{client_name_linked}</span></td>' if selected_client_id == 0 else ''
        
        table_rows_html += f\"\"\"\n        <tr>\n            <td>{id_val}</td>\n            {client_column_html}\n            <td><small>{created_at}</small></td>\n            <td><span class=\"badge-source\">{source.upper()}</span></td>\n            <td><strong>{name or 'Unknown'}</strong><br><small style=\"color:#666;\">{phone}</small></td>\n            <td>{gclid_display}</td>\n            <td>{qual_badge}</td>\n            <td>{closed_badge}</td>\n            <td>{value_display}</td>\n            <td><small>{reason or 'N/A'}</small></td>\n        </tr>\n        \"\"\"

    if not table_rows_html:
        table_rows_html = f'<tr><td colspan=\"{"10" if selected_client_id == 0 else "9"}\" style=\"text-align: center; color: #888; padding: 40px;\">No lead sessions recorded for this client. Set up their CallRail webhook to populate this space!</td></tr>'

    # Build the action button dynamically
    if selected_client_id == 0:
        export_btn_html = '<button class="btn-export disabled" onclick="alert(\'Please select a specific client from the dropdown above to export their Google Ads offline conversion CSV!\')" style="opacity:0.6; cursor:not-allowed;">📥 Select a Client to Export</button>'
    else:
        export_btn_html = f'<a href=\"/dashboard/export?client_id={selected_client_id}\" class=\"btn-export\">📥 Export Google Ads CSV</a>'

    # Conditionally show the Client header column
    client_th_html = '<th>Client Account</th>' if selected_client_id == 0 else ''

    return f\"\"\"\n    <!DOCTYPE html>\n    <html>\n        <head>\n            <title>Offline Lead & Conversion Dashboard 📊</title>\n            <meta charset=\"utf-8\">\n            <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n            <style>\n                body {{ font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", Roboto, Arial, sans-serif; background-color: #f4f6f9; color: #333; margin: 0; padding: 20px; }}\n                .container {{ max-width: 1300px; margin: 0 auto; background: white; padding: 30px; border-radius: 12px; box-shadow: 0px 4px 15px rgba(0,0,0,0.05); }}\n                header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #eaeaea; padding-bottom: 20px; margin-bottom: 30px; flex-wrap: wrap; gap: 15px; }}\n                h1 {{ margin: 0; color: #1a237e; font-size: 26px; }}\n                .client-selector-container {{ display: flex; align-items: center; gap: 10px; background: #e8eaf6; padding: 10px 15px; border-radius: 8px; border: 1px solid #c5cae9; }}\n                .client-label {{ font-weight: bold; color: #1a237e; font-size: 14px; }}\n                .client-select {{ padding: 8px 12px; font-size: 14px; border-radius: 5px; border: 1px solid #9fa8da; outline: none; font-weight: 600; cursor: pointer; color: #1a237e; }}\n                .client-select:focus {{ border-color: #1a237e; }}\n                \n                /* Analytics Stats */\n                .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 20px; margin-bottom: 30px; }}\n                .stat-card {{ background: #fafafa; border: 1px solid #e0e0e0; padding: 20px; border-radius: 8px; text-align: center; }}\n                .stat-card h3 {{ margin: 0 0 10px 0; font-size: 13px; color: #666; text-transform: uppercase; letter-spacing: 0.5px; }}\n                .stat-card .value {{ font-size: 28px; font-weight: bold; color: #1a237e; margin: 0; }}\n                .stat-card.rev .value {{ color: #2e7d32; }}\n                \n                /* Table Styles */\n                .table-responsive {{ overflow-x: auto; }}\n                table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 14px; }}\n                th, td {{ padding: 12px 15px; text-align: left; border-bottom: 1px solid #eaeaea; }}\n                th {{ background-color: #f8f9fa; color: #495057; font-weight: 600; text-transform: uppercase; font-size: 11px; letter-spacing: 0.5px; }}\n                tr:hover {{ background-color: #fdfdfd; }}\n                .badge-source {{ background: #e0f2f1; color: #00695c; font-size: 11px; padding: 2px 6px; border-radius: 4px; font-weight: bold; }}\n                .client-badge {{ background: #eceff1; color: #37474f; font-size: 11px; padding: 3px 8px; border-radius: 4px; font-weight: bold; border: 1px solid #cfd8dc; }}\n                \n                .btn-export {{ display: inline-block; background-color: #2e7d32; color: white; padding: 10px 18px; text-decoration: none; border-radius: 5px; font-weight: bold; font-size: 14px; border: none; transition: background 0.2s; cursor: pointer; }}\n                .btn-export:hover {{ background-color: #1b5e20; }}\n            </style>\n        </head>\n        <body>\n            <div class=\"container\">\n                <header>\n                    <div>\n                        <h1>{client_name_header} 📊</h1>\n                        <p style=\"margin: 5px 0 0 0; color: #666; font-size: 14px;\">Google Ads Account: <strong>{client_ads_id}</strong> | Multi-Tenant Agency Engine</p>\n                    </div>\n                    \n                    <div class=\"client-selector-container\">\n                        <span class=\"client-label\">Viewing Account:</span>\n                        <select class=\"client-select\" onchange=\"window.location.href='/dashboard?client_id='+this.value\">\n                            {dropdown_options}\n                        </select>\n                    </div>\n                </header>\n\n                <!-- Stats Cards -->\n                <div class=\"stats-grid\">\n                    <div class=\"stat-card\">\n                        <h3>Tracked Sessions</h3>\n                        <p class=\"value\">{total_leads}</p>\n                    </div>\n                    <div class=\"stat-card\">\n                        <h3>AI Qualified Leads 🟢</h3>\n                        <p class=\"value\">{qualified_leads}</p>\n                    </div>\n                    <div class=\"stat-card\">\n                        <h3>Sales Closed 🤝</h3>\n                        <p class=\"value\">{sales_closed}</p>\n                    </div>\n                    <div class=\"stat-card rev\">\n                        <h3>Tracked Sales Revenue 💰</h3>\n                        <p class=\"value\">${total_revenue:,.2f}</p>\n                    </div>\n                </div>\n\n                <!-- Export Action -->\n                <div style=\"display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;\">\n                    <h3 style=\"margin: 0; color: #1a237e;\">Lead Activity Log</h3>\n                    {export_btn_html}\n                </div>\n\n                <!-- Table -->\n                <div class=\"table-responsive\">\n                    <table>\n                        <thead>\n                            <tr>\n                                <th>ID</th>\n                                {client_th_html}\n                                <th>Timestamp</th>\n                                <th>Source</th>\n                                <th>Lead Contact</th>\n                                <th>Google Click ID (GCLID)</th>\n                                <th>Qualified</th>\n                                <th>Closed</th>\n                                <th>Value</th>\n                                <th>Claude Decision Reasoning</th>\n                            </tr>\n                        </thead>\n                        <tbody>\n                            {table_rows_html}\n                        </tbody>\n                    </table>\n                </div>\n            </div>\n        </body>\n    </html>\n    \"\"\"\n\n\n@app.get(\"/dashboard/export\")\ndef export_google_ads_csv(client_id: int):\n    \"\"\"Generates a custom Google Ads Offline Conversions CSV file filtered by client_id.\"\"\"\n    try:\n        conn = sqlite3.connect(DB_PATH)\n        cursor = conn.cursor()\n        \n        # Verify client exists\n        cursor.execute(\"SELECT name FROM clients WHERE id = ?\", (client_id,))\n        client_row = cursor.fetchone()\n        if not client_row:\n            raise HTTPException(status_code=400, detail=\"Invalid client ID\")\n        \n        client_name = client_row[0]\n        \n        # Pull only rows belonging to this client that have a valid GCLID AND are either qualified or closed\n        cursor.execute(\"\"\"\n            SELECT gclid, qualified, sale_closed, value, created_at\n            FROM sessions\n            WHERE client_id = ? AND gclid IS NOT NULL AND gclid != '' AND (qualified = 'YES' OR sale_closed = 'YES')\n            ORDER BY created_at DESC\n        \"\"\", (client_id,))\n        rows = cursor.fetchall()\n        conn.close()\n    except Exception as e:\n        raise HTTPException(status_code=500, detail=f\"Database query error: {str(e)}\")\n\n    # Generate Google Ads Offline Conversion CSV template\n    # Required Headers: Google Click ID, Conversion Name, Conversion Time, Conversion Value, Conversion Currency\n    csv_content = \"Google Click ID,Conversion Name,Conversion Time,Conversion Value,Conversion Currency\\n\"\n    \n    for gclid, qualified, sale_closed, value, created_at in rows:\n        # Format timestamp to Google Ads requirement: YYYY-MM-DD HH:MM:SS +0000\n        # SQLite defaults to YYYY-MM-DD HH:MM:SS format, we just need to append the timezone offset\n        formatted_time = f\"{created_at} +0000\"\n        \n        # Map Conversion Type:\n        # If sale closed -> Offline Sale with Claude-extracted cash value\n        # If only qualified -> Qualified Lead with a baseline standard value of $1.00 for bidding optimization\n        if sale_closed == \"YES\":\n            conv_name = \"Offline Sale\"\n            conv_value = float(value or 0.0)\n        else:\n            conv_name = \"Qualified Lead\"\n            conv_value = 1.0\n            \n        csv_content += f\"{gclid},{conv_name},{formatted_time},{conv_value:.2f},USD\\n\"\n\n    # Safe slug for file downloading\n    safe_filename = re.sub(r'\\s+', '-', client_name.strip().lower())\n    \n    # Return raw text formatted as CSV download\n    return Response(\n        content=csv_content,\n        media_type=\"text/csv\",\n        headers={\n            \"Content-Disposition\": f\"attachment; filename=google_ads_offline_conversions_{safe_filename}.csv\"\n        }\n    )\n\n\n@app.post(\"/webhooks/callrail\")\nasync def receive_callrail_webhook(request: Request, client_id: Optional[int] = None):\n    \"\"\"\n    Multi-Tenant CallRail Webhook Receiver.\n    If no client_id query param is sent, parses the company/account info inside CallRail's payload to auto-map it!\n    \"\"\"\n    try:\n        payload = await request.json()\n        \n        # 1. Resolve Multi-Tenant Client Mapping\n        resolved_client_id = 1  # Default fallback\n        \n        if client_id:\n            resolved_client_id = client_id\n        else:\n            # Try auto-mapping using CallRail payload parameters\n            # CallRail webhooks usually pass 'company_id', 'company_name', or 'account_id'\n            company_id = payload.get('company_id') or payload.get('account_id')\n            if company_id:\n                conn = sqlite3.connect(DB_PATH)\n                cursor = conn.cursor()\n                cursor.execute(\"SELECT id FROM clients WHERE callrail_company_id = ?\", (str(company_id),))\n                match = cursor.fetchone()\n                if match:\n                    resolved_client_id = match[0]\n                conn.close()\n                \n        # 2. Extract Webhook Variables\n        gclid = payload.get('google_click_id') or payload.get('referrer', {}).get('gclid')\n        caller_name = payload.get('customer_name', 'Unknown Caller')\n        raw_phone = payload.get('customer_phone_number')\n        transcript = payload.get('transcript') or payload.get('transcription') or \"\"\n        \n        normalized_phone = normalize_phone(raw_phone)\n        if not normalized_phone:\n            return {\"status\": \"ignored\", \"message\": \"No valid phone number found in payload.\"}\n            \n        # 3. AI Transcript Audits using Claude 4.5 Haiku\n        ai_qualified = \"NO\"\n        ai_sale_closed = \"NO\"\n        ai_value = 0.0\n        ai_reason = \"No transcript provided.\"\n        model_name = \"None\"\n        \n        if transcript.strip():\n            print(f\"🧠 [Client #{resolved_client_id}] Transcript detected for {caller_name}. Auditing with Claude...\")\n            ai_result = analyze_transcript_with_claude(transcript)\n            ai_qualified = ai_result.get(\"qualified\", \"NO\")\n            ai_sale_closed = ai_result.get(\"sale_closed\", \"NO\")\n            ai_value = float(ai_result.get(\"value\", 0.0))\n            ai_reason = ai_result.get(\"reason\", \"No reason parsed.\")\n            model_name = \"claude-haiku-4-5-20251001\"\n            print(f\"🎯 Audit Complete: Qualified={ai_qualified}, Sales Value=${ai_value}\")\n        else:\n            print(f\"⚠️ [Client #{resolved_client_id}] No transcript provided. Skipping AI analysis.\")\n\n        # 4. Save Session to Isolated Client ID in SQLite\n        conn = sqlite3.connect(DB_PATH)\n        cursor = conn.cursor()\n        cursor.execute(\"\"\"\n            INSERT INTO sessions (\n                client_id, phone, name, gclid, source, qualified, sale_closed, value, reason, model_used, raw_data\n            )\n            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)\n        \"\"\", (\n            resolved_client_id,\n            normalized_phone, \n            caller_name, \n            gclid, \n            \"callrail\", \n            ai_qualified, \n            ai_sale_closed, \n            ai_value, \n            ai_reason, \n            model_name, \n            str(payload)\n        ))\n        conn.commit()\n        conn.close()\n        \n        return {\n            \"status\": \"success\",\n            \"client_id\": resolved_client_id,\n            \"message\": \"Webhook successfully processed and saved.\",\n            \"ai_audit\": {\n                \"qualified\": ai_qualified,\n                \"sale_closed\": ai_sale_closed,\n                \"value\": ai_value,\n                \"reason\": ai_reason\n            }\n        }\n            \n    except Exception as e:\n        print(f\"❌ Webhook Error: {e}\")\n        raise HTTPException(status_code=400, detail=str(e))\n\n\n@app.post(\"/webhooks/form\")\nasync def receive_form_lead(lead: FormLead, client_id: Optional[int] = None):\n    \"\"\"\n    Form Lead Webhook Receiver supporting Multi-Tenancy.\n    \"\"\"\n    try:\n        resolved_client_id = client_id or 1  # Fallback to Client 1 if not defined\n        full_name = f\"{lead.first_name} {lead.last_name}\".strip()\n        normalized_phone = normalize_phone(lead.phone)\n        email_clean = lead.email.strip().lower()\n        \n        conn = sqlite3.connect(DB_PATH)\n        cursor = conn.cursor()\n        cursor.execute(\"\"\"\n            INSERT INTO sessions (client_id, phone, email, name, company, gclid, source)\n            VALUES (?, ?, ?, ?, ?, ?, ?)\n        \"\"\", (resolved_client_id, normalized_phone, email_clean, full_name, lead.company, lead.gclid, \"form\"))\n        conn.commit()\n        conn.close()\n        \n        print(f\"📝 [Client #{resolved_client_id}] Form Lead saved: Name={full_name}, Phone={normalized_phone}, GCLID={lead.gclid}\")\n        return {\"status\": \"success\", \"message\": f\"Form lead saved under client #{resolved_client_id}.\"}\n        \n    except Exception as e:\n        print(f\"❌ Form Lead Error: {e}\")\n        raise HTTPException(status_code=400, detail=str(e))\n