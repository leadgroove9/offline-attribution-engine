import io
import csv
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse
from typing import Optional
from services.auth_service import is_authenticated, get_user_role_and_client
from services.identity_matcher import normalize_phone, normalize_email
from security.tenant_guard import verify_tenant_access
from database.connection import db_router

router = APIRouter()

def is_in_date_range(created_at_val, date_range: str, start_date_str: str, end_date_str: str) -> bool:
    if not created_at_val or date_range in [None, "all", ""]:
        return True
    try:
        now = datetime.now()
        dt_str = str(created_at_val).strip()
        dt = datetime.strptime(dt_str[:19], "%Y-%m-%d %H:%M:%S") if len(dt_str) >= 19 else datetime.now()
        if date_range in ["1d", "today"]:
            return dt.date() == now.date()
        elif date_range == "7d":
            return dt >= (now - timedelta(days=7))
        elif date_range == "30d":
            return dt >= (now - timedelta(days=30))
        elif date_range == "90d":
            return dt >= (now - timedelta(days=90))
        elif date_range == "custom":
            valid = True
            if start_date_str and start_date_str.strip():
                valid = valid and (dt >= datetime.strptime(start_date_str.strip(), "%Y-%m-%d"))
            if end_date_str and end_date_str.strip():
                valid = valid and (dt < (datetime.strptime(end_date_str.strip(), "%Y-%m-%d") + timedelta(days=1)))
            return valid
    except Exception:
        return True
    return True

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





