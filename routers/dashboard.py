import os
import re
import json
import io
import pandas as pd
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, HTTPException, File, UploadFile, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from typing import Optional
from database.connection import db_router
from services.auth_service import is_authenticated, get_user_role_and_client
from services.identity_matcher import (
    normalize_phone, calculate_company_similarity, check_name_transposition, find_dynamic_columns_custom
)
from models.schemas import SaleAdjustment

router = APIRouter()

def normalize_email(email_str: str) -> str:
    if not email_str:
        return ""
    return str(email_str).strip().lower()

@router.get("/dashboard", response_class=HTMLResponse)
def view_dashboard(request: Request, client_id: Optional[int] = None, date_range: Optional[str] = "all", start_date: Optional[str] = "", end_date: Optional[str] = ""):
    email = is_authenticated(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)
    """Interactive dashboard with client filtering."""
    user_role, user_client_id = get_user_role_and_client(email)
    is_manager = (user_role == "full")
    
    if user_client_id is not None:
        client_id = user_client_id
        
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        
        # 1. Fetch All Available Clients for the Dropdown Selector
        cursor.execute("SELECT id, name, google_ads_customer_id, facebook_ads_id, linkedin_ads_id, microsoft_ads_id, tiktok_ads_id, twitter_ads_id, pinterest_ads_id, chatgpt_ads_id, reddit_ads_id FROM clients ORDER BY name ASC")
        clients = cursor.fetchall()
        
        # Determine filtering
        selected_client_id = client_id if client_id is not None else 0 # 0 signifies "All Clients" (Agency Overview)
        if user_client_id is not None:
            selected_client_id = user_client_id
            
        opt_all = "selected" if date_range in [None, "all", ""] else ""
        opt_1d = "selected" if date_range in ["1d", "today"] else ""
        opt_7d = "selected" if date_range == "7d" else ""
        opt_30d = "selected" if date_range == "30d" else ""
        opt_90d = "selected" if date_range == "90d" else ""
        opt_custom = "selected" if date_range == "custom" else ""
        
        start_date_val = start_date or ""
        end_date_val = end_date or ""
        
        active_client_name = "All Clients"
        for c_id, c_name, *rest in clients:
            if c_id == selected_client_id:
                active_client_name = c_name
                break
                
        if date_range in ["1d", "today"]:
            date_range_label = "1 Day (Today)"
        elif date_range == "7d":
            date_range_label = "Last 7 Days"
        elif date_range == "30d":
            date_range_label = "Last 30 Days"
        elif date_range == "90d":
            date_range_label = "Last 90 Days"
        elif date_range == "custom":
            date_range_label = f"Custom: {start_date_val or 'Start'} to {end_date_val or 'Present'}"
        else:
            date_range_label = "All Time" 
        
        # Build Settings Button HTML
        if selected_client_id == 0:
            settings_btn_html = ''
        else:
            settings_label = "⚙️ Client Settings" if user_role == "full" else "⚙️ View Settings & History"
            settings_btn_html = f'<a href="/dashboard/settings?client_id={selected_client_id}" class="btn-settings">{settings_label}</a>'
            
        onboard_btn_html = '<a href="/dashboard/add-client" class="btn-add-client">➕ Onboard Client</a>' if is_manager and user_client_id is None else ''
        
        # 2. Query Dashboard Rows and Calculations
        if selected_client_id == 0:
            # Multi-Client (All Clients) View - Join with clients table to display client names
            cursor.execute("""
                SELECT s.id, s.phone, s.email, s.name, s.company, s.gclid, s.source, s.qualified, s.sale_closed, s.value, s.reason, s.created_at, c.name, s.fbclid, s.li_fat_id, s.msclkid, s.match_fuzzy, s.certainty_score, s.ttclid, s.twclid, s.pin_clid, s.scclid, s.gptclid, s.rdt_cid, s.adjusted, s.adjusted_value, s.adjustment_type, s.adjusted_at
                FROM sessions s
                LEFT JOIN clients c ON s.client_id = c.id
                ORDER BY s.created_at DESC
            """)
            raw_rows = cursor.fetchall()
            rows = [r for r in raw_rows if is_in_date_range(r[11], date_range, start_date, end_date)]
            client_name_header = "All Agency Accounts"
            client_ads_id = "Multiple Accounts"
        else:
            # Single-Client Filtered View
            cursor.execute("""
                SELECT s.id, s.phone, s.email, s.name, s.company, s.gclid, s.source, s.qualified, s.sale_closed, s.value, s.reason, s.created_at, c.name, s.fbclid, s.li_fat_id, s.msclkid, s.match_fuzzy, s.certainty_score, s.ttclid, s.twclid, s.pin_clid, s.scclid, s.gptclid, s.rdt_cid, s.adjusted, s.adjusted_value, s.adjustment_type, s.adjusted_at
                FROM sessions s
                LEFT JOIN clients c ON s.client_id = c.id
                WHERE s.client_id = ?
                ORDER BY s.created_at DESC
            """, (selected_client_id,))
            raw_rows = cursor.fetchall()
            rows = [r for r in raw_rows if is_in_date_range(r[11], date_range, start_date, end_date)]
            
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
    exportable_tiktok = sum(1 for r in rows if len(r) > 18 and r[18] and (r[7] == 'YES' or r[8] == 'YES'))
    exportable_twitter = sum(1 for r in rows if len(r) > 19 and r[19] and (r[7] == 'YES' or r[8] == 'YES'))
    exportable_pinterest = sum(1 for r in rows if len(r) > 20 and r[20] and (r[7] == 'YES' or r[8] == 'YES'))
    exportable_snapchat = sum(1 for r in rows if len(r) > 21 and r[21] and (r[7] == 'YES' or r[8] == 'YES'))
    exportable_chatgpt = sum(1 for r in rows if len(r) > 22 and r[22] and (r[7] == 'YES' or r[8] == 'YES'))
    exportable_reddit = sum(1 for r in rows if len(r) > 23 and r[23] and (r[7] == 'YES' or r[8] == 'YES'))

    # Generate the Selector Dropdown Options
    dropdown_options = ""
    if user_client_id is not None:
        restricted_clients = [c for c in clients if c[0] == user_client_id]
        for c_id, c_name, c_ads, c_fb, c_li, c_ms, c_tt, c_tw, c_pin, c_gpt, c_rdt in restricted_clients:
            dropdown_options += f'<option value="{c_id}" selected> {c_name} (Ads: {c_ads})</option>'
    else:
        dropdown_options = f'<option value="0" {"selected" if selected_client_id == 0 else ""}> [Show All Clients / Agency View]</option>'
        for c_id, c_name, c_ads, c_fb, c_li, c_ms, c_tt, c_tw, c_pin, c_gpt, c_rdt in clients:
            is_selected = "selected" if selected_client_id == c_id else ""
            dropdown_options += f'<option value="{c_id}" {is_selected}> {c_name} (Ads: {c_ads})</option>'

    # Convert rows to table items
    table_rows_html = ""
    for r in rows:
        id_val, phone, email, name, company, gclid, source, qualified, sale_closed, value, reason, created_at, client_name_linked, fbclid, li_fat_id, msclkid, match_fuzzy, certainty_score, ttclid, twclid, pin_clid, scclid, gptclid, rdt_cid, adjusted, adjusted_value, adjustment_type, adjusted_at = r
        
        qual_badge = '<span style="background: #e8f5e9; color: #2e7d32; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 12px;">YES</span>' if qualified == 'YES' else '<span style="background: #ffebee; color: #c62828; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 12px;">NO</span>'
        
        if sale_closed == 'YES':
            if adjusted == 'YES' and adjustment_type == 'RETRACT':
                closed_badge = '<span style="background: #ffebee; color: #c62828; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 11px; text-decoration: line-through;">YES</span><br><small style="color: #c62828; font-weight: bold;"> Retracted</small>'
            elif adjusted == 'YES' and adjustment_type == 'RESTATE':
                closed_badge = '<span style="background: #e8f5e9; color: #2e7d32; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 11px;">YES</span><br><small style="color: #f57c00; font-weight: bold;"> Restated</small>'
            else:
                closed_badge = '<span style="background: #e8f5e9; color: #2e7d32; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 12px;">YES</span>'
        else:
            closed_badge = '<span style="background: #ffebee; color: #c62828; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 12px;">NO</span>'
        
        # Build multi-channel click ID display blocks
        click_ids_list = []
        if gclid:
            click_ids_list.append(f'<span style="display:inline-block; margin-bottom: 2px;"><strong style="color: #4285F4; font-size: 10px;">G:</strong> <code style="background: #f1f3f4; padding: 1px 4px; border-radius: 3px; font-size: 11px; display: inline-block; max-width: 100px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; vertical-align: middle;" title="{gclid}">{gclid}</code></span>')
        if fbclid:
            click_ids_list.append(f'<span style="display:inline-block; margin-bottom: 2px;"><strong style="color: #1877F2; font-size: 10px;">F:</strong> <code style="background: #f1f3f4; padding: 1px 4px; border-radius: 3px; font-size: 11px; display: inline-block; max-width: 100px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; vertical-align: middle;" title="{fbclid}">{fbclid}</code></span>')
        if li_fat_id:
            click_ids_list.append(f'<span style="display:inline-block; margin-bottom: 2px;"><strong style="color: #0A66C2; font-size: 10px;">L:</strong> <code style="background: #f1f3f4; padding: 1px 4px; border-radius: 3px; font-size: 11px; display: inline-block; max-width: 100px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; vertical-align: middle;" title="{li_fat_id}">{li_fat_id}</code></span>')
        if msclkid:
            click_ids_list.append(f'<span style="display:inline-block; margin-bottom: 2px;"><strong style="color: #00A4EF; font-size: 10px;">M:</strong> <code style="background: #f1f3f4; padding: 1px 4px; border-radius: 3px; font-size: 11px; display: inline-block; max-width: 100px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; vertical-align: middle;" title="{msclkid}">{msclkid}</code></span>')
        if ttclid:
            click_ids_list.append(f'<span style="display:inline-block; margin-bottom: 2px;"><strong style="color: #000000; font-size: 10px;">TT:</strong> <code style="background: #f1f3f4; padding: 1px 4px; border-radius: 3px; font-size: 11px; display: inline-block; max-width: 100px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; vertical-align: middle;" title="{ttclid}">{ttclid}</code></span>')
        if twclid:
            click_ids_list.append(f'<span style="display:inline-block; margin-bottom: 2px;"><strong style="color: #1DA1F2; font-size: 10px;">X:</strong> <code style="background: #f1f3f4; padding: 1px 4px; border-radius: 3px; font-size: 11px; display: inline-block; max-width: 100px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; vertical-align: middle;" title="{twclid}">{twclid}</code></span>')
        if pin_clid:
            click_ids_list.append(f'<span style="display:inline-block; margin-bottom: 2px;"><strong style="color: #E60023; font-size: 10px;">P:</strong> <code style="background: #f1f3f4; padding: 1px 4px; border-radius: 3px; font-size: 11px; display: inline-block; max-width: 100px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; vertical-align: middle;" title="{pin_clid}">{pin_clid}</code></span>')
        if scclid:
            click_ids_list.append(f'<span style="display:inline-block; margin-bottom: 2px;"><strong style="color: #E9B800; background: #000; padding: 1px 3px; border-radius: 2px; font-size: 10px;">SC:</strong> <code style="background: #f1f3f4; padding: 1px 4px; border-radius: 3px; font-size: 11px; display: inline-block; max-width: 100px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; vertical-align: middle;" title="{scclid}">{scclid}</code></span>')
        if gptclid:
            click_ids_list.append(f'<span style="display:inline-block; margin-bottom: 2px;"><strong style="color: #10a37f; font-size: 10px;">GPT:</strong> <code style="background: #f1f3f4; padding: 1px 4px; border-radius: 3px; font-size: 11px; display: inline-block; max-width: 100px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; vertical-align: middle;" title="{gptclid}">{gptclid}</code></span>')
        if rdt_cid:
            click_ids_list.append(f'<span style="display:inline-block; margin-bottom: 2px;"><strong style="color: #FF4500; font-size: 10px;">Reddit:</strong> <code style="background: #f1f3f4; padding: 1px 4px; border-radius: 3px; font-size: 11px; display: inline-block; max-width: 100px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; vertical-align: middle;" title="{rdt_cid}">{rdt_cid}</code></span>')
            
        click_ids_display = "<br>".join(click_ids_list) if click_ids_list else '<span style="color: #999; font-style: italic;">None detected</span>'
        if adjusted == 'YES' and adjustment_type == 'RETRACT':
            value_display = f'<span style="text-decoration: line-through; color: #999; font-size: 11px;">${value:,.2f}</span><br><strong style="color: #c62828;">$0.00</strong>'
        elif adjusted == 'YES' and adjustment_type == 'RESTATE':
            value_display = f'<span style="text-decoration: line-through; color: #999; font-size: 11px;">${value:,.2f}</span><br><strong style="color: #2e7d32;">${adjusted_value:,.2f}</strong>'
        else:
            value_display = f"<strong>${value:,.2f}</strong>" if value and value > 0 else '<span style="color: #999;">$0.00</span>'
            
        # Determine Adjustment Action button
        if sale_closed == 'YES':
            if adjusted == 'YES':
                action_btn_html = f'<span style="color: #666; font-weight: bold; font-size: 11px; white-space: nowrap;">✏️ Adjusted ({adjustment_type})</span>'
            elif user_role == 'full':
                escaped_name = (name or 'Unknown').replace("'", "\\'")
                action_btn_html = f'<button onclick="openAdjustmentModal({id_val}, \'{escaped_name}\', {value})" style="background-color: #1a237e; color: white; border: none; padding: 4px 10px; border-radius: 4px; font-size: 11px; cursor: pointer; font-weight: bold; white-space: nowrap; transition: background 0.2s;">✏️ Adjust</button>'
            else:
                action_btn_html = '<span style="color: #999; font-size: 11px; font-style: italic;">Read-Only</span>'
        else:
            action_btn_html = '<span style="color: #bbb; font-size: 11px; font-style: italic;">N/A</span>'
        
        # Display the client column only in the multi-client view
        client_column_html = f'<td><span class="client-badge">{client_name_linked}</span></td>' if selected_client_id == 0 else ''
        
        # Determine Matching Method & Certainty Badge
        if sale_closed == 'YES':
            if match_fuzzy == 'YES':
                matching_badge = f'<td><span style="background-color: #fff3cd; color: #856404; border: 1px solid #ffe0b2; padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 11px; white-space: nowrap;"> Fuzzy Match ({certainty_score or 0}%)</span></td>'
            else:
                matching_badge = f'<td><span style="background-color: #e8f5e9; color: #2e7d32; border: 1px solid #c8e6c9; padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 11px; white-space: nowrap;">✅ Exact Match ({certainty_score or 100}%)</span></td>'
        else:
            matching_badge = '<td><span style="color: #666; font-style: italic; font-size: 11px; white-space: nowrap;">Direct Tracking</span></td>'

        # Beautiful lead source delineation (Phone Call vs Web Form)
        source_lower = str(source).lower() if source else ""
        if 'form' in source_lower:
            source_badge = '<span class="badge-source badge-form"> Web Form</span>'
        elif any(x in source_lower for x in ['call', 'phone', 'callrail']):
            source_badge = '<span class="badge-source badge-call"> Phone Call</span>'
        else:
            source_badge = f'<span class="badge-source">{str(source).upper()}</span>'
            
        table_rows_html += f"""
        <tr>
            <td>{id_val}</td>
            {client_column_html}
            <td><small>{created_at}</small></td>
            <td>{source_badge}</td>
            <td><strong>{name or 'Unknown'}</strong><br><small style="color:#666;">{phone}</small></td>
            <td>{click_ids_display}</td>
            <td>{qual_badge}</td>
            <td>{closed_badge}</td>
            <td>{value_display}</td>
            {matching_badge}
            <td><small>{reason or 'N/A'}</small></td>
            <td>{action_btn_html}</td>
        </tr>
        """

    if not table_rows_html:
        table_rows_html = f'<tr><td colspan="{"12" if selected_client_id == 0 else "11"}" style="text-align: center; color: #888; padding: 40px;">No lead sessions recorded for this client. Set up their CallRail webhook to populate this space!</td></tr>'

    # Calculate adjustments count
    exportable_adjustments = sum(1 for r in rows if r[5] and len(r) > 23 and r[23] == 'YES')
    exportable_microsoft_adjustments = sum(1 for r in rows if r[15] and len(r) > 23 and r[23] == 'YES')

    # Build multi-channel action buttons dynamically
    if selected_client_id == 0:
        google_export_button = '<button class="btn-export disabled" onclick="alert(\'Please select a specific client from the dropdown above to export their Google Ads offline conversion CSV!\')" style="opacity:0.6; cursor:not-allowed; background-color: #bdc3c7; width: 100%;"> Export Disabled</button>'
        google_adjustments_button = '<button class="btn-export disabled" onclick="alert(\'Please select a specific client from the dropdown above to export their Google Ads offline conversion adjustments CSV!\')" style="opacity:0.6; cursor:not-allowed; background-color: #bdc3c7; width: 100%;"> Export Disabled</button>'
        microsoft_adjustments_button = '<button class="btn-export disabled" onclick="alert(\'Please select a specific client from the dropdown above to export their Microsoft Ads offline conversion adjustments CSV!\')" style="opacity:0.6; cursor:not-allowed; background-color: #bdc3c7; width: 100%;"> Export Disabled</button>'
        google_audience_button = '<button class="btn-export disabled" onclick="alert(\'Please select a specific client from the dropdown above to export their Google Customer Match list!\')" style="opacity:0.6; cursor:not-allowed; background-color: #bdc3c7; width: 100%;"> Export Disabled</button>'
        facebook_audience_button = '<button class="btn-export disabled" onclick="alert(\'Please select a specific client from the dropdown above to export their Meta Custom Audience list!\')" style="opacity:0.6; cursor:not-allowed; background-color: #bdc3c7; width: 100%;"> Export Disabled</button>'
        linkedin_audience_button = '<button class="btn-export disabled" onclick="alert(\'Please select a specific client from the dropdown above to export their LinkedIn List Match list!\')" style="opacity:0.6; cursor:not-allowed; background-color: #bdc3c7; width: 100%;"> Export Disabled</button>'
        facebook_export_button = '<button class="btn-export disabled" onclick="alert(\'Please select a specific client from the dropdown above to export their Facebook conversions CSV!\')" style="opacity:0.6; cursor:not-allowed; background-color: #bdc3c7; width: 100%;"> Export Disabled</button>'
        linkedin_export_button = '<button class="btn-export disabled" onclick="alert(\'Please select a specific client from the dropdown above to export their LinkedIn conversions CSV!\')" style="opacity:0.6; cursor:not-allowed; background-color: #bdc3c7; width: 100%;"> Export Disabled</button>'
        microsoft_export_button = '<button class="btn-export disabled" onclick="alert(\'Please select a specific client from the dropdown above to export their Microsoft conversions CSV!\')" style="opacity:0.6; cursor:not-allowed; background-color: #bdc3c7; width: 100%;"> Export Disabled</button>'
        tiktok_export_button = '<button class="btn-export disabled" onclick="alert(\'Please select a specific client from the dropdown above to export their TikTok conversions CSV!\')" style="opacity:0.6; cursor:not-allowed; background-color: #bdc3c7; width: 100%;"> Export Disabled</button>'
        twitter_export_button = '<button class="btn-export disabled" onclick="alert(\'Please select a specific client from the dropdown above to export their X Ads conversions CSV!\')" style="opacity:0.6; cursor:not-allowed; background-color: #bdc3c7; width: 100%;"> Export Disabled</button>'
        pinterest_export_button = '<button class="btn-export disabled" onclick="alert(\'Please select a specific client from the dropdown above to export their Pinterest conversions CSV!\')" style="opacity:0.6; cursor:not-allowed; background-color: #bdc3c7; width: 100%;"> Export Disabled</button>'
        snapchat_export_button = '<button class="btn-export disabled" onclick="alert(\'Please select a specific client from the dropdown above to export their Snapchat conversions CSV!\')" style="opacity:0.6; cursor:not-allowed; background-color: #bdc3c7; width: 100%;"> Export Disabled</button>'
        chatgpt_export_button = '<button class="btn-export disabled" onclick="alert(\'Please select a specific client from the dropdown above to export their ChatGPT Ads conversions CSV!\')" style="opacity:0.6; cursor:not-allowed; background-color: #bdc3c7; width: 100%;"> Export Disabled</button>'
        reddit_export_button = '<button class="btn-export disabled" onclick="alert(\'Please select a specific client from the dropdown above to export their Reddit Ads conversions CSV!\')" style="opacity:0.6; cursor:not-allowed; background-color: #bdc3c7; width: 100%;"> Export Disabled</button>'
    else:
        google_export_button = f'<a href="/dashboard/export/google?client_id={selected_client_id}" class="btn-export" style="background-color: #4285F4; text-align: center; text-decoration: none; width: 100%;"> Download Google CSV ({exportable_google})</a>'
        google_adjustments_button = f'<a href="/dashboard/export/google-adjustments?client_id={selected_client_id}" class="btn-export" style="background-color: #37474F; text-align: center; text-decoration: none; width: 100%;"> Download Google Adjustments CSV ({exportable_adjustments})</a>'
        microsoft_adjustments_button = f'<a href="/dashboard/export/microsoft-adjustments?client_id={selected_client_id}" class="btn-export" style="background-color: #00838F; text-align: center; text-decoration: none; width: 100%;"> Download Bing Adjustments CSV ({exportable_microsoft_adjustments})</a>'
        google_audience_button = f"""
        <div style="display: flex; flex-direction: column; gap: 8px; margin-top: 10px; width: 100%;">
            <a href="/dashboard/export/audience/google?client_id={selected_client_id}&segment=all" class="btn-export" style="background-color: #4285F4; text-align: center; text-decoration: none; font-size: 12px; font-weight: bold; border-radius: 5px; padding: 10px 12px;"> All Profiles (Leads & Buyers)</a>
            <a href="/dashboard/export/audience/google?client_id={selected_client_id}&segment=single" class="btn-export" style="background-color: #3b71ca; text-align: center; text-decoration: none; font-size: 12px; font-weight: bold; border-radius: 5px; padding: 10px 12px;"> Single-Time Buyers</a>
            <a href="/dashboard/export/audience/google?client_id={selected_client_id}&segment=multi" class="btn-export" style="background-color: #14a44d; text-align: center; text-decoration: none; font-size: 12px; font-weight: bold; border-radius: 5px; padding: 10px 12px;"> Repeat Buyers (2+ Sales)</a>
        </div>
        """
        facebook_audience_button = f"""
        <div style="display: flex; flex-direction: column; gap: 8px; margin-top: 10px; width: 100%;">
            <a href="/dashboard/export/audience/facebook?client_id={selected_client_id}&segment=all" class="btn-export" style="background-color: #1877F2; text-align: center; text-decoration: none; font-size: 12px; font-weight: bold; border-radius: 5px; padding: 10px 12px;"> All Profiles (Leads & Buyers)</a>
            <a href="/dashboard/export/audience/facebook?client_id={selected_client_id}&segment=single" class="btn-export" style="background-color: #3b5998; text-align: center; text-decoration: none; font-size: 12px; font-weight: bold; border-radius: 5px; padding: 10px 12px;"> Single-Time Buyers</a>
            <a href="/dashboard/export/audience/facebook?client_id={selected_client_id}&segment=multi" class="btn-export" style="background-color: #2e7d32; text-align: center; text-decoration: none; font-size: 12px; font-weight: bold; border-radius: 5px; padding: 10px 12px;"> Repeat Buyers (2+ Sales)</a>
        </div>
        """
        linkedin_audience_button = f"""
        <div style="display: flex; flex-direction: column; gap: 8px; margin-top: 10px; width: 100%;">
            <a href="/dashboard/export/audience/linkedin?client_id={selected_client_id}&segment=all" class="btn-export" style="background-color: #0A66C2; text-align: center; text-decoration: none; font-size: 12px; font-weight: bold; border-radius: 5px; padding: 10px 12px;"> All Profiles (Leads & Buyers)</a>
            <a href="/dashboard/export/audience/linkedin?client_id={selected_client_id}&segment=single" class="btn-export" style="background-color: #0077b5; text-align: center; text-decoration: none; font-size: 12px; font-weight: bold; border-radius: 5px; padding: 10px 12px;"> Single-Time Buyers</a>
            <a href="/dashboard/export/audience/linkedin?client_id={selected_client_id}&segment=multi" class="btn-export" style="background-color: #155724; text-align: center; text-decoration: none; font-size: 12px; font-weight: bold; border-radius: 5px; padding: 10px 12px;"> Repeat Buyers (2+ Sales)</a>
        </div>
        """
        facebook_export_button = f'<a href="/dashboard/export/facebook?client_id={selected_client_id}" class="btn-export" style="background-color: #1877F2; text-align: center; text-decoration: none; width: 100%;"> Download Meta CSV ({exportable_facebook})</a>'
        linkedin_export_button = f'<a href="/dashboard/export/linkedin?client_id={selected_client_id}" class="btn-export" style="background-color: #0A66C2; text-align: center; text-decoration: none; width: 100%;"> Download LinkedIn CSV ({exportable_linkedin})</a>'
        microsoft_export_button = f'<a href="/dashboard/export/microsoft?client_id={selected_client_id}" class="btn-export" style="background-color: #00A4EF; text-align: center; text-decoration: none; width: 100%;"> Download Bing CSV ({exportable_microsoft})</a>'
        tiktok_export_button = f'<a href="/dashboard/export/tiktok?client_id={selected_client_id}" class="btn-export" style="background-color: #010101; text-align: center; text-decoration: none; width: 100%;"> Download TikTok CSV ({exportable_tiktok})</a>'
        twitter_export_button = f'<a href="/dashboard/export/twitter?client_id={selected_client_id}" class="btn-export" style="background-color: #15202B; text-align: center; text-decoration: none; width: 100%;"> Download X Ads CSV ({exportable_twitter})</a>'
        pinterest_export_button = f'<a href="/dashboard/export/pinterest?client_id={selected_client_id}" class="btn-export" style="background-color: #E60023; text-align: center; text-decoration: none; width: 100%;"> Download Pinterest CSV ({exportable_pinterest})</a>'
        snapchat_export_button = f'<a href="/dashboard/export/snapchat?client_id={selected_client_id}" class="btn-export" style="background-color: #E9B800; color: #000; text-align: center; text-decoration: none; width: 100%;"> Download Snapchat CSV ({exportable_snapchat})</a>'
        chatgpt_export_button = f'<a href="/dashboard/export/chatgpt?client_id={selected_client_id}" class="btn-export" style="background-color: #10a37f; text-align: center; text-decoration: none; width: 100%;"> Download ChatGPT CSV ({exportable_chatgpt})</a>'
        reddit_export_button = f'<a href="/dashboard/export/reddit?client_id={selected_client_id}" class="btn-export" style="background-color: #FF4500; text-align: center; text-decoration: none; width: 100%;"> Download Reddit CSV ({exportable_reddit})</a>'

    # Conditionally show the Client header column
    client_th_html = '<th>Client Account</th>' if selected_client_id == 0 else ''
    admin_link_html = ""
    if email in ADMIN_EMAILS:
        admin_link_html = ' | <a href="/admin/users" style="color: #2e7d32; text-decoration: none; font-weight: bold; margin-left: 5px;">️ Admin User Directory</a>'
        

    # Define Global Instructions and Adjustments Modals (Rendered for ALL users)
    global_modals_html = """
        <!-- Upload Instructions Modal -->
        <div id="upload-instructions-modal" style="display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background-color: rgba(0,0,0,0.5); z-index: 10000; align-items: center; justify-content: center;">
            <div style="background: white; border-radius: 12px; box-shadow: 0 10px 30px rgba(0,0,0,0.2); max-width: 550px; width: 90%; text-align: left; overflow: hidden; display: flex; flex-direction: column;">
                <!-- Header -->
                <div id="ins_modal_header" style="background: #1a237e; color: white; padding: 20px; display: flex; justify-content: space-between; align-items: center;">
                    <h3 id="ins_modal_title" style="margin: 0; font-size: 16px; color: white; display: flex; align-items: center; gap: 8px;"> Upload Instructions</h3>
                    <span onclick="closeUploadInstructionsModal()" style="font-size: 24px; font-weight: bold; cursor: pointer; color: white; opacity: 0.8;">&times;</span>
                </div>
                <!-- Body -->
                <div style="padding: 25px; font-size: 14px; color: #333; margin: 0; overflow-y: auto; max-height: 70vh;">
                    <div style="margin-bottom: 15px; background: #e8eaf6; padding: 12px; border-radius: 6px; border-left: 4px solid #1a237e;">
                        <span style="font-weight: bold; color: #1a237e; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; display: block; margin-bottom: 4px;"> TARGET UPLOAD LOCATION:</span>
                        <strong id="ins_platform_path" style="display: block; font-size: 13px; color: #333; line-height: 1.4;"></strong>
                    </div>
                    
                    <div style="margin-top: 15px;">
                        <span style="font-weight: bold; color: #666; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; display: block; margin-bottom: 8px;"> STEP-BY-STEP WORKFLOW:</span>
                        <ol id="ins_steps_list" style="padding-left: 20px; font-size: 13px; line-height: 1.6; margin: 0; color: #333;"></ol>
                    </div>
                </div>
                <!-- Footer -->
                <div style="background: #f1f3f4; padding: 15px; display: flex; justify-content: flex-end; border-top: 1px solid #eaeaea;">
                    <button onclick="closeUploadInstructionsModal()" style="background: #1a237e; color: white; border: none; padding: 8px 18px; border-radius: 5px; font-weight: bold; cursor: pointer; transition: background 0.2s; font-size: 13px;">Got It, Close</button>
                </div>
            </div>
        </div>

        <!-- Conversion Adjustment Modal -->
        <div id="adjustment-modal" style="display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background-color: rgba(0,0,0,0.5); z-index: 10000; align-items: center; justify-content: center;">
            <div style="background: white; border-radius: 12px; box-shadow: 0 10px 30px rgba(0,0,0,0.2); max-width: 480px; width: 90%; text-align: left; overflow: hidden;">
                <!-- Header -->
                <div style="background: #1a237e; color: white; padding: 20px; display: flex; justify-content: space-between; align-items: center;">
                    <h3 style="margin: 0; font-size: 16px; color: white; display: flex; align-items: center; gap: 8px;">✏️ Adjust Offline Conversion</h3>
                    <span onclick="closeAdjustmentModal()" style="font-size: 24px; font-weight: bold; cursor: pointer; color: white; opacity: 0.8;">&times;</span>
                </div>
                <!-- Body -->
                <form id="adjustment-form" onsubmit="submitAdjustment(event)" style="padding: 25px; font-size: 14px; color: #333; margin: 0;">
                    <input type="hidden" id="adj_session_id">
                    
                    <div style="margin-bottom: 15px;">
                        <span style="color: #666; font-size: 13px;">Customer Lead:</span>
                        <strong id="adj_customer_name" style="display: block; font-size: 15px; color: #1a237e; margin-top: 4px;"></strong>
                    </div>
                    
                    <div style="margin-bottom: 15px;">
                        <span style="color: #666; font-size: 13px;">Original Recorded Sale Value:</span>
                        <strong id="adj_original_value" style="display: block; font-size: 15px; color: #2e7d32; margin-top: 4px;"></strong>
                    </div>
                    
                    <div style="margin-bottom: 20px;">
                        <label style="font-weight: bold; font-size: 13px; display: block; color: #1a237e; margin-bottom: 8px;">Select Adjustment Strategy:</label>
                        <div style="display: flex; flex-direction: column; gap: 10px;">
                            <label style="display: flex; align-items: flex-start; gap: 8px; cursor: pointer; background: #fdfdfd; border: 1px solid #e0e0e0; padding: 10px 12px; border-radius: 6px; margin: 0;">
                                <input type="radio" name="adj_strategy" value="RETRACT" onclick="toggleAdjValueField(false)" checked style="margin-top: 3px;">
                                <div>
                                    <strong style="color: #c62828; font-size: 13px;"> Refund / Cancel Sale (Retract)</strong>
                                    <span style="display: block; font-size: 11px; color: #666; margin-top: 2px;">Instructs Google to completely delete/cancel this conversion from your ad optimization datasets.</span>
                                </div>
                            </label>
                            <label style="display: flex; align-items: flex-start; gap: 8px; cursor: pointer; background: #fdfdfd; border: 1px solid #e0e0e0; padding: 10px 12px; border-radius: 6px; margin: 0;">
                                <input type="radio" name="adj_strategy" value="RESTATE" onclick="toggleAdjValueField(true)" style="margin-top: 3px;">
                                <div>
                                    <strong style="color: #2e7d32; font-size: 13px;"> Restate Transaction Value (Restate)</strong>
                                    <span style="display: block; font-size: 11px; color: #666; margin-top: 2px;">Corrects or updates the transaction revenue value. Perfect for partial refunds or contract upsells.</span>
                                </div>
                            </label>
                        </div>
                    </div>
                    
                    <div id="adj_value_container" style="display: none; margin-bottom: 20px;">
                        <label for="adj_new_value" style="font-weight: bold; font-size: 13px; display: block; color: #1a237e; margin-bottom: 6px;">New Corrected Value ($ USD):</label>
                        <input type="number" id="adj_new_value" step="0.01" min="0" placeholder="e.g. 1200.00" style="width: 100%; padding: 10px; border-radius: 6px; border: 1px solid #ced4da; box-sizing: border-box; font-weight: bold;">
                    </div>
                    
                    <div style="display: flex; justify-content: flex-end; gap: 10px; border-top: 1px solid #eaeaea; padding-top: 20px; margin-top: 20px;">
                        <button type="button" onclick="closeAdjustmentModal()" style="background: #f1f3f4; color: #333; border: none; padding: 10px 18px; border-radius: 6px; font-weight: bold; cursor: pointer;">Cancel</button>
                        <button type="submit" style="background: #1a237e; color: white; border: none; padding: 10px 24px; border-radius: 6px; font-weight: bold; cursor: pointer;"> Save Adjustment</button>
                    </div>
                </form>
            </div>
        </div>
    """

    global_modals_script_html = """
        <script>
        function toggleCustomDateInputs() {
            const rangeSelect = document.getElementById('date_range_select');
            const customContainer = document.getElementById('custom_date_container');
            if (rangeSelect && customContainer) {
                if (rangeSelect.value === 'custom') {
                    customContainer.style.display = 'flex';
                } else {
                    customContainer.style.display = 'none';
                }
            }
        }

        function setQuickDate(rangeVal) {
            const rangeSelect = document.getElementById('date_range_select');
            if (rangeSelect) {
                rangeSelect.value = rangeVal;
                toggleCustomDateInputs();
                filterDashboard();
            }
        }

        function exportFilteredLeads(e) {
            if (e) e.preventDefault();
            const clientSelect = document.getElementById('dashboard_client_select');
            const rangeSelect = document.getElementById('date_range_select');
            const startInput = document.getElementById('start_date_input');
            const endInput = document.getElementById('end_date_input');
            
            const clientId = clientSelect ? clientSelect.value : '';
            const dateRange = rangeSelect ? rangeSelect.value : 'all';
            
            let url = `/dashboard/export/leads?client_id=${clientId}&date_range=${dateRange}`;
            
            if (dateRange === 'custom') {
                if (startInput && startInput.value) {
                    url += `&start_date=${encodeURIComponent(startInput.value)}`;
                }
                if (endInput && endInput.value) {
                    url += `&end_date=${encodeURIComponent(endInput.value)}`;
                }
            }
            
            window.location.href = url;
        }

        function filterDashboard() {
            const clientSelect = document.getElementById('dashboard_client_select');
            const rangeSelect = document.getElementById('date_range_select');
            const startInput = document.getElementById('start_date_input');
            const endInput = document.getElementById('end_date_input');
            
            const clientId = clientSelect ? clientSelect.value : '';
            const dateRange = rangeSelect ? rangeSelect.value : 'all';
            
            let url = `/dashboard?client_id=${clientId}&date_range=${dateRange}`;
            
            if (dateRange === 'custom') {
                if (startInput && startInput.value) {
                    url += `&start_date=${encodeURIComponent(startInput.value)}`;
                }
                if (endInput && endInput.value) {
                    url += `&end_date=${encodeURIComponent(endInput.value)}`;
                }
            }
            
            window.location.href = url;
        }

        function openAdjustmentModal(sessionId, customerName, originalValue) {
                document.getElementById('adj_session_id').value = sessionId;
                document.getElementById('adj_customer_name').innerText = customerName;
                document.getElementById('adj_original_value').innerText = `$$${originalValue.toFixed(2)}`;
                document.getElementById('adj_new_value').value = '';
                document.querySelectorAll('input[name="adj_strategy"]')[0].checked = true;
                document.getElementById('adj_value_container').style.display = 'none';
                document.getElementById('adjustment-modal').style.display = 'flex';
            }

            function closeAdjustmentModal() {
                document.getElementById('adjustment-modal').style.display = 'none';
            }

            function openUploadInstructions(platform) {
                const modal = document.getElementById('upload-instructions-modal');
                const titleEl = document.getElementById('ins_modal_title');
                const headerEl = document.getElementById('ins_modal_header');
                const pathEl = document.getElementById('ins_platform_path');
                const stepsEl = document.getElementById('ins_steps_list');
                
                let title = "";
                let headerBg = "#1a237e";
                let path = "";
                let steps = [];
                
                if (platform === 'google') {
                    title = " Google Ads Upload Instructions";
                    headerBg = "#4285F4";
                    path = "Tools and Settings ➡️ Goals ➡️ Conversions ➡️ Uploads";
                    steps = [
                        "Click the <strong>Uploads</strong> tab from the left-hand navigation panel of the conversions screen.",
                        "Click the blue <strong>plus (+)</strong> button to create a new upload session.",
                        "Under <strong>Source</strong>, select <strong>Upload a file</strong> and choose the downloaded Google conversions CSV file.",
                        "Select <strong>Apply</strong> or click <strong>Preview</strong> to verify GCLID click mappings and timestamps before applying."
                    ];
                } else if (platform === 'facebook') {
                    title = " Meta / Facebook Offline Conversions Upload Guide";
                    headerBg = "#1877F2";
                    path = "Meta Events Manager ➡️ Data Sources";
                    steps = [
                        "Select the <strong>Offline Event Set</strong> matching your target active pixel campaigns.",
                        "Click the <strong>Upload Events</strong> button inside the events set dashboard.",
                        "Select and upload your downloaded Meta Offline Conversions CSV file.",
                        "Verify and map key customer fields (like SHA-256 hashed email, phone, name) alongside the click ID (<code>fbclid</code>).",
                        "Click <strong>Start Upload</strong> to transmit transaction attribution data to Meta."
                    ];
                } else if (platform === 'linkedin') {
                    title = " LinkedIn Offline Conversions Upload Guide";
                    headerBg = "#0A66C2";
                    path = "LinkedIn Campaign Manager ➡️ Analyze ➡️ Conversion Tracking";
                    steps = [
                        "Click the <strong>Conversions</strong> tab inside the Campaign Manager analyzer.",
                        "Click <strong>Create Conversion</strong> and define an <strong>Offline Upload (CSV)</strong> goal mapping (e.g., LeadGroove Lead/Sale).",
                        "Click <strong>Upload Conversions</strong> in the upper right corner of the tracking summary panel.",
                        "Choose the downloaded LinkedIn CSV file, associate your offline conversion goal, and click <strong>Upload</strong>."
                    ];
                } else if (platform === 'microsoft') {
                    title = " Microsoft (Bing) Ads Offline Conversions Guide";
                    headerBg = "#00A4EF";
                    path = "Microsoft Advertising Dashboard ➡️ Tools ➡️ Conversion Goals";
                    steps = [
                        "Click the <strong>Offline Conversions</strong> tab under Conversion Goals management.",
                        "Click the <strong>Upload</strong> button to launch the MS Ads import wizard.",
                        "Select your downloaded Microsoft conversions CSV file.",
                        "Ensure the TimeZone is aligned (defaults to UTC/+00:00), and click <strong>Apply</strong> to complete the process."
                    ];
                } else if (platform === 'tiktok') {
                    title = " TikTok Ads Offline Event Upload Instructions";
                    headerBg = "#010101";
                    path = "TikTok Ads Manager ➡️ Tools ➡️ Events ➡️ Offline Events";
                    steps = [
                        "Select your configured active Offline Event Set.",
                        "Click the <strong>Upload Offline Conversions (CSV)</strong> button.",
                        "Choose the downloaded TikTok conversions CSV file.",
                        "Verify that click ID (<code>ttclid</code>), conversion event name, value, and timestamp map cleanly, and click <strong>Submit</strong>."
                    ];
                } else if (platform === 'twitter') {
                    title = " X (Twitter) Ads Offline Upload Guide";
                    headerBg = "#15202B";
                    path = "X Ads Manager ➡️ Tools ➡️ Events Manager";
                    steps = [
                        "Select your target offline attribution event set.",
                        "Click **Upload Events (CSV)**.",
                        "Select and upload the downloaded X Ads conversions CSV.",
                        "Review column mappings (click ID, value, timestamp) and click <strong>Apply</strong> to queue conversion attribution."
                    ];
                } else if (platform === 'snapchat') {
                    title = " Snapchat Ads Offline Conversions Upload Instructions";
                    headerBg = "#E9B800";
                    path = "Snapchat Ads Manager ➡️ Assets ➡️ Events Manager";
                    steps = [
                        "Select your active Snapchat Pixel profile.",
                        "Choose the **Upload Event Log** option inside event actions.",
                        "Drop your downloaded Snapchat conversions CSV file.",
                        "Verify event matching parameters (Click ID, Event Name) and click <strong>Process</strong> to trigger matching."
                    ];
                } else if (platform === 'pinterest') {
                    title = " Pinterest Ads Offline Event Upload Guide";
                    headerBg = "#E60023";
                    path = "Pinterest Ads Manager ➡️ Ads ➡️ Conversions ➡️ Offline Conversions";
                    steps = [
                        "Select your active Pinterest Tag offline dataset.",
                        "Click the <strong>Upload Offline Conversions (CSV)</strong> button.",
                        "Choose your downloaded Pinterest conversions CSV file.",
                        "Confirm column metrics (PIN Click ID, Value, Currency) and select <strong>Apply</strong>."
                    ];
                } else if (platform === 'chatgpt') {
                    title = " ChatGPT Ads Conversion Upload Instructions";
                    headerBg = "#10a37f";
                    path = "ChatGPT Ads Campaign Manager ➡️ Conversion Event Manager";
                    steps = [
                        "Click on the **Upload Offline CSV Match** button.",
                        "Select your downloaded ChatGPT Ads conversions CSV file.",
                        "Verify target mapping fields (ChatGPT Click ID, Event Name) and click <strong>Apply</strong>."
                    ];
                } else if (platform === 'reddit') {
                    title = " Reddit Ads Offline Conversion Upload Guide";
                    headerBg = "#FF4500";
                    path = "Reddit Ads Manager ➡️ Events Manager ➡️ Offline Conversions";
                    steps = [
                        "Click the <strong>Upload Conversions (CSV)</strong> button inside Events Manager.",
                        "Select your downloaded Reddit Ads conversions CSV file.",
                        "Verify field mappings (<code>rdt_cid</code>, Event Name, Value, Currency) and click <strong>Submit</strong>."
                    ];
                } else if (platform === 'google-adjustments') {
                    title = "⚙️ Google Ads Offline Adjustments Guide";
                    headerBg = "#37474F";
                    path = "Goals ➡️ Conversions ➡️ Uploads ➡️ Adjustments";
                    steps = [
                        "Click the <strong>Adjustments</strong> tab at the top of your Google Ads Uploads menu.",
                        "Click the blue <strong>plus (+)</strong> button.",
                        "Select **Upload a file** and choose the downloaded Google Offline Adjustments CSV file.",
                        "Click **Apply** or **Preview** to verify retracting or restating of matching click transactions."
                    ];
                } else if (platform === 'microsoft-adjustments') {
                    title = "⚙️ Microsoft (Bing) Ads Offline Adjustments Guide";
                    headerBg = "#00838F";
                    path = "Microsoft Advertising Dashboard ➡️ Tools ➡️ Conversion Goals";
                    steps = [
                        "Click the <strong>Offline Conversions</strong> tab under Conversion Goals management inside Microsoft Advertising.",
                        "Click the <strong>Upload</strong> button and select <strong>Offline Conversion Adjustments</strong>.",
                        "Select your downloaded Microsoft Offline Adjustments CSV file.",
                        "Ensure column headers match <code>Microsoft Click ID</code>, <code>Conversion Name</code>, <code>Conversion Time</code>, <code>Adjustment Type</code> (RETRACT or RESTATE), <code>Adjustment Time</code>, <code>Adjusted Value</code>, and <code>Microsoft Account ID</code>.",
                        "Click <strong>Apply</strong> to commit your conversion retractions or value updates."
                    ];
                }
                
                titleEl.innerHTML = title;
                headerEl.style.backgroundColor = headerBg;
                pathEl.innerHTML = path;
                
                stepsEl.innerHTML = "";
                steps.forEach(step => { 
                    const li = document.createElement("li");
                    li.style.marginBottom = "8px";
                    li.innerHTML = step;
                    stepsEl.appendChild(li);
                });
                
                modal.style.display = "flex";
            }

            function closeUploadInstructionsModal() { 
                document.getElementById('upload-instructions-modal').style.display = 'none';
            }

            function toggleAdjValueField(show) { 
                document.getElementById('adj_value_container').style.display = show ? 'block' : 'none';
                if (show) { 
                    document.getElementById('adj_new_value').required = true;
                    document.getElementById('adj_new_value').focus();
                } else { 
                    document.getElementById('adj_new_value').required = false;
                }
            }

            async function submitAdjustment(event) { 
                event.preventDefault();
                const sessionId = document.getElementById('adj_session_id').value;
                const strategy = document.querySelector('input[name="adj_strategy"]:checked').value;
                const newValue = strategy === 'RESTATE' ? parseFloat(document.getElementById('adj_new_value').value) : 0.0;
                
                if (strategy === 'RESTATE' && (isNaN(newValue) || newValue < 0)) { 
                    alert('Please enter a valid positive number for the restated value.');
                    return;
                }
                
                try { 
                    const response = await fetch('/dashboard/adjust-sale', { 
                        method: 'POST',
                        headers: { 
                            'Content-Type': 'application/json' 
                        },
                        body: JSON.stringify({ 
                            session_id: parseInt(sessionId),
                            adjustment_type: strategy,
                            adjusted_value: newValue
                        })
                    });
                    
                    const data = await response.json();
                    if (response.ok) { 
                        alert('Success: Sale conversion adjustment saved cleanly! This correction will be uploaded to Google Ads next time you run adjustments sync.');
                        closeAdjustmentModal();
                        window.location.reload();
                    } else { 
                        throw new Error(data.detail || 'Failed to submit adjustment.');
                    }
                } catch (error) { 
                    alert('Error submitting adjustment: ' + error.message);
                }
            }
        </script>
    """

    # Configure upload box visibility
    if user_role == "full":
        # Check target clients options
        if selected_client_id == 0:
            upload_client_selector_html = """
            <div style="margin-bottom: 25px; display: flex; align-items: center; justify-content: center; gap: 10px;">
                <span style="font-weight: bold; color: #1a237e; font-size: 13px;">Target Client Account:</span>
                <select id="upload_client_id" style="padding: 6px 12px; font-size: 13px; border-radius: 4px; border: 1px solid #9fa8da; font-weight: 600; outline: none; cursor: pointer; color: #1a237e; background: white;">
            """
            for c_id, c_name, *rest in clients:
                sel_up = 'selected' if c_id == selected_client_id else ''
                upload_client_selector_html += f'<option value="{c_id}" {sel_up}> {c_name}</option>'
            upload_client_selector_html += """
                </select>
            </div>
            """
        else:
            upload_client_selector_html = f'<input type="hidden" id="upload_client_id" value="{selected_client_id}">'

        upload_box_html = f"""
        <!-- Spreadsheet Formatting Instructions Card -->
        <div style="background: #ffffff; border: 1px solid #e0e6ed; border-left: 4px solid #1a237e; border-radius: 8px; padding: 18px 20px; margin-bottom: 20px; text-align: left; box-shadow: 0 2px 6px rgba(0,0,0,0.03);">
            <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; flex-wrap: wrap; gap: 8px;">
                <strong style="color: #1a237e; font-size: 14px; display: flex; align-items: center; gap: 6px;">
                     How to Format Your Sales Spreadsheet (CSV or Excel)
                </strong>
                <span style="font-size: 11px; background: #e8eaf6; color: #1a237e; padding: 3px 8px; border-radius: 4px; font-weight: bold;">
                    Supported Formats: .CSV, .XLSX, .XLS
                </span>
            </div>
            
            <p style="font-size: 12px; color: #495057; margin: 0 0 12px 0; line-height: 1.5;">
                Ensure <strong>Row 1</strong> of your sheet contains clear column headers so LeadGrove can automatically map caller phone numbers and purchase revenue to your ad campaigns:
            </p>
            
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 10px; margin-bottom: 12px;">
                <div style="background: #f8f9fa; border: 1px solid #e9ecef; border-radius: 6px; padding: 10px;">
                    <span style="font-size: 11px; font-weight: bold; color: #1a237e; display: block; margin-bottom: 3px;"> Phone Column (Required)</span>
                    <span style="font-size: 11px; color: #666; display: block;">Header: <code>Phone</code>, <code>Telephone</code>, <code>Mobile</code>, or <code>Contact</code></span>
                    <small style="font-size: 10px; color: #888; display: block; margin-top: 3px;">Used for exact phone matching against CallRail call logs.</small>
                </div>
                
                <div style="background: #f8f9fa; border: 1px solid #e9ecef; border-radius: 6px; padding: 10px;">
                    <span style="font-size: 11px; font-weight: bold; color: #1a237e; display: block; margin-bottom: 3px;">✉️ Email Column (Optional/Alt)</span>
                    <span style="font-size: 11px; color: #666; display: block;">Header: <code>Email</code>, <code>E-mail</code>, or <code>Mail</code></span>
                    <small style="font-size: 10px; color: #888; display: block; margin-top: 3px;">Secondary contact identifier for lead matching.</small>
                </div>
                
                <div style="background: #f8f9fa; border: 1px solid #e9ecef; border-radius: 6px; padding: 10px;">
                    <span style="font-size: 11px; font-weight: bold; color: #2e7d32; display: block; margin-bottom: 3px;"> Sale Amount Column</span>
                    <span style="font-size: 11px; color: #666; display: block;">Header: <code>Amount</code>, <code>Value</code>, <code>Revenue</code>, <code>Price</code>, or <code>Total</code></span>
                    <small style="font-size: 10px; color: #888; display: block; margin-top: 3px;">Purchase dollar value uploaded to ad networks (e.g. <code>450.00</code>).</small>
                </div>
                
                <div style="background: #f8f9fa; border: 1px solid #e9ecef; border-radius: 6px; padding: 10px;">
                    <span style="font-size: 11px; font-weight: bold; color: #495057; display: block; margin-bottom: 3px;"> Name & Company (Optional)</span>
                    <span style="font-size: 11px; color: #666; display: block;">Header: <code>Name</code>, <code>Customer</code>, <code>Company</code></span>
                    <small style="font-size: 10px; color: #888; display: block; margin-top: 3px;">Used for dashboard logs and smart fuzzy matching.</small>
                </div>
            </div>
            
            <div style="font-size: 11px; color: #495057; background: #e8eaf6; padding: 8px 12px; border-radius: 4px; border: 1px dashed #3f51b5;">
                 <strong>Formatting Tip:</strong> Phone numbers can include dashes or parentheses (LeadGrove normalizes them automatically), and currency values can include <code>$</code> symbols or commas.
            </div>
        </div>

        <!-- Drag & Drop Ingestion Box -->
        <div id="drop-zone" style="background: #f8f9fc; border: 2px dashed #1a237e; border-radius: 8px; padding: 25px; text-align: center; margin-bottom: 30px; cursor: pointer; transition: all 0.2s; position: relative;">
            <div id="drop-zone-content">
                <span style="font-size: 32px; display: block; margin-bottom: 10px;"></span>
                <strong style="color: #1a237e; font-size: 15px; display: block;">Drag & drop your Customer Sales Spreadsheet (CSV or Excel)</strong>
                <span style="color: #666; font-size: 13px; display: block; margin-top: 5px;">Or click here to browse and upload from your computer</span>
                <small style="color: #888; font-size: 11px; display: block; margin-top: 10px; font-style: italic;">Supports exact phone/email matching & smart fuzzy name/company matching</small>
            </div>
            <input type="file" id="csv-file-input" accept=".csv, .xlsx, .xls" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; opacity: 0; cursor: pointer;">
        </div>
        
        {upload_client_selector_html}
        
        <!-- Ingestion Success Modal Overlay -->
        <div id="upload-success-modal" style="display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background-color: rgba(0,0,0,0.5); z-index: 10000; align-items: center; justify-content: center;">
            <div style="background: white; border-radius: 12px; box-shadow: 0 10px 30px rgba(0,0,0,0.2); max-width: 480px; width: 90%; text-align: center; overflow: hidden;">
                <!-- Header -->
                <div style="background: #1a237e; color: white; padding: 20px;">
                    <span style="font-size: 40px;"></span>
                    <h3 style="margin: 10px 0 0 0; font-size: 18px;">Sales Spreadsheet Processed!</h3>
                </div>
                <!-- Body -->
                <div style="padding: 25px; text-align: left; font-size: 14px; color: #333; line-height: 1.6;">
                    <p id="modal-message" style="margin-top: 0; font-weight: 600; text-align: center; color: #1b5e20;"></p>
                    <div style="background: #f8f9fa; border: 1px solid #e0e0e0; border-radius: 8px; padding: 15px; margin-top: 15px;">
                        <strong style="display: block; margin-bottom: 8px; font-size: 12px; color: #666; text-transform: uppercase; letter-spacing: 0.5px;"> Ingestion Stats:</strong>
                        <ul style="margin: 0; padding-left: 20px;">
                            <li>Processed Rows: <strong id="stat-processed">0</strong></li>
                            <li>Successful Matches: <strong id="stat-matches" style="color: #2e7d32;">0</strong></li>
                            <li>New Organic Logged: <strong id="stat-organic" style="color: #1565c0;">0</strong></li>
                            <li>Row Failures/Errors: <strong id="stat-errors" style="color: #c62828;">0</strong></li>
                        </ul>
                    </div>
                </div>
                <!-- Footer -->
                <div style="background: #f1f3f4; padding: 15px; display: flex; justify-content: center;">
                    <button onclick="closeUploadModal()" style="background: #1a237e; color: white; border: none; padding: 10px 24px; border-radius: 6px; font-weight: bold; cursor: pointer; transition: background 0.2s;">Great, Refresh Dashboard!</button>
                </div>
            </div>
        </div>
        """
        upload_box_script_html = """
        <script>
            const dropZone = document.getElementById('drop-zone');
            const fileInput = document.getElementById('csv-file-input');

            // Add drag & drop event listeners
            dropZone.addEventListener('dragover', (e) => {
                e.preventDefault();
                dropZone.style.background = '#e8eaf6';
                dropZone.style.borderColor = '#3f51b5';
                dropZone.style.transform = 'scale(1.01)';
            });

            dropZone.addEventListener('dragleave', (e) => {
                e.preventDefault();
                dropZone.style.background = '#f8f9fc';
                dropZone.style.borderColor = '#1a237e';
                dropZone.style.transform = 'scale(1)';
            });

            dropZone.addEventListener('drop', (e) => {
                e.preventDefault();
                dropZone.style.background = '#f8f9fc';
                dropZone.style.borderColor = '#1a237e';
                dropZone.style.transform = 'scale(1)';
                
                const files = e.dataTransfer.files;
                if (files.length > 0) {
                    fileInput.files = files;
                    handleFileUpload(files[0]);
                }
            });

            fileInput.addEventListener('change', (e) => {
                if (fileInput.files.length > 0) {
                    handleFileUpload(fileInput.files[0]);
                }
            });

            async function handleFileUpload(file) {
                const clientIdSelect = document.getElementById('upload_client_id');
                const clientId = clientIdSelect ? clientIdSelect.value : "{selected_client_id}";
                
                if (clientId === "0") {
                    alert("Please select a specific client account from the Target Client selector inside the upload box first!");
                    return;
                }

                // Show visual loading
                const content = document.getElementById('drop-zone-content');
                const originalHTML = content.innerHTML;
                content.innerHTML = `
                    <span style="font-size: 32px; display: block; margin-bottom: 10px; animation: spin 2s linear infinite; width: 40px; margin: 0 auto 10px auto;">⏳</span>
                    <strong style="color: #1a237e; font-size: 15px; display: block;">Processing spreadsheet, performing fuzzy matching audits...</strong>
                    <span style="color: #666; font-size: 13px; display: block; margin-top: 5px;">Do not close your browser or navigate away.</span>
                `;
                dropZone.style.pointerEvents = 'none';

                const formData = new FormData();
                formData.append('file', file);
                formData.append('client_id', clientId);

                try {
                    const response = await fetch('/dashboard/upload-sales', {
                        method: 'POST',
                        body: formData
                    });

                    const data = await response.json();

                    if (response.ok) {
                        // Show success modal
                        document.getElementById('modal-message').innerText = `Spreadsheet successfully parsed for client ID #${clientId}!`;
                        document.getElementById('stat-processed').innerText = data.stats.processed;
                        document.getElementById('stat-matches').innerText = data.stats.successful_matches;
                        document.getElementById('stat-organic').innerText = data.stats.organic_logged;
                        document.getElementById('stat-errors').innerText = data.stats.errors;

                        document.getElementById('upload-success-modal').style.display = 'flex';
                    } else {
                        throw new Error(data.detail || 'An error occurred during file parsing.');
                    }
                } catch (error) {
                    alert('Upload Error: ' + error.message);
                } finally {
                    // Reset Drop Zone content
                    content.innerHTML = originalHTML;
                    dropZone.style.pointerEvents = 'auto';
                    fileInput.value = ''; // Reset file input
                }
            }

            function closeUploadModal() {
                document.getElementById('upload-success-modal').style.display = 'none';
                window.location.reload();
            }
        </script>
        """.replace("{selected_client_id}", str(selected_client_id))
    else:
        upload_box_html = ""
        upload_box_script_html = ""

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
            <title>Offline Lead & Conversion Dashboard </title>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                * {{ box-sizing: border-box; }} body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif; background-color: #f4f6f9; color: #333; margin: 0; padding: 20px; }}
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
                .badge-source {{ font-size: 11px; padding: 4px 8px; border-radius: 6px; font-weight: bold; border: 1px solid transparent; display: inline-flex; align-items: center; gap: 4px; }}
                .badge-source.badge-call {{ background: #e3f2fd; color: #0d47a1; border-color: #bbdefb; }}
                .badge-source.badge-form {{ background: #f3e5f5; color: #4a148c; border-color: #e1bee7; }}
                .client-badge {{ background: #eceff1; color: #37474f; font-size: 11px; padding: 3px 8px; border-radius: 4px; font-weight: bold; border: 1px solid #cfd8dc; }}
                
                .btn-export {{ display: block; color: white; padding: 10px 12px; border-radius: 5px; font-weight: bold; font-size: 13px; border: none; transition: filter 0.2s; cursor: pointer; box-sizing: border-box; width: 100%; text-align: center; }}
                .btn-export:hover {{ filter: brightness(0.9); }}
                .btn-add-client {{ display: inline-block; background-color: #1a237e; color: white; padding: 10px 18px; text-decoration: none; border-radius: 5px; font-weight: bold; font-size: 14px; border: none; transition: background 0.2s; cursor: pointer; }}
                .btn-add-client:hover {{ background-color: #0d1b2a; }}
                .btn-settings {{ display: inline-block; background-color: #607d8b; color: white; padding: 10px 18px; text-decoration: none; border-radius: 5px; font-weight: bold; font-size: 14px; border: none; transition: background 0.2s; cursor: pointer; }}
                .btn-settings:hover {{ background-color: #455a64; }}

                @keyframes spin {{
                    0% {{ transform: rotate(0deg); }}
                    100% {{ transform: rotate(360deg); }}
                }}
            </style>
        </head>
        <body>
            <div class="container">
                {user_header_bar}
                <header>
                    <div>
                        <h1>{client_name_header} </h1>
                        <p style="margin: 5px 0 0 0; color: #666; font-size: 14px;">Google Ads Account: <strong>{client_ads_id}</strong> | Multi-Tenant Agency Engine</p>
                    </div>
                    
                    <div style="display: flex; gap: 10px; align-items: center; flex-wrap: wrap;">
        <div style="background: white; padding: 18px 20px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.05); margin-bottom: 25px; border-left: 4px solid #1a237e; width: 100%;">
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 15px;">
                
                <!-- Client Selection Dropdown -->
                <div style="display: flex; align-items: center; gap: 10px;">
                    <label for="dashboard_client_select" style="font-weight: bold; color: #1a237e; font-size: 13px;"> Active Client Profile:</label>
                    <select id="dashboard_client_select" class="client-select" onchange="filterDashboard()" style="padding: 8px 14px; border-radius: 6px; border: 1px solid #1a237e; font-weight: bold; font-size: 13px; cursor: pointer; background: #f8f9fc; color: #1a237e;">
                        {dropdown_options}
                    </select>
                </div>

                <!-- Date Range Filters -->
                <div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap;">
                    <label for="date_range_select" style="font-weight: bold; color: #1a237e; font-size: 13px;"> Date Filter:</label>
                    <select id="date_range_select" onchange="toggleCustomDateInputs(); filterDashboard();" style="padding: 8px 12px; border-radius: 6px; border: 1px solid #ced4da; font-weight: bold; font-size: 13px; cursor: pointer; background: #fff;">
                        <option value="all" {opt_all}>All Time</option>
                        <option value="1d" {opt_1d}>1 Day (Today)</option>
                        <option value="7d" {opt_7d}>Last 7 Days</option>
                        <option value="30d" {opt_30d}>Last 30 Days</option>
                        <option value="90d" {opt_90d}>Last 90 Days</option>
                        <option value="custom" {opt_custom}> Custom Range...</option>
                    </select>

                    <!-- Custom Calendar Pickers -->
                    <div id="custom_date_container" style="display: {'flex' if date_range == 'custom' else 'none'}; align-items: center; gap: 8px;">
                        <input type="date" id="start_date_input" value="{start_date_val}" onchange="filterDashboard()" style="padding: 6px 10px; border-radius: 6px; border: 1px solid #ced4da; font-size: 12px;">
                        <span style="color: #666; font-size: 12px; font-weight: bold;">to</span>
                        <input type="date" id="end_date_input" value="{end_date_val}" onchange="filterDashboard()" style="padding: 6px 10px; border-radius: 6px; border: 1px solid #ced4da; font-size: 12px;">
                    </div>
                    
                    <!-- Quick Preset Pill Buttons -->
                    <div style="display: flex; gap: 4px; margin-left: 5px;">
                        <button type="button" onclick="setQuickDate('1d')" class="btn-copy" style="padding: 6px 10px; font-size: 11px; background: {'#1a237e' if date_range == '1d' else '#e8eaf6'}; color: {'#fff' if date_range == '1d' else '#1a237e'}; border: none; cursor: pointer;">1D</button>
                        <button type="button" onclick="setQuickDate('7d')" class="btn-copy" style="padding: 6px 10px; font-size: 11px; background: {'#1a237e' if date_range == '7d' else '#e8eaf6'}; color: {'#fff' if date_range == '7d' else '#1a237e'}; border: none; cursor: pointer;">7D</button>
                        <button type="button" onclick="setQuickDate('30d')" class="btn-copy" style="padding: 6px 10px; font-size: 11px; background: {'#1a237e' if date_range == '30d' else '#e8eaf6'}; color: {'#fff' if date_range == '30d' else '#1a237e'}; border: none; cursor: pointer;">30D</button>
                        <button type="button" onclick="setQuickDate('90d')" class="btn-copy" style="padding: 6px 10px; font-size: 11px; background: {'#1a237e' if date_range == '90d' else '#e8eaf6'}; color: {'#fff' if date_range == '90d' else '#1a237e'}; border: none; cursor: pointer;">90D</button>
                        <button type="button" onclick="setQuickDate('all')" class="btn-copy" style="padding: 6px 10px; font-size: 11px; background: {'#1a237e' if date_range in [None, 'all', ''] else '#e8eaf6'}; color: {'#fff' if date_range in [None, 'all', ''] else '#1a237e'}; border: none; cursor: pointer;">All</button>
                    </div>
                </div>

            </div>
        </div>
                        <a href="/dashboard/health?client_id={selected_client_id}" class="btn-copy" style="background-color: #d81b60; text-decoration: none; padding: 10px 16px; font-size: 13px; font-weight: bold; color: white; margin-right: 8px;"> Webhook & Sync Health</a> <a href="/dashboard/reports?client_id={selected_client_id}" class="btn-copy" style="background-color: #1a237e; text-decoration: none; padding: 10px 16px; font-size: 13px; font-weight: bold; color: white;"> Reports & Analytics</a>
                        {settings_btn_html}
                        {onboard_btn_html}
                    </div>
                </header>

                <!-- Stats Cards -->
                <div class="stats-grid">
                    <div class="stat-card">
                        <h3>Tracked Sessions</h3>
                        <p class="value">{total_leads}</p>
                    </div>
                    <div class="stat-card">
                        <h3>AI Qualified Leads </h3>
                        <p class="value">{qualified_leads}</p>
                    </div>
                    <div class="stat-card">
                        <h3>Sales Closed </h3>
                        <p class="value">{sales_closed}</p>
                    </div>
                    <div class="stat-card rev">
                        <h3>Tracked Sales Revenue </h3>
                        <p class="value">${total_revenue:,.2f}</p>
                    </div>
                </div>

                <!-- Multi-Channel Exports Panel -->
                <div style="background: #fafafa; border: 1px solid #e0e0e0; border-radius: 8px; padding: 20px; margin-bottom: 30px;">
                    <h3 style="margin: 0 0 15px 0; color: #1a237e; font-size: 15px; text-transform: uppercase; letter-spacing: 0.5px;"> Multi-Channel Offline Conversion Exports</h3>
                    <div class="export-card-grid">
                        <!-- Google Ads -->
                        <div class="export-card">
                            <div>
                                <h4 style="color: #4285F4;">Google Ads (GCLID)</h4>
                                <p>Export verified lead signals and transaction revenue for Smart Bidding optimization.</p>
                            </div>
                            {google_export_button}
                            <div style="text-align: center; margin-top: 10px;"><a href="javascript:void(0)" onclick="openUploadInstructions('google')" style="color: #4285F4; text-decoration: underline; font-size: 11px; font-weight: bold; cursor: pointer; display: block;"> Instructions for uploading</a></div>
                        </div>
                        <!-- Facebook Ads -->
                        <div class="export-card">
                            <div>
                                <h4 style="color: #1877F2;">Meta/Facebook (FBCLID)</h4>
                                <p>Export offline events to optimize Facebook Conversions API and Custom Audiences.</p>
                            </div>
                            {facebook_export_button}
                            <div style="text-align: center; margin-top: 10px;"><a href="javascript:void(0)" onclick="openUploadInstructions('facebook')" style="color: #1877F2; text-decoration: underline; font-size: 11px; font-weight: bold; cursor: pointer; display: block;"> Instructions for uploading</a></div>
                        </div>
                        <!-- LinkedIn Ads -->
                        <div class="export-card">
                            <div>
                                <h4 style="color: #0A66C2;">LinkedIn Ads (LI_FAT_ID)</h4>
                                <p>Export professional business conversions directly into LinkedIn Campaign Manager.</p>
                            </div>
                            {linkedin_export_button}
                            <div style="text-align: center; margin-top: 10px;"><a href="javascript:void(0)" onclick="openUploadInstructions('linkedin')" style="color: #0A66C2; text-decoration: underline; font-size: 11px; font-weight: bold; cursor: pointer; display: block;"> Instructions for uploading</a></div>
                        </div>
                        <!-- Microsoft Ads -->
                        <div class="export-card">
                            <div>
                                <h4 style="color: #00A4EF;">Microsoft Ads (MSCLKID)</h4>
                                <p>Export click-matched offline sessions back into Bing/Microsoft campaign metrics.</p>
                            </div>
                            {microsoft_export_button}
                            <div style="text-align: center; margin-top: 10px;"><a href="javascript:void(0)" onclick="openUploadInstructions('microsoft')" style="color: #00A4EF; text-decoration: underline; font-size: 11px; font-weight: bold; cursor: pointer; display: block;"> Instructions for uploading</a></div>
                        </div>
                        <!-- TikTok Ads -->
                        <div class="export-card">
                            <div>
                                <h4 style="color: #010101;">TikTok Ads (TTCLID)</h4>
                                <p>Export offline conversion signals and purchase revenue directly into TikTok Ads Manager.</p>
                            </div>
                            {tiktok_export_button}
                            <div style="text-align: center; margin-top: 10px;"><a href="javascript:void(0)" onclick="openUploadInstructions('tiktok')" style="color: #010101; text-decoration: underline; font-size: 11px; font-weight: bold; cursor: pointer; display: block;"> Instructions for uploading</a></div>
                        </div>
                        <!-- X Ads -->
                        <div class="export-card">
                            <div>
                                <h4 style="color: #15202B;">X (Twitter) Ads (TWCLID)</h4>
                                <p>Export verified offline lead and sale transactions back into your X Ads campaigns.</p>
                            </div>
                            {twitter_export_button}
                            <div style="text-align: center; margin-top: 10px;"><a href="javascript:void(0)" onclick="openUploadInstructions('twitter')" style="color: #15202B; text-decoration: underline; font-size: 11px; font-weight: bold; cursor: pointer; display: block;"> Instructions for uploading</a></div>
                        </div>
                        <!-- Snapchat Ads -->
                        <div class="export-card">
                            <div>
                                <h4 style="color: #000; background: #FFFC00; display: inline-block; padding: 2px 6px; border-radius: 3px; font-size: 13px;">Snapchat Ads (SCCLID)</h4>
                                <p style="margin-top: 5px;">Export offline event transactions directly into Snapchat Ads Pixel conversions manager.</p>
                            </div>
                            {snapchat_export_button}
                            <div style="text-align: center; margin-top: 10px;"><a href="javascript:void(0)" onclick="openUploadInstructions('snapchat')" style="color: #000; text-decoration: underline; font-size: 11px; font-weight: bold; cursor: pointer; display: block;"> Instructions for uploading</a></div>
                        </div>
                        <!-- Pinterest Ads -->
                        <div class="export-card">
                            <div>
                                <h4 style="color: #E60023;">Pinterest Ads (PIN_CLID)</h4>
                                <p>Export matched audience actions directly into your Pinterest Tag metrics.</p>
                            </div>
                            {pinterest_export_button}
                            <div style="text-align: center; margin-top: 10px;"><a href="javascript:void(0)" onclick="openUploadInstructions('pinterest')" style="color: #E60023; text-decoration: underline; font-size: 11px; font-weight: bold; cursor: pointer; display: block;"> Instructions for uploading</a></div>
                        </div>
                        <!-- ChatGPT Ads -->
                        <div class="export-card">
                            <div>
                                <h4 style="color: #10a37f;">ChatGPT Ads (GPTCLID)</h4>
                                <p>Export verified offline lead and sale transactions back into your ChatGPT Ads metrics.</p>
                            </div>
                            {chatgpt_export_button}
                            <div style="text-align: center; margin-top: 10px;"><a href="javascript:void(0)" onclick="openUploadInstructions('chatgpt')" style="color: #10a37f; text-decoration: underline; font-size: 11px; font-weight: bold; cursor: pointer; display: block;"> Instructions for uploading</a></div>
                        </div>
                        <!-- Reddit Ads -->
                        <div class="export-card">
                            <div>
                                <h4 style="color: #FF4500;">Reddit Ads (rdt_cid)</h4>
                                <p>Export verified offline lead and purchase conversions directly into Reddit Ads Manager.</p>
                            </div>
                            {reddit_export_button}
                            <div style="text-align: center; margin-top: 10px;"><a href="javascript:void(0)" onclick="openUploadInstructions('reddit')" style="color: #FF4500; text-decoration: underline; font-size: 11px; font-weight: bold; cursor: pointer; display: block;"> Instructions for uploading</a></div>
                        </div>
                        <!-- Google Ads Adjustments -->
                        <div class="export-card">
                            <div>
                                <h4 style="color: #37474F;">Google Ads Adjustments (Offline)</h4>
                                <p>Export conversion adjustments (retractions and value restatements) to optimize bid accuracy.</p>
                            </div>
                            {google_adjustments_button}
                            <div style="text-align: center; margin-top: 10px;"><a href="javascript:void(0)" onclick="openUploadInstructions('google-adjustments')" style="color: #37474F; text-decoration: underline; font-size: 11px; font-weight: bold; cursor: pointer; display: block;"> Instructions for uploading</a></div>
                        </div>
                        <!-- Microsoft Ads Adjustments -->
                        <div class="export-card">
                            <div>
                                <h4 style="color: #00838F;">Microsoft Ads Adjustments (Offline)</h4>
                                <p>Export Bing/Microsoft conversion adjustments (retractions & value restatements) for ROAS accuracy.</p>
                            </div>
                            {microsoft_adjustments_button}
                            <div style="text-align: center; margin-top: 10px;"><a href="javascript:void(0)" onclick="openUploadInstructions('microsoft-adjustments')" style="color: #00838F; text-decoration: underline; font-size: 11px; font-weight: bold; cursor: pointer; display: block;"> Instructions for uploading</a></div>
                        </div>
                    </div>
                </div>

                <!-- First-Party Audience Builder Panel (Customer Match) -->
                <div style="background: #fafafa; border: 1px solid #e0e0e0; border-radius: 8px; padding: 20px; margin-bottom: 30px;">
                    <h3 style="margin: 0 0 5px 0; color: #1a237e; font-size: 15px; text-transform: uppercase; letter-spacing: 0.5px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
                         First-Party Audience Builder (Customer Match)
                        <span style="background: #e8f5e9; color: #2e7d32; font-size: 10px; padding: 2px 8px; border-radius: 12px; font-weight: bold; text-transform: none; letter-spacing: normal;">⚡ Active Bonus Feature</span>
                    </h3>
                    <p style="margin: 0 0 20px 0; font-size: 13px; color: #555; line-height: 1.5;">
                        Export compiled first-party customer profiles to train ad network smart bidding systems. These lists are pre-formatted and automatically hashed with <strong>SHA-256 security algorithms</strong> to comply with privacy regulations, bypassing expensive learning phases!
                    </p>
                    <div class="export-card-grid">
                        <!-- Google Ads -->
                        <div class="export-card">
                            <div>
                                <h4 style="color: #4285F4; display: flex; align-items: center; gap: 6px; margin: 0 0 5px 0;">
                                    <span style="font-size: 16px;"></span> Google Ads Customer Match
                                </h4>
                                <p>Download privacy-compliant hashed CSV for Google Customer Match. Boost smart bidding accuracy instantly.</p>
                            </div>
                            {google_audience_button}
                        </div>
                        <!-- Meta / Facebook Custom Audience -->
                        <div class="export-card">
                            <div>
                                <h4 style="color: #1877F2; display: flex; align-items: center; gap: 6px; margin: 0 0 5px 0;">
                                    <span style="font-size: 16px;"></span> Meta Custom Audiences
                                </h4>
                                <p>Download SHA-256 hashed CSV to build Facebook Custom/Lookalike Audiences and target high-value buyers.</p>
                            </div>
                            {facebook_audience_button}
                        </div>
                        <!-- LinkedIn List Member Matching -->
                        <div class="export-card">
                            <div>
                                <h4 style="color: #0A66C2; display: flex; align-items: center; gap: 6px; margin: 0 0 5px 0;">
                                    <span style="font-size: 16px;"></span> LinkedIn List Matching
                                </h4>
                                <p>Download pre-formatted target contact lists incorporating hashed identifiers and corporate accounts for LinkedIn B2B matched audiences.</p>
                            </div>
                            {linkedin_audience_button}
                        </div>
                    </div>
                </div>

                {upload_box_html}
                {global_modals_html}
                {global_modals_script_html}

                <!-- Table -->
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; flex-wrap: wrap; gap: 10px;">
            <h3 style="margin: 0; color: #1a237e; display: flex; align-items: center; gap: 8px;">
                 Lead Activity Log
                <span style="font-size: 12px; font-weight: normal; background: #e8eaf6; color: #1a237e; padding: 3px 10px; border-radius: 12px;">Showing {len(rows)} entries ({date_range_label})</span>
            </h3>
            <div style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap;">
                <a href="#" onclick="exportFilteredLeads(event)" class="btn-copy" style="background-color: #2e7d32; text-decoration: none; padding: 8px 14px; font-size: 12px; font-weight: bold; color: white; display: inline-flex; align-items: center; gap: 6px; box-shadow: 0 2px 5px rgba(0,0,0,0.1);">
                     Export Filtered Leads CSV ({len(rows)})
                </a>
                <div style="font-size: 12px; color: #666; font-style: italic;">
                    Active: <strong>{active_client_name}</strong> | <strong>{date_range_label}</strong>
                </div>
            </div>
        </div>
                <div class="table-responsive">
                    <table>
                        <thead>
                            <tr>
                                <th>ID</th>
                                {client_th_html}
                                <th>Timestamp</th>
                                <th>Source</th>
                                <th>Lead Contact</th>
                                <th style="width: 140px; min-width: 140px; max-width: 140px;">Click IDs Detected</th>
                                <th>Qualified</th>
                                <th>Closed</th>
                                <th>Value</th>
                                <th>Matching Method</th>
                                <th>Claude Decision Reasoning</th>
                                <th>Actions</th>
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



class UserInvite(BaseModel):
    email: str
    role: str
    client_id: Optional[int] = None

class UserRoleUpdate(BaseModel):
    email: str
    role: str

class UserDelete(BaseModel):
    email: str

class InviteRoleUpdate(BaseModel):
    token: str
    role: str

class InviteDelete(BaseModel):
    token: str

class SaleAdjustment(BaseModel):
    session_id: int
    adjustment_type: str # 'RETRACT' or 'RESTATE'
    adjusted_value: Optional[float] = 0.0

class ClientUpdate(BaseModel):
    id: int
    name: str
    call_tracking_provider: Optional[str] = "callrail"
    callrail_account_id: Optional[str] = ""
    callrail_company_id: Optional[str] = ""
    ctm_account_id: Optional[str] = ""
    ctm_profile_id: Optional[str] = ""
    wc_account_id: Optional[str] = ""
    wc_profile_id: Optional[str] = "" 
    google_ads_customer_id: Optional[str] = ""
    facebook_ads_id: Optional[str] = ""
    tiktok_ads_id: Optional[str] = ""
    twitter_ads_id: Optional[str] = ""
    pinterest_ads_id: Optional[str] = ""
    snapchat_ads_id: Optional[str] = ""
    snapchat_ads_id: Optional[str] = ""
    chatgpt_ads_id: Optional[str] = ""
    reddit_ads_id: Optional[str] = ""
    linkedin_ads_id: Optional[str] = ""
    microsoft_ads_id: Optional[str] = ""
    lead_gen_method: str
    qualification_criteria: str
    source_of_truth: str
    email_provider: Optional[str] = ""
    email_account: Optional[str] = ""
    email_app_password: Optional[str] = ""
    email_account_2: Optional[str] = ""
    email_app_password_2: Optional[str] = ""
    email_account_3: Optional[str] = ""
    email_app_password_3: Optional[str] = ""
    email_account_4: Optional[str] = ""
    email_app_password_4: Optional[str] = ""
    email_account_5: Optional[str] = ""
    email_app_password_5: Optional[str] = ""
    crm_deal_tags: Optional[str] = ""
    crm_won_deal_tags: Optional[str] = ""
    crm_value_field: Optional[str] = ""
    crm_lead_tags: Optional[str] = ""
    lead_count_rule: str
    exclude_past_customers: str
    excluded_customers: Optional[list[ExcludedCustomer]] = None
    exclusion_action: Optional[str] = "append"


