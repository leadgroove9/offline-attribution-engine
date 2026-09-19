import json
import re
from typing import Dict, Any
from config import ANTHROPIC_API_KEY, CRITERIA_MAP

try:
    from anthropic import Anthropic
    anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY, max_retries=3, timeout=30.0) if ANTHROPIC_API_KEY else None
except ImportError:
    anthropic_client = None

def clean_json_string(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'```json\s*', '', text)
    text = re.sub(r'```\s*', '', text)
    return text.strip()

def analyze_transcript_with_claude(transcript: str, qualification_criteria_desc: str) -> Dict[str, Any]:
    if not anthropic_client:
        return {
            "qualified": "NO",
            "sale_closed": "NO",
            "value": 0.0,
            "reason": "Anthropic Claude API Key not configured."
        }
    
    prompt = (
        "You are LeadGroove AI, an expert offline sales call auditor.\n"
        f"Evaluate the following sales call transcript against this specific qualification criteria:\n"
        f"Criteria: \"{qualification_criteria_desc}\"\n\n"
        "Return ONLY a JSON object with these exact keys:\n"
        '- "qualified": "YES" or "NO"\n'
        '- "sale_closed": "YES" or "NO"\n'
        '- "value": float dollar amount (e.g. 1250.00 or 0.0)\n'
        '- "reason": brief 1-sentence explanation of your evaluation.\n\n'
        "Transcript:\n"
        f"{transcript}"
    )
    
    try:
        response = anthropic_client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=300,
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}]
        )
        content_text = clean_json_string(response.content[0].text)
        data = json.loads(content_text)
        return {
            "qualified": str(data.get("qualified", "NO")).upper(),
            "sale_closed": str(data.get("sale_closed", "NO")).upper(),
            "value": float(data.get("value", 0.0)),
            "reason": str(data.get("reason", "Evaluated cleanly."))
        }
    except Exception as e:
        return {
            "qualified": "NO",
            "sale_closed": "NO",
            "value": 0.0,
            "reason": f"Claude API audit exception: {e}"
        }
