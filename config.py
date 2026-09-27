import os

DATABASE_URL = os.environ.get("DATABASE_URL")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()

ADMIN_EMAILS = {"admin@leadgrove.net", "admin@leadgroove.net", "corey@test.com", "corey@leadgrove.net"}

SOT_MAP = {
    "transcripts": "Phone/Email Transcripts (AI-Graded Lead Qualification & Sales Tracking)",
    "spreadsheets": "Spreadsheets (Manual CSV / Spreadsheet Ingestion)",
    "hubspot": "HubSpot CRM Webhook",
    "salesforce": "Salesforce CRM Webhook",
    "zoho": "Zoho CRM Webhook",
    "servicetitan": "ServiceTitan Lead & Job Webhook",
    "housecallpro": "Housecall Pro Job Webhook",
    "gohighlevel": "GoHighLevel (GHL) CRM Webhook",
    "pipedrive": "Pipedrive CRM Webhook",
    "quickbooks": "QuickBooks Online Paid Invoice Webhook",
    "xero": "Xero Paid Invoice Webhook",
    "zoho_books": "Zoho Books Paid Invoice Webhook",
    "netsuite": "NetSuite Paid Invoice Webhook",
    "sage": "Sage Accounting Paid Invoice Webhook",
    "freshbooks": "FreshBooks Paid Invoice Webhook",
    "google_sheets": "Google Sheets Live Sync",
    "zapier": "Zapier Custom Webhook",
    "email": "Automated Email Sales Log Scanner",
    "manual": "Manual CSV / Spreadsheet Ingestion",
    "ai_rating": "Phone/Email Transcripts (AI-Graded Lead Qualification & Sales Tracking)"
}

CRITERIA_MAP = {
    "A": "Option A: Anyone asking for pricing, services, quote, or appointment",
    "B": "Option B: Anyone who requested a quote or schedule",
    "C": "Option C: Someone who books an appointment or makes a purchase",
    "D": "Option D: Highly qualified decision-maker ready to buy immediately"
}
