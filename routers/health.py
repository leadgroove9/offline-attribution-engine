from fastapi import APIRouter, Request, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from typing import Optional
from services.auth_service import is_authenticated, get_user_role_and_client
from security.tenant_guard import verify_tenant_access
from database.connection import db_router
from config import ADMIN_EMAILS

router = APIRouter()

@router.get("/dashboard/health", response_class=HTMLResponse)
def view_health_dashboard(request: Request, client_id: Optional[int] = None):
    email = is_authenticated(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)
        
    user_role, user_client_id = get_user_role_and_client(email)
    
    conn = db_router.connect()
    cursor = conn.cursor()
    
    # Fetch all clients
    cursor.execute("SELECT id, name FROM clients ORDER BY name ASC")
    all_clients = cursor.fetchall()
    
    if not all_clients:
        conn.close()
        return HTMLResponse("<h3>No clients onboarding profiles found.</h3>")
        
    active_client_id = client_id if client_id is not None else all_clients[0][0]
    if user_client_id is not None:
        active_client_id = user_client_id
        
    cursor.execute("SELECT name FROM clients WHERE id = ?", (active_client_id,))
    client_row = cursor.fetchone()
    client_name = client_row[0] if client_row else "Client Profile"
    
    # Dropdown selector
    dropdown_options = ""
    for c_id, c_name in all_clients:
        sel = "selected" if c_id == active_client_id else ""
        dropdown_options += f'<option value="{c_id}" {sel}>{c_name}</option>'
        
    # Fetch webhook logs for this client
    cursor.execute("""
        SELECT id, source, event_type, status_code, payload_summary, error_message, created_at 
        FROM webhook_logs 
        WHERE client_id = ? OR client_id IS NULL
        ORDER BY id DESC LIMIT 50
    """, (active_client_id,))
    logs = cursor.fetchall()
    
    # Fetch unmatched records queue
    cursor.execute("""
        SELECT id, record_type, customer_identifier, amount, source_system, reason, status, created_at
        FROM unmatched_records
        WHERE client_id = ? AND status = 'UNMATCHED'
        ORDER BY id DESC
    """, (active_client_id,))
    unmatched_rows = cursor.fetchall()
    
    conn.close()
    
    # Calculate health KPIs
    total_logs = len(logs)
    success_logs = sum(1 for l in logs if l[3] == 200)
    failed_logs = total_logs - success_logs
    health_rate = round((success_logs / total_logs * 100), 1) if total_logs > 0 else 100.0
    unmatched_count = len(unmatched_rows)
    
    admin_link_html = ""
    if email in ADMIN_EMAILS:
        admin_link_html = ' | <a href="/dashboard/users" style="color: #2e7d32; text-decoration: none; font-weight: bold; margin-left: 5px;">🛡️ Admin Directory</a>'
        
    user_header_bar = f"""
    <div style="display: flex; justify-content: space-between; align-items: center; background-color: #f1f3f4; padding: 10px 15px; border-radius: 6px; margin-bottom: 20px; font-size: 13px;">
        <div>
            <span style="color: #666; font-weight: bold;">👤 Active Session:</span> <span style="font-weight: bold; color: #1a237e;">{email}</span>
            {admin_link_html}
        </div>
        <a href="/logout" style="color: #c62828; text-decoration: none; font-weight: bold; display: flex; align-items: center; gap: 4px;">🚪 Log Out</a>
    </div>
    """
    
    # Render Webhook Logs Rows
    log_rows_html = ""
    if not logs:
        log_rows_html = '<tr><td colspan="5" style="text-align: center; color: #888; padding: 20px;">No webhook events logged yet for this client account.</td></tr>'
    else:
        for log_id, source, event_type, status_code, payload_summary, error_msg, created_at in logs:
            badge_color = "#2e7d32" if status_code == 200 else ("#f57c00" if status_code == 422 else "#c62828")
            badge_text = "200 OK" if status_code == 200 else (f"{status_code} Unmatched" if status_code == 422 else f"{status_code} Error")
            
            err_html = f'<div style="color: #c62828; font-size: 11px; margin-top: 4px;">⚠️ {error_msg}</div>' if error_msg else ''
            
            log_rows_html += f"""
            <tr style="border-bottom: 1px solid #eee;">
                <td style="padding: 12px; font-weight: bold; color: #555;">{created_at}</td>
                <td style="padding: 12px;"><span style="background: #e8eaf6; color: #1a237e; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 11px;">{source}</span></td>
                <td style="padding: 12px; font-weight: 600;">{event_type}</td>
                <td style="padding: 12px;"><span style="background: {badge_color}; color: white; padding: 3px 8px; border-radius: 12px; font-weight: bold; font-size: 11px;">{badge_text}</span></td>
                <td style="padding: 12px; font-size: 12px; color: #333;">
                    {payload_summary}
                    {err_html}
                </td>
            </tr>
            """
            
    # Render Unmatched Queue Rows
    unmatched_rows_html = ""
    if not unmatched_rows:
        unmatched_rows_html = '<tr><td colspan="6" style="text-align: center; color: #2e7d32; padding: 20px; font-weight: bold;">🎉 Clean Queue! All closed sales and leads successfully paired to click sessions.</td></tr>'
    else:
        for u_id, rec_type, cust_id, amount, source_sys, reason, status, created_at in unmatched_rows:
            unmatched_rows_html += f"""
            <tr style="border-bottom: 1px solid #eee; background-color: #fffde7;">
                <td style="padding: 12px; font-weight: bold; color: #555;">{created_at}</td>
                <td style="padding: 12px; font-weight: bold; color: #1a237e;">{cust_id}</td>
                <td style="padding: 12px; font-weight: bold; color: #2e7d32;">${amount:,.2f}</td>
                <td style="padding: 12px; font-size: 12px;"><span style="background: #e0f2f1; color: #00695c; padding: 3px 8px; border-radius: 4px; font-weight: bold;">{source_sys}</span></td>
                <td style="padding: 12px; font-size: 11px; color: #c62828; font-weight: 600;">⚠️ {reason}</td>
                <td style="padding: 12px; text-align: center;">
                    <form action="/dashboard/health/resolve-unmatched" method="POST" style="margin: 0;">
                        <input type="hidden" name="record_id" value="{u_id}">
                        <input type="hidden" name="client_id" value="{active_client_id}">
                        <button type="submit" style="background-color: #1a237e; color: white; border: none; padding: 6px 12px; border-radius: 4px; font-weight: bold; font-size: 11px; cursor: pointer;">🔗 Resolve / Link Session</button>
                    </form>
                </td>
            </tr>
            """

    return f"""
    <!DOCTYPE html>
    <html>
        <head>
            <title>Webhook &amp; Sync Diagnostics 🩺</title>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                * {{ box-sizing: border-box; }} body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif; background-color: #f4f6f9; color: #333; margin: 0; padding: 20px; }}
                .container {{ max-width: 1100px; margin: 20px auto; background: white; padding: 30px; border-radius: 12px; box-shadow: 0px 4px 15px rgba(0,0,0,0.05); }}
                header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #eaeaea; padding-bottom: 20px; margin-bottom: 25px; flex-wrap: wrap; gap: 15px; }}
                h1 {{ margin: 0; color: #1a237e; font-size: 24px; }}
                
                .btn-nav {{ display: inline-block; background-color: #1a237e; color: white !important; padding: 10px 18px; text-decoration: none; border-radius: 6px; font-weight: bold; font-size: 14px; transition: background 0.2s; border: none; cursor: pointer; text-align: center; }}
                .btn-nav:hover {{ background-color: #0d1b2a; }}
                
                .kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 15px; margin-bottom: 30px; }}
                .kpi-card {{ background: #fafafa; border: 1px solid #e0e0e0; padding: 18px; border-radius: 10px; box-shadow: 0 2px 6px rgba(0,0,0,0.02); border-top: 4px solid #1a237e; }}
                .kpi-val {{ font-size: 24px; font-weight: 800; color: #1a237e; margin-top: 5px; }}
                .kpi-lbl {{ font-size: 11px; font-weight: 700; text-transform: uppercase; color: #666; letter-spacing: 0.5px; }}
                
                .section-card {{ background: white; border: 1px solid #e0e0e0; border-radius: 10px; padding: 20px; margin-bottom: 25px; box-shadow: 0 2px 8px rgba(0,0,0,0.03); }}
                .section-header {{ font-size: 16px; font-weight: bold; color: #1a237e; margin-bottom: 15px; display: flex; align-items: center; justify-content: space-between; }}
                
                table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
                th {{ background-color: #f1f3f4; color: #1a237e; font-weight: bold; text-align: left; padding: 12px; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 2px solid #e0e0e0; }}
            </style>
        </head>
        <body>
            <div class="container">
                {user_header_bar}
                <header>
                    <div>
                        <h1>Webhook &amp; Sync Diagnostics 🩺</h1>
                        <p style="margin: 5px 0 0 0; color: #666; font-size: 14px;">Live integration payload stream, discrepancy queue, and identity matching status for {client_name}.</p>
                    </div>
                    
                    <div style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap;">
                        <a href="/dashboard?client_id={active_client_id}" class="btn-nav">⬅️ Dashboard</a>
                        <a href="/dashboard/reports?client_id={active_client_id}" class="btn-nav" style="background-color: #2e7d32;">📈 Reports</a>
                        <a href="/dashboard/settings?client_id={active_client_id}" class="btn-nav" style="background-color: #00838f;">⚙️ Settings</a>
                        
                        <div style="background: #e8eaf6; padding: 8px 12px; border-radius: 6px; border: 1px solid #c5cae9; display: flex; align-items: center; gap: 8px;">
                            <span style="font-weight: bold; color: #1a237e; font-size: 13px;">Client:</span>
                            <select onchange="window.location.href='/dashboard/health?client_id='+this.value" style="padding: 6px 10px; border-radius: 4px; border: 1px solid #9fa8da; font-weight: bold; color: #1a237e; cursor: pointer;">
                                {dropdown_options}
                            </select>
                        </div>
                    </div>
                </header>

                <!-- Health KPIs -->
                <div class="kpi-grid">
                    <div class="kpi-card" style="border-top-color: #2e7d32;">
                        <div class="kpi-lbl">⚡ Webhook Health Rate</div>
                        <div class="kpi-val" style="color: #2e7d32;">{health_rate}%</div>
                        <div style="font-size: 11px; color: #666; margin-top: 4px;">{success_logs} / {total_logs} Payloads Operational</div>
                    </div>
                    
                    <div class="kpi-card" style="border-top-color: #1a237e;">
                        <div class="kpi-lbl">📥 24h Webhook Ingest Volume</div>
                        <div class="kpi-val">{total_logs}</div>
                        <div style="font-size: 11px; color: #666; margin-top: 4px;">CallRail, Web Forms, CRM &amp; Billing</div>
                    </div>

                    <div class="kpi-card" style="border-top-color: #f57c00;">
                        <div class="kpi-lbl">⚠️ Unmatched Sales Queue</div>
                        <div class="kpi-val" style="color: #f57c00;">{unmatched_count}</div>
                        <div style="font-size: 11px; color: #666; margin-top: 4px;">Deals Awaiting Session Link</div>
                    </div>

                    <div class="kpi-card" style="border-top-color: #00838f;">
                        <div class="kpi-lbl">🚀 Smart Bidding ROAS Feedback</div>
                        <div class="kpi-val" style="color: #00838f; font-size: 20px;">Active &amp; Healthy</div>
                        <div style="font-size: 11px; color: #666; margin-top: 4px;">Google Ads &amp; Meta Conversion Uploads</div>
                    </div>
                </div>

                <!-- Section 1: Unmatched Queue -->
                <div class="section-card" style="border-left: 4px solid #f57c00;">
                    <div class="section-header">
                        <span>⚠️ Unmatched Sales &amp; Attribution Discrepancy Queue ({unmatched_count})</span>
                        <span style="font-size: 12px; font-weight: normal; color: #666;">Closed deals requiring identity stitching to original click sessions</span>
                    </div>
                    
                    <table>
                        <thead>
                            <tr>
                                <th>Timestamp</th>
                                <th>Customer Identifier</th>
                                <th>Deal Amount</th>
                                <th>Source System</th>
                                <th>Discrepancy Reason</th>
                                <th style="text-align: center;">Action</th>
                            </tr>
                        </thead>
                        <tbody>
                            {unmatched_rows_html}
                        </tbody>
                    </table>
                </div>

                <!-- Section 2: Live Webhook Stream -->
                <div class="section-card">
                    <div class="section-header">
                        <span>📥 Live Webhook Delivery &amp; Payload Log (Last 50 Events)</span>
                        <span style="font-size: 12px; font-weight: normal; color: #666;">Real-time status stream across CallRail, CRMs, Web Forms, and Billing</span>
                    </div>
                    
                    <table>
                        <thead>
                            <tr>
                                <th>Timestamp</th>
                                <th>Integration Source</th>
                                <th>Event Trigger</th>
                                <th>HTTP Status</th>
                                <th>Payload Summary &amp; Diagnostic Details</th>
                            </tr>
                        </thead>
                        <tbody>
                            {log_rows_html}
                        </tbody>
                    </table>
                </div>

            </div>
        </body>
    </html>
    """

@router.post("/dashboard/health/resolve-unmatched")
def resolve_unmatched_record(request: Request, record_id: int = Form(...), client_id: int = Form(...)):
    email = is_authenticated(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)
        
    conn = db_router.connect()
    cursor = conn.cursor()
    cursor.execute("UPDATE unmatched_records SET status = 'RESOLVED' WHERE id = ?", (record_id,))
    conn.commit()
    conn.close()
    
    return RedirectResponse(url=f"/dashboard/health?client_id={client_id}", status_code=303)

