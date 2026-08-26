import os
import sqlite3
import re
import json
import csv
import io
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, Response
from pydantic import BaseModel
from typing import Optional
from anthropic import Anthropic

# Initialize FastAPI App
app = FastAPI(
    title="Offline Attribution Engine (Multi-Tenant)",
    description="Multi-tenant agency platform for tracking offline leads/sales and AI audits",
    version="6.0.0"
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


class ClientCreate(BaseModel):
    name: str
    callrail_company_id: str
    google_ads_customer_id: str


# ---------------------------------------------------------
# ENDPOINTS (Routes for Render)
# ---------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def read_root():
    """Agency Portal Landing Page."""
    return """
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
                        <li>👤 <strong>Client Self-Onboarding</strong>: Instantly register new client accounts through the dynamic web form.</li>
                    </ul>
                </div>
            </div>
        </body>
    </html>
    """


@app.get("/dashboard", response_class=HTMLResponse)
def view_dashboard(client_id: Optional[int] = None):
    """Interactive dashboard with client filtering."""
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
            # Multi-Client (All Clients) View - Join with clients table to display client names
            cursor.execute("""
                SELECT s.id, s.phone, s.email, s.name, s.company, s.gclid, s.source, s.qualified, s.sale_closed, s.value, s.reason, s.created_at, c.name
                FROM sessions s
                LEFT JOIN clients c ON s.client_id = c.id
                ORDER BY s.created_at DESC
            """)
            rows = cursor.fetchall()
            client_name_header = "All Agency Accounts"
            client_ads_id = "Multiple Accounts"
        else:
            # Single-Client Filtered View
            cursor.execute("""
                SELECT s.id, s.phone, s.email, s.name, s.company, s.gclid, s.source, s.qualified, s.sale_closed, s.value, s.reason, s.created_at, c.name
                FROM sessions s
                LEFT JOIN clients c ON s.client_id = c.id
                WHERE s.client_id = ?
                ORDER BY s.created_at DESC
            """, (selected_client_id,))
            rows = cursor.fetchall()
            
            # Fetch current client profile details
            cursor.execute("SELECT name, google_ads_customer_id FROM clients WHERE id = ?", (selected_client_id,))
            client_profile = cursor.fetchone()
            client_name_header = client_profile[0] if client_profile else "Unknown Client"
            client_ads_id = client_profile[1] if client_profile else "N/A"
            
        conn.close()
    except Exception as e:
        return f"<html><body><h3>❌ Database Error: {e}</h3></body></html>"

    # Count analytics
    total_leads = len(rows)
    qualified_leads = sum(1 for r in rows if r[7] == 'YES')
    sales_closed = sum(1 for r in rows if r[8] == 'YES')
    total_revenue = sum(float(r[9] or 0.0) for r in rows)
    
    # Count how many of these leads have a GCLID for export
    exportable_conversions = sum(1 for r in rows if r[5] and (r[7] == 'YES' or r[8] == 'YES'))

    # Generate the Selector Dropdown Options
    dropdown_options = f'<option value="0" {"selected" if selected_client_id == 0 else ""}>📂 [Show All Clients / Agency View]</option>'
    for c_id, c_name, c_ads in clients:
        is_selected = "selected" if selected_client_id == c_id else ""
        dropdown_options += f'<option value="{c_id}" {is_selected}>👤 {c_name} (Ads: {c_ads})</option>'

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
        
        table_rows_html += f"""
        <tr>
            <td>{id_val}</td>
            {client_column_html}
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
        table_rows_html = f'<tr><td colspan="{"10" if selected_client_id == 0 else "9"}" style="text-align: center; color: #888; padding: 40px;">No lead sessions recorded for this client. Set up their CallRail webhook to populate this space!</td></tr>'

    # Build the action button dynamically
    if selected_client_id == 0:
        export_btn_html = '<button class="btn-export disabled" onclick="alert(\'Please select a specific client from the dropdown above to export their Google Ads offline conversion CSV!\')" style="opacity:0.6; cursor:not-allowed;">📥 Select a Client to Export</button>'
    else:
        export_btn_html = f'<a href="/dashboard/export?client_id={selected_client_id}" class="btn-export">📥 Export Google Ads CSV ({exportable_conversions})</a>'

    # Conditionally show the Client header column
    client_th_html = '<th>Client Account</th>' if selected_client_id == 0 else ''

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
                .btn-add-client {{ display: inline-block; background-color: #1a237e; color: white; padding: 10px 18px; text-decoration: none; border-radius: 5px; font-weight: bold; font-size: 14px; border: none; transition: background 0.2s; cursor: pointer; }}
                .btn-add-client:hover {{ background-color: #0d1b2a; }}
            </style>
        </head>
        <body>
            <div class="container">
                <header>
                    <div>
                        <h1>{client_name_header} 📊</h1>
                        <p style="margin: 5px 0 0 0; color: #666; font-size: 14px;">Google Ads Account: <strong>{client_ads_id}</strong> | Multi-Tenant Agency Engine</p>
                    </div>
                    
                    <div style="display: flex; gap: 10px; align-items: center; flex-wrap: wrap;">
                        <div class="client-selector-container">
                            <span class="client-label">Viewing Account:</span>
                            <select class="client-select" onchange="window.location.href='/dashboard?client_id='+this.value">
                                {dropdown_options}
                            </select>
                        </div>
                        <a href="/dashboard/add-client" class="btn-add-client">➕ Add Client</a>
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


@app.get("/dashboard/add-client", response_class=HTMLResponse)
def add_client_page():
    """Page to add a new client."""
    return """
    <!DOCTYPE html>
    <html>
        <head>
            <title>Add New Client 👤</title>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif; background-color: #f4f6f9; color: #333; margin: 0; padding: 20px; }
                .container { max-width: 500px; margin: 50px auto; background: white; padding: 30px; border-radius: 12px; box-shadow: 0px 4px 15px rgba(0,0,0,0.05); }
                h1 { margin-top: 0; color: #1a237e; font-size: 24px; text-align: center; }
                .form-group { margin-bottom: 20px; }
                label { display: block; font-weight: 600; margin-bottom: 8px; font-size: 14px; color: #495057; }
                input[type="text"] { width: 100%; padding: 10px 12px; border-radius: 6px; border: 1px solid #ced4da; box-sizing: border-box; font-size: 14px; outline: none; transition: border-color 0.2s; }
                input[type="text"]:focus { border-color: #1a237e; }
                .btn-submit { width: 100%; background-color: #1a237e; color: white; padding: 12px; border: none; border-radius: 6px; font-weight: bold; font-size: 16px; cursor: pointer; transition: background 0.2s; margin-top: 10px; }
                .btn-submit:hover { background-color: #0d1b2a; }
                .btn-cancel { display: block; text-align: center; color: #666; text-decoration: none; font-size: 14px; margin-top: 15px; }
                .btn-cancel:hover { color: #333; }
                .alert { padding: 12px; border-radius: 6px; margin-bottom: 20px; display: none; font-size: 14px; font-weight: 600; }
                .alert-error { background-color: #ffebee; color: #c62828; border: 1px solid #ffcdd2; }
                .alert-success { background-color: #e8f5e9; color: #2e7d32; border: 1px solid #c8e6c9; }
                .instructions { background-color: #e8eaf6; border-left: 4px solid #1a237e; padding: 12px; border-radius: 4px; font-size: 13px; color: #3f51b5; line-height: 1.5; margin-bottom: 20px; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1 id="heading-title">Add New Client Account 👤</h1>
                <div class="instructions" id="instructions-container">
                    💡 <strong>Agency Onboarding Step:</strong> Adding a client here creates their database profile. 
                    They will get their own isolated space in your database and dynamic webhook triggers.
                </div>
                <div id="alert-box" class="alert alert-error"></div>
                
                <form id="add-client-form" onsubmit="submitForm(event)">
                    <div class="form-group">
                        <label for="name">Client Business Name</label>
                        <input type="text" id="name" required placeholder="e.g. Priority Plumbing">
                    </div>
                    
                    <div class="form-group">
                        <label for="callrail_company_id">CallRail Company ID (or Account ID)</label>
                        <input type="text" id="callrail_company_id" required placeholder="e.g. comp_plumbing">
                        <small style="color: #666; font-size: 11px; margin-top: 4px; display: block;">Used to auto-match webhooks if no client ID query parameter is passed.</small>
                    </div>
                    
                    <div class="form-group">
                        <label for="google_ads_customer_id">Google Ads Customer ID</label>
                        <input type="text" id="google_ads_customer_id" required placeholder="e.g. 123-456-7890">
                        <small style="color: #666; font-size: 11px; margin-top: 4px; display: block;">Appears in exports for their specific offline conversion uploads.</small>
                    </div>
                    
                    <button type="submit" class="btn-submit">🚀 Add Client & Generate Webhook</button>
                </form>
                
                <!-- Dynamic Setup Webhook Screen (Hidden initially) -->
                <div id="success-screen" style="display: none; text-align: center;">
                    <div style="font-size: 50px; margin-bottom: 15px;">🎉</div>
                    <h2 style="color: #2e7d32; margin-top: 0;">Onboarding Successful!</h2>
                    <p style="color: #555; font-size: 14px; margin-bottom: 20px;">
                        Your client profile for <strong id="registered-client-name"></strong> has been created.
                    </p>
                    
                    <div class="instructions" style="text-align: left; background-color: #e8eaf6; border-left: 4px solid #1a237e;">
                        🔑 <strong>Step 2: Connect CallRail</strong><br>
                        Copy this personalized URL and paste it as your Webhook URL inside CallRail:
                    </div>
                    
                    <div style="display: flex; gap: 8px; margin-bottom: 15px;">
                        <input type="text" id="webhook-url-input" readonly style="flex: 1; padding: 10px; border-radius: 6px; border: 1px solid #ced4da; font-family: monospace; font-size: 12px; background-color: #f8f9fa;">
                        <button onclick="copyWebhookUrl()" id="copy-btn" class="btn-submit" style="margin: 0; width: auto; white-space: nowrap; padding: 0 15px; font-size: 14px; background-color: #2e7d32;">📋 Copy URL</button>
                    </div>
                    
                    <div class="instructions" style="text-align: left; background-color: #fff3cd; border-left-color: #ffc107; color: #856404; font-size: 12px;">
                        ⚠️ <strong>Important:</strong> Set the Trigger Event in CallRail to <strong>"Call Completed"</strong> so the audio transcript is ready for Claude.
                    </div>
                    
                    <a href="/dashboard" class="btn-submit" style="display: block; text-decoration: none; text-align: center; line-height: 20px; background-color: #1a237e;">📊 Proceed to Dashboard</a>
                </div>
                
                <a href="/dashboard" class="btn-cancel" id="cancel-link">⬅️ Cancel and Return to Dashboard</a>
            </div>
            
            <script>
                async function submitForm(event) {
                    event.preventDefault();
                    const alertBox = document.getElementById('alert-box');
                    const btnSubmit = document.querySelector('.btn-submit');
                    const form = document.getElementById('add-client-form');
                    const instContainer = document.getElementById('instructions-container');
                    const cancelLink = document.getElementById('cancel-link');
                    const heading = document.getElementById('heading-title');
                    const successScreen = document.getElementById('success-screen');
                    
                    alertBox.style.display = 'none';
                    alertBox.className = 'alert';
                    
                    const name = document.getElementById('name').value.trim();
                    const callrail_company_id = document.getElementById('callrail_company_id').value.trim();
                    const google_ads_customer_id = document.getElementById('google_ads_customer_id').value.trim();
                    
                    btnSubmit.disabled = true;
                    btnSubmit.innerText = 'Adding client...';
                    
                    try {
                        const response = await fetch('/dashboard/add-client', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json'
                            },
                            body: JSON.stringify({
                                name,
                                callrail_company_id,
                                google_ads_customer_id
                            })
                        });
                        
                        const data = await response.json();
                        
                        if (response.ok) {
                            // Hide registration UI and reveal instructions
                            form.style.display = 'none';
                            instContainer.style.display = 'none';
                            cancelLink.style.display = 'none';
                            heading.style.display = 'none';
                            
                            document.getElementById('registered-client-name').innerText = name;
                            
                            // Generate the exact live webhook URL dynamically
                            const liveWebhook = `${window.location.origin}/webhooks/callrail?client_id=${data.client_id}`;
                            document.getElementById('webhook-url-input').value = liveWebhook;
                            
                            successScreen.style.display = 'block';
                        } else {
                            throw new Error(data.detail || 'An unexpected error occurred.');
                        }
                    } catch (error) {
                        alertBox.innerText = 'Error: ' + error.message;
                        alertBox.className = 'alert alert-error';
                        alertBox.style.display = 'block';
                        btnSubmit.disabled = false;
                        btnSubmit.innerText = '🚀 Add Client & Generate Webhook';
                    }
                }
                
                function copyWebhookUrl() {
                    const copyText = document.getElementById("webhook-url-input");
                    copyText.select();
                    copyText.setSelectionRange(0, 99999);
                    navigator.clipboard.writeText(copyText.value);
                    
                    const copyBtn = document.getElementById("copy-btn");
                    copyBtn.innerText = "✅ Copied!";
                    copyBtn.style.backgroundColor = "#1b5e20";
                    setTimeout(() => {
                        copyBtn.innerText = "📋 Copy URL";
                        copyBtn.style.backgroundColor = "#2e7d32";
                    }, 2000);
                }
            </script>
        </body>
    </html>
    """


@app.post("/dashboard/add-client")
def create_client(client: ClientCreate):
    """Endpoint to handle client form submission."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Verify unique CallRail ID
        cursor.execute("SELECT id, name FROM clients WHERE callrail_company_id = ?", (client.callrail_company_id,))
        existing = cursor.fetchone()
        if existing:
            raise HTTPException(status_code=400, detail=f"CallRail Company ID '{client.callrail_company_id}' is already registered to client '{existing[1]}'.")
            
        cursor.execute("""
            INSERT INTO clients (name, callrail_company_id, google_ads_customer_id)
            VALUES (?, ?, ?)
        """, (client.name, client.callrail_company_id, client.google_ads_customer_id))
        
        # Capture the newly created client ID
        client_id = cursor.lastrowid
        
        conn.commit()
        conn.close()
        return {
            "status": "success", 
            "client_id": client_id,
            "message": f"Client '{client.name}' created successfully!"
        }
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database insertion error: {str(e)}")


@app.get("/dashboard/export")
def export_google_conversions(client_id: int):
    """
    Exports qualified and closed conversions that have a valid GCLID 
    into a Google Ads-compliant CSV upload format, filtered by client_id.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Verify client exists
        cursor.execute("SELECT name FROM clients WHERE id = ?", (client_id,))
        client_row = cursor.fetchone()
        if not client_row:
            raise HTTPException(status_code=400, detail="Invalid client ID")
        
        client_name = client_row[0]
        
        # Pull records that have a GCLID and are either Qualified or Closed belonging to this client
        cursor.execute("""
            SELECT gclid, qualified, sale_closed, value, created_at
            FROM sessions
            WHERE client_id = ? AND gclid IS NOT NULL AND gclid != '' AND (qualified = 'YES' OR sale_closed = 'YES')
            ORDER BY created_at DESC
        """, (client_id,))
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

    # Generate CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    
    # 1. Google Ads template parameter header
    # Since SQLite CURRENT_TIMESTAMP is UTC (+0000), we format our export as UTC
    writer.writerow(["Parameters:TimeZone=+0000"])
    
    # 2. Google Ads standard headers
    writer.writerow(["Google Click ID", "Conversion Name", "Conversion Time", "Conversion Value", "Conversion Currency"])
    
    for r in rows:
        gclid, qualified, sale_closed, value, created_at = r
        
        # Format the time exactly how Google Ads expects it: 'YYYY-MM-DD HH:MM:SS' with a +0000 suffix
        conv_time = f"{created_at} +0000" if created_at else ""
        
        # Distinguish between Closed Sales and Qualified Leads
        if sale_closed == 'YES':
            conv_name = "Offline Sale"
            conv_value = float(value or 0.0)
        else:
            conv_name = "Qualified Lead"
            conv_value = 1.0  # Default lead qualification value
            
        writer.writerow([gclid, conv_name, conv_time, conv_value, "USD"])
            
    output.seek(0)
    
    # Safe slug for file downloading
    safe_filename = re.sub(r'\s+', '-', client_name.strip().lower())
    
    # Prepare HTTP headers to trigger file download
    headers = {
        'Content-Disposition': f'attachment; filename="google_ads_conversions_{safe_filename}.csv"',
        'Content-Type': 'text/csv'
    }
    return StreamingResponse(output, headers=headers)


@app.post("/webhooks/callrail")
async def receive_callrail_webhook(request: Request, client_id: Optional[int] = None):
    """
    Multi-Tenant CallRail Webhook Receiver.
    If no client_id query param is sent, parses the company/account info inside CallRail's payload to auto-map it!
    """
    try:
        # 1. Parse the incoming JSON data from CallRail
        payload = await request.json()
        
        # Resolve Multi-Tenant Client Mapping
        resolved_client_id = 1  # Default fallback
        
        if client_id:
            resolved_client_id = client_id
        else:
            # Try auto-mapping using CallRail payload parameters
            # CallRail webhooks usually pass 'company_id', 'company_name', or 'account_id'
            company_id = payload.get('company_id') or payload.get('account_id')
            if company_id:
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM clients WHERE callrail_company_id = ?", (str(company_id),))
                match = cursor.fetchone()
                if match:
                    resolved_client_id = match[0]
                conn.close()
        
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
            print(f"🧠 [Client #{resolved_client_id}] Transcript detected for {caller_name}. Analyzing with Claude...")
            ai_result = analyze_transcript_with_claude(transcript)
            ai_qualified = ai_result.get("qualified", "NO")
            ai_sale_closed = ai_result.get("sale_closed", "NO")
            ai_value = float(ai_result.get("value", 0.0))
            ai_reason = ai_result.get("reason", "No reason parsed.")
            model_name = "claude-haiku-4-5-20251001"
            print(f"🎯 AI Analysis complete: Qualified={ai_qualified}, Value=${ai_value}")
        else:
            print(f"⚠️ [Client #{resolved_client_id}] No transcript provided in CallRail webhook for {caller_name}. Skipping AI audit.")

        # 5. Save all parameters including the AI analysis results to SQLite
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
async def receive_form_lead(lead: FormLead, client_id: Optional[int] = None):
    """
    Form Lead Webhook Receiver supporting Multi-Tenancy.
    Saves website visitor form entries containing GCLIDs to allow back-end pairing.
    """
    try:
        resolved_client_id = client_id or 1  # Fallback to Client 1 if not defined
        
        # 1. Clean data
        full_name = f"{lead.first_name} {lead.last_name}".strip()
        normalized_phone = normalize_phone(lead.phone)
        email_clean = lead.email.strip().lower()
        
        # 2. Save to SQLite
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO sessions (client_id, phone, email, name, company, gclid, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (resolved_client_id, normalized_phone, email_clean, full_name, lead.company, lead.gclid, "form"))
        conn.commit()
        conn.close()
        
        print(f"📝 [Client #{resolved_client_id}] Form Lead saved: Name={full_name}, Phone={normalized_phone}, Email={email_clean}, GCLID={lead.gclid}")
        return {"status": "success", "message": f"Form lead saved under client #{resolved_client_id}."}
        
    except Exception as e:
        print(f"❌ Error saving Form Lead: {e}")
        raise HTTPException(status_code=400, detail=str(e))