@router.get("/dashboard/reports", response_class=HTMLResponse)
def view_reports(
    request: Request,
    client_id: Optional[int] = None,
    date_range: Optional[str] = "all",
    start_date: Optional[str] = "",
    end_date: Optional[str] = "",
    sub_tab: Optional[str] = "monthly"
):
    email = is_authenticated(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)
        
    user_role, user_client_id = get_user_role_and_client(email)
    
    conn = db_router.connect()
    cursor = conn.cursor()
    
    selected_client_id = None
    if user_role == "full":
        if client_id:
            selected_client_id = client_id
        elif user_client_id:
            selected_client_id = user_client_id
    else:
        selected_client_id = user_client_id
        
    cursor.execute("SELECT id, name FROM clients ORDER BY name ASC")
    clients = cursor.fetchall()
    
    dashboard_client_options = ""
    active_client_name = "All Clients"
    for c_id, c_name in clients:
        sel = "selected" if c_id == selected_client_id else ""
        if c_id == selected_client_id:
            active_client_name = c_name
        dashboard_client_options += f'<option value="{c_id}" {sel}>👤 {c_name}</option>'
        
    if selected_client_id:
        cursor.execute("""
            SELECT id, client_id, phone, name, source, qualified, sale_closed, value, reason, model_used, created_at, gclid, fbclid, msclkid, li_fat_id, ttclid, twclid, pin_clid, gptclid, rdt_cid
            FROM sessions
            WHERE client_id = ?
            ORDER BY created_at ASC
        """, (selected_client_id,))
    else:
        cursor.execute("""
            SELECT id, client_id, phone, name, source, qualified, sale_closed, value, reason, model_used, created_at, gclid, fbclid, msclkid, li_fat_id, ttclid, twclid, pin_clid, gptclid, rdt_cid
            FROM sessions
            ORDER BY created_at ASC
        """)
    raw_sessions = cursor.fetchall()
    conn.close()
    
    sessions = [s for s in raw_sessions if is_in_date_range(s[10], date_range, start_date, end_date)]
    
    total_leads = len(sessions)
    total_qualified = sum(1 for s in sessions if s[5] == "YES")
    total_sales = sum(1 for s in sessions if s[6] == "YES")
    total_revenue = sum(float(s[7] or 0.0) for s in sessions)
    qual_rate = (total_qualified / total_leads * 100) if total_leads > 0 else 0.0
    aov = (total_revenue / total_sales) if total_sales > 0 else 0.0
    
    channels = [
        "Google Ads", "Meta Ads", "Microsoft Ads", "LinkedIn Ads", 
        "TikTok Ads", "ChatGPT & Reddit Ads", "CallRail Phone Calls", "Web Forms & Organic"
    ]
    
    channel_ltv_data = {
        c: {
            "total": 0, "qualified": 0, "sales": 0, "revenue": 0.0,
            "contacts": set(), "buyers": set(), "buyer_sales_count": {}
        } for c in channels
    }
    
    def resolve_channel(src, gclid, fbclid, msclkid, li_fat, ttclid, twclid, pin_clid, gptclid, rdt_cid):
        s = str(src or "").lower()
        if gclid or "google" in s:
            return "Google Ads"
        elif fbclid or "facebook" in s or "meta" in s or "instagram" in s:
            return "Meta Ads"
        elif msclkid or "bing" in s or "microsoft" in s:
            return "Microsoft Ads"
        elif li_fat or "linkedin" in s:
            return "LinkedIn Ads"
        elif ttclid or "tiktok" in s:
            return "TikTok Ads"
        elif gptclid or rdt_cid or "chatgpt" in s or "reddit" in s:
            return "ChatGPT & Reddit Ads"
        elif "callrail" in s or "call" in s or "phone" in s:
            return "CallRail Phone Calls"
        else:
            return "Web Forms & Organic"

    for s in sessions:
        ch = resolve_channel(s[4], s[11], s[12], s[13], s[14], s[15], s[16], s[17], s[18], s[19])
        contact = str(s[2] or s[3] or s[0]).strip()
        is_qual = s[5] == "YES"
        is_sale = s[6] == "YES"
        val = float(s[7] or 0.0)
        
        channel_ltv_data[ch]["total"] += 1
        if is_qual:
            channel_ltv_data[ch]["qualified"] += 1
        if contact:
            channel_ltv_data[ch]["contacts"].add(contact)
        if is_sale:
            channel_ltv_data[ch]["sales"] += 1
            channel_ltv_data[ch]["revenue"] += val
            if contact:
                channel_ltv_data[ch]["buyers"].add(contact)
                channel_ltv_data[ch]["buyer_sales_count"][contact] = channel_ltv_data[ch]["buyer_sales_count"].get(contact, 0) + 1

    ltv_table_rows_html = ""
    for ch in channels:
        d = channel_ltv_data[ch]
        tot = d["total"]
        qual = d["qualified"]
        q_pct = (qual / tot * 100) if tot > 0 else 0.0
        sales_cnt = d["sales"]
        rev = d["revenue"]
        buyers_cnt = len(d["buyers"])
        repeat_cnt = sum(1 for cnt in d["buyer_sales_count"].values() if cnt > 1)
        ch_aov = (rev / sales_cnt) if sales_cnt > 0 else 0.0
        ch_ltv = (rev / buyers_cnt) if buyers_cnt > 0 else 0.0
        
        ltv_table_rows_html += f"""
        <tr>
            <td style="font-weight: bold; color: #1a237e;">{ch}</td>
            <td style="font-weight: bold;">{tot:,}</td>
            <td style="color: #0097a7; font-weight: bold;">{qual:,}</td>
            <td>{q_pct:.1f}%</td>
            <td style="color: #2e7d32; font-weight: bold;">{sales_cnt:,}</td>
            <td style="color: #2e7d32; font-weight: bold;">${rev:,.2f}</td>
            <td>${ch_aov:,.2f}</td>
            <td style="color: #155724; font-weight: bold; background: #e8f5e9;">${ch_ltv:,.2f}</td>
            <td><span style="background: {'#d4edda' if repeat_cnt > 0 else '#f8f9fa'}; color: {'#155724' if repeat_cnt > 0 else '#666'}; padding: 3px 8px; border-radius: 12px; font-weight: bold; font-size: 11px;">{repeat_cnt:,} Repeat Buyers</span></td>
        </tr>
        """

    monthly_data = {}
    for s in sessions:
        created_str = str(s[10] or "").strip()
        month_key = created_str[:7] if len(created_str) >= 7 and "-" in created_str[:7] else "2026-09"
        ch = resolve_channel(s[4], s[11], s[12], s[13], s[14], s[15], s[16], s[17], s[18], s[19])
        is_qual = s[5] == "YES"
        is_sale = s[6] == "YES"
        val = float(s[7] or 0.0)
        
        if month_key not in monthly_data:
            monthly_data[month_key] = {
                "total": 0, "qualified": 0, "sales": 0, "revenue": 0.0,
                "by_channel": {c: {"qual": 0, "rev": 0.0} for c in channels}
            }
            
        monthly_data[month_key]["total"] += 1
        if is_qual:
            monthly_data[month_key]["qualified"] += 1
            monthly_data[month_key]["by_channel"][ch]["qual"] += 1
        if is_sale:
            monthly_data[month_key]["sales"] += 1
            monthly_data[month_key]["revenue"] += val
            monthly_data[month_key]["by_channel"][ch]["rev"] += val

    sorted_months = sorted(monthly_data.keys())
    if not sorted_months:
        sorted_months = ["2026-09"]
        monthly_data["2026-09"] = {
            "total": 0, "qualified": 0, "sales": 0, "revenue": 0.0,
            "by_channel": {c: {"qual": 0, "rev": 0.0} for c in channels}
        }
    
    monthly_table_rows_html = ""
    for m in reversed(sorted_months):
        md = monthly_data[m]
        m_tot = md["total"]
        m_qual = md["qualified"]
        m_q_pct = (m_qual / m_tot * 100) if m_tot > 0 else 0.0
        m_sales = md["sales"]
        m_rev = md["revenue"]
        m_aov = (m_rev / m_sales) if m_sales > 0 else 0.0
        
        monthly_table_rows_html += f"""
        <tr>
            <td style="font-weight: bold; color: #1a237e;">📅 {m}</td>
            <td style="font-weight: bold;">{m_tot:,}</td>
            <td style="color: #0097a7; font-weight: bold;">{m_qual:,}</td>
            <td>{m_q_pct:.1f}%</td>
            <td style="color: #2e7d32; font-weight: bold;">{m_sales:,}</td>
            <td style="color: #2e7d32; font-weight: bold;">${m_rev:,.2f}</td>
            <td>${m_aov:,.2f}</td>
        </tr>
        """

    opt_all = "selected" if date_range == "all" else ""
    opt_1d = "selected" if date_range in ["1d", "today"] else ""
    opt_7d = "selected" if date_range == "7d" else ""
    opt_30d = "selected" if date_range == "30d" else ""
    opt_90d = "selected" if date_range == "90d" else ""
    opt_custom = "selected" if date_range == "custom" else ""
    
    start_date_val = start_date or ""
    end_date_val = end_date or ""
    active_sub_tab = sub_tab or "monthly"

    settings_label = "⚙️ Client Settings" if user_role == "full" else "⚙️ View Settings"
    settings_btn_html = f'<a href="/dashboard/settings?client_id={selected_client_id or 1}" class="btn-settings">{settings_label}</a>' if selected_client_id else ''
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Reports & Analytics - LeadGroove Offline Attribution</title>
        <meta charset="utf-8">
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif; background-color: #f4f6f9; color: #333; margin: 0; padding: 20px; }}
            .container {{ max-width: 1200px; margin: 0 auto; }}
            
            header {{ display: flex; justify-content: space-between; align-items: center; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.05); margin-bottom: 25px; flex-wrap: wrap; gap: 15px; }}
            .logo {{ font-size: 22px; font-weight: bold; color: #1a237e; text-decoration: none; }}
            .nav-tabs {{ display: flex; gap: 10px; align-items: center; }}
            .nav-link {{ padding: 8px 16px; border-radius: 6px; text-decoration: none; font-weight: bold; font-size: 13px; color: #666; transition: all 0.2s; }}
            .nav-link.active {{ background-color: #1a237e; color: white; }}
            .nav-link:hover:not(.active) {{ background-color: #e8eaf6; color: #1a237e; }}
            
            .sub-tabs {{ display: flex; gap: 10px; margin-bottom: 25px; border-bottom: 2px solid #e0e0e0; padding-bottom: 10px; }}
            .sub-tab-btn {{ padding: 10px 20px; border: none; background: #e0e0e0; color: #333; font-weight: bold; font-size: 14px; border-radius: 6px; cursor: pointer; transition: all 0.2s; }}
            .sub-tab-btn.active {{ background: #1a237e; color: white; box-shadow: 0 3px 8px rgba(26, 35, 126, 0.3); }}
            
            .toolbar {{ background: white; padding: 18px 20px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.05); margin-bottom: 25px; border-left: 4px solid #1a237e; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 15px; }}
            
            .kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 15px; margin-bottom: 25px; }}
            .kpi-card {{ background: white; padding: 18px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.05); border-top: 4px solid #1a237e; }}
            .kpi-title {{ font-size: 11px; color: #666; font-weight: bold; text-transform: uppercase; display: block; }}
            .kpi-value {{ font-size: 24px; font-weight: bold; color: #1a237e; margin-top: 5px; }}
            
            .chart-card {{ background: white; padding: 22px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.05); margin-bottom: 25px; }}
            .chart-title {{ font-size: 16px; font-weight: bold; color: #1a237e; margin-top: 0; margin-bottom: 15px; display: flex; align-items: center; justify-content: space-between; }}
            
            .table-container {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.05); overflow-x: auto; margin-bottom: 30px; }}
            table {{ width: 100%; border-collapse: collapse; font-size: 13px; text-align: left; }}
            th {{ background-color: #f8f9fc; color: #1a237e; font-weight: bold; padding: 12px; border-bottom: 2px solid #e0e0e0; white-space: nowrap; }}
            td {{ padding: 12px; border-bottom: 1px solid #f0f0f0; white-space: nowrap; }}
            tr:hover {{ background-color: #f8f9fa; }}
            
            .btn-copy {{ background-color: #1a237e; color: white; padding: 6px 14px; border: none; border-radius: 6px; font-weight: bold; font-size: 12px; cursor: pointer; text-decoration: none; transition: background 0.2s; display: inline-flex; align-items: center; gap: 5px; }}
            .btn-copy:hover {{ background-color: #0d1b2a; }}
        </style>
    </head>
    <body>
        <div class="container">
            <header>
                <a href="/dashboard" class="logo">⚡ LeadGroove Offline Attribution</a>
                <div class="nav-tabs">
                    <a href="/dashboard?client_id={selected_client_id or 1}" class="nav-link">📊 Dashboard</a>
                    <a href="/dashboard/reports?client_id={selected_client_id or 1}" class="nav-link active">📈 Reports & Analytics</a>
                    {settings_btn_html}
                </div>
            </header>

            <div class="sub-tabs">
                <button type="button" onclick="switchSubTab('monthly')" id="btn-subtab-monthly" class="sub-tab-btn {'active' if active_sub_tab == 'monthly' else ''}">
                    📅 Monthly Trends (By Month)
                </button>
                <button type="button" onclick="switchSubTab('ltv')" id="btn-subtab-ltv" class="sub-tab-btn {'active' if active_sub_tab == 'ltv' else ''}">
                    💎 Traffic Source LTV & Lifetime Revenue
                </button>
            </div>

            <div class="toolbar">
                <div style="display: flex; align-items: center; gap: 10px;">
                    <label for="report_client_select" style="font-weight: bold; color: #1a237e; font-size: 13px;">🏢 Active Client Profile:</label>
                    <select id="report_client_select" onchange="filterReports()" style="padding: 8px 14px; border-radius: 6px; border: 1px solid #1a237e; font-weight: bold; font-size: 13px; cursor: pointer; background: #f8f9fc; color: #1a237e;">
                        {dashboard_client_options}
                    </select>
                </div>

                <div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap;">
                    <label for="report_date_range" style="font-weight: bold; color: #1a237e; font-size: 13px;">📅 Time Horizon:</label>
                    <select id="report_date_range" onchange="toggleCustomReportDates(); filterReports();" style="padding: 8px 12px; border-radius: 6px; border: 1px solid #ced4da; font-weight: bold; font-size: 13px; cursor: pointer; background: #fff;">
                        <option value="all" {opt_all}>All Time</option>
                        <option value="1d" {opt_1d}>1 Day (Today)</option>
                        <option value="7d" {opt_7d}>Last 7 Days</option>
                        <option value="30d" {opt_30d}>Last 30 Days</option>
                        <option value="90d" {opt_90d}>Last 90 Days</option>
                        <option value="custom" {opt_custom}>📅 Custom Range...</option>
                    </select>

                    <div id="custom_report_date_container" style="display: {'flex' if date_range == 'custom' else 'none'}; align-items: center; gap: 8px;">
                        <input type="date" id="report_start_date" value="{start_date_val}" onchange="filterReports()" style="padding: 6px 10px; border-radius: 6px; border: 1px solid #ced4da; font-size: 12px;">
                        <span style="color: #666; font-size: 12px; font-weight: bold;">to</span>
                        <input type="date" id="report_end_date" value="{end_date_val}" onchange="filterReports()" style="padding: 6px 10px; border-radius: 6px; border: 1px solid #ced4da; font-size: 12px;">
                    </div>
                </div>
            </div>

            <div class="kpi-grid">
                <div class="kpi-card">
                    <span class="kpi-title">Total Inbound Leads</span>
                    <div class="kpi-value">{total_leads:,}</div>
                </div>
                <div class="kpi-card" style="border-top-color: #0097a7;">
                    <span class="kpi-title">Qualified Leads</span>
                    <div class="kpi-value" style="color: #0097a7;">{total_qualified:,}</div>
                </div>
                <div class="kpi-card" style="border-top-color: #00838f;">
                    <span class="kpi-title">Qualification Rate</span>
                    <div class="kpi-value" style="color: #00838f;">{qual_rate:.1f}%</div>
                </div>
                <div class="kpi-card" style="border-top-color: #2e7d32;">
                    <span class="kpi-title">Closed Sales</span>
                    <div class="kpi-value" style="color: #2e7d32;">{total_sales:,}</div>
                </div>
                <div class="kpi-card" style="border-top-color: #ff8f00;">
                    <span class="kpi-title">Total Sales Revenue</span>
                    <div class="kpi-value" style="color: #ff8f00;">${total_revenue:,.2f}</div>
                </div>
                <div class="kpi-card" style="border-top-color: #155724;">
                    <span class="kpi-title">Average Order Value (AOV)</span>
                    <div class="kpi-value" style="color: #155724;">${aov:,.2f}</div>
                </div>
            </div>

            <div id="view-panel-monthly" style="display: {'block' if active_sub_tab == 'monthly' else 'none'};">
                <div class="chart-card">
                    <div class="chart-title">
                        <span>📊 Qualified Leads by Month & Traffic Source</span>
                        <span style="font-size: 12px; font-weight: normal; color: #666;">Monthly Bar Chart</span>
                    </div>
                    <div style="height: 320px; position: relative;">
                        <canvas id="monthlyBarChart"></canvas>
                    </div>
                </div>

                <div class="chart-card">
                    <div class="chart-title">
                        <span>💰 Closed Sales Revenue ($) by Month & Traffic Source</span>
                        <span style="font-size: 12px; font-weight: normal; color: #666;">Monthly Line Chart</span>
                    </div>
                    <div style="height: 320px; position: relative;">
                        <canvas id="monthlyLineChart"></canvas>
                    </div>
                </div>

                <div class="table-container">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px;">
                        <h3 style="margin: 0; color: #1a237e;">📅 Monthly Performance Breakdown</h3>
                    </div>
                    <table>
                        <thead>
                            <tr>
                                <th>Month</th>
                                <th>Total Inbound Leads</th>
                                <th>Qualified Leads</th>
                                <th>Qualification Rate (%)</th>
                                <th>Closed Sales Count</th>
                                <th>Total Sales Revenue ($)</th>
                                <th>Average Order Value ($)</th>
                            </tr>
                        </thead>
                        <tbody>
                            {monthly_table_rows_html}
                        </tbody>
                    </table>
                </div>
            </div>

            <div id="view-panel-ltv" style="display: {'block' if active_sub_tab == 'ltv' else 'none'};">
                <div class="chart-card">
                    <div class="chart-title">
                        <span>💎 Total Sales Revenue ($) & Customer LTV To Date by Traffic Source</span>
                        <span style="font-size: 12px; font-weight: normal; color: #666;">Channel LTV Comparison</span>
                    </div>
                    <div style="height: 320px; position: relative;">
                        <canvas id="ltvBarChart"></canvas>
                    </div>
                </div>

                <div class="table-container">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px;">
                        <h3 style="margin: 0; color: #1a237e;">🎯 Traffic Source Performance & LTV Matrix</h3>
                    </div>
                    <table>
                        <thead>
                            <tr>
                                <th>Traffic Channel / Source</th>
                                <th>Total Inbound Leads</th>
                                <th>Qualified Leads</th>
                                <th>Qualification Rate (%)</th>
                                <th>Closed Sales Count</th>
                                <th>Total Revenue To Date ($)</th>
                                <th>Average Order Value ($)</th>
                                <th>Customer LTV ($)</th>
                                <th>Repeat Buyer Ratio</th>
                            </tr>
                        </thead>
                        <tbody>
                            {ltv_table_rows_html}
                        </tbody>
                    </table>
                </div>
            </div>

        </div>

        <script>
            let currentSubTab = '{active_sub_tab}';

            function switchSubTab(tabName) {{
                currentSubTab = tabName;
                document.getElementById('btn-subtab-monthly').classList.toggle('active', tabName === 'monthly');
                document.getElementById('btn-subtab-ltv').classList.toggle('active', tabName === 'ltv');
                
                document.getElementById('view-panel-monthly').style.display = tabName === 'monthly' ? 'block' : 'none';
                document.getElementById('view-panel-ltv').style.display = tabName === 'ltv' ? 'block' : 'none';
            }}

            function toggleCustomReportDates() {{
                const rangeSelect = document.getElementById('report_date_range');
                const customContainer = document.getElementById('custom_report_date_container');
                if (rangeSelect && customContainer) {{
                    customContainer.style.display = rangeSelect.value === 'custom' ? 'flex' : 'none';
                }}
            }}

            function filterReports() {{
                const clientSelect = document.getElementById('report_client_select');
                const rangeSelect = document.getElementById('report_date_range');
                const startInput = document.getElementById('report_start_date');
                const endInput = document.getElementById('report_end_date');
                
                const clientId = clientSelect ? clientSelect.value : '';
                const dateRange = rangeSelect ? rangeSelect.value : 'all';
                
                let url = `/dashboard/reports?client_id=${{clientId}}&date_range=${{dateRange}}&sub_tab=${{currentSubTab}}`;
                
                if (dateRange === 'custom') {{
                    if (startInput && startInput.value) {{
                        url += `&start_date=${{encodeURIComponent(startInput.value)}}`;
                    }}
                    if (endInput && endInput.value) {{
                        url += `&end_date=${{encodeURIComponent(endInput.value)}}`;
                    }}
                }}
                
                window.location.href = url;
            }}

            const monthsLabels = {sorted_months};
            const channelNames = {channels};
            
            const monthlyQualData = { {m: [monthly_data[m]["by_channel"][c]["qual"] for c in channels] for m in sorted_months} };
            const monthlyRevData = { {m: [monthly_data[m]["by_channel"][c]["rev"] for c in channels] for m in sorted_months} };

            const ctxMonthlyBar = document.getElementById('monthlyBarChart').getContext('2d');
            new Chart(ctxMonthlyBar, {{
                type: 'bar',
                data: {{
                    labels: monthsLabels,
                    datasets: channelNames.map((c, i) => ({{
                        label: c,
                        data: monthsLabels.map(m => monthlyQualData[m] ? monthlyQualData[m][i] : 0),
                        backgroundColor: ['#4285f4', '#1877f2', '#00a4ef', '#0a66c2', '#000000', '#ff4500', '#2e7d32', '#607d8b'][i % 8]
                    }}))
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {{ x: {{ stacked: true }}, y: {{ stacked: true, beginAtZero: true }} }}
                }}
            }});

            const ctxMonthlyLine = document.getElementById('monthlyLineChart').getContext('2d');
            new Chart(ctxMonthlyLine, {{
                type: 'line',
                data: {{
                    labels: monthsLabels,
                    datasets: channelNames.map((c, i) => ({{
                        label: c,
                        data: monthsLabels.map(m => monthlyRevData[m] ? monthlyRevData[m][i] : 0),
                        borderColor: ['#4285f4', '#1877f2', '#00a4ef', '#0a66c2', '#000000', '#ff4500', '#2e7d32', '#607d8b'][i % 8],
                        backgroundColor: 'transparent',
                        tension: 0.3
                    }}))
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {{ y: {{ beginAtZero: true }} }}
                }}
            }});

            const ltvRevenues = {[channel_ltv_data[c]["revenue"] for c in channels]};
            const ltvValues = {[ (channel_ltv_data[c]["revenue"] / len(channel_ltv_data[c]["buyers"])) if len(channel_ltv_data[c]["buyers"]) > 0 else 0.0 for c in channels ]};

            const ctxLtvBar = document.getElementById('ltvBarChart').getContext('2d');
            new Chart(ctxLtvBar, {{
                type: 'bar',
                data: {{
                    labels: channelNames,
                    datasets: [
                        {{
                            label: 'Total Revenue To Date ($)',
                            data: ltvRevenues,
                            backgroundColor: '#1a237e'
                        }},
                        {{
                            label: 'Average Customer LTV ($)',
                            data: ltvValues,
                            backgroundColor: '#2e7d32'
                        }}
                    ]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {{ y: {{ beginAtZero: true }} }}
                }}
            }});
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    print(f"🌐 Starting Uvicorn server on 0.0.0.0:{port}...")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)



