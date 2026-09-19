import re
import difflib
from typing import Optional
from database.connection import db_router

def normalize_phone(phone_str: Optional[str]) -> str:
    if not phone_str:
        return ""
    digits = re.sub(r'\D', '', str(phone_str))
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    if len(digits) == 10:
        return f"+1{digits}"
    return f"+{digits}" if digits else ""

def normalize_email(email_str: Optional[str]) -> str:
    if not email_str:
        return ""
    return str(email_str).strip().lower()

def clean_company_name(name: str) -> str:
    if not name:
        return ""
    name = str(name).lower()
    name = re.sub(r'\b(inc|llc|corp|co|ltd|group|services|solutions)\b', '', name)
    name = re.sub(r'[^a-z0-9]', '', name)
    return name.strip()

def clean_person_name(name: str) -> str:
    if not name:
        return ""
    name = str(name).lower()
    name = re.sub(r'[^a-z]', '', name)
    return name.strip()

def check_name_transposition(name1: str, name2: str) -> float:
    c1, c2 = clean_person_name(name1), clean_person_name(name2)
    if not c1 or not c2:
        return 0.0
    return difflib.SequenceMatcher(None, c1, c2).ratio()

def calculate_company_similarity(name1: str, name2: str) -> float:
    c1, c2 = clean_company_name(name1), clean_company_name(name2)
    if not c1 or not c2:
        return 0.0
    return difflib.SequenceMatcher(None, c1, c2).ratio()

def extract_param_from_url(url: str, param_name: str) -> Optional[str]:
    if not url:
        return None
    match = re.search(rf'[?&]{param_name}=([^&]+)', url)
    return match.group(1) if match else None

def check_is_excluded_customer(client_id: int, phone: str = "", email: str = "") -> Optional[str]:
    conn = db_router.connect()
    cursor = conn.cursor()
    
    norm_phone = normalize_phone(phone)
    norm_email = normalize_email(email)
    
    if norm_phone:
        cursor.execute("SELECT id FROM excluded_customers WHERE client_id = ? AND phone = ?", (client_id, norm_phone))
        if cursor.fetchone():
            conn.close()
            return "Phone Number Match"
            
    if norm_email:
        cursor.execute("SELECT id FROM excluded_customers WHERE client_id = ? AND LOWER(email) = ?", (client_id, norm_email))
        if cursor.fetchone():
            conn.close()
            return "Email Address Match"
            
    conn.close()
    return None
