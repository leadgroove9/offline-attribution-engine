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
    title="Offline Attribution Engine (Multi-Tenant Multi-Channel)",
    description="Multi-tenant agency platform for tracking offline leads/sales and AI audits across Google, Meta, LinkedIn, and Microsoft",
    version="9.0.0"
)

# ---------------------------------------------------------
# DATABASE CONFIGURATION (SQLite)
# ---------------------------------------------------------
DB_PATH = "offline_attribution.db"

def init_db():
    """Initializes the database, creates necessary tables, and self-heals schemas."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. Create Clients Table with complete questionnaire fields
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            callrail_company_id TEXT UNIQUE,
            google_ads_customer_id TEXT,
            facebook_ads_id TEXT,
            linkedin_ads_id TEXT,
            microsoft_ads_id TEXT,
            lead_gen_method TEXT,
            qualification_criteria TEXT,
            source_of_truth TEXT,
            email_provider TEXT,
            email_account TEXT,
            crm_deal_tags TEXT,
            crm_lead_tags TEXT,
            lead_count_rule TEXT,
            exclude_past_customers TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Self-heal clients schema
    cursor.execute("PRAGMA table_info(clients)")
    existing_cols = [col[1] for col in cursor.fetchall()]
    
    cols_to_verify = [
        ("facebook_ads_id", "TEXT"),
        ("linkedin_ads_id", "TEXT"),
        ("microsoft_ads_id", "TEXT"),
        ("lead_gen_method", "TEXT"),
        ("qualification_criteria", "TEXT"),
        ("source_of_truth", "TEXT"),
        ("email_provider", "TEXT"),
        ("email_account", "TEXT"),
        ("crm_deal_tags", "TEXT"),
        ("crm_lead_tags", "TEXT"),
        ("lead_count_rule", "TEXT"),
        ("exclude_past_customers", "TEXT")
    ]
    for col_name, col_type in cols_to_verify:
        if col_name not in existing_cols:
            cursor.execute(f"ALTER TABLE clients ADD COLUMN {col_name} {col_type}")
            print(f"Added missing database column in clients: {col_name}")

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
            fbclid TEXT,
            li_fat_id TEXT,
            msclkid TEXT,
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
    
    # Self-heal sessions schema
    cursor.execute("PRAGMA table_info(sessions)")
    existing_session_cols = [col[1] for col in cursor.fetchall()]
    session_cols_to_verify = [
        ("fbclid", "TEXT"),
        ("li_fat_id", "TEXT"),
        ("msclkid", "TEXT")
    ]
    for col_name, col_type in session_cols_to_verify:
        if col_name not in existing_session_cols:
            cursor.execute(f"ALTER TABLE sessions ADD COLUMN {col_name} {col_type}")
            print(f"Added missing session column: {col_name}")
            
    # 3. Seed Mock Clients if empty
    cursor.execute("SELECT COUNT(*) FROM clients")
    if cursor.fetchone()[0] == 0:
        mock_clients = [
            ("Priority Plumbing", "comp_plumbing", "123-456-7890", "fb_plumb_99", "", "", "both", "C", "hubspot", "", "", "closed-won", "", "all", "NO"),
            ("Apex HVAC & Air", "comp_hvac", "987-654-3210", "", "", "ms_hvac_88", "both", "E", "servicetitan", "", "", "", "completed-lead", "all", "NO"),
            ("Metro Dental Care", "comp_dental", "555-123-4567", "", "li_dental_77", "", "both", "C", "email", "gmail", "bookings@metrodental.com", "", "", "maximum_one", "YES")
        ]
        cursor.executemany("""
            INSERT INTO clients (
                name, callrail_company_id, google_ads_customer_id, facebook_ads_id, linkedin_ads_id, microsoft_ads_id,
                lead_gen_method, qualification_criteria, source_of_truth, email_provider, email_account,
                crm_deal_tags, crm_lead_tags, lead_count_rule, exclude_past_customers
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, mock_clients)
        print("Seeded 3 mock agency clients successfully!")
    
    conn.commit()
    conn.close()

# Run database initialization
init_db()


# ---------------------------------------------------------
# ANTHROPIC CLAUDE CONFIGURATION
# ---------------------------------------------------------
API_KEY = os.environ.get("ANTHROPIC_API_KEY")
client = Anthropic(api_key=API_KEY) if API_KEY else None

