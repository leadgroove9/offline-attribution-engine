from fastapi import HTTPException, Request
from typing import Optional, Tuple
from services.auth_service import is_authenticated, get_user_role_and_client

def verify_tenant_access(request: Request, client_id: Optional[int] = None) -> Tuple[str, str, Optional[int]]:
    email = is_authenticated(request)
    if not email:
        raise HTTPException(status_code=401, detail="Session expired or unauthenticated. Please log in.")
        
    user_role, user_client_id = get_user_role_and_client(email)
    
    if user_role != "full" and user_client_id is not None:
        if client_id is not None and client_id != 0 and client_id != user_client_id:
            raise HTTPException(status_code=403, detail=f"Forbidden: You do not have permission to access data for client #{client_id}.")
            
    return email, user_role, user_client_id
