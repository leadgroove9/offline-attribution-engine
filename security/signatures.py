import hmac
import hashlib
from fastapi import Request
from config import CALLRAIL_WEBHOOK_SECRET, HUBSPOT_CLIENT_SECRET, QUICKBOOKS_VERIFIER_TOKEN

def verify_callrail_signature(request: Request, body_bytes: bytes) -> bool:
    if not CALLRAIL_WEBHOOK_SECRET:
        return True
    signature = request.headers.get("X-CallRail-Signature", "")
    if not signature:
        return False
    expected_sig = hmac.new(CALLRAIL_WEBHOOK_SECRET.encode(), body_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected_sig)

def verify_hubspot_signature(request: Request, body_bytes: bytes) -> bool:
    if not HUBSPOT_CLIENT_SECRET:
        return True
    signature = request.headers.get("X-HubSpot-Signature-v3", "")
    if not signature:
        return False
    url = str(request.url)
    source_string = f"POST{url}{body_bytes.decode('utf-8', errors='ignore')}"
    expected_sig = hmac.new(HUBSPOT_CLIENT_SECRET.encode(), source_string.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected_sig)

def verify_quickbooks_signature(request: Request, body_bytes: bytes) -> bool:
    if not QUICKBOOKS_VERIFIER_TOKEN:
        return True
    signature = request.headers.get("Intuit-Signature", "")
    if not signature:
        return False
    expected_sig = hmac.new(QUICKBOOKS_VERIFIER_TOKEN.encode(), body_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected_sig)