def clean_json_string(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()

def analyze_transcript_with_claude(transcript: str, qualification_criteria_desc: str) -> dict:
    """
    Sends a transcript to Claude 4.5 Haiku to audit based on the client's custom qualification criteria.
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

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            system=system_prompt,
            messages=[
                {"role": "user", "content": f"Analyze this transcript:\n\n{transcript}"}
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
# HELPERS & CONVERTERS
# ---------------------------------------------------------
def normalize_phone(phone_str: str) -> str:
    if not phone_str:
        return ""
    cleaned = re.sub(r'\D', '', phone_str)
    if len(cleaned) == 10:
        cleaned = "1" + cleaned
    return cleaned

def extract_param_from_url(url: str, param_name: str) -> Optional[str]:
    if not url:
        return None
    match = re.search(rf"[?&]{param_name}=([^&#]+)", url)
    return match.group(1) if match else None

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

SOT_MAP = {
    "email": "Email Account Notifications",
    "hubspot": "HubSpot CRM",
    "zoho": "Zoho CRM",
    "salesforce": "Salesforce CRM",
    "servicetitan": "ServiceTitan CRM",
    "housecallpro": "Housecall Pro CRM",
    "quickbooks": "QuickBooks Billing",
    "xero": "Xero Accounting"
}


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
    fbclid: Optional[str] = None
    li_fat_id: Optional[str] = None
    msclkid: Optional[str] = None


class ClientCreate(BaseModel):
    name: str
    callrail_company_id: str
    google_ads_customer_id: str
    facebook_ads_id: Optional[str] = ""
    linkedin_ads_id: Optional[str] = ""
    microsoft_ads_id: Optional[str] = ""
    lead_gen_method: str
    qualification_criteria: str
    source_of_truth: str
    email_provider: Optional[str] = ""
    email_account: Optional[str] = ""
    crm_deal_tags: Optional[str] = ""
    crm_lead_tags: Optional[str] = ""
    lead_count_rule: str
    exclude_past_customers: str


# ---------------------------------------------------------
# ENDPOINTS
# ---------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def read_root():
    """Agency Portal Landing Page."""
    return """
    <html>
        <head>
            <title>Multi-Tenant Multi-Channel Attribution Engine 🤖</title>
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
                <h1>Multi-Channel Attribution Engine Live! 🚀</h1>
                <p>Status: <span class="badge">Healthy, Multi-Tenant & AI-Enabled</span></p>
                <p>Welcome, Corey. Your agency platform is live with secure SQLite schema routing and Claude 4.5 Haiku.</p>
                
                <a href="/dashboard" class="btn">📊 Open Agency & Client Dashboard</a>
                
                <div class="feature-list">
                    <h3>Multi-Tenant Architecture Capabilities:</h3>
                    <ul>
                        <li>🏢 <strong>Multi-Channel Tracking</strong>: Seamless matching of Google (GCLID), Facebook (FBCLID), LinkedIn (LI_FAT_ID), and Microsoft (MSCLKID) Click IDs!</li>
                        <li>🏢 <strong>4-Step Onboarding Wizard</strong>: Custom qualification mapping, billing order routing, and CRM parameters.</li>
                        <li>📥 <strong>Dynamic Webhook Endpoint</strong>: `/webhooks/callrail?client_id=X` automatically links tracking logs to the correct account.</li>
                        <li>🧠 <strong>Claude 4.5 Haiku Custom Audits</strong>: Real-time transcript parsing based on client's specific qualification definitions.</li>
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
        cursor.execute("SELECT id, name, google_ads_customer_id, facebook_ads_id, linkedin_ads_id, microsoft_ads_id FROM clients ORDER BY name ASC")
        clients = cursor.fetchall()
        
        # Determine filtering
        selected_client_id = client_id if client_id is not None else 0 # 0 signifies "All Clients" (Agency Overview)
        
        # 2. Query Dashboard Rows and Calculations
        if selected_client_id == 0:
            # Multi-Client (All Clients) View - Join with clients table to display client names
            cursor.execute("""
                SELECT s.id, s.phone, s.email, s.name, s.company, s.gclid, s.source, s.qualified, s.sale_closed, s.value, s.reason, s.created_at, c.name, s.fbclid, s.li_fat_id, s.msclkid
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
                SELECT s.id, s.phone, s.email, s.name, s.company, s.gclid, s.source, s.qualified, s.sale_closed, s.value, s.reason, s.created_at, c.name, s.fbclid, s.li_fat_id, s.msclkid
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
    
    # Count how many of these leads have click IDs for each channel (and are either qualified or closed)
    exportable_google = sum(1 for r in rows if r[5] and (r[7] == 'YES' or r[8] == 'YES'))
    exportable_facebook = sum(1 for r in rows if r[13] and (r[7] == 'YES' or r[8] == 'YES'))
    exportable_linkedin = sum(1 for r in rows if r[14] and (r[7] == 'YES' or r[8] == 'YES'))
    exportable_microsoft = sum(1 for r in rows if r[15] and (r[7] == 'YES' or r[8] == 'YES'))

    # Generate the Selector Dropdown Options
    dropdown_options = f'<option value="0" {"selected" if selected_client_id == 0 else ""}>📂 [Show All Clients / Agency View]</option>'
    for c_id, c_name, c_ads, c_fb, c_li, c_ms in clients:
        is_selected = "selected" if selected_client_id == c_id else ""
        dropdown_options += f'<option value="{c_id}" {is_selected}>👤 {c_name} (Ads: {c_ads})</option>'

    # Convert rows to table items
    table_rows_html = ""
    for r in rows:
        id_val, phone, email, name, company, gclid, source, qualified, sale_closed, value, reason, created_at, client_name_linked, fbclid, li_fat_id, msclkid = r
        
        qual_badge = '<span style="background: #e8f5e9; color: #2e7d32; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 12px;">YES</span>' if qualified == 'YES' else '<span style="background: #ffebee; color: #c62828; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 12px;">NO</span>'
        closed_badge = '<span style="background: #e8f5e9; color: #2e7d32; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 12px;">YES</span>' if sale_closed == 'YES' else '<span style="background: #ffebee; color: #c62828; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 12px;">NO</span>'
        
        # Build multi-channel click ID display blocks
        click_ids_list = []
        if gclid:
            click_ids_list.append(f'<span style="display:inline-block; margin-bottom: 2px;"><strong style="color: #4285F4; font-size: 10px;">G:</strong> <code style="background: #f1f3f4; padding: 1px 4px; border-radius: 3px; font-size: 11px;">{gclid}</code></span>')
        if fbclid:
            click_ids_list.append(f'<span style="display:inline-block; margin-bottom: 2px;"><strong style="color: #1877F2; font-size: 10px;">F:</strong> <code style="background: #f1f3f4; padding: 1px 4px; border-radius: 3px; font-size: 11px;">{fbclid}</code></span>')
        if li_fat_id:
            click_ids_list.append(f'<span style="display:inline-block; margin-bottom: 2px;"><strong style="color: #0A66C2; font-size: 10px;">L:</strong> <code style="background: #f1f3f4; padding: 1px 4px; border-radius: 3px; font-size: 11px;">{li_fat_id}</code></span>')
        if msclkid:
            click_ids_list.append(f'<span style="display:inline-block; margin-bottom: 2px;"><strong style="color: #00A4EF; font-size: 10px;">M:</strong> <code style="background: #f1f3f4; padding: 1px 4px; border-radius: 3px; font-size: 11px;">{msclkid}</code></span>')
            
        click_ids_display = "<br>".join(click_ids_list) if click_ids_list else '<span style="color: #999; font-style: italic;">None detected</span>'
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
            <td>{click_ids_display}</td>
            <td>{qual_badge}</td>
            <td>{closed_badge}</td>
            <td>{value_display}</td>
            <td><small>{reason or 'N/A'}</small></td>
        </tr>
        """

    if not table_rows_html:
        table_rows_html = f'<tr><td colspan="{"10" if selected_client_id == 0 else "9"}" style="text-align: center; color: #888; padding: 40px;">No lead sessions recorded for this client. Set up their CallRail webhook to populate this space!</td></tr>'

    # Build multi-channel action buttons dynamically
    if selected_client_id == 0:
        google_export_button = '<button class="btn-export disabled" onclick="alert(\'Please select a specific client from the dropdown above to export their Google Ads offline conversion CSV!\')" style="opacity:0.6; cursor:not-allowed; background-color: #bdc3c7; width: 100%;">📥 Export Disabled</button>'
        facebook_export_button = '<button class="btn-export disabled" onclick="alert(\'Please select a specific client from the dropdown above to export their Facebook conversions CSV!\')" style="opacity:0.6; cursor:not-allowed; background-color: #bdc3c7; width: 100%;">📥 Export Disabled</button>'
        linkedin_export_button = '<button class="btn-export disabled" onclick="alert(\'Please select a specific client from the dropdown above to export their LinkedIn conversions CSV!\')" style="opacity:0.6; cursor:not-allowed; background-color: #bdc3c7; width: 100%;">📥 Export Disabled</button>'
        microsoft_export_button = '<button class="btn-export disabled" onclick="alert(\'Please select a specific client from the dropdown above to export their Microsoft conversions CSV!\')" style="opacity:0.6; cursor:not-allowed; background-color: #bdc3c7; width: 100%;">📥 Export Disabled</button>'
    else:
        google_export_button = f'<a href="/dashboard/export/google?client_id={selected_client_id}" class="btn-export" style="background-color: #4285F4; text-align: center; text-decoration: none; width: 100%;">📥 Download Google CSV ({exportable_google})</a>'
        facebook_export_button = f'<a href="/dashboard/export/facebook?client_id={selected_client_id}" class="btn-export" style="background-color: #1877F2; text-align: center; text-decoration: none; width: 100%;">📥 Download Meta CSV ({exportable_facebook})</a>'
        linkedin_export_button = f'<a href="/dashboard/export/linkedin?client_id={selected_client_id}" class="btn-export" style="background-color: #0A66C2; text-align: center; text-decoration: none; width: 100%;">📥 Download LinkedIn CSV ({exportable_linkedin})</a>'
        microsoft_export_button = f'<a href="/dashboard/export/microsoft?client_id={selected_client_id}" class="btn-export" style="background-color: #00A4EF; text-align: center; text-decoration: none; width: 100%;">📥 Download Bing CSV ({exportable_microsoft})</a>'

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
                
                /* Multi-Channel Grid */
                .export-card-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 15px; margin-bottom: 30px; }}
                .export-card {{ background: white; border: 1px solid #eaeaea; padding: 15px; border-radius: 6px; display: flex; flex-direction: column; justify-content: space-between; box-shadow: 0 2px 4px rgba(0,0,0,0.02); }}
                .export-card h4 {{ margin: 0 0 5px 0; font-size: 14px; font-weight: bold; }}
                .export-card p {{ margin: 0 0 15px 0; font-size: 11px; color: #666; line-height: 1.4; }}
                
                /* Table Styles */
                .table-responsive {{ overflow-x: auto; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 14px; }}
                th, td {{ padding: 12px 15px; text-align: left; border-bottom: 1px solid #eaeaea; }}
                th {{ background-color: #f8f9fa; color: #495057; font-weight: 600; text-transform: uppercase; font-size: 11px; letter-spacing: 0.5px; }}
                tr:hover {{ background-color: #fdfdfd; }}
                .badge-source {{ background: #e0f2f1; color: #00695c; font-size: 11px; padding: 2px 6px; border-radius: 4px; font-weight: bold; }}
                .client-badge {{ background: #eceff1; color: #37474f; font-size: 11px; padding: 3px 8px; border-radius: 4px; font-weight: bold; border: 1px solid #cfd8dc; }}
                
                .btn-export {{ display: block; color: white; padding: 10px 12px; border-radius: 5px; font-weight: bold; font-size: 13px; border: none; transition: filter 0.2s; cursor: pointer; }}
                .btn-export:hover {{ filter: brightness(0.9); }}
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
                        <a href="/dashboard/add-client" class="btn-add-client">➕ Onboard Client</a>
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

                <!-- Multi-Channel Exports Panel -->
                <div style="background: #fafafa; border: 1px solid #e0e0e0; border-radius: 8px; padding: 20px; margin-bottom: 30px;">
                    <h3 style="margin: 0 0 15px 0; color: #1a237e; font-size: 15px; text-transform: uppercase; letter-spacing: 0.5px;">📥 Multi-Channel Offline Conversion Exports</h3>
                    <div class="export-card-grid">
                        <!-- Google Ads -->
                        <div class="export-card">
                            <div>
                                <h4 style="color: #4285F4;">Google Ads (GCLID)</h4>
                                <p>Export verified lead signals and transaction revenue for Smart Bidding optimization.</p>
                            </div>
                            {google_export_button}
                        </div>
                        <!-- Facebook Ads -->
                        <div class="export-card">
                            <div>
                                <h4 style="color: #1877F2;">Meta/Facebook (FBCLID)</h4>
                                <p>Export offline events to optimize Facebook Conversions API and Custom Audiences.</p>
                            </div>
                            {facebook_export_button}
                        </div>
                        <!-- LinkedIn Ads -->
                        <div class="export-card">
                            <div>
                                <h4 style="color: #0A66C2;">LinkedIn Ads (LI_FAT_ID)</h4>
                                <p>Export professional business conversions directly into LinkedIn Campaign Manager.</p>
                            </div>
                            {linkedin_export_button}
                        </div>
                        <!-- Microsoft Ads -->
                        <div class="export-card">
                            <div>
                                <h4 style="color: #00A4EF;">Microsoft Ads (MSCLKID)</h4>
                                <p>Export click-matched offline sessions back into Bing/Microsoft campaign metrics.</p>
                            </div>
                            {microsoft_export_button}
                        </div>
                    </div>
                </div>

                <!-- Table -->
                <h3 style="margin: 0 0 15px 0; color: #1a237e;">Lead Activity Log</h3>
                <div class="table-responsive">
                    <table>
                        <thead>
                            <tr>
                                <th>ID</th>
                                {client_th_html}
                                <th>Timestamp</th>
                                <th>Source</th>
                                <th>Lead Contact</th>
                                <th>Click IDs Detected</th>
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
    """Page to onboard a new client with complete wizard properties."""
    return """
    <!DOCTYPE html>
    <html>
        <head>
            <title>Onboard New Client 👤</title>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif; background-color: #f4f6f9; color: #333; margin: 0; padding: 20px; }
                .container { max-width: 600px; margin: 30px auto; background: white; padding: 40px; border-radius: 12px; box-shadow: 0px 4px 15px rgba(0,0,0,0.05); }
                h1 { margin-top: 0; color: #1a237e; font-size: 24px; text-align: center; }
                .subtitle { text-align: center; color: #666; margin-top: -15px; margin-bottom: 30px; font-size: 14px; }
                
                /* Step Progress Tracker */
                .progress-container { display: flex; justify-content: space-between; position: relative; margin-bottom: 40px; max-width: 450px; margin-left: auto; margin-right: auto; }
                .progress-container::before { content: ''; background-color: #e0e0e0; position: absolute; top: 50%; left: 0; transform: translateY(-50%); height: 4px; width: 100%; z-index: 1; }
                .progress-bar { background-color: #1a237e; position: absolute; top: 50%; left: 0; transform: translateY(-50%); height: 4px; width: 0%; z-index: 2; transition: width 0.3s ease; }
                .step-circle { background-color: #fff; border: 3px solid #e0e0e0; border-radius: 50%; height: 32px; width: 32px; display: flex; align-items: center; justify-content: center; z-index: 3; font-weight: bold; font-size: 13px; color: #999; transition: all 0.3s ease; }
                .step-circle.active { border-color: #1a237e; color: #1a237e; background-color: #e8eaf6; }
                .step-circle.completed { border-color: #2e7d32; color: #fff; background-color: #2e7d32; }
                
                /* Step Panels */
                .wizard-step { display: none; }
                .wizard-step.active { display: block; }
                
                .form-group { margin-bottom: 20px; }
                .form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }
                label { display: block; font-weight: 600; margin-bottom: 8px; font-size: 13px; color: #495057; }
                input[type="text"], select { width: 100%; padding: 10px 12px; border-radius: 6px; border: 1px solid #ced4da; box-sizing: border-box; font-size: 14px; outline: none; transition: border-color 0.2s; font-family: inherit; }
                input[type="text"]:focus, select:focus { border-color: #1a237e; }
                
                .instructions { background-color: #e8eaf6; border-left: 4px solid #1a237e; padding: 15px; border-radius: 4px; font-size: 13px; color: #1a237e; line-height: 1.5; margin-bottom: 25px; }
                
                /* Card Radio Styles */
                .card-radio-group { display: flex; flex-direction: column; gap: 10px; margin-bottom: 10px; }
                .card-radio { display: flex; align-items: center; padding: 12px 15px; border: 2px solid #e0e0e0; border-radius: 8px; cursor: pointer; transition: all 0.2s; gap: 12px; position: relative; }
                .card-radio:hover { border-color: #b3e5fc; background-color: #f6fbfd; }
                .card-radio.selected { border-color: #1a237e; background-color: #e8eaf6; }
                .card-radio input[type="radio"] { position: absolute; opacity: 0; }
                .card-radio-label { font-size: 14px; font-weight: bold; color: #333; margin: 0; }
                .card-radio-sub { font-size: 12px; color: #666; margin-top: 3px; }
                
                /* Navigation Buttons */
                .nav-buttons { display: flex; justify-content: space-between; margin-top: 30px; border-top: 1px solid #eaeaea; padding-top: 20px; }
                .btn-nav { background-color: #1a237e; color: white; padding: 10px 22px; border: none; border-radius: 6px; font-weight: bold; font-size: 14px; cursor: pointer; transition: background 0.2s; }
                .btn-nav:hover { background-color: #0d1b2a; }
                .btn-nav.secondary { background-color: #e0e0e0; color: #333; }
                .btn-nav.secondary:hover { background-color: #d5d5d5; }
                .btn-nav.success { background-color: #2e7d32; }
                .btn-nav.success:hover { background-color: #1b5e20; }
                
                .alert { padding: 12px; border-radius: 6px; margin-bottom: 20px; display: none; font-size: 14px; font-weight: 600; }
                .alert-error { background-color: #ffebee; color: #c62828; border: 1px solid #ffcdd2; }
                .alert-success { background-color: #e8f5e9; color: #2e7d32; border: 1px solid #c8e6c9; }
                
                /* Helper classes */
                .conditional-box { background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none; animation: slideDown 0.3s ease-out; }
                @keyframes slideDown {
                    from { opacity: 0; transform: translateY(-10px); }
                    to { opacity: 1; transform: translateY(0); }
                }
            </style>
        </head>
        <body>
            <div class="container">
                <h1 id="heading-title">Onboard Client Account 👤</h1>
                <p class="subtitle" id="heading-subtitle">Agency Setup & Software Mapping Pipeline</p>
                
                <!-- Step Progress -->
                <div class="progress-container" id="progress-container">
                    <div class="progress-bar" id="progress-bar"></div>
                    <div class="step-circle active" data-step="1">1</div>
                    <div class="step-circle" data-step="2">2</div>
                    <div class="step-circle" data-step="3">3</div>
                    <div class="step-circle" data-step="4">4</div>
                </div>
                
                <div id="alert-box" class="alert alert-error"></div>
                
                <!-- Wizard Form -->
                <form id="onboarding-form">
                    
                    <!-- STEP 1: Accounts & Tracking channels -->
                    <div class="wizard-step active" id="step-panel-1">
                        <div class="instructions">
                            🚀 <strong>Step 1: General Profile & Ad Accounts</strong><br>
                            Enter the general company properties and the advertising account IDs to sync conversions and metrics.
                        </div>
                        
                        <div class="form-group">
                            <label for="name">Client Business Name</label>
                            <input type="text" id="name" required placeholder="e.g. Priority Plumbing">
                        </div>
                        
                        <div class="form-group">
                            <label for="callrail_company_id">CallRail Company ID (or Account ID)</label>
                            <input type="text" id="callrail_company_id" required placeholder="e.g. comp_plumbing">
                        </div>
                        
                        <div class="form-row">
                            <div class="form-group">
                                <label for="google_ads_customer_id">Google Ads Customer ID</label>
                                <input type="text" id="google_ads_customer_id" required placeholder="e.g. 123-456-7890">
                            </div>
                            <div class="form-group">
                                <label for="facebook_ads_id">Facebook Ads Pixel/Account ID</label>
                                <input type="text" id="facebook_ads_id" placeholder="e.g. fb_pixel_999">
                            </div>
                        </div>
                        
                        <div class="form-row">
                            <div class="form-group">
                                <label for="linkedin_ads_id">LinkedIn Ads Account ID</label>
                                <input type="text" id="linkedin_ads_id" placeholder="e.g. li_account_12">
                            </div>
                            <div class="form-group">
                                <label for="microsoft_ads_id">Microsoft (Bing) Ads ID</label>
                                <input type="text" id="microsoft_ads_id" placeholder="e.g. ms_campaign_45">
                            </div>
                        </div>
                    </div>
                    
                    <!-- STEP 2: Lead Gen & Qualification -->
                    <div class="wizard-step" id="step-panel-2">
                        <div class="instructions">
                            🧠 <strong>Step 2: Lead Generation & Qualification Preferences</strong><br>
                            Tell us how this client receives and identifies a qualified lead so that Claude's sales auditing aligns perfectly.
                        </div>
                        
                        <div class="form-group">
                            <label>How do you generate your leads?</label>
                            <div class="card-radio-group">
                                <div class="card-radio selected" onclick="selectCardRadio('lead_gen_method', 'both', this)">
                                    <input type="radio" name="lead_gen_method" value="both" checked>
                                    <div>
                                        <div class="card-radio-label">Both Phone Calls & Web Forms</div>
                                        <div class="card-radio-sub">Full multi-channel capture (Recommended)</div>
                                    </div>
                                </div>
                                <div class="card-radio" onclick="selectCardRadio('lead_gen_method', 'phone', this)">
                                    <input type="radio" name="lead_gen_method" value="phone">
                                    <div>
                                        <div class="card-radio-label">Phone Calls Only</div>
                                        <div class="card-radio-sub">Auditing CallRail phone transcripts exclusively</div>
                                    </div>
                                </div>
                                <div class="card-radio" onclick="selectCardRadio('lead_gen_method', 'form', this)">
                                    <input type="radio" name="lead_gen_method" value="form">
                                    <div>
                                        <div class="card-radio-label">Form Submissions Only</div>
                                        <div class="card-radio-sub">Matching visitor website form GCLIDs only</div>
                                    </div>
                                </div>
                            </div>
                        </div>
                        
                        <div class="form-group">
                            <label for="qualification_criteria">How do you qualify a lead?</label>
                            <select id="qualification_criteria">
                                <option value="C">👤 Option C: Someone who books an appointment (Local Services Default)</option>
                                <option value="A">👤 Option A: Someone that I have a conversation with</option>
                                <option value="B">👤 Option B: Someone who shows strong buying interest</option>
                                <option value="D">👤 Option D: Someone who books a demo</option>
                                <option value="E">👤 Option E: Someone who requests a quote</option>
                                <option value="F">👤 Option F: Someone who we send a proposal</option>
                                <option value="H">👤 Option H: Someone who has qualified insurance</option>
                                <option value="I">👤 Option I: Someone who is credit pre-qualified</option>
                            </select>
                            <small style="color: #666; font-size: 11px; margin-top: 4px; display: block;">
                                💡 This choice dynamically feeds directly into <strong>Claude 4.5 Haiku's prompt</strong> to customize auditing.
                            </small>
                        </div>
                    </div>
                    
                    <!-- STEP 3: Source of Truth & CRM -->
                    <div class="wizard-step" id="step-panel-3">
                        <div class="instructions">
                            📁 <strong>Step 3: Single Source of Truth & Integration Mapping</strong><br>
                            Identify where your conversion status lives. Your platform scans this resource to upload revenue data to Ad Networks.
                        </div>
                        
                        <div class="form-group">
                            <label for="source_of_truth">Where is your Single Source of Truth?</label>
                            <select id="source_of_truth" onchange="toggleSOTFields()">
                                <option value="hubspot">HubSpot CRM</option>
                                <option value="salesforce">Salesforce CRM</option>
                                <option value="zoho">Zoho CRM</option>
                                <option value="servicetitan">ServiceTitan (Home Services)</option>
                                <option value="housecallpro">Housecall Pro (Home Services)</option>
                                <option value="quickbooks">QuickBooks Accounting</option>
                                <option value="xero">Xero Accounting</option>
                                <option value="email">No CRM: Email Accounts (Gmail/Outlook Fallback)</option>
                            </select>
                        </div>
                        
                        <!-- CONDITIONAL INPUT: CRM Deal status tags (HubSpot, Salesforce, Zoho) -->
                        <div id="sot-deal-tags-box" class="conditional-box" style="display: block;">
                            <label for="crm_deal_tags">Which tags/statuses under <strong>Deals</strong> signify a qualified/won conversion?</label>
                            <input type="text" id="crm_deal_tags" placeholder="e.g. closed-won, scheduled, estimate-approved">
                            <small style="color: #666; font-size: 11px; margin-top: 4px; display: block;">
                                List comma-separated tags that trigger a successful offline sale upload.
                            </small>
                        </div>
                        
                        <!-- CONDITIONAL INPUT: CRM Lead status tags (ServiceTitan, Housecall Pro) -->
                        <div id="sot-lead-tags-box" class="conditional-box">
                            <label for="crm_lead_tags">Which tags/statuses under <strong>Leads</strong> signify qualification?</label>
                            <input type="text" id="crm_lead_tags" placeholder="e.g. job-booked, estimate-given, dispatched">
                        </div>
                        
                        <!-- CONDITIONAL INPUT: Email account fallback settings -->
                        <div id="sot-email-box" class="conditional-box">
                            <div class="form-group">
                                <label for="email_provider">Email Provider</label>
                                <select id="email_provider">
                                    <option value="gmail">Google Gmail API</option>
                                    <option value="outlook">Microsoft Outlook 365</option>
                                    <option value="custom_imap">Custom IMAP (Secure Server)</option>
                                </select>
                            </div>
                            <div class="form-group" style="margin-bottom: 0;">
                                <label for="email_account">Onboarding Integration Email Account</label>
                                <input type="text" id="email_account" placeholder="e.g. bookings@clientcompany.com">
                                <small style="color: #666; font-size: 11px; margin-top: 4px; display: block;">
                                    Your system will securely monitor this inbox for transcript files & booking notifications.
                                </small>
                            </div>
                        </div>
                    </div>
                    
                    <!-- STEP 4: Conversion & Deduplication Rules -->
                    <div class="wizard-step" id="step-panel-4">
                        <div class="instructions">
                            💰 <strong>Step 4: Smart Deduplication & Conversion Control</strong><br>
                            Define parameters for repeat conversions to ensure you only bid on optimal, unique customer value.
                        </div>
                        
                        <div class="form-group">
                            <label>How should we track multiple leads from the same customer?</label>
                            <div class="card-radio-group">
                                <div class="card-radio selected" onclick="selectCardRadio('lead_count_rule', 'all', this)">
                                    <input type="radio" name="lead_count_rule" value="all" checked>
                                    <div>
                                        <div class="card-radio-label">Count Every Lead Session</div>
                                        <div class="card-radio-sub">Pushes all unique calls/forms from the same user to smart bidding</div>
                                    </div>
                                </div>
                                <div class="card-radio" onclick="selectCardRadio('lead_count_rule', 'maximum_one', this)">
                                    <input type="radio" name="lead_count_rule" value="maximum_one">
                                    <div>
                                        <div class="card-radio-label">Maximum of One Conversion Each</div>
                                        <div class="card-radio-sub">Caps tracking at 1 conversion per customer to prevent inflated signals</div>
                                    </div>
                                </div>
                            </div>
                        </div>
                        
                        <div class="form-group">
                            <label>Would you like to exclude past customers as eligible lead conversions?</label>
                            <div class="card-radio-group">
                                <div class="card-radio selected" onclick="selectCardRadio('exclude_past_customers', 'NO', this)">
                                    <input type="radio" name="exclude_past_customers" value="NO" checked>
                                    <div>
                                        <div class="card-radio-label">No, allow past customers (Recommended)</div>
                                        <div class="card-radio-sub">Allows repeat buyers to optimize overall ad account ROAS</div>
                                    </div>
                                </div>
                                <div class="card-radio" onclick="selectCardRadio('exclude_past_customers', 'YES', this)">
                                    <input type="radio" name="exclude_past_customers" value="YES">
                                    <div>
                                        <div class="card-radio-label">Yes, exclude past customers</div>
                                        <div class="card-radio-sub">Strict deduplication: only optimizes ads on completely net-new leads</div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                    
                    <!-- Navigation Panel -->
                    <div class="nav-buttons">
                        <button type="button" class="btn-nav secondary" id="prev-btn" onclick="changeStep(-1)" style="visibility: hidden;">⬅️ Back</button>
                        <button type="button" class="btn-nav" id="next-btn" onclick="changeStep(1)">Next ➡️</button>
                    </div>
                </form>
                
                <!-- Dynamic Setup Webhook Screen (Hidden initially) -->
                <div id="success-screen" style="display: none; text-align: center;">
                    <div style="font-size: 50px; margin-bottom: 15px;">🎉</div>
                    <h2 style="color: #2e7d32; margin-top: 0;">Client Onboarded Successfully!</h2>
                    <p style="color: #555; font-size: 14px; margin-bottom: 25px;">
                        The account configuration file for <strong id="registered-client-name"></strong> has been created.
                    </p>
                    
                    <div class="instructions" style="text-align: left; background-color: #e8eaf6; border-left: 4px solid #1a237e;">
                        🔑 <strong>Step 2: Configure CallRail Integration</strong><br>
                        Your live platform webhook is built. Copy this link and paste it into CallRail:
                    </div>
                    
                    <div style="display: flex; gap: 8px; margin-bottom: 15px;">
                        <input type="text" id="webhook-url-input" readonly style="flex: 1; padding: 10px; border-radius: 6px; border: 1px solid #ced4da; font-family: monospace; font-size: 12px; background-color: #f8f9fa;">
                        <button onclick="copyWebhookUrl()" id="copy-btn" class="btn-submit" style="margin: 0; width: auto; white-space: nowrap; padding: 0 15px; font-size: 14px; background-color: #2e7d32;">📋 Copy URL</button>
                    </div>
                    
                    <div class="instructions" style="text-align: left; background-color: #fff3cd; border-left-color: #ffc107; color: #856404; font-size: 12px;">
                        ⚠️ <strong>Reminder:</strong> For CallRail call transcript tracking, make sure the webhook trigger inside CallRail is set to <strong>"Call Completed"</strong>.
                    </div>
                    
                    <a href="/dashboard" class="btn-submit" style="display: block; text-decoration: none; text-align: center; line-height: 20px; background-color: #1a237e;">📊 Proceed to Dashboard</a>
                </div>
                
                <a href="/dashboard" class="btn-cancel" id="cancel-link">⬅️ Cancel and Return to Dashboard</a>
            </div>
            
            <script>
                let currentStep = 1;
                const totalSteps = 4;
                
                function updateProgressBar() {
                    const percent = ((currentStep - 1) / (totalSteps - 1)) * 100;
                    document.getElementById('progress-bar').style.width = percent + '%';
                    
                    document.querySelectorAll('.step-circle').forEach((circle) => {
                        const step = parseInt(circle.getAttribute('data-step'));
                        if (step < currentStep) {
                            circle.className = 'step-circle completed';
                            circle.innerText = '✓';
                        } else if (step === currentStep) {
                            circle.className = 'step-circle active';
                            circle.innerText = step;
                        } else {
                            circle.className = 'step-circle';
                            circle.innerText = step;
                        }
                    });
                }
                
                function changeStep(direction) {
                    if (direction === 1) {
                        if (currentStep === 1) {
                            const name = document.getElementById('name').value.trim();
                            const callrail = document.getElementById('callrail_company_id').value.trim();
                            const g_ads = document.getElementById('google_ads_customer_id').value.trim();
                            if (!name || !callrail || !g_ads) {
                                showErrorAlert('Please fill out all required fields on Step 1.');
                                return;
                            }
                        }
                    }
                    
                    document.getElementById(`step-panel-${currentStep}`).classList.remove('active');
                    currentStep += direction;
                    document.getElementById(`step-panel-${currentStep}`).classList.add('active');
                    
                    const prevBtn = document.getElementById('prev-btn');
                    const nextBtn = document.getElementById('next-btn');
                    
                    prevBtn.style.visibility = currentStep === 1 ? 'hidden' : 'visible';
                    
                    if (currentStep === totalSteps) {
                        nextBtn.innerText = '🚀 Complete Onboarding';
                        nextBtn.className = 'btn-nav success';
                        nextBtn.onclick = submitWizard;
                    } else {
                        nextBtn.innerText = 'Next ➡️';
                        nextBtn.className = 'btn-nav';
                        nextBtn.onclick = () => changeStep(1);
                    }
                    
                    updateProgressBar();
                    hideErrorAlert();
                }
                
                function selectCardRadio(name, value, element) {
                    element.parentNode.querySelectorAll('.card-radio').forEach(card => {
                        card.classList.remove('selected');
                    });
                    element.classList.add('selected');
                    element.querySelector('input[type="radio"]').checked = true;
                }
                
                function toggleSOTFields() {
                    const sot = document.getElementById('source_of_truth').value;
                    const dealBox = document.getElementById('sot-deal-tags-box');
                    const leadBox = document.getElementById('sot-lead-tags-box');
                    const emailBox = document.getElementById('sot-email-box');
                    
                    dealBox.style.display = 'none';
                    leadBox.style.display = 'none';
                    emailBox.style.display = 'none';
                    
                    if (['hubspot', 'salesforce', 'zoho'].includes(sot)) {
                        dealBox.style.display = 'block';
                    } else if (['servicetitan', 'housecallpro'].includes(sot)) {
                        leadBox.style.display = 'block';
                    } else if (sot === 'email') {
                        emailBox.style.display = 'block';
                    }
                }
                
                function showErrorAlert(text) {
                    const alertBox = document.getElementById('alert-box');
                    alertBox.innerText = text;
                    alertBox.style.display = 'block';
                    window.scrollTo({ top: 0, behavior: 'smooth' });
                }
                
                function hideErrorAlert() {
                    document.getElementById('alert-box').style.display = 'none';
                }
                
                async function submitWizard() {
                    const alertBox = document.getElementById('alert-box');
                    const nextBtn = document.getElementById('next-btn');
                    const form = document.getElementById('onboarding-form');
                    const progress = document.getElementById('progress-container');
                    const heading = document.getElementById('heading-title');
                    const subHeading = document.getElementById('heading-subtitle');
                    const cancelLink = document.getElementById('cancel-link');
                    const successScreen = document.getElementById('success-screen');
                    
                    alertBox.style.display = 'none';
                    nextBtn.disabled = true;
                    nextBtn.innerText = 'Creating profile...';
                    
                    const payload = {
                        name: document.getElementById('name').value.trim(),
                        callrail_company_id: document.getElementById('callrail_company_id').value.trim(),
                        google_ads_customer_id: document.getElementById('google_ads_customer_id').value.trim(),
                        facebook_ads_id: document.getElementById('facebook_ads_id').value.trim(),
                        linkedin_ads_id: document.getElementById('linkedin_ads_id').value.trim(),
                        microsoft_ads_id: document.getElementById('microsoft_ads_id').value.trim(),
                        lead_gen_method: document.querySelector('input[name="lead_gen_method"]:checked').value,
                        qualification_criteria: document.getElementById('qualification_criteria').value,
                        source_of_truth: document.getElementById('source_of_truth').value,
                        email_provider: document.getElementById('email_provider').value,
                        email_account: document.getElementById('email_account').value.trim(),
                        crm_deal_tags: document.getElementById('crm_deal_tags').value.trim(),
                        crm_lead_tags: document.getElementById('crm_lead_tags').value.trim(),
                        lead_count_rule: document.querySelector('input[name="lead_count_rule"]:checked').value,
                        exclude_past_customers: document.querySelector('input[name="exclude_past_customers"]:checked').value
                    };
                    
                    try {
                        const response = await fetch('/dashboard/add-client', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json'
                            },
                            body: JSON.stringify(payload)
                        });
                        
                        const data = await response.json();
                        
                        if (response.ok) {
                            form.style.display = 'none';
                            progress.style.display = 'none';
                            heading.style.display = 'none';
                            subHeading.style.display = 'none';
                            cancelLink.style.display = 'none';
                            
                            document.getElementById('registered-client-name').innerText = payload.name;
                            const liveWebhook = `${window.location.origin}/webhooks/callrail?client_id=${data.client_id}`;
                            document.getElementById('webhook-url-input').value = liveWebhook;
                            
                            successScreen.style.display = 'block';
                        } else {
                            throw new Error(data.detail || 'An unexpected database error occurred.');
                        }
                    } catch (error) {
                        showErrorAlert('Error: ' + error.message);
                        nextBtn.disabled = false;
                        nextBtn.innerText = '🚀 Complete Onboarding';
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
    """Endpoint to handle questionnaire form submission."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Verify unique CallRail ID
        cursor.execute("SELECT id, name FROM clients WHERE callrail_company_id = ?", (client.callrail_company_id,))
        existing = cursor.fetchone()
        if existing:
            raise HTTPException(status_code=400, detail=f"CallRail Company ID '{client.callrail_company_id}' is already registered to client '{existing[1]}'.")
            
        cursor.execute("""
            INSERT INTO clients (
                name, callrail_company_id, google_ads_customer_id, facebook_ads_id, linkedin_ads_id, microsoft_ads_id,
                lead_gen_method, qualification_criteria, source_of_truth, email_provider, email_account,
                crm_deal_tags, crm_lead_tags, lead_count_rule, exclude_past_customers
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            client.name, 
            client.callrail_company_id, 
            client.google_ads_customer_id, 
            client.facebook_ads_id,
            client.linkedin_ads_id,
            client.microsoft_ads_id,
            client.lead_gen_method,
            client.qualification_criteria,
            client.source_of_truth,
            client.email_provider,
            client.email_account,
            client.crm_deal_tags,
            client.crm_lead_tags,
            client.lead_count_rule,
            client.exclude_past_customers
        ))
        
        client_id = cursor.lastrowid
        
        conn.commit()
        conn.close()
        return {
            "status": "success", 
            "client_id": client_id,
            "message": f"Client '{client.name}' onboarded successfully!"
        }
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database insertion error: {str(e)}")


@app.get("/dashboard/export/google")
def export_google_conversions(client_id: int):
    """
    Exports qualified and closed conversions that have a valid GCLID 
    into a Google Ads-compliant CSV upload format, filtered by client_id.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Verify client exists
        cursor.execute("SELECT name, google_ads_customer_id FROM clients WHERE id = ?", (client_id,))
        client_row = cursor.fetchone()
        if not client_row:
            raise HTTPException(status_code=400, detail="Invalid client ID")
        
        client_name = client_row[0]
        google_ads_id = client_row[1] or ""
        
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
    
    # 1. Google Ads template parameter header with account-specific Customer ID if available
    ads_parameter = f"Parameters:TimeZone=+0000;customerId={google_ads_id}" if google_ads_id else "Parameters:TimeZone=+0000"
    writer.writerow([ads_parameter])
    
    # 2. Google Ads standard headers
    writer.writerow(["Google Click ID", "Conversion Name", "Conversion Time", "Conversion Value", "Conversion Currency"])
    
    for r in rows:
        gclid, qualified, sale_closed, value, created_at = r
        conv_time = f"{created_at} +0000" if created_at else ""
        
        if sale_closed == 'YES':
            conv_name = "Offline Sale"
            conv_value = float(value or 0.0)
        else:
            conv_name = "Qualified Lead"
            conv_value = 1.0  # Default lead qualification value
            
        writer.writerow([gclid, conv_name, conv_time, conv_value, "USD"])
            
    output.seek(0)
    safe_filename = re.sub(r'\s+', '-', client_name.strip().lower())
    
    headers = {
        'Content-Disposition': f'attachment; filename="google_ads_conversions_{safe_filename}.csv"',
        'Content-Type': 'text/csv'
    }
    return StreamingResponse(output, headers=headers)


@app.get("/dashboard/export/facebook")
def export_facebook_conversions(client_id: int):
    """
    Exports qualified and closed conversions that have a valid FBCLID 
    into a Facebook-compliant Offline Conversions CSV format.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Verify client exists
        cursor.execute("SELECT name, facebook_ads_id FROM clients WHERE id = ?", (client_id,))
        client_row = cursor.fetchone()
        if not client_row:
            raise HTTPException(status_code=400, detail="Invalid client ID")
        
        client_name = client_row[0]
        pixel_id = client_row[1] or ""
        
        # Pull records that have a FBCLID and are either Qualified or Closed belonging to this client
        cursor.execute("""
            SELECT fbclid, qualified, sale_closed, value, created_at
            FROM sessions
            WHERE client_id = ? AND fbclid IS NOT NULL AND fbclid != '' AND (qualified = 'YES' OR sale_closed = 'YES')
            ORDER BY created_at DESC
        """, (client_id,))
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

    # Generate Facebook standard format
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Headers for Meta upload (Facebook Click ID, Event Name, Event Time, Value, Currency, Pixel ID)
    writer.writerow(["fbclid", "Event Name", "Event Time", "Value", "Currency", "Pixel ID"])
    
    for r in rows:
        fbclid, qualified, sale_closed, value, created_at = r
        conv_time = f"{created_at}"
        
        if sale_closed == 'YES':
            event_name = "Purchase"
            event_val = float(value or 0.0)
        else:
            event_name = "Lead"
            event_val = 1.0
            
        writer.writerow([fbclid, event_name, conv_time, f"{event_val:.2f}", "USD", pixel_id])
            
    output.seek(0)
    safe_filename = re.sub(r'\s+', '-', client_name.strip().lower())
    
    headers = {
        'Content-Disposition': f'attachment; filename="facebook_conversions_{safe_filename}.csv"',
        'Content-Type': 'text/csv'
    }
    return StreamingResponse(output, headers=headers)


@app.get("/dashboard/export/linkedin")
def export_linkedin_conversions(client_id: int):
    """
    Exports qualified and closed conversions that have a valid LI_FAT_ID
    into a LinkedIn-compliant Offline Conversions CSV format.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Verify client exists
        cursor.execute("SELECT name, linkedin_ads_id FROM clients WHERE id = ?", (client_id,))
        client_row = cursor.fetchone()
        if not client_row:
            raise HTTPException(status_code=400, detail="Invalid client ID")
        
        client_name = client_row[0]
        linkedin_account_id = client_row[1] or ""
        
        # Pull records that have a LI_FAT_ID belonging to this client
        cursor.execute("""
            SELECT li_fat_id, qualified, sale_closed, value, created_at
            FROM sessions
            WHERE client_id = ? AND li_fat_id IS NOT NULL AND li_fat_id != '' AND (qualified = 'YES' OR sale_closed = 'YES')
            ORDER BY created_at DESC
        """, (client_id,))
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

    output = io.StringIO()
    writer = csv.writer(output)
    
    # LinkedIn Offline Conversion headers
    writer.writerow(["li_fat_id", "Conversion Name", "Conversion Time", "Conversion Value", "Conversion Currency", "LinkedIn Account ID"])
    
    for r in rows:
        li_fat_id, qualified, sale_closed, value, created_at = r
        conv_time = f"{created_at} +0000"
        
        if sale_closed == 'YES':
            conv_name = "Offline Purchase"
            conv_val = float(value or 0.0)
        else:
            conv_name = "Qualified Lead"
            conv_val = 1.0
            
        writer.writerow([li_fat_id, conv_name, conv_time, f"{conv_val:.2f}", "USD", linkedin_account_id])
            
    output.seek(0)
    safe_filename = re.sub(r'\s+', '-', client_name.strip().lower())
    
    headers = {
        'Content-Disposition': f'attachment; filename="linkedin_conversions_{safe_filename}.csv"',
        'Content-Type': 'text/csv'
    }
    return StreamingResponse(output, headers=headers)


@app.get("/dashboard/export/microsoft")
def export_microsoft_conversions(client_id: int):
    """
    Exports qualified and closed conversions that have a valid MSCLKID
    into a Microsoft Ads-compliant Offline Conversions CSV format.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Verify client exists
        cursor.execute("SELECT name, microsoft_ads_id FROM clients WHERE id = ?", (client_id,))
        client_row = cursor.fetchone()
        if not client_row:
            raise HTTPException(status_code=400, detail="Invalid client ID")
        
        client_name = client_row[0]
        microsoft_id = client_row[1] or ""
        
        # Pull records that have a MSCLKID belonging to this client
        cursor.execute("""
            SELECT msclkid, qualified, sale_closed, value, created_at
            FROM sessions
            WHERE client_id = ? AND msclkid IS NOT NULL AND msclkid != '' AND (qualified = 'YES' OR sale_closed = 'YES')
            ORDER BY created_at DESC
        """, (client_id,))
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

    output = io.StringIO()
    writer = csv.writer(output)
    
    # Microsoft Ads offline conversions headers
    writer.writerow(["Microsoft Click ID", "Conversion Name", "Conversion Time", "Conversion Value", "Conversion Currency", "Microsoft Account ID"])
    
    for r in rows:
        msclkid, qualified, sale_closed, value, created_at = r
        conv_time = f"{created_at} +0000"
        
        if sale_closed == 'YES':
            conv_name = "Offline Transaction"
            conv_val = float(value or 0.0)
        else:
            conv_name = "Qualified Lead"
            conv_val = 1.0
            
        writer.writerow([msclkid, conv_name, conv_time, f"{conv_val:.2f}", "USD", microsoft_id])
            
    output.seek(0)
    safe_filename = re.sub(r'\s+', '-', client_name.strip().lower())
    
    headers = {
        'Content-Disposition': f'attachment; filename="microsoft_conversions_{safe_filename}.csv"',
        'Content-Type': 'text/csv'
    }
    return StreamingResponse(output, headers=headers)


@app.post("/webhooks/callrail")
async def receive_callrail_webhook(request: Request, client_id: Optional[int] = None):
    """
    Multi-Tenant CallRail Webhook Receiver.
    If no client_id query param is sent, parses the company/account info inside CallRail's payload to auto-map it!
    Also performs dynamic, regex-based URL query extraction to catch fbclid, li_fat_id, and msclkid.
    """
    try: 
        # 1. Parse the incoming JSON data from CallRail
        payload = await request.json()
        
        # Resolve Multi-Tenant Client Mapping
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
        gclid = payload.get('google_click_id') or payload.get('referrer', {}).get('gclid') or payload.get('gclid')
        fbclid = payload.get('facebook_click_id') or payload.get('fbclid') or payload.get('referrer', {}).get('fbclid')
        li_fat_id = payload.get('linkedin_click_id') or payload.get('li_fat_id') or payload.get('referrer', {}).get('li_fat_id')
        msclkid = payload.get('microsoft_click_id') or payload.get('msclkid') or payload.get('referrer', {}).get('msclkid')
        
        # Advanced dynamic regex URL extraction (for redundancy / fallback)
        landing_page = payload.get('landing_page_url') or payload.get('referrer', {}).get('landing_page_url') or ""
        referrer_url = payload.get('referrer_url') or payload.get('referrer', {}).get('referrer_url') or ""
        
        if not gclid:
            gclid = extract_param_from_url(landing_page, 'gclid') or extract_param_from_url(referrer_url, 'gclid')
        if not fbclid:
            fbclid = extract_param_from_url(landing_page, 'fbclid') or extract_param_from_url(referrer_url, 'fbclid')
        if not li_fat_id:
            li_fat_id = extract_param_from_url(landing_page, 'li_fat_id') or extract_param_from_url(referrer_url, 'li_fat_id')
        if not msclkid:
            msclkid = extract_param_from_url(landing_page, 'msclkid') or extract_param_from_url(referrer_url, 'msclkid')
            
        caller_name = payload.get('customer_name', 'Unknown Caller')
        raw_phone = payload.get('customer_phone_number')
        transcript = payload.get('transcript') or payload.get('transcription') or ""
        
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
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT name, qualification_criteria, lead_count_rule, exclude_past_customers FROM clients WHERE id = ?", (resolved_client_id,))
        client_info = cursor.fetchone()
        conn.close()
        
        if client_info and client_info[1]:
            criteria_code = client_info[1]
            qualification_definition_desc = CRITERIA_MAP.get(criteria_code, qualification_definition_desc)
            
        if transcript.strip():
            print(f"🧠 [Client #{resolved_client_id}] Transcript detected for {caller_name}. Custom Threshold: {qualification_definition_desc}. Auditing...")
            ai_result = analyze_transcript_with_claude(transcript, qualification_definition_desc)
            ai_qualified = ai_result.get("qualified", "NO")
            ai_sale_closed = ai_result.get("sale_closed", "NO")
            ai_value = float(ai_result.get("value", 0.0))
            ai_reason = ai_result.get("reason", "No reason parsed.")
            model_name = "claude-haiku-4-5-20251001"
            print(f"🎯 Audit Complete: Qualified={ai_qualified}, Sales Value=${ai_value}")
        else:
            print(f"⚠️ [Client #{resolved_client_id}] No transcript provided in CallRail webhook for {caller_name}. Skipping AI audit.")

        # 5. Save Session including multi-channel click IDs
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO sessions (
                client_id, phone, name, gclid, fbclid, li_fat_id, msclkid, source, qualified, sale_closed, value, reason, model_used, raw_data
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            resolved_client_id,
            normalized_phone, 
            caller_name, 
            gclid,
            fbclid,
            li_fat_id,
            msclkid,
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


@app.post("/webhooks/form")
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
        
        # 2. Save to SQLite
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO sessions (client_id, phone, email, name, company, gclid, fbclid, li_fat_id, msclkid, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            "form"
        ))
        conn.commit()
        conn.close()
        
        print(f"📝 [Client #{resolved_client_id}] Form Lead saved: Name={full_name}, Phone={normalized_phone}, Email={email_clean}, GCLID={lead.gclid}")
        return {"status": "success", "message": f"Form lead saved under client #{resolved_client_id}."}
        
    except Exception as e:
        print(f"❌ Error saving Form Lead: {e}")
        raise HTTPException(status_code=400, detail=str(e))
