import re

def sanitize_log(msg: str) -> str:
    if not msg:
        return ""
    msg = re.sub(r'sk-ant-[a-zA-Z0-9_-]+', 'sk-ant-REDACTED', msg)
    msg = re.sub(r'Bearer\s+[a-zA-Z0-9_\-\.]+', 'Bearer REDACTED', msg)
    return msg
