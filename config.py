import os

DATABASE_URL = os.environ.get("DATABASE_URL")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()

ADMIN_EMAILS = {"admin@leadgrove.net", "admin@leadgroove.net", "corey@test.com", "corey@leadgrove.net"}

SOT_MAP = {
    "transcripts": "Phone/Email Transcripts (AI-Graded Lead Qualification & Sales Tracking)",
    "spreadsheets": "Spreadsheets (Manual CSV / Spreadsheet Ingestion)",
    "hubspot": "HubSpot CRM",
    "zoho": "Zoho CRM",
    "salesforce": "Salesforce CRM",
    "servicetitan": "ServiceTitan CRM",
    "housecallpro": "Housecall Pro CRM",
    "gohighlevel": "GoHighLevel (GHL) CRM",
    "pipedrive": "Pipedrive CRM",
    "quickbooks": "QuickBooks Billing",
    "xero": "Xero Accounting",
    "zoho_books": "Zoho Books Accounting",
    "netsuite": "NetSuite ERP/Accounting",
    "sage": "Sage Accounting",
    "freshbooks": "FreshBooks Billing",
    "google_sheets": "Google Sheets (Live Sync)",
    "zapier": "Zapier Custom Integration",
    "email": "Automated Email Sales Log Scanner",
    "manual": "Manual CSV / Spreadsheet Upload",
    "ai_rating": "Claude AI Transcript Rating (All Calls)"
}

CRITERIA_MAP = {
    "A": "Option A: Anyone asking for pricing, services, quote, or appointment",
    "B": "Option B: Anyone who requested a quote or schedule",
    "C": "Option C: Someone who books an appointment or makes a purchase",
    "D": "Option D: Highly qualified decision-maker ready to buy immediately"
}
