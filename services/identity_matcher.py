import os
import re
import difflib
from datetime import datetime, timedelta
from typing import Optional
from database.connection import db_router

def clean_company_name(name: str) -> str:
    if not name:
        return ""
    name_clean = name.lower().strip()
    name_clean = re.sub(r"[^\w\s]", "", name_clean)
    noise_words = ["inc", "llc", "corp", "co", "ltd", "group", "services", "limited", "incorporated", "corporation"]
    tokens = [w for w in name_clean.split() if w not in noise_words]
    return " ".join(tokens)

def clean_person_name(name: str) -> str:
    if not name:
        return ""
    name_clean = name.lower().strip()
    name_clean = re.sub(r"[^\w\s,]", "", name_clean)
    return name_clean

def check_name_transposition(name1: str, name2: str) -> float:
    n1 = clean_person_name(name1)
    n2 = clean_person_name(name2)
    tokens1 = [t.strip() for t in n1.split(",") if t.strip()]
    tokens2 = [t.strip() for t in n2.split(",") if t.strip()]
    
    if len(tokens1) > 1:
        n1_standard = " ".join(reversed(tokens1))
    else:
        n1_standard = n1
        
    if len(tokens2) > 1:
        n2_standard = " ".join(reversed(tokens2))
    else:
        n2_standard = n2

    t1 = n1_standard.split()
    t2 = n2_standard.split()
    
    if not t1 or not t2:
        return 0.0
        
    if sorted(t1) == sorted(t2):
        return 1.0
        
    if t1[-1] == t2[-1]:
        f1, f2 = t1[0], t2[0]
        if f1 == f2:
            return 1.0
        f1_clean = re.sub(r"\.", "", f1).strip()
        f2_clean = re.sub(r"\.", "", f2).strip()
        if f1_clean == f2_clean:
            return 1.0
        if len(f1_clean) == 1 and f2_clean.startswith(f1_clean):
            return 0.90
        if len(f2_clean) == 1 and f1_clean.startswith(f2_clean):
            return 0.90
        if f1_clean in f2_clean or f2_clean in f1_clean:
            return 0.85
            
    return difflib.SequenceMatcher(None, n1_standard, n2_standard).ratio()

def calculate_company_similarity(name1: str, name2: str) -> float:
    c1 = clean_company_name(name1)
    c2 = clean_company_name(name2)
    if not c1 or not c2:
        return 0.0
    if c1 in c2 or c2 in c1:
        return 1.0
    return difflib.SequenceMatcher(None, c1, c2).ratio()

def check_is_excluded_customer(client_id: int, phone: str = "", email: str = "") -> Optional[str]:
    if not phone and not email:
        return None
        
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        
        if phone:
            normalized_p = normalize_phone(phone)
            if normalized_p:
                cursor.execute("SELECT id FROM excluded_customers WHERE client_id = ? AND phone = ?", (client_id, normalized_p))
                if cursor.fetchone():
                    conn.close()
                    return "Phone Match"
                    
        if email:
            clean_e = email.strip().lower()
            if clean_e:
                cursor.execute("SELECT id FROM excluded_customers WHERE client_id = ? AND email = ?", (client_id, clean_e))
                if cursor.fetchone():
                    conn.close()
                    return "Email Match"
                    
        conn.close()
    except Exception as e:
        print(f"⚠️ Error checking customer exclusion: {e}")
    return None

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


def is_in_date_range(created_at_str: str, date_range: str, start_date: Optional[str] = None, end_date: Optional[str] = None) -> bool:
    if not created_at_str:
        return True
    if date_range == "all":
        return True
    try:
        dt = datetime.strptime(str(created_at_str)[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        try:
            dt = datetime.strptime(str(created_at_str)[:10], "%Y-%m-%d")
        except Exception:
            return True
    now = datetime.now()
    if date_range == "today":
        return dt.date() == now.date()
    elif date_range == "yesterday":
        return dt.date() == (now - timedelta(days=1)).date()
    elif date_range == "7d":
        return dt >= now - timedelta(days=7)
    elif date_range == "30d":
        return dt >= now - timedelta(days=30)
    elif date_range == "90d":
        return dt >= now - timedelta(days=90)
    elif date_range == "custom" and start_date and end_date:
        try:
            s_dt = datetime.strptime(start_date, "%Y-%m-%d")
            e_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
            return s_dt <= dt <= e_dt
        except Exception:
            return True
    return True
