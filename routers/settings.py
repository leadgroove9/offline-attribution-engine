import os
import sqlite3
import re
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from typing import Optional
from database.connection import db_router
from services.auth_service import is_authenticated, get_user_role_and_client
from models.schemas import ClientCreate, ClientUpdate, UserInvite, UserRoleUpdate, UserDelete, InviteRoleUpdate, InviteDelete
from config import CRITERIA_MAP, SOT_MAP, ADMIN_EMAILS

router = APIRouter()

@router.get("/dashboard/settings", response_class=HTMLResponse)
def view_settings(request: Request, client_id: Optional[int] = None):
    email = is_authenticated(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)
    """Page to manage and update client account configuration settings."""
    user_role, user_client_id = get_user_role_and_client(email)
    is_readonly = (user_role == "read")
        
    if user_client_id is not None:
        if client_id is not None and client_id != user_client_id:
            raise HTTPException(status_code=403, detail="Unauthorized: You do not have permission to access settings for this client account.")
        client_id = user_client_id

    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        
        # 1. Fetch All Available Clients for the dropdown selector
        cursor.execute("SELECT id, name, google_ads_customer_id FROM clients ORDER BY name ASC")
        all_clients = cursor.fetchall()
        
        if not all_clients:
            conn.close()
            return HTMLResponse("<script>alert('No clients found. Please onboard a client first!'); window.location.href='/dashboard/add-client';</script>")
            
        # Determine which client to edit
        active_client_id = client_id if client_id is not None else all_clients[0][0]
        if user_client_id is not None:
            active_client_id = user_client_id
        
        # Extract column names dynamically first to guarantee matching order during SELECT
        cursor.execute("PRAGMA table_info(clients)")
        cols = [col[1] for col in cursor.fetchall()]
        
        # 2. Fetch the specific client's settings using explicit columns order (avoids PostgreSQL zip misalignment)
        cols_formatted = ", ".join([f'"{c}"' for c in cols])
        cursor.execute(f"SELECT {cols_formatted} FROM clients WHERE id = ?", (active_client_id,))
        client_row = cursor.fetchone()
        
        if not client_row:
            conn.close()
            raise HTTPException(status_code=404, detail="Client not found")
            
        client_data = dict(zip(cols, client_row))
        client_name = client_data.get("name", "Client Profile")
        
        # Prepare dynamic visual sales funnel overlay labels
        prompt_raw = str(client_data.get("prompt", "") or "").strip()
        if not prompt_raw:
            prompt_text = "Standard Criteria: Inquiring about core services, requesting a quote, or scheduling an appointment"
        elif len(prompt_raw) > 90:
            prompt_text = prompt_raw[:87] + "..."
        else:
            prompt_text = prompt_raw

        # Extract variables for dynamic stage cards & funnel overlays
        lg_method = str(client_data.get("lead_gen_method", "both") or "both").lower()
        prov = str(client_data.get("call_tracking_provider", "callrail") or "callrail").lower()
        if prov in ["ctm", "calltrackingmetrics"]:
            provider_display = "CallTrackingMetrics"
        elif prov in ["wc", "whatconverts"]:
            provider_display = "WhatConverts"
        else:
            provider_display = "CallRail"
            
        sot = str(client_data.get("source_of_truth", "manual") or "manual").lower()

        # Detect active connected ad network platforms for funnel spout
        active_ad_platforms = []
        if str(client_data.get("google_ads_customer_id", "") or "").strip():
            active_ad_platforms.append("Google Ads")
        if str(client_data.get("facebook_ads_id", "") or "").strip():
            active_ad_platforms.append("Meta CAPI")
        if str(client_data.get("microsoft_ads_id", "") or "").strip():
            active_ad_platforms.append("Bing Ads")
        if str(client_data.get("linkedin_ads_id", "") or "").strip():
            active_ad_platforms.append("LinkedIn Ads")
        if str(client_data.get("tiktok_ads_id", "") or "").strip():
            active_ad_platforms.append("TikTok Ads")
        if str(client_data.get("twitter_ads_id", "") or "").strip():
            active_ad_platforms.append("X (Twitter) Ads")
        if str(client_data.get("pinterest_ads_id", "") or "").strip():
            active_ad_platforms.append("Pinterest Ads")
        if str(client_data.get("snapchat_ads_id", "") or "").strip():
            active_ad_platforms.append("Snapchat Ads")
        if str(client_data.get("chatgpt_ads_id", "") or "").strip():
            active_ad_platforms.append("ChatGPT Ads")
        if str(client_data.get("reddit_ads_id", "") or "").strip():
            active_ad_platforms.append("Reddit Ads")

        if active_ad_platforms:
            if len(active_ad_platforms) == 1:
                ad_platforms_display = f"{active_ad_platforms[0]} Conversion Uploads"
            elif len(active_ad_platforms) == 2:
                ad_platforms_display = f"{active_ad_platforms[0]} & {active_ad_platforms[1]} Conversion Uploads"
            else:
                ad_platforms_display = ", ".join(active_ad_platforms[:-1]) + f" & {active_ad_platforms[-1]} Conversion Uploads"
        else:
            ad_platforms_display = "Google Ads, Meta CAPI & Bing Conversion Uploads"

        # Stage 1-4 Card dynamic variables
        stage1_trigger_display = "Call Completed / Form POST"
        if lg_method == "phone":
            stage1_trigger_display = "Call Completed Webhook"
        elif lg_method == "form":
            stage1_trigger_display = "Form POST Webhook"

        sot_display_card = "Manual CSV / Spreadsheet Upload"
        if sot in ["hubspot", "salesforce", "zoho", "servicetitan", "housecallpro", "gohighlevel", "pipedrive"]:
            sot_display_card = f"{sot.upper()} CRM Webhook Pipeline"
        elif sot == "email":
            sot_display_card = "Automated Email Sales Scanner"
        elif sot in ["quickbooks", "xero", "zoho_books", "netsuite", "sage", "freshbooks"]:
            sot_display_card = f"{sot.capitalize()} Integration Webhook"

        lg_method = str(client_data.get("lead_gen_method", "both") or "both").lower()
        prov = str(client_data.get("call_tracking_provider", "callrail") or "callrail").lower()
        if prov in ["ctm", "calltrackingmetrics"]:
            provider_display = "CallTrackingMetrics"
        elif prov in ["wc", "whatconverts"]:
            provider_display = "WhatConverts"
        else:
            provider_display = "CallRail"

        if lg_method == "phone":
            tier1_source_text = f'Source: <strong id="funnel-tier1-provider">{provider_display} Call Tracking Only</strong>'
        elif lg_method == "form":
            tier1_source_text = f'Source: <strong id="funnel-tier1-provider">{provider_display} Webhook Forms Only</strong>'
        else:
            tier1_source_text = f'Source: <strong id="funnel-tier1-provider">{provider_display} Call Tracking</strong> + <strong>{provider_display} Webhook Forms</strong>'
            
        sot = str(client_data.get("source_of_truth", "manual") or "manual").lower()
        
        # Dynamic Layer a) Qualified Leads overlay based on selected Single Source of Truth
        if sot in ["hubspot", "salesforce", "zoho", "servicetitan", "housecallpro", "gohighlevel", "pipedrive"]:
            sot_label = SOT_MAP.get(sot, sot.upper())
            qual_overlay_heading = f"Qualified Leads ({sot_label} Stage Sync)"
            qual_rule_text = f"Configured Lead Rule: <strong>\"Syncs qualified lead tags & deal stage transitions from {sot_label} Webhook\"</strong>"
        elif sot == "email":
            qual_overlay_heading = "Qualified Leads (Email Sales & Lead Scanner)"
            qual_rule_text = "Configured Lead Rule: <strong>\"Parses incoming lead notification emails & automated qualification reports\"</strong>"
        elif sot in ["quickbooks", "xero", "zoho_books", "netsuite", "sage", "freshbooks"]:
            sot_label = SOT_MAP.get(sot, sot.upper())
            qual_overlay_heading = f"Qualified Leads ({sot_label} Integration)"
            qual_rule_text = f"Configured Lead Rule: <strong>\"Qualifies leads upon initial invoice creation or customer onboarding in {sot_label}\"</strong>"
        elif sot in ["google_sheets", "zapier"]:
            sot_label = SOT_MAP.get(sot, sot.upper())
            qual_overlay_heading = f"Qualified Leads ({sot_label} Feed)"
            qual_rule_text = f"Configured Lead Rule: <strong>\"Qualifies leads matching custom status rows in {sot_label}\"</strong>"
        else:
            qual_overlay_heading = f"Qualified Leads ({provider_display} Transcripts & Forms)"
            qual_rule_text = f"Configured AI Audit Rule: <strong>\"{prompt_text}\"</strong>"
            
        # Dynamic Layer b) Won Deals overlay based on selected Single Source of Truth
        # Dynamic SOT Labels and Value Field
        if sot == "google_sheets":
            deal_tags_label_text = "Which tags/statuses on Google sheet signify a qualified lead conversion?"
            won_tags_label_text = "Which tags/statuses on Google sheet signify a won deal conversion?"
            value_box_display_style = "block"
        elif sot in ["quickbooks", "xero", "zoho_books", "netsuite", "sage", "freshbooks", "zapier"]:
            deal_tags_label_text = "Which invoice/payment statuses signify a qualified conversion?"
            won_tags_label_text = "Which invoice/payment statuses signify a won deal conversion?"
            value_box_display_style = "none"
        else:
            deal_tags_label_text = "Which tags/statuses under Deals signify a qualified conversion?"
            won_tags_label_text = "Which tags/statuses under Deals signify a won deal conversion?"
            value_box_display_style = "none"

        sales_source = client_data.get("sales_source", "manual") or "manual"
        if sales_source == "crm" or sot in ["hubspot", "salesforce", "zoho", "servicetitan", "housecallpro", "gohighlevel", "pipedrive"]:
            won_overlay_heading = "Won Deals & Closed Revenue (Live CRM Webhook Pipeline)"
            won_source_text = "Source: <strong>Live CRM Webhooks (HubSpot / Salesforce / Zoho / ServiceTitan)</strong> → Match Method: <strong>Phone & Email Session Pair</strong>"
        elif sales_source == "email" or sot == "email":
            won_overlay_heading = "Won Deals & Closed Revenue (Automated Email Sales Log Scanner)"
            won_source_text = "Source: <strong>Email Sales Scanner / Order Confirmations</strong> → Match Method: <strong>Phone & Email Session Pair</strong>"
        elif sales_source == "accounting" or sot in ["quickbooks", "xero", "zoho_books", "netsuite", "sage", "freshbooks"]:
            won_overlay_heading = "Won Deals & Closed Revenue (QuickBooks / Xero Invoices)"
            won_source_text = "Source: <strong>Accounting Webhooks (QuickBooks / Xero Paid Invoices)</strong> → Match Method: <strong>Phone & Email Session Pair</strong>"
        else:
            won_overlay_heading = "Won Deals & Closed Revenue (Manual CSV / Spreadsheet Upload)"
            won_source_text = "Source: <strong>Manual CSV / Spreadsheet Sales Log Upload</strong> → Match Method: <strong>Phone & Email Session Pair</strong>"

        
        active_provider = client_data.get("call_tracking_provider", "callrail") or "callrail"
        sel_cr = 'selected' if active_provider == 'callrail' else ''
        sel_ctm = 'selected' if active_provider == 'calltrackingmetrics' else ''
        sel_wc = 'selected' if active_provider == 'whatconverts' else ''
        
        p_cr_style = 'display: flex;' if active_provider == 'callrail' else 'display: none;'
        p_ctm_style = 'display: flex;' if active_provider == 'calltrackingmetrics' else 'display: none;'
        p_wc_style = 'display: flex;' if active_provider == 'whatconverts' else 'display: none;'

        if active_provider == "calltrackingmetrics":
            webhook_card_title = " CallTrackingMetrics Transcription Webhook"
            webhook_card_desc = "Paste this dynamic endpoint into CallTrackingMetrics webhook setup to sync automated call recordings and transcripts:"
            webhook_suffix = f"/webhooks/calltrackingmetrics?client_id={active_client_id}"
        elif active_provider == "whatconverts":
            webhook_card_title = " WhatConverts CallCompleted Webhook"
            webhook_card_desc = "Paste this dynamic endpoint into WhatConverts webhook setup to sync automated call recordings and transcripts:"
            webhook_suffix = f"/webhooks/whatconverts?client_id={active_client_id}"
        else:
            webhook_card_title = " CallRail CallCompleted Webhook"
            webhook_card_desc = "Paste this dynamic endpoint into CallRail Integration Settings to sync automated call recordings and transcripts:"
            webhook_suffix = f"/webhooks/callrail?client_id={active_client_id}" 
        
        # Query last 5 analyzed emails for active_client_id
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS analyzed_emails (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER,
                subject TEXT,
                sender TEXT,
                recipient TEXT,
                analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        
        cursor.execute("""
            SELECT subject, datetime(analyzed_at, 'localtime') 
            FROM analyzed_emails 
            WHERE client_id = ? 
            ORDER BY analyzed_at DESC LIMIT 5
        """, (active_client_id,))
        emails_list = cursor.fetchall()
        
        if not emails_list:
            last_emails_html = "<span style='color: #ccc; font-style: italic;'>No emails analyzed yet.</span>"
        else:
            items = []
            for sub, ts in emails_list:
                clean_sub = sub if sub else "(No Subject)"
                # Clean up nested f-string issues
                items.append(f"<li style='margin-bottom: 4px; border-bottom: 1px solid rgba(255,255,255,0.1); padding-bottom: 4px;'><strong>{clean_sub}</strong><br><span style='font-size: 9px; color: #aaa;'>{ts}</span></li>")
            last_emails_html = f"<ul style='margin: 5px 0 0 0; padding-left: 15px; text-align: left; list-style-type: disc;'>{''.join(items)}</ul>"

        # Query last 5 received CRM webhooks for active_client_id
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
        conn.commit()
        
        cursor.execute("""
            SELECT contact_name, stage, amount, datetime(received_at, 'localtime') 
            FROM crm_webhook_logs 
            WHERE client_id = ? 
            ORDER BY received_at DESC LIMIT 5
        """, (active_client_id,))
        crm_logs_list = cursor.fetchall()
        
        if not crm_logs_list:
            last_crm_logs_html = "<span style='color: #ccc; font-style: italic;'>No CRM webhooks received yet.</span>"
        else:
            items = []
            for name, stg, amt, ts in crm_logs_list:
                clean_name = name if name else "Unknown Deal/Contact"
                clean_stage = stg if stg else "Updated"
                clean_amt = f"${amt:,.2f}" if amt else "$0.00"
                items.append(f"<li style='margin-bottom: 4px; border-bottom: 1px solid rgba(255,255,255,0.1); padding-bottom: 4px;'><strong>{clean_name}</strong> ({clean_stage})<br><span style='font-size: 9px; color: #aaa;'>Value: {clean_amt} | {ts}</span></li>")
            last_crm_logs_html = f"<ul style='margin: 5px 0 0 0; padding-left: 15px; text-align: left; list-style-type: disc;'>{''.join(items)}</ul>"

        # Query last 5 received Billing webhooks for active_client_id
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
        conn.commit()
        
        cursor.execute("""
            SELECT customer_name, invoice_number, amount, datetime(received_at, 'localtime') 
            FROM billing_webhook_logs 
            WHERE client_id = ? 
            ORDER BY received_at DESC LIMIT 5
        """, (active_client_id,))
        billing_logs_list = cursor.fetchall()
        
        if not billing_logs_list:
            last_billing_logs_html = "<span style='color: #ccc; font-style: italic;'>No billing webhooks received yet.</span>"
        else:
            items = []
            for name, inv, amt, ts in billing_logs_list:
                clean_name = name if name else "Unknown Customer"
                clean_inv = f"Inv #{inv}" if inv else "Invoice"
                clean_amt = f"${amt:,.2f}" if amt else "$0.00"
                items.append(f"<li style='margin-bottom: 4px; border-bottom: 1px solid rgba(255,255,255,0.1); padding-bottom: 4px;'><strong>{clean_name}</strong> ({clean_inv})<br><span style='font-size: 9px; color: #aaa;'>Paid: {clean_amt} | {ts}</span></li>")
            last_billing_logs_html = f"<ul style='margin: 5px 0 0 0; padding-left: 15px; text-align: left; list-style-type: disc;'>{''.join(items)}</ul>"

        # Query exclusions count for current client
        cursor.execute("SELECT COUNT(*) FROM excluded_customers WHERE client_id = ?", (active_client_id,))
        exclusion_count = cursor.fetchone()[0]
        
        # Query configuration change history for active_client_id
        cursor.execute("""
            SELECT feature_name, old_value, new_value, datetime(changed_at, 'localtime'), changed_by
            FROM client_config_history
            WHERE client_id = ?
            ORDER BY changed_at DESC
        """, (active_client_id,))
        history_list = cursor.fetchall()
        
        change_history_rows_html = ""
        for feat, old_val, new_val, ts, changed_by in history_list:
            old_display = f'<span style="color: #777; font-family: monospace;">{old_val}</span>' if old_val else '<span style="color: #bbb; font-style: italic;">(empty)</span>'
            new_display = f'<strong style="color: #1a237e; font-family: monospace;">{new_val}</strong>' if new_val else '<span style="color: #bbb; font-style: italic;">(empty)</span>'
            change_history_rows_html += f"""
            <tr style="border-bottom: 1px solid #eee;">
                <td style="padding: 10px;"><small>{ts}</small></td>
                <td style="padding: 10px;"><strong>{feat}</strong></td>
                <td style="padding: 10px;">{old_display}</td>
                <td style="padding: 10px;">{new_display}</td>
                <td style="padding: 10px;"><code>{changed_by}</code></td>
            </tr>
            """
            
        if not change_history_rows_html:
            change_history_rows_html = '<tr><td colspan="5" style="text-align: center; color: #888; padding: 40px; font-style: italic;">No configuration changes recorded for this client yet.</td></tr>'
        
        # Query collaborators (users associated with this client_id)
        cursor.execute("SELECT email, role FROM users WHERE client_id = ? ORDER BY email ASC", (active_client_id,))
        users_list = cursor.fetchall()
        
        # Query active pending invitations for this client_id
        cursor.execute("SELECT email, role, token FROM user_invitations WHERE client_id = ? AND is_used = 'NO' ORDER BY created_at DESC", (active_client_id,))
        invites_list = cursor.fetchall()
        
        conn.close()
    except Exception as e:
        return f"<html><body><h3>❌ Database Error: {e}</h3></body></html>"

    # Generate collaborators HTML rows
    collaborators_list = []
    for u_email, u_role in users_list:
        role_desc = "Full Function (Manager)" if u_role == "full" else "Read-Only (Viewer)"
        role_badge = f'<span style="background: #e8f5e9; color: #2e7d32; padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 11px; border: 1px solid #c8e6c9;">{role_desc}</span>'
        status_badge = '<span style="color: #2e7d32; font-weight: bold;">● Active Member</span>'
        
        # Build action controls
        if is_readonly:
            action_html = '<span style="color: #999; font-style: italic;">Read-Only</span>'
        elif u_email.strip().lower() == email.strip().lower():
            action_html = '<span style="color: #1a237e; font-weight: bold; font-size: 11px;">Current Session</span>'
        else:
            sel_full = 'selected' if u_role == 'full' else ''
            sel_read = 'selected' if u_role == 'read' else ''
            action_html = f"""
            <select onchange="updateCollaboratorRole('{u_email}', this.value)" style="padding: 4px 8px; font-size: 11px; border-radius: 4px; border: 1px solid #ced4da; background: white; font-weight: bold; cursor: pointer; display: inline-block; vertical-align: middle; width: auto;">
                <option value="full" {sel_full}>Full Function</option>
                <option value="read" {sel_read}>Read-Only</option>
            </select>
            <button type="button" onclick="deleteCollaborator('{u_email}')" style="background-color: #c62828; color: white; padding: 5px 10px; border: none; border-radius: 4px; font-size: 11px; font-weight: bold; cursor: pointer; margin-left: 8px; display: inline-block; vertical-align: middle; transition: background 0.2s; border-color: #c62828;">Delete</button>
            """
            
        collaborators_list.append(f"""
        <tr style="border-bottom: 1px solid #eaeaea;">
            <td style="padding: 12px 15px;"><code>{u_email}</code></td>
            <td style="padding: 12px 15px;">{role_badge}</td>
            <td style="padding: 12px 15px;">{status_badge}</td>
            <td style="padding: 12px 15px;">{action_html}</td>
        </tr>
        """)
        
    for i_email, i_role, i_token in invites_list:
        role_desc = "Full Function (Manager)" if i_role == "full" else "Read-Only (Viewer)"
        role_badge = f'<span style="background: #fff3cd; color: #856404; padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 11px; border: 1px solid #ffe0b2;">{role_desc}</span>'
        status_badge = f'<span style="color: #ff9100; font-weight: bold;">⏳ Pending Invite</span><br><span style="font-size: 10px; color: #666; font-family: monospace;">token: {i_token[:8]}...</span>'
        
        # Build action controls
        if is_readonly:
            action_html = '<span style="color: #999; font-style: italic;">Read-Only</span>'
        else:
            sel_full = 'selected' if i_role == 'full' else ''
            sel_read = 'selected' if i_role == 'read' else ''
            action_html = f"""
            <select onchange="updateInviteRole('{i_token}', this.value)" style="padding: 4px 8px; font-size: 11px; border-radius: 4px; border: 1px solid #ced4da; background: white; font-weight: bold; cursor: pointer; display: inline-block; vertical-align: middle; width: auto;">
                <option value="full" {sel_full}>Full Function</option>
                <option value="read" {sel_read}>Read-Only</option>
            </select>
            <button type="button" onclick="deleteInvite('{i_token}')" style="background-color: #c62828; color: white; padding: 5px 10px; border: none; border-radius: 4px; font-size: 11px; font-weight: bold; cursor: pointer; margin-left: 8px; display: inline-block; vertical-align: middle; transition: background 0.2s; border-color: #c62828;">Cancel Invite</button>
            """
            
        collaborators_list.append(f"""
        <tr style="border-bottom: 1px solid #eaeaea;">
            <td style="padding: 12px 15px;"><code>{i_email}</code></td>
            <td style="padding: 12px 15px;">{role_badge}</td>
            <td style="padding: 12px 15px;">{status_badge}</td>
            <td style="padding: 12px 15px;">{action_html}</td>
        </tr>
        """)
        
    if not collaborators_list:
        collaborator_rows_html = '<tr><td colspan="4" style="text-align: center; color: #888; padding: 20px;">No additional collaborators registered yet.</td></tr>'
    else:
        collaborator_rows_html = "".join(collaborators_list)

    # Determine invite form visibility
    invite_form_display = "block" if user_role == "full" else "none"
    
    # Configure read-only form elements mapping
    if is_readonly:
        save_btn_html = "<div style=\"background-color: #fff3cd; color: #856404; padding: 15px; border-radius: 6px; font-weight: bold; font-size: 13px; border: 1px solid #ffeeba; display: flex; align-items: center; gap: 8px;\"> Read-Only: You have view-only access to the configuration section.</div>"
        fieldset_disabled_attr = "disabled"
        invite_form_display = "none"
    else:
        save_btn_html = "<button type=\"submit\" id=\"btn-settings-submit\" class=\"btn-submit\"> Save Configuration Changes</button>"
        fieldset_disabled_attr = "" 

    # Generate the Selector Dropdown Options for the Settings Header
    dropdown_options = ""
    if user_client_id is not None:
        restricted_clients = [c for c in all_clients if c[0] == user_client_id]
        for c_id, c_name, c_ads in restricted_clients:
            dropdown_options += f'<option value="{c_id}" selected> {c_name} (Ads: {c_ads})</option>'
    else:
        for c_id, c_name, c_ads in all_clients:
            is_selected = "selected" if active_client_id == c_id else ""
            dropdown_options += f'<option value="{c_id}" {is_selected}> {c_name} (Ads: {c_ads})</option>'

    # Handle dropdown lists with pre-selected options
    lead_gen_both_checked = "checked" if client_data.get("lead_gen_method") == "both" else ""
    lead_gen_phone_checked = "checked" if client_data.get("lead_gen_method") == "phone" else ""
    lead_gen_form_checked = "checked" if client_data.get("lead_gen_method") == "form" else ""

    lead_count_all_checked = "checked" if client_data.get("lead_count_rule") == "all" else ""
    lead_count_max_checked = "checked" if client_data.get("lead_count_rule") == "maximum_one" else ""

    exclude_no_checked = "checked" if client_data.get("exclude_past_customers") == "NO" else ""
    exclude_yes_checked = "checked" if client_data.get("exclude_past_customers") == "YES" else ""

    # Qualification criteria dropdown helper
    crit_options = ""
    for code, label in CRITERIA_MAP.items():
        is_sel = "selected" if client_data.get("qualification_criteria") == code else ""
        crit_options += f'<option value="{code}" {is_sel}>Option {code}: {label}</option>'

    # Source of Truth dropdown helper
    sot_options = ""
    for code, label in SOT_MAP.items():
        is_sel = "selected" if client_data.get("source_of_truth") == code else ""
        sot_options += f'<option value="{code}" {is_sel}>{label}</option>'

    # Email provider selector helper
    provider_options = ""
    for code, label in [("gmail", "Google Gmail API"), ("outlook", "Microsoft Outlook 365"), ("custom_imap", "Custom IMAP (Secure Server)")]:
        is_sel = "selected" if client_data.get("email_provider") == code else ""
        provider_options += f'<option value="{code}" {is_sel}>{label}</option>'

    # Setup the live Integration webhook variables to display on the page
    # Since these are loaded in the browser, window.location.origin is perfect!
    client_name = client_data.get("name", "")
    admin_link_html = ""
    if email in ADMIN_EMAILS:
        admin_link_html = ' | <a href="/admin/users" style="color: #2e7d32; text-decoration: none; font-weight: bold; margin-left: 5px;">️ Admin User Directory</a>'
        
    user_header_bar = f"""
    <div style="display: flex; justify-content: space-between; align-items: center; background-color: #f1f3f4; padding: 10px 15px; border-radius: 6px; margin-bottom: 20px; font-size: 13px;">
        <div>
            <span style="color: #666; font-weight: bold;"> Active Session:</span> <span style="font-weight: bold; color: #1a237e;">{email}</span>
            {admin_link_html}
        </div>
        <a href="/logout" style="color: #c62828; text-decoration: none; font-weight: bold; display: flex; align-items: center; gap: 4px;"> Log Out</a>
    </div>
    """

    
    return f"""
    <!DOCTYPE html>
    <html>
        <head>
            <title>Client Settings ⚙️</title>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                * {{ box-sizing: border-box; }} body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif; background-color: #f4f6f9; color: #333; margin: 0; padding: 20px; }}
                .container {{ max-width: 1000px; margin: 20px auto; background: white; padding: 40px; border-radius: 12px; box-shadow: 0px 4px 15px rgba(0,0,0,0.05); }}
                header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #eaeaea; padding-bottom: 20px; margin-bottom: 30px; flex-wrap: wrap; gap: 15px; }}
                h1 {{ margin: 0; color: #1a237e; font-size: 24px; }}
                .client-selector-container {{ display: flex; align-items: center; gap: 10px; background: #e8eaf6; padding: 10px 15px; border-radius: 8px; border: 1px solid #c5cae9; }}
                .client-label {{ font-weight: bold; color: #1a237e; font-size: 14px; }}
                .client-select {{ padding: 8px 12px; font-size: 14px; border-radius: 5px; border: 1px solid #9fa8da; outline: none; font-weight: 600; cursor: pointer; color: #1a237e; }}
                
                /* Layout */
                .settings-layout {{ display: grid; grid-template-columns: 1fr 1fr; gap: 30px; }}
                @media (max-width: 768px) {{ .settings-layout {{ grid-template-columns: 1fr; }} }}
                
                .form-group {{ margin-bottom: 20px; }}
                .form-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }}
                label {{ display: block; font-weight: 600; margin-bottom: 8px; font-size: 13px; color: #495057; }}
                input[type="text"], input[type="email"], select {{ width: 100%; padding: 10px 12px; border-radius: 6px; border: 1px solid #ced4da; box-sizing: border-box; font-size: 14px; outline: none; transition: border-color 0.2s; font-family: inherit; }}
                input[type="text"]:focus, select:focus {{ border-color: #1a237e; }}
                
                .section-title {{ font-size: 16px; color: #1a237e; font-weight: bold; border-bottom: 1px solid #eaeaea; padding-bottom: 8px; margin-bottom: 15px; text-transform: uppercase; letter-spacing: 0.5px; display: flex; align-items: center; gap: 8px; }}
                
                /* Card Radio Styles */
                .card-radio-group {{ display: flex; flex-direction: column; gap: 10px; margin-bottom: 10px; }}
                .card-radio {{ display: flex; align-items: center; padding: 10px 15px; border: 2px solid #e0e0e0; border-radius: 8px; cursor: pointer; transition: all 0.2s; gap: 12px; position: relative; }}
                .card-radio:hover {{ border-color: #b3e5fc; background-color: #f6fbfd; }}
                .card-radio.selected {{ border-color: #1a237e; background-color: #e8eaf6; }}
                .card-radio input[type="radio"] {{ position: absolute; opacity: 0; }}
                .card-radio-label {{ font-size: 13px; font-weight: bold; color: #333; margin: 0; }}
                .card-radio-sub {{ font-size: 11px; color: #666; margin-top: 3px; }}
                
                /* Webhook Card Box */
                .webhook-card {{ background-color: #fafafa; border: 1px solid #eaeaea; border-left: 4px solid #1a237e; padding: 15px; border-radius: 0 6px 6px 0; margin-bottom: 15px; }}
                .webhook-title {{ font-weight: bold; font-size: 13px; color: #1a237e; margin-bottom: 5px; }}
                .webhook-desc {{ font-size: 11px; color: #666; margin-bottom: 10px; line-height: 1.4; }}
                .webhook-input-group {{ display: flex; gap: 8px; }}
                .webhook-input {{ flex: 1; padding: 8px 10px; border: 1px solid #ced4da; border-radius: 5px; font-family: monospace; font-size: 11px; background-color: #fff; outline: none; }}
                
                .btn-copy {{ background-color: #2e7d32; color: white; padding: 6px 12px; border: none; border-radius: 5px; font-weight: bold; font-size: 12px; cursor: pointer; transition: background 0.2s; white-space: nowrap; }}
                .btn-copy:hover {{ background-color: #1b5e20; }}
                
                .btn-back {{ display: inline-block; background-color: #1a237e; color: white !important; padding: 10px 18px; text-decoration: none; border-radius: 6px; font-weight: bold; font-size: 14px; transition: background 0.2s; border: none; cursor: pointer; text-align: center; }}
                .btn-back:hover {{ background-color: #0d1b2a; }}
                .btn-submit {{ background-color: #1a237e; color: white; padding: 12px 24px; border: none; border-radius: 6px; font-weight: bold; font-size: 15px; cursor: pointer; transition: background 0.2s; }}
                .btn-submit:hover {{ background-color: #0d1b2a; }}
                .btn-cancel {{ color: #666; text-decoration: none; font-size: 14px; font-weight: bold; }}
                .btn-cancel:hover {{ color: #333; }}
                
                .alert {{ padding: 12px; border-radius: 6px; margin-bottom: 20px; display: none; font-size: 14px; font-weight: 600; }}
                .alert-error {{ background-color: #ffebee; color: #c62828; border: 1px solid #ffcdd2; }}
                .alert-success {{ background-color: #e8f5e9; color: #2e7d32; border: 1px solid #c8e6c9; }}
                
                .conditional-box {{ background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 15px; margin-top: 15px; display: none; }}
                @keyframes pulseHighlight {{
                    0% {{ transform: scale(1); box-shadow: 0 0 0 0 rgba(46, 125, 50, 0.7); background-color: #2e7d32; }}
                    50% {{ transform: scale(1.04); box-shadow: 0 0 0 12px rgba(46, 125, 50, 0); background-color: #1b5e20; }}
                    100% {{ transform: scale(1); box-shadow: 0 0 0 0 rgba(46, 125, 50, 0); background-color: #2e7d32; }}
                }}
                .btn-pulse-save {{
                    animation: pulseHighlight 2s infinite !important;
                    background-color: #2e7d32 !important;
                    border-color: #1b5e20 !important;
                    color: white !important;
                }}
                
                
                .tab-btn {{ background: none; border: none; padding: 10px 15px; font-size: 13px; font-weight: bold; color: #666; cursor: pointer; border-bottom: 2px solid transparent; }}
                .tab-btn.active {{ color: #1a237e; border-bottom-color: #1a237e; }}
                .btn-modal-close {{ background-color: #1a237e; color: white; border: none; padding: 8px 18px; border-radius: 5px; font-weight: bold; font-size: 13px; cursor: pointer; transition: background 0.2s; }}
                .btn-modal-close:hover {{ background-color: #0d1b2a; }}

                /* Speech Bubble Tooltip Styles */
                .tooltip-icon {{
                    position: relative;
                    display: inline-block;
                    cursor: help;
                    margin-left: 6px;
                    font-size: 14px;
                    vertical-align: middle;
                    color: #1a237e;
                }}
                .tooltip-icon .tooltip-text {{
                    visibility: hidden;
                    width: 320px;
                    background-color: #1a237e;
                    color: #fff;
                    text-align: left;
                    border-radius: 6px;
                    padding: 10px 12px;
                    position: absolute;
                    z-index: 1000;
                    bottom: 125%;
                    left: 50%;
                    margin-left: -160px;
                    opacity: 0;
                    transition: opacity 0.3s;
                    font-size: 11px;
                    line-height: 1.4;
                    font-weight: normal;
                    box-shadow: 0 4px 10px rgba(0,0,0,0.15);
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                }}
                .tooltip-icon .tooltip-text::after {{
                    content: "";
                    position: absolute;
                    top: 100%;
                    left: 50%;
                    margin-left: -5px;
                    border-width: 5px;
                    border-style: solid;
                    border-color: #1a237e transparent transparent transparent;
                }}
                .tooltip-icon:hover .tooltip-text {{
                    visibility: visible;
                    opacity: 1;
                }}

                /* Tooltip styling */
                .tooltip {{
                    position: relative;
                    display: inline-flex;
                    align-items: center;
                    cursor: pointer;
                    margin-left: 5px;
                    color: #1a237e;
                    font-size: 14px;
                    vertical-align: middle;
                }}
                .tooltip .tooltiptext {{
                    visibility: hidden;
                    width: 250px;
                    background-color: #333;
                    color: #fff;
                    text-align: left;
                    border-radius: 6px;
                    padding: 10px;
                    position: absolute;
                    z-index: 100;
                    bottom: 125%; /* Position above the text */
                    left: 50%;
                    margin-left: -125px;
                    opacity: 0;
                    transition: opacity 0.3s;
                    font-size: 11px;
                    font-weight: normal;
                    line-height: 1.4;
                    box-shadow: 0px 4px 10px rgba(0,0,0,0.15);
                    white-space: normal;
                }}
                .tooltip .tooltiptext::after {{
                    content: "";
                    position: absolute;
                    top: 100%; /* At the bottom of the tooltip */
                    left: 50%;
                    margin-left: -5px;
                    border-width: 5px;
                    border-style: solid;
                    border-color: #333 transparent transparent transparent;
                }}
                .tooltip:hover .tooltiptext {{
                    visibility: visible;
                    opacity: 1;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                {user_header_bar}
                <header>
                    <div>
                        <h1>Client Configuration Settings ⚙️</h1>
                        <p style="margin: 5px 0 0 0; color: #666; font-size: 14px;">Update dynamic rules, ad accounts, and system webhooks.</p>
                    </div>
                    
                    <div style="display: flex; align-items: center; gap: 15px; flex-wrap: wrap;">
                        <a href="/dashboard?client_id={active_client_id}" class="btn-back">⬅️ Return to Dashboard</a>
                        <div class="client-selector-container">
                            <span class="client-label">Editing Client:</span>
                            <select class="client-select" onchange="window.location.href='/dashboard/settings?client_id='+this.value">
                                {dropdown_options}
                            </select>
                        </div>
                    </div>
                </header>

            <!-- Google Ads Conversion Menu Style Visual Sales Funnel -->
            <div class="card" style="background: linear-gradient(135deg, #1a237e 0%, #283593 100%); color: white; padding: 25px; border-radius: 12px; box-shadow: 0 4px 15px rgba(26, 35, 126, 0.2); margin-bottom: 30px; position: relative; overflow: hidden;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; border-bottom: 1px solid rgba(255,255,255,0.15); padding-bottom: 15px; flex-wrap: wrap; gap: 10px;">
                    <div>
                        <h2 style="margin: 0; font-size: 18px; color: #ffffff; display: flex; align-items: center; gap: 8px;">
                             Offline Conversion Funnel Architecture
                        </h2>
                        <p style="margin: 4px 0 0 0; font-size: 12px; color: #e8eaf6;">
                            Google Ads Conversion Menu Mapping & Offline Feedback Loop for <strong>{client_name}</strong>
                        </p>
                    </div>
                    <span style="background: rgba(255,255,255,0.15); color: #fff; padding: 4px 12px; border-radius: 20px; font-size: 11px; font-weight: bold; border: 1px solid rgba(255,255,255,0.25);">
                        ⚡ Active Pipeline Sync
                    </span>
                </div>

                <!-- VISUAL SALES FUNNEL DIAGRAM GRAPHIC -->
                <div style="background: rgba(0, 0, 0, 0.25); border-radius: 10px; padding: 22px 20px; margin-bottom: 25px; border: 1px solid rgba(255,255,255,0.15); box-shadow: inset 0 2px 10px rgba(0,0,0,0.2);">
                    <div style="text-align: center; font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: 1px; color: #90caf9; margin-bottom: 15px;">
                         Active Client Sales Funnel Graphic & Tracking Overlays
                    </div>

                    <div style="max-width: 780px; margin: 0 auto; display: flex; flex-direction: column; align-items: center; gap: 6px;">
                        
                        <!-- Funnel Tier 1: All Inbound Clicks & Leads -->
                        <div style="width: 100%; background: linear-gradient(90deg, #1565c0 0%, #1e88e5 100%); padding: 12px 16px; border-radius: 8px 8px 3px 3px; text-align: center; box-shadow: 0 3px 6px rgba(0,0,0,0.2); border: 1px solid rgba(255,255,255,0.2); position: relative;">
                            <div style="font-size: 12px; font-weight: bold; color: #ffffff; display: flex; align-items: center; justify-content: center; gap: 8px; flex-wrap: wrap;">
                                <span> 1. Inbound Leads & Traffic Capture</span>
                                <span style="font-size: 10px; background: rgba(0,0,0,0.25); padding: 2px 8px; border-radius: 10px; color: #e3f2fd;">GCLID / FBCLID / MSCLKID</span>
                            </div>
                            <div id="funnel-tier1-source" style="font-size: 11px; color: #e3f2fd; margin-top: 3px;">
                                {tier1_source_text}
                            </div>
                        </div>

                        <!-- Funnel Arrow 1 -->
                        <div style="color: #90caf9; font-size: 11px; margin: -2px 0;">▼</div>

                        <!-- Funnel Tier 2: Qualified Leads (Overlay Label A) -->
                        <div style="width: 82%; background: linear-gradient(90deg, #00838f 0%, #00acc1 100%); padding: 12px 18px; border-radius: 4px; text-align: center; box-shadow: 0 3px 8px rgba(0,0,0,0.25); border: 1.5px solid #80deea; position: relative;">
                            <div style="position: absolute; top: -10px; left: 15px; background: #004d40; color: #80deea; font-size: 9px; font-weight: 800; padding: 2px 8px; border-radius: 10px; border: 1px solid #80deea; text-transform: uppercase; letter-spacing: 0.5px;">
                                a) QUALIFIED LEADS TRACKING
                            </div>
                            <div id="funnel-qual-heading" style="font-size: 13px; font-weight: bold; color: #ffffff; margin-top: 2px;">
                                 {qual_overlay_heading}
                            </div>
                            <div id="funnel-qual-rule" style="font-size: 11px; color: #e0f7fa; font-style: italic; margin-top: 4px; background: rgba(0,0,0,0.22); padding: 5px 10px; border-radius: 4px; border-left: 3px solid #80deea;">
                                {qual_rule_text}
                            </div>
                        </div>

                        <!-- Funnel Arrow 2 -->
                        <div style="color: #80deea; font-size: 11px; margin: -2px 0;">▼</div>

                        <!-- Funnel Tier 3: Won Deals & Sales (Overlay Label B) -->
                        <div style="width: 64%; background: linear-gradient(90deg, #2e7d32 0%, #43a047 100%); padding: 12px 18px; border-radius: 4px; text-align: center; box-shadow: 0 3px 8px rgba(0,0,0,0.25); border: 1.5px solid #a5d6a7; position: relative;">
                            <div style="position: absolute; top: -10px; left: 15px; background: #1b5e20; color: #a5d6a7; font-size: 9px; font-weight: 800; padding: 2px 8px; border-radius: 10px; border: 1px solid #a5d6a7; text-transform: uppercase; letter-spacing: 0.5px;">
                                b) WON DEALS & REVENUE TRACKING
                            </div>
                            <div id="funnel-won-heading" style="font-size: 13px; font-weight: bold; color: #ffffff; margin-top: 2px;">
                                 {won_overlay_heading}
                            </div>
                            <div id="funnel-won-source" style="font-size: 11px; color: #e8f5e9; font-style: italic; margin-top: 4px; background: rgba(0,0,0,0.22); padding: 5px 10px; border-radius: 4px; border-left: 3px solid #a5d6a7;">
                                {won_source_text}
                            </div>
                        </div>

                        <!-- Funnel Arrow 3 -->
                        <div style="color: #a5d6a7; font-size: 11px; margin: -2px 0;">▼</div>

                        <!-- Funnel Spout: Ad Network Offline Sync -->
                        <div style="width: 48%; min-width: 260px; background: linear-gradient(90deg, #f57f17 0%, #fbc02d 100%); padding: 8px 12px; border-radius: 3px 3px 8px 8px; text-align: center; box-shadow: 0 3px 8px rgba(0,0,0,0.3); border: 1.5px solid #ffe082; color: #000;">
                            <div style="font-size: 11px; font-weight: 900; color: #212121; text-transform: uppercase; letter-spacing: 0.5px;">
                                 Smart Bidding Feedback Loop
                            </div>
                            <div id="funnel-ad-platforms-spout" style="font-size: 10px; color: #37474f; font-weight: bold;">
                                {ad_platforms_display}
                            </div>
                        </div>

                    </div>
                </div>

                <!-- Funnel Pipeline Steps Grid -->
                <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; position: relative;">

                    <!-- Stage 1: Lead Capture -->
                    <div style="background: rgba(255,255,255,0.08); backdrop-filter: blur(10px); border: 1px solid rgba(255,255,255,0.18); border-radius: 10px; padding: 16px; position: relative;">
                        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px;">
                            <span style="font-size: 10px; font-weight: 800; letter-spacing: 0.5px; text-transform: uppercase; color: #90caf9;">Stage 1</span>
                            <span style="font-size: 18px;"></span>
                        </div>
                        <h3 style="margin: 0 0 6px 0; font-size: 13px; font-weight: bold; color: #ffffff;">1. Lead Capture & Click IDs</h3>
                        <p id="stage1-card-desc" style="margin: 0; font-size: 11px; color: #c5cae9; line-height: 1.4;">
                            Tracks inbound leads for <strong>{provider_display}</strong>. Captures <strong>GCLID</strong>, <strong>FBCLID</strong>, <strong>MSCLKID</strong>, and caller contact data.
                        </p>
                        <div style="margin-top: 12px; padding-top: 8px; border-top: 1px dashed rgba(255,255,255,0.15); display: flex; align-items: center; justify-content: space-between; font-size: 10px; color: #bbdefb;">
                            <span>Webhook Trigger:</span>
                            <strong id="stage1-card-trigger" style="color: #fff;">{stage1_trigger_display}</strong>
                        </div>
                    </div>

                    <!-- Stage 2: AI Qualification Audit -->
                    <div style="background: rgba(255,255,255,0.08); backdrop-filter: blur(10px); border: 1px solid rgba(255,255,255,0.18); border-radius: 10px; padding: 16px; position: relative;">
                        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px;">
                            <span style="font-size: 10px; font-weight: 800; letter-spacing: 0.5px; text-transform: uppercase; color: #80deea;">Stage 2</span>
                            <span style="font-size: 18px;"></span>
                        </div>
                        <h3 style="margin: 0 0 6px 0; font-size: 13px; font-weight: bold; color: #ffffff;">2. AI Qualification Audit</h3>
                        <p id="stage2-card-desc" style="margin: 0; font-size: 11px; color: #c5cae9; line-height: 1.4;">
                            Claude 4.5 Haiku evaluates call transcripts &amp; responses against rule: <strong>"{prompt_text}"</strong> to identify qualified leads.
                        </p>
                        <div style="margin-top: 12px; padding-top: 8px; border-top: 1px dashed rgba(255,255,255,0.15); display: flex; align-items: center; justify-content: space-between; font-size: 10px; color: #b2ebf2;">
                            <span>Signal Action:</span>
                            <strong style="color: #fff;">Qualified Lead ($1.00 Value)</strong>
                        </div>
                    </div>

                    <!-- Stage 3: Revenue & Closed Sale Matching -->
                    <div style="background: rgba(255,255,255,0.08); backdrop-filter: blur(10px); border: 1px solid rgba(255,255,255,0.18); border-radius: 10px; padding: 16px; position: relative;">
                        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px;">
                            <span style="font-size: 10px; font-weight: 800; letter-spacing: 0.5px; text-transform: uppercase; color: #a5d6a7;">Stage 3</span>
                            <span style="font-size: 18px;"></span>
                        </div>
                        <h3 style="margin: 0 0 6px 0; font-size: 13px; font-weight: bold; color: #ffffff;">3. Closed Sale & Revenue Match</h3>
                        <p id="stage3-card-desc" style="margin: 0; font-size: 11px; color: #c5cae9; line-height: 1.4;">
                            Matches closed deals &amp; invoice amounts from <strong id="stage3-card-sot">{sot_display_card}</strong> via phone &amp; email fuzzy logic back to click session records.
                        </p>
                        <div style="margin-top: 12px; padding-top: 8px; border-top: 1px dashed rgba(255,255,255,0.15); display: flex; align-items: center; justify-content: space-between; font-size: 10px; color: #c8e6c9;">
                            <span>Matching Method:</span>
                            <strong style="color: #fff;">Phone/Email + Fuzzy Name</strong>
                        </div>
                    </div>

                    <!-- Stage 4: Ad Network Offline Sync -->
                    <div style="background: rgba(255,255,255,0.08); backdrop-filter: blur(10px); border: 1px solid rgba(255,255,255,0.18); border-radius: 10px; padding: 16px; position: relative;">
                        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px;">
                            <span style="font-size: 10px; font-weight: 800; letter-spacing: 0.5px; text-transform: uppercase; color: #ffe082;">Stage 4</span>
                            <span style="font-size: 18px;"></span>
                        </div>
                        <h3 style="margin: 0 0 6px 0; font-size: 13px; font-weight: bold; color: #ffffff;">4. Conversion Upload & Bidding</h3>
                        <p id="stage4-card-desc" style="margin: 0; font-size: 11px; color: #c5cae9; line-height: 1.4;">
                            Pushes audited conversion events &amp; exact revenue values to <strong id="stage4-card-platforms">{ad_platforms_display}</strong> to optimize Smart Bidding ROAS.
                        </p>
                        <div style="margin-top: 12px; padding-top: 8px; border-top: 1px dashed rgba(255,255,255,0.15); display: flex; align-items: center; justify-content: space-between; font-size: 10px; color: #ffecb3;">
                            <span>Destination:</span>
                            <strong style="color: #fff;">Active Ad Conversion Uploads</strong>
                        </div>
                    </div>

                </div>
            </div>


                <div id="alert-box" class="alert"></div>
                
                <form id="settings-form" onsubmit="submitSettings(event)">
                    <fieldset {fieldset_disabled_attr} style="border: 0; padding: 0; margin: 0;">
                    <div class="settings-layout">
                        
                        <!-- LEFT COLUMN: Configurations -->
                        <div>
                            <!-- SECTION 1: Accounts -->
                            <div class="section-title"> Profile & Ad Accounts</div>
                            
                            <div class="form-group">
                                <label for="name">Client Business Name</label>
                                <input type="text" id="name" value="{client_name}" required>
                            </div>
                            
                            <div class="form-group">
                                <label for="call_tracking_provider">Call Tracking Provider</label>
                                <select id="call_tracking_provider" onchange="toggleSettingsCallTrackingFields()" style="width: 100%; padding: 12px; border-radius: 6px; border: 1px solid #ced4da; font-size: 14px; font-weight: 600;">
                                    <option value="callrail" {sel_cr}>CallRail</option>
                                    <option value="calltrackingmetrics" {sel_ctm}>CallTrackingMetrics</option>
                                    <option value="whatconverts" {sel_wc}>WhatConverts</option>
                                </select>
                            </div>
                            
                            <div id="settings_call_tracking_callrail_box" class="form-row" style="{p_cr_style}">
                                <div class="form-group">
                                    <label for="callrail_account_id">CallRail Account ID</label>
                                    <input type="text" id="callrail_account_id" value="{client_data.get('callrail_account_id', '') or ''}" placeholder="e.g. 123456789">
                                </div>
                                <div class="form-group">
                                    <label for="callrail_company_id">CallRail Client ID (Company ID)</label>
                                    <input type="text" id="callrail_company_id" value="{client_data.get('callrail_company_id', '') or ''}" placeholder="e.g. 987654321">
                                </div>
                            </div>

                            <div id="settings_call_tracking_ctm_box" class="form-row" style="{p_ctm_style}">
                                <div class="form-group">
                                    <label for="ctm_account_id">CallTrackingMetrics Account ID</label>
                                    <input type="text" id="ctm_account_id" value="{client_data.get('ctm_account_id', '') or ''}" placeholder="e.g. 12345">
                                </div>
                                <div class="form-group">
                                    <label for="ctm_profile_id">CallTrackingMetrics Client ID (Profile ID)</label>
                                    <input type="text" id="ctm_profile_id" value="{client_data.get('ctm_profile_id', '') or ''}" placeholder="e.g. 67890">
                                </div>
                            </div>

                            <div id="settings_call_tracking_wc_box" class="form-row" style="{p_wc_style}">
                                <div class="form-group">
                                    <label for="wc_account_id">WhatConverts Account ID</label>
                                    <input type="text" id="wc_account_id" value="{client_data.get('wc_account_id', '') or ''}" placeholder="e.g. 11111">
                                </div>
                                <div class="form-group">
                                    <label for="wc_profile_id">WhatConverts Client ID (Profile ID)</label>
                                    <input type="text" id="wc_profile_id" value="{client_data.get('wc_profile_id', '') or ''}" placeholder="e.g. 22222">
                                </div>
                            </div>
                            
                            <div class="form-row">
                                <div class="form-group">
                                    <label for="google_ads_customer_id">Google Ads Customer ID</label>
                                    <input type="text" id="google_ads_customer_id" value="{client_data.get("google_ads_customer_id", "")}">
                                </div>
                                <div class="form-group">
                                    <label for="facebook_ads_id">Facebook Ads Pixel ID</label>
                                    <input type="text" id="facebook_ads_id" value="{client_data.get("facebook_ads_id", "") or ""}">
                                </div>
                            </div>
                            
                            <div class="form-row">
                                <div class="form-group">
                                    <label for="linkedin_ads_id">LinkedIn Ads ID</label>
                                    <input type="text" id="linkedin_ads_id" value="{client_data.get("linkedin_ads_id", "") or ""}">
                                </div>
                                <div class="form-group">
                                    <label for="microsoft_ads_id">Microsoft Ads ID</label>
                                    <input type="text" id="microsoft_ads_id" value="{client_data.get("microsoft_ads_id", "") or ""}">
                                </div>
                            </div>
                            
                            <div class="form-row">
                                <div class="form-group">
                                    <label for="tiktok_ads_id">TikTok Ads Pixel/Account ID</label>
                                    <input type="text" id="tiktok_ads_id" value="{client_data.get("tiktok_ads_id", "") or ""}" placeholder="e.g. tt_pixel_123">
                                </div>
                                <div class="form-group">
                                    <label for="snapchat_ads_id">Snapchat Ads Pixel ID</label>
                                    <input type="text" id="snapchat_ads_id" value="{client_data.get('snapchat_ads_id', '') or ''}" placeholder="e.g. snap_pixel_123">
                                </div>
                                <div class="form-group">
                                    <label for="pinterest_ads_id">Pinterest Ads ID</label>
                                    <input type="text" id="pinterest_ads_id" value="{client_data.get("pinterest_ads_id", "") or ""}" placeholder="e.g. pin_pixel_123">
                                </div>
                                <div class="form-group">
                                    <label for="chatgpt_ads_id">ChatGPT Ads ID</label>
                                    <input type="text" id="chatgpt_ads_id" value="{client_data.get("chatgpt_ads_id", "") or ""}" placeholder="e.g. gpt_pixel_123">
                                </div>
                                <div class="form-group">
                                    <label for="reddit_ads_id">Reddit Ads Account / Pixel ID</label>
                                    <input type="text" id="reddit_ads_id" value="{client_data.get("reddit_ads_id", "") or ""}" placeholder="e.g. rdt_pixel_456">
                                </div>
                            </div>
                            
                            <div class="form-row">
                                <div class="form-group">
                                    <div style="display: flex; align-items: center; gap: 6px;">
                                        <label for="twitter_ads_id" style="margin-bottom: 0;">X (Twitter) Ads Pixel ID</label>
                                        <span class="tooltip-icon">
                                            
                                            <span class="tooltip-text" style="width: 250px;">
                                                ⚠️ <strong>Manual UTM required:</strong> To track X click IDs (twclid) via CallRail/form submissions, you must manually append <code>?twclid={{click_id}}</code> to your X Ad destination URLs.
                                            </span>
                                        </span>
                                    </div>
                                    <input type="text" id="twitter_ads_id" value="{client_data.get("twitter_ads_id", "") or ""}" placeholder="e.g. tw_pixel_123" style="margin-top: 8px;">
                                </div>
                                <div class="form-group" style="visibility: hidden;">
                                    <!-- Spacer -->
                                </div>
                            </div>
                            
                            <!-- SECTION 2: Lead Gen & Qualification -->
                            <div class="section-title"> Lead Generation & AI Auditing</div>
                            
                            <div class="form-group">
                                <label>How do you generate your leads?</label>
                                <div class="card-radio-group">
                                    <div class="card-radio {lead_gen_both_checked and 'selected'}" onclick="selectCardRadio('lead_gen_method', 'both', this)">
                                        <input type="radio" name="lead_gen_method" value="both" {lead_gen_both_checked}>
                                        <div>
                                            <div class="card-radio-label">Both Phone Calls & Web Forms</div>
                                        </div>
                                    </div>
                                    <div class="card-radio {lead_gen_phone_checked and 'selected'}" onclick="selectCardRadio('lead_gen_method', 'phone', this)">
                                        <input type="radio" name="lead_gen_method" value="phone" {lead_gen_phone_checked}>
                                        <div>
                                            <div class="card-radio-label">Phone Calls Only</div>
                                        </div>
                                    </div>
                                    <div class="card-radio {lead_gen_form_checked and 'selected'}" onclick="selectCardRadio('lead_gen_method', 'form', this)">
                                        <input type="radio" name="lead_gen_method" value="form" {lead_gen_form_checked}>
                                        <div>
                                            <div class="card-radio-label">Form Submissions Only</div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                            
                            <div class="form-group">
                                <label for="qualification_criteria" style="display: inline-flex; align-items: center; gap: 5px;">
                                    How do you qualify a lead?
                                    <span class="tooltip">
                                        <span class="tooltiptext">Define a lead stage that is &quot;good enough&quot;, and would be happy with paying for all day long from your Ads. This is the minimum standard the system will go for when optimizing your Ads.</span>
                                    </span>
                                </label>
                                <select id="qualification_criteria">
                                    {crit_options}
                                </select>
                            </div>
                            
                            <!-- SECTION 4: Smart Deduplication -->
                            <div class="section-title"> Smart Conversion Controls</div>
                            
                            <div class="form-group">
                                <label>How should we track multiple leads from the same customer?</label>
                                <div class="card-radio-group">
                                    <div class="card-radio {lead_count_all_checked and 'selected'}" onclick="selectCardRadio('lead_count_rule', 'all', this)">
                                        <input type="radio" name="lead_count_rule" value="all" {lead_count_all_checked}>
                                        <div>
                                            <div class="card-radio-label">Count Every Lead Session</div>
                                        </div>
                                    </div>
                                    <div class="card-radio {lead_count_max_checked and 'selected'}" onclick="selectCardRadio('lead_count_rule', 'maximum_one', this)">
                                        <input type="radio" name="lead_count_rule" value="maximum_one" {lead_count_max_checked}>
                                        <div>
                                            <div class="card-radio-label">Maximum of One Conversion Each</div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                            
                            <div class="form-group">
                                <label>Exclude past customers as eligible lead conversions?</label>
                                <div class="card-radio-group">
                                    <div class="card-radio {exclude_no_checked and 'selected'}" onclick="selectCardRadio('exclude_past_customers', 'NO', this)">
                                        <input type="radio" name="exclude_past_customers" value="NO" {exclude_no_checked}>
                                        <div>
                                            <div class="card-radio-label">No, allow past customers</div>
                                        </div>
                                    </div>
                                    <div class="card-radio {exclude_yes_checked and 'selected'}" onclick="selectCardRadio('exclude_past_customers', 'YES', this)">
                                        <input type="radio" name="exclude_past_customers" value="YES" {exclude_yes_checked}>
                                        <div>
                                            <div class="card-radio-label">Yes, exclude past customers</div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                            
                            <!-- EXCLUSION UPLOAD PANEL -->
                            <p id="existing-exclusions-msg" style="font-size: 11px; color: #1b5e20; font-weight: bold; margin-top: 10px; margin-bottom: 10px; display: {'block' if client_data.get('exclude_past_customers') == 'YES' else 'none'};">
                                ℹ️ Currently ignoring <strong>{exclusion_count}</strong> past customers. Uploading a new list can append or replace this database.
                            </p>
                            <div id="exclusion-upload-box" class="conditional-box" style="display: {'block' if client_data.get('exclude_past_customers') == 'YES' else 'none'}; padding: 15px; margin-top: 10px;">
                                <div class="instructions" style="background-color: #f1f8e9; border-left-color: #2e7d32; color: #2e7d32; margin-bottom: 15px; font-size: 12px; line-height: 1.5; padding: 12px;">
                                     <strong>Upload Past Customers to Ignore:</strong><br>
                                    Upload your list of customers, to use to ignore future conversion triggering. Only one piece of information is needed for each user in order to do this, but more data points for each user is best, for higher match rates. Here is a sample sheet that you can use to fill in, or you can provide your own sheet that have "first name, last name, email, phone number, company name" as the column headers.
                                </div>
                                <div style="display: flex; gap: 10px; align-items: center; margin-bottom: 15px; flex-wrap: wrap;">
                                    <button type="button" onclick="triggerSampleSheetDownload()" class="btn-copy" style="background-color: #1a237e; padding: 8px 12px; font-size: 11px;"> Download Sample Sheet (.CSV)</button>
                                    <a href="/dashboard/export/exclusions?client_id={active_client_id}" id="btn-download-exclusions" class="btn-copy" style="background-color: #607d8b; padding: 8px 12px; font-size: 11px; text-decoration: none; display: {'inline-block' if exclusion_count > 0 else 'none'};"> Download Current Exclusions ({exclusion_count})</a>
                                    <input type="file" id="exclusion-file-input" accept=".csv" onchange="handleExclusionFileUpload(event)" style="display: none;">
                                    <button type="button" onclick="document.getElementById('exclusion-file-input').click()" class="btn-copy" style="background-color: #2e7d32; padding: 8px 12px; font-size: 11px;"> Choose File & Upload (.CSV)</button>
                                </div>
                                <div style="margin-top: 15px; margin-bottom: 15px; background: #fff; padding: 12px; border: 1px solid #e0e0e0; border-radius: 6px;">
                                    <label style="font-weight: bold; font-size: 12px; margin-bottom: 8px; display: block; color: #1a237e;"> Exclusions Upload Strategy:</label>
                                    <div style="display: flex; gap: 20px; align-items: center;">
                                        <label style="font-weight: normal; cursor: pointer; display: flex; align-items: center; gap: 6px; font-size: 12px; margin: 0;">
                                            <input type="radio" name="exclusion_upload_action" value="append" checked style="cursor: pointer;">
                                            <strong>Append new records</strong> (Keep existing ones, only add new customers)
                                        </label>
                                        <label style="font-weight: normal; cursor: pointer; display: flex; align-items: center; gap: 6px; font-size: 12px; margin: 0;">
                                            <input type="radio" name="exclusion_upload_action" value="replace" style="cursor: pointer;">
                                            <strong>Overwrite list</strong> (Wipe existing entries and start fresh)
                                        </label>
                                    </div>
                                </div>

                                <div id="upload-status-box" class="alert alert-success" style="display: none; margin-bottom: 0; font-size: 11px; padding: 10px;"></div>
                            </div>
                            
                            <!-- Real-Time CRM Exclusion sync webhook section -->
                            <div id="settings-exclusion-webhook-box" class="conditional-box" style="display: {'block' if client_data.get('exclude_past_customers') == 'YES' else 'none'}; padding: 15px; margin-top: 15px; border-top: 1px dashed #ccc;">
                                <p style="font-size: 12px; font-weight: bold; color: #1a237e; margin-top: 0; margin-bottom: 5px;">⚡ Real-Time CRM Exclusion Sync Webhook URL</p>
                                <p style="font-size: 11px; color: #666; margin-top: 0; margin-bottom: 10px; line-height: 1.4;">
                                    Connect your CRM (HubSpot, ServiceTitan, Salesforce, Zoho, etc.) directly using Zapier or a native webhook. Set your CRM to send a POST webhook to this URL whenever a customer is added or won. The contact's phone/email will be added to your exclusion filter automatically in real-time!
                                </p>
                                <div class="webhook-input-group">
                                    <input type="text" class="webhook-input" id="exclusion-crm-webhook" readonly value="" data-suffix="/webhooks/exclude-customer?client_id={active_client_id}">
                                    <button type="button" onclick="copyText('exclusion-crm-webhook', 'exclusion-crm-copy-btn')" id="exclusion-crm-copy-btn" class="btn-copy"> Copy</button>
                                </div>
                            </div>

                        </div>
                        
                        <!-- RIGHT COLUMN: Webhooks & SOT Integration -->
                        <div>
                            <!-- SECTION 3: SOT Integration -->
                            <div class="section-title"> Single Source of Truth Settings</div>
                            
                            <div class="form-group">
                                <label for="source_of_truth">Single Source of Truth Platform</label>
                                <select id="source_of_truth" onchange="toggleSOTFields()">
                                    {sot_options}
                                </select>
                            </div>
                            
                            <!-- CONDITIONAL: CRM Deal status tags -->
                            <div id="sot-deal-tags-box" class="conditional-box">
                                <div style="margin-bottom: 15px;">
                                    <label id="sot-deal-tags-label" for="crm_deal_tags">{deal_tags_label_text}</label>
                                    <input type="text" id="crm_deal_tags" value="{client_data.get("crm_deal_tags", "") or ""}" placeholder="e.g. appointment-booked, estimate-approved">
                                </div>
                                <div style="margin-bottom: 15px;">
                                    <label id="sot-won-deal-tags-label" for="crm_won_deal_tags">{won_tags_label_text}</label>
                                    <input type="text" id="crm_won_deal_tags" value="{client_data.get("crm_won_deal_tags", "") or ""}" placeholder="e.g. closed-won, job-completed">
                                </div>
                            </div>
                            
                                                    <div id="sot-hubspot-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                            <div style="background-color: #fff8e1; border-left: 4px solid #ffb300; padding: 15px; border-radius: 4px; color: #5d4037; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                 <strong>HubSpot Private App Quick Setup Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #4e342e;">
                                    <li>Log into HubSpot as a <strong>Super Admin</strong>.</li>
                                    <li>Go to <strong>Settings (Gear Icon) &gt; Integrations &gt; Private Apps</strong>.</li>
                                    <li>Click <strong>Create Private App</strong> and configure basic info.</li>
                                    <li>Under <strong>Scopes</strong>, search <code>CRM</code> and check <code>Read</code> permissions for:
                                        <ul style="list-style-type: disc; padding-left: 15px; margin: 4px 0;">
                                            <li><code>crm.objects.deals.read</code> (to track closed sales &amp; revenue)</li>
                                            <li><code>crm.objects.contacts.read</code> (to sync leads)</li>
                                        </ul>
                                    </li>
                                    <li>Click <strong>Create App</strong>. Click the <strong>Webhooks</strong> tab, click <strong>Edit Webhooks</strong>, paste your dynamic target URL below, and subscribe to <code>propertyChange</code> or <code>creation</code> for <strong>Deals</strong>!</li>
                                </ol>
                                <div style="margin-top: 12px; background: white; padding: 12px; border-radius: 6px; border: 1px solid #ffb300;">
                                    <label style="font-size: 11px; font-weight: bold; color: #5d4037; display: block; margin-bottom: 5px;">⚡ YOUR TARGET WEBHOOK ENDPOINT URL:</label>
                                    <div class="webhook-input-group">
                                        <input type="text" class="webhook-input" id="sot-hubspot-instructions-box-input" readonly value="" data-suffix="/webhooks/crm?client_id={active_client_id}">
                                        <button type="button" onclick="copyText('sot-hubspot-instructions-box-input', 'sot-hubspot-instructions-box-btn')" id="sot-hubspot-instructions-box-btn" class="btn-copy"> Copy Webhook URL</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div id="sot-salesforce-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                            <div style="background-color: #e3f2fd; border-left: 4px solid #1e88e5; padding: 15px; border-radius: 4px; color: #0d47a1; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                 <strong>Salesforce Outbound Flow Setup Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #1565c0;">
                                <li>In Salesforce Setup, go to <strong>Named Credentials &gt; External Credentials</strong> tab, click <strong>New</strong>. Label <code>LeadGroove External Credential</code>, Name <code>LeadGroove_External_Credential</code>, Protocol <strong>Custom</strong>. Save, scroll to <em>Principals</em>, click <strong>New</strong> and define a principal named <code>LeadGroove_Principal</code>.</li>
                                <li>Create a <strong>Permission Set</strong> in Setup named <code>LeadGroove Webhook Access</code>. In it, click <strong>External Credential Principal Access</strong>, enable your new credential and principal, and assign this permission set to any integrating users.</li>
                                <li>Back in <strong>Named Credentials</strong>, click <strong>New</strong> under the main tab. Label <code>LeadGroove API</code>, Name <code>LeadGroove_API</code>, URL set to your active origin. Under <em>External Credential</em>, select the credential you created in Step 1, and save.</li>
                                <li>Create a <strong>Record-Triggered Flow</strong> on the <strong>Opportunity</strong> object (when updated) with conditions <code>StageName Equals Closed Won</code> (Only when updated to meet conditions), optimized for <strong>Actions and Related Records</strong>.</li>
                                <li>On the flow canvas, click <strong>+ Add Action &gt; Create HTTP Callout</strong>, select your Named Credential, and define a <strong>POST</strong> method targeting your webhook URL below.</li>
                            </ol>
                                <div style="margin-top: 12px; background: white; padding: 12px; border-radius: 6px; border: 1px solid #1e88e5;">
                                    <label style="font-size: 11px; font-weight: bold; color: #0d47a1; display: block; margin-bottom: 5px;">⚡ YOUR TARGET WEBHOOK ENDPOINT URL:</label>
                                    <div class="webhook-input-group">
                                        <input type="text" class="webhook-input" id="sot-salesforce-instructions-box-input" readonly value="" data-suffix="/webhooks/crm?client_id={active_client_id}">
                                        <button type="button" onclick="copyText('sot-salesforce-instructions-box-input', 'sot-salesforce-instructions-box-btn')" id="sot-salesforce-instructions-box-btn" class="btn-copy"> Copy Webhook URL</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div id="sot-zoho-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                            <div style="background-color: #e8f5e9; border-left: 4px solid #2e7d32; padding: 15px; border-radius: 4px; color: #1b5e20; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                 <strong>Zoho CRM Outbound Webhook Setup Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #2e7d32;">
                                <li>Click the <strong>Setup (Gear Icon)</strong> in the top-right corner of your Zoho CRM dashboard.</li>
                                <li>Under <strong>Automation</strong>, click on <strong>Actions</strong>, then select the <strong>Webhooks</strong> tab at the top.</li>
                                <li>Click <strong>Configure Webhook</strong>, set Name to <code>LeadGroove Conversion Sync</code>, Method to <strong>POST</strong>, Module to <strong>Deals</strong>, and paste your target URL below into <strong>URL to notify</strong>.</li>
                                <li>In the <strong>Body</strong> section, select <strong>Raw (JSON)</strong> format, and type <code>#</code> to insert CRM fields into your payload structure. Click <strong>Save</strong>!</li>
                            </ol>
                                <div style="margin-top: 12px; background: white; padding: 12px; border-radius: 6px; border: 1px solid #2e7d32;">
                                    <label style="font-size: 11px; font-weight: bold; color: #1b5e20; display: block; margin-bottom: 5px;">⚡ YOUR TARGET WEBHOOK ENDPOINT URL:</label>
                                    <div class="webhook-input-group">
                                        <input type="text" class="webhook-input" id="sot-zoho-instructions-box-input" readonly value="" data-suffix="/webhooks/crm?client_id={active_client_id}">
                                        <button type="button" onclick="copyText('sot-zoho-instructions-box-input', 'sot-zoho-instructions-box-btn')" id="sot-zoho-instructions-box-btn" class="btn-copy"> Copy Webhook URL</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div id="sot-servicetitan-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                            <div style="background-color: #f3f4f6; border-left: 4px solid #4b5563; padding: 15px; border-radius: 4px; color: #1f2937; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                 <strong>ServiceTitan Webhooks V2 Quick Setup Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #374151;">
                                    <li>Navigate to the <strong>ServiceTitan Developer Portal</strong> at <a href="https://developer.servicetitan.io" target="_blank" style="color: #1a237e; font-weight: bold; text-decoration: none;">developer.servicetitan.io</a>.</li>
                                    <li>Click <strong>Create and Manage Applications ➡️ Create New App</strong>. Name it <code>LeadGroove Webhook Sync</code> and set scopes <code>crm.objects.leads.read</code> / <code>jpm.objects.jobs.read</code>.</li>
                                    <li>Log into your portal at <a href="https://go.servicetitan.com" target="_blank" style="color: #1a237e; font-weight: bold; text-decoration: none;">go.servicetitan.com</a>, go to <strong>Settings ➡️ Integrations ➡️ API Application Access</strong>, edit your app, and set your target Webhook URL below!</li>
                                </ol>
                                <div style="margin-top: 12px; background: white; padding: 12px; border-radius: 6px; border: 1px solid #4b5563;">
                                    <label style="font-size: 11px; font-weight: bold; color: #1f2937; display: block; margin-bottom: 5px;">⚡ YOUR TARGET WEBHOOK ENDPOINT URL:</label>
                                    <div class="webhook-input-group">
                                        <input type="text" class="webhook-input" id="sot-servicetitan-instructions-box-input" readonly value="" data-suffix="/webhooks/crm?client_id={active_client_id}">
                                        <button type="button" onclick="copyText('sot-servicetitan-instructions-box-input', 'sot-servicetitan-instructions-box-btn')" id="sot-servicetitan-instructions-box-btn" class="btn-copy"> Copy Webhook URL</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div id="sot-gohighlevel-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                            <div style="background-color: #e8eaf6; border-left: 4px solid #3f51b5; padding: 15px; border-radius: 4px; color: #1a237e; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                 <strong>GoHighLevel (GHL) Workflow Webhook Setup Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #1a237e;">
                                <li>Log into your <strong>GoHighLevel Sub-Account / Agency Portal</strong>.</li>
                                <li>Go to <strong>Automation ➡️ Workflows</strong> and click <strong>+ Create Workflow</strong>.</li>
                                <li>Set Trigger to <strong>Opportunity Status Changed</strong> (e.g. Stage updated to <em>Won</em> or <em>Qualified</em>), <strong>Contact Tag Added</strong>, or <strong>Form Submitted</strong>.</li>
                                <li>Add Action ➡️ Select <strong>Webhook</strong>, set Method to <strong>POST</strong>, and paste your target URL below.</li>
                                <li>Toggle Workflow to <strong>Publish</strong> and save! Whenever an opportunity updates or form submits, data pushes to LeadGrove in real time.</li>
                            </ol>
                                <div style="margin-top: 12px; background: white; padding: 12px; border-radius: 6px; border: 1px solid #3f51b5;">
                                    <label style="font-size: 11px; font-weight: bold; color: #1a237e; display: block; margin-bottom: 5px;">⚡ YOUR TARGET WEBHOOK ENDPOINT URL:</label>
                                    <div class="webhook-input-group">
                                        <input type="text" class="webhook-input" id="sot-gohighlevel-instructions-box-input" readonly value="" data-suffix="/webhooks/crm?client_id={active_client_id}">
                                        <button type="button" onclick="copyText('sot-gohighlevel-instructions-box-input', 'sot-gohighlevel-instructions-box-btn')" id="sot-gohighlevel-instructions-box-btn" class="btn-copy"> Copy Webhook URL</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div id="sot-housecallpro-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                            <div style="background-color: #fff3e0; border-left: 4px solid #e65100; padding: 15px; border-radius: 4px; color: #e65100; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                 <strong>Housecall Pro Webhooks Quick Setup Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #e65100;">
                                    <li>Sign in as an <strong>Admin</strong> user in Housecall Pro.</li>
                                    <li>Go to <strong>My Apps ➡️ All Apps</strong>, search for <strong>Webhooks</strong>, and click to open.</li>
                                    <li>Toggle <strong>Enable Webhooks</strong> on, and paste your target URL below into <strong>Target URL</strong>.</li>
                                    <li>Subscribe to <code>job.completed</code>, <code>job.created</code>, and <code>job.paid</code>. Save to activate!</li>
                                </ol>
                                <div style="margin-top: 12px; background: white; padding: 12px; border-radius: 6px; border: 1px solid #e65100;">
                                    <label style="font-size: 11px; font-weight: bold; color: #e65100; display: block; margin-bottom: 5px;">⚡ YOUR TARGET WEBHOOK ENDPOINT URL:</label>
                                    <div class="webhook-input-group">
                                        <input type="text" class="webhook-input" id="sot-housecallpro-instructions-box-input" readonly value="" data-suffix="/webhooks/crm?client_id={active_client_id}">
                                        <button type="button" onclick="copyText('sot-housecallpro-instructions-box-input', 'sot-housecallpro-instructions-box-btn')" id="sot-housecallpro-instructions-box-btn" class="btn-copy"> Copy Webhook URL</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div id="sot-quickbooks-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                            <div style="background-color: #e3f2fd; border-left: 4px solid #0288d1; padding: 15px; border-radius: 4px; color: #01579b; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                 <strong>QuickBooks Online Webhooks Setup Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #0277bd;">
                                    <li>Log into the <strong>Intuit Developer Portal</strong> at <a href="https://developer.intuit.com" target="_blank" style="color: #1a237e; font-weight: bold; text-decoration: none;">developer.intuit.com</a>.</li>
                                    <li>Go to <strong>Production Settings ➡️ Webhooks</strong> in your App sidebar.</li>
                                    <li>In the <strong>Endpoint URL</strong> field, paste your dynamic target URL below.</li>
                                    <li>Check event boxes under <strong>Invoices</strong> or <strong>Payments</strong> and click Save!</li>
                                </ol>
                                <div style="margin-top: 12px; background: white; padding: 12px; border-radius: 6px; border: 1px solid #0288d1;">
                                    <label style="font-size: 11px; font-weight: bold; color: #01579b; display: block; margin-bottom: 5px;">⚡ YOUR TARGET WEBHOOK ENDPOINT URL:</label>
                                    <div class="webhook-input-group">
                                        <input type="text" class="webhook-input" id="sot-quickbooks-instructions-box-input" readonly value="" data-suffix="/webhooks/billing?client_id={active_client_id}">
                                        <button type="button" onclick="copyText('sot-quickbooks-instructions-box-input', 'sot-quickbooks-instructions-box-btn')" id="sot-quickbooks-instructions-box-btn" class="btn-copy"> Copy Webhook URL</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div id="sot-xero-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                            <div style="background-color: #e0f7fa; border-left: 4px solid #00b0ff; padding: 15px; border-radius: 4px; color: #006064; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                 <strong>Xero Webhooks Setup Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #00838f;">
                                    <li>Log into <strong>Xero Developer Portal</strong> under My Apps, select your App, and open the <strong>Webhooks</strong> tab.</li>
                                    <li>Paste your live endpoint below into the <strong>Send notifications to</strong> field.</li>
                                    <li>Subscribe to <strong>Invoices</strong> (CREATE, UPDATE) and click Save!</li>
                                </ol>
                                <div style="margin-top: 12px; background: white; padding: 12px; border-radius: 6px; border: 1px solid #00b0ff;">
                                    <label style="font-size: 11px; font-weight: bold; color: #006064; display: block; margin-bottom: 5px;">⚡ YOUR TARGET WEBHOOK ENDPOINT URL:</label>
                                    <div class="webhook-input-group">
                                        <input type="text" class="webhook-input" id="sot-xero-instructions-box-input" readonly value="" data-suffix="/webhooks/billing?client_id={active_client_id}">
                                        <button type="button" onclick="copyText('sot-xero-instructions-box-input', 'sot-xero-instructions-box-btn')" id="sot-xero-instructions-box-btn" class="btn-copy"> Copy Webhook URL</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div id="sot-zoho_books-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                            <div style="background-color: #e8f5e9; border-left: 4px solid #2e7d32; padding: 15px; border-radius: 4px; color: #1b5e20; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                 <strong>Zoho Books Webhooks Setup Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #1b5e20;">
                                    <li>Go to <strong>Settings ➡️ Developer Space ➡️ Webhooks</strong> in Zoho Books and click <strong>+ New Webhook</strong>.</li>
                                    <li>Paste your custom endpoint below into <strong>URL to Notify</strong>, set Module to <strong>Invoices</strong>, and select event <strong>Invoice Paid</strong>!</li>
                                </ol>
                                <div style="margin-top: 12px; background: white; padding: 12px; border-radius: 6px; border: 1px solid #2e7d32;">
                                    <label style="font-size: 11px; font-weight: bold; color: #1b5e20; display: block; margin-bottom: 5px;">⚡ YOUR TARGET WEBHOOK ENDPOINT URL:</label>
                                    <div class="webhook-input-group">
                                        <input type="text" class="webhook-input" id="sot-zoho_books-instructions-box-input" readonly value="" data-suffix="/webhooks/billing?client_id={active_client_id}">
                                        <button type="button" onclick="copyText('sot-zoho_books-instructions-box-input', 'sot-zoho_books-instructions-box-btn')" id="sot-zoho_books-instructions-box-btn" class="btn-copy"> Copy Webhook URL</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div id="sot-netsuite-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                            <div style="background-color: #eceff1; border-left: 4px solid #455a64; padding: 15px; border-radius: 4px; color: #263238; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                 <strong>NetSuite SuiteScript Integration Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #37474f;">
                                    <li>Deploy a <strong>SuiteScript 2.x User Event Script</strong> on `Invoice` or `CustomerPayment` records.</li>
                                    <li>On `afterSubmit`, trigger an outbound HTTP POST to your web receiver endpoint below.</li>
                                </ol>
                                <div style="margin-top: 12px; background: white; padding: 12px; border-radius: 6px; border: 1px solid #455a64;">
                                    <label style="font-size: 11px; font-weight: bold; color: #263238; display: block; margin-bottom: 5px;">⚡ YOUR TARGET WEBHOOK ENDPOINT URL:</label>
                                    <div class="webhook-input-group">
                                        <input type="text" class="webhook-input" id="sot-netsuite-instructions-box-input" readonly value="" data-suffix="/webhooks/billing?client_id={active_client_id}">
                                        <button type="button" onclick="copyText('sot-netsuite-instructions-box-input', 'sot-netsuite-instructions-box-btn')" id="sot-netsuite-instructions-box-btn" class="btn-copy"> Copy Webhook URL</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div id="sot-sage-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                            <div style="background-color: #fff3e0; border-left: 4px solid #e65100; padding: 15px; border-radius: 4px; color: #e65100; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                 <strong>Sage Accounting Webhooks Setup Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #e65100;">
                                    <li>In Sage Developer Portal, configure webhooks and paste your dynamic endpoint URL below.</li>
                                    <li>Subscribe to <code>sales_invoice.paid</code> and <code>payment_received</code>!</li>
                                </ol>
                                <div style="margin-top: 12px; background: white; padding: 12px; border-radius: 6px; border: 1px solid #e65100;">
                                    <label style="font-size: 11px; font-weight: bold; color: #e65100; display: block; margin-bottom: 5px;">⚡ YOUR TARGET WEBHOOK ENDPOINT URL:</label>
                                    <div class="webhook-input-group">
                                        <input type="text" class="webhook-input" id="sot-sage-instructions-box-input" readonly value="" data-suffix="/webhooks/billing?client_id={active_client_id}">
                                        <button type="button" onclick="copyText('sot-sage-instructions-box-input', 'sot-sage-instructions-box-btn')" id="sot-sage-instructions-box-btn" class="btn-copy"> Copy Webhook URL</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div id="sot-freshbooks-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                            <div style="background-color: #f3e5f5; border-left: 4px solid #4a148c; padding: 15px; border-radius: 4px; color: #4a148c; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                 <strong>FreshBooks Billing Webhooks Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #4a148c;">
                                    <li>Subscribe to webhook notifications in FreshBooks Developer Center and paste your endpoint below.</li>
                                    <li>Set trigger event to <code>invoice.payment.create</code>.</li>
                                </ol>
                                <div style="margin-top: 12px; background: white; padding: 12px; border-radius: 6px; border: 1px solid #4a148c;">
                                    <label style="font-size: 11px; font-weight: bold; color: #4a148c; display: block; margin-bottom: 5px;">⚡ YOUR TARGET WEBHOOK ENDPOINT URL:</label>
                                    <div class="webhook-input-group">
                                        <input type="text" class="webhook-input" id="sot-freshbooks-instructions-box-input" readonly value="" data-suffix="/webhooks/billing?client_id={active_client_id}">
                                        <button type="button" onclick="copyText('sot-freshbooks-instructions-box-input', 'sot-freshbooks-instructions-box-btn')" id="sot-freshbooks-instructions-box-btn" class="btn-copy"> Copy Webhook URL</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div id="sot-google_sheets-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                            <div style="background-color: #efebe9; border-left: 4px solid #4e342e; padding: 15px; border-radius: 4px; color: #4e342e; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                 <strong>Google Sheets (Live Sync via Apps Script) Setup Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #4e342e;">
                                    <li><strong>Define Lead & Won Deal Tags Above:</strong> Enter status tags in the <em>Qualified Lead Tags</em> and <em>Won Deal Tags</em> boxes above (e.g. <code>appointment-booked</code> or <code>closed-won</code>).</li>
                                    <li><strong>Format Row 1 Column Headings:</strong> Ensure your Google Sheet contains these standard Row 1 headers:
                                        <ul style="list-style-type: disc; padding-left: 15px; margin: 4px 0;">
                                            <li><code>Phone</code> (or <code>Phone Number</code>): Required to match lead phone numbers with CallRail/form sessions.</li>
                                            <li><code>Email</code> (or <code>Email Address</code>): Optional secondary customer identifier.</li>
                                            <li><code>Status</code> (or <code>Lead Status</code> / <code>Stage</code>): Cell value indicating lead stage (must match tags defined above).</li>
                                            <li><code>Amount</code> (or <code>Revenue</code> / <code>Value</code> / <code>Total</code>): Dollar amount of closed sale (e.g. <code>450.00</code>).</li>
                                            <li><code>Name</code> (or <code>Customer Name</code>): Optional customer name for audit logs.</li>
                                        </ul>
                                    </li>
                                    <li><strong>Connect Apps Script Sync:</strong> Open your Google Sheet, click <strong>Extensions ➡️ Apps Script</strong>, paste the Apps Script webhook trigger, and set your target endpoint URL below!</li>
                                </ol>
                                <div style="margin-top: 12px; background: white; padding: 12px; border-radius: 6px; border: 1px solid #4e342e;">
                                    <label style="font-size: 11px; font-weight: bold; color: #4e342e; display: block; margin-bottom: 5px;">⚡ YOUR TARGET WEBHOOK ENDPOINT URL:</label>
                                    <div class="webhook-input-group">
                                        <input type="text" class="webhook-input" id="sot-google_sheets-instructions-box-input" readonly value="" data-suffix="/webhooks/crm?client_id={active_client_id}">
                                        <button type="button" onclick="copyText('sot-google_sheets-instructions-box-input', 'sot-google_sheets-instructions-box-btn')" id="sot-google_sheets-instructions-box-btn" class="btn-copy"> Copy Webhook URL</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                                                <div id="sot-zapier-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #f57c00; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                            <div style="background-color: #fff3e0; border-left: 4px solid #ff9800; padding: 15px; border-radius: 4px; color: #e65100; font-size: 13px; line-height: 1.6; margin-bottom: 0; text-align: left;">
                                 <strong>Webhooks by Zapier Custom Setup Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 10px; margin-bottom: 10px; line-height: 1.8; font-size: 12px; color: #e65100;">
                                    <li><strong>Indicate Conversion Tags Above:</strong> In the fields above, enter the exact tags or statuses (e.g. <code>appointment-booked</code>, <code>closed-won</code>, <code>paid</code>) that signify a <strong>Qualified Lead</strong> and a <strong>Won Deal</strong> in your pipeline.</li>
                                    <li><strong>Create a Zap in Zapier:</strong> Set your <strong>Trigger</strong> app (e.g. Stripe, PayPal, Typeform, Calendly, or custom CRM).</li>
                                    <li><strong>Add Webhook Action:</strong> Add an Action step, select <strong>Webhooks by Zapier ➡️ Custom Request (POST) or POST</strong>, and paste your endpoint URL below into the <strong>URL</strong> field.</li>
                                    <li><strong>Map Payload Data Fields:</strong> Map your trigger data to these key parameters so LeadGrove accurately parses your conversions:
                                        <ul style="list-style-type: disc; padding-left: 20px; margin-top: 5px; margin-bottom: 5px;">
                                            <li><code>phone</code> (or <code>phone_number</code>) — Customer phone number (used to match original ad click)</li>
                                            <li><code>status</code> (or <code>stage</code> / <code>tag</code>) — Matching the tags specified in Step 1</li>
                                            <li><code>amount</code> (or <code>value</code>) — Transaction dollar revenue for won deals (e.g. <code>450.00</code>)</li>
                                            <li><code>email</code> / <code>name</code> — Customer email and name</li>
                                        </ul>
                                    </li>
                                </ol>

                                <!-- Visual Diagram: Zapier Workflow Mapping -->
                                <div style="margin-top: 15px; margin-bottom: 15px; background: white; border: 1px solid #ffe0b2; border-radius: 8px; padding: 15px;">
                                    <div style="font-size: 11px; font-weight: bold; color: #e65100; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 10px; text-align: center;">
                                        ⚡ ZAPIER WEBHOOK CONFIGURATION ARCHITECTURE
                                    </div>
                                    <div style="display: flex; align-items: center; justify-content: space-around; gap: 8px; flex-wrap: wrap; text-align: center; margin-bottom: 12px;">
                                        <div style="background: #fff8e1; border: 1px solid #ffe0b2; border-radius: 6px; padding: 10px; min-width: 120px; flex: 1;">
                                            <div style="font-size: 18px;"></div>
                                            <div style="font-weight: bold; font-size: 11px; color: #e65100;">1. Trigger App</div>
                                            <div style="font-size: 10px; color: #795548;">CRM, Form, Stripe</div>
                                        </div>
                                        <div style="font-size: 16px; color: #ff9800; font-weight: bold;">➔</div>
                                        <div style="background: #fff3e0; border: 1px solid #ffcc80; border-radius: 6px; padding: 10px; min-width: 130px; flex: 1;">
                                            <div style="font-size: 18px;">⚡</div>
                                            <div style="font-weight: bold; font-size: 11px; color: #e65100;">2. Webhooks by Zapier</div>
                                            <div style="font-size: 10px; color: #795548;">Action: POST Method</div>
                                        </div>
                                        <div style="font-size: 16px; color: #ff9800; font-weight: bold;">➔</div>
                                        <div style="background: #e8f5e9; border: 1px solid #a5d6a7; border-radius: 6px; padding: 10px; min-width: 120px; flex: 1;">
                                            <div style="font-size: 18px;"></div>
                                            <div style="font-weight: bold; font-size: 11px; color: #2e7d32;">3. LeadGrove Engine</div>
                                            <div style="font-size: 10px; color: #388e3c;">Ad Network Uploads</div>
                                        </div>
                                    </div>

                                    <div style="background: #263238; color: #eceff1; border-radius: 6px; padding: 12px; font-family: monospace; font-size: 11px; line-height: 1.6; text-align: left;">
                                        <div style="color: #ffb74d; font-weight: bold; margin-bottom: 4px;">// Zapier Action Setup Mockup</div>
                                        <div><span style="color: #80cbc4;">Action Event :</span> <span style="color: #fff;">POST</span></div>
                                        <div><span style="color: #80cbc4;">URL          :</span> <span style="color: #fff;">[YOUR TARGET WEBHOOK URL BELOW]</span></div>
                                        <div><span style="color: #80cbc4;">Payload Type :</span> <span style="color: #fff;">json</span></div>
                                        <div><span style="color: #80cbc4;">Data Fields  :</span></div>
                                        <div style="padding-left: 15px;"><span style="color: #81c784;">phone</span>  ➡️  <span style="color: #b0bec5;">1. Customer Phone Number</span></div>
                                        <div style="padding-left: 15px;"><span style="color: #81c784;">status</span> ➡️  <span style="color: #b0bec5;">1. Deal Stage / Status Tag</span></div>
                                        <div style="padding-left: 15px;"><span style="color: #81c784;">amount</span> ➡️  <span style="color: #b0bec5;">1. Purchase Revenue ($)</span></div>
                                    </div>
                                </div>

                                <div style="margin-top: 12px; background: white; padding: 12px; border-radius: 6px; border: 1px solid #fbc02d;">
                                    <label style="font-size: 11px; font-weight: bold; color: #f57f17; display: block; margin-bottom: 5px;">⚡ YOUR TARGET WEBHOOK ENDPOINT URL:</label>
                                    <div class="webhook-input-group">
                                        <input type="text" class="webhook-input" id="sot-zapier-instructions-box-input" readonly value="" data-suffix="/webhooks/crm?client_id={active_client_id}">
                                        <button type="button" onclick="copyText('sot-zapier-instructions-box-input', 'sot-zapier-instructions-box-btn')" id="sot-zapier-instructions-box-btn" class="btn-copy"> Copy Webhook URL</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div id="sot-monthly-email-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                            <div style="background-color: #e8eaf6; border-left: 4px solid #1a237e; padding: 15px; border-radius: 4px; color: #1a237e; font-size: 13px; line-height: 1.6; margin-bottom: 0; text-align: left;">
                                 <strong>Monthly Sales Spreadsheet Email Ingestion Setup Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 10px; margin-bottom: 10px; line-height: 1.8; font-size: 12px; color: #1a237e;">
                                    <li><strong>Setup Email Forwarding:</strong> Set up automated forwarding or email your monthly sales spreadsheet directly to:<br>
                                        <div style="margin-top: 6px; margin-bottom: 6px; background: white; padding: 10px; border-radius: 6px; border: 1px solid #c5cae9; display: inline-block;">
                                            <code class="monthly-forwarding-email" style="font-size: 13px; font-weight: bold; color: #2e7d32; font-family: monospace;">conversions-{active_client_id}@your-agency.com</code>
                                        </div>
                                    </li>
                                    <li><strong>Specify Qualification & Revenue Tags:</strong> In the fields above, specify which tags or cell statuses (e.g. <code>appointment-booked</code>, <code>closed-won</code>, <code>paid</code>) indicate qualified leads and purchase revenue.</li>
                                    <li><strong>Format Spreadsheet Column Headings:</strong> Ensure Row 1 of your spreadsheet includes matching column headers:
                                        <ul style="list-style-type: disc; padding-left: 20px; margin-top: 5px; margin-bottom: 5px;">
                                            <li><code>Phone</code> (or <code>Phone Number</code>) — Required to match original callers / web leads</li>
                                            <li><code>Status</code> (or <code>Stage</code> / <code>Tag</code>) — Matching the tags specified in Step #2 above</li>
                                            <li><code>Amount</code> (or <code>Revenue</code> / <code>Total</code>) — The purchase dollar value</li>
                                            <li><code>Email</code> / <code>Name</code> — Optional customer details</li>
                                        </ul>
                                    </li>
                                </ol>
                            </div>
                        </div>


<!-- CONDITIONAL: CRM Lead status tags -->
                            <div id="sot-lead-tags-box" class="conditional-box">
                                <label for="crm_lead_tags">Which statuses under <strong>Leads</strong> signify qualification?</label>
                                <input type="text" id="crm_lead_tags" value="{client_data.get("crm_lead_tags", "") or ""}" placeholder="e.g. job-booked, estimate-given">
                            </div>
                            
                                                        <!-- CONDITIONAL: Email settings fallback -->
                            
                            <!-- CONDITIONAL: AI Rating VoIP Provider Box -->
                                                        <div id="sot-voip-box" class="conditional-box" style="display: none; background-color: #f8f9fa; border: 1px dashed #1a237e; border-radius: 8px; padding: 20px; margin-top: 15px;">
                                <div class="form-group" style="margin-bottom: 15px;">
                                    <label for="voip_provider" style="font-weight: bold; color: #1a237e; font-size: 13px;">Select Your Current VOIP Provider (Optional)</label>
                                    <select id="voip_provider" onchange="toggleVoipInstructions()" style="width: 100%; padding: 10px; border-radius: 6px; border: 1px solid #ced4da; font-size: 13px; font-weight: 600; cursor: pointer;">
                                        <option value="dialpad" selected>Dialpad (Ai Recap)</option>
                                        <option value="ringcentral">RingCentral</option>
                                        <option value="zoom_phone">Zoom Phone</option>
                                        <option value="openphone">OpenPhone</option>
                                        <option value="nextiva">Nextiva</option>
                                        <option value="vonage">Vonage Business</option>
                                        <option value="ooma">Ooma Office</option>
                                        <option value="grasshopper">Grasshopper</option>
                                        <option value="custom_voip">Custom VoIP / Other</option>
                                    </select>
                                    <small style="color: #666; font-size: 11px; margin-top: 4px; display: block;">
                                        LeadGrove will receive call recordings & transcripts from this provider to run automated AI audits.
                                    </small>
                                </div>

                                <!-- Dynamic Instructions per VoIP Provider -->

                                <div id="voip-inst-dialpad" class="voip-inst-card" style="display: none; background-color: #f3e5f5; border-left: 4px solid #7b1fa2; padding: 14px; border-radius: 4px; color: #4a148c; font-size: 12px; line-height: 1.5;">
                                     <strong>Dialpad (Ai Recap) Setup Instructions:</strong><br>
                                    1. In Dialpad Admin Settings, go to <strong>Integrations ➡️ Webhooks ➡️ Add Webhook</strong>.<br>
                                    2. Set Target URL to your endpoint below, and check events <strong>"call_completed"</strong> and <strong>"transcript_ready"</strong>.<br>
                                    3. Dialpad's native AI transcripts will automatically stream to LeadGrove for Claude auditing!
                                    <div style="margin-top: 10px; background: white; padding: 10px; border-radius: 6px; border: 1px solid #e1bee7;">
                                        <label style="font-size: 11px; font-weight: bold; color: #4a148c; display: block; margin-bottom: 4px;">⚡ DIALPAD WEBHOOK ENDPOINT URL:</label>
                                        <div class="webhook-input-group">
                                            <input type="text" class="webhook-input" id="voip-endpoint-dialpad" readonly value="" data-suffix="/webhooks/voip?client_id={active_client_id}">
                                            <button type="button" onclick="copyText('voip-endpoint-dialpad', 'voip-btn-dialpad')" id="voip-btn-dialpad" class="btn-copy"> Copy Webhook URL</button>
                                        </div>
                                    </div>
                                </div>

                                <div id="voip-inst-ringcentral" class="voip-inst-card" style="display: none; background-color: #e0f7fa; border-left: 4px solid #0097a7; padding: 14px; border-radius: 4px; color: #006064; font-size: 12px; line-height: 1.5;">
                                     <strong>RingCentral Setup Instructions:</strong><br>
                                    1. In RingCentral Admin Console, go to <strong>Integrations / Webhooks ➡️ Create Subscription</strong>.<br>
                                    2. Set Notification Event to <strong>"Telephony Session / Call Log"</strong> and paste target URL below.<br>
                                    3. RingCentral inbound & outbound calls will sync automatically with LeadGrove lead timelines!
                                    <div style="margin-top: 10px; background: white; padding: 10px; border-radius: 6px; border: 1px solid #b2ebf2;">
                                        <label style="font-size: 11px; font-weight: bold; color: #006064; display: block; margin-bottom: 4px;">⚡ RINGCENTRAL WEBHOOK ENDPOINT URL:</label>
                                        <div class="webhook-input-group">
                                            <input type="text" class="webhook-input" id="voip-endpoint-rc" readonly value="" data-suffix="/webhooks/voip?client_id={active_client_id}">
                                            <button type="button" onclick="copyText('voip-endpoint-rc', 'voip-btn-rc')" id="voip-btn-rc" class="btn-copy"> Copy Webhook URL</button>
                                        </div>
                                    </div>
                                </div>

                                <div id="voip-inst-zoom_phone" class="voip-inst-card" style="display: none; background-color: #e3f2fd; border-left: 4px solid #1e88e5; padding: 14px; border-radius: 4px; color: #0d47a1; font-size: 12px; line-height: 1.5;">
                                     <strong>Zoom Phone Setup Instructions:</strong><br>
                                    1. In Zoom Marketplace, go to <strong>Develop ➡️ Build App ➡️ Webhook Only</strong>.<br>
                                    2. Subscribe to Event Notifications: <strong>"phone.callee_ended"</strong> & <strong>"phone.recording_completed"</strong>.<br>
                                    3. Set Webhook Endpoint URL to the link below to stream Zoom call logs directly to LeadGrove!
                                    <div style="margin-top: 10px; background: white; padding: 10px; border-radius: 6px; border: 1px solid #bbdefb;">
                                        <label style="font-size: 11px; font-weight: bold; color: #0d47a1; display: block; margin-bottom: 4px;">⚡ ZOOM PHONE WEBHOOK ENDPOINT URL:</label>
                                        <div class="webhook-input-group">
                                            <input type="text" class="webhook-input" id="voip-endpoint-zoom" readonly value="" data-suffix="/webhooks/voip?client_id={active_client_id}">
                                            <button type="button" onclick="copyText('voip-endpoint-zoom', 'voip-btn-zoom')" id="voip-btn-zoom" class="btn-copy"> Copy Webhook URL</button>
                                        </div>
                                    </div>
                                </div>

                                <div id="voip-inst-openphone" class="voip-inst-card" style="display: none; background-color: #f1f8e9; border-left: 4px solid #33691e; padding: 14px; border-radius: 4px; color: #1b5e20; font-size: 12px; line-height: 1.5;">
                                     <strong>OpenPhone Setup Instructions:</strong><br>
                                    1. In OpenPhone Settings, go to <strong>Integrations ➡️ Webhooks ➡️ Add Webhook</strong>.<br>
                                    2. Paste target URL below and check events: <strong>"call.completed"</strong> and <strong>"call.transcript.completed"</strong>.<br>
                                    3. Sales rep follow-up calls will instantly pair with original lead click IDs!
                                    <div style="margin-top: 10px; background: white; padding: 10px; border-radius: 6px; border: 1px solid #c8e6c9;">
                                        <label style="font-size: 11px; font-weight: bold; color: #1b5e20; display: block; margin-bottom: 4px;">⚡ OPENPHONE WEBHOOK ENDPOINT URL:</label>
                                        <div class="webhook-input-group">
                                            <input type="text" class="webhook-input" id="voip-endpoint-openphone" readonly value="" data-suffix="/webhooks/voip?client_id={active_client_id}">
                                            <button type="button" onclick="copyText('voip-endpoint-openphone', 'voip-btn-openphone')" id="voip-btn-openphone" class="btn-copy"> Copy Webhook URL</button>
                                        </div>
                                    </div>
                                </div>

                                <div id="voip-inst-nextiva" class="voip-inst-card" style="display: none; background-color: #e8eaf6; border-left: 4px solid #283593; padding: 14px; border-radius: 4px; color: #1a237e; font-size: 12px; line-height: 1.5;">
                                     <strong>Nextiva Setup Instructions:</strong><br>
                                    1. Log into Nextiva Voice Admin Portal ➡️ <strong>Integrations / Analytics ➡️ Webhooks</strong>.<br>
                                    2. Click <strong>Add Webhook</strong>, set Target URL to your endpoint below, and subscribe to <strong>"Call Completed"</strong>.<br>
                                    3. Ensure Call Recording & Speech-to-Text Transcriptions are enabled for your team extensions!
                                    <div style="margin-top: 10px; background: white; padding: 10px; border-radius: 6px; border: 1px solid #c5cae9;">
                                        <label style="font-size: 11px; font-weight: bold; color: #1a237e; display: block; margin-bottom: 4px;">⚡ NEXTIVA WEBHOOK ENDPOINT URL:</label>
                                        <div class="webhook-input-group">
                                            <input type="text" class="webhook-input" id="voip-endpoint-nextiva" readonly value="" data-suffix="/webhooks/voip?client_id={active_client_id}">
                                            <button type="button" onclick="copyText('voip-endpoint-nextiva', 'voip-btn-nextiva')" id="voip-btn-nextiva" class="btn-copy"> Copy Webhook URL</button>
                                        </div>
                                    </div>
                                </div>

                                <div id="voip-inst-vonage" class="voip-inst-card" style="display: none; background-color: #fff3e0; border-left: 4px solid #e65100; padding: 14px; border-radius: 4px; color: #e65100; font-size: 12px; line-height: 1.5;">
                                     <strong>Vonage Business Setup Instructions:</strong><br>
                                    1. Log into Vonage Business Communications (VBC) Admin Portal ➡️ <strong>Integration Suite ➡️ Webhooks</strong>.<br>
                                    2. Create a new webhook subscription, paste your Target URL below, and select event <strong>"call.completed"</strong>.<br>
                                    3. Ensure Automatic Call Recording is enabled so call logs and audio stream directly to LeadGrove!
                                    <div style="margin-top: 10px; background: white; padding: 10px; border-radius: 6px; border: 1px solid #ffe0b2;">
                                        <label style="font-size: 11px; font-weight: bold; color: #e65100; display: block; margin-bottom: 4px;">⚡ VONAGE WEBHOOK ENDPOINT URL:</label>
                                        <div class="webhook-input-group">
                                            <input type="text" class="webhook-input" id="voip-endpoint-vonage" readonly value="" data-suffix="/webhooks/voip?client_id={active_client_id}">
                                            <button type="button" onclick="copyText('voip-endpoint-vonage', 'voip-btn-vonage')" id="voip-btn-vonage" class="btn-copy"> Copy Webhook URL</button>
                                        </div>
                                    </div>
                                </div>

                                <div id="voip-inst-ooma" class="voip-inst-card" style="display: none; background-color: #e0f2f1; border-left: 4px solid #00695c; padding: 14px; border-radius: 4px; color: #004d40; font-size: 12px; line-height: 1.5;">
                                     <strong>Ooma Office Setup Instructions:</strong><br>
                                    1. Log into Ooma Office Manager (office.ooma.com) ➡️ <strong>System ➡️ Integrations & API Webhooks</strong>.<br>
                                    2. Click <strong>Add Webhook</strong>, paste your target endpoint below, and set trigger to <strong>"Call Ended"</strong>.<br>
                                    3. Confirm Call Recording is activated for your extension group so recordings & transcripts are captured!
                                    <div style="margin-top: 10px; background: white; padding: 10px; border-radius: 6px; border: 1px solid #b2dfdb;">
                                        <label style="font-size: 11px; font-weight: bold; color: #004d40; display: block; margin-bottom: 4px;">⚡ OOMA OFFICE WEBHOOK ENDPOINT URL:</label>
                                        <div class="webhook-input-group">
                                            <input type="text" class="webhook-input" id="voip-endpoint-ooma" readonly value="" data-suffix="/webhooks/voip?client_id={active_client_id}">
                                            <button type="button" onclick="copyText('voip-endpoint-ooma', 'voip-btn-ooma')" id="voip-btn-ooma" class="btn-copy"> Copy Webhook URL</button>
                                        </div>
                                    </div>
                                </div>

                                <div id="voip-inst-grasshopper" class="voip-inst-card" style="display: none; background-color: #f3e5f5; border-left: 4px solid #6a1b9a; padding: 14px; border-radius: 4px; color: #4a148c; font-size: 12px; line-height: 1.5;">
                                     <strong>Grasshopper Setup Instructions:</strong><br>
                                    1. Log into Grasshopper Admin Portal ➡️ <strong>Settings ➡️ Integrations & Webhooks</strong>.<br>
                                    2. Enable Call Webhook Notifications and paste your dynamic target endpoint URL below.<br>
                                    3. Ensure Voicemail & Call Transcriptions are toggled ON so call data streams automatically to LeadGrove!
                                    <div style="margin-top: 10px; background: white; padding: 10px; border-radius: 6px; border: 1px solid #e1bee7;">
                                        <label style="font-size: 11px; font-weight: bold; color: #4a148c; display: block; margin-bottom: 4px;">⚡ GRASSHOPPER WEBHOOK ENDPOINT URL:</label>
                                        <div class="webhook-input-group">
                                            <input type="text" class="webhook-input" id="voip-endpoint-grasshopper" readonly value="" data-suffix="/webhooks/voip?client_id={active_client_id}">
                                            <button type="button" onclick="copyText('voip-endpoint-grasshopper', 'voip-btn-grasshopper')" id="voip-btn-grasshopper" class="btn-copy"> Copy Webhook URL</button>
                                        </div>
                                    </div>
                                </div>

                                <div id="voip-inst-custom_voip" class="voip-inst-card" style="display: none; background-color: #eceff1; border-left: 4px solid #455a64; padding: 14px; border-radius: 4px; color: #263238; font-size: 12px; line-height: 1.5;">
                                     <strong>Custom VoIP / Other Setup Instructions:</strong><br>
                                    1. In your VoIP provider's developer console or Zapier Integration, configure an HTTP POST Webhook.<br>
                                    2. Set target destination URL to the endpoint below.<br>
                                    3. Ensure call recording URLs and customer phone parameters are included in the payload!
                                    <div style="margin-top: 10px; background: white; padding: 10px; border-radius: 6px; border: 1px solid #cfd8dc;">
                                        <label style="font-size: 11px; font-weight: bold; color: #263238; display: block; margin-bottom: 4px;">⚡ CUSTOM VOIP WEBHOOK ENDPOINT URL:</label>
                                        <div class="webhook-input-group">
                                            <input type="text" class="webhook-input" id="voip-endpoint-custom" readonly value="" data-suffix="/webhooks/voip?client_id={active_client_id}">
                                            <button type="button" onclick="copyText('voip-endpoint-custom', 'voip-btn-custom')" id="voip-btn-custom" class="btn-copy"> Copy Webhook URL</button>
                                        </div>
                                    </div>
                                </div>
                            </div>


                            <div id="sot-email-box" class="conditional-box">
                                <div class="form-group">
                                    <label for="email_provider">Email Provider</label>
                                    <select id="email_provider">
                                        {provider_options}
                                    </select>
                                </div>
                                
                                <!-- Sales Agent Inbox #1 (Primary) -->
                                <div class="form-group">
                                    <div style="display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-bottom: 5px;">
                                        <label for="email_account" style="font-weight: bold; margin-bottom: 0;">Sales Agent Inbox #1 (Primary Email to Monitor)</label>
                                        
                                        <!-- Speech Bubble Tooltip -->
                                        <span class="tooltip-icon">
                                            
                                            <span class="tooltip-text">
                                                As a secondary option, you can also have AI monitor your incoming emails by cc'ing a copy of every email correspondence to:<br>
                                                <strong class="settings-forwarding-email" style="color: #81c784; word-break: break-all;">conversions-{active_client_id}@your-agency.com</strong>
                                            </span>
                                        </span>
                                        
                                        <!-- Check Logs Hover Link -->
                                        <span class="tooltip-icon" style="font-size: 11px; font-weight: bold; margin-left: 5px;">
                                            <a href="javascript:void(0)" style="color: #1a237e; text-decoration: underline;">check logs</a>
                                            <span class="tooltip-text" style="width: 290px;">
                                                <strong>Last 5 Analyzed Emails:</strong><br>
                                                {last_emails_html}
                                            </span>
                                        </span>
                                    </div>
                                    <input type="text" id="email_account" value="{client_data.get("email_account", "") or ""}" placeholder="e.g. agent1@clientcompany.com">
                                    <small style="color: #666; font-size: 11px; margin-top: 4px; display: block;">
                                        Enter primary sales agent email address where form leads or booking receipts arrive.
                                    </small>
                                </div>
                                <div class="form-group" style="margin-top: 12px;">
                                    <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 15px; margin-bottom: 5px;">
                                        <label for="email_app_password" style="font-weight: bold; margin-bottom: 0;">Inbox #1 App Password / IMAP Key</label>
                                        <a href="javascript:void(0)" onclick="openAppPasswordModal()" style="font-size: 12px; color: #1a237e; font-weight: bold; text-decoration: none; display: flex; align-items: center; gap: 4px;">
                                             How to get an App Password?
                                        </a>
                                    </div>
                                    <input type="password" id="email_app_password" value="{client_data.get("email_app_password", "") or ""}" placeholder="e.g. abcd efgh ijkl mnop">
                                </div>

                                <!-- Sales Agent Inbox #2 -->
                                <div id="sot-agent-inbox-2" style="margin-top: 20px; padding-top: 15px; border-top: 1px dashed #ccc; display: {'block' if (client_data.get('email_account_2') or client_data.get('email_app_password_2')) else 'none'};">
                                    <div class="form-group">
                                        <label for="email_account_2" style="font-weight: bold;">Sales Agent Inbox #2 (Optional Email to Monitor)</label>
                                        <input type="text" id="email_account_2" value="{client_data.get("email_account_2", "") or ""}" placeholder="e.g. agent2@clientcompany.com">
                                    </div>
                                    <div class="form-group" style="margin-top: 10px;">
                                        <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 15px; margin-bottom: 5px;">
                                            <label for="email_app_password_2" style="font-weight: bold;">Inbox #2 App Password / IMAP Key</label>
                                            <a href="javascript:void(0)" onclick="openAppPasswordModal()" style="font-size: 12px; color: #1a237e; font-weight: bold; text-decoration: none;"> How to get an App Password?</a>
                                        </div>
                                        <input type="password" id="email_app_password_2" value="{client_data.get("email_app_password_2", "") or ""}" placeholder="e.g. abcd efgh ijkl mnop">
                                    </div>
                                </div>

                                <!-- Sales Agent Inbox #3 -->
                                <div id="sot-agent-inbox-3" style="margin-top: 20px; padding-top: 15px; border-top: 1px dashed #ccc; display: {'block' if (client_data.get('email_account_3') or client_data.get('email_app_password_3')) else 'none'};">
                                    <div class="form-group">
                                        <label for="email_account_3" style="font-weight: bold;">Sales Agent Inbox #3 (Optional Email to Monitor)</label>
                                        <input type="text" id="email_account_3" value="{client_data.get("email_account_3", "") or ""}" placeholder="e.g. agent3@clientcompany.com">
                                    </div>
                                    <div class="form-group" style="margin-top: 10px;">
                                        <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 15px; margin-bottom: 5px;">
                                            <label for="email_app_password_3" style="font-weight: bold;">Inbox #3 App Password / IMAP Key</label>
                                            <a href="javascript:void(0)" onclick="openAppPasswordModal()" style="font-size: 12px; color: #1a237e; font-weight: bold; text-decoration: none;"> How to get an App Password?</a>
                                        </div>
                                        <input type="password" id="email_app_password_3" value="{client_data.get("email_app_password_3", "") or ""}" placeholder="e.g. abcd efgh ijkl mnop">
                                    </div>
                                </div>

                                <!-- Sales Agent Inbox #4 -->
                                <div id="sot-agent-inbox-4" style="margin-top: 20px; padding-top: 15px; border-top: 1px dashed #ccc; display: {'block' if (client_data.get('email_account_4') or client_data.get('email_app_password_4')) else 'none'};">
                                    <div class="form-group">
                                        <label for="email_account_4" style="font-weight: bold;">Sales Agent Inbox #4 (Optional Email to Monitor)</label>
                                        <input type="text" id="email_account_4" value="{client_data.get("email_account_4", "") or ""}" placeholder="e.g. agent4@clientcompany.com">
                                    </div>
                                    <div class="form-group" style="margin-top: 10px;">
                                        <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 15px; margin-bottom: 5px;">
                                            <label for="email_app_password_4" style="font-weight: bold;">Inbox #4 App Password / IMAP Key</label>
                                            <a href="javascript:void(0)" onclick="openAppPasswordModal()" style="font-size: 12px; color: #1a237e; font-weight: bold; text-decoration: none;"> How to get an App Password?</a>
                                        </div>
                                        <input type="password" id="email_app_password_4" value="{client_data.get("email_app_password_4", "") or ""}" placeholder="e.g. abcd efgh ijkl mnop">
                                    </div>
                                </div>

                                <!-- Sales Agent Inbox #5 -->
                                <div id="sot-agent-inbox-5" style="margin-top: 20px; padding-top: 15px; border-top: 1px dashed #ccc; display: {'block' if (client_data.get('email_account_5') or client_data.get('email_app_password_5')) else 'none'};">
                                    <div class="form-group">
                                        <label for="email_account_5" style="font-weight: bold;">Sales Agent Inbox #5 (Optional Email to Monitor)</label>
                                        <input type="text" id="email_account_5" value="{client_data.get("email_account_5", "") or ""}" placeholder="e.g. agent5@clientcompany.com">
                                    </div>
                                    <div class="form-group" style="margin-top: 10px;">
                                        <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 15px; margin-bottom: 5px;">
                                            <label for="email_app_password_5" style="font-weight: bold;">Inbox #5 App Password / IMAP Key</label>
                                            <a href="javascript:void(0)" onclick="openAppPasswordModal()" style="font-size: 12px; color: #1a237e; font-weight: bold; text-decoration: none;"> How to get an App Password?</a>
                                        </div>
                                        <input type="password" id="email_app_password_5" value="{client_data.get("email_app_password_5", "") or ""}" placeholder="e.g. abcd efgh ijkl mnop">
                                    </div>
                                </div>

                                <div style="margin-top: 15px; text-align: left;">
                                    <button type="button" id="btn-add-sales-agent" onclick="addSalesAgentInboxRow()" style="background: #e8eaf6; color: #1a237e; border: 1px solid #c5cae9; padding: 8px 14px; border-radius: 6px; font-weight: bold; font-size: 12px; cursor: pointer; transition: all 0.2s;">
                                        ➕ Add Another Sales Agent Inbox (Up to 5 Inboxes)
                                    </button>
                                </div>
                            </div>

                            <!-- SOT Dynamic Webhook Card (Positioned directly under SOT Selection) -->
                            <!-- CRM webhook (Available if CRM active) -->
                            <div class="webhook-card" id="crm-webhook-card" style="display: none; margin-top: 15px;">
                                <div class="webhook-title" style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px;">
                                    <span id="settings-crm-webhook-title">⚙️ CRM Deal/Lead Webhook</span>
                                    
                                    <!-- Check Logs Hover Link -->
                                    <span class="tooltip-icon" style="font-size: 11px; font-weight: bold; margin-left: auto; cursor: help;">
                                        <a href="javascript:void(0)" style="color: #1a237e; text-decoration: underline;">check logs</a>
                                        <span class="tooltip-text" style="width: 290px;">
                                            <strong>Last 5 Received Payloads:</strong><br>
                                            {last_crm_logs_html}
                                        </span>
                                    </span>
                                </div>
                                <div class="webhook-desc">Paste this dynamic endpoint into your CRM or Zapier workflow to push lead updates to LeadGrove:</div>
                                <div class="webhook-input-group">
                                    <input type="text" class="webhook-input" id="crm-webhook" readonly value="" data-suffix="/webhooks/crm?client_id={active_client_id}">
                                    <button type="button" onclick="copyText('crm-webhook', 'crm-copy-btn')" id="crm-copy-btn" class="btn-copy"> Copy Webhook URL</button>
                                </div>
                            </div>
                            
                            <!-- Billing webhook (Available if Accounting active) -->
                            <div class="webhook-card" id="billing-webhook-card" style="display: none; margin-top: 15px;">
                                <div class="webhook-title" style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px;">
                                    <span id="settings-billing-webhook-title"> QuickBooks / Xero Billing Webhook</span>
                                    
                                    <!-- Check Logs Hover Link -->
                                    <span class="tooltip-icon" style="font-size: 11px; font-weight: bold; margin-left: auto; cursor: help;">
                                        <a href="javascript:void(0)" style="color: #1a237e; text-decoration: underline;">check logs</a>
                                        <span class="tooltip-text" style="width: 290px;">
                                            <strong>Last 5 Received Payments:</strong><br>
                                            {last_billing_logs_html}
                                        </span>
                                    </span>
                                </div>
                                <div class="webhook-desc">Link your paid transaction updates directly using this endpoint to register closed invoice values:</div>
                                <div class="webhook-input-group">
                                    <input type="text" class="webhook-input" id="billing-webhook" readonly value="" data-suffix="/webhooks/billing?client_id={active_client_id}">
                                    <button type="button" onclick="copyText('billing-webhook', 'billing-copy-btn')" id="billing-copy-btn" class="btn-copy"> Copy Webhook URL</button>
                                </div>
                            </div>
                            
                            <!-- Email Forwarder (Available if Email active) -->
                            <div class="webhook-card" id="email-webhook-card" style="display: none; margin-top: 15px;">
                                <div class="webhook-title"> Inbound Invoice & Booking Email</div>
                                <div class="webhook-desc">Set up auto-forwarding from your email inbox to send receipts or booking alerts directly to our system for Claude to audit:</div>
                                <div class="webhook-input-group">
                                    <input type="text" class="webhook-input" id="email-webhook" readonly value="" data-suffix="conversions-{active_client_id}@your-agency.com">
                                    <button type="button" onclick="copyText('email-webhook', 'em-copy-btn')" id="em-copy-btn" class="btn-copy"> Copy Email Address</button>
                                </div>
                            </div>
                            
                            <!-- SECTION 5: Active Webhooks read-only deck -->
                            <div class="section-title" style="margin-top: 30px;"> Live Webhooks & Integration URLs</div>
                            
                            <!-- SECTION A: Google Sheets Export & Ad Platform Scheduled Pulls (Google & Microsoft Ads) -->
                            <div class="webhook-card" id="sheets-export-webhook-card" style="border-left: 4px solid #34A853;">
                                <div class="webhook-title" style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px; color: #1e7e34;">
                                    <span> Section A: Google Sheets Export & Ad Platform Scheduled Pulls (Google & Microsoft Ads)</span>
                                    
                                    <!-- Interactive Speech Bubble Tooltip for Google Sheets & Scheduled Pull Setup -->
                                    <span class="tooltip-icon" style="font-size: 16px; cursor: help; color: #1e7e34; margin-left: auto;">
                                        
                                        <span class="tooltip-text" style="width: 410px; max-width: 88vw; background-color: #ffffff; color: #333333; border: 2px solid #34A853; border-radius: 8px; padding: 14px; box-shadow: 0 8px 24px rgba(0,0,0,0.2); font-size: 12px; line-height: 1.5; bottom: 125%; right: 0; left: auto; margin-left: 0;">
                                            <div style="font-size: 13px; font-weight: bold; color: #1e7e34; margin-bottom: 8px; border-bottom: 1px solid #c8e6c9; padding-bottom: 5px; display: flex; align-items: center; gap: 6px;">
                                                 Google Sheets Export & Ad Platform Scheduled Pull Guide
                                            </div>
                                            
                                            <strong style="color: #1b5e20;">1. Exporting & Editing Data in Google Sheets:</strong>
                                            <ol style="margin: 4px 0 10px 0; padding-left: 18px; color: #444; font-size: 11px; line-height: 1.4;">
                                                <li>Open a Google Sheet (or go to <a href="https://sheets.new" target="_blank" style="color: #1e7e34; font-weight: bold; text-decoration: underline;">sheets.new</a>).</li>
                                                <li>Click cell <strong>A1</strong> and paste this formula:
                                                    <code style="display: block; background: #e8f5e9; color: #1b5e20; border: 1px solid #a5d6a7; padding: 4px 6px; border-radius: 4px; font-family: monospace; font-size: 10px; margin-top: 3px; word-break: break-all;">=IMPORTDATA("https://your-agency-app.onrender.com/feeds/google-conversions.csv?client_id={active_client_id}")</code>
                                                </li>
                                                <li>Google Sheets automatically populates your live conversion data across columns:</li>
                                            </ol>

                                            <!-- Graphic / Visual Layout Diagram -->
                                            <div style="background: #f8f9fa; border: 1px solid #a5d6a7; border-radius: 6px; padding: 6px; margin: 6px 0 12px 0;">
                                                <div style="display: flex; align-items: center; gap: 5px; font-weight: bold; color: #1b5e20; font-size: 10px; margin-bottom: 4px;">
                                                    <span style="background: #34A853; color: white; padding: 1px 5px; border-radius: 3px; font-size: 9px; font-family: monospace;">fx</span>
                                                    =IMPORTDATA("...")
                                                </div>
                                                <table style="width: 100%; border-collapse: collapse; font-size: 9px; text-align: center; border: 1px solid #c8e6c9; background: white;">
                                                    <thead>
                                                        <tr style="background: #e8f5e9; color: #1b5e20;">
                                                            <th style="border: 1px solid #a5d6a7; padding: 2px;">A</th>
                                                            <th style="border: 1px solid #a5d6a7; padding: 2px;">B</th>
                                                            <th style="border: 1px solid #a5d6a7; padding: 2px;">C</th>
                                                            <th style="border: 1px solid #a5d6a7; padding: 2px;">D</th>
                                                            <th style="border: 1px solid #a5d6a7; padding: 2px;">E</th>
                                                        </tr>
                                                    </thead>
                                                    <tbody>
                                                        <tr style="color: #555; background: #fafafa; font-weight: bold;">
                                                            <td style="border: 1px solid #e0e0e0; padding: 2px;">GCLID / ID</td>
                                                            <td style="border: 1px solid #e0e0e0; padding: 2px;">Name</td>
                                                            <td style="border: 1px solid #e0e0e0; padding: 2px;">Time</td>
                                                            <td style="border: 1px solid #e0e0e0; padding: 2px;">Value</td>
                                                            <td style="border: 1px solid #e0e0e0; padding: 2px;">Currency</td>
                                                        </tr>
                                                        <tr style="color: #2e7d32;">
                                                            <td style="border: 1px solid #e0e0e0; padding: 2px;">EAIaIQ...</td>
                                                            <td style="border: 1px solid #e0e0e0; padding: 2px;">Offline Sale</td>
                                                            <td style="border: 1px solid #e0e0e0; padding: 2px;">2026-09-09...</td>
                                                            <td style="border: 1px solid #e0e0e0; padding: 2px;">450.00</td>
                                                            <td style="border: 1px solid #e0e0e0; padding: 2px;">USD</td>
                                                        </tr>
                                                    </tbody>
                                                </table>
                                            </div>

                                            <strong style="color: #1b5e20;">2. Sending Data from Google Sheet to Ad Platforms via Scheduled Pull:</strong>
                                            <ul style="margin: 4px 0 0 0; padding-left: 18px; color: #444; font-size: 11px; line-height: 1.4; list-style-type: disc;">
                                                <li style="margin-bottom: 6px;"><strong>Google Ads Scheduled Pull:</strong> Go to <strong>Goals ➡️ Conversions ➡️ Uploads ➡️ Schedules</strong>, click <strong>+</strong>, select <strong>Google Sheets</strong> as Source, choose your Google Sheet URL, set frequency to <strong>Every 24 hours</strong>, and save!</li>
                                                <li><strong>Microsoft (Bing) Ads Scheduled Pull:</strong> Go to <strong>Tools ➡️ Conversion Goals ➡️ Offline Conversions ➡️ Schedules</strong>, click <strong>Create Schedule</strong>, select <strong>Google Sheets</strong>, link your Sheet URL, set daily fetch, and save!</li>
                                            </ul>
                                        </span>
                                    </span>
                                </div>
                                <div class="webhook-desc">Use this live feed URL in Google Sheets via <code>=IMPORTDATA("...")</code> to view and edit conversion data, then configure <strong>Google Ads & Microsoft Ads</strong> to automatically pull data from that Google Sheet on a recurring schedule!</div>
                                <div class="webhook-input-group">
                                    <input type="text" class="webhook-input" id="sheets-feed-webhook" readonly value="" data-suffix="/feeds/google-conversions.csv?client_id={active_client_id}">
                                    <button type="button" onclick="copyText('sheets-feed-webhook', 'sheets-feed-copy-btn')" id="sheets-feed-copy-btn" class="btn-copy" style="background-color: #34A853;"> Copy Google Sheets Feed URL</button>
                                </div>
                            </div>

                            <!-- SECTION C: Zapier Multi-Network Conversions API Export (TikTok, X, Pinterest, Snapchat, LinkedIn & ChatGPT Ads) -->
                            <div class="webhook-card" id="zapier-capi-export-card" style="border-left: 4px solid #FF4F00;">
                                <div class="webhook-title" style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px; color: #E65100;">
                                    <span>⚡ Section C: Zapier Multi-Network Conversions API Export (Reddit, TikTok, X, Pinterest, Snapchat, LinkedIn & ChatGPT Ads)</span>
                                    
                                    <!-- Interactive Speech Bubble Tooltip for Zapier CAPI Export Setup -->
                                    <span class="tooltip-icon" style="font-size: 16px; cursor: help; color: #FF4F00; margin-left: auto;">
                                        
                                        <span class="tooltip-text" style="width: 420px; max-width: 88vw; background-color: #ffffff; color: #333333; border: 2px solid #FF4F00; border-radius: 8px; padding: 14px; box-shadow: 0 8px 24px rgba(0,0,0,0.2); font-size: 12px; line-height: 1.5; bottom: 125%; right: 0; left: auto; margin-left: 0;">
                                            <div style="font-size: 13px; font-weight: bold; color: #E65100; margin-bottom: 8px; border-bottom: 1px solid #ffe0b2; padding-bottom: 5px; display: flex; align-items: center; gap: 6px;">
                                                ⚡ Zapier Multi-Network CAPI Export Guide
                                            </div>
                                            
                                            <p style="margin: 0 0 8px 0; color: #555; font-size: 11px;">Send LeadGrove conversion events to TikTok, X, Pinterest, Snapchat, LinkedIn, and ChatGPT Ads via your own Zapier account!</p>

                                            <strong style="color: #E65100;">1. Setting Up the Zapier Trigger:</strong>
                                            <ol style="margin: 4px 0 10px 0; padding-left: 18px; color: #444; font-size: 11px; line-height: 1.4;">
                                                <li>In Zapier, click <strong>Create Zap</strong>.</li>
                                                <li><strong>Trigger Options:</strong> Choose <strong>Google Sheets (New or Updated Row)</strong> linked to your Section A feed Sheet, OR choose <strong>Webhooks by Zapier (Catch Hook)</strong> using the LeadGrove endpoint below.</li>
                                            </ol>

                                            <strong style="color: #E65100;">2. Ad Platform CAPI Action & Click ID Mapping:</strong>
                                            <p style="margin: 4px 0 6px 0; color: #444; font-size: 11px;">Add an Action step in Zapier for your target ad network and map LeadGrove's click ID columns:</p>

                                            <!-- Visual Graphic CAPI Mapping Table -->
                                            <div style="background: #fff8e1; border: 1px solid #ffe0b2; border-radius: 6px; padding: 6px; margin: 6px 0 10px 0;">
                                                <table style="width: 100%; border-collapse: collapse; font-size: 9px; text-align: left; border: 1px solid #ffe0b2; background: white;">
                                                    <thead>
                                                        <tr style="background: #fff3e0; color: #e65100; font-weight: bold;">
                                                            <th style="border: 1px solid #ffe0b2; padding: 3px;">Ad Platform</th>
                                                            <th style="border: 1px solid #ffe0b2; padding: 3px;">Click ID Field</th>
                                                            <th style="border: 1px solid #ffe0b2; padding: 3px;">Zapier CAPI Action</th>
                                                        </tr>
                                                    </thead>
                                                    <tbody>
                                                        <tr>
                                                            <td style="border: 1px solid #eee; padding: 3px; font-weight: bold;"> TikTok Ads</td>
                                                            <td style="border: 1px solid #eee; padding: 3px; font-family: monospace; color: #c62828;">ttclid</td>
                                                            <td style="border: 1px solid #eee; padding: 3px;">TikTok Offline Events</td>
                                                        </tr>
                                                        <tr style="background: #fafafa;">
                                                            <td style="border: 1px solid #eee; padding: 3px; font-weight: bold;"> LinkedIn Ads</td>
                                                            <td style="border: 1px solid #eee; padding: 3px; font-family: monospace; color: #1565c0;">li_fat_id</td>
                                                            <td style="border: 1px solid #eee; padding: 3px;">LinkedIn Conversions</td>
                                                        </tr>
                                                        <tr>
                                                            <td style="border: 1px solid #eee; padding: 3px; font-weight: bold;"> Pinterest Ads</td>
                                                            <td style="border: 1px solid #eee; padding: 3px; font-family: monospace; color: #b71c1c;">pin_clid</td>
                                                            <td style="border: 1px solid #eee; padding: 3px;">Pinterest Conversions</td>
                                                        </tr>
                                                        <tr style="background: #fafafa;">
                                                            <td style="border: 1px solid #eee; padding: 3px; font-weight: bold;"> Snapchat Ads</td>
                                                            <td style="border: 1px solid #eee; padding: 3px; font-family: monospace; color: #f57f17;">scclid</td>
                                                            <td style="border: 1px solid #eee; padding: 3px;">Snapchat CAPI Event</td>
                                                        </tr>
                                                        <tr>
                                                            <td style="border: 1px solid #eee; padding: 3px; font-weight: bold;"> X (Twitter) Ads</td>
                                                            <td style="border: 1px solid #eee; padding: 3px; font-family: monospace; color: #333;">twclid</td>
                                                            <td style="border: 1px solid #eee; padding: 3px;">X Ads Conversion Event</td>
                                                        </tr>
                                                        <tr style="background: #fafafa;">
                                                            <td style="border: 1px solid #eee; padding: 3px; font-weight: bold;"> ChatGPT Ads</td>
                                                            <td style="border: 1px solid #eee; padding: 3px; font-family: monospace; color: #00796b;">gptclid</td>
                                                            <td style="border: 1px solid #eee; padding: 3px;">Webhooks POST Custom</td>
                                                        </tr>
                                                        <tr style="color: #FF4500;">
                                                            <td style="border: 1px solid #eee; padding: 3px; font-weight: bold;"> Reddit Ads</td>
                                                            <td style="border: 1px solid #eee; padding: 3px; font-family: monospace; color: #FF4500;">rdt_cid</td>
                                                            <td style="border: 1px solid #eee; padding: 3px;">Reddit Conversions API</td>
                                                        </tr>
                                                    </tbody>
                                                </table>
                                            </div>

                                            <p style="margin: 0; font-size: 10px; color: #666; font-style: italic;"> LeadGrove automatically includes hashed customer emails and phone numbers for high CAPI match rates!</p>
                                        </span>
                                    </span>
                                </div>
                                <div class="webhook-desc">Export LeadGrove conversion data to your personal Zapier account via Webhooks or Google Sheets triggers, and automatically forward conversion signals to <strong>TikTok Ads, LinkedIn, Pinterest, Snapchat, X (Twitter), and ChatGPT Ads</strong> via their Conversion APIs!</div>
                                <div class="webhook-input-group">
                                    <input type="text" class="webhook-input" id="zapier-export-webhook" readonly value="" data-suffix="/webhooks/crm?client_id={active_client_id}">
                                    <button type="button" onclick="copyText('zapier-export-webhook', 'zapier-export-copy-btn')" id="zapier-export-copy-btn" class="btn-copy" style="background-color: #FF4F00;"> Copy Zapier Export Webhook URL</button>
                                </div>
                            </div>

                            <!-- SECTION B: Direct HTTP Scheduled Imports (Google Ads, Microsoft Ads & Meta Ads) -->
                            <div class="webhook-card" id="direct-http-feed-webhook-card" style="border-left: 4px solid #1a237e;">
                                <div class="webhook-title" style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px; color: #1a237e;">
                                    <span>⚡ Section B: Direct HTTP Scheduled Imports (Google Ads, Microsoft Ads & Meta Ads)</span>
                                    
                                    <!-- Interactive Speech Bubble Tooltip for Direct HTTP Imports -->
                                    <span class="tooltip-icon" style="font-size: 16px; cursor: help; color: #1a237e; margin-left: auto;">
                                        
                                        <span class="tooltip-text" style="width: 410px; max-width: 88vw; background-color: #ffffff; color: #333333; border: 2px solid #1a237e; border-radius: 8px; padding: 14px; box-shadow: 0 8px 24px rgba(0,0,0,0.2); font-size: 12px; line-height: 1.5; bottom: 125%; right: 0; left: auto; margin-left: 0;">
                                            <div style="font-size: 13px; font-weight: bold; color: #1a237e; margin-bottom: 8px; border-bottom: 1px solid #c5cae9; padding-bottom: 5px; display: flex; align-items: center; gap: 6px;">
                                                ⚡ Direct HTTP Scheduled Import Instructions
                                            </div>
                                            
                                            <strong style="color: #1a237e;">1. Google Ads Direct HTTP Import:</strong>
                                            <ol style="margin: 4px 0 8px 0; padding-left: 18px; color: #444; font-size: 11px; line-height: 1.4;">
                                                <li>In Google Ads, navigate to <strong>Goals ➡️ Conversions ➡️ Uploads ➡️ Schedules</strong>.</li>
                                                <li>Click <strong>+ (Plus)</strong>, select <strong>HTTPS</strong> as Source, and paste your live LeadGrove URL below.</li>
                                                <li>Set frequency to <strong>Every 24 hours</strong> and save! <em>(Use the Adjustments URL under the Adjustments tab for retractions).</em></li>
                                            </ol>

                                            <strong style="color: #00A4EF;">2. Microsoft Ads Direct HTTP Import:</strong>
                                            <ol style="margin: 4px 0 8px 0; padding-left: 18px; color: #444; font-size: 11px; line-height: 1.4;">
                                                <li>In Microsoft Advertising, go to <strong>Tools ➡️ Conversion Goals ➡️ Offline Conversions ➡️ Schedules</strong>.</li>
                                                <li>Click <strong>Create Schedule</strong>, select <strong>HTTPS / Feed URL</strong>, paste your LeadGrove feed URL, set daily fetch, and save!</li>
                                            </ol>

                                            <strong style="color: #1877F2;">3. Meta (Facebook) Ads Scheduled Feed Import:</strong>
                                            <ol style="margin: 4px 0 0 0; padding-left: 18px; color: #444; font-size: 11px; line-height: 1.4;">
                                                <li>In Meta Events Manager, go to <strong>Data Sources ➡️ Add Events ➡️ Offline Events / Feed URL</strong>.</li>
                                                <li>Select <strong>Scheduled Feed / Feed URL</strong>, paste your LeadGrove feed URL, map columns (<code>fbclid</code>, event name, timestamp, value), and save for 24/7 automated sync!</li>
                                            </ol>
                                        </span>
                                    </span>
                                </div>
                                <div class="webhook-desc">Directly fetch live conversion feeds straight from LeadGrove via HTTP scheduled imports in <strong>Google Ads, Microsoft Ads, and Meta Ads</strong> without needing an intermediate spreadsheet!</div>
                                <div class="webhook-input-group" style="margin-bottom: 10px;">
                                    <input type="text" class="webhook-input" id="direct-http-conversions-webhook" readonly value="" data-suffix="/feeds/google-conversions.csv?client_id={active_client_id}">
                                    <button type="button" onclick="copyText('direct-http-conversions-webhook', 'direct-http-conv-copy-btn')" id="direct-http-conv-copy-btn" class="btn-copy" style="background-color: #1a237e;"> Copy Standard Conversions HTTP URL</button>
                                </div>
                                <div class="webhook-input-group">
                                    <input type="text" class="webhook-input" id="direct-http-adjustments-webhook" readonly value="" data-suffix="/feeds/google-adjustments.csv?client_id={active_client_id}">
                                    <button type="button" onclick="copyText('direct-http-adjustments-webhook', 'direct-http-adj-copy-btn')" id="direct-http-adj-copy-btn" class="btn-copy" style="background-color: #37474F;"> Copy Adjustments Feed HTTP URL</button>
                                </div>
                            </div>
                            
                            <!-- Call Tracking webhook (Dynamic based on provider) -->
                            <div class="webhook-card">
                                <div class="webhook-title" id="settings-call-webhook-title">{webhook_card_title}</div>
                                <div class="webhook-desc" id="settings-call-webhook-desc">{webhook_card_desc}</div>
                                <div class="webhook-input-group">
                                    <input type="text" class="webhook-input" id="callrail-webhook" readonly value="" data-suffix="{webhook_suffix}">
                                    <button type="button" onclick="copyText('callrail-webhook', 'cr-copy-btn')" id="cr-copy-btn" class="btn-copy"> Copy</button>
                                </div>
                            </div>
                            

                        </div>

                    </div>

                    <!-- App Password Modal Overlay -->
                    <div id="app-password-modal" class="modal-overlay" style="display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background-color: rgba(0,0,0,0.5); z-index: 10000; align-items: center; justify-content: center;">
                        <div class="modal-card" style="background: white; border-radius: 12px; box-shadow: 0 10px 30px rgba(0,0,0,0.2); max-width: 550px; width: 90%; text-align: left; overflow: hidden; display: flex; flex-direction: column;">
                            <!-- Header -->
                            <div class="modal-header" style="background: #1a237e; color: white; padding: 16px 20px; display: flex; justify-content: space-between; align-items: center;">
                                <h3 style="margin: 0; font-size: 18px; color: #1a237e; display: flex; align-items: center; gap: 8px;">
                                     Generate a Secure App Password
                                </h3>
                                <span class="modal-close" onclick="closeAppPasswordModal()" style="font-size: 24px; font-weight: bold; cursor: pointer; color: white; opacity: 0.8;">&times;</span>
                            </div>

                            <!-- Body -->
                            <div class="modal-body" style="padding: 20px; max-height: 70vh; overflow-y: auto; text-align: left;">
                                <p style="margin-top: 0; font-size: 13px; line-height: 1.5; color: #555;">
                                    For security, modern email networks require a <strong>16-character App Password</strong> rather than your standard account login password. This restricts our AI's access strictly to reading incoming booking emails via IMAP.
                                </p>

                                <!-- Provider Tabs -->
                                <div style="display: flex; border-bottom: 2px solid #e0e0e0; margin-bottom: 15px; flex-wrap: wrap;">
                                    <button type="button" id="tab-btn-google" class="tab-btn active" onclick="switchModalTab('google')">
                                         Google Workspace / Gmail
                                    </button>
                                    <button type="button" id="tab-btn-ms" class="tab-btn" onclick="switchModalTab('ms')">
                                         Microsoft 365 / Outlook
                                    </button>
                                    <button type="button" id="tab-btn-imap" class="tab-btn" onclick="switchModalTab('imap')">
                                         Custom IMAP / cPanel / Other
                                    </button>
                                </div>

                                <!-- Tab Content: Google -->
                                <div id="modal-tab-google" class="tab-content">
                                    <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; color: #333; margin: 0;">
                                        <li style="margin-bottom: 8px;">Go to your <a href="https://myaccount.google.com/security" target="_blank" style="color: #1a237e; font-weight: bold; text-decoration: none;">Google Account Security Panel</a>.</li>
                                        <li style="margin-bottom: 8px;">Ensure <strong>2-Step Verification</strong> is active under "How you sign in to Google".</li>
                                        <li style="margin-bottom: 8px;">Type <strong>"App Passwords"</strong> in Google's search bar, or scroll to the bottom of 2-Step Verification and click <strong>App Passwords</strong>.</li>
                                        <li style="margin-bottom: 8px;">Enter a custom name (e.g., <code>LeadGroove Conversion Engine</code>) and click <strong>Create</strong>.</li>
                                        <li style="margin-bottom: 8px;">Copy the <strong>16-character code</strong> inside Google's yellow box, strip any spaces, and enter it as your password!</li>
                                    </ol>
                                </div>

                                <!-- Tab Content: Microsoft -->
                                <div id="modal-tab-ms" class="tab-content" style="display: none;">
                                    <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; color: #333; margin: 0;">
                                        <li style="margin-bottom: 8px;">Go to your <a href="https://mysignins.microsoft.com/security-info" target="_blank" style="color: #1a237e; font-weight: bold; text-decoration: none;">Microsoft Security Info Page</a>.</li>
                                        <li style="margin-bottom: 8px;">Click the <strong>+ Add sign-in method</strong> button at the top.</li>
                                        <li style="margin-bottom: 8px;">Select <strong>App Password</strong> from the dropdown menu and click <strong>Add</strong>.</li>
                                        <li style="margin-bottom: 8px;">Name it (e.g., <code>LeadGroove Offline Tracker</code>) and click <strong>Next</strong>.</li>
                                        <li style="margin-bottom: 8px;">Copy the <strong>16-character password key</strong> immediately before closing the confirmation window.</li>
                                    </ol>
                                </div>

                                <!-- Tab Content: Custom IMAP / Other Providers -->
                                <div id="modal-tab-imap" class="tab-content" style="display: none;">
                                    <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; color: #333; margin: 0;">
                                        <li style="margin-bottom: 8px;">Log into your hosting control panel or email admin settings (e.g., <strong>cPanel, Webmail, Yahoo, iCloud, Fastmail, Zoho Mail, GoDaddy, or Namecheap</strong>).</li>
                                        <li style="margin-bottom: 8px;">Navigate to <strong>Email Accounts ➡️ Security / Two-Factor Authentication</strong> or <strong>App Passwords</strong>.</li>
                                        <li style="margin-bottom: 8px;">If your email provider enforces 2FA (e.g., Yahoo, Apple iCloud, Zoho), create a dedicated <strong>App Password</strong> named <code>LeadGroove IMAP Sync</code>.</li>
                                        <li style="margin-bottom: 8px;">If your server uses standard IMAP authentication (e.g., cPanel, Webmail, self-hosted server), use your standard account email password.</li>
                                        <li style="margin-bottom: 8px;">Ensure IMAP access is enabled on port <strong>993 (SSL/TLS)</strong> or port <strong>143 (STARTTLS)</strong>.</li>
                                    </ol>
                                </div>

                                <!-- Security Footnote -->
                                <div style="background-color: #f1f8e9; border-left: 4px solid #2e7d32; padding: 12px; margin-top: 20px; border-radius: 4px;">
                                    <p style="margin: 0; font-size: 11px; line-height: 1.4; color: #1b5e20;">
                                         <strong>Strict Privacy Guard:</strong> This code grants read-only IMAP credentials. It does not access your emails, calendars, or account dashboards. You can revoke it instantly at any time in your security settings.
                                    </p>
                                </div>
                            </div>

                            <!-- Footer -->
                            <div class="modal-footer" style="padding: 15px 20px; border-top: 1px solid #eaeaea; background-color: #f8f9fa; display: flex; justify-content: flex-end; border-bottom-left-radius: 12px; border-bottom-right-radius: 12px;">
                                <button type="button" class="btn-modal-close" onclick="closeAppPasswordModal()">Got It, Thanks!</button>
                            </div>
                        </div>
                    </div>
                    
                    <!-- Form Buttons -->
                    <div style="display:flex; justify-content: space-between; align-items: center; margin-top: 40px; border-top: 1px solid #eaeaea; padding-top: 20px;">
                        <a href="/dashboard?client_id={active_client_id}" class="btn-cancel">⬅️ Return to Dashboard</a>
                        {save_btn_html}
                    </div>
                    </fieldset>
                </form>

                <hr style="border: 0; height: 1px; background: #eaeaea; margin: 40px 0;">

                <div class="section-title" style="margin-bottom: 20px; color: #1a237e; font-size: 20px; font-weight: bold; display: flex; align-items: center; gap: 8px;">
                     Account Collaborators & Invitations
                </div>
                <p style="color: #666; font-size: 13px; margin-top: -10px; margin-bottom: 20px;">
                    Invite and manage team members who can access this client's tracking dashboard.
                </p>

                <!-- Table of Active Users & Pending Invites -->
                <div style="background: #fafafa; border: 1px solid #eaeaea; border-radius: 8px; padding: 20px; margin-bottom: 30px;">
                    <h3 style="margin-top: 0; color: #1a237e; font-size: 15px; border-bottom: 1px solid #eaeaea; padding-bottom: 10px;">Active Collaborators & Pending Invites</h3>
                    <table style="width: 100%; border-collapse: collapse; font-size: 13px; text-align: left;">
                        <thead>
                            <tr style="border-bottom: 2px solid #eaeaea; color: #495057;">
                                <th style="padding: 10px;">Email / User</th>
                                <th style="padding: 10px;">Access Level</th>
                                <th style="padding: 10px;">Status</th>
                                <th style="padding: 10px;">Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            {collaborator_rows_html}
                        </tbody>
                    </table>
                </div>

                <!-- Configuration Change History Log Table -->
                <hr style="border: 0; height: 1px; background: #eaeaea; margin: 40px 0;">

                <div class="section-title" style="margin-bottom: 20px; color: #1a237e; font-size: 20px; font-weight: bold; display: flex; align-items: center; gap: 8px;">
                     Configuration Change History Log
                </div>
                <p style="color: #666; font-size: 13px; margin-top: -10px; margin-bottom: 20px;">
                    Audit trail of all updates and changes made within this client's configuration section.
                </p>

                <div style="background: #fafafa; border: 1px solid #eaeaea; border-radius: 8px; padding: 20px; margin-bottom: 30px; max-height: 400px; overflow-y: auto;">
                    <table style="width: 100%; border-collapse: collapse; font-size: 13px; text-align: left;">
                        <thead>
                            <tr style="border-bottom: 2px solid #eaeaea; color: #495057;">
                                <th style="padding: 10px;">Date & Time</th>
                                <th style="padding: 10px;">Updated Feature</th>
                                <th style="padding: 10px;">Old Option</th>
                                <th style="padding: 10px;">New Option Selected</th>
                                <th style="padding: 10px;">Updated By</th>
                            </tr>
                        </thead>
                        <tbody>
                            {change_history_rows_html}
                        </tbody>
                    </table>
                </div>

                <!-- Invite Form -->
                <div id="invite-box" style="background: #e8eaf6; border: 1px solid #c5cae9; border-radius: 8px; padding: 20px; display: {invite_form_display};">
                    <h3 style="margin-top: 0; color: #1a237e; font-size: 15px;">✉️ Invite a New Collaborator</h3>
                    <div id="invite-alert" class="alert" style="margin-bottom: 15px; padding: 10px; font-size: 12px;"></div>
                    
                    <div style="display: flex; gap: 20px; flex-wrap: wrap; margin-bottom: 15px; align-items: flex-end;">
                        <div class="form-group" style="flex: 1; min-width: 250px; margin-bottom: 10px;">
                            <label for="invite_email" style="font-weight: bold; font-size: 11px; margin-bottom: 8px; display: block;">RECIPIENT EMAIL ADDRESS</label>
                            <input type="email" id="invite_email" placeholder="e.g. employee@clientcompany.com" style="width: 100%; padding: 10px; border-radius: 6px; border: 1px solid #ced4da; font-size: 13px; background: white; box-sizing: border-box;">
                        </div>
                        <div class="form-group" style="flex: 1; min-width: 200px; margin-bottom: 10px;">
                            <label for="invite_role" style="font-weight: bold; font-size: 11px; margin-bottom: 8px; display: block;">ACCESS LEVEL</label>
                            <select id="invite_role" style="width: 100%; padding: 10px; border-radius: 6px; border: 1px solid #ced4da; font-size: 13px; background: white; height: 38px; box-sizing: border-box; cursor: pointer;">
                                <option value="read">Read-Only (Viewer)</option>
                                <option value="full">Full Function (Manager)</option>
                            </select>
                        </div>
                    </div>

                    <button type="button" onclick="generateInvitationLink()" class="btn-submit" style="margin-top: 15px; width: auto; font-size: 13px; padding: 10px 20px; background-color: #2e7d32;">
                        ✉️ Generate Invitation Link
                    </button>
                    
                    <div id="invite-link-container" style="display: none; margin-top: 20px; background: white; padding: 15px; border-radius: 6px; border: 1px dashed #2e7d32;">
                        <span style="font-weight: bold; color: #2e7d32; font-size: 13px; display: block; margin-bottom: 5px;"> Invitation Link Generated!</span>
                        <p style="color: #555; font-size: 12px; margin: 0 0 10px 0;">Copy this link and send it directly to your collaborator to register:</p>
                        <div style="display: flex; gap: 8px;">
                            <input type="text" id="invite-url-output" readonly style="flex: 1; padding: 8px; border-radius: 4px; border: 1px solid #ced4da; font-family: monospace; font-size: 11px; background-color: #f8f9fa;">
                            <button type="button" onclick="copyText('invite-url-output', 'invite-copy-btn')" id="invite-copy-btn" class="btn-copy" style="margin: 0; width: auto; font-size: 12px; background-color: #2e7d32; color: white; padding: 0 12px; border: none; border-radius: 4px; cursor: pointer;"> Copy Link</button>
                        </div>
                    </div>
                </div>
            </div>
            
            <script>

                
                function addSalesAgentInboxRow() {{
                    for (let i = 2; i <= 5; i++) {{
                        const row = document.getElementById('sot-agent-inbox-' + i);
                        if (row && (row.style.display === 'none' || getComputedStyle(row).display === 'none')) {{
                            row.style.display = 'block';
                            const emailInput = document.getElementById('email_account_' + i);
                            if (emailInput) emailInput.focus();
                            break;
                        }}
                    }}
                    let hiddenCount = 0;
                    for (let i = 2; i <= 5; i++) {{
                        const row = document.getElementById('sot-agent-inbox-' + i);
                        if (row && (row.style.display === 'none' || getComputedStyle(row).display === 'none')) {{
                            hiddenCount++;
                        }}
                    }}
                    if (hiddenCount === 0) {{
                        const btn = document.getElementById('btn-add-sales-agent');
                        if (btn) btn.style.display = 'none';
                    }}
                }}

                function addSalesAgentInboxRowWiz() {{
                    for (let i = 2; i <= 5; i++) {{
                        const row = document.getElementById('wiz-agent-inbox-' + i);
                        if (row && (row.style.display === 'none' || getComputedStyle(row).display === 'none')) {{
                            row.style.display = 'block';
                            const emailInput = document.getElementById('email_account_' + i);
                            if (emailInput) emailInput.focus();
                            break;
                        }}
                    }}
                    let hiddenCount = 0;
                    for (let i = 2; i <= 5; i++) {{
                        const row = document.getElementById('wiz-agent-inbox-' + i);
                        if (row && (row.style.display === 'none' || getComputedStyle(row).display === 'none')) {{
                            hiddenCount++;
                        }}
                    }}
                    if (hiddenCount === 0) {{
                        const btn = document.getElementById('wiz-btn-add-sales-agent');
                        if (btn) btn.style.display = 'none';
                    }}
                }}

                function openAppPasswordModal() {{
                    const providerSelect = document.getElementById('email_provider');
                    const selectedProvider = providerSelect ? providerSelect.value : 'gmail';
                    if (selectedProvider === 'outlook') {{
                        switchModalTab('ms');
                    }} else if (selectedProvider === 'custom_imap') {{
                        switchModalTab('imap');
                    }} else {{
                        switchModalTab('google');
                    }}
                    document.getElementById('app-password-modal').style.display = 'flex';
                }}
                
                function closeAppPasswordModal() {{
                    document.getElementById('app-password-modal').style.display = 'none';
                }}
                
                function switchModalTab(provider) {{
                    const btnG = document.getElementById('tab-btn-google');
                    const btnM = document.getElementById('tab-btn-ms');
                    const btnI = document.getElementById('tab-btn-imap');
                    const tabG = document.getElementById('modal-tab-google');
                    const tabM = document.getElementById('modal-tab-ms');
                    const tabI = document.getElementById('modal-tab-imap');
                    
                    if (btnG) btnG.classList.remove('active');
                    if (btnM) btnM.classList.remove('active');
                    if (btnI) btnI.classList.remove('active');
                    
                    if (tabG) tabG.style.display = 'none';
                    if (tabM) tabM.style.display = 'none';
                    if (tabI) tabI.style.display = 'none';
                    
                    if (provider === 'google') {{
                        if (btnG) btnG.classList.add('active');
                        if (tabG) tabG.style.display = 'block';
                    }} else if (provider === 'ms') {{
                        if (btnM) btnM.classList.add('active');
                        if (tabM) tabM.style.display = 'block';
                    }} else if (provider === 'imap') {{
                        if (btnI) btnI.classList.add('active');
                        if (tabI) tabI.style.display = 'block';
                    }}
                }}

                // Close modal if user clicks outside of the card
                window.addEventListener('click', (e) => {{
                    const overlay = document.getElementById('app-password-modal');
                    if (e.target === overlay) {{
                        closeAppPasswordModal();
                    }}
                }});

                // Auto-populate the active hostname into webhook input fields
                                window.addEventListener('DOMContentLoaded', () => {{
                    const origin = window.location.origin;
                    const host = window.location.host;
                    const emailDomain = host.includes('localhost') ? 'your-agency.com' : host.replace('www.', '').split(':')[0];
                    
                    document.querySelectorAll('.webhook-input').forEach(input => {{
                        const suffix = input.getAttribute('data-suffix');
                        if (suffix && suffix.startsWith('conversions-')) {{
                            input.value = `conversions-{active_client_id}@${{emailDomain}}`;
                        }} else if (suffix) {{
                            input.value = origin + suffix;
                        }}
                    }});
                    document.querySelectorAll('.monthly-forwarding-email').forEach(el => {{
                        el.innerText = `conversions-{active_client_id}@${{emailDomain}}`;
                    }});
                    
                    // Dynamically replace placeholder origins inside code instruction blocks
                    document.querySelectorAll('code').forEach(el => {{
                        if (el.innerText.includes('https://your-agency-app.onrender.com')) {{
                            el.innerText = el.innerText.replaceAll('https://your-agency-app.onrender.com', origin);
                        }}
                    }});
                    
                    // Inject dynamic forwarding email domain inside Settings page tooltip
                    const forwardingLabel = document.querySelector('.settings-forwarding-email');
                    if (forwardingLabel) {{
                        forwardingLabel.innerText = `conversions-{active_client_id}@${{emailDomain}}`;
                    }}
                    
                    toggleSOTFields();
                    toggleSettingsCallTrackingFields();
                    updateFunnelAdPlatforms();
                }});

                function updateFunnelAdPlatforms() {{
                    const spoutEl = document.getElementById('funnel-ad-platforms-spout');
                    if (!spoutEl) return;
                    
                    const platforms = [];
                    
                    const gads = document.getElementById('google_ads_customer_id');
                    if (gads && gads.value.trim()) platforms.push("Google Ads");
                    
                    const fb = document.getElementById('facebook_ads_id');
                    if (fb && fb.value.trim()) platforms.push("Meta CAPI");
                    
                    const ms = document.getElementById('microsoft_ads_id');
                    if (ms && ms.value.trim()) platforms.push("Bing Ads");
                    
                    const li = document.getElementById('linkedin_ads_id');
                    if (li && li.value.trim()) platforms.push("LinkedIn Ads");
                    
                    const tt = document.getElementById('tiktok_ads_id');
                    if (tt && tt.value.trim()) platforms.push("TikTok Ads");
                    
                    const tw = document.getElementById('twitter_ads_id');
                    if (tw && tw.value.trim()) platforms.push("X (Twitter) Ads");
                    
                    const pin = document.getElementById('pinterest_ads_id');
                    if (pin && pin.value.trim()) platforms.push("Pinterest Ads");
                    
                    const sc = document.getElementById('snapchat_ads_id');
                    if (sc && sc.value.trim()) platforms.push("Snapchat Ads");
                    
                    const gpt = document.getElementById('chatgpt_ads_id');
                    if (gpt && gpt.value.trim()) platforms.push("ChatGPT Ads");
                    
                    const rdt = document.getElementById('reddit_ads_id');
                    if (rdt && rdt.value.trim()) platforms.push("Reddit Ads");
                    
                    let platText = "Google Ads, Meta CAPI & Bing Conversion Uploads";
                    if (platforms.length > 0) {{
                        if (platforms.length === 1) {{
                            platText = platforms[0] + " Conversion Uploads";
                        }} else if (platforms.length === 2) {{
                            platText = platforms[0] + " & " + platforms[1] + " Conversion Uploads";
                        }} else {{
                            platText = platforms.slice(0, -1).join(", ") + " & " + platforms[platforms.length - 1] + " Conversion Uploads";
                        }}
                    }}
                    spoutEl.innerText = platText;

                    // Update Stage 4 Card
                    const stage4Desc = document.getElementById('stage4-card-platforms');
                    if (stage4Desc) {{
                        stage4Desc.innerText = platText;
                    }}
                }}


                
                function updateFunnelTier1Source() {{
                    const tier1El = document.getElementById('funnel-tier1-source');
                    if (!tier1El) return;
                    
                    const providerSelect = document.getElementById('call_tracking_provider');
                    const providerVal = providerSelect ? providerSelect.value : 'callrail';
                    
                    let providerName = "CallRail";
                    if (providerVal === 'calltrackingmetrics') {{
                        providerName = "CallTrackingMetrics";
                    }} else if (providerVal === 'whatconverts') {{
                        providerName = "WhatConverts";
                    }}
                    
                    const lgRadio = document.querySelector('input[name="lead_gen_method"]:checked');
                    const lgVal = lgRadio ? lgRadio.value : 'both';
                    
                    let trigText = 'Call Completed / Form POST';
                    if (lgVal === 'phone') {{
                        tier1El.innerHTML = 'Source: <strong id="funnel-tier1-provider">' + providerName + ' Call Tracking Only</strong>';
                        trigText = 'Call Completed Webhook';
                    }} else if (lgVal === 'form') {{
                        tier1El.innerHTML = 'Source: <strong id="funnel-tier1-provider">' + providerName + ' Webhook Forms Only</strong>';
                        trigText = 'Form POST Webhook';
                    }} else {{
                        tier1El.innerHTML = 'Source: <strong id="funnel-tier1-provider">' + providerName + ' Call Tracking</strong> + <strong>' + providerName + ' Webhook Forms</strong>';
                        trigText = 'Call Completed / Form POST';
                    }}

                    // Update Stage 1 Card
                    const stage1Desc = document.getElementById('stage1-card-desc');
                    const stage1Trig = document.getElementById('stage1-card-trigger');
                    if (stage1Desc) {{
                        stage1Desc.innerHTML = 'Tracks inbound leads for <strong>' + providerName + '</strong>. Captures <strong>GCLID</strong>, <strong>FBCLID</strong>, <strong>MSCLKID</strong>, and caller contact data.';
                    }}
                    if (stage1Trig) {{
                        stage1Trig.innerText = trigText;
                    }}
                }}

                function toggleSettingsCallTrackingFields() {{
                    const provider = document.getElementById('call_tracking_provider').value;
                    const crBox = document.getElementById('settings_call_tracking_callrail_box');
                    const ctmBox = document.getElementById('settings_call_tracking_ctm_box');
                    const wcBox = document.getElementById('settings_call_tracking_wc_box');
                    
                    const titleEl = document.getElementById('settings-call-webhook-title');
                    const descEl = document.getElementById('settings-call-webhook-desc');
                    const inputEl = document.getElementById('callrail-webhook');
                    const origin = window.location.origin || '';
                    
                    let ctSuffix = "/webhooks/callrail?client_id={active_client_id}";
                    if (provider === 'callrail') {{
                        crBox.style.display = 'flex';
                        ctmBox.style.display = 'none';
                        wcBox.style.display = 'none';
                        if (titleEl) titleEl.innerHTML = " CallRail CallCompleted Webhook";
                        if (descEl) descEl.innerHTML = "Paste this dynamic endpoint into CallRail Integration Settings to sync automated call recordings and transcripts:";
                        ctSuffix = "/webhooks/callrail?client_id={active_client_id}";
                    }} else if (provider === 'calltrackingmetrics') {{
                        crBox.style.display = 'none';
                        ctmBox.style.display = 'flex';
                        wcBox.style.display = 'none';
                        if (titleEl) titleEl.innerHTML = " CallTrackingMetrics Transcription Webhook";
                        if (descEl) descEl.innerHTML = "Paste this dynamic endpoint into CallTrackingMetrics webhook setup to sync automated call recordings and transcripts:";
                        ctSuffix = "/webhooks/calltrackingmetrics?client_id={active_client_id}";
                    }} else if (provider === 'whatconverts') {{
                        crBox.style.display = 'none';
                        ctmBox.style.display = 'none';
                        wcBox.style.display = 'flex';
                        if (titleEl) titleEl.innerHTML = " WhatConverts CallCompleted Webhook";
                        if (descEl) descEl.innerHTML = "Paste this dynamic endpoint into WhatConverts webhook setup to sync automated call recordings and transcripts:";
                        ctSuffix = "/webhooks/whatconverts?client_id={active_client_id}";
                    }}
                    if (inputEl) {{
                        inputEl.setAttribute('data-suffix', ctSuffix);
                        inputEl.value = origin + ctSuffix;
                    }}
                    
                    // Live update Visual Sales Funnel Tier 1 Provider Name
                    const tier1ProviderEl = document.getElementById('funnel-tier1-provider');
                    const qualHeadingEl = document.getElementById('funnel-qual-heading');
                    let providerName = "CallRail";
                    if (provider === 'calltrackingmetrics') {{
                        providerName = "CallTrackingMetrics";
                    }} else if (provider === 'whatconverts') {{
                        providerName = "WhatConverts";
                    }}
                    
                    updateFunnelTier1Source();
                    const sotSelect = document.getElementById('source_of_truth');
                    const sotVal = sotSelect ? sotSelect.value : '';
                    if (qualHeadingEl && (sotVal === 'ai_rating' || !sotVal)) {{
                        qualHeadingEl.innerHTML = " Qualified Leads (" + providerName + " Transcripts & Forms)";
                    }}
                }}
                
                function selectCardRadio(name, value, element) {{
                    element.parentNode.querySelectorAll('.card-radio').forEach(card => {{
                        card.classList.remove('selected');
                    }});
                    element.classList.add('selected');
                    element.querySelector('input[type="radio"]').checked = true;
                    
                    if (name === 'sales_source') {{
                        const funnelWonHeading = document.getElementById('funnel-won-heading');
                        const funnelWonSource = document.getElementById('funnel-won-source');
                        const stage3SotEl = document.getElementById('stage3-card-sot');
                    if (stage3SotEl) {{
                        if (['hubspot', 'salesforce', 'zoho', 'servicetitan', 'housecallpro', 'gohighlevel', 'pipedrive'].includes(sot)) {{
                            stage3SotEl.innerText = selectedText + " CRM Webhook Pipeline";
                        }} else if (sot === 'email') {{
                            stage3SotEl.innerText = "Automated Email Sales Scanner";
                        }} else if (['quickbooks', 'xero', 'zoho_books', 'netsuite', 'sage', 'freshbooks'].includes(sot)) {{
                            stage3SotEl.innerText = selectedText + " Integration Webhook";
                        }} else {{
                            stage3SotEl.innerText = "Manual CSV / Spreadsheet Upload";
                        }}
                    }}

                    if (funnelWonHeading && funnelWonSource) {{
                            if (value === 'crm') {{
                                funnelWonHeading.innerHTML = ' Won Deals & Closed Revenue (Live CRM Webhook Pipeline)';
                                funnelWonSource.innerHTML = 'Source: <strong>Live CRM Webhooks (HubSpot / Salesforce / Zoho / ServiceTitan)</strong> → Match Method: <strong>Phone & Email Session Pair</strong>';
                            }} else if (value === 'email') {{
                                funnelWonHeading.innerHTML = ' Won Deals & Closed Revenue (Automated Email Sales Log Scanner)';
                                funnelWonSource.innerHTML = 'Source: <strong>Email Sales Scanner / Order Confirmations</strong> → Match Method: <strong>Phone & Email Session Pair</strong>';
                            }} else if (value === 'accounting') {{
                                funnelWonHeading.innerHTML = ' Won Deals & Closed Revenue (QuickBooks / Xero Invoices)';
                                funnelWonSource.innerHTML = 'Source: <strong>Accounting Webhooks (QuickBooks / Xero Paid Invoices)</strong> → Match Method: <strong>Phone & Email Session Pair</strong>';
                            }} else {{
                                funnelWonHeading.innerHTML = ' Won Deals & Closed Revenue (Manual CSV / Spreadsheet Upload)';
                                funnelWonSource.innerHTML = 'Source: <strong>Manual CSV / Spreadsheet Sales Log Upload</strong> → Match Method: <strong>Phone & Email Session Pair</strong>';
                            }}
                        }}
                    }}
                    
                    if (name === 'lead_gen_method') {{
                        updateFunnelTier1Source();
                        toggleSOTFields();
                    }}
                    
                    if (name === 'exclude_past_customers') {{
                        const uploadBox = document.getElementById('exclusion-upload-box');
                        const msgBox = document.getElementById('existing-exclusions-msg');
                        if (value === 'YES') {{
                            uploadBox.style.display = 'block';
                            if (msgBox) msgBox.style.display = 'block';
                        }} else {{
                            uploadBox.style.display = 'none';
                            if (msgBox) msgBox.style.display = 'none';
                            
                            // Reset submit button if disabled exclusions
                            const btnSubmit = document.getElementById('btn-settings-submit');
                            if (btnSubmit) {{
                                btnSubmit.classList.remove('btn-pulse-save');
                                btnSubmit.innerHTML = ' Save Configuration Changes';
                            }}
                            parsedExclusions = [];
                        }}
                    }}
                }}
                
                function generateStep5Instructions() {{
                    const instructionsContainer = document.getElementById('step-5-instructions-container');
                    if (!instructionsContainer) return;
                    
                    // Clear previous dynamic content
                    instructionsContainer.innerHTML = '';
                    
                    const googleAds = document.getElementById('google_ads_customer_id').value.trim();
                    const facebookAds = document.getElementById('facebook_ads_id').value.trim();
                    const linkedinAds = document.getElementById('linkedin_ads_id').value.trim();
                    const microsoftAds = document.getElementById('microsoft_ads_id').value.trim();
                    const tiktokAds = document.getElementById('tiktok_ads_id').value.trim();
                    const twitterAds = document.getElementById('twitter_ads_id').value.trim();
                    const pinterestAds = document.getElementById('pinterest_ads_id').value.trim();
                    const snapchatAds = document.getElementById('snapchat_ads_id').value.trim();
                    const chatgptAds = document.getElementById('chatgpt_ads_id').value.trim();
                    const redditAds = document.getElementById('reddit_ads_id').value.trim();
                    
                    let blocks = [];
                    
                    if (googleAds) {{
                        blocks.push(`
                            <div style="background: #fdfdfd; border: 1px solid #e0e0e0; border-left: 4px solid #4285F4; padding: 15px; border-radius: 6px; margin-bottom: 20px;">
                                <h4 style="margin-top: 0; color: #4285F4; display: flex; align-items: center; gap: 8px; font-size: 14px;">
                                     Google Ads Goal Setup (ID: ${{googleAds}})
                                </h4>
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; margin: 0; color: #333;">
                                    <li style="margin-bottom: 8px;">Log into your <strong>Google Ads account</strong>.</li>
                                    <li style="margin-bottom: 8px;">Navigate to <strong>Goals ➡️ Conversions ➡️ Summary</strong>.</li>
                                    <li style="margin-bottom: 8px;">Click <strong>+ New conversion action</strong>, select <strong>Import</strong>, choose <strong>Other data sources or CRMs</strong>, select <strong>Track conversions from clicks</strong>, and click <strong>Continue</strong>.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 1 (Qualification):</strong> Set Goal Name to <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Qualified Lead</code>. Under Category, choose <strong>Qualified Lead</strong>. Set Value to use a default value of <code>$1.00</code>.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 2 (Offline Sale):</strong> Create a second conversion import action. Set Goal Name to <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Offline Sale</code>. Under Category, choose <strong>Purchase</strong> or <strong>Converted Lead</strong>. Set Value to <strong>Use different values for each conversion</strong> (defaulting to <code>$0.00</code>).</li>
                                    <li style="margin-bottom: 8px; background: #fff8e1; border-left: 3px solid #ffa000; padding: 8px 10px; border-radius: 4px; color: #5d4037;"> <strong>Recommended Conversion Settings:</strong> Under <strong>Count</strong>, select <strong>Every</strong> conversion (so multiple sales/leads from the same user are tracked). Always select the <strong>longest allowable conversion window</strong> (e.g., 90-day click-through window) each time to maximize historical match depth.</li>
                                    <li style="margin-bottom: 8px; background: #e8f5e9; border-left: 3px solid #2e7d32; padding: 8px 10px; border-radius: 4px; color: #1b5e20;"> <strong>Hands-Free Automated Sync (Optional):</strong> Want Google Ads to pull conversions automatically without manual CSV uploads? Go to <strong>Goals ➡️ Conversions ➡️ Uploads ➡️ Schedules</strong>, click <strong>+</strong>, select <strong>HTTPS</strong> as Source, and paste your live LeadGrove feed URL: <code style="background: #fff; padding: 2px 5px; border-radius: 3px; border: 1px solid #a5d6a7;">https://your-agency-app.onrender.com/feeds/google-conversions.csv?client_id=[id]</code>! (For automated retractions & restatements, set up a second schedule under the Adjustments tab using: <code style="background: #fff; padding: 2px 5px; border-radius: 3px; border: 1px solid #b0bec5;">https://your-agency-app.onrender.com/feeds/google-adjustments.csv?client_id=[id]</code>).</li>
                                </ol>
                            </div>
                        `);
                    }}
                    if (chatgptAds) {{
                        blocks.push(`
                            <div style="background: #fdfdfd; border: 1px solid #e0e0e0; border-left: 4px solid #10a37f; padding: 15px; border-radius: 4px; margin-bottom: 20px;">
                                <h4 style="margin-top: 0; color: #10a37f; display: flex; align-items: center; gap: 8px; font-size: 14px;">
                                     ChatGPT Ads Goal Setup (ID: ${{chatgptAds}})
                                </h4>
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; margin: 0; color: #333;">
                                    <li style="margin-bottom: 8px;">Log into your <strong>ChatGPT Ads Campaign Manager</strong>.</li>
                                    <li style="margin-bottom: 8px;">Go to <strong>Conversion Event Manager</strong> and click <strong>Create Custom Conversion Goal</strong>.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 1 (Qualification):</strong> Name the custom offline event <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Qualified Lead</code>. Under action, select <strong>Lead</strong>, and set tracing to utilize secure CSV match.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 2 (Offline Sale):</strong> Create another custom event. Name it <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Offline Sale</code>, choose event type <strong>Purchase</strong>, and ensure dynamic revenue mapping is selected.</li>
                                </ol>
                            </div>
                        `);
                    }}
                    if (redditAds) {{
                        blocks.push(`
                            <div style="background: #fdfdfd; border: 1px solid #e0e0e0; border-left: 4px solid #FF4500; padding: 15px; border-radius: 4px; margin-bottom: 20px;">
                                <h4 style="margin-top: 0; color: #FF4500; display: flex; align-items: center; gap: 8px; font-size: 14px;">
                                     Reddit Ads Goal Setup (ID: ${{redditAds}})
                                </h4>
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; margin: 0; color: #333;">
                                    <li style="margin-bottom: 8px;">Log into your <strong>Reddit Ads Manager</strong>.</li>
                                    <li style="margin-bottom: 8px;">Go to <strong>Events Manager ➡️ Custom Conversion Events</strong>.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 1 (Qualification):</strong> Create event <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Qualified Lead</code>, event type <strong>Lead</strong>.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 2 (Offline Sale):</strong> Create event <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Offline Sale</code>, event type <strong>Purchase</strong> with dynamic currency mapping.</li>
                                </ol>
                            </div>
                        `);
                    }}

                    
                    if (facebookAds) {{
                        blocks.push(`
                            <div style="background: #fdfdfd; border: 1px solid #e0e0e0; border-left: 4px solid #1877F2; padding: 15px; border-radius: 6px; margin-bottom: 20px;">
                                <h4 style="margin-top: 0; color: #1877F2; display: flex; align-items: center; gap: 8px; font-size: 14px;">
                                     Meta / Facebook Ads Goal Setup (Pixel: ${{facebookAds}})
                                </h4>
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; margin: 0; color: #333;">
                                    <li style="margin-bottom: 8px;">Log into your <strong>Meta Events Manager</strong>.</li>
                                    <li style="margin-bottom: 8px;">Go to <strong>Custom Conversions</strong> in the left-hand navigation and click <strong>Create Custom Conversion</strong>.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 1 (Qualification):</strong> Name the conversion <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Qualified Lead</code>. Choose your Pixel, set the Event to <strong>Lead</strong>, and set rules if necessary.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 2 (Offline Sale):</strong> Create another Custom Conversion. Name it <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Offline Sale</code>, set the Event to <strong>Purchase</strong>, and ensure the value is mapped from the CSV uploads.</li>
                                </ol>
                            </div>
                        `);
                    }}
                    if (chatgptAds) {{
                        blocks.push(`
                            <div style="background: #fdfdfd; border: 1px solid #e0e0e0; border-left: 4px solid #10a37f; padding: 15px; border-radius: 4px; margin-bottom: 20px;">
                                <h4 style="margin-top: 0; color: #10a37f; display: flex; align-items: center; gap: 8px; font-size: 14px;">
                                     ChatGPT Ads Goal Setup (ID: ${{chatgptAds}})
                                </h4>
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; margin: 0; color: #333;">
                                    <li style="margin-bottom: 8px;">Log into your <strong>ChatGPT Ads Campaign Manager</strong>.</li>
                                    <li style="margin-bottom: 8px;">Go to <strong>Conversion Event Manager</strong> and click <strong>Create Custom Conversion Goal</strong>.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 1 (Qualification):</strong> Name the custom offline event <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Qualified Lead</code>. Under action, select <strong>Lead</strong>, and set tracing to utilize secure CSV match.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 2 (Offline Sale):</strong> Create another custom event. Name it <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Offline Sale</code>, choose event type <strong>Purchase</strong>, and ensure dynamic revenue mapping is selected.</li>
                                </ol>
                            </div>
                        `);
                    }}
                    if (redditAds) {{
                        blocks.push(`
                            <div style="background: #fdfdfd; border: 1px solid #e0e0e0; border-left: 4px solid #FF4500; padding: 15px; border-radius: 4px; margin-bottom: 20px;">
                                <h4 style="margin-top: 0; color: #FF4500; display: flex; align-items: center; gap: 8px; font-size: 14px;">
                                     Reddit Ads Goal Setup (ID: ${{redditAds}})
                                </h4>
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; margin: 0; color: #333;">
                                    <li style="margin-bottom: 8px;">Log into your <strong>Reddit Ads Manager</strong>.</li>
                                    <li style="margin-bottom: 8px;">Go to <strong>Events Manager ➡️ Custom Conversion Events</strong>.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 1 (Qualification):</strong> Create event <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Qualified Lead</code>, event type <strong>Lead</strong>.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 2 (Offline Sale):</strong> Create event <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Offline Sale</code>, event type <strong>Purchase</strong> with dynamic currency mapping.</li>
                                </ol>
                            </div>
                        `);
                    }}

                    
                    if (linkedinAds) {{
                        blocks.push(`
                            <div style="background: #fdfdfd; border: 1px solid #e0e0e0; border-left: 4px solid #0A66C2; padding: 15px; border-radius: 4px; margin-bottom: 20px;">
                                <h4 style="margin-top: 0; color: #0A66C2; display: flex; align-items: center; gap: 8px; font-size: 14px;">
                                     LinkedIn Ads Goal Setup (Account: ${{linkedinAds}})
                                </h4>
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; margin: 0; color: #333;">
                                    <li style="margin-bottom: 8px;">Log into <strong>LinkedIn Campaign Manager</strong>.</li>
                                    <li style="margin-bottom: 8px;">Click <strong>Analyze ➡️ Conversion Tracking</strong> in the left sidebar.</li>
                                    <li style="margin-bottom: 8px;">Click <strong>Create Conversion</strong>, and configure:
                                        <ul style="list-style-type: disc; padding-left: 15px; margin-top: 4px;">
                                            <li><strong>Goal 1 (Qualification):</strong> Name it <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Qualified Lead</code>, set key event to <strong>Lead</strong>, and choose <strong>Offline Upload (CSV)</strong> as your tracking method.</li>
                                            <li><strong>Goal 2 (Offline Sale):</strong> Create another conversion. Name it <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Offline Sale</code>, set event type to <strong>Purchase</strong>, and select <strong>Offline Upload (CSV)</strong>.</li>
                                        </ul>
                                    </li>
                                </ol>
                            </div>
                        `);
                    }}
                    if (chatgptAds) {{
                        blocks.push(`
                            <div style="background: #fdfdfd; border: 1px solid #e0e0e0; border-left: 4px solid #10a37f; padding: 15px; border-radius: 4px; margin-bottom: 20px;">
                                <h4 style="margin-top: 0; color: #10a37f; display: flex; align-items: center; gap: 8px; font-size: 14px;">
                                     ChatGPT Ads Goal Setup (ID: ${{chatgptAds}})
                                </h4>
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; margin: 0; color: #333;">
                                    <li style="margin-bottom: 8px;">Log into your <strong>ChatGPT Ads Campaign Manager</strong>.</li>
                                    <li style="margin-bottom: 8px;">Go to <strong>Conversion Event Manager</strong> and click <strong>Create Custom Conversion Goal</strong>.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 1 (Qualification):</strong> Name the custom offline event <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Qualified Lead</code>. Under action, select <strong>Lead</strong>, and set tracing to utilize secure CSV match.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 2 (Offline Sale):</strong> Create another custom event. Name it <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Offline Sale</code>, choose event type <strong>Purchase</strong>, and ensure dynamic revenue mapping is selected.</li>
                                </ol>
                            </div>
                        `);
                    }}
                    if (redditAds) {{
                        blocks.push(`
                            <div style="background: #fdfdfd; border: 1px solid #e0e0e0; border-left: 4px solid #FF4500; padding: 15px; border-radius: 4px; margin-bottom: 20px;">
                                <h4 style="margin-top: 0; color: #FF4500; display: flex; align-items: center; gap: 8px; font-size: 14px;">
                                     Reddit Ads Goal Setup (ID: ${{redditAds}})
                                </h4>
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; margin: 0; color: #333;">
                                    <li style="margin-bottom: 8px;">Log into your <strong>Reddit Ads Manager</strong>.</li>
                                    <li style="margin-bottom: 8px;">Go to <strong>Events Manager ➡️ Custom Conversion Events</strong>.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 1 (Qualification):</strong> Create event <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Qualified Lead</code>, event type <strong>Lead</strong>.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 2 (Offline Sale):</strong> Create event <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Offline Sale</code>, event type <strong>Purchase</strong> with dynamic currency mapping.</li>
                                </ol>
                            </div>
                        `);
                    }}

                    
                    if (microsoftAds) {{
                        blocks.push(`
                            <div style="background: #fdfdfd; border: 1px solid #e0e0e0; border-left: 4px solid #00A4EF; padding: 15px; border-radius: 4px; margin-bottom: 20px;">
                                <h4 style="margin-top: 0; color: #00A4EF; display: flex; align-items: center; gap: 8px; font-size: 14px;">
                                     Microsoft (Bing) Ads Goal Setup (ID: ${{microsoftAds}})
                                </h4>
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; margin: 0; color: #333;">
                                    <li style="margin-bottom: 8px;">Log into your <strong>Microsoft Advertising Dashboard</strong>.</li>
                                    <li style="margin-bottom: 8px;">Go to <strong>Tools ➡️ Conversion goals</strong> and click <strong>Create</strong>.</li>
                                    <li style="margin-bottom: 8px;">Choose <strong>Offline conversions</strong> as the goal type.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 1 (Qualification):</strong> Name the goal <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Qualified Lead</code>. Choose <strong>Lead</strong> as the goal category.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 2 (Offline Sale):</strong> Create another goal. Name it <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Offline Sale</code>, category as <strong>Purchase/Sale</strong>, and select <strong>Each time it happens, the conversion value may vary</strong>.</li>
                                </ol>
                            </div>
                        `);
                    }}
                    if (chatgptAds) {{
                        blocks.push(`
                            <div style="background: #fdfdfd; border: 1px solid #e0e0e0; border-left: 4px solid #10a37f; padding: 15px; border-radius: 4px; margin-bottom: 20px;">
                                <h4 style="margin-top: 0; color: #10a37f; display: flex; align-items: center; gap: 8px; font-size: 14px;">
                                     ChatGPT Ads Goal Setup (ID: ${{chatgptAds}})
                                </h4>
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; margin: 0; color: #333;">
                                    <li style="margin-bottom: 8px;">Log into your <strong>ChatGPT Ads Campaign Manager</strong>.</li>
                                    <li style="margin-bottom: 8px;">Go to <strong>Conversion Event Manager</strong> and click <strong>Create Custom Conversion Goal</strong>.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 1 (Qualification):</strong> Name the custom offline event <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Qualified Lead</code>. Under action, select <strong>Lead</strong>, and set tracing to utilize secure CSV match.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 2 (Offline Sale):</strong> Create another custom event. Name it <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Offline Sale</code>, choose event type <strong>Purchase</strong>, and ensure dynamic revenue mapping is selected.</li>
                                </ol>
                            </div>
                        `);
                    }}
                    if (redditAds) {{
                        blocks.push(`
                            <div style="background: #fdfdfd; border: 1px solid #e0e0e0; border-left: 4px solid #FF4500; padding: 15px; border-radius: 4px; margin-bottom: 20px;">
                                <h4 style="margin-top: 0; color: #FF4500; display: flex; align-items: center; gap: 8px; font-size: 14px;">
                                     Reddit Ads Goal Setup (ID: ${{redditAds}})
                                </h4>
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; margin: 0; color: #333;">
                                    <li style="margin-bottom: 8px;">Log into your <strong>Reddit Ads Manager</strong>.</li>
                                    <li style="margin-bottom: 8px;">Go to <strong>Events Manager ➡️ Custom Conversion Events</strong>.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 1 (Qualification):</strong> Create event <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Qualified Lead</code>, event type <strong>Lead</strong>.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 2 (Offline Sale):</strong> Create event <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Offline Sale</code>, event type <strong>Purchase</strong> with dynamic currency mapping.</li>
                                </ol>
                            </div>
                        `);
                    }}

                    
                    if (tiktokAds) {{
                        blocks.push(`
                            <div style="background: #fdfdfd; border: 1px solid #e0e0e0; border-left: 4px solid #010101; padding: 15px; border-radius: 4px; margin-bottom: 20px;">
                                <h4 style="margin-top: 0; color: #010101; display: flex; align-items: center; gap: 8px; font-size: 14px;">
                                     TikTok Ads Goal Setup (Pixel: ${{tiktokAds}})
                                </h4>
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; margin: 0; color: #333;">
                                    <li style="margin-bottom: 8px;">Log into your <strong>TikTok Ads Manager</strong>.</li>
                                    <li style="margin-bottom: 8px;">Go to <strong>Tools ➡️ Events ➡️ Offline Events</strong>.</li>
                                    <li style="margin-bottom: 8px;">Create a new Offline Event Set, and define conversion rules:
                                        <ul style="list-style-type: disc; padding-left: 15px; margin-top: 4px;">
                                            <li><strong>Goal 1 (Qualification):</strong> Name it <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Qualified Lead</code>, mapped to event category <strong>Contact</strong>.</li>
                                            <li><strong>Goal 2 (Offline Sale):</strong> Name it <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Offline Sale</code>, mapped to event category <strong>CompletePayment</strong>.</li>
                                        </ul>
                                    </li>
                                </ol>
                            </div>
                        `);
                    }}
                    if (chatgptAds) {{
                        blocks.push(`
                            <div style="background: #fdfdfd; border: 1px solid #e0e0e0; border-left: 4px solid #10a37f; padding: 15px; border-radius: 4px; margin-bottom: 20px;">
                                <h4 style="margin-top: 0; color: #10a37f; display: flex; align-items: center; gap: 8px; font-size: 14px;">
                                     ChatGPT Ads Goal Setup (ID: ${{chatgptAds}})
                                </h4>
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; margin: 0; color: #333;">
                                    <li style="margin-bottom: 8px;">Log into your <strong>ChatGPT Ads Campaign Manager</strong>.</li>
                                    <li style="margin-bottom: 8px;">Go to <strong>Conversion Event Manager</strong> and click <strong>Create Custom Conversion Goal</strong>.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 1 (Qualification):</strong> Name the custom offline event <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Qualified Lead</code>. Under action, select <strong>Lead</strong>, and set tracing to utilize secure CSV match.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 2 (Offline Sale):</strong> Create another custom event. Name it <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Offline Sale</code>, choose event type <strong>Purchase</strong>, and ensure dynamic revenue mapping is selected.</li>
                                </ol>
                            </div>
                        `);
                    }}
                    if (redditAds) {{
                        blocks.push(`
                            <div style="background: #fdfdfd; border: 1px solid #e0e0e0; border-left: 4px solid #FF4500; padding: 15px; border-radius: 4px; margin-bottom: 20px;">
                                <h4 style="margin-top: 0; color: #FF4500; display: flex; align-items: center; gap: 8px; font-size: 14px;">
                                     Reddit Ads Goal Setup (ID: ${{redditAds}})
                                </h4>
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; margin: 0; color: #333;">
                                    <li style="margin-bottom: 8px;">Log into your <strong>Reddit Ads Manager</strong>.</li>
                                    <li style="margin-bottom: 8px;">Go to <strong>Events Manager ➡️ Custom Conversion Events</strong>.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 1 (Qualification):</strong> Create event <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Qualified Lead</code>, event type <strong>Lead</strong>.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 2 (Offline Sale):</strong> Create event <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Offline Sale</code>, event type <strong>Purchase</strong> with dynamic currency mapping.</li>
                                </ol>
                            </div>
                        `);
                    }}

                    
                    if (twitterAds) {{
                        blocks.push(`
                            <div style="background: #fdfdfd; border: 1px solid #e0e0e0; border-left: 4px solid #15202B; padding: 15px; border-radius: 4px; margin-bottom: 20px;">
                                <h4 style="margin-top: 0; color: #15202B; display: flex; align-items: center; gap: 8px; font-size: 14px;">
                                     X (Twitter) Ads Goal Setup (Pixel: ${{twitterAds}})
                                </h4>
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; margin: 0; color: #333;">
                                    <li style="margin-bottom: 8px;">Log into your <strong>X Ads Manager</strong>.</li>
                                    <li style="margin-bottom: 8px;">Go to <strong>Tools ➡️ Events Manager</strong> and click <strong>Add Event</strong>.</li>
                                    <li style="margin-bottom: 8px;">Select <strong>Offline</strong> as the conversion tracking type:
                                        <ul style="list-style-type: disc; padding-left: 15px; margin-top: 4px;">
                                            <li><strong>Goal 1 (Qualification):</strong> Name the event <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-size: 12px; font-family: monospace;">LeadGroove Qualified Lead</code>, event type <strong>Lead</strong>.</li>
                                            <li><strong>Goal 2 (Offline Sale):</strong> Name the event <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-size: 12px; font-family: monospace;">LeadGroove Offline Sale</code>, event type <strong>Purchase</strong>.</li>
                                        </ul>
                                    </li>
                                </ol>
                            </div>
                        `);
                    }}
                    if (chatgptAds) {{
                        blocks.push(`
                            <div style="background: #fdfdfd; border: 1px solid #e0e0e0; border-left: 4px solid #10a37f; padding: 15px; border-radius: 4px; margin-bottom: 20px;">
                                <h4 style="margin-top: 0; color: #10a37f; display: flex; align-items: center; gap: 8px; font-size: 14px;">
                                     ChatGPT Ads Goal Setup (ID: ${{chatgptAds}})
                                </h4>
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; margin: 0; color: #333;">
                                    <li style="margin-bottom: 8px;">Log into your <strong>ChatGPT Ads Campaign Manager</strong>.</li>
                                    <li style="margin-bottom: 8px;">Go to <strong>Conversion Event Manager</strong> and click <strong>Create Custom Conversion Goal</strong>.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 1 (Qualification):</strong> Name the custom offline event <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Qualified Lead</code>. Under action, select <strong>Lead</strong>, and set tracing to utilize secure CSV match.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 2 (Offline Sale):</strong> Create another custom event. Name it <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Offline Sale</code>, choose event type <strong>Purchase</strong>, and ensure dynamic revenue mapping is selected.</li>
                                </ol>
                            </div>
                        `);
                    }}
                    if (redditAds) {{
                        blocks.push(`
                            <div style="background: #fdfdfd; border: 1px solid #e0e0e0; border-left: 4px solid #FF4500; padding: 15px; border-radius: 4px; margin-bottom: 20px;">
                                <h4 style="margin-top: 0; color: #FF4500; display: flex; align-items: center; gap: 8px; font-size: 14px;">
                                     Reddit Ads Goal Setup (ID: ${{redditAds}})
                                </h4>
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; margin: 0; color: #333;">
                                    <li style="margin-bottom: 8px;">Log into your <strong>Reddit Ads Manager</strong>.</li>
                                    <li style="margin-bottom: 8px;">Go to <strong>Events Manager ➡️ Custom Conversion Events</strong>.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 1 (Qualification):</strong> Create event <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Qualified Lead</code>, event type <strong>Lead</strong>.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 2 (Offline Sale):</strong> Create event <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Offline Sale</code>, event type <strong>Purchase</strong> with dynamic currency mapping.</li>
                                </ol>
                            </div>
                        `);
                    }}

                    
                    if (snapchatAds) {{
                        blocks.push(`
                            <div style="background: #fdfdfd; border: 1px solid #e0e0e0; border-left: 4px solid #FFFC00; padding: 15px; border-radius: 4px; margin-bottom: 20px;">
                                <h4 style="margin-top: 0; color: #000; background: #FFFC00; display: inline-block; padding: 2px 6px; border-radius: 3px; font-size: 14px;">
                                     Snapchat Ads Goal Setup (ID: ${{snapchatAds}})
                                </h4>
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; margin: 0; color: #333;">
                                    <li style="margin-bottom: 8px;">Log into your <strong>Snapchat Ads Manager</strong>.</li>
                                    <li style="margin-bottom: 8px;">Go to <strong>Assets ➡️ Events Manager</strong>.</li>
                                    <li style="margin-bottom: 8px;">Select your Active Web Pixel and click <strong>Create Custom Goal</strong> or <strong>Offline Conversion Event</strong>.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 1 (Qualification):</strong> Name your Custom Offline Event <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Qualified Lead</code>. Set category to <strong>SIGN_UP</strong> or <strong>PAGE_VIEW</strong>.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 2 (Offline Sale):</strong> Create another custom event. Name it <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Offline Sale</code>. Set event category to <strong>PURCHASE</strong> and enable dynamic revenue tracking.</li>
                                </ol>
                            </div>
                        `);
                    }}
                    if (pinterestAds) {{
                        blocks.push(`
                            <div style="background: #fdfdfd; border: 1px solid #e0e0e0; border-left: 4px solid #E60023; padding: 15px; border-radius: 4px; margin-bottom: 20px;">
                                <h4 style="margin-top: 0; color: #E60023; display: flex; align-items: center; gap: 8px; font-size: 14px;">
                                     Pinterest Ads Goal Setup (Tag: ${{pinterestAds}})
                                </h4>
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; margin: 0; color: #333;">
                                    <li style="margin-bottom: 8px;">Log into your <strong>Pinterest Ads Manager</strong>.</li>
                                    <li style="margin-bottom: 8px;">Go to <strong>Ads ➡️ Conversions</strong> and select <strong>Offline conversions</strong>.</li>
                                    <li style="margin-bottom: 8px;">Click <strong>Create conversion event</strong>:
                                        <ul style="list-style-type: disc; padding-left: 15px; margin-top: 4px;">
                                            <li><strong>Goal 1 (Qualification):</strong> Set event name to <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Qualified Lead</code>, category <strong>Lead</strong>.</li>
                                            <li><strong>Goal 2 (Offline Sale):</strong> Set event name to <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Offline Sale</code>, category <strong>Checkout</strong>.</li>
                                        </ul>
                                    </li>
                                </ol>
                            </div>
                        `);
                    }}
                    if (chatgptAds) {{
                        blocks.push(`
                            <div style="background: #fdfdfd; border: 1px solid #e0e0e0; border-left: 4px solid #10a37f; padding: 15px; border-radius: 4px; margin-bottom: 20px;">
                                <h4 style="margin-top: 0; color: #10a37f; display: flex; align-items: center; gap: 8px; font-size: 14px;">
                                     ChatGPT Ads Goal Setup (ID: ${{chatgptAds}})
                                </h4>
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; margin: 0; color: #333;">
                                    <li style="margin-bottom: 8px;">Log into your <strong>ChatGPT Ads Campaign Manager</strong>.</li>
                                    <li style="margin-bottom: 8px;">Go to <strong>Conversion Event Manager</strong> and click <strong>Create Custom Conversion Goal</strong>.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 1 (Qualification):</strong> Name the custom offline event <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Qualified Lead</code>. Under action, select <strong>Lead</strong>, and set tracing to utilize secure CSV match.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 2 (Offline Sale):</strong> Create another custom event. Name it <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Offline Sale</code>, choose event type <strong>Purchase</strong>, and ensure dynamic revenue mapping is selected.</li>
                                </ol>
                            </div>
                        `);
                    }}
                    if (redditAds) {{
                        blocks.push(`
                            <div style="background: #fdfdfd; border: 1px solid #e0e0e0; border-left: 4px solid #FF4500; padding: 15px; border-radius: 4px; margin-bottom: 20px;">
                                <h4 style="margin-top: 0; color: #FF4500; display: flex; align-items: center; gap: 8px; font-size: 14px;">
                                     Reddit Ads Goal Setup (ID: ${{redditAds}})
                                </h4>
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; margin: 0; color: #333;">
                                    <li style="margin-bottom: 8px;">Log into your <strong>Reddit Ads Manager</strong>.</li>
                                    <li style="margin-bottom: 8px;">Go to <strong>Events Manager ➡️ Custom Conversion Events</strong>.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 1 (Qualification):</strong> Create event <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Qualified Lead</code>, event type <strong>Lead</strong>.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 2 (Offline Sale):</strong> Create event <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Offline Sale</code>, event type <strong>Purchase</strong> with dynamic currency mapping.</li>
                                </ol>
                            </div>
                        `);
                    }}

                    
                    if (blocks.length === 0) {{
                        instructionsContainer.innerHTML = `
                            <div style="text-align: center; color: #666; padding: 30px; border: 1px dashed #ccc; border-radius: 6px; background: #fafafa;">
                                <span style="font-size: 24px; display: block; margin-bottom: 10px;">ℹ️</span>
                                No advertising account IDs were entered in Step 1. If you added them later, please configure custom conversions on your ad networks matching the names:
                                <br><br>
                                <code style="background: #f1f3f4; padding: 4px 10px; border-radius: 3px; font-weight: bold; font-size: 14px; font-family: monospace; color: #1a237e;">LeadGroove Qualified Lead</code>
                                <br><span style="font-size: 11px; color: #888;">and</span><br>
                                <code style="background: #f1f3f4; padding: 4px 10px; border-radius: 3px; font-weight: bold; font-size: 14px; font-family: monospace; color: #1a237e;">LeadGroove Offline Sale</code>
                            </div>
                        `;
                    }} else {{
                        instructionsContainer.innerHTML = blocks.join('');
                    }}
                }}

                                
                
                function toggleVoipInstructions() {{
                    const voipSelect = document.getElementById('voip_provider');
                    if (!voipSelect) return;
                    const voip = voipSelect.value;
                    const cards = document.querySelectorAll('.voip-inst-card');
                    cards.forEach(card => {{ card.style.display = 'none'; }});
                    const activeCard = document.getElementById('voip-inst-' + voip);
                    if (activeCard) {{ 
                        activeCard.style.display = 'block'; 
                        const origin = window.location.origin || '';
                        const inputs = activeCard.querySelectorAll('.webhook-input');
                        inputs.forEach(input => {{
                            const suffix = input.getAttribute('data-suffix');
                            if (suffix) {{
                                input.value = origin + suffix;
                            }}
                        }});
                    }}
                }}

                function toggleSOTFields() {{
                    const sotSelect = document.getElementById('source_of_truth');
                    if (!sotSelect) return;
                    
                    const selectedOption = sotSelect.options[sotSelect.selectedIndex];
                    const selectedText = selectedOption ? selectedOption.text : '';
                    const sot = sotSelect.value;
                    
                    // Update Visual Sales Funnel Layer a) Qualified Leads Live
                    const funnelQualHeading = document.getElementById('funnel-qual-heading');
                    const funnelQualRule = document.getElementById('funnel-qual-rule');
                    if (funnelQualHeading && funnelQualRule) {{
                        if (['hubspot', 'salesforce', 'zoho', 'servicetitan', 'housecallpro', 'gohighlevel', 'pipedrive'].includes(sot)) {{
                            funnelQualHeading.innerHTML = ' Qualified Leads (' + selectedText + ' Stage Sync)';
                            funnelQualRule.innerHTML = 'Configured Lead Rule: <strong>"Syncs qualified lead tags & deal stage transitions from ' + selectedText + ' Webhook"</strong>';
                        }} else if (sot === 'email') {{
                            funnelQualHeading.innerHTML = ' Qualified Leads (Email Sales & Lead Scanner)';
                            funnelQualRule.innerHTML = 'Configured Lead Rule: <strong>"Parses incoming lead notification emails & automated qualification reports"</strong>';
                        }} else if (['quickbooks', 'xero', 'zoho_books', 'netsuite', 'sage', 'freshbooks'].includes(sot)) {{
                            funnelQualHeading.innerHTML = ' Qualified Leads (' + selectedText + ' Integration)';
                            funnelQualRule.innerHTML = 'Configured Lead Rule: <strong>"Qualifies leads upon initial invoice creation or customer onboarding in ' + selectedText + '"</strong>';
                        }} else if (['google_sheets', 'zapier'].includes(sot)) {{
                            funnelQualHeading.innerHTML = ' Qualified Leads (' + selectedText + ' Feed)';
                            funnelQualRule.innerHTML = 'Configured Lead Rule: <strong>"Qualifies leads matching custom status rows in ' + selectedText + '"</strong>';
                        }} else {{
                            const promptInput = document.getElementById('prompt');
                            let promptVal = (promptInput && promptInput.value && promptInput.value.trim()) ? promptInput.value.trim() : 'Standard Criteria: Inquiring about core services, requesting a quote, or scheduling an appointment';
                            if (promptVal.length > 90) promptVal = promptVal.substring(0, 87) + '...';
                            funnelQualHeading.innerHTML = ' Qualified Leads (CallRail Transcripts & Forms)';
                            funnelQualRule.innerHTML = 'Configured AI Audit Rule: <strong>"' + promptVal + '"</strong>';
                        }}
                    }}

                    // Update Visual Sales Funnel Layer b) Won Deals Live
                    const funnelWonHeading = document.getElementById('funnel-won-heading');
                    const funnelWonSource = document.getElementById('funnel-won-source');
                    const stage3SotEl = document.getElementById('stage3-card-sot');
                    if (stage3SotEl) {{
                        if (['hubspot', 'salesforce', 'zoho', 'servicetitan', 'housecallpro', 'gohighlevel', 'pipedrive'].includes(sot)) {{
                            stage3SotEl.innerText = selectedText + " CRM Webhook Pipeline";
                        }} else if (sot === 'email') {{
                            stage3SotEl.innerText = "Automated Email Sales Scanner";
                        }} else if (['quickbooks', 'xero', 'zoho_books', 'netsuite', 'sage', 'freshbooks'].includes(sot)) {{
                            stage3SotEl.innerText = selectedText + " Integration Webhook";
                        }} else {{
                            stage3SotEl.innerText = "Manual CSV / Spreadsheet Upload";
                        }}
                    }}

                    if (funnelWonHeading && funnelWonSource) {{
                        if (['hubspot', 'salesforce', 'zoho', 'servicetitan', 'housecallpro', 'gohighlevel', 'pipedrive'].includes(sot)) {{
                            funnelWonHeading.innerHTML = ' Won Deals & Closed Revenue (' + selectedText + ' Webhook Pipeline)';
                            funnelWonSource.innerHTML = 'Source: <strong>' + selectedText + ' Webhook</strong> → Match Method: <strong>Phone & Email Session Pair</strong>';
                        }} else if (sot === 'email') {{
                            funnelWonHeading.innerHTML = ' Won Deals & Closed Revenue (Automated Email Sales Log Scanner)';
                            funnelWonSource.innerHTML = 'Source: <strong>Email Sales Scanner / Order Confirmations</strong> → Match Method: <strong>Phone & Email Session Pair</strong>';
                        }} else if (['quickbooks', 'xero', 'zoho_books', 'netsuite', 'sage', 'freshbooks', 'google_sheets', 'zapier'].includes(sot)) {{
                            funnelWonHeading.innerHTML = ' Won Deals & Closed Revenue (' + selectedText + ' Integration)';
                            funnelWonSource.innerHTML = 'Source: <strong>' + selectedText + ' Integration Webhook</strong> → Match Method: <strong>Phone & Email Session Pair</strong>';
                        }} else {{
                            funnelWonHeading.innerHTML = ' Won Deals & Closed Revenue (Manual CSV / Spreadsheet Upload)';
                            funnelWonSource.innerHTML = 'Source: <strong>Manual CSV / Spreadsheet Sales Log Upload</strong> → Match Method: <strong>Phone & Email Session Pair</strong>';
                        }}
                    }}
                    
                    const crmTitleSpan = document.getElementById('settings-crm-webhook-title');
                    const billingTitleSpan = document.getElementById('settings-billing-webhook-title');
                    if (crmTitleSpan) {{
                        crmTitleSpan.innerText = "⚙️ " + selectedText + " Webhook";
                    }}
                    if (billingTitleSpan) {{
                        billingTitleSpan.innerText = " " + selectedText + " Webhook";
                    }}
                    
                    const dealBox = document.getElementById('sot-deal-tags-box');
                    const leadBox = document.getElementById('sot-lead-tags-box');
                    const emailBox = document.getElementById('sot-email-box');
                    const voipBox = document.getElementById('sot-voip-box');
                    
                    const hsBox = document.getElementById('sot-hubspot-instructions-box');
                    const salesforceBox = document.getElementById('sot-salesforce-instructions-box');
                    const zohoBox = document.getElementById('sot-zoho-instructions-box');
                    const servicetitanBox = document.getElementById('sot-servicetitan-instructions-box');
                    const housecallproBox = document.getElementById('sot-housecallpro-instructions-box');
                    const ghlBox = document.getElementById('sot-gohighlevel-instructions-box');
                    
                    const quickbooksBox = document.getElementById('sot-quickbooks-instructions-box');
                    const xeroBox = document.getElementById('sot-xero-instructions-box');
                    const zohoBooksBox = document.getElementById('sot-zoho_books-instructions-box');
                    const netsuiteBox = document.getElementById('sot-netsuite-instructions-box');
                    const sageBox = document.getElementById('sot-sage-instructions-box');
                    const freshbooksBox = document.getElementById('sot-freshbooks-instructions-box');
                    const googleSheetsBox = document.getElementById('sot-google_sheets-instructions-box');
                    const zapierBox = document.getElementById('sot-zapier-instructions-box');
                    const monthlyEmailBox = document.getElementById('sot-monthly-email-instructions-box');
                    
                    const crmCard = document.getElementById('crm-webhook-card');
                    const billingCard = document.getElementById('billing-webhook-card');
                    const emailCard = document.getElementById('email-webhook-card');
                    
                    // 1. Reset / Hide all SOT fields and instruction boxes
                    if (dealBox) dealBox.style.display = 'none';
                    const valGroup = document.getElementById('sot-value-tags-group');
                    if (valGroup) valGroup.style.display = 'none';
                    if (leadBox) leadBox.style.display = 'none';
                    if (emailBox) emailBox.style.display = 'none';
                    if (voipBox) voipBox.style.display = 'none';
                    
                    if (hsBox) hsBox.style.display = 'none';
                    if (salesforceBox) salesforceBox.style.display = 'none';
                    if (zohoBox) zohoBox.style.display = 'none';
                    if (servicetitanBox) servicetitanBox.style.display = 'none';
                    if (housecallproBox) housecallproBox.style.display = 'none';
                    if (ghlBox) ghlBox.style.display = 'none';
                    
                    if (quickbooksBox) quickbooksBox.style.display = 'none';
                    if (xeroBox) xeroBox.style.display = 'none';
                    if (zohoBooksBox) zohoBooksBox.style.display = 'none';
                    if (netsuiteBox) netsuiteBox.style.display = 'none';
                    if (sageBox) sageBox.style.display = 'none';
                    if (freshbooksBox) freshbooksBox.style.display = 'none';
                    if (googleSheetsBox) googleSheetsBox.style.display = 'none';
                    if (zapierBox) zapierBox.style.display = 'none';
                    if (monthlyEmailBox) monthlyEmailBox.style.display = 'none';
                    
                    // 2. Reset / Hide all SOT webhook cards in Section 5
                    if (crmCard) crmCard.style.display = 'none';
                    if (billingCard) billingCard.style.display = 'none';
                    if (emailCard) emailCard.style.display = 'none';
                    
                    // 3. Show ONLY the active SOT webhook card and instructions
                    const crmPlatforms = ['hubspot', 'salesforce', 'zoho', 'servicetitan', 'housecallpro', 'gohighlevel'];
                    const billingPlatforms = ['quickbooks', 'xero', 'zoho_books', 'netsuite', 'sage', 'freshbooks', 'google_sheets', 'zapier'];
                    
                    if (crmPlatforms.includes(sot)) {{
                        if (crmCard) crmCard.style.display = 'block';
                        
                        if (['hubspot', 'salesforce', 'zoho', 'gohighlevel', 'google_sheets', 'email', 'zapier'].includes(sot)) {{
                            if (dealBox) dealBox.style.display = 'block';
                            const dLabel = document.getElementById('sot-deal-tags-label');
                            const wLabel = document.getElementById('sot-won-deal-tags-label');
                            if (dLabel && sot !== 'google_sheets') dLabel.innerText = 'Which tags/statuses under Deals signify a qualified conversion?';
                            if (wLabel && sot !== 'google_sheets') wLabel.innerText = 'Which tags/statuses under Deals signify a won deal conversion?';
                        }}
                        if (['servicetitan', 'housecallpro', 'gohighlevel'].includes(sot)) {{
                            if (leadBox) leadBox.style.display = 'block';
                        }}
                        
                        if (sot === 'hubspot' && hsBox) hsBox.style.display = 'block';
                        else if (sot === 'salesforce' && salesforceBox) salesforceBox.style.display = 'block';
                        else if (sot === 'zoho' && zohoBox) zohoBox.style.display = 'block';
                        else if (sot === 'servicetitan' && servicetitanBox) servicetitanBox.style.display = 'block';
                        else if (sot === 'housecallpro' && housecallproBox) housecallproBox.style.display = 'block';
                        else if (sot === 'gohighlevel' && ghlBox) ghlBox.style.display = 'block';
                        
                    }} else if (billingPlatforms.includes(sot)) {{
                        if (billingCard) billingCard.style.display = 'block';
                        
                        if (sot === 'quickbooks' && quickbooksBox) quickbooksBox.style.display = 'block';
                        else if (sot === 'xero' && xeroBox) xeroBox.style.display = 'block';
                        else if (sot === 'zoho_books' && zohoBooksBox) zohoBooksBox.style.display = 'block';
                        else if (sot === 'netsuite' && netsuiteBox) netsuiteBox.style.display = 'block';
                        else if (sot === 'sage' && sageBox) sageBox.style.display = 'block';
                        else if (sot === 'freshbooks' && freshbooksBox) freshbooksBox.style.display = 'block';
                        else if (sot === 'google_sheets' && googleSheetsBox) {{ 
                            googleSheetsBox.style.display = 'block'; 
                            if (dealBox) dealBox.style.display = 'block';
                            const valGroup = document.getElementById('sot-value-tags-group');
                            if (valGroup) valGroup.style.display = 'block';
                            const dLabel = document.getElementById('sot-deal-tags-label');
                            const wLabel = document.getElementById('sot-won-deal-tags-label');
                            if (dLabel) dLabel.innerText = 'Which tags/statuses on Google sheet signify a qualified lead conversion?';
                            if (wLabel) wLabel.innerText = 'Which tags/statuses on Google sheet signify a won deal conversion?';
                        }}
                        else if (sot === 'zapier' && zapierBox) {{ zapierBox.style.display = 'block'; if (dealBox) dealBox.style.display = 'block'; }}
                    }} else if (sot === 'email') {{
                        if (monthlyEmailBox) monthlyEmailBox.style.display = 'block';
                        if (dealBox) dealBox.style.display = 'block';
                        if (emailCard) emailCard.style.display = 'block';
                    }} else if (sot === 'ai_rating') {{
                        if (emailBox) emailBox.style.display = 'block';
                        if (voipBox) {{
                            voipBox.style.display = 'block';
                            toggleVoipInstructions();
                        }}
                    }}
                }}

                let parsedExclusions = [];

                function handleExclusionFileUpload(event) {{
                    const file = event.target.files[0];
                    if (!file) return;
                    
                    const reader = new FileReader();
                    reader.onload = function(e) {{
                        const text = e.target.result;
                        parseCSVToExclusions(text, file.name);
                    }};
                    reader.readAsText(file);
                }}

                function parseCSVToExclusions(text, filename) {{
                    const lines = text.split(/\\r\\n|\\n/);
                    if (lines.length === 0) {{
                        showUploadStatus('Error: The file is empty.', 'error');
                        return;
                    }}
                    
                    function parseCSVLine(line) {{
                        let arr = [];
                        let quote = false;
                        let cell = "";
                        for (let colIdx = 0; colIdx < line.length; colIdx++) {{
                            let char = line[colIdx];
                            if (char === '"') {{
                                quote = !quote;
                            }} else if (char === ',' && !quote) {{
                                arr.push(cell.trim());
                                cell = "";
                            }} else {{
                                cell += char;
                            }}
                        }}
                        arr.push(cell.trim());
                        return arr;
                    }}
                    
                    const headers = parseCSVLine(lines[0]).map(h => h.toLowerCase().replace(/[^a-z0-9]/g, ''));
                    if (headers.length === 0 || headers.join('').trim() === '') {{
                        showUploadStatus('Error: Could not read headers from the first row of your CSV file.', 'error');
                        return;
                    }}
                    
                    let fnIdx = headers.findIndex(h => h.includes('firstname') || h.includes('first'));
                    let lnIdx = headers.findIndex(h => h.includes('lastname') || h.includes('last'));
                    let emailIdx = headers.findIndex(h => h.includes('email') || h.includes('mail'));
                    let phoneIdx = headers.findIndex(h => h.includes('phone') || h.includes('tel') || h.includes('mobile'));
                    let compIdx = headers.findIndex(h => h.includes('company') || h.includes('business'));
                    
                    if (fnIdx === -1 && lnIdx === -1 && emailIdx === -1 && phoneIdx === -1 && compIdx === -1) {{
                        fnIdx = 0; lnIdx = 1; emailIdx = 2; phoneIdx = 3; compIdx = 4;
                    }}
                    
                    let list = [];
                    for (let i = 1; i < lines.length; i++) {{
                        const line = lines[i].trim();
                        if (!line) continue;
                        
                        const row = parseCSVLine(line);
                        if (row.length === 0 || row.join('').trim() === '') continue;
                        
                        const cust = {{
                            first_name: fnIdx !== -1 && row[fnIdx] ? row[fnIdx] : "",
                            last_name: lnIdx !== -1 && row[lnIdx] ? row[lnIdx] : "",
                            email: emailIdx !== -1 && row[emailIdx] ? row[emailIdx] : "",
                            phone: phoneIdx !== -1 && row[phoneIdx] ? row[phoneIdx] : "",
                            company_name: compIdx !== -1 && row[compIdx] ? row[compIdx] : ""
                        }};
                        
                        if (cust.first_name || cust.last_name || cust.email || cust.phone || cust.company_name) {{
                            list.push(cust);
                        }}
                    }}
                    
                    parsedExclusions = list;
                    showUploadStatus(`✓ Loaded ${{list.length}} exclusions from "${{filename}}". Save changes to apply!`, 'success');
                    
                    // Option B: Visual Pulse & Highlight of settings submit button
                    const btnSubmit = document.getElementById('btn-settings-submit');
                    if (btnSubmit) {{
                        btnSubmit.classList.add('btn-pulse-save');
                        btnSubmit.innerHTML = ` Save Changes (Includes ${{list.length}} Uploaded Exclusions!)`;
                    }}
                }}

                function showUploadStatus(message, type) {{
                    const statusBox = document.getElementById('upload-status-box');
                    if (statusBox) {{
                        statusBox.innerText = message;
                        statusBox.className = type === 'success' ? 'alert alert-success' : 'alert alert-error';
                        statusBox.style.display = 'block';
                    }}
                }}

                function triggerSampleSheetDownload() {{
                    const headers = ["First Name", "Last Name", "Email", "Phone Number", "Company Name"];
                    const sampleRows = [
                        ["John", "Doe", "john.doe@example.com", "555-123-4567", "Doe Plumbing Inc"],
                        ["Jane", "Smith", "jane@company.com", "555-987-6543", "Smith Solar Corp"]
                    ];
                    let csvContent = "data:text/csv;charset=utf-8,";
                    csvContent += headers.join(",") + "\\n";
                    sampleRows.forEach(row => {{
                        csvContent += row.join(",") + "\\n";
                    }});
                    const encodedUri = encodeURI(csvContent);
                    const link = document.createElement("a");
                    link.setAttribute("href", encodedUri);
                    link.setAttribute("download", "sample_customer_exclusions.csv");
                    document.body.appendChild(link);
                    link.click();
                    document.body.removeChild(link);
                }}
                
                function copyText(id, btnId) {{
                    const copyText = document.getElementById(id);
                    copyText.select();
                    copyText.setSelectionRange(0, 99999);
                    navigator.clipboard.writeText(copyText.value);
                    
                    const copyBtn = document.getElementById(btnId);
                    copyBtn.innerText = "Copied!";
                    copyBtn.style.backgroundColor = "#1b5e20";
                    setTimeout(() => {{
                        copyBtn.innerText = " Copy";
                        copyBtn.style.backgroundColor = "#2e7d32";
                    }}, 2000);
                }}
                
                async function generateInvitationLink() {{
                    const emailInput = document.getElementById('invite_email');
                    const roleInput = document.getElementById('invite_role');
                    const alertBox = document.getElementById('invite-alert');
                    const linkContainer = document.getElementById('invite-link-container');
                    const urlOutput = document.getElementById('invite-url-output');
                    
                    alertBox.style.display = 'none';
                    linkContainer.style.display = 'none';
                    
                    const email = emailInput.value.trim();
                    const role = roleInput.value;
                    
                    if (!email) {{
                        alertBox.innerText = 'Please enter a valid email address.';
                        alertBox.className = 'alert alert-error';
                        alertBox.style.display = 'block';
                        return;
                    }}
                    
                    try {{
                        const response = await fetch('/dashboard/invite', {{
                            method: 'POST',
                            headers: {{
                                'Content-Type': 'application/json'
                            }},
                            body: JSON.stringify({{
                                email: email,
                                role: role,
                                client_id: {active_client_id}
                            }})
                        }});
                        
                        const data = await response.json();
                        
                        if (response.ok) {{
                            const origin = window.location.origin;
                            const inviteUrl = `${{origin}}/register?invite_token=${{data.token}}`;
                            urlOutput.value = inviteUrl;
                            linkContainer.style.display = 'block';
                            emailInput.value = '';
                        }} else {{
                            throw new Error(data.detail || 'Failed to generate invitation.');
                        }}
                    }} catch (error) {{
                        alertBox.innerText = 'Error: ' + error.message;
                        alertBox.className = 'alert alert-error';
                        alertBox.style.display = 'block';
                    }}
                }}

                async function updateCollaboratorRole(email, role) {{
                    if (!confirm(`Are you sure you want to change the access level of ${{email}} to ${{role === 'full' ? 'Full Function (Manager)' : 'Read-Only (Viewer)'}}?`)) {{
                        location.reload();
                        return;
                    }}
                    try {{
                        const response = await fetch('/dashboard/user/update-role', {{
                            method: 'POST',
                            headers: {{
                                'Content-Type': 'application/json'
                            }},
                            body: JSON.stringify({{ email: email, role: role }})
                        }});
                        const data = await response.json();
                        if (response.ok) {{
                            alert(data.message || 'Access level updated successfully!');
                            location.reload();
                        }} else {{
                            alert('Error: ' + (data.detail || 'Failed to update access level.'));
                            location.reload();
                        }}
                    }} catch (err) {{
                        alert('Network Error: ' + err.message);
                        location.reload();
                    }}
                }}

                async function deleteCollaborator(email) {{
                    if (!confirm(`⚠️ WARNING: Are you sure you want to delete ${{email}}? This will immediately revoke their access and terminate any active sessions.`)) {{
                        return;
                    }}
                    try {{
                        const response = await fetch('/dashboard/user/delete', {{
                            method: 'POST',
                            headers: {{
                                'Content-Type': 'application/json'
                            }},
                            body: JSON.stringify({{ email: email }})
                        }});
                        const data = await response.json();
                        if (response.ok) {{
                            alert(data.message || 'User deleted successfully!');
                            location.reload();
                        }} else {{
                            alert('Error: ' + (data.detail || 'Failed to delete user.'));
                        }}
                    }} catch (err) {{
                        alert('Network Error: ' + err.message);
                    }}
                }}

                async function updateInviteRole(token, role) {{
                    try {{
                        const response = await fetch('/dashboard/invite/update-role', {{
                            method: 'POST',
                            headers: {{
                                'Content-Type': 'application/json'
                            }},
                            body: JSON.stringify({{ token: token, role: role }})
                        }});
                        const data = await response.json();
                        if (response.ok) {{
                            alert(data.message || 'Invitation access level updated successfully!');
                            location.reload();
                        }} else {{
                            alert('Error: ' + (data.detail || 'Failed to update invitation role.'));
                            location.reload();
                        }}
                    }} catch (err) {{
                        alert('Network Error: ' + err.message);
                        location.reload();
                    }}
                }}

                async function deleteInvite(token) {{
                    if (!confirm('Are you sure you want to cancel and delete this invitation? The registration link will be permanently invalidated.')) {{
                        return;
                    }}
                    try {{
                        const response = await fetch('/dashboard/invite/delete', {{
                            method: 'POST',
                            headers: {{
                                'Content-Type': 'application/json'
                            }},
                            body: JSON.stringify({{ token: token }})
                        }});
                        const data = await response.json();
                        if (response.ok) {{
                            alert(data.message || 'Invitation deleted successfully!');
                            location.reload();
                        }} else {{
                            alert('Error: ' + (data.detail || 'Failed to delete invitation.'));
                        }}
                    }} catch (err) {{
                        alert('Network Error: ' + err.message);
                    }}
                }}

                async function submitSettings(event) {{
                    event.preventDefault();
                    const alertBox = document.getElementById('alert-box');
                    const btnSubmit = document.querySelector('.btn-submit');
                    
                    alertBox.style.display = 'none';
                    btnSubmit.disabled = true;
                    btnSubmit.innerText = 'Saving changes...';
                    
                    const actionRadio = document.querySelector('input[name="exclusion_upload_action"]:checked');
                    const exclusionActionValue = actionRadio ? actionRadio.value : 'append';
                    
                    const payload = {{
                        id: {active_client_id},
                        name: document.getElementById('name').value.trim(),
                        call_tracking_provider: document.getElementById('call_tracking_provider').value,
                        callrail_account_id: document.getElementById('callrail_account_id').value.trim(),
                        callrail_company_id: document.getElementById('callrail_company_id').value.trim(),
                        ctm_account_id: document.getElementById('ctm_account_id').value.trim(),
                        ctm_profile_id: document.getElementById('ctm_profile_id').value.trim(),
                        wc_account_id: document.getElementById('wc_account_id').value.trim(),
                        wc_profile_id: document.getElementById('wc_profile_id').value.trim(),
                        google_ads_customer_id: document.getElementById('google_ads_customer_id').value.trim(),
                        facebook_ads_id: document.getElementById('facebook_ads_id').value.trim(),
                        linkedin_ads_id: document.getElementById('linkedin_ads_id').value.trim(),
                        microsoft_ads_id: document.getElementById('microsoft_ads_id').value.trim(),
                        tiktok_ads_id: document.getElementById('tiktok_ads_id').value.trim(),
                        twitter_ads_id: document.getElementById('twitter_ads_id').value.trim(),
                        pinterest_ads_id: document.getElementById('pinterest_ads_id').value.trim(),
                        snapchat_ads_id: document.getElementById('snapchat_ads_id').value.trim(),
                        snapchat_ads_id: document.getElementById('snapchat_ads_id').value.trim(),
                        chatgpt_ads_id: document.getElementById('chatgpt_ads_id').value.trim(),
                        reddit_ads_id: document.getElementById('reddit_ads_id').value.trim(),
                        chatgpt_ads_id: document.getElementById('chatgpt_ads_id').value.trim(),
                        reddit_ads_id: document.getElementById('reddit_ads_id').value.trim(),
                        tiktok_ads_id: document.getElementById('tiktok_ads_id').value.trim(),
                        twitter_ads_id: document.getElementById('twitter_ads_id').value.trim(),
                        pinterest_ads_id: document.getElementById('pinterest_ads_id').value.trim(),
                        snapchat_ads_id: document.getElementById('snapchat_ads_id').value.trim(),
                        snapchat_ads_id: document.getElementById('snapchat_ads_id').value.trim(),
                        chatgpt_ads_id: document.getElementById('chatgpt_ads_id').value.trim(),
                        reddit_ads_id: document.getElementById('reddit_ads_id').value.trim(),
                        chatgpt_ads_id: document.getElementById('chatgpt_ads_id').value.trim(),
                        reddit_ads_id: document.getElementById('reddit_ads_id').value.trim(),
                        lead_gen_method: document.querySelector('input[name="lead_gen_method"]:checked').value,
                        qualification_criteria: document.getElementById('qualification_criteria').value,
                        source_of_truth: document.getElementById('source_of_truth').value,
                        email_provider: document.getElementById('email_provider').value,
                        email_account: document.getElementById('email_account').value.trim(),
                        email_app_password: document.getElementById('email_app_password') ? document.getElementById('email_app_password').value.trim() : '',
                        email_account_2: document.getElementById('email_account_2') ? document.getElementById('email_account_2').value.trim() : '',
                        email_app_password_2: document.getElementById('email_app_password_2') ? document.getElementById('email_app_password_2').value.trim() : '',
                        email_account_3: document.getElementById('email_account_3') ? document.getElementById('email_account_3').value.trim() : '',
                        email_app_password_3: document.getElementById('email_app_password_3') ? document.getElementById('email_app_password_3').value.trim() : '',
                        email_account_4: document.getElementById('email_account_4') ? document.getElementById('email_account_4').value.trim() : '',
                        email_app_password_4: document.getElementById('email_app_password_4') ? document.getElementById('email_app_password_4').value.trim() : '',
                        email_account_5: document.getElementById('email_account_5') ? document.getElementById('email_account_5').value.trim() : '',
                        email_app_password_5: document.getElementById('email_app_password_5') ? document.getElementById('email_app_password_5').value.trim() : '',
                        email_app_password: document.getElementById('email_app_password').value.trim(),
                        email_account_2: document.getElementById('email_account_2') ? document.getElementById('email_account_2').value.trim() : '',
                        email_app_password_2: document.getElementById('email_app_password_2') ? document.getElementById('email_app_password_2').value.trim() : '',
                        email_account_3: document.getElementById('email_account_3') ? document.getElementById('email_account_3').value.trim() : '',
                        email_app_password_3: document.getElementById('email_app_password_3') ? document.getElementById('email_app_password_3').value.trim() : '',
                        email_account_4: document.getElementById('email_account_4') ? document.getElementById('email_account_4').value.trim() : '',
                        email_app_password_4: document.getElementById('email_app_password_4') ? document.getElementById('email_app_password_4').value.trim() : '',
                        email_account_5: document.getElementById('email_account_5') ? document.getElementById('email_account_5').value.trim() : '',
                        email_app_password_5: document.getElementById('email_app_password_5') ? document.getElementById('email_app_password_5').value.trim() : '',
                        crm_deal_tags: document.getElementById('crm_deal_tags').value.trim(),
                        crm_won_deal_tags: document.getElementById('crm_won_deal_tags').value.trim(),
                        crm_value_field: document.getElementById('crm_value_field') ? document.getElementById('crm_value_field').value.trim() : '',
                        crm_lead_tags: document.getElementById('crm_lead_tags').value.trim(),
                        lead_count_rule: document.querySelector('input[name="lead_count_rule"]:checked').value,
                        exclude_past_customers: document.querySelector('input[name="exclude_past_customers"]:checked').value,
                        exclusion_action: exclusionActionValue,
                        excluded_customers: parsedExclusions
                    }};
                    
                    try {{
                        const response = await fetch('/dashboard/settings', {{
                            method: 'POST',
                            headers: {{
                                'Content-Type': 'application/json'
                            }},
                            body: JSON.stringify(payload)
                        }});
                        
                        const data = await response.json();
                        
                        if (response.ok) {{
                            alertBox.innerText = `Success! Configuration settings for "${{payload.name}}" saved successfully.`;
                            alertBox.className = 'alert alert-success';
                            alertBox.style.display = 'block';
                            btnSubmit.disabled = false;
                            btnSubmit.innerText = ' Save Configuration Changes';
                            window.scrollTo({{ top: 0, behavior: 'smooth' }});
                        }} else {{
                            throw new Error(data.detail || 'An unexpected error occurred.');
                        }}
                    }} catch (error) {{
                        alertBox.innerText = 'Error: ' + error.message;
                        alertBox.className = 'alert alert-error';
                        alertBox.style.display = 'block';
                        btnSubmit.disabled = false;
                        btnSubmit.innerText = ' Save Configuration Changes';
                        window.scrollTo({{ top: 0, behavior: 'smooth' }});
                    }}
                }}
            </script>
        </body>
    </html>
    """


@router.post("/dashboard/user/update-role")
def update_user_role(request: Request, req_data: UserRoleUpdate):
    email = is_authenticated(request)
    if not email:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    user_role, user_client_id = get_user_role_and_client(email)
    if user_role != "full":
        raise HTTPException(status_code=403, detail="Unauthorized: Only managers and administrators can manage roles.")
    
    target_email = req_data.email.strip().lower()
    if email.strip().lower() == target_email:
        raise HTTPException(status_code=400, detail="You cannot modify your own role.")
        
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        
        # Verify the target user exists and belongs to the same client_id (if restricted)
        cursor.execute("SELECT client_id FROM users WHERE LOWER(TRIM(email)) = ?", (target_email,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            raise HTTPException(status_code=404, detail="User not found.")
            
        target_client_id = row[0]
        if user_client_id is not None and target_client_id != user_client_id:
            conn.close()
            raise HTTPException(status_code=403, detail="Unauthorized: You do not have permission to manage this user.")
            
        cursor.execute("UPDATE users SET role = ? WHERE LOWER(TRIM(email)) = ?", (req_data.role, target_email))
        conn.commit()
        conn.close()
        return {"status": "success", "message": f"Successfully updated role for {target_email} to {req_data.role}."}
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@router.post("/dashboard/user/delete")
def delete_user(request: Request, req_data: UserDelete):
    email = is_authenticated(request)
    if not email:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    user_role, user_client_id = get_user_role_and_client(email)
    if user_role != "full":
        raise HTTPException(status_code=403, detail="Unauthorized: Only managers and administrators can delete users.")
    
    target_email = req_data.email.strip().lower()
    if email.strip().lower() == target_email:
        raise HTTPException(status_code=400, detail="You cannot delete your own account.")
        
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        
        # Verify the target user exists and belongs to the same client_id (if restricted)
        cursor.execute("SELECT client_id FROM users WHERE LOWER(TRIM(email)) = ?", (target_email,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            raise HTTPException(status_code=404, detail="User not found.")
            
        target_client_id = row[0]
        if user_client_id is not None and target_client_id != user_client_id:
            conn.close()
            raise HTTPException(status_code=403, detail="Unauthorized: You do not have permission to delete this user.")
            
        cursor.execute("DELETE FROM users WHERE LOWER(TRIM(email)) = ?", (target_email,))
        # Also clean up any active sessions for the deleted user
        cursor.execute("DELETE FROM user_sessions WHERE LOWER(TRIM(email)) = ?", (target_email,))
        
        conn.commit()
        conn.close()
        return {"status": "success", "message": f"Successfully removed user {target_email} from the platform."}
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@router.post("/dashboard/invite/update-role")
def update_invite_role(request: Request, req_data: InviteRoleUpdate):
    email = is_authenticated(request)
    if not email:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    user_role, user_client_id = get_user_role_and_client(email)
    if user_role != "full":
        raise HTTPException(status_code=403, detail="Unauthorized: Only managers and administrators can manage invitations.")
        
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        
        cursor.execute("SELECT client_id FROM user_invitations WHERE token = ? AND is_used = 'NO'", (req_data.token,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            raise HTTPException(status_code=404, detail="Active invitation not found.")
            
        target_client_id = row[0]
        if user_client_id is not None and target_client_id != user_client_id:
            conn.close()
            raise HTTPException(status_code=403, detail="Unauthorized: You do not have permission to manage this invitation.")
            
        cursor.execute("UPDATE user_invitations SET role = ? WHERE token = ?", (req_data.role, req_data.token))
        conn.commit()
        conn.close()
        return {"status": "success", "message": "Successfully updated invitation role."}
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@router.post("/dashboard/invite/delete")
def delete_invite_endpoint(request: Request, req_data: InviteDelete):
    email = is_authenticated(request)
    if not email:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    user_role, user_client_id = get_user_role_and_client(email)
    if user_role != "full":
        raise HTTPException(status_code=403, detail="Unauthorized: Only managers and administrators can delete invitations.")
        
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        
        cursor.execute("SELECT client_id FROM user_invitations WHERE token = ?", (req_data.token,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            raise HTTPException(status_code=404, detail="Invitation not found.")
            
        target_client_id = row[0]
        if user_client_id is not None and target_client_id != user_client_id:
            conn.close()
            raise HTTPException(status_code=403, detail="Unauthorized: You do not have permission to delete this invitation.")
            
        cursor.execute("DELETE FROM user_invitations WHERE token = ?", (req_data.token,))
        conn.commit()
        conn.close()
        return {"status": "success", "message": "Successfully deleted/canceled the invitation."}
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@router.post("/dashboard/invite")
def create_user_invitation(request: Request, invite: UserInvite):
    email = is_authenticated(request)
    if not email:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    user_role, user_client_id = get_user_role_and_client(email)
    if user_role != "full":
        raise HTTPException(status_code=403, detail="Unauthorized: Only managers and administrators can invite collaborators.")
    
    # If the user is a restricted client manager, they can only invite users to their own client_id
    resolved_client_id = invite.client_id
    if user_client_id is not None:
        resolved_client_id = user_client_id
        
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        
        # Verify the client exists
        if resolved_client_id is not None:
            cursor.execute("SELECT id FROM clients WHERE id = ?", (resolved_client_id,))
            if not cursor.fetchone():
                conn.close()
                raise HTTPException(status_code=400, detail="Invalid client ID.")
                
        # Generate a secure 32-character token
        import secrets
        token = secrets.token_hex(16)
        
        # Delete any existing invitation for this email first (to simulate REPLACE/UPSERT on both SQLite & Postgres)
        cursor.execute("DELETE FROM user_invitations WHERE email = ?", (invite.email.strip().lower(),))
        
        # Insert the invitation cleanly
        cursor.execute("""
            INSERT INTO user_invitations (email, role, client_id, token, invited_by)
            VALUES (?, ?, ?, ?, ?)
        """, (invite.email.strip().lower(), invite.role, resolved_client_id, token, email))
        
        conn.commit()
        conn.close()
        
        return {"status": "success", "token": token}
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@router.post("/dashboard/upload-sales")
async def dashboard_upload_sales(
    request: Request,
    file: UploadFile = File(...),
    client_id: int = Form(...)
):
    email = is_authenticated(request)
    if not email:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    user_role, user_client_id = get_user_role_and_client(email)
    if user_role != "full":
        raise HTTPException(status_code=403, detail="Unauthorized: Only managers and administrators can upload sales reports.")
    if user_client_id is not None and client_id != user_client_id:
        raise HTTPException(status_code=403, detail="Unauthorized: You do not have permission to upload sales for this client.")
        
    contents = await file.read()
    import io
    try:
        if file.filename.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(contents))
        else:
            df = pd.read_excel(io.BytesIO(contents))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read file: {e}")
        
    columns = list(df.columns)
    phone_col, email_col, value_col, name_col, company_col = find_dynamic_columns_custom(columns)
    
    if not phone_col and not email_col:
        raise HTTPException(status_code=400, detail=f"Mapping Failure: Could not locate a valid phone or email contact column in headers: {columns}")
        
    stats = {"processed": 0, "successful_matches": 0, "organic_logged": 0, "errors": 0}
    
    conn = db_router.connect()
    cursor = conn.cursor()
    
    try:
        for _, row in df.iterrows():
            stats["processed"] += 1
            
            raw_phone = str(row[phone_col]) if phone_col and pd.notna(row[phone_col]) else ""
            raw_email = str(row[email_col]) if email_col and pd.notna(row[email_col]) else ""
            raw_value = row[value_col] if value_col and pd.notna(row[value_col]) else 0.0
            raw_name = str(row[name_col]).strip() if name_col and pd.notna(row[name_col]) else ""
            raw_company = str(row[company_col]).strip() if company_col and pd.notna(row[company_col]) else ""
            
            norm_phone = normalize_phone(raw_phone)
            norm_email = normalize_email(raw_email)
            
            try:
                clean_val_str = re.sub(r"[^\d.]", "", str(raw_value))
                value = float(clean_val_str) if clean_val_str else 0.0
            except (ValueError, TypeError):
                value = 0.0
                
            if not norm_phone and not norm_email and not raw_name and not raw_company:
                stats["errors"] += 1
                continue
                
            matching_session_id = None
            match_reason = ""
            match_fuzzy = "NO"
            certainty_score = 100
            current_status = None
            
            # Tier 1: Search by Phone Match
            if norm_phone:
                cursor.execute("""
                    SELECT id, gclid, fbclid, msclkid, li_fat_id, sale_closed, value 
                    FROM sessions 
                    WHERE client_id = ? AND phone = ?
                    ORDER BY created_at DESC LIMIT 1
                """, (client_id, norm_phone))
                row_match = cursor.fetchone()
                if row_match:
                    matching_session_id, g, fb, ms, li, sale_closed, val = row_match
                    current_status = (sale_closed, val)
                    match_reason = f"Successfully matched closed transaction via spreadsheet upload (Phone Match: {norm_phone})."
                    
            # Tier 2: Search by Email Match
            if not matching_session_id and norm_email:
                cursor.execute("""
                    SELECT id, gclid, fbclid, msclkid, li_fat_id, sale_closed, value 
                    FROM sessions 
                    WHERE client_id = ? AND email = ?
                    ORDER BY created_at DESC LIMIT 1
                """, (client_id, norm_email))
                row_match = cursor.fetchone()
                if row_match:
                    matching_session_id, g, fb, ms, li, sale_closed, val = row_match
                    current_status = (sale_closed, val)
                    match_reason = f"Successfully matched closed transaction via spreadsheet upload (Email Match: {norm_email})."
                    
            # Tier 3 & 4: Fuzzy Match against Active Sessions
            if not matching_session_id:
                cursor.execute("""
                    SELECT id, phone, email, name, company, gclid, fbclid, msclkid, li_fat_id, sale_closed, value 
                    FROM sessions 
                    WHERE client_id = ? AND (sale_closed IS NULL OR sale_closed = 'NO')
                    ORDER BY created_at DESC
                """)
                unclosed_sessions = cursor.fetchall()
                
                best_match_id = None
                best_score = 0
                best_reason = ""
                best_click_vals = None
                
                for sess in unclosed_sessions:
                    s_id, s_phone, s_email, s_name, s_company, s_g, s_fb, s_ms, s_li, s_closed, s_val = sess
                    
                    # Fuzzy Company Matching (Tier 3)
                    if raw_company and s_company:
                        score = calculate_company_similarity(raw_company, s_company)
                        if score >= 0.85 and int(score * 100) > best_score:
                            best_score = int(score * 100)
                            best_match_id = s_id
                            best_reason = f"Successfully matched closed transaction via Fuzzy Company Match ('{raw_company.strip()}' ➡️ '{s_company.strip()}')."
                            best_click_vals = (s_closed, s_val)
                            
                    # Fuzzy Name Matching (Tier 4)
                    if raw_name and s_name:
                        score = check_name_transposition(raw_name, s_name)
                        if score >= 0.80 and int(score * 100) > best_score:
                            best_score = int(score * 100)
                            best_match_id = s_id
                            best_reason = f"Successfully matched closed transaction via Fuzzy Name Match ('{raw_name.strip()}' ➡️ '{s_name.strip()}')."
                            best_click_vals = (s_closed, s_val)
                            
                if best_match_id:
                    matching_session_id = best_match_id
                    s_closed, s_val = best_click_vals
                    current_status = (s_closed, s_val)
                    match_fuzzy = "YES"
                    certainty_score = best_score
                    match_reason = best_reason
                    
            # Update database
            if not matching_session_id:
                cursor.execute("""
                    INSERT INTO sessions (
                        client_id, phone, email, name, company, source, qualified, sale_closed, value, reason, model_used, match_fuzzy, certainty_score
                    ) VALUES (?, ?, ?, ?, ?, 'dashboard_upload', 'NO', 'YES', ?, ?, 'Dashboard Spreadsheet Ingest', 'NO', 100)
                """, (
                    client_id,
                    norm_phone or None,
                    norm_email or None,
                    raw_name or "Dashboard Export Lead",
                    raw_company or None,
                    value,
                    "Organic transaction saved: No corresponding historical click-session detected.",
                ))
                stats["organic_logged"] += 1
            else:
                if current_status and current_status[0] == "YES" and current_status[1] >= value:
                    continue
                    
                cursor.execute("""
                    UPDATE sessions SET 
                        sale_closed = 'YES',
                        value = ?,
                        reason = ?,
                        model_used = 'Dashboard Spreadsheet Ingest',
                        match_fuzzy = ?,
                        certainty_score = ?
                    WHERE id = ?
                """, (value, match_reason, match_fuzzy, certainty_score, matching_session_id))
                stats["successful_matches"] += 1
                
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Database upload error: {str(e)}")
    finally:
        conn.close()
        
    return {"status": "success", "stats": stats}


@router.post("/dashboard/settings")
def update_client_settings(request: Request, client: ClientUpdate):
    email = is_authenticated(request)
    if not email:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    user_role, user_client_id = get_user_role_and_client(email)
    if user_role != "full":
        raise HTTPException(status_code=403, detail="Unauthorized: Client setting modifications are restricted to managers and administrators.")
    if user_client_id is not None and client.id != user_client_id:
        raise HTTPException(status_code=403, detail="Unauthorized: You do not have permission to modify settings for this client account.")
    """Endpoint to handle questionnaire form settings update."""
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        
        # Extract column names dynamically
        cursor.execute("PRAGMA table_info(clients)")
        cols = [col[1] for col in cursor.fetchall()]
        
        # Verify client exists and fetch old settings using explicit columns order to avoid zip misalignment on PostgreSQL
        cols_formatted = ", ".join([f'"{c}"' for c in cols])
        cursor.execute(f"SELECT {cols_formatted} FROM clients WHERE id = ?", (client.id,))
        client_row = cursor.fetchone()
        if not client_row:
            raise HTTPException(status_code=404, detail="Client not found")
            
        old_data = dict(zip(cols, client_row))
        
        # Field label mappings for change history log
        FIELD_LABELS = {
            "name": "Client Business Name",
            "call_tracking_provider": "Call Tracking Provider",
            "callrail_account_id": "CallRail Account ID",
            "callrail_company_id": "CallRail Client ID",
            "ctm_account_id": "CallTrackingMetrics Account ID",
            "ctm_profile_id": "CallTrackingMetrics Client ID",
            "wc_account_id": "WhatConverts Account ID",
            "wc_profile_id": "WhatConverts Client ID",
            "google_ads_customer_id": "Google Ads Customer ID",
            "facebook_ads_id": "Facebook Ads Pixel/Account ID",
            "linkedin_ads_id": "LinkedIn Ads Account ID",
            "microsoft_ads_id": "Microsoft Ads Account ID",
            "tiktok_ads_id": "TikTok Ads Pixel/Account ID",
            "twitter_ads_id": "X (Twitter) Ads Pixel ID",
            "pinterest_ads_id": "Pinterest Ads ID",
            "snapchat_ads_id": "Snapchat Ads Pixel ID",
            "chatgpt_ads_id": "ChatGPT Ads ID",
            "reddit_ads_id": "Reddit Ads Account / Pixel ID",
            "lead_gen_method": "Lead Gen Method",
            "qualification_criteria": "Qualification Criteria Option",
            "source_of_truth": "Single Source of Truth",
            "email_provider": "Email Provider",
            "email_account": "Email Integration Account #1",
            "email_app_password": "Email Integration App Password #1",
            "email_account_2": "Email Integration Account #2",
            "email_app_password_2": "Email Integration App Password #2",
            "email_account_3": "Email Integration Account #3",
            "email_app_password_3": "Email Integration App Password #3",
            "email_account_4": "Email Integration Account #4",
            "email_app_password_4": "Email Integration App Password #4",
            "email_account_5": "Email Integration Account #5",
            "email_app_password_5": "Email Integration App Password #5",
            "crm_deal_tags": "CRM Deal Tags",
            "crm_won_deal_tags": "CRM Won Deal Tags",
            "crm_value_field": "Google Sheet Transaction Value Column",
            "crm_lead_tags": "CRM Lead Qualification Tags",
            "lead_count_rule": "Lead Count Optimization Rule",
            "exclude_past_customers": "Exclude Past Customers Setting"
        }
        
        new_data = {
            "name": client.name,
            "call_tracking_provider": client.call_tracking_provider or "callrail",
            "callrail_account_id": client.callrail_account_id or "",
            "callrail_company_id": client.callrail_company_id or "",
            "ctm_account_id": client.ctm_account_id or "",
            "ctm_profile_id": client.ctm_profile_id or "",
            "wc_account_id": client.wc_account_id or "",
            "wc_profile_id": client.wc_profile_id or "",
            "google_ads_customer_id": client.google_ads_customer_id,
            "facebook_ads_id": client.facebook_ads_id or "",
            "linkedin_ads_id": client.linkedin_ads_id or "",
            "microsoft_ads_id": client.microsoft_ads_id or "",
            "tiktok_ads_id": client.tiktok_ads_id or "",
            "twitter_ads_id": client.twitter_ads_id or "",
            "pinterest_ads_id": client.pinterest_ads_id or "",
            "snapchat_ads_id": client.snapchat_ads_id or "",
            "chatgpt_ads_id": client.chatgpt_ads_id or "",
            "reddit_ads_id": client.reddit_ads_id or "",
            "lead_gen_method": client.lead_gen_method,
            "qualification_criteria": client.qualification_criteria,
            "source_of_truth": client.source_of_truth,
            "email_provider": client.email_provider or "",
            "email_account": client.email_account or "",
            "email_app_password": client.email_app_password or "",
            "email_account_2": client.email_account_2 or "",
            "email_app_password_2": client.email_app_password_2 or "",
            "email_account_3": client.email_account_3 or "",
            "email_app_password_3": client.email_app_password_3 or "",
            "email_account_4": client.email_account_4 or "",
            "email_app_password_4": client.email_app_password_4 or "",
            "email_account_5": client.email_account_5 or "",
            "email_app_password_5": client.email_app_password_5 or "",
            "crm_deal_tags": client.crm_deal_tags or "",
            "crm_won_deal_tags": client.crm_won_deal_tags or "",
            "crm_value_field": getattr(client, 'crm_value_field', '') or "",
            "crm_lead_tags": client.crm_lead_tags or "",
            "lead_count_rule": client.lead_count_rule,
            "exclude_past_customers": client.exclude_past_customers
        }

        # Scan each field and write updates to client_config_history
        for field, label in FIELD_LABELS.items():
            old_val = str(old_data.get(field) or "").strip()
            new_val = str(new_data.get(field) or "").strip()
            if old_val != new_val:
                cursor.execute("""
                    INSERT INTO client_config_history (client_id, changed_by, feature_name, old_value, new_value)
                    VALUES (?, ?, ?, ?, ?)
                """, (client.id, email, label, old_val, new_val))
            
        # Update settings
        cursor.execute("""
            UPDATE clients SET
                name = ?,
                callrail_account_id = ?,
                callrail_company_id = ?,
                google_ads_customer_id = ?,
                facebook_ads_id = ?,
                linkedin_ads_id = ?,
                microsoft_ads_id = ?,
                tiktok_ads_id = ?,
                twitter_ads_id = ?,
                pinterest_ads_id = ?,
                snapchat_ads_id = ?,
                chatgpt_ads_id = ?,
                reddit_ads_id = ?,
                lead_gen_method = ?,
                qualification_criteria = ?,
                source_of_truth = ?,
                email_provider = ?,
                email_account = ?,
                email_app_password = ?,
                email_account_2 = ?,
                email_app_password_2 = ?,
                email_account_3 = ?,
                email_app_password_3 = ?,
                email_account_4 = ?,
                email_app_password_4 = ?,
                email_account_5 = ?,
                email_app_password_5 = ?,
                crm_deal_tags = ?,
                crm_won_deal_tags = ?,
                crm_value_field = ?,
                crm_lead_tags = ?,
                lead_count_rule = ?,
                exclude_past_customers = ?,
                call_tracking_provider = ?,
                ctm_account_id = ?,
                ctm_profile_id = ?,
                wc_account_id = ?,
                wc_profile_id = ?
            WHERE id = ?
        """, (
            str(client.name or ""),
            client.callrail_account_id or None,
            client.callrail_company_id or None,
            str(client.google_ads_customer_id or ""),
            str(client.facebook_ads_id or ""),
            str(client.linkedin_ads_id or ""),
            str(client.microsoft_ads_id or ""),
            str(client.tiktok_ads_id or ""),
            str(client.twitter_ads_id or ""),
            str(client.pinterest_ads_id or ""),
            str(client.snapchat_ads_id or ""),
            str(client.chatgpt_ads_id or ""),
            str(getattr(client, 'reddit_ads_id', '') or ""),
            str(client.lead_gen_method or "both"),
            str(client.qualification_criteria or "ai_rules"),
            str(client.source_of_truth or "manual"),
            str(client.email_provider or ""),
            str(client.email_account or ""),
            str(client.email_app_password or ""),
            str(client.email_account_2 or ""),
            str(client.email_app_password_2 or ""),
            str(client.email_account_3 or ""),
            str(client.email_app_password_3 or ""),
            str(client.email_account_4 or ""),
            str(client.email_app_password_4 or ""),
            str(client.email_account_5 or ""),
            str(client.email_app_password_5 or ""),
            str(client.crm_deal_tags or ""),
            str(client.crm_won_deal_tags or ""),
            str(getattr(client, 'crm_value_field', '') or ""),
            str(client.crm_lead_tags or ""),
            str(client.lead_count_rule or "all"),
            str(client.exclude_past_customers or "NO"),
            str(client.call_tracking_provider or "callrail"),
            str(client.ctm_account_id or ""),
            str(client.ctm_profile_id or ""),
            str(client.wc_account_id or ""),
            str(client.wc_profile_id or ""),
            int(client.id)
        ))
        
        # Handle excluded customers updates if a new list was uploaded
        if client.excluded_customers is not None and len(client.excluded_customers) > 0:
            if getattr(client, 'exclusion_action', 'append') == 'replace':
                cursor.execute("DELETE FROM excluded_customers WHERE client_id = ?", (client.id,))
                
            for cust in client.excluded_customers:
                normalized_p = normalize_phone(cust.phone)
                email_clean = cust.email.strip().lower() if cust.email else ""
                
                # Check for duplicates before inserting in append mode
                if getattr(client, 'exclusion_action', 'append') == 'append':
                    exists = False
                    if normalized_p:
                        cursor.execute("SELECT id FROM excluded_customers WHERE client_id = ? AND phone = ?", (client.id, normalized_p))
                        if cursor.fetchone():
                            exists = True
                    if not exists and email_clean:
                        cursor.execute("SELECT id FROM excluded_customers WHERE client_id = ? AND email = ?", (client.id, email_clean))
                        if cursor.fetchone():
                            exists = True
                    if exists:
                        continue # Skip duplicate record
                        
                cursor.execute("""
                    INSERT INTO excluded_customers (client_id, first_name, last_name, email, phone, company_name)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    client.id,
                    cust.first_name,
                    cust.last_name,
                    email_clean,
                    normalized_p,
                    cust.company_name
                ))
        
        conn.commit()
        conn.close()
        return {"status": "success", "message": f"Settings for '{client.name}' updated successfully!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database update error: {str(e)}")

@router.get("/dashboard/add-client", response_class=HTMLResponse)
def add_client_page(request: Request):
    email = is_authenticated(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)
    user_role, user_client_id = get_user_role_and_client(email)
    if user_role != "full" or user_client_id is not None:
        raise HTTPException(status_code=403, detail="Unauthorized: Client onboarding is restricted to Agency Administrators.")
    """Page to onboard a new client with complete wizard properties."""
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        cursor.execute("SELECT seq FROM sqlite_sequence WHERE name = 'clients'")
        row = cursor.fetchone()
        next_id = (row[0] + 1) if row else 1
        if not row:
            cursor.execute("SELECT MAX(id) FROM clients")
            max_row = cursor.fetchone()
            next_id = (max_row[0] + 1) if (max_row and max_row[0] is not None) else 1
        conn.close()
    except Exception:
        next_id = 1
    admin_link_html = ""
    if email in ADMIN_EMAILS:
        admin_link_html = ' | <a href="/admin/users" style="color: #2e7d32; text-decoration: none; font-weight: bold; margin-left: 5px;">️ Admin User Directory</a>'
        
    user_header_bar = f"""
    <div style="display: flex; justify-content: space-between; align-items: center; background-color: #f1f3f4; padding: 10px 15px; border-radius: 6px; margin-bottom: 20px; font-size: 13px;">
        <div>
            <span style="color: #666; font-weight: bold;"> Active Session:</span> <span style="font-weight: bold; color: #1a237e;">{email}</span>
            {admin_link_html}
        </div>
        <a href="/logout" style="color: #c62828; text-decoration: none; font-weight: bold; display: flex; align-items: center; gap: 4px;"> Log Out</a>
    </div>
    """

        
    html_content = """
    <!DOCTYPE html>
    <html>
        <head>
            <title>Onboard New Client </title>
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
                
                .btn-submit { display: inline-block; background-color: #1a237e; color: white !important; text-decoration: none; padding: 12px 24px; border: none; border-radius: 6px; font-weight: bold; font-size: 15px; cursor: pointer; transition: background 0.2s; }
                .btn-submit:hover { background-color: #0d1b2a; }
                .btn-cancel { color: #666 !important; text-decoration: none; font-size: 14px; font-weight: bold; display: inline-block; margin-top: 25px; }
                .btn-cancel:hover { color: #333 !important; }
                
                .alert { padding: 12px; border-radius: 6px; margin-bottom: 20px; display: none; font-size: 14px; font-weight: 600; }
                .alert-error { background-color: #ffebee; color: #c62828; border: 1px solid #ffcdd2; }
                .alert-success { background-color: #e8f5e9; color: #2e7d32; border: 1px solid #c8e6c9; }
                
                /* Helper classes */
                .conditional-box { background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none; animation: slideDown 0.3s ease-out; }
                @keyframes pulseHighlight {
                    0% { transform: scale(1); box-shadow: 0 0 0 0 rgba(46, 125, 50, 0.7); background-color: #2e7d32; }
                    50% { transform: scale(1.04); box-shadow: 0 0 0 12px rgba(46, 125, 50, 0); background-color: #1b5e20; }
                    100% { transform: scale(1); box-shadow: 0 0 0 0 rgba(46, 125, 50, 0); background-color: #2e7d32; }
                }
                .btn-pulse-save {
                    animation: pulseHighlight 2s infinite !important;
                    background-color: #2e7d32 !important;
                    border-color: #1b5e20 !important;
                    color: white !important;
                }
                
                
                .tab-btn {{ background: none; border: none; padding: 10px 15px; font-size: 13px; font-weight: bold; color: #666; cursor: pointer; border-bottom: 2px solid transparent; }}
                .tab-btn.active {{ color: #1a237e; border-bottom-color: #1a237e; }}
                .btn-modal-close {{ background-color: #1a237e; color: white; border: none; padding: 8px 18px; border-radius: 5px; font-weight: bold; font-size: 13px; cursor: pointer; transition: background 0.2s; }}
                .btn-modal-close:hover {{ background-color: #0d1b2a; }}

                /* Speech Bubble Tooltip Styles */
                .tooltip-icon {
                    position: relative;
                    display: inline-block;
                    cursor: help;
                    margin-left: 6px;
                    font-size: 14px;
                    vertical-align: middle;
                    color: #1a237e;
                }
                .tooltip-icon .tooltip-text {
                    visibility: hidden;
                    width: 320px;
                    background-color: #1a237e;
                    color: #fff;
                    text-align: left;
                    border-radius: 6px;
                    padding: 10px 12px;
                    position: absolute;
                    z-index: 1000;
                    bottom: 125%;
                    left: 50%;
                    margin-left: -160px;
                    opacity: 0;
                    transition: opacity 0.3s;
                    font-size: 11px;
                    line-height: 1.4;
                    font-weight: normal;
                    box-shadow: 0 4px 10px rgba(0,0,0,0.15);
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                }
                .tooltip-icon .tooltip-text::after {
                    content: "";
                    position: absolute;
                    top: 100%;
                    left: 50%;
                    margin-left: -5px;
                    border-width: 5px;
                    border-style: solid;
                    border-color: #1a237e transparent transparent transparent;
                }
                .tooltip-icon:hover .tooltip-text {
                    visibility: visible;
                    opacity: 1;
                }
                @keyframes slideDown {
                    from { opacity: 0; transform: translateY(-10px); }
                    to { opacity: 1; transform: translateY(0); }
                }
            
                /* Tooltip styling */
                .tooltip {
                    position: relative;
                    display: inline-flex;
                    align-items: center;
                    cursor: pointer;
                    margin-left: 5px;
                    color: #1a237e;
                    font-size: 14px;
                    vertical-align: middle;
                }
                .tooltip .tooltiptext {
                    visibility: hidden;
                    width: 250px;
                    background-color: #333;
                    color: #fff;
                    text-align: left;
                    border-radius: 6px;
                    padding: 10px;
                    position: absolute;
                    z-index: 100;
                    bottom: 125%; /* Position above the text */
                    left: 50%;
                    margin-left: -125px;
                    opacity: 0;
                    transition: opacity 0.3s;
                    font-size: 11px;
                    font-weight: normal;
                    line-height: 1.4;
                    box-shadow: 0px 4px 10px rgba(0,0,0,0.15);
                    white-space: normal;
                }
                .tooltip .tooltiptext::after {
                    content: "";
                    position: absolute;
                    top: 100%; /* At the bottom of the tooltip */
                    left: 50%;
                    margin-left: -5px;
                    border-width: 5px;
                    border-style: solid;
                    border-color: #333 transparent transparent transparent;
                }
                .tooltip:hover .tooltiptext {
                    visibility: visible;
                    opacity: 1;
                }
            </style>
        </head>
        <body>
            <div class="container">
                <h1 id="heading-title">Onboard Client Account </h1>
                <p class="subtitle" id="heading-subtitle">Agency Setup & Software Mapping Pipeline</p>
                
                <!-- Step Progress -->
                <div class="progress-container" id="progress-container">
                    <div class="progress-bar" id="progress-bar"></div>
                    <div class="step-circle active" data-step="1">1</div>
                    <div class="step-circle" data-step="2">2</div>
                    <div class="step-circle" data-step="3">3</div>
                    <div class="step-circle" data-step="4">4</div>
                    <div class="step-circle" data-step="5">5</div>
                </div>
                
                <div id="alert-box" class="alert alert-error"></div>
                
                <!-- Wizard Form -->
                <form id="onboarding-form">
                    
                    <!-- STEP 1: Accounts & Tracking channels -->
                    <div class="wizard-step active" id="step-panel-1">
                        <div class="instructions">
                             <strong>Step 1: General Profile & Ad Accounts</strong><br>
                            Enter the general company properties and the advertising account IDs to sync conversions and metrics.
                        </div>
                        
                        <div class="form-group">
                            <label for="name">Client Business Name</label>
                            <input type="text" id="name" required placeholder="e.g. Priority Plumbing">
                        </div>
                        
                        <div class="form-group">
                            <label for="call_tracking_provider">Call Tracking Provider</label>
                            <select id="call_tracking_provider" onchange="toggleCallTrackingFields()" style="width: 100%; padding: 12px; border-radius: 6px; border: 1px solid #ced4da; font-size: 14px; font-weight: 600;">
                                <option value="callrail" selected>CallRail</option>
                                <option value="calltrackingmetrics">CallTrackingMetrics</option>
                                <option value="whatconverts">WhatConverts</option>
                            </select>
                        </div>
                        
                        <div id="call_tracking_callrail_box" class="form-row" style="display: flex;">
                            <div class="form-group">
                                <label for="callrail_account_id">CallRail Account ID</label>
                                <input type="text" id="callrail_account_id" placeholder="e.g. 123456789">
                            </div>
                            <div class="form-group">
                                <label for="callrail_company_id">CallRail Client ID (Company ID)</label>
                                <input type="text" id="callrail_company_id" placeholder="e.g. 987654321">
                            </div>
                        </div>

                        <div id="call_tracking_ctm_box" class="form-row" style="display: none;">
                            <div class="form-group">
                                <label for="ctm_account_id">CallTrackingMetrics Account ID</label>
                                <input type="text" id="ctm_account_id" placeholder="e.g. 12345">
                            </div>
                            <div class="form-group">
                                <label for="ctm_profile_id">CallTrackingMetrics Client ID (Profile ID)</label>
                                <input type="text" id="ctm_profile_id" placeholder="e.g. 67890">
                            </div>
                        </div>

                        <div id="call_tracking_wc_box" class="form-row" style="display: none;">
                            <div class="form-group">
                                <label for="wc_account_id">WhatConverts Account ID</label>
                                <input type="text" id="wc_account_id" placeholder="e.g. 11111">
                            </div>
                            <div class="form-group">
                                <label for="wc_profile_id">WhatConverts Client ID (Profile ID)</label>
                                <input type="text" id="wc_profile_id" placeholder="e.g. 22222">
                            </div>
                        </div>
                        
                        <div class="form-row">
                            <div class="form-group">
                                <label for="google_ads_customer_id">Google Ads Customer ID</label>
                                <input type="text" id="google_ads_customer_id" placeholder="e.g. 123-456-7890">
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
                        
                        <div class="form-row">
                            <div class="form-group">
                                <label for="tiktok_ads_id">TikTok Ads Pixel/Account ID</label>
                                <input type="text" id="tiktok_ads_id" placeholder="e.g. tt_pixel_123">
                            </div>
                            <div class="form-group">
                                <label for="pinterest_ads_id">Pinterest Ads ID</label>
                                <input type="text" id="pinterest_ads_id" placeholder="e.g. pin_pixel_123">
                            </div>
                        </div>
                        <div class="form-row">
                            <div class="form-group">
                                <label for="chatgpt_ads_id">ChatGPT Ads ID</label>
                                <input type="text" id="chatgpt_ads_id" placeholder="e.g. gpt_pixel_123">
                            </div>
                            <div class="form-group">
                                <label for="reddit_ads_id">Reddit Ads Account / Pixel ID</label>
                                <input type="text" id="reddit_ads_id" placeholder="e.g. rdt_pixel_456">
                            </div>
                            <div class="form-group" style="visibility: hidden;">
                                <!-- Spacer -->
                            </div>
                        </div>
                        
                        <div class="form-row">
                            <div class="form-group">
                                <label for="snapchat_ads_id">Snapchat Ads Pixel ID</label>
                                <input type="text" id="snapchat_ads_id" placeholder="e.g. snap_pixel_123">
                            </div>
                            <div class="form-group" style="visibility: hidden;">
                                <!-- Spacer -->
                            </div>
                        </div>
                        <div class="form-row">
                            <div class="form-group">
                                <div style="display: flex; align-items: center; gap: 6px;">
                                    <label for="twitter_ads_id" style="margin-bottom: 0;">X (Twitter) Ads Pixel ID</label>
                                    <span class="tooltip-icon">
                                        
                                        <span class="tooltip-text" style="width: 250px;">
                                            ⚠️ <strong>Manual UTM required:</strong> To track X click IDs (twclid) via CallRail/form submissions, you must manually append <code>?twclid={{click_id}}</code> to your X Ad destination URLs.
                                        </span>
                                    </span>
                                </div>
                                <input type="text" id="twitter_ads_id" placeholder="e.g. tw_pixel_123" style="margin-top: 8px;">
                            </div>
                            <div class="form-group" style="visibility: hidden;">
                                <!-- Spacer -->
                            </div>
                        </div>
                    </div>
                    
                    <!-- STEP 2: Lead Gen & Qualification -->
                    <div class="wizard-step" id="step-panel-2">
                        <div class="instructions">
                             <strong>Step 2: Lead Generation & Qualification Preferences</strong><br>
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
                            <label for="qualification_criteria" style="display: inline-flex; align-items: center; gap: 5px;">
                                How do you qualify a lead?
                                <span class="tooltip">
                                    <span class="tooltiptext">Define a lead stage that is &quot;good enough&quot;, and would be happy with paying for all day long from your Ads. This is the minimum standard the system will go for when optimizing your Ads.</span>
                                </span>
                            </label>
                            <select id="qualification_criteria">
                                <option value="C"> Option C: Someone who books an appointment (Local Services Default)</option>
                                <option value="A"> Option A: Someone that I have a conversation with</option>
                                <option value="B"> Option B: Someone who shows strong buying interest</option>
                                <option value="D"> Option D: Someone who books a demo</option>
                                <option value="E"> Option E: Someone who requests a quote</option>
                                <option value="F"> Option F: Someone who we send a proposal</option>
                                <option value="H"> Option H: Someone who has qualified insurance</option>
                                <option value="I"> Option I: Someone who is credit pre-qualified</option>
                            </select>
                            <small style="color: #666; font-size: 11px; margin-top: 4px; display: block;">
                                 This choice dynamically feeds directly into <strong>Claude 4.5 Haiku's prompt</strong> to customize auditing.
                            </small>
                        </div>
                    </div>
                    
                    <!-- STEP 3: Source of Truth & CRM -->
                    <div class="wizard-step" id="step-panel-3">
                        <div class="instructions">
                             <strong>Step 3: Single Source of Truth & Integration Mapping</strong><br>
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
                                <option value="gohighlevel">GoHighLevel / GHL (CRM & Funnels)</option>
                                <option value="quickbooks">QuickBooks Accounting</option>
                                <option value="xero">Xero Accounting</option>
                                <option value="zoho_books">Zoho Books Accounting</option>
                                <option value="netsuite">NetSuite ERP/Accounting</option>
                                <option value="sage">Sage Accounting</option>
                                <option value="freshbooks">FreshBooks Billing</option>
                                <option value="google_sheets">Google Sheets (Live Sync)</option>
                                <option value="zapier">Zapier Custom Integration</option>
                                <option value="email">Monthly Sales Spreadsheet Ingestion via Email</option>
                                <option value="ai_rating">AI Rating (Direct Call Audits & Dynamic Form-Email Monitoring)</option>
                            </select>
                        </div>
                        
                        <!-- CONDITIONAL INPUT: CRM Deal status tags (HubSpot, Salesforce, Zoho) -->
                        <div id="sot-deal-tags-box" class="conditional-box" style="display: block;">
                            <div style="margin-bottom: 15px;">
                                <label for="crm_deal_tags">Which tags/statuses under <strong>Deals</strong> signify a qualified conversion?</label>
                                <input type="text" id="crm_deal_tags" placeholder="e.g. appointment-booked, estimate-given">
                                <small style="color: #666; font-size: 11px; margin-top: 4px; display: block;">
                                    List comma-separated tags that trigger a qualified lead conversion.
                                </small>
                            </div>
                            <div>
                                <label for="crm_won_deal_tags">Which tags/statuses under <strong>Deals</strong> signify a won deal conversion?</label>
                                <input type="text" id="crm_won_deal_tags" placeholder="e.g. closed-won, job-completed">
                                <small style="color: #666; font-size: 11px; margin-top: 4px; display: block;">
                                    List comma-separated tags that trigger a won deal conversion.
                                </small>
                            </div>
                        </div>
                        
                                                <div id="sot-hubspot-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                            <div style="background-color: #fff8e1; border-left: 4px solid #ffb300; padding: 15px; border-radius: 4px; color: #5d4037; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                 <strong>HubSpot Private App Quick Setup Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #4e342e;">
                                    <li>Log into HubSpot as a <strong>Super Admin</strong>.</li>
                                    <li>Go to <strong>Settings (Gear Icon) &gt; Integrations &gt; Private Apps</strong>.</li>
                                    <li>Click <strong>Create Private App</strong> and configure basic info.</li>
                                    <li>Under <strong>Scopes</strong>, search <code>CRM</code> and check <code>Read</code> permissions for:
                                        <ul style="list-style-type: disc; padding-left: 15px; margin: 4px 0;">
                                            <li><code>crm.objects.deals.read</code> (to track closed sales &amp; revenue)</li>
                                            <li><code>crm.objects.contacts.read</code> (to sync leads)</li>
                                        </ul>
                                    </li>
                                    <li>Click <strong>Create App</strong>. Click the <strong>Webhooks</strong> tab, click <strong>Edit Webhooks</strong>, paste your dynamic target URL below, and subscribe to <code>propertyChange</code> or <code>creation</code> for <strong>Deals</strong>!</li>
                                </ol>
                                <div style="margin-top: 12px; background: white; padding: 12px; border-radius: 6px; border: 1px solid #ffb300;">
                                    <label style="font-size: 11px; font-weight: bold; color: #5d4037; display: block; margin-bottom: 5px;">⚡ YOUR TARGET WEBHOOK ENDPOINT URL:</label>
                                    <div class="webhook-input-group">
                                        <input type="text" class="webhook-input" id="wiz-sot-hubspot-instructions-box-input" readonly value="" data-suffix="/webhooks/crm?client_id={next_id}">
                                        <button type="button" onclick="copyText('wiz-sot-hubspot-instructions-box-input', 'wiz-sot-hubspot-instructions-box-btn')" id="wiz-sot-hubspot-instructions-box-btn" class="btn-copy"> Copy Webhook URL</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div id="sot-salesforce-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                            <div style="background-color: #e3f2fd; border-left: 4px solid #1e88e5; padding: 15px; border-radius: 4px; color: #0d47a1; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                 <strong>Salesforce Outbound Flow Setup Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #1565c0;">
                                <li>In Salesforce Setup, go to <strong>Named Credentials &gt; External Credentials</strong> tab, click <strong>New</strong>. Label <code>LeadGroove External Credential</code>, Name <code>LeadGroove_External_Credential</code>, Protocol <strong>Custom</strong>. Save, scroll to <em>Principals</em>, click <strong>New</strong> and define a principal named <code>LeadGroove_Principal</code>.</li>
                                <li>Create a <strong>Permission Set</strong> in Setup named <code>LeadGroove Webhook Access</code>. In it, click <strong>External Credential Principal Access</strong>, enable your new credential and principal, and assign this permission set to any integrating users.</li>
                                <li>Back in <strong>Named Credentials</strong>, click <strong>New</strong> under the main tab. Label <code>LeadGroove API</code>, Name <code>LeadGroove_API</code>, URL set to your active origin. Under <em>External Credential</em>, select the credential you created in Step 1, and save.</li>
                                <li>Create a <strong>Record-Triggered Flow</strong> on the <strong>Opportunity</strong> object (when updated) with conditions <code>StageName Equals Closed Won</code> (Only when updated to meet conditions), optimized for <strong>Actions and Related Records</strong>.</li>
                                <li>On the flow canvas, click <strong>+ Add Action &gt; Create HTTP Callout</strong>, select your Named Credential, and define a <strong>POST</strong> method targeting your webhook URL below.</li>
                            </ol>
                                <div style="margin-top: 12px; background: white; padding: 12px; border-radius: 6px; border: 1px solid #1e88e5;">
                                    <label style="font-size: 11px; font-weight: bold; color: #0d47a1; display: block; margin-bottom: 5px;">⚡ YOUR TARGET WEBHOOK ENDPOINT URL:</label>
                                    <div class="webhook-input-group">
                                        <input type="text" class="webhook-input" id="wiz-sot-salesforce-instructions-box-input" readonly value="" data-suffix="/webhooks/crm?client_id={next_id}">
                                        <button type="button" onclick="copyText('wiz-sot-salesforce-instructions-box-input', 'wiz-sot-salesforce-instructions-box-btn')" id="wiz-sot-salesforce-instructions-box-btn" class="btn-copy"> Copy Webhook URL</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div id="sot-zoho-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                            <div style="background-color: #e8f5e9; border-left: 4px solid #2e7d32; padding: 15px; border-radius: 4px; color: #1b5e20; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                 <strong>Zoho CRM Outbound Webhook Setup Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #2e7d32;">
                                <li>Click the <strong>Setup (Gear Icon)</strong> in the top-right corner of your Zoho CRM dashboard.</li>
                                <li>Under <strong>Automation</strong>, click on <strong>Actions</strong>, then select the <strong>Webhooks</strong> tab at the top.</li>
                                <li>Click <strong>Configure Webhook</strong>, set Name to <code>LeadGroove Conversion Sync</code>, Method to <strong>POST</strong>, Module to <strong>Deals</strong>, and paste your target URL below into <strong>URL to notify</strong>.</li>
                                <li>In the <strong>Body</strong> section, select <strong>Raw (JSON)</strong> format, and type <code>#</code> to insert CRM fields into your payload structure. Click <strong>Save</strong>!</li>
                            </ol>
                                <div style="margin-top: 12px; background: white; padding: 12px; border-radius: 6px; border: 1px solid #2e7d32;">
                                    <label style="font-size: 11px; font-weight: bold; color: #1b5e20; display: block; margin-bottom: 5px;">⚡ YOUR TARGET WEBHOOK ENDPOINT URL:</label>
                                    <div class="webhook-input-group">
                                        <input type="text" class="webhook-input" id="wiz-sot-zoho-instructions-box-input" readonly value="" data-suffix="/webhooks/crm?client_id={next_id}">
                                        <button type="button" onclick="copyText('wiz-sot-zoho-instructions-box-input', 'wiz-sot-zoho-instructions-box-btn')" id="wiz-sot-zoho-instructions-box-btn" class="btn-copy"> Copy Webhook URL</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div id="sot-servicetitan-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                            <div style="background-color: #f3f4f6; border-left: 4px solid #4b5563; padding: 15px; border-radius: 4px; color: #1f2937; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                 <strong>ServiceTitan Webhooks V2 Quick Setup Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #374151;">
                                    <li>Navigate to the <strong>ServiceTitan Developer Portal</strong> at <a href="https://developer.servicetitan.io" target="_blank" style="color: #1a237e; font-weight: bold; text-decoration: none;">developer.servicetitan.io</a>.</li>
                                    <li>Click <strong>Create and Manage Applications ➡️ Create New App</strong>. Name it <code>LeadGroove Webhook Sync</code> and set scopes <code>crm.objects.leads.read</code> / <code>jpm.objects.jobs.read</code>.</li>
                                    <li>Log into your portal at <a href="https://go.servicetitan.com" target="_blank" style="color: #1a237e; font-weight: bold; text-decoration: none;">go.servicetitan.com</a>, go to <strong>Settings ➡️ Integrations ➡️ API Application Access</strong>, edit your app, and set your target Webhook URL below!</li>
                                </ol>
                                <div style="margin-top: 12px; background: white; padding: 12px; border-radius: 6px; border: 1px solid #4b5563;">
                                    <label style="font-size: 11px; font-weight: bold; color: #1f2937; display: block; margin-bottom: 5px;">⚡ YOUR TARGET WEBHOOK ENDPOINT URL:</label>
                                    <div class="webhook-input-group">
                                        <input type="text" class="webhook-input" id="wiz-sot-servicetitan-instructions-box-input" readonly value="" data-suffix="/webhooks/crm?client_id={next_id}">
                                        <button type="button" onclick="copyText('wiz-sot-servicetitan-instructions-box-input', 'wiz-sot-servicetitan-instructions-box-btn')" id="wiz-sot-servicetitan-instructions-box-btn" class="btn-copy"> Copy Webhook URL</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div id="sot-gohighlevel-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                            <div style="background-color: #e8eaf6; border-left: 4px solid #3f51b5; padding: 15px; border-radius: 4px; color: #1a237e; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                 <strong>GoHighLevel (GHL) Workflow Webhook Setup Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #1a237e;">
                                <li>Log into your <strong>GoHighLevel Sub-Account / Agency Portal</strong>.</li>
                                <li>Go to <strong>Automation ➡️ Workflows</strong> and click <strong>+ Create Workflow</strong>.</li>
                                <li>Set Trigger to <strong>Opportunity Status Changed</strong> (e.g. Stage updated to <em>Won</em> or <em>Qualified</em>), <strong>Contact Tag Added</strong>, or <strong>Form Submitted</strong>.</li>
                                <li>Add Action ➡️ Select <strong>Webhook</strong>, set Method to <strong>POST</strong>, and paste your target URL below.</li>
                                <li>Toggle Workflow to <strong>Publish</strong> and save! Whenever an opportunity updates or form submits, data pushes to LeadGrove in real time.</li>
                            </ol>
                                <div style="margin-top: 12px; background: white; padding: 12px; border-radius: 6px; border: 1px solid #3f51b5;">
                                    <label style="font-size: 11px; font-weight: bold; color: #1a237e; display: block; margin-bottom: 5px;">⚡ YOUR TARGET WEBHOOK ENDPOINT URL:</label>
                                    <div class="webhook-input-group">
                                        <input type="text" class="webhook-input" id="wiz-sot-gohighlevel-instructions-box-input" readonly value="" data-suffix="/webhooks/crm?client_id={next_id}">
                                        <button type="button" onclick="copyText('wiz-sot-gohighlevel-instructions-box-input', 'wiz-sot-gohighlevel-instructions-box-btn')" id="wiz-sot-gohighlevel-instructions-box-btn" class="btn-copy"> Copy Webhook URL</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div id="sot-housecallpro-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                            <div style="background-color: #fff3e0; border-left: 4px solid #e65100; padding: 15px; border-radius: 4px; color: #e65100; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                 <strong>Housecall Pro Webhooks Quick Setup Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #e65100;">
                                    <li>Sign in as an <strong>Admin</strong> user in Housecall Pro.</li>
                                    <li>Go to <strong>My Apps ➡️ All Apps</strong>, search for <strong>Webhooks</strong>, and click to open.</li>
                                    <li>Toggle <strong>Enable Webhooks</strong> on, and paste your target URL below into <strong>Target URL</strong>.</li>
                                    <li>Subscribe to <code>job.completed</code>, <code>job.created</code>, and <code>job.paid</code>. Save to activate!</li>
                                </ol>
                                <div style="margin-top: 12px; background: white; padding: 12px; border-radius: 6px; border: 1px solid #e65100;">
                                    <label style="font-size: 11px; font-weight: bold; color: #e65100; display: block; margin-bottom: 5px;">⚡ YOUR TARGET WEBHOOK ENDPOINT URL:</label>
                                    <div class="webhook-input-group">
                                        <input type="text" class="webhook-input" id="wiz-sot-housecallpro-instructions-box-input" readonly value="" data-suffix="/webhooks/crm?client_id={next_id}">
                                        <button type="button" onclick="copyText('wiz-sot-housecallpro-instructions-box-input', 'wiz-sot-housecallpro-instructions-box-btn')" id="wiz-sot-housecallpro-instructions-box-btn" class="btn-copy"> Copy Webhook URL</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div id="sot-quickbooks-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                            <div style="background-color: #e3f2fd; border-left: 4px solid #0288d1; padding: 15px; border-radius: 4px; color: #01579b; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                 <strong>QuickBooks Online Webhooks Setup Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #0277bd;">
                                    <li>Log into the <strong>Intuit Developer Portal</strong> at <a href="https://developer.intuit.com" target="_blank" style="color: #1a237e; font-weight: bold; text-decoration: none;">developer.intuit.com</a>.</li>
                                    <li>Go to <strong>Production Settings ➡️ Webhooks</strong> in your App sidebar.</li>
                                    <li>In the <strong>Endpoint URL</strong> field, paste your dynamic target URL below.</li>
                                    <li>Check event boxes under <strong>Invoices</strong> or <strong>Payments</strong> and click Save!</li>
                                </ol>
                                <div style="margin-top: 12px; background: white; padding: 12px; border-radius: 6px; border: 1px solid #0288d1;">
                                    <label style="font-size: 11px; font-weight: bold; color: #01579b; display: block; margin-bottom: 5px;">⚡ YOUR TARGET WEBHOOK ENDPOINT URL:</label>
                                    <div class="webhook-input-group">
                                        <input type="text" class="webhook-input" id="wiz-sot-quickbooks-instructions-box-input" readonly value="" data-suffix="/webhooks/billing?client_id={next_id}">
                                        <button type="button" onclick="copyText('wiz-sot-quickbooks-instructions-box-input', 'wiz-sot-quickbooks-instructions-box-btn')" id="wiz-sot-quickbooks-instructions-box-btn" class="btn-copy"> Copy Webhook URL</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div id="sot-xero-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                            <div style="background-color: #e0f7fa; border-left: 4px solid #00b0ff; padding: 15px; border-radius: 4px; color: #006064; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                 <strong>Xero Webhooks Setup Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #00838f;">
                                    <li>Log into <strong>Xero Developer Portal</strong> under My Apps, select your App, and open the <strong>Webhooks</strong> tab.</li>
                                    <li>Paste your live endpoint below into the <strong>Send notifications to</strong> field.</li>
                                    <li>Subscribe to <strong>Invoices</strong> (CREATE, UPDATE) and click Save!</li>
                                </ol>
                                <div style="margin-top: 12px; background: white; padding: 12px; border-radius: 6px; border: 1px solid #00b0ff;">
                                    <label style="font-size: 11px; font-weight: bold; color: #006064; display: block; margin-bottom: 5px;">⚡ YOUR TARGET WEBHOOK ENDPOINT URL:</label>
                                    <div class="webhook-input-group">
                                        <input type="text" class="webhook-input" id="wiz-sot-xero-instructions-box-input" readonly value="" data-suffix="/webhooks/billing?client_id={next_id}">
                                        <button type="button" onclick="copyText('wiz-sot-xero-instructions-box-input', 'wiz-sot-xero-instructions-box-btn')" id="wiz-sot-xero-instructions-box-btn" class="btn-copy"> Copy Webhook URL</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div id="sot-zoho_books-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                            <div style="background-color: #e8f5e9; border-left: 4px solid #2e7d32; padding: 15px; border-radius: 4px; color: #1b5e20; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                 <strong>Zoho Books Webhooks Setup Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #1b5e20;">
                                    <li>Go to <strong>Settings ➡️ Developer Space ➡️ Webhooks</strong> in Zoho Books and click <strong>+ New Webhook</strong>.</li>
                                    <li>Paste your custom endpoint below into <strong>URL to Notify</strong>, set Module to <strong>Invoices</strong>, and select event <strong>Invoice Paid</strong>!</li>
                                </ol>
                                <div style="margin-top: 12px; background: white; padding: 12px; border-radius: 6px; border: 1px solid #2e7d32;">
                                    <label style="font-size: 11px; font-weight: bold; color: #1b5e20; display: block; margin-bottom: 5px;">⚡ YOUR TARGET WEBHOOK ENDPOINT URL:</label>
                                    <div class="webhook-input-group">
                                        <input type="text" class="webhook-input" id="wiz-sot-zoho_books-instructions-box-input" readonly value="" data-suffix="/webhooks/billing?client_id={next_id}">
                                        <button type="button" onclick="copyText('wiz-sot-zoho_books-instructions-box-input', 'wiz-sot-zoho_books-instructions-box-btn')" id="wiz-sot-zoho_books-instructions-box-btn" class="btn-copy"> Copy Webhook URL</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div id="sot-netsuite-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                            <div style="background-color: #eceff1; border-left: 4px solid #455a64; padding: 15px; border-radius: 4px; color: #263238; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                 <strong>NetSuite SuiteScript Integration Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #37474f;">
                                    <li>Deploy a <strong>SuiteScript 2.x User Event Script</strong> on `Invoice` or `CustomerPayment` records.</li>
                                    <li>On `afterSubmit`, trigger an outbound HTTP POST to your web receiver endpoint below.</li>
                                </ol>
                                <div style="margin-top: 12px; background: white; padding: 12px; border-radius: 6px; border: 1px solid #455a64;">
                                    <label style="font-size: 11px; font-weight: bold; color: #263238; display: block; margin-bottom: 5px;">⚡ YOUR TARGET WEBHOOK ENDPOINT URL:</label>
                                    <div class="webhook-input-group">
                                        <input type="text" class="webhook-input" id="wiz-sot-netsuite-instructions-box-input" readonly value="" data-suffix="/webhooks/billing?client_id={next_id}">
                                        <button type="button" onclick="copyText('wiz-sot-netsuite-instructions-box-input', 'wiz-sot-netsuite-instructions-box-btn')" id="wiz-sot-netsuite-instructions-box-btn" class="btn-copy"> Copy Webhook URL</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div id="sot-sage-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                            <div style="background-color: #fff3e0; border-left: 4px solid #e65100; padding: 15px; border-radius: 4px; color: #e65100; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                 <strong>Sage Accounting Webhooks Setup Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #e65100;">
                                    <li>In Sage Developer Portal, configure webhooks and paste your dynamic endpoint URL below.</li>
                                    <li>Subscribe to <code>sales_invoice.paid</code> and <code>payment_received</code>!</li>
                                </ol>
                                <div style="margin-top: 12px; background: white; padding: 12px; border-radius: 6px; border: 1px solid #e65100;">
                                    <label style="font-size: 11px; font-weight: bold; color: #e65100; display: block; margin-bottom: 5px;">⚡ YOUR TARGET WEBHOOK ENDPOINT URL:</label>
                                    <div class="webhook-input-group">
                                        <input type="text" class="webhook-input" id="wiz-sot-sage-instructions-box-input" readonly value="" data-suffix="/webhooks/billing?client_id={next_id}">
                                        <button type="button" onclick="copyText('wiz-sot-sage-instructions-box-input', 'wiz-sot-sage-instructions-box-btn')" id="wiz-sot-sage-instructions-box-btn" class="btn-copy"> Copy Webhook URL</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div id="sot-freshbooks-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                            <div style="background-color: #f3e5f5; border-left: 4px solid #4a148c; padding: 15px; border-radius: 4px; color: #4a148c; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                 <strong>FreshBooks Billing Webhooks Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #4a148c;">
                                    <li>Subscribe to webhook notifications in FreshBooks Developer Center and paste your endpoint below.</li>
                                    <li>Set trigger event to <code>invoice.payment.create</code>.</li>
                                </ol>
                                <div style="margin-top: 12px; background: white; padding: 12px; border-radius: 6px; border: 1px solid #4a148c;">
                                    <label style="font-size: 11px; font-weight: bold; color: #4a148c; display: block; margin-bottom: 5px;">⚡ YOUR TARGET WEBHOOK ENDPOINT URL:</label>
                                    <div class="webhook-input-group">
                                        <input type="text" class="webhook-input" id="wiz-sot-freshbooks-instructions-box-input" readonly value="" data-suffix="/webhooks/billing?client_id={next_id}">
                                        <button type="button" onclick="copyText('wiz-sot-freshbooks-instructions-box-input', 'wiz-sot-freshbooks-instructions-box-btn')" id="wiz-sot-freshbooks-instructions-box-btn" class="btn-copy"> Copy Webhook URL</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div id="sot-google_sheets-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                            <div style="background-color: #efebe9; border-left: 4px solid #4e342e; padding: 15px; border-radius: 4px; color: #4e342e; font-size: 13px; line-height: 1.5; margin-bottom: 0; text-align: left;">
                                 <strong>Google Sheets (Live Sync via Apps Script) Setup Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 8px; margin-bottom: 8px; line-height: 1.6; font-size: 12px; color: #4e342e;">
                                    <li><strong>Define Lead & Won Deal Tags Above:</strong> Enter status tags in the <em>Qualified Lead Tags</em> and <em>Won Deal Tags</em> boxes above (e.g. <code>appointment-booked</code> or <code>closed-won</code>).</li>
                                    <li><strong>Format Row 1 Column Headings:</strong> Ensure your Google Sheet contains these standard Row 1 headers:
                                        <ul style="list-style-type: disc; padding-left: 15px; margin: 4px 0;">
                                            <li><code>Phone</code> (or <code>Phone Number</code>): Required to match lead phone numbers with CallRail/form sessions.</li>
                                            <li><code>Email</code> (or <code>Email Address</code>): Optional secondary customer identifier.</li>
                                            <li><code>Status</code> (or <code>Lead Status</code> / <code>Stage</code>): Cell value indicating lead stage (must match tags defined above).</li>
                                            <li><code>Amount</code> (or <code>Revenue</code> / <code>Value</code> / <code>Total</code>): Dollar amount of closed sale (e.g. <code>450.00</code>).</li>
                                            <li><code>Name</code> (or <code>Customer Name</code>): Optional customer name for audit logs.</li>
                                        </ul>
                                    </li>
                                    <li><strong>Connect Apps Script Sync:</strong> Open your Google Sheet, click <strong>Extensions ➡️ Apps Script</strong>, paste the Apps Script webhook trigger, and set your target endpoint URL below!</li>
                                </ol>
                                <div style="margin-top: 12px; background: white; padding: 12px; border-radius: 6px; border: 1px solid #4e342e;">
                                    <label style="font-size: 11px; font-weight: bold; color: #4e342e; display: block; margin-bottom: 5px;">⚡ YOUR TARGET WEBHOOK ENDPOINT URL:</label>
                                    <div class="webhook-input-group">
                                        <input type="text" class="webhook-input" id="wiz-sot-google_sheets-instructions-box-input" readonly value="" data-suffix="/webhooks/crm?client_id={next_id}">
                                        <button type="button" onclick="copyText('wiz-sot-google_sheets-instructions-box-input', 'wiz-sot-google_sheets-instructions-box-btn')" id="wiz-sot-google_sheets-instructions-box-btn" class="btn-copy"> Copy Webhook URL</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                                                <div id="sot-zapier-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #f57c00; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                            <div style="background-color: #fff3e0; border-left: 4px solid #ff9800; padding: 15px; border-radius: 4px; color: #e65100; font-size: 13px; line-height: 1.6; margin-bottom: 0; text-align: left;">
                                 <strong>Webhooks by Zapier Custom Setup Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 10px; margin-bottom: 10px; line-height: 1.8; font-size: 12px; color: #e65100;">
                                    <li><strong>Indicate Conversion Tags Above:</strong> In the fields above, enter the exact tags or statuses (e.g. <code>appointment-booked</code>, <code>closed-won</code>, <code>paid</code>) that signify a <strong>Qualified Lead</strong> and a <strong>Won Deal</strong> in your pipeline.</li>
                                    <li><strong>Create a Zap in Zapier:</strong> Set your <strong>Trigger</strong> app (e.g. Stripe, PayPal, Typeform, Calendly, or custom CRM).</li>
                                    <li><strong>Add Webhook Action:</strong> Add an Action step, select <strong>Webhooks by Zapier ➡️ Custom Request (POST) or POST</strong>, and paste your endpoint URL below into the <strong>URL</strong> field.</li>
                                    <li><strong>Map Payload Data Fields:</strong> Map your trigger data to these key parameters so LeadGrove accurately parses your conversions:
                                        <ul style="list-style-type: disc; padding-left: 20px; margin-top: 5px; margin-bottom: 5px;">
                                            <li><code>phone</code> (or <code>phone_number</code>) — Customer phone number (used to match original ad click)</li>
                                            <li><code>status</code> (or <code>stage</code> / <code>tag</code>) — Matching the tags specified in Step 1</li>
                                            <li><code>amount</code> (or <code>value</code>) — Transaction dollar revenue for won deals (e.g. <code>450.00</code>)</li>
                                            <li><code>email</code> / <code>name</code> — Customer email and name</li>
                                        </ul>
                                    </li>
                                </ol>

                                <!-- Visual Diagram: Zapier Workflow Mapping -->
                                <div style="margin-top: 15px; margin-bottom: 15px; background: white; border: 1px solid #ffe0b2; border-radius: 8px; padding: 15px;">
                                    <div style="font-size: 11px; font-weight: bold; color: #e65100; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 10px; text-align: center;">
                                        ⚡ ZAPIER WEBHOOK CONFIGURATION ARCHITECTURE
                                    </div>
                                    <div style="display: flex; align-items: center; justify-content: space-around; gap: 8px; flex-wrap: wrap; text-align: center; margin-bottom: 12px;">
                                        <div style="background: #fff8e1; border: 1px solid #ffe0b2; border-radius: 6px; padding: 10px; min-width: 120px; flex: 1;">
                                            <div style="font-size: 18px;"></div>
                                            <div style="font-weight: bold; font-size: 11px; color: #e65100;">1. Trigger App</div>
                                            <div style="font-size: 10px; color: #795548;">CRM, Form, Stripe</div>
                                        </div>
                                        <div style="font-size: 16px; color: #ff9800; font-weight: bold;">➔</div>
                                        <div style="background: #fff3e0; border: 1px solid #ffcc80; border-radius: 6px; padding: 10px; min-width: 130px; flex: 1;">
                                            <div style="font-size: 18px;">⚡</div>
                                            <div style="font-weight: bold; font-size: 11px; color: #e65100;">2. Webhooks by Zapier</div>
                                            <div style="font-size: 10px; color: #795548;">Action: POST Method</div>
                                        </div>
                                        <div style="font-size: 16px; color: #ff9800; font-weight: bold;">➔</div>
                                        <div style="background: #e8f5e9; border: 1px solid #a5d6a7; border-radius: 6px; padding: 10px; min-width: 120px; flex: 1;">
                                            <div style="font-size: 18px;"></div>
                                            <div style="font-weight: bold; font-size: 11px; color: #2e7d32;">3. LeadGrove Engine</div>
                                            <div style="font-size: 10px; color: #388e3c;">Ad Network Uploads</div>
                                        </div>
                                    </div>

                                    <div style="background: #263238; color: #eceff1; border-radius: 6px; padding: 12px; font-family: monospace; font-size: 11px; line-height: 1.6; text-align: left;">
                                        <div style="color: #ffb74d; font-weight: bold; margin-bottom: 4px;">// Zapier Action Setup Mockup</div>
                                        <div><span style="color: #80cbc4;">Action Event :</span> <span style="color: #fff;">POST</span></div>
                                        <div><span style="color: #80cbc4;">URL          :</span> <span style="color: #fff;">[YOUR TARGET WEBHOOK URL BELOW]</span></div>
                                        <div><span style="color: #80cbc4;">Payload Type :</span> <span style="color: #fff;">json</span></div>
                                        <div><span style="color: #80cbc4;">Data Fields  :</span></div>
                                        <div style="padding-left: 15px;"><span style="color: #81c784;">phone</span>  ➡️  <span style="color: #b0bec5;">1. Customer Phone Number</span></div>
                                        <div style="padding-left: 15px;"><span style="color: #81c784;">status</span> ➡️  <span style="color: #b0bec5;">1. Deal Stage / Status Tag</span></div>
                                        <div style="padding-left: 15px;"><span style="color: #81c784;">amount</span> ➡️  <span style="color: #b0bec5;">1. Purchase Revenue ($)</span></div>
                                    </div>
                                </div>

                                <div style="margin-top: 12px; background: white; padding: 12px; border-radius: 6px; border: 1px solid #fbc02d;">
                                    <label style="font-size: 11px; font-weight: bold; color: #f57f17; display: block; margin-bottom: 5px;">⚡ YOUR TARGET WEBHOOK ENDPOINT URL:</label>
                                    <div class="webhook-input-group">
                                        <input type="text" class="webhook-input" id="wiz-sot-zapier-instructions-box-input" readonly value="" data-suffix="/webhooks/crm?client_id={next_id}">
                                        <button type="button" onclick="copyText('wiz-sot-zapier-instructions-box-input', 'wiz-sot-zapier-instructions-box-btn')" id="wiz-sot-zapier-instructions-box-btn" class="btn-copy"> Copy Webhook URL</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div id="sot-monthly-email-instructions-box" class="conditional-box" style="background-color: #fafafa; border: 1px dashed #ccc; border-radius: 8px; padding: 20px; margin-top: 15px; display: none;">
                            <div style="background-color: #e8eaf6; border-left: 4px solid #1a237e; padding: 15px; border-radius: 4px; color: #1a237e; font-size: 13px; line-height: 1.6; margin-bottom: 0; text-align: left;">
                                 <strong>Monthly Sales Spreadsheet Email Ingestion Setup Guide:</strong><br>
                                <ol style="padding-left: 20px; margin-top: 10px; margin-bottom: 10px; line-height: 1.8; font-size: 12px; color: #1a237e;">
                                    <li><strong>Setup Email Forwarding:</strong> Set up automated forwarding or email your monthly sales spreadsheet directly to:<br>
                                        <div style="margin-top: 6px; margin-bottom: 6px; background: white; padding: 10px; border-radius: 6px; border: 1px solid #c5cae9; display: inline-block;">
                                            <code class="monthly-forwarding-email" style="font-size: 13px; font-weight: bold; color: #2e7d32; font-family: monospace;">conversions-[id]@your-agency.com</code>
                                        </div>
                                    </li>
                                    <li><strong>Specify Qualification & Revenue Tags:</strong> In the fields above, specify which tags or cell statuses (e.g. <code>appointment-booked</code>, <code>closed-won</code>, <code>paid</code>) indicate qualified leads and purchase revenue.</li>
                                    <li><strong>Format Spreadsheet Column Headings:</strong> Ensure Row 1 of your spreadsheet includes matching column headers:
                                        <ul style="list-style-type: disc; padding-left: 20px; margin-top: 5px; margin-bottom: 5px;">
                                            <li><code>Phone</code> (or <code>Phone Number</code>) — Required to match original callers / web leads</li>
                                            <li><code>Status</code> (or <code>Stage</code> / <code>Tag</code>) — Matching the tags specified in Step #2 above</li>
                                            <li><code>Amount</code> (or <code>Revenue</code> / <code>Total</code>) — The purchase dollar value</li>
                                            <li><code>Email</code> / <code>Name</code> — Optional customer details</li>
                                        </ul>
                                    </li>
                                </ol>
                            </div>
                        </div>


<!-- CONDITIONAL INPUT: CRM Lead status tags (ServiceTitan, Housecall Pro) -->
                        <div id="sot-lead-tags-box" class="conditional-box">
                            <label for="crm_lead_tags">Which tags/statuses under <strong>Leads</strong> signify qualification?</label>
                            <input type="text" id="crm_lead_tags" placeholder="e.g. job-booked, estimate-given, dispatched">
                        </div>
                        
                                                <!-- CONDITIONAL INPUT: Email account fallback settings -->
                        
                            <!-- CONDITIONAL: AI Rating VoIP Provider Box -->
                                                        <div id="sot-voip-box" class="conditional-box" style="display: none; background-color: #f8f9fa; border: 1px dashed #1a237e; border-radius: 8px; padding: 20px; margin-top: 15px;">
                                <div class="form-group" style="margin-bottom: 15px;">
                                    <label for="voip_provider" style="font-weight: bold; color: #1a237e; font-size: 13px;">Select Your Current VOIP Provider (Optional)</label>
                                    <select id="voip_provider" onchange="toggleVoipInstructions()" style="width: 100%; padding: 10px; border-radius: 6px; border: 1px solid #ced4da; font-size: 13px; font-weight: 600; cursor: pointer;">
                                        <option value="dialpad" selected>Dialpad (Ai Recap)</option>
                                        <option value="ringcentral">RingCentral</option>
                                        <option value="zoom_phone">Zoom Phone</option>
                                        <option value="openphone">OpenPhone</option>
                                        <option value="nextiva">Nextiva</option>
                                        <option value="vonage">Vonage Business</option>
                                        <option value="ooma">Ooma Office</option>
                                        <option value="grasshopper">Grasshopper</option>
                                        <option value="custom_voip">Custom VoIP / Other</option>
                                    </select>
                                    <small style="color: #666; font-size: 11px; margin-top: 4px; display: block;">
                                        LeadGrove will receive call recordings & transcripts from this provider to run automated AI audits.
                                    </small>
                                </div>

                                <!-- Dynamic Instructions per VoIP Provider -->

                                <div id="voip-inst-dialpad" class="voip-inst-card" style="display: none; background-color: #f3e5f5; border-left: 4px solid #7b1fa2; padding: 14px; border-radius: 4px; color: #4a148c; font-size: 12px; line-height: 1.5;">
                                     <strong>Dialpad (Ai Recap) Setup Instructions:</strong><br>
                                    1. In Dialpad Admin Settings, go to <strong>Integrations ➡️ Webhooks ➡️ Add Webhook</strong>.<br>
                                    2. Set Target URL to your endpoint below, and check events <strong>"call_completed"</strong> and <strong>"transcript_ready"</strong>.<br>
                                    3. Dialpad's native AI transcripts will automatically stream to LeadGrove for Claude auditing!
                                    <div style="margin-top: 10px; background: white; padding: 10px; border-radius: 6px; border: 1px solid #e1bee7;">
                                        <label style="font-size: 11px; font-weight: bold; color: #4a148c; display: block; margin-bottom: 4px;">⚡ DIALPAD WEBHOOK ENDPOINT URL:</label>
                                        <div class="webhook-input-group">
                                            <input type="text" class="webhook-input" id="voip-endpoint-dialpad" readonly value="" data-suffix="/webhooks/voip?client_id={next_id}">
                                            <button type="button" onclick="copyText('voip-endpoint-dialpad', 'voip-btn-dialpad')" id="voip-btn-dialpad" class="btn-copy"> Copy Webhook URL</button>
                                        </div>
                                    </div>
                                </div>

                                <div id="voip-inst-ringcentral" class="voip-inst-card" style="display: none; background-color: #e0f7fa; border-left: 4px solid #0097a7; padding: 14px; border-radius: 4px; color: #006064; font-size: 12px; line-height: 1.5;">
                                     <strong>RingCentral Setup Instructions:</strong><br>
                                    1. In RingCentral Admin Console, go to <strong>Integrations / Webhooks ➡️ Create Subscription</strong>.<br>
                                    2. Set Notification Event to <strong>"Telephony Session / Call Log"</strong> and paste target URL below.<br>
                                    3. RingCentral inbound & outbound calls will sync automatically with LeadGrove lead timelines!
                                    <div style="margin-top: 10px; background: white; padding: 10px; border-radius: 6px; border: 1px solid #b2ebf2;">
                                        <label style="font-size: 11px; font-weight: bold; color: #006064; display: block; margin-bottom: 4px;">⚡ RINGCENTRAL WEBHOOK ENDPOINT URL:</label>
                                        <div class="webhook-input-group">
                                            <input type="text" class="webhook-input" id="voip-endpoint-rc" readonly value="" data-suffix="/webhooks/voip?client_id={next_id}">
                                            <button type="button" onclick="copyText('voip-endpoint-rc', 'voip-btn-rc')" id="voip-btn-rc" class="btn-copy"> Copy Webhook URL</button>
                                        </div>
                                    </div>
                                </div>

                                <div id="voip-inst-zoom_phone" class="voip-inst-card" style="display: none; background-color: #e3f2fd; border-left: 4px solid #1e88e5; padding: 14px; border-radius: 4px; color: #0d47a1; font-size: 12px; line-height: 1.5;">
                                     <strong>Zoom Phone Setup Instructions:</strong><br>
                                    1. In Zoom Marketplace, go to <strong>Develop ➡️ Build App ➡️ Webhook Only</strong>.<br>
                                    2. Subscribe to Event Notifications: <strong>"phone.callee_ended"</strong> & <strong>"phone.recording_completed"</strong>.<br>
                                    3. Set Webhook Endpoint URL to the link below to stream Zoom call logs directly to LeadGrove!
                                    <div style="margin-top: 10px; background: white; padding: 10px; border-radius: 6px; border: 1px solid #bbdefb;">
                                        <label style="font-size: 11px; font-weight: bold; color: #0d47a1; display: block; margin-bottom: 4px;">⚡ ZOOM PHONE WEBHOOK ENDPOINT URL:</label>
                                        <div class="webhook-input-group">
                                            <input type="text" class="webhook-input" id="voip-endpoint-zoom" readonly value="" data-suffix="/webhooks/voip?client_id={next_id}">
                                            <button type="button" onclick="copyText('voip-endpoint-zoom', 'voip-btn-zoom')" id="voip-btn-zoom" class="btn-copy"> Copy Webhook URL</button>
                                        </div>
                                    </div>
                                </div>

                                <div id="voip-inst-openphone" class="voip-inst-card" style="display: none; background-color: #f1f8e9; border-left: 4px solid #33691e; padding: 14px; border-radius: 4px; color: #1b5e20; font-size: 12px; line-height: 1.5;">
                                     <strong>OpenPhone Setup Instructions:</strong><br>
                                    1. In OpenPhone Settings, go to <strong>Integrations ➡️ Webhooks ➡️ Add Webhook</strong>.<br>
                                    2. Paste target URL below and check events: <strong>"call.completed"</strong> and <strong>"call.transcript.completed"</strong>.<br>
                                    3. Sales rep follow-up calls will instantly pair with original lead click IDs!
                                    <div style="margin-top: 10px; background: white; padding: 10px; border-radius: 6px; border: 1px solid #c8e6c9;">
                                        <label style="font-size: 11px; font-weight: bold; color: #1b5e20; display: block; margin-bottom: 4px;">⚡ OPENPHONE WEBHOOK ENDPOINT URL:</label>
                                        <div class="webhook-input-group">
                                            <input type="text" class="webhook-input" id="voip-endpoint-openphone" readonly value="" data-suffix="/webhooks/voip?client_id={next_id}">
                                            <button type="button" onclick="copyText('voip-endpoint-openphone', 'voip-btn-openphone')" id="voip-btn-openphone" class="btn-copy"> Copy Webhook URL</button>
                                        </div>
                                    </div>
                                </div>

                                <div id="voip-inst-nextiva" class="voip-inst-card" style="display: none; background-color: #e8eaf6; border-left: 4px solid #283593; padding: 14px; border-radius: 4px; color: #1a237e; font-size: 12px; line-height: 1.5;">
                                     <strong>Nextiva Setup Instructions:</strong><br>
                                    1. Log into Nextiva Voice Admin Portal ➡️ <strong>Integrations / Analytics ➡️ Webhooks</strong>.<br>
                                    2. Click <strong>Add Webhook</strong>, set Target URL to your endpoint below, and subscribe to <strong>"Call Completed"</strong>.<br>
                                    3. Ensure Call Recording & Speech-to-Text Transcriptions are enabled for your team extensions!
                                    <div style="margin-top: 10px; background: white; padding: 10px; border-radius: 6px; border: 1px solid #c5cae9;">
                                        <label style="font-size: 11px; font-weight: bold; color: #1a237e; display: block; margin-bottom: 4px;">⚡ NEXTIVA WEBHOOK ENDPOINT URL:</label>
                                        <div class="webhook-input-group">
                                            <input type="text" class="webhook-input" id="voip-endpoint-nextiva" readonly value="" data-suffix="/webhooks/voip?client_id={next_id}">
                                            <button type="button" onclick="copyText('voip-endpoint-nextiva', 'voip-btn-nextiva')" id="voip-btn-nextiva" class="btn-copy"> Copy Webhook URL</button>
                                        </div>
                                    </div>
                                </div>

                                <div id="voip-inst-vonage" class="voip-inst-card" style="display: none; background-color: #fff3e0; border-left: 4px solid #e65100; padding: 14px; border-radius: 4px; color: #e65100; font-size: 12px; line-height: 1.5;">
                                     <strong>Vonage Business Setup Instructions:</strong><br>
                                    1. Log into Vonage Business Communications (VBC) Admin Portal ➡️ <strong>Integration Suite ➡️ Webhooks</strong>.<br>
                                    2. Create a new webhook subscription, paste your Target URL below, and select event <strong>"call.completed"</strong>.<br>
                                    3. Ensure Automatic Call Recording is enabled so call logs and audio stream directly to LeadGrove!
                                    <div style="margin-top: 10px; background: white; padding: 10px; border-radius: 6px; border: 1px solid #ffe0b2;">
                                        <label style="font-size: 11px; font-weight: bold; color: #e65100; display: block; margin-bottom: 4px;">⚡ VONAGE WEBHOOK ENDPOINT URL:</label>
                                        <div class="webhook-input-group">
                                            <input type="text" class="webhook-input" id="voip-endpoint-vonage" readonly value="" data-suffix="/webhooks/voip?client_id={next_id}">
                                            <button type="button" onclick="copyText('voip-endpoint-vonage', 'voip-btn-vonage')" id="voip-btn-vonage" class="btn-copy"> Copy Webhook URL</button>
                                        </div>
                                    </div>
                                </div>

                                <div id="voip-inst-ooma" class="voip-inst-card" style="display: none; background-color: #e0f2f1; border-left: 4px solid #00695c; padding: 14px; border-radius: 4px; color: #004d40; font-size: 12px; line-height: 1.5;">
                                     <strong>Ooma Office Setup Instructions:</strong><br>
                                    1. Log into Ooma Office Manager (office.ooma.com) ➡️ <strong>System ➡️ Integrations & API Webhooks</strong>.<br>
                                    2. Click <strong>Add Webhook</strong>, paste your target endpoint below, and set trigger to <strong>"Call Ended"</strong>.<br>
                                    3. Confirm Call Recording is activated for your extension group so recordings & transcripts are captured!
                                    <div style="margin-top: 10px; background: white; padding: 10px; border-radius: 6px; border: 1px solid #b2dfdb;">
                                        <label style="font-size: 11px; font-weight: bold; color: #004d40; display: block; margin-bottom: 4px;">⚡ OOMA OFFICE WEBHOOK ENDPOINT URL:</label>
                                        <div class="webhook-input-group">
                                            <input type="text" class="webhook-input" id="voip-endpoint-ooma" readonly value="" data-suffix="/webhooks/voip?client_id={next_id}">
                                            <button type="button" onclick="copyText('voip-endpoint-ooma', 'voip-btn-ooma')" id="voip-btn-ooma" class="btn-copy"> Copy Webhook URL</button>
                                        </div>
                                    </div>
                                </div>

                                <div id="voip-inst-grasshopper" class="voip-inst-card" style="display: none; background-color: #f3e5f5; border-left: 4px solid #6a1b9a; padding: 14px; border-radius: 4px; color: #4a148c; font-size: 12px; line-height: 1.5;">
                                     <strong>Grasshopper Setup Instructions:</strong><br>
                                    1. Log into Grasshopper Admin Portal ➡️ <strong>Settings ➡️ Integrations & Webhooks</strong>.<br>
                                    2. Enable Call Webhook Notifications and paste your dynamic target endpoint URL below.<br>
                                    3. Ensure Voicemail & Call Transcriptions are toggled ON so call data streams automatically to LeadGrove!
                                    <div style="margin-top: 10px; background: white; padding: 10px; border-radius: 6px; border: 1px solid #e1bee7;">
                                        <label style="font-size: 11px; font-weight: bold; color: #4a148c; display: block; margin-bottom: 4px;">⚡ GRASSHOPPER WEBHOOK ENDPOINT URL:</label>
                                        <div class="webhook-input-group">
                                            <input type="text" class="webhook-input" id="voip-endpoint-grasshopper" readonly value="" data-suffix="/webhooks/voip?client_id={next_id}">
                                            <button type="button" onclick="copyText('voip-endpoint-grasshopper', 'voip-btn-grasshopper')" id="voip-btn-grasshopper" class="btn-copy"> Copy Webhook URL</button>
                                        </div>
                                    </div>
                                </div>

                                <div id="voip-inst-custom_voip" class="voip-inst-card" style="display: none; background-color: #eceff1; border-left: 4px solid #455a64; padding: 14px; border-radius: 4px; color: #263238; font-size: 12px; line-height: 1.5;">
                                     <strong>Custom VoIP / Other Setup Instructions:</strong><br>
                                    1. In your VoIP provider's developer console or Zapier Integration, configure an HTTP POST Webhook.<br>
                                    2. Set target destination URL to the endpoint below.<br>
                                    3. Ensure call recording URLs and customer phone parameters are included in the payload!
                                    <div style="margin-top: 10px; background: white; padding: 10px; border-radius: 6px; border: 1px solid #cfd8dc;">
                                        <label style="font-size: 11px; font-weight: bold; color: #263238; display: block; margin-bottom: 4px;">⚡ CUSTOM VOIP WEBHOOK ENDPOINT URL:</label>
                                        <div class="webhook-input-group">
                                            <input type="text" class="webhook-input" id="voip-endpoint-custom" readonly value="" data-suffix="/webhooks/voip?client_id={next_id}">
                                            <button type="button" onclick="copyText('voip-endpoint-custom', 'voip-btn-custom')" id="voip-btn-custom" class="btn-copy"> Copy Webhook URL</button>
                                        </div>
                                    </div>
                                </div>
                            </div>


                        <div id="sot-email-box" class="conditional-box">
                            <div class="form-group">
                                <label for="email_provider">Email Provider</label>
                                <select id="email_provider">
                                    <option value="gmail">Google Gmail API</option>
                                    <option value="outlook">Microsoft Outlook 365</option>
                                    <option value="custom_imap">Custom IMAP (Secure Server)</option>
                                </select>
                            </div>

                            <!-- Sales Agent Inbox #1 (Primary) -->
                            <div class="form-group">
                                <div style="display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-bottom: 5px;">
                                    <label for="email_account" style="font-weight: bold; margin-bottom: 0;">Sales Agent Inbox #1 (Primary Email to Monitor)</label>
                                    
                                    <!-- Speech Bubble Tooltip -->
                                    <span class="tooltip-icon">
                                        
                                        <span class="tooltip-text">
                                            As a secondary option, you can also have AI monitor your incoming emails by cc'ing a copy of every email correspondence to:<br>
                                            <strong class="wizard-forwarding-email" style="color: #81c784; word-break: break-all;">conversions-[id]@your-agency.com</strong><br>
                                            <span style="font-size: 9px; color: #ccc;">(Your actual ID will show up on the next screen once profile is created)</span>
                                        </span>
                                    </span>
                                    
                                    <!-- Check Logs Hover Link -->
                                    <span class="tooltip-icon" style="font-size: 11px; font-weight: bold; margin-left: 5px;">
                                        <a href="javascript:void(0)" style="color: #1a237e; text-decoration: underline;">check logs</a>
                                        <span class="tooltip-text" style="width: 290px;">
                                            <strong>Last 5 Emails Analyzed by System:</strong><br>
                                            <span style="color: #ccc; font-style: italic;">No emails analyzed yet (Onboarding in progress).</span>
                                        </span>
                                    </span>
                                </div>
                                <input type="text" id="email_account" placeholder="e.g. agent1@clientcompany.com">
                                <small style="color: #666; font-size: 11px; margin-top: 4px; display: block;">
                                    Enter primary sales agent email address where form leads arrive.
                                </small>
                            </div>
                            <div class="form-group" style="margin-top: 12px;">
                                <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 15px; margin-bottom: 5px;">
                                    <label for="email_app_password" style="font-weight: bold; margin-bottom: 0;">Inbox #1 App Password / IMAP Key</label>
                                    <a href="javascript:void(0)" onclick="openAppPasswordModal()" style="font-size: 12px; color: #1a237e; font-weight: bold; text-decoration: none; display: flex; align-items: center; gap: 4px;">
                                         How to get an App Password?
                                    </a>
                                </div>
                                <input type="password" id="email_app_password" placeholder="e.g. abcd efgh ijkl mnop">
                            </div>

                            <!-- Sales Agent Inbox #2 -->
                            <div id="wiz-agent-inbox-2" style="margin-top: 20px; padding-top: 15px; border-top: 1px dashed #ccc; display: none;">
                                <div class="form-group">
                                    <label for="email_account_2" style="font-weight: bold;">Sales Agent Inbox #2 (Optional Email to Monitor)</label>
                                    <input type="text" id="email_account_2" placeholder="e.g. agent2@clientcompany.com">
                                </div>
                                <div class="form-group" style="margin-top: 10px;">
                                    <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 15px; margin-bottom: 5px;">
                                        <label for="email_app_password_2" style="font-weight: bold;">Inbox #2 App Password / IMAP Key</label>
                                        <a href="javascript:void(0)" onclick="openAppPasswordModal()" style="font-size: 12px; color: #1a237e; font-weight: bold; text-decoration: none;"> How to get an App Password?</a>
                                    </div>
                                    <input type="password" id="email_app_password_2" placeholder="e.g. abcd efgh ijkl mnop">
                                </div>
                            </div>

                            <!-- Sales Agent Inbox #3 -->
                            <div id="wiz-agent-inbox-3" style="margin-top: 20px; padding-top: 15px; border-top: 1px dashed #ccc; display: none;">
                                <div class="form-group">
                                    <label for="email_account_3" style="font-weight: bold;">Sales Agent Inbox #3 (Optional Email to Monitor)</label>
                                    <input type="text" id="email_account_3" placeholder="e.g. agent3@clientcompany.com">
                                </div>
                                <div class="form-group" style="margin-top: 10px;">
                                    <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 15px; margin-bottom: 5px;">
                                        <label for="email_app_password_3" style="font-weight: bold;">Inbox #3 App Password / IMAP Key</label>
                                        <a href="javascript:void(0)" onclick="openAppPasswordModal()" style="font-size: 12px; color: #1a237e; font-weight: bold; text-decoration: none;"> How to get an App Password?</a>
                                    </div>
                                    <input type="password" id="email_app_password_3" placeholder="e.g. abcd efgh ijkl mnop">
                                </div>
                            </div>

                            <!-- Sales Agent Inbox #4 -->
                            <div id="wiz-agent-inbox-4" style="margin-top: 20px; padding-top: 15px; border-top: 1px dashed #ccc; display: none;">
                                <div class="form-group">
                                    <label for="email_account_4" style="font-weight: bold;">Sales Agent Inbox #4 (Optional Email to Monitor)</label>
                                    <input type="text" id="email_account_4" placeholder="e.g. agent4@clientcompany.com">
                                </div>
                                <div class="form-group" style="margin-top: 10px;">
                                    <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 15px; margin-bottom: 5px;">
                                        <label for="email_app_password_4" style="font-weight: bold;">Inbox #4 App Password / IMAP Key</label>
                                        <a href="javascript:void(0)" onclick="openAppPasswordModal()" style="font-size: 12px; color: #1a237e; font-weight: bold; text-decoration: none;"> How to get an App Password?</a>
                                    </div>
                                    <input type="password" id="email_app_password_4" placeholder="e.g. abcd efgh ijkl mnop">
                                </div>
                            </div>

                            <!-- Sales Agent Inbox #5 -->
                            <div id="wiz-agent-inbox-5" style="margin-top: 20px; padding-top: 15px; border-top: 1px dashed #ccc; display: none;">
                                <div class="form-group">
                                    <label for="email_account_5" style="font-weight: bold;">Sales Agent Inbox #5 (Optional Email to Monitor)</label>
                                    <input type="text" id="email_account_5" placeholder="e.g. agent5@clientcompany.com">
                                </div>
                                <div class="form-group" style="margin-top: 10px;">
                                    <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 15px; margin-bottom: 5px;">
                                        <label for="email_app_password_5" style="font-weight: bold;">Inbox #5 App Password / IMAP Key</label>
                                        <a href="javascript:void(0)" onclick="openAppPasswordModal()" style="font-size: 12px; color: #1a237e; font-weight: bold; text-decoration: none;"> How to get an App Password?</a>
                                    </div>
                                    <input type="password" id="email_app_password_5" placeholder="e.g. abcd efgh ijkl mnop">
                                </div>
                            </div>

                            <div style="margin-top: 15px; text-align: left;">
                                <button type="button" id="wiz-btn-add-sales-agent" onclick="addSalesAgentInboxRowWiz()" style="background: #e8eaf6; color: #1a237e; border: 1px solid #c5cae9; padding: 8px 14px; border-radius: 6px; font-weight: bold; font-size: 12px; cursor: pointer; transition: all 0.2s;">
                                    ➕ Add Another Sales Agent Inbox (Up to 5 Inboxes)
                                </button>
                            </div>
                        </div>
                    </div>
                    
                    <!-- STEP 4: Conversion & Deduplication Rules -->
                    <div class="wizard-step" id="step-panel-4">
                        <div class="instructions">
                             <strong>Step 4: Smart Deduplication & Conversion Control</strong><br>
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
                        
                        <!-- EXCLUSION UPLOAD PANEL -->
                        <div id="exclusion-upload-box" class="conditional-box" style="display: none; padding: 20px; margin-top: 15px;">
                            <div class="instructions" style="background-color: #f1f8e9; border-left-color: #2e7d32; color: #2e7d32; margin-bottom: 15px; font-size: 13px; line-height: 1.5; padding: 15px;">
                                 <strong>Upload Past Customers to Ignore:</strong><br>
                                Upload your list of customers, to use to ignore future conversion triggering. Only one piece of information is needed for each user in order to do this, but more data points for each user is best, for higher match rates. Here is a sample sheet that you can use to fill in, or you can provide your own sheet that have "first name, last name, email, phone number, company name" as the column headers.
                            </div>
                            <div style="display: flex; gap: 15px; align-items: center; margin-bottom: 15px; flex-wrap: wrap;">
                                <button type="button" onclick="triggerSampleSheetDownload()" class="btn-copy" style="background-color: #1a237e; padding: 8px 15px; font-size: 12px; cursor: pointer;"> Download Sample Sheet (.CSV)</button>
                                <input type="file" id="exclusion-file-input" accept=".csv" onchange="handleExclusionFileUpload(event)" style="display: none;">
                                <button type="button" onclick="document.getElementById('exclusion-file-input').click()" class="btn-copy" style="background-color: #2e7d32; padding: 8px 15px; font-size: 12px; cursor: pointer;"> Choose File & Upload (.CSV)</button>
                            </div>
                            <div style="margin-top: 15px; margin-bottom: 15px; background: #fff; padding: 12px; border: 1px solid #e0e0e0; border-radius: 6px;">
                                <label style="font-weight: bold; font-size: 12px; margin-bottom: 8px; display: block; color: #1a237e;"> Exclusions Upload Strategy:</label>
                                <div style="display: flex; gap: 20px; align-items: center;">
                                    <label style="font-weight: normal; cursor: pointer; display: flex; align-items: center; gap: 6px; font-size: 12px; margin: 0;">
                                        <input type="radio" name="exclusion_upload_action" value="append" checked style="cursor: pointer;">
                                        <strong>Append new records</strong> (Keep existing ones, only add new customers)
                                    </label>
                                    <label style="font-weight: normal; cursor: pointer; display: flex; align-items: center; gap: 6px; font-size: 12px; margin: 0;">
                                        <input type="radio" name="exclusion_upload_action" value="replace" style="cursor: pointer;">
                                        <strong>Overwrite list</strong> (Wipe existing entries and start fresh)
                                    </label>
                                </div>
                            </div>
                            <div id="upload-status-box" class="alert alert-success" style="display: none; margin-bottom: 0; font-size: 12px; padding: 12px;"></div>
                        </div>
                    </div>
                    
                    <!-- STEP 5: Ad Goals & Conversions Setup -->
                    <div class="wizard-step" id="step-panel-5">
                        <div class="instructions">
                             <strong>Step 5: Ad Accounts Goal & Conversion Setup</strong><br>
                            To allow conversion uploads to sync successfully, you must create these 2 matching goals/events inside your active ad networks. Use these exact, standardized goal names for consistency across all systems.
                        </div>
                        
                        <div style="background: #fff8e1; border-left: 4px solid #ffb300; padding: 15px; border-radius: 6px; color: #5d4037; font-size: 13px; line-height: 1.5; margin-bottom: 25px;">
                             <strong>Ad Platform Goal Consistency Rule:</strong><br>
                            Make sure you name these 2 custom conversions or offline goals <strong>EXACTLY</strong> as shown below in your ad managers. Our CSV spreadsheet exports write these specific, standardized names in every row to trigger conversion syncs!
                        </div>
                        
                        <div id="step-5-instructions-container" style="max-height: 450px; overflow-y: auto; padding: 5px; border-radius: 6px;">
                            <!-- Dynamically generated instructions will be injected here -->
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
                    <div style="font-size: 50px; margin-bottom: 15px;"></div>
                    <h2 style="color: #2e7d32; margin-top: 0;">Client Onboarded Successfully!</h2>
                    <p style="color: #555; font-size: 14px; margin-bottom: 25px;">
                        The account configuration file for <strong id="registered-client-name"></strong> has been created.
                    </p>
                    
                    <!-- Call Tracking Step (Always Shown) -->
                    <div class="instructions" style="text-align: left; background-color: #e8eaf6; border-left: 4px solid #1a237e; margin-bottom: 10px;">
                         <strong id="call-tracking-provider-title">Step 2: Configure CallRail Integration</strong><br>
                        Your live call tracking webhook endpoint is ready. Copy this link and paste it into <span id="call-tracking-provider-span">CallRail</span>:
                    </div>
                    <div style="display: flex; gap: 8px; margin-bottom: 15px;">
                        <input type="text" id="webhook-url-input" readonly style="flex: 1; padding: 10px; border-radius: 6px; border: 1px solid #ced4da; font-family: monospace; font-size: 12px; background-color: #f8f9fa;">
                        <button type="button" onclick="copyWebhookUrl('webhook-url-input', 'copy-btn')" id="copy-btn" class="btn-submit" style="margin: 0; width: auto; white-space: nowrap; padding: 0 15px; font-size: 14px; background-color: #2e7d32;"> Copy URL</button>
                    </div>
                    <div id="call-tracking-instructions-reminder" class="instructions" style="text-align: left; background-color: #fff3cd; border-left-color: #ffc107; color: #856404; font-size: 11px; margin-top: -10px; margin-bottom: 25px; padding: 12px 15px;">
                        ⚠️ <strong>CallRail Setup Checklist (Inbound & Outbound Call Recording & Transcripts):</strong><br>
                        <ol style="margin: 6px 0 0 0; padding-left: 18px; line-height: 1.6; font-size: 11px;">
                            <li>Log into <strong>CallRail</strong> and go to <strong>Settings ➡️ Company / Numbers ➡️ Call Recording & Transcripts</strong>.</li>
                            <li>Ensure both <strong>Inbound & Outbound Call Recording</strong> and <strong>Speech-to-Text Transcripts</strong> are toggled <strong>ON</strong> in English for all target tracking numbers.</li>
                            <li>Go to <strong>Integrations ➡️ Webhooks</strong>, paste your dynamic target URL (above), and set the trigger event to <strong>"Call Completed"</strong> so complete call recordings and transcripts are compiled and sent to LeadGrove automatically!</li>
                        </ol>
                    </div>

                    <!-- CRM / Billing Program Connection Step (Shown conditionally) -->
                    <div id="sot-instructions-box" style="display: none; margin-top: 25px;">
                        <div class="instructions" id="sot-instructions-label" style="text-align: left; background-color: #e8f5e9; border-left: 4px solid #2e7d32; color: #1b5e20; margin-bottom: 10px;">
                            ⚙️ <strong>Step 3: Connect Your Platform Webhook</strong><br>
                        </div>
                        <div style="display: flex; gap: 8px; margin-bottom: 20px;">
                            <input type="text" id="sot-webhook-input" readonly style="flex: 1; padding: 10px; border-radius: 6px; border: 1px solid #ced4da; font-family: monospace; font-size: 12px; background-color: #f8f9fa;">
                            <button type="button" onclick="copyWebhookUrl('sot-webhook-input', 'sot-copy-btn')" id="sot-copy-btn" class="btn-submit" style="margin: 0; width: auto; white-space: nowrap; padding: 0 15px; font-size: 14px; background-color: #2e7d32;"> Copy URL</button>
                        </div>
                    </div>

                    <!-- Email Forwarding Connection Step (Shown conditionally) -->
                    <div id="sot-email-instructions-box" style="display: none; margin-top: 25px;">
                        <div class="instructions" style="text-align: left; background-color: #e8f5e9; border-left: 4px solid #2e7d32; color: #1b5e20; margin-bottom: 10px;">
                             <strong>Step 3: Set Up Email Forwarding</strong><br>
                            To allow conversion auditing, set up an email auto-forwarding rule in your inbox. Forward any matching customer invoice or booking confirmation alerts to this custom system email:
                        </div>
                        <div style="display: flex; gap: 8px; margin-bottom: 15px;">
                            <input type="text" id="sot-email-address" readonly style="flex: 1; padding: 10px; border-radius: 6px; border: 1px solid #ced4da; font-family: monospace; font-size: 12px; background-color: #f8f9fa;">
                            <button type="button" onclick="copyWebhookUrl('sot-email-address', 'sot-email-copy-btn')" id="sot-email-copy-btn" class="btn-submit" style="margin: 0; width: auto; white-space: nowrap; padding: 0 15px; font-size: 14px; background-color: #2e7d32;"> Copy Email</button>
                        </div>
                        <div class="instructions" style="text-align: left; background-color: #fff3cd; border-left-color: #ffc107; color: #856404; font-size: 11px; margin-top: -10px; margin-bottom: 25px; padding: 8px 12px;">
                             <strong>Tip:</strong> Create a rule in Gmail or Outlook to forward emails with subject keywords like "invoice" or "booking confirmation" automatically.
                        </div>
                    </div>
                    
                    
                    <!-- Real-Time CRM Exclusions Step (Shown conditionally) -->
                    <div id="exclusion-instructions-box" style="display: none; margin-top: 25px;">
                        <div class="instructions" style="text-align: left; background-color: #f1f8e9; border-left: 4px solid #2e7d32; color: #2e7d32; margin-bottom: 10px;">
                             <strong>Step 4: Connect CRM for Real-Time Exclusions</strong><br>
                            You enabled past customer exclusions! Copy this exclusion webhook URL and paste it into HubSpot, ServiceTitan, Salesforce, Zoho, or Zapier. Whenever a contact is added or a deal is won in your CRM, trigger a POST request to this URL to automatically add their contact info to our exclusion list in real-time:
                        </div>
                        <div style="display: flex; gap: 8px; margin-bottom: 15px;">
                            <input type="text" id="exclusion-webhook-url-input" readonly style="flex: 1; padding: 10px; border-radius: 6px; border: 1px solid #ced4da; font-family: monospace; font-size: 12px; background-color: #f8f9fa;">
                            <button type="button" onclick="copyWebhookUrl('exclusion-webhook-url-input', 'exclusion-copy-btn')" id="exclusion-copy-btn" class="btn-submit" style="margin: 0; width: auto; white-space: nowrap; padding: 0 15px; font-size: 14px; background-color: #2e7d32;"> Copy Webhook</button>
                        </div>
                    </div>

                    <a href="/dashboard" class="btn-submit" style="display: block; text-decoration: none; text-align: center; line-height: 20px; background-color: #1a237e; color: white !important; margin-top: 30px;"> Proceed to Dashboard</a>
                </div>
                

                <!-- App Password Modal Overlay -->
                <div id="app-password-modal" class="modal-overlay" style="display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background-color: rgba(0,0,0,0.5); z-index: 10000; align-items: center; justify-content: center;">
                    <div class="modal-card" style="background: white; border-radius: 12px; box-shadow: 0 10px 30px rgba(0,0,0,0.2); max-width: 550px; width: 90%; text-align: left; overflow: hidden; display: flex; flex-direction: column;">
                        <!-- Header -->
                        <div class="modal-header" style="background: #1a237e; color: white; padding: 16px 20px; display: flex; justify-content: space-between; align-items: center;">
                            <h3 style="margin: 0; font-size: 18px; color: #1a237e; display: flex; align-items: center; gap: 8px;">
                                 Generate a Secure App Password
                            </h3>
                            <span class="modal-close" onclick="closeAppPasswordModal()" style="font-size: 24px; font-weight: bold; cursor: pointer; color: white; opacity: 0.8;">&times;</span>
                        </div>

                        <!-- Body -->
                        <div class="modal-body" style="padding: 20px; max-height: 70vh; overflow-y: auto; text-align: left;">
                            <p style="margin-top: 0; font-size: 13px; line-height: 1.5; color: #555;">
                                For security, modern email networks require a <strong>16-character App Password</strong> rather than your standard account login password. This restricts our AI's access strictly to reading incoming booking emails via IMAP.
                            </p>

                            <!-- Provider Tabs -->
                            <div style="display: flex; border-bottom: 2px solid #e0e0e0; margin-bottom: 15px; flex-wrap: wrap;">
                                <button type="button" id="tab-btn-google" class="tab-btn active" onclick="switchModalTab('google')">
                                     Google Workspace / Gmail
                                </button>
                                <button type="button" id="tab-btn-ms" class="tab-btn" onclick="switchModalTab('ms')">
                                     Microsoft 365 / Outlook
                                </button>
                                <button type="button" id="tab-btn-imap" class="tab-btn" onclick="switchModalTab('imap')">
                                     Custom IMAP / cPanel / Other
                                </button>
                            </div>

                            <!-- Tab Content: Google -->
                            <div id="modal-tab-google" class="tab-content">
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; color: #333; margin: 0;">
                                    <li style="margin-bottom: 8px;">Go to your <a href="https://myaccount.google.com/security" target="_blank" style="color: #1a237e; font-weight: bold; text-decoration: none;">Google Account Security Panel</a>.</li>
                                    <li style="margin-bottom: 8px;">Ensure <strong>2-Step Verification</strong> is active under "How you sign in to Google".</li>
                                    <li style="margin-bottom: 8px;">Type <strong>"App Passwords"</strong> in Google's search bar, or scroll to the bottom of 2-Step Verification and click <strong>App Passwords</strong>.</li>
                                    <li style="margin-bottom: 8px;">Enter a custom name (e.g., <code>LeadGroove Conversion Engine</code>) and click <strong>Create</strong>.</li>
                                    <li style="margin-bottom: 8px;">Copy the <strong>16-character code</strong> inside Google's yellow box, strip any spaces, and enter it as your password!</li>
                                </ol>
                            </div>

                            <!-- Tab Content: Microsoft -->
                            <div id="modal-tab-ms" class="tab-content" style="display: none;">
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; color: #333; margin: 0;">
                                    <li style="margin-bottom: 8px;">Go to your <a href="https://mysignins.microsoft.com/security-info" target="_blank" style="color: #1a237e; font-weight: bold; text-decoration: none;">Microsoft Security Info Page</a>.</li>
                                    <li style="margin-bottom: 8px;">Click the <strong>+ Add sign-in method</strong> button at the top.</li>
                                    <li style="margin-bottom: 8px;">Select <strong>App Password</strong> from the dropdown menu and click <strong>Add</strong>.</li>
                                    <li style="margin-bottom: 8px;">Name it (e.g., <code>LeadGroove Offline Tracker</code>) and click <strong>Next</strong>.</li>
                                    <li style="margin-bottom: 8px;">Copy the <strong>16-character password key</strong> immediately before closing the confirmation window.</li>
                                </ol>
                            </div>

                            <!-- Tab Content: Custom IMAP / Other Providers -->
                            <div id="modal-tab-imap" class="tab-content" style="display: none;">
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; color: #333; margin: 0;">
                                    <li style="margin-bottom: 8px;">Log into your hosting control panel or email admin settings (e.g., <strong>cPanel, Webmail, Yahoo, iCloud, Fastmail, Zoho Mail, GoDaddy, or Namecheap</strong>).</li>
                                    <li style="margin-bottom: 8px;">Navigate to <strong>Email Accounts ➡️ Security / Two-Factor Authentication</strong> or <strong>App Passwords</strong>.</li>
                                    <li style="margin-bottom: 8px;">If your email provider enforces 2FA (e.g., Yahoo, Apple iCloud, Zoho), create a dedicated <strong>App Password</strong> named <code>LeadGroove IMAP Sync</code>.</li>
                                    <li style="margin-bottom: 8px;">If your server uses standard IMAP authentication (e.g., cPanel, Webmail, self-hosted server), use your standard account email password.</li>
                                    <li style="margin-bottom: 8px;">Ensure IMAP access is enabled on port <strong>993 (SSL/TLS)</strong> or port <strong>143 (STARTTLS)</strong>.</li>
                                </ol>
                            </div>

                            <!-- Security Footnote -->
                            <div style="background-color: #f1f8e9; border-left: 4px solid #2e7d32; padding: 12px; margin-top: 20px; border-radius: 4px;">
                                <p style="margin: 0; font-size: 11px; line-height: 1.4; color: #1b5e20;">
                                     <strong>Strict Privacy Guard:</strong> This code grants read-only IMAP credentials. It does not access your emails, calendars, or account dashboards. You can revoke it instantly at any time in your security settings.
                                </p>
                            </div>
                        </div>

                        <!-- Footer -->
                        <div class="modal-footer" style="padding: 15px 20px; border-top: 1px solid #eaeaea; background-color: #f8f9fa; display: flex; justify-content: flex-end; border-bottom-left-radius: 12px; border-bottom-right-radius: 12px;">
                            <button type="button" class="btn-modal-close" onclick="closeAppPasswordModal()">Got It, Thanks!</button>
                        </div>
                    </div>
                </div>

                <a href="/dashboard" class="btn-cancel" id="cancel-link">⬅️ Cancel and Return to Dashboard</a>
            </div>
            
            <script>

                function openAppPasswordModal() {
                    const providerSelect = document.getElementById('email_provider');
                    const selectedProvider = providerSelect ? providerSelect.value : 'gmail';
                    if (selectedProvider === 'outlook') {
                        switchModalTab('ms');
                    } else if (selectedProvider === 'custom_imap') {
                        switchModalTab('imap');
                    } else {
                        switchModalTab('google');
                    }
                    document.getElementById('app-password-modal').style.display = 'flex';
                }
                
                function closeAppPasswordModal() {
                    document.getElementById('app-password-modal').style.display = 'none';
                }
                
                function switchModalTab(provider) {
                    const btnG = document.getElementById('tab-btn-google');
                    const btnM = document.getElementById('tab-btn-ms');
                    const btnI = document.getElementById('tab-btn-imap');
                    const tabG = document.getElementById('modal-tab-google');
                    const tabM = document.getElementById('modal-tab-ms');
                    const tabI = document.getElementById('modal-tab-imap');
                    
                    if (btnG) btnG.classList.remove('active');
                    if (btnM) btnM.classList.remove('active');
                    if (btnI) btnI.classList.remove('active');
                    
                    if (tabG) tabG.style.display = 'none';
                    if (tabM) tabM.style.display = 'none';
                    if (tabI) tabI.style.display = 'none';
                    
                    if (provider === 'google') {
                        if (btnG) btnG.classList.add('active');
                        if (tabG) tabG.style.display = 'block';
                    } else if (provider === 'ms') {
                        if (btnM) btnM.classList.add('active');
                        if (tabM) tabM.style.display = 'block';
                    } else if (provider === 'imap') {
                        if (btnI) btnI.classList.add('active');
                        if (tabI) tabI.style.display = 'block';
                    }
                }

                // Close modal if user clicks outside of the card
                window.addEventListener('click', (e) => {
                    const overlay = document.getElementById('app-password-modal');
                    if (e.target === overlay) {
                        closeAppPasswordModal();
                    }
                });

                // Auto-populate the active hostname into wizard forwarding tooltips
                window.addEventListener('DOMContentLoaded', () => {
                    const origin = window.location.origin;
                    const host = window.location.host;
                    const emailDomain = host.includes('localhost') ? 'your-agency.com' : host.replace('www.', '').split(':')[0];
                    const wizardEmailLabel = document.querySelector('.wizard-forwarding-email');
                    if (wizardEmailLabel) {
                        wizardEmailLabel.innerText = `conversions-[id]@${emailDomain}`;
                    }
                    document.querySelectorAll('.monthly-forwarding-email').forEach(el => {
                        el.innerText = `conversions-[id]@${emailDomain}`;
                    });
                    document.querySelectorAll('.webhook-input').forEach(input => {
                        const suffix = input.getAttribute('data-suffix');
                        if (suffix) {
                            input.value = origin + suffix;
                        }
                    });
                    toggleCallTrackingFields();
                });
                
                function toggleCallTrackingFields() {
                    const provider = document.getElementById('call_tracking_provider').value;
                    const crBox = document.getElementById('call_tracking_callrail_box');
                    const ctmBox = document.getElementById('call_tracking_ctm_box');
                    const wcBox = document.getElementById('call_tracking_wc_box');
                    
                    if (provider === 'callrail') {
                        crBox.style.display = 'flex';
                        ctmBox.style.display = 'none';
                        wcBox.style.display = 'none';
                    } else if (provider === 'calltrackingmetrics') {
                        crBox.style.display = 'none';
                        ctmBox.style.display = 'flex';
                        wcBox.style.display = 'none';
                    } else if (provider === 'whatconverts') {
                        crBox.style.display = 'none';
                        ctmBox.style.display = 'none';
                        wcBox.style.display = 'flex';
                    }
                }

                let currentStep = 1;
                const totalSteps = 5;
                
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
                            if (!name) {
                                showErrorAlert('Please fill out Client Business Name to continue.');
                                return;
                            }
                            hideErrorAlert();
                        }
                    }
                    
                    document.getElementById(`step-panel-${currentStep}`).classList.remove('active');
                    currentStep += direction;
                    document.getElementById(`step-panel-${currentStep}`).classList.add('active');
                    
                    if (currentStep === 5) {{
                        generateStep5Instructions();
                    }}
                    
                    const prevBtn = document.getElementById('prev-btn');
                    const nextBtn = document.getElementById('next-btn');
                    
                    prevBtn.style.visibility = currentStep === 1 ? 'hidden' : 'visible';
                    
                    if (currentStep === totalSteps) {
                        nextBtn.innerText = ' Complete Onboarding';
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
                
                let parsedExclusions = [];

                function handleExclusionFileUpload(event) {
                    const file = event.target.files[0];
                    if (!file) return;
                    
                    const reader = new FileReader();
                    reader.onload = function(e) {
                        const text = e.target.result;
                        parseCSVToExclusions(text, file.name);
                    };
                    reader.readAsText(file);
                }

                function parseCSVToExclusions(text, filename) {
                    const lines = text.split(/\\r\\n|\\n/);
                    if (lines.length === 0) {
                        showUploadStatus('Error: The file is empty.', 'error');
                        return;
                    }
                    
                    function parseCSVLine(line) {
                        let arr = [];
                        let quote = false;
                        let cell = "";
                        for (let colIdx = 0; colIdx < line.length; colIdx++) {
                            let char = line[colIdx];
                            if (char === '"') {
                                quote = !quote;
                            } else if (char === ',' && !quote) {
                                arr.push(cell.trim());
                                cell = "";
                            } else {
                                cell += char;
                            }
                        }
                        arr.push(cell.trim());
                        return arr;
                    }
                    
                    const headers = parseCSVLine(lines[0]).map(h => h.toLowerCase().replace(/[^a-z0-9]/g, ''));
                    if (headers.length === 0 || headers.join('').trim() === '') {
                        showUploadStatus('Error: Could not read headers from the first row of your CSV file.', 'error');
                        return;
                    }
                    
                    let fnIdx = headers.findIndex(h => h.includes('firstname') || h.includes('first'));
                    let lnIdx = headers.findIndex(h => h.includes('lastname') || h.includes('last'));
                    let emailIdx = headers.findIndex(h => h.includes('email') || h.includes('mail'));
                    let phoneIdx = headers.findIndex(h => h.includes('phone') || h.includes('tel') || h.includes('mobile'));
                    let compIdx = headers.findIndex(h => h.includes('company') || h.includes('business'));
                    
                    if (fnIdx === -1 && lnIdx === -1 && emailIdx === -1 && phoneIdx === -1 && compIdx === -1) {
                        fnIdx = 0; lnIdx = 1; emailIdx = 2; phoneIdx = 3; compIdx = 4;
                    }
                    
                    let list = [];
                    for (let i = 1; i < lines.length; i++) {
                        const line = lines[i].trim();
                        if (!line) continue;
                        
                        const row = parseCSVLine(line);
                        if (row.length === 0 || row.join('').trim() === '') continue;
                        
                        const cust = {
                            first_name: fnIdx !== -1 && row[fnIdx] ? row[fnIdx] : "",
                            last_name: lnIdx !== -1 && row[lnIdx] ? row[lnIdx] : "",
                            email: emailIdx !== -1 && row[emailIdx] ? row[emailIdx] : "",
                            phone: phoneIdx !== -1 && row[phoneIdx] ? row[phoneIdx] : "",
                            company_name: compIdx !== -1 && row[compIdx] ? row[compIdx] : ""
                        };
                        
                        if (cust.first_name || cust.last_name || cust.email || cust.phone || cust.company_name) {
                            list.push(cust);
                        }
                    }
                    
                    parsedExclusions = list;
                    showUploadStatus(`✓ Loaded ${list.length} exclusions from "${filename}". Save changes to apply!`, 'success');
                    
                    // Option B: Visual Pulse & Highlight of onboarding submit button
                    const nextBtn = document.getElementById('next-btn');
                    if (nextBtn) {
                        nextBtn.classList.add('btn-pulse-save');
                        nextBtn.innerHTML = ` Complete Onboarding (With ${list.length} Exclusions!)`;
                    }
                }

                function showUploadStatus(message, type) {
                    const statusBox = document.getElementById('upload-status-box');
                    statusBox.innerText = message;
                    statusBox.className = type === 'success' ? 'alert alert-success' : 'alert alert-error';
                    statusBox.style.display = 'block';
                }

                function triggerSampleSheetDownload() {
                    const headers = ["First Name", "Last Name", "Email", "Phone Number", "Company Name"];
                    const sampleRows = [
                        ["John", "Doe", "john.doe@example.com", "555-123-4567", "Doe Plumbing Inc"],
                        ["Jane", "Smith", "jane@company.com", "555-987-6543", "Smith Solar Corp"]
                    ];
                    let csvContent = "data:text/csv;charset=utf-8,";
                    csvContent += headers.join(",") + "\\n";
                    sampleRows.forEach(row => {
                        csvContent += row.join(",") + "\\n";
                    });
                    const encodedUri = encodeURI(csvContent);
                    const link = document.createElement("a");
                    link.setAttribute("href", encodedUri);
                    link.setAttribute("download", "sample_customer_exclusions.csv");
                    document.body.appendChild(link);
                    link.click();
                    document.body.removeChild(link);
                }

                function selectCardRadio(name, value, element) {
                    element.parentNode.querySelectorAll('.card-radio').forEach(card => {
                        card.classList.remove('selected');
                    });
                    element.classList.add('selected');
                    element.querySelector('input[type="radio"]').checked = true;
                    
                    if (name === 'sales_source') {
                        updateFunnelWonOverlay(value);
                    }
                    
                    if (name === 'lead_gen_method') {
                        toggleSOTFields();
                    }
                    
                    if (name === 'exclude_past_customers') {
                        const uploadBox = document.getElementById('exclusion-upload-box');
                        const msgBox = document.getElementById('existing-exclusions-msg');
                        if (value === 'YES') {
                            uploadBox.style.display = 'block';
                            if (msgBox) msgBox.style.display = 'block';
                        } else {
                            uploadBox.style.display = 'none';
                            if (msgBox) msgBox.style.display = 'none';
                            
                            // Reset submit button if disabled exclusions
                            const nextBtn = document.getElementById('next-btn');
                            if (nextBtn) {
                                nextBtn.classList.remove('btn-pulse-save');
                                if (currentStep === totalSteps) {
                                    nextBtn.innerHTML = ' Complete Onboarding';
                                }
                            }
                            parsedExclusions = [];
                        }
                    }
                }
                
                function generateStep5Instructions() {{
                    const instructionsContainer = document.getElementById('step-5-instructions-container');
                    if (!instructionsContainer) return;
                    
                    // Clear previous dynamic content
                    instructionsContainer.innerHTML = '';
                    
                    const googleAds = document.getElementById('google_ads_customer_id').value.trim();
                    const facebookAds = document.getElementById('facebook_ads_id').value.trim();
                    const linkedinAds = document.getElementById('linkedin_ads_id').value.trim();
                    const microsoftAds = document.getElementById('microsoft_ads_id').value.trim();
                    const tiktokAds = document.getElementById('tiktok_ads_id').value.trim();
                    const twitterAds = document.getElementById('twitter_ads_id').value.trim();
                    const pinterestAds = document.getElementById('pinterest_ads_id').value.trim();
                    const snapchatAds = document.getElementById('snapchat_ads_id').value.trim();
                    const chatgptAds = document.getElementById('chatgpt_ads_id').value.trim();
                    const redditAds = document.getElementById('reddit_ads_id').value.trim();
                    
                    let blocks = [];
                    
                    if (googleAds) {{
                        blocks.push(`
                            <div style="background: #fdfdfd; border: 1px solid #e0e0e0; border-left: 4px solid #4285F4; padding: 15px; border-radius: 6px; margin-bottom: 20px;">
                                <h4 style="margin-top: 0; color: #4285F4; display: flex; align-items: center; gap: 8px; font-size: 14px;">
                                     Google Ads Goal Setup (ID: ${googleAds})
                                </h4>
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; margin: 0; color: #333;">
                                    <li style="margin-bottom: 8px;">Log into your <strong>Google Ads account</strong>.</li>
                                    <li style="margin-bottom: 8px;">Navigate to <strong>Goals ➡️ Conversions ➡️ Summary</strong>.</li>
                                    <li style="margin-bottom: 8px;">Click <strong>+ New conversion action</strong>, select <strong>Import</strong>, choose <strong>Other data sources or CRMs</strong>, select <strong>Track conversions from clicks</strong>, and click <strong>Continue</strong>.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 1 (Qualification):</strong> Set Goal Name to <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Qualified Lead</code>. Under Category, choose <strong>Qualified Lead</strong>. Set Value to use a default value of <code>$1.00</code>.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 2 (Offline Sale):</strong> Create a second conversion import action. Set Goal Name to <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Offline Sale</code>. Under Category, choose <strong>Purchase</strong> or <strong>Converted Lead</strong>. Set Value to <strong>Use different values for each conversion</strong> (defaulting to <code>$0.00</code>).</li>
                                    <li style="margin-bottom: 8px; background: #fff8e1; border-left: 3px solid #ffa000; padding: 8px 10px; border-radius: 4px; color: #5d4037;"> <strong>Recommended Conversion Settings:</strong> Under <strong>Count</strong>, select <strong>Every</strong> conversion (so multiple sales/leads from the same user are tracked). Always select the <strong>longest allowable conversion window</strong> (e.g., 90-day click-through window) each time to maximize historical match depth.</li>
                                    <li style="margin-bottom: 8px; background: #e8f5e9; border-left: 3px solid #2e7d32; padding: 8px 10px; border-radius: 4px; color: #1b5e20;"> <strong>Hands-Free Automated Sync (Optional):</strong> Want Google Ads to pull conversions automatically without manual CSV uploads? Go to <strong>Goals ➡️ Conversions ➡️ Uploads ➡️ Schedules</strong>, click <strong>+</strong>, select <strong>HTTPS</strong> as Source, and paste your live LeadGrove feed URL: <code style="background: #fff; padding: 2px 5px; border-radius: 3px; border: 1px solid #a5d6a7;">https://your-agency-app.onrender.com/feeds/google-conversions.csv?client_id=[id]</code>! (For automated retractions & restatements, set up a second schedule under the Adjustments tab using: <code style="background: #fff; padding: 2px 5px; border-radius: 3px; border: 1px solid #b0bec5;">https://your-agency-app.onrender.com/feeds/google-adjustments.csv?client_id=[id]</code>).</li>
                                </ol>
                            </div>
                        `);
                    }}
                    if (chatgptAds) {{
                        blocks.push(`
                            <div style="background: #fdfdfd; border: 1px solid #e0e0e0; border-left: 4px solid #10a37f; padding: 15px; border-radius: 4px; margin-bottom: 20px;">
                                <h4 style="margin-top: 0; color: #10a37f; display: flex; align-items: center; gap: 8px; font-size: 14px;">
                                     ChatGPT Ads Goal Setup (ID: ${chatgptAds})
                                </h4>
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; margin: 0; color: #333;">
                                    <li style="margin-bottom: 8px;">Log into your <strong>ChatGPT Ads Campaign Manager</strong>.</li>
                                    <li style="margin-bottom: 8px;">Go to <strong>Conversion Event Manager</strong> and click <strong>Create Custom Conversion Goal</strong>.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 1 (Qualification):</strong> Name the custom offline event <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Qualified Lead</code>. Under action, select <strong>Lead</strong>, and set tracing to utilize secure CSV match.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 2 (Offline Sale):</strong> Create another custom event. Name it <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Offline Sale</code>, choose event type <strong>Purchase</strong>, and ensure dynamic revenue mapping is selected.</li>
                                </ol>
                            </div>
                        `);
                    }}

                    
                    if (facebookAds) {{
                        blocks.push(`
                            <div style="background: #fdfdfd; border: 1px solid #e0e0e0; border-left: 4px solid #1877F2; padding: 15px; border-radius: 6px; margin-bottom: 20px;">
                                <h4 style="margin-top: 0; color: #1877F2; display: flex; align-items: center; gap: 8px; font-size: 14px;">
                                     Meta / Facebook Ads Goal Setup (Pixel: ${facebookAds})
                                </h4>
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; margin: 0; color: #333;">
                                    <li style="margin-bottom: 8px;">Log into your <strong>Meta Events Manager</strong>.</li>
                                    <li style="margin-bottom: 8px;">Go to <strong>Custom Conversions</strong> in the left-hand navigation and click <strong>Create Custom Conversion</strong>.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 1 (Qualification):</strong> Name the conversion <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Qualified Lead</code>. Choose your Pixel, set the Event to <strong>Lead</strong>, and set rules if necessary.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 2 (Offline Sale):</strong> Create another Custom Conversion. Name it <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Offline Sale</code>, set the Event to <strong>Purchase</strong>, and ensure the value is mapped from the CSV uploads.</li>
                                </ol>
                            </div>
                        `);
                    }}
                    if (chatgptAds) {{
                        blocks.push(`
                            <div style="background: #fdfdfd; border: 1px solid #e0e0e0; border-left: 4px solid #10a37f; padding: 15px; border-radius: 4px; margin-bottom: 20px;">
                                <h4 style="margin-top: 0; color: #10a37f; display: flex; align-items: center; gap: 8px; font-size: 14px;">
                                     ChatGPT Ads Goal Setup (ID: ${chatgptAds})
                                </h4>
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; margin: 0; color: #333;">
                                    <li style="margin-bottom: 8px;">Log into your <strong>ChatGPT Ads Campaign Manager</strong>.</li>
                                    <li style="margin-bottom: 8px;">Go to <strong>Conversion Event Manager</strong> and click <strong>Create Custom Conversion Goal</strong>.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 1 (Qualification):</strong> Name the custom offline event <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Qualified Lead</code>. Under action, select <strong>Lead</strong>, and set tracing to utilize secure CSV match.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 2 (Offline Sale):</strong> Create another custom event. Name it <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Offline Sale</code>, choose event type <strong>Purchase</strong>, and ensure dynamic revenue mapping is selected.</li>
                                </ol>
                            </div>
                        `);
                    }}

                    
                    if (linkedinAds) {{
                        blocks.push(`
                            <div style="background: #fdfdfd; border: 1px solid #e0e0e0; border-left: 4px solid #0A66C2; padding: 15px; border-radius: 4px; margin-bottom: 20px;">
                                <h4 style="margin-top: 0; color: #0A66C2; display: flex; align-items: center; gap: 8px; font-size: 14px;">
                                     LinkedIn Ads Goal Setup (Account: ${linkedinAds})
                                </h4>
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; margin: 0; color: #333;">
                                    <li style="margin-bottom: 8px;">Log into <strong>LinkedIn Campaign Manager</strong>.</li>
                                    <li style="margin-bottom: 8px;">Click <strong>Analyze ➡️ Conversion Tracking</strong> in the left sidebar.</li>
                                    <li style="margin-bottom: 8px;">Click <strong>Create Conversion</strong>, and configure:
                                        <ul style="list-style-type: disc; padding-left: 15px; margin-top: 4px;">
                                            <li><strong>Goal 1 (Qualification):</strong> Name it <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Qualified Lead</code>, set key event to <strong>Lead</strong>, and choose <strong>Offline Upload (CSV)</strong> as your tracking method.</li>
                                            <li><strong>Goal 2 (Offline Sale):</strong> Create another conversion. Name it <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Offline Sale</code>, set event type to <strong>Purchase</strong>, and select <strong>Offline Upload (CSV)</strong>.</li>
                                        </ul>
                                    </li>
                                </ol>
                            </div>
                        `);
                    }}
                    if (chatgptAds) {{
                        blocks.push(`
                            <div style="background: #fdfdfd; border: 1px solid #e0e0e0; border-left: 4px solid #10a37f; padding: 15px; border-radius: 4px; margin-bottom: 20px;">
                                <h4 style="margin-top: 0; color: #10a37f; display: flex; align-items: center; gap: 8px; font-size: 14px;">
                                     ChatGPT Ads Goal Setup (ID: ${chatgptAds})
                                </h4>
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; margin: 0; color: #333;">
                                    <li style="margin-bottom: 8px;">Log into your <strong>ChatGPT Ads Campaign Manager</strong>.</li>
                                    <li style="margin-bottom: 8px;">Go to <strong>Conversion Event Manager</strong> and click <strong>Create Custom Conversion Goal</strong>.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 1 (Qualification):</strong> Name the custom offline event <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Qualified Lead</code>. Under action, select <strong>Lead</strong>, and set tracing to utilize secure CSV match.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 2 (Offline Sale):</strong> Create another custom event. Name it <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Offline Sale</code>, choose event type <strong>Purchase</strong>, and ensure dynamic revenue mapping is selected.</li>
                                </ol>
                            </div>
                        `);
                    }}

                    
                    if (microsoftAds) {{
                        blocks.push(`
                            <div style="background: #fdfdfd; border: 1px solid #e0e0e0; border-left: 4px solid #00A4EF; padding: 15px; border-radius: 4px; margin-bottom: 20px;">
                                <h4 style="margin-top: 0; color: #00A4EF; display: flex; align-items: center; gap: 8px; font-size: 14px;">
                                     Microsoft (Bing) Ads Goal Setup (ID: ${microsoftAds})
                                </h4>
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; margin: 0; color: #333;">
                                    <li style="margin-bottom: 8px;">Log into your <strong>Microsoft Advertising Dashboard</strong>.</li>
                                    <li style="margin-bottom: 8px;">Go to <strong>Tools ➡️ Conversion goals</strong> and click <strong>Create</strong>.</li>
                                    <li style="margin-bottom: 8px;">Choose <strong>Offline conversions</strong> as the goal type.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 1 (Qualification):</strong> Name the goal <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Qualified Lead</code>. Choose <strong>Lead</strong> as the goal category.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 2 (Offline Sale):</strong> Create another goal. Name it <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Offline Sale</code>, category as <strong>Purchase/Sale</strong>, and select <strong>Each time it happens, the conversion value may vary</strong>.</li>
                                </ol>
                            </div>
                        `);
                    }}
                    if (chatgptAds) {{
                        blocks.push(`
                            <div style="background: #fdfdfd; border: 1px solid #e0e0e0; border-left: 4px solid #10a37f; padding: 15px; border-radius: 4px; margin-bottom: 20px;">
                                <h4 style="margin-top: 0; color: #10a37f; display: flex; align-items: center; gap: 8px; font-size: 14px;">
                                     ChatGPT Ads Goal Setup (ID: ${chatgptAds})
                                </h4>
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; margin: 0; color: #333;">
                                    <li style="margin-bottom: 8px;">Log into your <strong>ChatGPT Ads Campaign Manager</strong>.</li>
                                    <li style="margin-bottom: 8px;">Go to <strong>Conversion Event Manager</strong> and click <strong>Create Custom Conversion Goal</strong>.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 1 (Qualification):</strong> Name the custom offline event <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Qualified Lead</code>. Under action, select <strong>Lead</strong>, and set tracing to utilize secure CSV match.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 2 (Offline Sale):</strong> Create another custom event. Name it <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Offline Sale</code>, choose event type <strong>Purchase</strong>, and ensure dynamic revenue mapping is selected.</li>
                                </ol>
                            </div>
                        `);
                    }}

                    
                    if (tiktokAds) {{
                        blocks.push(`
                            <div style="background: #fdfdfd; border: 1px solid #e0e0e0; border-left: 4px solid #010101; padding: 15px; border-radius: 4px; margin-bottom: 20px;">
                                <h4 style="margin-top: 0; color: #010101; display: flex; align-items: center; gap: 8px; font-size: 14px;">
                                     TikTok Ads Goal Setup (Pixel: ${tiktokAds})
                                </h4>
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; margin: 0; color: #333;">
                                    <li style="margin-bottom: 8px;">Log into your <strong>TikTok Ads Manager</strong>.</li>
                                    <li style="margin-bottom: 8px;">Go to <strong>Tools ➡️ Events ➡️ Offline Events</strong>.</li>
                                    <li style="margin-bottom: 8px;">Create a new Offline Event Set, and define conversion rules:
                                        <ul style="list-style-type: disc; padding-left: 15px; margin-top: 4px;">
                                            <li><strong>Goal 1 (Qualification):</strong> Name it <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Qualified Lead</code>, mapped to event category <strong>Contact</strong>.</li>
                                            <li><strong>Goal 2 (Offline Sale):</strong> Name it <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Offline Sale</code>, mapped to event category <strong>CompletePayment</strong>.</li>
                                        </ul>
                                    </li>
                                </ol>
                            </div>
                        `);
                    }}
                    if (chatgptAds) {{
                        blocks.push(`
                            <div style="background: #fdfdfd; border: 1px solid #e0e0e0; border-left: 4px solid #10a37f; padding: 15px; border-radius: 4px; margin-bottom: 20px;">
                                <h4 style="margin-top: 0; color: #10a37f; display: flex; align-items: center; gap: 8px; font-size: 14px;">
                                     ChatGPT Ads Goal Setup (ID: ${chatgptAds})
                                </h4>
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; margin: 0; color: #333;">
                                    <li style="margin-bottom: 8px;">Log into your <strong>ChatGPT Ads Campaign Manager</strong>.</li>
                                    <li style="margin-bottom: 8px;">Go to <strong>Conversion Event Manager</strong> and click <strong>Create Custom Conversion Goal</strong>.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 1 (Qualification):</strong> Name the custom offline event <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Qualified Lead</code>. Under action, select <strong>Lead</strong>, and set tracing to utilize secure CSV match.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 2 (Offline Sale):</strong> Create another custom event. Name it <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Offline Sale</code>, choose event type <strong>Purchase</strong>, and ensure dynamic revenue mapping is selected.</li>
                                </ol>
                            </div>
                        `);
                    }}

                    
                    if (twitterAds) {{
                        blocks.push(`
                            <div style="background: #fdfdfd; border: 1px solid #e0e0e0; border-left: 4px solid #15202B; padding: 15px; border-radius: 4px; margin-bottom: 20px;">
                                <h4 style="margin-top: 0; color: #15202B; display: flex; align-items: center; gap: 8px; font-size: 14px;">
                                     X (Twitter) Ads Goal Setup (Pixel: ${twitterAds})
                                </h4>
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; margin: 0; color: #333;">
                                    <li style="margin-bottom: 8px;">Log into your <strong>X Ads Manager</strong>.</li>
                                    <li style="margin-bottom: 8px;">Go to <strong>Tools ➡️ Events Manager</strong> and click <strong>Add Event</strong>.</li>
                                    <li style="margin-bottom: 8px;">Select <strong>Offline</strong> as the conversion tracking type:
                                        <ul style="list-style-type: disc; padding-left: 15px; margin-top: 4px;">
                                            <li><strong>Goal 1 (Qualification):</strong> Name the event <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-size: 12px; font-family: monospace;">LeadGroove Qualified Lead</code>, event type <strong>Lead</strong>.</li>
                                            <li><strong>Goal 2 (Offline Sale):</strong> Name the event <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-size: 12px; font-family: monospace;">LeadGroove Offline Sale</code>, event type <strong>Purchase</strong>.</li>
                                        </ul>
                                    </li>
                                </ol>
                            </div>
                        `);
                    }}
                    if (chatgptAds) {{
                        blocks.push(`
                            <div style="background: #fdfdfd; border: 1px solid #e0e0e0; border-left: 4px solid #10a37f; padding: 15px; border-radius: 4px; margin-bottom: 20px;">
                                <h4 style="margin-top: 0; color: #10a37f; display: flex; align-items: center; gap: 8px; font-size: 14px;">
                                     ChatGPT Ads Goal Setup (ID: ${chatgptAds})
                                </h4>
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; margin: 0; color: #333;">
                                    <li style="margin-bottom: 8px;">Log into your <strong>ChatGPT Ads Campaign Manager</strong>.</li>
                                    <li style="margin-bottom: 8px;">Go to <strong>Conversion Event Manager</strong> and click <strong>Create Custom Conversion Goal</strong>.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 1 (Qualification):</strong> Name the custom offline event <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Qualified Lead</code>. Under action, select <strong>Lead</strong>, and set tracing to utilize secure CSV match.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 2 (Offline Sale):</strong> Create another custom event. Name it <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Offline Sale</code>, choose event type <strong>Purchase</strong>, and ensure dynamic revenue mapping is selected.</li>
                                </ol>
                            </div>
                        `);
                    }}

                    
                    if (snapchatAds) {{
                        blocks.push(`
                            <div style="background: #fdfdfd; border: 1px solid #e0e0e0; border-left: 4px solid #FFFC00; padding: 15px; border-radius: 4px; margin-bottom: 20px;">
                                <h4 style="margin-top: 0; color: #000; background: #FFFC00; display: inline-block; padding: 2px 6px; border-radius: 3px; font-size: 14px;">
                                     Snapchat Ads Goal Setup (ID: ${snapchatAds})
                                </h4>
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; margin: 0; color: #333;">
                                    <li style="margin-bottom: 8px;">Log into your <strong>Snapchat Ads Manager</strong>.</li>
                                    <li style="margin-bottom: 8px;">Go to <strong>Assets ➡️ Events Manager</strong>.</li>
                                    <li style="margin-bottom: 8px;">Select your Active Web Pixel and click <strong>Create Custom Goal</strong> or <strong>Offline Conversion Event</strong>.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 1 (Qualification):</strong> Name your Custom Offline Event <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Qualified Lead</code>. Set category to <strong>SIGN_UP</strong> or <strong>PAGE_VIEW</strong>.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 2 (Offline Sale):</strong> Create another custom event. Name it <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Offline Sale</code>. Set event category to <strong>PURCHASE</strong> and enable dynamic revenue tracking.</li>
                                </ol>
                            </div>
                        `);
                    }}
                    if (pinterestAds) {{
                        blocks.push(`
                            <div style="background: #fdfdfd; border: 1px solid #e0e0e0; border-left: 4px solid #E60023; padding: 15px; border-radius: 4px; margin-bottom: 20px;">
                                <h4 style="margin-top: 0; color: #E60023; display: flex; align-items: center; gap: 8px; font-size: 14px;">
                                     Pinterest Ads Goal Setup (Tag: ${pinterestAds})
                                </h4>
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; margin: 0; color: #333;">
                                    <li style="margin-bottom: 8px;">Log into your <strong>Pinterest Ads Manager</strong>.</li>
                                    <li style="margin-bottom: 8px;">Go to <strong>Ads ➡️ Conversions</strong> and select <strong>Offline conversions</strong>.</li>
                                    <li style="margin-bottom: 8px;">Click <strong>Create conversion event</strong>:
                                        <ul style="list-style-type: disc; padding-left: 15px; margin-top: 4px;">
                                            <li><strong>Goal 1 (Qualification):</strong> Set event name to <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Qualified Lead</code>, category <strong>Lead</strong>.</li>
                                            <li><strong>Goal 2 (Offline Sale):</strong> Set event name to <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Offline Sale</code>, category <strong>Checkout</strong>.</li>
                                        </ul>
                                    </li>
                                </ol>
                            </div>
                        `);
                    }}
                    if (chatgptAds) {{
                        blocks.push(`
                            <div style="background: #fdfdfd; border: 1px solid #e0e0e0; border-left: 4px solid #10a37f; padding: 15px; border-radius: 4px; margin-bottom: 20px;">
                                <h4 style="margin-top: 0; color: #10a37f; display: flex; align-items: center; gap: 8px; font-size: 14px;">
                                     ChatGPT Ads Goal Setup (ID: ${chatgptAds})
                                </h4>
                                <ol style="padding-left: 20px; font-size: 13px; line-height: 1.6; margin: 0; color: #333;">
                                    <li style="margin-bottom: 8px;">Log into your <strong>ChatGPT Ads Campaign Manager</strong>.</li>
                                    <li style="margin-bottom: 8px;">Go to <strong>Conversion Event Manager</strong> and click <strong>Create Custom Conversion Goal</strong>.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 1 (Qualification):</strong> Name the custom offline event <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Qualified Lead</code>. Under action, select <strong>Lead</strong>, and set tracing to utilize secure CSV match.</li>
                                    <li style="margin-bottom: 8px;"><strong>Goal 2 (Offline Sale):</strong> Create another custom event. Name it <code style="background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-family: monospace;">LeadGroove Offline Sale</code>, choose event type <strong>Purchase</strong>, and ensure dynamic revenue mapping is selected.</li>
                                </ol>
                            </div>
                        `);
                    }}

                    
                    if (blocks.length === 0) {{
                        instructionsContainer.innerHTML = `
                            <div style="text-align: center; color: #666; padding: 30px; border: 1px dashed #ccc; border-radius: 6px; background: #fafafa;">
                                <span style="font-size: 24px; display: block; margin-bottom: 10px;">ℹ️</span>
                                No advertising account IDs were entered in Step 1. If you added them later, please configure custom conversions on your ad networks matching the names:
                                <br><br>
                                <code style="background: #f1f3f4; padding: 4px 10px; border-radius: 3px; font-weight: bold; font-size: 14px; font-family: monospace; color: #1a237e;">LeadGroove Qualified Lead</code>
                                <br><span style="font-size: 11px; color: #888;">and</span><br>
                                <code style="background: #f1f3f4; padding: 4px 10px; border-radius: 3px; font-weight: bold; font-size: 14px; font-family: monospace; color: #1a237e;">LeadGroove Offline Sale</code>
                            </div>
                        `;
                    }} else {{
                        instructionsContainer.innerHTML = blocks.join('');
                    }}
                }}

                
                function toggleVoipInstructions() {
                    const voipSelect = document.getElementById('voip_provider');
                    if (!voipSelect) return;
                    const voip = voipSelect.value;
                    const cards = document.querySelectorAll('.voip-inst-card');
                    cards.forEach(card => { card.style.display = 'none'; });
                    const activeCard = document.getElementById('voip-inst-' + voip);
                    if (activeCard) { 
                        activeCard.style.display = 'block'; 
                        const origin = window.location.origin || '';
                        const inputs = activeCard.querySelectorAll('.webhook-input');
                        inputs.forEach(input => {
                            const suffix = input.getAttribute('data-suffix');
                            if (suffix) {
                                input.value = origin + suffix;
                            }
                        });
                    }
                }

                function toggleSOTFields() {
                    const sotSelect = document.getElementById('source_of_truth');
                    if (!sotSelect) return;
                    const sot = sotSelect.value;
                    
                    const dealBox = document.getElementById('sot-deal-tags-box');
                    const leadBox = document.getElementById('sot-lead-tags-box');
                    const emailBox = document.getElementById('sot-email-box');
                    const voipBox = document.getElementById('sot-voip-box');
                    
                    const hsBox = document.getElementById('sot-hubspot-instructions-box');
                    const salesforceBox = document.getElementById('sot-salesforce-instructions-box');
                    const zohoBox = document.getElementById('sot-zoho-instructions-box');
                    const servicetitanBox = document.getElementById('sot-servicetitan-instructions-box');
                    const housecallproBox = document.getElementById('sot-housecallpro-instructions-box');
                    const ghlBox = document.getElementById('sot-gohighlevel-instructions-box');
                    
                    const quickbooksBox = document.getElementById('sot-quickbooks-instructions-box');
                    const xeroBox = document.getElementById('sot-xero-instructions-box');
                    const zohoBooksBox = document.getElementById('sot-zoho_books-instructions-box');
                    const netsuiteBox = document.getElementById('sot-netsuite-instructions-box');
                    const sageBox = document.getElementById('sot-sage-instructions-box');
                    const freshbooksBox = document.getElementById('sot-freshbooks-instructions-box');
                    const googleSheetsBox = document.getElementById('sot-google_sheets-instructions-box');
                    const zapierBox = document.getElementById('sot-zapier-instructions-box');
                    const monthlyEmailBox = document.getElementById('sot-monthly-email-instructions-box');
                    
                    if (dealBox) dealBox.style.display = 'none';
                    const valGroup = document.getElementById('sot-value-tags-group');
                    if (valGroup) valGroup.style.display = 'none';
                    if (leadBox) leadBox.style.display = 'none';
                    if (emailBox) emailBox.style.display = 'none';
                    if (voipBox) voipBox.style.display = 'none';
                    
                    if (hsBox) hsBox.style.display = 'none';
                    if (salesforceBox) salesforceBox.style.display = 'none';
                    if (zohoBox) zohoBox.style.display = 'none';
                    if (servicetitanBox) servicetitanBox.style.display = 'none';
                    if (housecallproBox) housecallproBox.style.display = 'none';
                    if (ghlBox) ghlBox.style.display = 'none';
                    
                    if (quickbooksBox) quickbooksBox.style.display = 'none';
                    if (xeroBox) xeroBox.style.display = 'none';
                    if (zohoBooksBox) zohoBooksBox.style.display = 'none';
                    if (netsuiteBox) netsuiteBox.style.display = 'none';
                    if (sageBox) sageBox.style.display = 'none';
                    if (freshbooksBox) freshbooksBox.style.display = 'none';
                    if (googleSheetsBox) googleSheetsBox.style.display = 'none';
                    if (zapierBox) zapierBox.style.display = 'none';
                    if (monthlyEmailBox) monthlyEmailBox.style.display = 'none';
                    
                    if (['hubspot', 'salesforce', 'zoho', 'servicetitan', 'housecallpro', 'gohighlevel'].includes(sot)) {
                        if (['hubspot', 'salesforce', 'zoho', 'gohighlevel', 'google_sheets', 'email', 'zapier'].includes(sot)) {
                            if (dealBox) dealBox.style.display = 'block';
                        }
                        if (['servicetitan', 'housecallpro', 'gohighlevel'].includes(sot)) {
                            if (leadBox) leadBox.style.display = 'block';
                        }
                        if (sot === 'hubspot' && hsBox) hsBox.style.display = 'block';
                        else if (sot === 'salesforce' && salesforceBox) salesforceBox.style.display = 'block';
                        else if (sot === 'zoho' && zohoBox) zohoBox.style.display = 'block';
                        else if (sot === 'servicetitan' && servicetitanBox) servicetitanBox.style.display = 'block';
                        else if (sot === 'housecallpro' && housecallproBox) housecallproBox.style.display = 'block';
                        else if (sot === 'gohighlevel' && ghlBox) ghlBox.style.display = 'block';
                    } else if (['quickbooks', 'xero', 'zoho_books', 'netsuite', 'sage', 'freshbooks', 'google_sheets', 'zapier'].includes(sot)) {
                        if (sot === 'quickbooks' && quickbooksBox) quickbooksBox.style.display = 'block';
                        else if (sot === 'xero' && xeroBox) xeroBox.style.display = 'block';
                        else if (sot === 'zoho_books' && zohoBooksBox) zohoBooksBox.style.display = 'block';
                        else if (sot === 'netsuite' && netsuiteBox) netsuiteBox.style.display = 'block';
                        else if (sot === 'sage' && sageBox) sageBox.style.display = 'block';
                        else if (sot === 'freshbooks' && freshbooksBox) freshbooksBox.style.display = 'block';
                        else if (sot === 'google_sheets' && googleSheetsBox) { googleSheetsBox.style.display = 'block'; if (dealBox) dealBox.style.display = 'block'; }
                        else if (sot === 'zapier' && zapierBox) { zapierBox.style.display = 'block'; if (dealBox) dealBox.style.display = 'block'; }
                    } else if (sot === 'email') {
                        if (monthlyEmailBox) monthlyEmailBox.style.display = 'block';
                        if (dealBox) dealBox.style.display = 'block';
                    } else if (sot === 'ai_rating') {
                        if (emailBox) emailBox.style.display = 'block';
                        if (voipBox) {
                            voipBox.style.display = 'block';
                            toggleVoipInstructions();
                        }
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
                    let sotUrlGroup = null;

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
                    
                    const actionRadio = document.querySelector('input[name="exclusion_upload_action"]:checked');
                    const exclusionActionValue = actionRadio ? actionRadio.value : 'append';
                    
                    const payload = {
                        name: document.getElementById('name').value.trim(),
                        excluded_customers: parsedExclusions,
                        exclusion_action: exclusionActionValue,
                        call_tracking_provider: document.getElementById('call_tracking_provider').value,
                        callrail_account_id: document.getElementById('callrail_account_id').value.trim(),
                        callrail_company_id: document.getElementById('callrail_company_id').value.trim(),
                        ctm_account_id: document.getElementById('ctm_account_id').value.trim(),
                        ctm_profile_id: document.getElementById('ctm_profile_id').value.trim(),
                        wc_account_id: document.getElementById('wc_account_id').value.trim(),
                        wc_profile_id: document.getElementById('wc_profile_id').value.trim(),
                        google_ads_customer_id: document.getElementById('google_ads_customer_id').value.trim(),
                        facebook_ads_id: document.getElementById('facebook_ads_id').value.trim(),
                        linkedin_ads_id: document.getElementById('linkedin_ads_id').value.trim(),
                        microsoft_ads_id: document.getElementById('microsoft_ads_id').value.trim(),
                        tiktok_ads_id: document.getElementById('tiktok_ads_id').value.trim(),
                        twitter_ads_id: document.getElementById('twitter_ads_id').value.trim(),
                        pinterest_ads_id: document.getElementById('pinterest_ads_id').value.trim(),
                        snapchat_ads_id: document.getElementById('snapchat_ads_id').value.trim(),
                        chatgpt_ads_id: document.getElementById('chatgpt_ads_id').value.trim(),
                        reddit_ads_id: document.getElementById('reddit_ads_id').value.trim(),
                        lead_gen_method: document.querySelector('input[name="lead_gen_method"]:checked').value,
                        qualification_criteria: document.getElementById('qualification_criteria').value,
                        source_of_truth: document.getElementById('source_of_truth').value,
                        email_provider: document.getElementById('email_provider').value,
                        email_account: document.getElementById('email_account').value.trim(),
                        email_app_password: document.getElementById('email_app_password') ? document.getElementById('email_app_password').value.trim() : '',
                        email_account_2: document.getElementById('email_account_2') ? document.getElementById('email_account_2').value.trim() : '',
                        email_app_password_2: document.getElementById('email_app_password_2') ? document.getElementById('email_app_password_2').value.trim() : '',
                        email_account_3: document.getElementById('email_account_3') ? document.getElementById('email_account_3').value.trim() : '',
                        email_app_password_3: document.getElementById('email_app_password_3') ? document.getElementById('email_app_password_3').value.trim() : '',
                        email_account_4: document.getElementById('email_account_4') ? document.getElementById('email_account_4').value.trim() : '',
                        email_app_password_4: document.getElementById('email_app_password_4') ? document.getElementById('email_app_password_4').value.trim() : '',
                        email_account_5: document.getElementById('email_account_5') ? document.getElementById('email_account_5').value.trim() : '',
                        email_app_password_5: document.getElementById('email_app_password_5') ? document.getElementById('email_app_password_5').value.trim() : '',
                        crm_deal_tags: document.getElementById('crm_deal_tags').value.trim(),
                        crm_won_deal_tags: document.getElementById('crm_won_deal_tags').value.trim(),
                        crm_value_field: document.getElementById('crm_value_field') ? document.getElementById('crm_value_field').value.trim() : '',
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
                            let liveWebhook = `${window.location.origin}/webhooks/callrail?client_id=${data.client_id}`;
                            let providerName = "CallRail";
                            let providerInstructions = `
                                <strong>CallRail Setup Checklist (Inbound & Outbound Call Recording & Transcripts):</strong><br>
                                <ol style="margin: 6px 0 0 0; padding-left: 18px; line-height: 1.6; font-size: 11px;">
                                    <li>Log into <strong>CallRail</strong> and go to <strong>Settings ➡️ Company / Numbers ➡️ Call Recording & Transcripts</strong>.</li>
                                    <li>Ensure both <strong>Inbound & Outbound Call Recording</strong> and <strong>Speech-to-Text Transcripts</strong> are toggled <strong>ON</strong> in English for all target tracking numbers.</li>
                                    <li>Go to <strong>Integrations ➡️ Webhooks</strong>, paste your dynamic target URL (above), and set the trigger event to <strong>"Call Completed"</strong> so complete call recordings and transcripts are compiled and sent to LeadGrove automatically!</li>
                                </ol>
                            `;
                            if (payload.call_tracking_provider === 'calltrackingmetrics') {
                                liveWebhook = `${window.location.origin}/webhooks/calltrackingmetrics?client_id=${data.client_id}`;
                                providerName = "CallTrackingMetrics";
                                providerInstructions = `
                                    <strong>CallTrackingMetrics Setup Checklist (Inbound & Outbound Call Recording & Transcripts):</strong><br>
                                    <ol style="margin: 6px 0 0 0; padding-left: 18px; line-height: 1.6; font-size: 11px;">
                                        <li>Log into <strong>CallTrackingMetrics</strong> and navigate to <strong>Numbers ➡️ Call Settings / Account Settings</strong>.</li>
                                        <li>Turn on <strong>Call Recording</strong> and enable <strong>Speech-to-Text / Automated Transcriptions</strong> for both inbound and outbound calls.</li>
                                        <li>Go to <strong>Settings ➡️ Webhooks</strong>, paste your dynamic target URL (above), and set the trigger to fire when <strong>Call / Transcription is Completed</strong> so full logs are delivered to LeadGrove.</li>
                                    </ol>
                                `;
                            } else if (payload.call_tracking_provider === 'whatconverts') {
                                liveWebhook = `${window.location.origin}/webhooks/whatconverts?client_id=${data.client_id}`;
                                providerName = "WhatConverts";
                                providerInstructions = `
                                    <strong>WhatConverts Setup Checklist (Inbound & Outbound Call Recording & Transcripts):</strong><br>
                                    <ol style="margin: 6px 0 0 0; padding-left: 18px; line-height: 1.6; font-size: 11px;">
                                        <li>Log into <strong>WhatConverts</strong> and navigate to <strong>Tracking ➡️ Phone Calls / Call Settings</strong>.</li>
                                        <li>Ensure <strong>Call Recording</strong> and <strong>Call Transcriptions</strong> are toggled <strong>ON</strong> for both inbound and outbound call flows.</li>
                                        <li>Go to <strong>Integrations ➡️ Webhooks</strong>, paste your dynamic target URL (above), and create a trigger for <strong>Phone Calls</strong> with <strong>Transcriptions</strong> enabled.</li>
                                    </ol>
                                `;
                            }
                            document.getElementById('webhook-url-input').value = liveWebhook;
                            document.getElementById('call-tracking-provider-title').innerHTML = `Step 2: Configure ${providerName} Integration`;
                            document.getElementById('call-tracking-provider-span').innerText = providerName;
                            document.getElementById('call-tracking-instructions-reminder').innerHTML = `⚠️ ${providerInstructions}`;
                            
                                                        // CRM / Billing / Email custom success steps
                            const sotBox = document.getElementById('sot-instructions-box');
                            const sotLabel = document.getElementById('sot-instructions-label');
                            const sotUrlInput = document.getElementById('sot-webhook-input');
                            const sotEmailBox = document.getElementById('sot-email-instructions-box');
                            const sotEmailAddress = document.getElementById('sot-email-address');
                            
                            sotBox.style.display = 'none';
                            sotEmailBox.style.display = 'none';
                            
                            // Real-Time Exclusions Webhook Step
                            const exclusionBox = document.getElementById('exclusion-instructions-box');
                            const exclusionUrlInput = document.getElementById('exclusion-webhook-url-input');
                            exclusionBox.style.display = 'none';
                            
                            if (payload.exclude_past_customers === 'YES') {
                                exclusionUrlInput.value = `${window.location.origin}/webhooks/exclude-customer?client_id=${data.client_id}`;
                                exclusionBox.style.display = 'block';
                            }
                            
                            if (payload.source_of_truth === 'ai_rating') {
                                sotLabel.innerHTML = `⚡ <strong>Step 3: Direct AI Call Auditing Active!</strong><br>Since your Single Source of Truth is set to <strong>AI Rating</strong>, we have automatically fetched and audited your client's past 90 days CallRail history! Proceed directly to the dashboard to inspect their conversion upload sheets.`;
                                // Set dummy or instructions for URL input, or just hide the input block
                                sotUrlGroup = document.getElementById('sot-webhook-input') ? document.getElementById('sot-webhook-input').parentNode : null;
                                if (sotUrlGroup) sotUrlGroup.style.display = 'none';
                                sotBox.style.display = 'block';
                                
                                // Auto-forwarding instruction if they configure email verification under AI Rating mode
                                if ((payload.lead_gen_method === 'both' || payload.lead_gen_method === 'form') && payload.email_account) {
                                    const wHost = window.location.host;
                                    const wEmailDomain = wHost.includes('localhost') ? 'your-agency.com' : wHost.replace('www.', '').split(':')[0];
                                    sotEmailAddress.value = `conversions-${data.client_id}@${wEmailDomain}`;
                                    sotEmailBox.style.display = 'block';
                                }
                            } else if (['hubspot', 'salesforce', 'zoho', 'servicetitan', 'housecallpro', 'gohighlevel'].includes(payload.source_of_truth)) {
                                sotUrlGroup = document.getElementById('sot-webhook-input') ? document.getElementById('sot-webhook-input').parentNode : null;
                                if (sotUrlGroup) sotUrlGroup.style.display = 'flex';
                                sotLabel.innerHTML = `⚙️ <strong>Step 3: Connect Your ${payload.source_of_truth.toUpperCase()} CRM Webhook</strong><br>Copy this webhook URL and paste it into your CRM's Developer Settings or configure it in Zapier to trigger when a Lead or Deal is updated:`;
                                sotUrlInput.value = `${window.location.origin}/webhooks/crm?client_id=${data.client_id}`;
                                sotBox.style.display = 'block';
                            } else if (['quickbooks', 'xero', 'zoho_books', 'netsuite', 'sage', 'freshbooks', 'google_sheets', 'zapier'].includes(payload.source_of_truth)) {
                                const displayName = payload.source_of_truth.replace('_', ' ').toUpperCase();
                                sotLabel.innerHTML = ` <strong>Step 3: Connect Your ${displayName} Integration Webhook</strong><br>Copy this webhook URL and paste it into your developer or integration settings console to sync transactions instantly:`;
                                sotUrlInput.value = `${window.location.origin}/webhooks/billing?client_id=${data.client_id}`;
                                sotBox.style.display = 'block';
                            } else if (payload.source_of_truth === 'email') {
                                const host = window.location.host;
                                const emailDomain = host.includes('localhost') ? 'your-agency.com' : host.replace('www.', '').split(':')[0];
                                sotEmailAddress.value = `conversions-${data.client_id}@${emailDomain}`;
                                sotEmailBox.style.display = 'block';
                            }
                            
                            successScreen.style.display = 'block';
                        } else {
                            throw new Error(data.detail || 'An unexpected database error occurred.');
                        }
                    } catch (error) {
                        showErrorAlert('Error: ' + error.message);
                        nextBtn.disabled = false;
                        nextBtn.innerText = ' Complete Onboarding';
                    }
                }
                
                function copyWebhookUrl(inputId, btnId) {
                    const copyText = document.getElementById(inputId);
                    copyText.select();
                    copyText.setSelectionRange(0, 99999);
                    navigator.clipboard.writeText(copyText.value);
                    
                    const copyBtn = document.getElementById(btnId);
                    let originalText = " Copy URL";
                    if (inputId === "sot-email-address") {
                        originalText = " Copy Email";
                    } else if (inputId === "exclusion-webhook-url-input") {
                        originalText = " Copy Webhook";
                    }
                    copyBtn.innerText = "✅ Copied!";
                    copyBtn.style.backgroundColor = "#1b5e20";
                    setTimeout(() => {
                        copyBtn.innerText = originalText;
                        copyBtn.style.backgroundColor = "#2e7d32";
                    }, 2000);
                }
            </script>
        </body>
    </html>
    """
    html_content = html_content.replace('<body>\n            <div class="container">', f'<body>\n            <div class="container">\n                {user_header_bar}')
    html_content = html_content.replace("conversions-[id]", f"conversions-{next_id}")
    return HTMLResponse(html_content)



from datetime import datetime, timedelta

def backfill_historical_callrail_leads(client_id: int, qualification_criteria_code: str, provider: str = "callrail"):
    """
    Simulates fetching the last 90 days of CallRail data for a client,
    filters for leads with matching ad click IDs (Google, Microsoft, LinkedIn, Facebook),
    runs Claude AI audits on them, and saves them to the sessions database.
    """
    qualification_definition_desc = CRITERIA_MAP.get(qualification_criteria_code, "Someone who expresses real intent to buy or schedule a service.")
    
    now = datetime.now()
    historical_leads = [
        {
            "name": "David Fletcher",
            "phone": "14155550231",
            "gclid": "gclid_historical_google_77a",
            "fbclid": "",
            "li_fat_id": "",
            "msclkid": "",
            "transcript": (
                "[00:05] Agent: Thanks for calling, this is solar services consulting. How can I help you?\n"
                "[00:11] Caller: Yes, I saw your Google Ad for residential solar. I want to book an appointment to get an estimate.\n"
                "[00:18] Agent: Great, I can schedule a site surveyor to come out this Thursday at 2 PM. Does that work?\n"
                "[00:25] Caller: Yes, that is perfect. Sign me up!\n"
            ),
            "days_ago": 12,
            "sim_results": {
                "qualified": "YES",
                "sale_closed": "NO",
                "value": 0.0,
                "reason": "Caller booked a solar consultation estimate after seeing a Google ad."
            }
        },
        {
            "name": "Amanda Sterling",
            "phone": "12065550148",
            "gclid": "",
            "fbclid": "",
            "li_fat_id": "",
            "msclkid": "msclkid_historical_msft_88b",
            "transcript": (
                "[00:04] Agent: Heating and cooling diagnostics, how can we help?\n"
                "[00:09] Caller: Hi, my furnace is making a loud noise. I saw your Bing ad and wanted to schedule a repair.\n"
                "[00:16] Agent: Okay, our standard diagnostic call is $99. Can we book you for today at 4 PM?\n"
                "[00:23] Caller: Yes, absolutely, please send someone over. I am ready to pay the diagnostic fee.\n"
            ),
            "days_ago": 28,
            "sim_results": {
                "qualified": "YES",
                "sale_closed": "YES",
                "value": 99.0,
                "reason": "Caller scheduled furnace diagnostic visit and agreed to the $99 service fee."
            }
        },
        {
            "name": "Robert Chen",
            "phone": "12135550199",
            "gclid": "",
            "fbclid": "fbclid_historical_meta_22f",
            "li_fat_id": "",
            "msclkid": "",
            "transcript": (
                "[00:05] Agent: Elite Dental Care. How can I help you?\n"
                "[00:11] Caller: Hi, I saw your dental implant special on Facebook for $1,200. Is that still available?\n"
                "[00:18] Agent: Yes, it is! We can book you for an initial consultation on Monday.\n"
                "[00:24] Caller: Great, let's do it, I want to get the implants started.\n"
            ),
            "days_ago": 45,
            "sim_results": {
                "qualified": "YES",
                "sale_closed": "NO",
                "value": 0.0,
                "reason": "Lead is highly qualified, inquiring specifically about the $1,200 implant offer on Meta."
            }
        },
        {
            "name": "Jessica Thompson",
            "phone": "16505550921",
            "gclid": "",
            "fbclid": "",
            "li_fat_id": "li_fat_id_historical_linkedin_44d",
            "msclkid": "",
            "transcript": (
                "[00:05] Agent: Commercial Valving Services. This is Mark.\n"
                "[00:11] Caller: Hi, I saw your LinkedIn ad regarding industrial valving solutions. We need three heavy-duty water valves replaced at our facility.\n"
                "[00:20] Agent: We can definitely help. Let me send our senior technician out for a site survey and formal bid.\n"
                "[00:28] Caller: Excellent. Looking forward to the proposal.\n"
            ),
            "days_ago": 68,
            "sim_results": {
                "qualified": "YES",
                "sale_closed": "NO",
                "value": 0.0,
                "reason": "Commercial B2B lead from LinkedIn looking for commercial water valve replacements."
            }
        },
        {
            "name": "Nancy Wheeler",
            "phone": "13125550212",
            "gclid": "",
            "fbclid": "",
            "li_fat_id": "",
            "msclkid": "",
            "transcript": (
                "[00:04] Agent: Local Services. Caller: Hi, my kitchen sink is leaking. Agent: We can have someone over. Caller: Actually my husband just fixed it himself, sorry to bother you."
            ),
            "days_ago": 80,
            "sim_results": {
                "qualified": "NO",
                "sale_closed": "NO",
                "value": 0.0,
                "reason": "Caller's husband fixed the leak himself; call cancelled."
            }
        }
    ]
    
    conn = db_router.connect()
    cursor = conn.cursor()
    
    for lead in historical_leads:
        gclid = lead["gclid"]
        fbclid = lead["fbclid"]
        li_fat_id = lead["li_fat_id"]
        msclkid = lead["msclkid"]
        
        has_click_id = any([gclid, fbclid, li_fat_id, msclkid])
        created_at_time = (now - timedelta(days=lead["days_ago"])).strftime("%Y-%m-%d %H:%M:%S")
        normalized_phone = normalize_phone(lead["phone"])
        
        # Determine ratings
        if has_click_id:
            if client:
                try:
                    # Live audit if key is active
                    ai_result = analyze_transcript_with_claude(lead["transcript"], qualification_definition_desc)
                    qualified = ai_result.get("qualified", "NO")
                    sale_closed = ai_result.get("sale_closed", "NO")
                    value = float(ai_result.get("value", 0.0))
                    reason = ai_result.get("reason", "No reason parsed.")
                    model_used = "claude-haiku-4-5-20251001"
                except Exception as e:
                    qualified = lead["sim_results"]["qualified"]
                    sale_closed = lead["sim_results"]["sale_closed"]
                    value = lead["sim_results"]["value"]
                    reason = f"Simulated Audit (Claude live failed: {e}): {lead['sim_results']['reason']}"
                    model_used = "claude-haiku-4-5-20251001 (Simulated)"
            else:
                # Simulated Claude audit
                qualified = lead["sim_results"]["qualified"]
                sale_closed = lead["sim_results"]["sale_closed"]
                value = lead["sim_results"]["value"]
                reason = f"Simulated Claude Audit: {lead['sim_results']['reason']}"
                model_used = "claude-haiku-4-5-20251001 (Simulated)"
        else:
            qualified = "NO"
            sale_closed = "NO"
            value = 0.0
            reason = "Ignored: Direct or organic search lead (no ad click ID detected)."
            model_used = "None"
            
        raw_data_json = json.dumps({
            "customer_name": lead["name"],
            "customer_phone_number": lead["phone"],
            "transcript_snippet": lead["transcript"][:150] + "..." if not lead["gclid"] else lead["transcript"]
        })
        
        cursor.execute("""
            INSERT INTO sessions (
                client_id, phone, name, gclid, fbclid, li_fat_id, msclkid, ttclid, twclid, pin_clid, gptclid, rdt_cid, source, qualified, sale_closed, value, reason, model_used, raw_data, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            client_id,
            normalized_phone,
            lead["name"],
            gclid or None,
            fbclid or None,
            li_fat_id or None,
            msclkid or None,
            None, # ttclid
            None, # twclid
            None, # pin_clid
            None, # gptclid
            provider,
            qualified,
            sale_closed,
            value,
            reason,
            model_used,
            raw_data_json,
            created_at_time
        ))
        
    conn.commit()
    conn.close()
    print(f"✅ Seseeded and audited {len(historical_leads)} historical 90 days {provider} leads for client #{client_id}")

@router.post("/dashboard/add-client")
def create_client(request: Request, client: ClientCreate):
    email = is_authenticated(request)
    if not email:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    user_role, user_client_id = get_user_role_and_client(email)
    if user_role != "full" or user_client_id is not None:
        raise HTTPException(status_code=403, detail="Unauthorized: Client onboarding is restricted to Agency Administrators.")
    """Endpoint to handle questionnaire form submission."""
    if not client.name or not client.name.strip():
        raise HTTPException(status_code=400, detail="Client Business Name is required.")
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        
        # Verify unique Call Tracking Provider ID
        prov = client.call_tracking_provider or "callrail"
        if prov == "callrail":
            if client.callrail_company_id:
                cursor.execute("SELECT id, name FROM clients WHERE callrail_company_id = ?", (client.callrail_company_id,))
                existing = cursor.fetchone()
                if existing:
                    raise HTTPException(status_code=400, detail=f"CallRail Company ID '{client.callrail_company_id}' is already registered to client '{existing[1]}'.")
        elif prov == "calltrackingmetrics":
            if client.ctm_profile_id:
                cursor.execute("SELECT id, name FROM clients WHERE ctm_profile_id = ?", (client.ctm_profile_id,))
                existing = cursor.fetchone()
                if existing:
                    raise HTTPException(status_code=400, detail=f"CallTrackingMetrics Profile ID '{client.ctm_profile_id}' is already registered to client '{existing[1]}'.")
        elif prov == "whatconverts":
            if client.wc_profile_id:
                cursor.execute("SELECT id, name FROM clients WHERE wc_profile_id = ?", (client.wc_profile_id,))
                existing = cursor.fetchone()
                if existing:
                    raise HTTPException(status_code=400, detail=f"WhatConverts Profile ID '{client.wc_profile_id}' is already registered to client '{existing[1]}'.")
            
        cursor.execute("""
            INSERT INTO clients (
                name, callrail_account_id, callrail_company_id, google_ads_customer_id, facebook_ads_id, linkedin_ads_id, microsoft_ads_id,
                lead_gen_method, qualification_criteria, source_of_truth, email_provider, email_account, email_app_password,
                email_account_2, email_app_password_2, email_account_3, email_app_password_3, email_account_4, email_app_password_4, email_account_5, email_app_password_5,
                crm_deal_tags, crm_won_deal_tags, crm_lead_tags, lead_count_rule, exclude_past_customers,
                call_tracking_provider, ctm_account_id, ctm_profile_id, wc_account_id, wc_profile_id,
                tiktok_ads_id, twitter_ads_id, pinterest_ads_id, snapchat_ads_id, chatgpt_ads_id, reddit_ads_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            client.name, 
            client.callrail_account_id or None,
            client.callrail_company_id or None, 
            client.google_ads_customer_id or "", 
            client.facebook_ads_id or "",
            client.linkedin_ads_id or "",
            client.microsoft_ads_id or "",
            client.lead_gen_method,
            client.qualification_criteria,
            client.source_of_truth,
            client.email_provider or "",
            client.email_account or "",
            client.email_app_password or "",
            client.email_account_2 or "",
            client.email_app_password_2 or "",
            client.email_account_3 or "",
            client.email_app_password_3 or "",
            client.email_account_4 or "",
            client.email_app_password_4 or "",
            client.email_account_5 or "",
            client.email_app_password_5 or "",
            client.crm_deal_tags or "",
            client.crm_won_deal_tags or "",
            client.crm_lead_tags or "",
            client.lead_count_rule,
            client.exclude_past_customers,
            client.call_tracking_provider or "callrail",
            client.ctm_account_id or "",
            client.ctm_profile_id or "",
            client.wc_account_id or "",
            client.wc_profile_id or "",
            client.tiktok_ads_id or "",
            client.twitter_ads_id or "",
            client.pinterest_ads_id or "",
            client.snapchat_ads_id or "",
            client.chatgpt_ads_id or "",
            client.reddit_ads_id or ""
        ))
        
        client_id = cursor.lastrowid
        
        # Handle excluded customers updates for new onboarding if uploaded
        if client.excluded_customers is not None and len(client.excluded_customers) > 0:
            for cust in client.excluded_customers:
                normalized_p = normalize_phone(cust.phone)
                email_clean = cust.email.strip().lower() if cust.email else ""
                
                cursor.execute("""
                    INSERT INTO excluded_customers (client_id, first_name, last_name, email, phone, company_name)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    client_id,
                    cust.first_name,
                    cust.last_name,
                    email_clean,
                    normalized_p,
                    cust.company_name
                ))
        
        conn.commit()
        conn.close()
        
        # Check if SOT is marked as AI Rating to execute historical backfill
        if client.source_of_truth == "ai_rating":
            try:
                backfill_historical_callrail_leads(client_id, client.qualification_criteria, client.call_tracking_provider or "callrail")
            except Exception as e:
                print(f"⚠️ Warning: Historical backfill failed: {e}")
                
        return {
            "status": "success", 
            "client_id": client_id,
            "message": f"Client '{client.name}' onboarded successfully!"
        }
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database insertion error: {str(e)}")


@router.post("/dashboard/adjust-sale")
def adjust_offline_sale(request: Request, adjustment: SaleAdjustment):
    email = is_authenticated(request)
    if not email:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    user_role, user_client_id = get_user_role_and_client(email)
    if user_role != "full":
        raise HTTPException(status_code=403, detail="Unauthorized: Only managers and administrators can perform conversion adjustments.")
        
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        
        # Check if session exists and belongs to user_client_id (if restricted)
        cursor.execute("SELECT id, client_id, sale_closed, value, gclid FROM sessions WHERE id = ?", (adjustment.session_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            raise HTTPException(status_code=404, detail="Session record not found.")
            
        sess_id, client_id, sale_closed, orig_value, gclid = row
        if user_client_id is not None and client_id != user_client_id:
            conn.close()
            raise HTTPException(status_code=403, detail="Unauthorized: This session does not belong to your client account.")
            
        if sale_closed != 'YES':
            conn.close()
            raise HTTPException(status_code=400, detail="Only closed sales conversions can be adjusted.")
            
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        cursor.execute("""
            UPDATE sessions SET
                adjusted = 'YES',
                adjustment_type = ?,
                adjusted_value = ?,
                adjusted_at = ?
            WHERE id = ?
        """, (adjustment.adjustment_type, adjustment.adjusted_value, now_str, adjustment.session_id))
        
        # Insert audit history
        adj_label = "Google Ads Conversion Adjustment"
        old_val = f"Closed Sale Value: ${orig_value:.2f}"
        new_val = f"Retracted/Canceled" if adjustment.adjustment_type == 'RETRACT' else f"Restated Value: ${adjustment.adjusted_value:.2f}"
        cursor.execute("""
            INSERT INTO client_config_history (client_id, changed_by, feature_name, old_value, new_value)
            VALUES (?, ?, ?, ?, ?)
        """, (client_id, email, adj_label, old_val, new_val))
        
        conn.commit()
        conn.close()
        return {"status": "success", "message": "Sale adjustment saved successfully!"}
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")


@router.get("/dashboard/export/microsoft-adjustments")
def export_microsoft_adjustments(request: Request, client_id: int):
    email = is_authenticated(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)
    """
    Exports adjusted conversions for Microsoft Advertising into a compliant 
    Microsoft Ads Offline Conversion Adjustments CSV format.
    """
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        
        # Verify client exists
        cursor.execute("SELECT name, microsoft_ads_id FROM clients WHERE id = ?", (client_id,))
        client_row = cursor.fetchone()
        if not client_row:
            raise HTTPException(status_code=400, detail="Invalid client ID")
        
        client_name = client_row[0]
        microsoft_id = client_row[1] or ""
        
        # Pull adjusted records belonging to this client that have a MSCLKID
        cursor.execute("""
            SELECT msclkid, adjustment_type, adjusted_value, adjusted_at, created_at
            FROM sessions
            WHERE client_id = ? AND msclkid IS NOT NULL AND msclkid != '' AND adjusted = 'YES'
            ORDER BY adjusted_at DESC
        """, (client_id,))
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

    # Generate CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Microsoft Ads Adjustments headers
    writer.writerow(["Microsoft Click ID", "Conversion Name", "Conversion Time", "Adjustment Type", "Adjustment Time", "Adjusted Value", "Adjusted Value Currency", "Microsoft Account ID"])
    
    for r in rows:
        msclkid, adj_type, adj_value, adj_at, orig_created_at = r
        orig_conv_time = f"{orig_created_at} +0000" if orig_created_at else ""
        adj_time = f"{adj_at} +0000" if adj_at else ""
        
        # Original conversion name matches "LeadGroove Offline Sale"
        conv_name = "LeadGroove Offline Sale"
        
        val_str = f"{adj_value:.2f}" if adj_type == 'RESTATE' else ""
        curr_str = "USD" if adj_type == 'RESTATE' else ""
        
        writer.writerow([msclkid, conv_name, orig_conv_time, adj_type, adj_time, val_str, curr_str, microsoft_id])
            
    output.seek(0)
    safe_filename = re.sub(r'\s+', '-', client_name.strip().lower())
    
    headers = {
        'Content-Disposition': f'attachment; filename="microsoft_ads_adjustments_{safe_filename}.csv"',
        'Content-Type': 'text/csv'
    }
    return StreamingResponse(output, headers=headers)

@router.get("/feeds/google-adjustments.csv")
@router.get("/feeds/adjustments.csv")
def feed_google_adjustments(request: Request, client_id: int = 1, feed_key: Optional[str] = None):
    """
    Live Automated CSV Feed for Google Sheets =IMPORTDATA() and Google Ads Scheduled Fetch (Adjustments/Retractions).
    Returns real-time, Google Ads-compliant conversion adjustments CSV data for a specific client account.
    """
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        
        # Verify client exists
        cursor.execute("SELECT name, google_ads_customer_id FROM clients WHERE id = ?", (client_id,))
        client_row = cursor.fetchone()
        if not client_row:
            raise HTTPException(status_code=400, detail="Invalid client ID")
        
        client_name = client_row[0]
        google_ads_id = client_row[1] or ""
        
        # Pull adjusted records belonging to this client that have a GCLID
        cursor.execute("""
            SELECT gclid, adjustment_type, adjusted_value, adjusted_at, created_at
            FROM sessions
            WHERE client_id = ? AND gclid IS NOT NULL AND gclid != '' AND adjusted = 'YES'
            ORDER BY adjusted_at DESC
        """, (client_id,))
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

    # Generate CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    
    # 1. Google Ads template parameter header
    ads_parameter = f"Parameters:TimeZone=+0000;customerId={google_ads_id}" if google_ads_id else "Parameters:TimeZone=+0000"
    writer.writerow([ads_parameter])
    
    # 2. Google Ads Adjustments headers
    writer.writerow(["Google Click ID", "Conversion Name", "Conversion Time", "Adjustment Type", "Adjustment Time", "Adjusted Value", "Adjusted Value Currency"])
    
    for r in rows:
        gclid, adj_type, adj_value, adj_at, orig_created_at = r
        orig_conv_time = f"{orig_created_at} +0000" if orig_created_at else ""
        adj_time = f"{adj_at} +0000" if adj_at else ""
        
        conv_name = "LeadGroove Offline Sale"
        val_str = f"{adj_value:.2f}" if adj_type == 'RESTATE' else ""
        curr_str = "USD" if adj_type == 'RESTATE' else ""
        
        writer.writerow([gclid, conv_name, orig_conv_time, adj_type, adj_time, val_str, curr_str])
        
    output.seek(0)
    safe_filename = re.sub(r'\s+', '-', client_name.strip().lower())
    headers = {
        'Content-Disposition': f'inline; filename="google_adjustments_feed_{safe_filename}.csv"',
        'Content-Type': 'text/csv'
    }
    return StreamingResponse(output, headers=headers)



@router.get("/dashboard/export/google-adjustments")
def export_google_adjustments(request: Request, client_id: int):
    email = is_authenticated(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)
    """
    Exports adjusted conversions for Google Ads into a compliant 
    Google Ads Offline Conversion Adjustments CSV format.
    """
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        
        # Verify client exists
        cursor.execute("SELECT name, google_ads_customer_id FROM clients WHERE id = ?", (client_id,))
        client_row = cursor.fetchone()
        if not client_row:
            raise HTTPException(status_code=400, detail="Invalid client ID")
        
        client_name = client_row[0]
        google_ads_id = client_row[1] or ""
        
        # Pull adjusted records belonging to this client that have a GCLID
        cursor.execute("""
            SELECT gclid, adjustment_type, adjusted_value, adjusted_at, created_at
            FROM sessions
            WHERE client_id = ? AND gclid IS NOT NULL AND gclid != '' AND adjusted = 'YES'
            ORDER BY adjusted_at DESC
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
    
    # 2. Google Ads Adjustments headers
    writer.writerow(["Google Click ID", "Conversion Name", "Conversion Time", "Adjustment Type", "Adjustment Time", "Adjusted Value", "Adjusted Value Currency"])
    
    for r in rows:
        gclid, adj_type, adj_value, adj_at, orig_created_at = r
        orig_conv_time = f"{orig_created_at} +0000" if orig_created_at else ""
        adj_time = f"{adj_at} +0000" if adj_at else ""
        
        # Original conversion name must match exactly (which was "LeadGroove Offline Sale")
        conv_name = "LeadGroove Offline Sale"
        
        val_str = f"{adj_value:.2f}" if adj_type == 'RESTATE' else ""
        curr_str = "USD" if adj_type == 'RESTATE' else ""
        
        writer.writerow([gclid, conv_name, orig_conv_time, adj_type, adj_time, val_str, curr_str])
            
    output.seek(0)
    safe_filename = re.sub(r'\s+', '-', client_name.strip().lower())
    
    headers = {
        'Content-Disposition': f'attachment; filename="google_ads_adjustments_{safe_filename}.csv"',
        'Content-Type': 'text/csv'
    }
    return StreamingResponse(output, headers=headers)



# ---------------------------------------------------------
# AUTOMATED GOOGLE SHEETS & GOOGLE ADS SCHEDULED FETCH FEEDS
# ---------------------------------------------------------
@router.get("/feeds/google-conversions.csv")
@router.get("/feeds/conversions.csv")
def feed_google_conversions(request: Request, client_id: int = 1, feed_key: Optional[str] = None):
    """
    Live Automated CSV Feed for Google Sheets =IMPORTDATA() and Google Ads Scheduled Fetch.
    Returns real-time, Google Ads-compliant offline conversion CSV data for a specific client account.
    """
    try:
        conn = db_router.connect()
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
    
    # 1. Google Ads template parameter header
    ads_parameter = f"Parameters:TimeZone=+0000;customerId={google_ads_id}" if google_ads_id else "Parameters:TimeZone=+0000"
    writer.writerow([ads_parameter])
    
    # 2. Google Ads standard headers
    writer.writerow(["Google Click ID", "Conversion Name", "Conversion Time", "Conversion Value", "Conversion Currency"])
    
    for r in rows:
        gclid, qualified, sale_closed, value, created_at = r
        conv_time = f"{created_at} +0000" if created_at else ""
        
        if sale_closed == 'YES':
            conv_name = "LeadGroove Offline Sale"
            conv_value = float(value or 0.0)
        else:
            conv_name = "LeadGroove Qualified Lead"
            conv_value = 1.0  # Default lead qualification value
            
        writer.writerow([gclid, conv_name, conv_time, f"{conv_value:.2f}", "USD"])
        
    output.seek(0)
    safe_filename = re.sub(r'\s+', '-', client_name.strip().lower())
    headers = {
        'Content-Disposition': f'inline; filename="google_conversions_feed_{safe_filename}.csv"',
        'Content-Type': 'text/csv'
    }
    return StreamingResponse(output, headers=headers)


@router.get("/dashboard/export/google")
def export_google_conversions(request: Request, client_id: int):
    email = is_authenticated(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)
    """
    Exports qualified and closed conversions that have a valid GCLID 
    into a Google Ads-compliant CSV upload format, filtered by client_id.
    """
    try:
        conn = db_router.connect()
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
            conv_name = "LeadGroove Offline Sale"
            conv_value = float(value or 0.0)
        else:
            conv_name = "LeadGroove Qualified Lead"
            conv_value = 1.0  # Default lead qualification value
            
        writer.writerow([gclid, conv_name, conv_time, conv_value, "USD"])
            
    output.seek(0)
    safe_filename = re.sub(r'\s+', '-', client_name.strip().lower())
    
    headers = {
        'Content-Disposition': f'attachment; filename="google_ads_conversions_{safe_filename}.csv"',
        'Content-Type': 'text/csv'
    }
    return StreamingResponse(output, headers=headers)


@router.get("/dashboard/export/facebook")
def export_facebook_conversions(request: Request, client_id: int):
    email = is_authenticated(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)
    """
    Exports qualified and closed conversions (GCLID, FBCLID, etc.) with automated SHA-256
    hashed personal identifiers alongside fbclid for maximum Meta matching rates.
    """
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        
        # Verify client exists
        cursor.execute("SELECT name, facebook_ads_id FROM clients WHERE id = ?", (client_id,))
        client_row = cursor.fetchone()
        if not client_row:
            raise HTTPException(status_code=400, detail="Invalid client ID")
        
        client_name = client_row[0]
        pixel_id = client_row[1] or ""
        
        # Pull records belonging to this client that are either Qualified or Closed (with or without fbclid)
        cursor.execute("""
            SELECT fbclid, email, phone, name, qualified, sale_closed, value, created_at
            FROM sessions
            WHERE client_id = ? AND (qualified = 'YES' OR sale_closed = 'YES')
            ORDER BY created_at DESC
        """, (client_id,))
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

    # Generate Facebook standard format
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Headers for Meta upload (Facebook Click ID, hashed Email, hashed Phone, hashed First Name, hashed Last Name, Event Name, Event Time, Value, Currency, Pixel ID)
    writer.writerow(["fbclid", "email", "phone", "fn", "ln", "Event Name", "Event Time", "Value", "Currency", "Pixel ID"])
    
    for r in rows:
        fbclid, email_raw, phone_raw, name_raw, qualified, sale_closed, value, created_at = r
        conv_time = f"{created_at}"
        
        # Normalize and hash fields using our global helpers
        hashed_email = sha256_hash_str(email_raw) if email_raw else ""
        hashed_phone = sha256_hash_phone_str(phone_raw) if phone_raw else ""
        
        first_name, last_name = "", ""
        if name_raw:
            parts = str(name_raw).strip().split()
            if len(parts) == 1:
                first_name = parts[0]
            elif len(parts) > 1:
                first_name = parts[0]
                last_name = " ".join(parts[1:])
                
        hashed_fn = sha256_hash_str(first_name) if first_name else ""
        hashed_ln = sha256_hash_str(last_name) if last_name else ""
        
        if sale_closed == 'YES':
            event_name = "LeadGroove Offline Sale"
            event_val = float(value or 0.0)
        else:
            event_name = "LeadGroove Qualified Lead"
            event_val = 1.0
            
        writer.writerow([
            fbclid or "",
            hashed_email,
            hashed_phone,
            hashed_fn,
            hashed_ln,
            event_name,
            conv_time,
            f"{event_val:.2f}",
            "USD",
            pixel_id
        ])
            
    output.seek(0)
    safe_filename = re.sub(r'\s+', '-', client_name.strip().lower())
    
    headers = {
        'Content-Disposition': f'attachment; filename="facebook_conversions_{safe_filename}.csv"',
        'Content-Type': 'text/csv'
    }
    return StreamingResponse(output, headers=headers)


@router.get("/dashboard/export/linkedin")
def export_linkedin_conversions(request: Request, client_id: int):
    email = is_authenticated(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)
    """
    Exports qualified and closed conversions that have a valid LI_FAT_ID
    into a LinkedIn-compliant Offline Conversions CSV format.
    """
    try:
        conn = db_router.connect()
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
            conv_name = "LeadGroove Offline Sale"
            conv_val = float(value or 0.0)
        else:
            conv_name = "LeadGroove Qualified Lead"
            conv_val = 1.0
            
        writer.writerow([li_fat_id, conv_name, conv_time, f"{conv_val:.2f}", "USD", linkedin_account_id])
            
    output.seek(0)
    safe_filename = re.sub(r'\s+', '-', client_name.strip().lower())
    
    headers = {
        'Content-Disposition': f'attachment; filename="linkedin_conversions_{safe_filename}.csv"',
        'Content-Type': 'text/csv'
    }
    return StreamingResponse(output, headers=headers)


@router.get("/dashboard/export/microsoft")
def export_microsoft_conversions(request: Request, client_id: int):
    email = is_authenticated(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)
    """
    Exports qualified and closed conversions that have a valid MSCLKID
    into a Microsoft Ads-compliant Offline Conversions CSV format.
    """
    try:
        conn = db_router.connect()
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
            conv_name = "LeadGroove Offline Sale"
            conv_val = float(value or 0.0)
        else:
            conv_name = "LeadGroove Qualified Lead"
            conv_val = 1.0
            
        writer.writerow([msclkid, conv_name, conv_time, f"{conv_val:.2f}", "USD", microsoft_id])
            
    output.seek(0)
    safe_filename = re.sub(r'\s+', '-', client_name.strip().lower())
    
    headers = {
        'Content-Disposition': f'attachment; filename="microsoft_conversions_{safe_filename}.csv"',
        'Content-Type': 'text/csv'
    }
    return StreamingResponse(output, headers=headers)


@router.get("/dashboard/export/tiktok")
def export_tiktok_conversions(request: Request, client_id: int):
    email = is_authenticated(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        cursor.execute("SELECT name, tiktok_ads_id FROM clients WHERE id = ?", (client_id,))
        client_row = cursor.fetchone()
        if not client_row:
            raise HTTPException(status_code=400, detail="Invalid client ID")
        client_name = client_row[0]
        pixel_id = client_row[1] or ""
        cursor.execute("""
            SELECT ttclid, qualified, sale_closed, value, created_at
            FROM sessions
            WHERE client_id = ? AND ttclid IS NOT NULL AND ttclid != '' AND (qualified = 'YES' OR sale_closed = 'YES')
            ORDER BY created_at DESC
        """, (client_id,))
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Tiktok Click ID", "Event Name", "Event Time", "Value", "Currency", "TikTok Pixel ID"])
    for r in rows:
        ttclid, qualified, sale_closed, value, created_at = r
        conv_time = f"{created_at}"
        if sale_closed == 'YES':
            event_name = "LeadGroove Offline Sale"
            event_val = float(value or 0.0)
        else:
            event_name = "LeadGroove Qualified Lead"
            event_val = 1.0
        writer.writerow([ttclid, event_name, conv_time, f"{event_val:.2f}", "USD", pixel_id])
    output.seek(0)
    safe_filename = re.sub(r'\s+', '-', client_name.strip().lower())
    headers = {
        'Content-Disposition': f'attachment; filename="tiktok_conversions_{safe_filename}.csv"',
        'Content-Type': 'text/csv'
    }
    return StreamingResponse(output, headers=headers)


@router.get("/dashboard/export/twitter")
def export_twitter_conversions(request: Request, client_id: int):
    email = is_authenticated(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        cursor.execute("SELECT name, twitter_ads_id FROM clients WHERE id = ?", (client_id,))
        client_row = cursor.fetchone()
        if not client_row:
            raise HTTPException(status_code=400, detail="Invalid client ID")
        client_name = client_row[0]
        pixel_id = client_row[1] or ""
        cursor.execute("""
            SELECT twclid, qualified, sale_closed, value, created_at
            FROM sessions
            WHERE client_id = ? AND twclid IS NOT NULL AND twclid != '' AND (qualified = 'YES' OR sale_closed = 'YES')
            ORDER BY created_at DESC
        """, (client_id,))
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["twclid", "Event Name", "Event Time", "Conversion Value", "Currency", "X Ads Pixel ID"])
    for r in rows:
        twclid, qualified, sale_closed, value, created_at = r
        conv_time = f"{created_at}"
        if sale_closed == 'YES':
            event_name = "LeadGroove Offline Sale"
            event_val = float(value or 0.0)
        else:
            event_name = "LeadGroove Qualified Lead"
            event_val = 1.0
        writer.writerow([twclid, event_name, conv_time, f"{event_val:.2f}", "USD", pixel_id])
    output.seek(0)
    safe_filename = re.sub(r'\s+', '-', client_name.strip().lower())
    headers = {
        'Content-Disposition': f'attachment; filename="x_conversions_{safe_filename}.csv"',
        'Content-Type': 'text/csv'
    }
    return StreamingResponse(output, headers=headers)



@router.get("/dashboard/export/snapchat")
def export_snapchat_conversions(request: Request, client_id: int):
    email = is_authenticated(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        cursor.execute("SELECT name, snapchat_ads_id FROM clients WHERE id = ?", (client_id,))
        client_row = cursor.fetchone()
        if not client_row:
            raise HTTPException(status_code=400, detail="Invalid client ID")
        client_name = client_row[0]
        pixel_id = client_row[1] or ""
        cursor.execute("""
            SELECT scclid, qualified, sale_closed, value, created_at
            FROM sessions
            WHERE client_id = ? AND scclid IS NOT NULL AND scclid != '' AND (qualified = 'YES' OR sale_closed = 'YES')
            ORDER BY created_at DESC
        """, (client_id,))
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["scclid", "Event Name", "Event Time", "Value", "Currency", "Snapchat Pixel ID"])
    for r in rows:
        scclid, qualified, sale_closed, value, created_at = r
        conv_time = f"{created_at}"
        if sale_closed == 'YES':
            event_name = "LeadGroove Offline Sale"
            event_val = float(value or 0.0)
        else:
            event_name = "LeadGroove Qualified Lead"
            event_val = 1.0
        writer.writerow([scclid, event_name, conv_time, f"{event_val:.2f}", "USD", pixel_id])
    output.seek(0)
    safe_filename = re.sub(r'\s+', '-', client_name.strip().lower())
    headers = {
        'Content-Disposition': f'attachment; filename="snapchat_conversions_{safe_filename}.csv"',
        'Content-Type': 'text/csv'
    }
    return StreamingResponse(output, headers=headers)


@router.get("/dashboard/export/pinterest")
def export_pinterest_conversions(request: Request, client_id: int):
    email = is_authenticated(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        cursor.execute("SELECT name, pinterest_ads_id FROM clients WHERE id = ?", (client_id,))
        client_row = cursor.fetchone()
        if not client_row:
            raise HTTPException(status_code=400, detail="Invalid client ID")
        client_name = client_row[0]
        tag_id = client_row[1] or ""
        cursor.execute("""
            SELECT pin_clid, qualified, sale_closed, value, created_at
            FROM sessions
            WHERE client_id = ? AND pin_clid IS NOT NULL AND pin_clid != '' AND (qualified = 'YES' OR sale_closed = 'YES')
            ORDER BY created_at DESC
        """, (client_id,))
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["pin_clid", "Event Name", "Event Time", "Value", "Currency", "Pinterest Tag ID"])
    for r in rows:
        pin_clid, qualified, sale_closed, value, created_at = r
        conv_time = f"{created_at}"
        if sale_closed == 'YES':
            event_name = "LeadGroove Offline Sale"
            event_val = float(value or 0.0)
        else:
            event_name = "LeadGroove Qualified Lead"
            event_val = 1.0
        writer.writerow([pin_clid, event_name, conv_time, f"{event_val:.2f}", "USD", tag_id])
    output.seek(0)
    safe_filename = re.sub(r'\s+', '-', client_name.strip().lower())
    headers = {
        'Content-Disposition': f'attachment; filename="pinterest_conversions_{safe_filename}.csv"',
        'Content-Type': 'text/csv'
    }
    return StreamingResponse(output, headers=headers)


@router.get("/dashboard/export/reddit")
def export_reddit_conversions(request: Request, client_id: int):
    email = is_authenticated(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)
    """
    Exports qualified and closed conversions that have a valid rdt_cid
    into a Reddit Ads-compliant Offline Conversions CSV format.
    """
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        
        cursor.execute("SELECT name, reddit_ads_id FROM clients WHERE id = ?", (client_id,))
        client_row = cursor.fetchone()
        if not client_row:
            raise HTTPException(status_code=400, detail="Invalid client ID")
        
        client_name = client_row[0]
        pixel_id = client_row[1] or ""
        
        cursor.execute("""
            SELECT rdt_cid, qualified, sale_closed, value, created_at
            FROM sessions
            WHERE client_id = ? AND rdt_cid IS NOT NULL AND rdt_cid != '' AND (qualified = 'YES' OR sale_closed = 'YES')
            ORDER BY created_at DESC
        """, (client_id,))
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

    output = io.StringIO()
    writer = csv.writer(output)
    
    writer.writerow(["rdt_cid", "Event Name", "Event Time", "Value", "Currency", "Reddit Ads ID"])
    
    for r in rows:
        rdt_cid, qualified, sale_closed, value, created_at = r
        conv_time = f"{created_at} +0000" if created_at else ""
        
        if sale_closed == 'YES':
            event_name = "LeadGroove Offline Sale"
            event_val = float(value or 0.0)
        else:
            event_name = "LeadGroove Qualified Lead"
            event_val = 1.0
            
        writer.writerow([rdt_cid, event_name, conv_time, f"{event_val:.2f}", "USD", pixel_id])
            
    output.seek(0)
    safe_filename = re.sub(r'\s+', '-', client_name.strip().lower())
    
    headers = {
        'Content-Disposition': f'attachment; filename="reddit_ads_conversions_{safe_filename}.csv"',
        'Content-Type': 'text/csv'
    }
    return StreamingResponse(output, headers=headers)


@router.get("/dashboard/export/chatgpt")
def export_chatgpt_conversions(request: Request, client_id: int):
    email = is_authenticated(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        cursor.execute("SELECT name, chatgpt_ads_id FROM clients WHERE id = ?", (client_id,))
        client_row = cursor.fetchone()
        if not client_row:
            raise HTTPException(status_code=400, detail="Invalid client ID")
        client_name = client_row[0]
        pixel_id = client_row[1] or ""
        cursor.execute("""
            SELECT gptclid, qualified, sale_closed, value, created_at
            FROM sessions
            WHERE client_id = ? AND gptclid IS NOT NULL AND gptclid != '' AND (qualified = 'YES' OR sale_closed = 'YES')
            ORDER BY created_at DESC
        """, (client_id,))
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["gptclid", "Event Name", "Event Time", "Value", "Currency", "ChatGPT Ads ID"])
    for r in rows:
        gptclid, qualified, sale_closed, value, created_at = r
        conv_time = f"{created_at}"
        if sale_closed == 'YES':
            event_name = "LeadGroove Offline Sale"
            event_val = float(value or 0.0)
        else:
            event_name = "LeadGroove Qualified Lead"
            event_val = 1.0
        writer.writerow([gptclid, event_name, conv_time, f"{event_val:.2f}", "USD", pixel_id])
    output.seek(0)
    safe_filename = re.sub(r'\s+', '-', client_name.strip().lower())
    headers = {
        'Content-Disposition': f'attachment; filename="chatgpt_conversions_{safe_filename}.csv"',
        'Content-Type': 'text/csv'
    }
    return StreamingResponse(output, headers=headers)



    return StreamingResponse(output, headers=headers)


import hashlib

def sha256_hash_str(val: str) -> str:
    cleaned = str(val).strip().lower()
    return hashlib.sha256(cleaned.encode('utf-8')).hexdigest()

def sha256_hash_phone_str(val: str) -> str:
    cleaned = re.sub(r'\D', '', str(val))
    if len(cleaned) == 10:
        cleaned = "1" + cleaned
    return hashlib.sha256(cleaned.encode('utf-8')).hexdigest()

def get_audience_contacts(client_id: int, segment: str = "all"):
    conn = db_router.connect()
    cursor = conn.cursor()
    # Pull unique sessions with contacts and their sale_closed status
    cursor.execute("""
        SELECT name, phone, email, company, sale_closed
        FROM sessions
        WHERE client_id = ? AND (email IS NOT NULL AND email != '' OR phone IS NOT NULL AND phone != '')
    """, (client_id,))
    rows = cursor.fetchall()
    conn.close()
    
    # Pass 1: Aggregate purchase counts per customer key
    purchase_counts = {}
    customer_profiles = {}
    
    for name, phone, email, company, sale_closed in rows:
        norm_phone = re.sub(r'\D', '', str(phone or ""))
        if len(norm_phone) == 10:
            norm_phone = "1" + norm_phone
        norm_email = str(email or "").strip().lower()
        
        key = (norm_phone, norm_email)
        if not key[0] and not key[1]:
            continue
            
        if key not in purchase_counts:
            purchase_counts[key] = 0
            customer_profiles[key] = {
                "name": name,
                "phone": norm_phone,
                "email": norm_email,
                "company": company or ""
            }
        else:
            if name and (not customer_profiles[key]["name"] or customer_profiles[key]["name"] == "Unknown"):
                customer_profiles[key]["name"] = name
            if company and not customer_profiles[key]["company"]:
                customer_profiles[key]["company"] = company

        if sale_closed == 'YES':
            purchase_counts[key] += 1
            
    # Pass 2: filter according to the requested cohort segment
    contacts = []
    for key, count in purchase_counts.items():
        profile = customer_profiles[key]
        
        # Split names
        first_name, last_name = "", ""
        if profile["name"]:
            parts = str(profile["name"]).strip().split()
            if len(parts) == 1:
                first_name = parts[0]
            elif len(parts) > 1:
                first_name = parts[0]
                last_name = " ".join(parts[1:])
                
        c_dict = {
            "first_name": first_name,
            "last_name": last_name,
            "phone": profile["phone"],
            "email": profile["email"],
            "company": profile["company"]
        }
        
        if segment == "single":
            if count == 1:
                contacts.append(c_dict)
        elif segment == "multi":
            if count >= 2:
                contacts.append(c_dict)
        elif segment == "leads":
            if count == 0:
                contacts.append(c_dict)
        else: # "all"
            contacts.append(c_dict)
            
    return contacts


@router.get("/dashboard/export/audience/google")
def export_google_audience(request: Request, client_id: int, segment: str = "all"):
    email = is_authenticated(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM clients WHERE id = ?", (client_id,))
        client_row = cursor.fetchone()
        if not client_row:
            raise HTTPException(status_code=400, detail="Invalid client ID")
        client_name = client_row[0]
        conn.close()
        
        contacts = get_audience_contacts(client_id, segment=segment)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
        
    output = io.StringIO()
    writer = csv.writer(output)
    # Google Customer Match headers
    writer.writerow(["Email", "Phone", "First Name", "Last Name", "Country"])
    for c in contacts:
        hashed_email = sha256_hash_str(c["email"]) if c["email"] else ""
        hashed_phone = sha256_hash_phone_str(c["phone"]) if c["phone"] else ""
        # Names can be raw for Google's browser match uploader, but we can also write them
        writer.writerow([hashed_email, hashed_phone, c["first_name"], c["last_name"], "US"])
        
    output.seek(0)
    safe_filename = re.sub(r'\s+', '-', client_name.strip().lower())
    headers = {
        'Content-Disposition': f'attachment; filename="google_audience_match_{segment}_{safe_filename}.csv"',
        'Content-Type': 'text/csv'
    }
    return StreamingResponse(output, headers=headers)


@router.get("/dashboard/export/audience/facebook")
def export_facebook_audience(request: Request, client_id: int, segment: str = "all"):
    email = is_authenticated(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM clients WHERE id = ?", (client_id,))
        client_row = cursor.fetchone()
        if not client_row:
            raise HTTPException(status_code=400, detail="Invalid client ID")
        client_name = client_row[0]
        conn.close()
        
        contacts = get_audience_contacts(client_id, segment=segment)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
        
    output = io.StringIO()
    writer = csv.writer(output)
    # Meta Custom Audience headers
    writer.writerow(["email", "phone", "fn", "ln", "country"])
    for c in contacts:
        hashed_email = sha256_hash_str(c["email"]) if c["email"] else ""
        hashed_phone = sha256_hash_phone_str(c["phone"]) if c["phone"] else ""
        hashed_fn = sha256_hash_str(c["first_name"]) if c["first_name"] else ""
        hashed_ln = sha256_hash_str(c["last_name"]) if c["last_name"] else ""
        writer.writerow([hashed_email, hashed_phone, hashed_fn, hashed_ln, "US"])
        
    output.seek(0)
    safe_filename = re.sub(r'\s+', '-', client_name.strip().lower())
    headers = {
        'Content-Disposition': f'attachment; filename="meta_custom_audience_{segment}_{safe_filename}.csv"',
        'Content-Type': 'text/csv'
    }
    return StreamingResponse(output, headers=headers)


@router.get("/dashboard/export/audience/linkedin")
def export_linkedin_audience(request: Request, client_id: int, segment: str = "all"):
    email = is_authenticated(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM clients WHERE id = ?", (client_id,))
        client_row = cursor.fetchone()
        if not client_row:
            raise HTTPException(status_code=400, detail="Invalid client ID")
        client_name = client_row[0]
        conn.close()
        
        contacts = get_audience_contacts(client_id, segment=segment)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
        
    output = io.StringIO()
    writer = csv.writer(output)
    # LinkedIn Member Match headers
    writer.writerow(["email", "phone", "firstname", "lastname", "companyname"])
    for c in contacts:
        hashed_email = sha256_hash_str(c["email"]) if c["email"] else ""
        hashed_phone = sha256_hash_phone_str(c["phone"]) if c["phone"] else ""
        # Names can be raw, company is extremely useful for LinkedIn member match
        writer.writerow([hashed_email, hashed_phone, c["first_name"], c["last_name"], c["company"]])
        
    output.seek(0)
    safe_filename = re.sub(r'\s+', '-', client_name.strip().lower())
    headers = {
        'Content-Disposition': f'attachment; filename="linkedin_member_matching_{segment}_{safe_filename}.csv"',
        'Content-Type': 'text/csv'
    }
    return StreamingResponse(output, headers=headers)


@router.get("/dashboard/export/exclusions")
def export_client_exclusions(request: Request, client_id: int):
    email = is_authenticated(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)
    """
    Exports the current active exclusion list for a client as a CSV file.
    """
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        
        # Verify client exists
        cursor.execute("SELECT name FROM clients WHERE id = ?", (client_id,))
        client_row = cursor.fetchone()
        if not client_row:
            raise HTTPException(status_code=400, detail="Invalid client ID")
        
        client_name = client_row[0]
        
        # Pull excluded customers belonging to this client
        cursor.execute("""
            SELECT first_name, last_name, email, phone, company_name
            FROM excluded_customers
            WHERE client_id = ?
            ORDER BY id ASC
        """, (client_id,))
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

    # Generate CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Standard headers that match our sample sheet
    writer.writerow(["first name", "last name", "email", "phone number", "company name"])
    
    for r in rows:
        writer.writerow(r)
            
    output.seek(0)
    safe_filename = re.sub(r'\\s+', '-', client_name.strip().lower())
    
    headers = {
        'Content-Disposition': f'attachment; filename="exclusions_{safe_filename}.csv"',
        'Content-Type': 'text/csv'
    }
    return StreamingResponse(output, headers=headers)



