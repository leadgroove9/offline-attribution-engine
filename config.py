import os

DB_PATH = "offline_attribution.db"
DATABASE_URL = os.environ.get("DATABASE_URL")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()

CALLRAIL_WEBHOOK_SECRET = os.environ.get("CALLRAIL_WEBHOOK_SECRET", "").strip()
HUBSPOT_CLIENT_SECRET = os.environ.get("HUBSPOT_CLIENT_SECRET", "").strip()
QUICKBOOKS_VERIFIER_TOKEN = os.environ.get("QUICKBOOKS_VERIFIER_TOKEN", "").strip()

ADMIN_EMAILS = {"admin@leadgrove.net", "admin@leadgroove.net", "corey@test.com", "corey@leadgrove.net"}

CRITERIA_MAP = {
    "A": "Option A: Anyone asking basic questions about services, pricing, or company hours",
    "B": "Option B: Someone who is inquiring about a quote or requesting pricing details",
    "C": "Option C: Someone who actually schedules an appointment, books a service, or agrees to a proposal",
    "ai_rules": "Standard Criteria: Inquiring about core services, requesting a quote, or scheduling an appointment"
}

SOT_MAP = {
    "hubspot": "HubSpot CRM",
    "salesforce": "Salesforce CRM",
    "zoho": "Zoho CRM",
    "servicetitan": "ServiceTitan",
    "housecallpro": "Housecall Pro",
    "gohighlevel": "GoHighLevel",
    "pipedrive": "Pipedrive",
    "quickbooks": "QuickBooks Online",
    "xero": "Xero Accounting",
    "zoho_books": "Zoho Books",
    "netsuite": "NetSuite",
    "sage": "Sage Intacct",
    "freshbooks": "FreshBooks",
    "google_sheets": "Google Sheets (Live Sync)",
    "zapier": "Zapier Webhook Feed",
    "email": "Automated Email Sales Scanner",
    "manual": "Manual CSV / Spreadsheet Upload"
}
