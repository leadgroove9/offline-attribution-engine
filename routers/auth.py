from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from typing import Optional
from database.connection import db_router
from services.auth_service import (
    hash_password, verify_password, create_session, get_session_email,
    delete_session, is_authenticated, get_user_role_and_client
)
from models.schemas import UserRoleUpdate, UserDelete, InviteRoleUpdate, InviteDelete, UserInvite
import uuid

router = APIRouter()

@router.get("/register", response_class=HTMLResponse)
def get_register(request: Request, error: Optional[str] = None, invite_token: Optional[str] = None):
    email_val = ""
    lock_email_attr = ""
    invite_role_msg = ""
    token_hidden_input = ""
    
    if invite_token:
        conn = db_router.connect()
        cursor = conn.cursor()
        cursor.execute("SELECT email, role, client_id FROM user_invitations WHERE token = ? AND is_used = 'NO'", (invite_token,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            error = "Invalid or expired invitation token. Please request a new invite link."
        else:
            invited_email, invited_role, invited_client_id = row
            email_val = invited_email
            lock_email_attr = "readonly style='background: #f1f3f4; color: #666;'"
            role_title = "Full Function (Manager)" if invited_role == "full" else "Read-Only (Viewer)"
            invite_role_msg = f"""
            <div style="background-color: #e8f5e9; color: #2e7d32; padding: 12px; border-radius: 6px; margin-bottom: 20px; font-size: 13px; font-weight: bold; border-left: 4px solid #2e7d32;">
                ✅ Invitation Verified!<br>
                You are registering as a <strong>{role_title}</strong>.
            </div>
            """
            token_hidden_input = f"<input type='hidden' name='invite_token' value='{invite_token}'>"

    error_html = f'<div style="background-color: #ffebee; color: #c62828; padding: 12px; border-radius: 6px; margin-bottom: 20px; font-size: 13px; font-weight: bold; border-left: 4px solid #c62828;">❌ {error}</div>' if error else ''
    return f"""
    <html>
        <head>
            <title>Register - LeadGroove </title>
            <style>
                body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; text-align: center; padding-top: 100px; background-color: #f4f6f9; color: #333; }}
                .container {{ display: inline-block; background: white; padding: 40px; border-radius: 12px; box-shadow: 0px 8px 24px rgba(0,0,0,0.08); max-width: 400px; width: 100%; text-align: left; box-sizing: border-box; }}
                h1 {{ color: #1a237e; margin-top: 0; margin-bottom: 8px; font-size: 24px; font-weight: 700; text-align: center; }}
                p {{ color: #666; font-size: 14px; margin-top: 0; margin-bottom: 24px; text-align: center; }}
                .form-group {{ margin-bottom: 18px; }}
                label {{ display: block; font-weight: bold; font-size: 12px; margin-bottom: 6px; color: #1a237e; text-transform: uppercase; letter-spacing: 0.5px; }}
                input[type="email"], input[type="password"] {{ width: 100%; padding: 12px; border-radius: 6px; border: 1px solid #ced4da; font-size: 14px; box-sizing: border-box; transition: border-color 0.2s; }}
                input[type="email"]:focus, input[type="password"]:focus {{ border-color: #1a237e; outline: none; }}
                .btn {{ width: 100%; background-color: #1a237e; color: white; padding: 12px; border: none; font-size: 16px; font-weight: bold; border-radius: 6px; cursor: pointer; margin-top: 10px; transition: background 0.2s; }}
                .btn:hover {{ background-color: #0d1b2a; }}
                .switch-link {{ text-align: center; margin-top: 20px; font-size: 13px; color: #555; }}
                .switch-link a {{ color: #1a237e; text-decoration: none; font-weight: bold; }}
                .switch-link a:hover {{ text-decoration: underline; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Create Your Account</h1>
                <p>Register to start tracking conversions on LeadGroove</p>
                {error_html}
                {invite_role_msg}
                <form action="/register" method="POST">
                    {token_hidden_input}
                    <div class="form-group">
                        <label for="email">Email Address</label>
                        <input type="email" id="email" name="email" required placeholder="e.g. corey@youragency.com" value="{email_val}" {lock_email_attr}>
                    </div>
                    <div class="form-group">
                        <label for="password">Password</label>
                        <input type="password" id="password" name="password" required placeholder="••••••••">
                    </div>
                    <div class="form-group" style="margin-bottom: 25px;">
                        <label for="confirm_password">Confirm Password</label>
                        <input type="password" id="confirm_password" name="confirm_password" required placeholder="••••••••">
                    </div>
                    <button type="submit" class="btn"> Create Account</button>
                </form>
                <div class="switch-link">
                    Already have an account? <a href="/login">Log In</a>
                </div>
            </div>
        </body>
    </html>
    """

@router.post("/register")
async def post_register(request: Request):
    try:
        form_data = await request.form()
        email = form_data.get("email", "").strip().lower()
        password = form_data.get("password")
        confirm_password = form_data.get("confirm_password")
        invite_token = form_data.get("invite_token")
        
        if not email or not password or not confirm_password:
            return HTMLResponse(get_register(request, error="All fields are required.", invite_token=invite_token))
        if password != confirm_password:
            return HTMLResponse(get_register(request, error="Passwords do not match.", invite_token=invite_token))
        if len(password) < 6:
            return HTMLResponse(get_register(request, error="Password must be at least 6 characters.", invite_token=invite_token))
            
        resolved_role = "full"
        resolved_client_id = None
        
        if invite_token:
            conn = db_router.connect()
            cursor = conn.cursor()
            cursor.execute("SELECT email, role, client_id FROM user_invitations WHERE token = ? AND is_used = 'NO'", (invite_token,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                return HTMLResponse(get_register(request, error="Invalid or expired invitation token.", invite_token=invite_token))
            invited_email, invited_role, invited_client_id = row
            email = invited_email
            resolved_role = invited_role
            resolved_client_id = invited_client_id
            conn.close()
            
        try:
            conn = db_router.connect()
            cursor = conn.cursor()
            
            # Check if user already exists
            cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
            if cursor.fetchone():
                conn.close()
                return HTMLResponse(get_register(request, error="An account with this email already exists.", invite_token=invite_token))
                
            hashed = hash_password(password)
            cursor.execute("""
                INSERT INTO users (email, hashed_password, role, client_id) 
                VALUES (?, ?, ?, ?)
            """, (email, hashed, resolved_role, resolved_client_id))
            
            if invite_token:
                cursor.execute("UPDATE user_invitations SET is_used = 'YES' WHERE token = ?", (invite_token,))
                
            conn.commit()
            conn.close()
            
            # Auto-login after registration
            token = create_session(email)
            response = RedirectResponse(url="/dashboard", status_code=303)
            response.set_cookie(key="session_token", value=token, max_age=86400 * 30, httponly=True)
            return response
        except Exception as e:
            return HTMLResponse(get_register(request, error=f"Database error: {str(e)}"))
    except Exception as outer_e:
        import traceback
        tb = traceback.format_exc()
        return HTMLResponse(f"""
        <html>
            <body style="font-family: monospace; padding: 40px; background-color: #ffebee; color: #c62828;">
                <h2>❌ Unhandled Registration Error</h2>
                <pre>{tb}</pre>
            </body>
        </html>
        """, status_code=500)

@router.get("/login", response_class=HTMLResponse)
def get_login(request: Request, error: Optional[str] = None):
    error_html = f'<div style="background-color: #ffebee; color: #c62828; padding: 12px; border-radius: 6px; margin-bottom: 20px; font-size: 13px; font-weight: bold; border-left: 4px solid #c62828;">❌ {error}</div>' if error else ''
    return f"""
    <html>
        <head>
            <title>Log In - LeadGroove </title>
            <style>
                body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; text-align: center; padding-top: 120px; background-color: #f4f6f9; color: #333; }}
                .container {{ display: inline-block; background: white; padding: 40px; border-radius: 12px; box-shadow: 0px 8px 24px rgba(0,0,0,0.08); max-width: 400px; width: 100%; text-align: left; box-sizing: border-box; }}
                h1 {{ color: #1a237e; margin-top: 0; margin-bottom: 8px; font-size: 24px; font-weight: 700; text-align: center; }}
                p {{ color: #666; font-size: 14px; margin-top: 0; margin-bottom: 24px; text-align: center; }}
                .form-group {{ margin-bottom: 18px; }}
                label {{ display: block; font-weight: bold; font-size: 12px; margin-bottom: 6px; color: #1a237e; text-transform: uppercase; letter-spacing: 0.5px; }}
                input[type="email"], input[type="password"] {{ width: 100%; padding: 12px; border-radius: 6px; border: 1px solid #ced4da; font-size: 14px; box-sizing: border-box; transition: border-color 0.2s; }}
                input[type="email"]:focus, input[type="password"]:focus {{ border-color: #1a237e; outline: none; }}
                .btn {{ width: 100%; background-color: #1a237e; color: white; padding: 12px; border: none; font-size: 16px; font-weight: bold; border-radius: 6px; cursor: pointer; margin-top: 10px; transition: background 0.2s; }}
                .btn:hover {{ background-color: #0d1b2a; }}
                .switch-link {{ text-align: center; margin-top: 20px; font-size: 13px; color: #555; }}
                .switch-link a {{ color: #1a237e; text-decoration: none; font-weight: bold; }}
                .switch-link a:hover {{ text-decoration: underline; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Welcome Back</h1>
                <p>Log in to access your conversion dashboard</p>
                {error_html}
                <form action="/login" method="POST">
                    <div class="form-group">
                        <label for="email">Email Address</label>
                        <input type="email" id="email" name="email" required placeholder="e.g. corey@youragency.com">
                    </div>
                    <div class="form-group" style="margin-bottom: 25px;">
                        <label for="password">Password</label>
                        <input type="password" id="password" name="password" required placeholder="••••••••">
                    </div>
                    <button type="submit" class="btn"> Log In</button>
                </form>
                <div class="switch-link">
                    Don't have an account? <a href="/register">Sign Up</a>
                </div>
                <div class="switch-link" style="margin-top: 10px;">
                    Forgot your password? <a href="/forgot-password">Reset it here</a>
                </div>
            </div>
        </body>
    </html>
    """

@router.post("/login")
async def post_login(request: Request):
    try:
        form_data = await request.form()
        email = form_data.get("email", "").strip().lower()
        password = form_data.get("password")
        
        if not email or not password:
            return HTMLResponse(get_login(request, error="All fields are required."))
            
        try:
            conn = db_router.connect()
            cursor = conn.cursor()
            cursor.execute("SELECT hashed_password FROM users WHERE email = ?", (email,))
            row = cursor.fetchone()
            conn.close()
            
            if not row or not verify_password(row[0], password):
                return HTMLResponse(get_login(request, error="Invalid email or password."))
                
            token = create_session(email)
            response = RedirectResponse(url="/dashboard", status_code=303)
            response.set_cookie(key="session_token", value=token, max_age=86400 * 30, httponly=True)
            return response
        except Exception as e:
            return HTMLResponse(get_login(request, error=f"Database error: {str(e)}"))
    except Exception as outer_e:
        import traceback
        tb = traceback.format_exc()
        return HTMLResponse(f"""
        <html>
            <body style="font-family: monospace; padding: 40px; background-color: #ffebee; color: #c62828;">
                <h2>❌ Unhandled Login Error</h2>
                <pre>{tb}</pre>
            </body>
        </html>
        """, status_code=500)

@router.get("/logout")
def get_logout(request: Request):
    token = request.cookies.get("session_token")
    delete_session(token)
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("session_token")
    return response


@router.get("/forgot-password", response_class=HTMLResponse)
def get_forgot_password(request: Request, error: Optional[str] = None, success: Optional[str] = None, token: Optional[str] = None):
    error_html = f'<div style="background-color: #ffebee; color: #c62828; padding: 12px; border-radius: 6px; margin-bottom: 20px; font-size: 13px; font-weight: bold; border-left: 4px solid #c62828;">❌ {error}</div>' if error else ''
    
    success_html = ""
    if success:
        success_html = f'''
        <div style="background-color: #e8f5e9; color: #2e7d32; padding: 16px; border-radius: 6px; margin-bottom: 20px; font-size: 13px; font-weight: bold; border-left: 4px solid #2e7d32; line-height: 1.6;">
            ✅ {success}
        </div>
        '''
        if token:
            success_html += f'''
            <div style="background-color: #fff9c4; border: 1px solid #fbc02d; padding: 16px; border-radius: 6px; margin-bottom: 25px; text-align: left; font-size: 13px; color: #574300; line-height: 1.5;">
                ️ <strong>Developer Sandbox Notice:</strong> Since no SMTP mail server is connected, the password reset request has been printed to the server console and generated directly below:
                <div style="margin-top: 10px; font-weight: bold; font-family: monospace; background: white; padding: 10px; border-radius: 4px; border: 1px solid #ffeb3b; word-break: break-all;">
                    <a href="/reset-password?token={token}" style="color: #1a237e; text-decoration: underline;">Click here to reset password</a>
                </div>
                <small style="color: #666; display: block; margin-top: 5px;">Link URL: <code>/reset-password?token={token}</code></small>
            </div>
            '''

    return f"""
    <html>
        <head>
            <title>Reset Password - LeadGroove \U0001f916</title>
            <style>
                body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; text-align: center; padding-top: 120px; background-color: #f4f6f9; color: #333; }}
                .container {{ display: inline-block; background: white; padding: 40px; border-radius: 12px; box-shadow: 0px 8px 24px rgba(0,0,0,0.08); max-width: 450px; width: 100%; text-align: left; box-sizing: border-box; }}
                h1 {{ color: #1a237e; margin-top: 0; margin-bottom: 8px; font-size: 24px; font-weight: 700; text-align: center; }}
                p {{ color: #666; font-size: 14px; margin-top: 0; margin-bottom: 24px; text-align: center; }}
                .form-group {{ margin-bottom: 18px; }}
                label {{ display: block; font-weight: bold; font-size: 12px; margin-bottom: 6px; color: #1a237e; text-transform: uppercase; letter-spacing: 0.5px; }}
                input[type="email"] {{ width: 100%; padding: 12px; border-radius: 6px; border: 1px solid #ced4da; font-size: 14px; box-sizing: border-box; transition: border-color 0.2s; }}
                input[type="email"]:focus {{ border-color: #1a237e; outline: none; }}
                .btn {{ width: 100%; background-color: #1a237e; color: white; padding: 12px; border: none; font-size: 16px; font-weight: bold; border-radius: 6px; cursor: pointer; margin-top: 10px; transition: background 0.2s; }}
                .btn:hover {{ background-color: #0d1b2a; }}
                .switch-link {{ text-align: center; margin-top: 20px; font-size: 13px; color: #555; }}
                .switch-link a {{ color: #1a237e; text-decoration: none; font-weight: bold; }}
                .switch-link a:hover {{ text-decoration: underline; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Forgot Password</h1>
                <p>Enter your email and we'll generate a secure password reset link.</p>
                {error_html}
                {success_html}
                <form action="/forgot-password" method="POST">
                    <div class="form-group">
                        <label for="email">Email Address</label>
                        <input type="email" id="email" name="email" required placeholder="e.g. corey@youragency.com">
                    </div>
                    <button type="submit" class="btn">\u2709\ufe0f Request Reset Link</button>
                </form>
                <div class="switch-link">
                    Remember your credentials? <a href="/login">Log In</a>
                </div>
            </div>
        </body>
    </html>
    """


@router.post("/forgot-password")
async def post_forgot_password(request: Request):
    try:
        form_data = await request.form()
        email = form_data.get("email", "").strip().lower()
        
        if not email:
            return HTMLResponse(get_forgot_password(request, error="Email is required."))
            
        conn = db_router.connect()
        cursor = conn.cursor()
        
        # Verify user exists
        cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
        user = cursor.fetchone()
        if not user:
            conn.close()
            return HTMLResponse(get_forgot_password(request, error="No registered account found with that email address."))
            
        # Create reset token
        token = uuid.uuid4().hex
        expires_at = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        
        cursor.execute("""
            INSERT INTO password_resets (email, token, expires_at)
            VALUES (?, ?, ?)
        """, (email, token, expires_at))
        
        conn.commit()
        conn.close()
        
        # Print link to standard logs
        print(f" [PASSWORD RESET] Link generated for {email}: http://localhost:8000/reset-password?token={token}")
        
        return HTMLResponse(get_forgot_password(
            request, 
            success="Password reset link generated successfully! This request expires in 1 hour.", 
            token=token
        ))
    except Exception as e:
        return HTMLResponse(get_forgot_password(request, error=f"Error generating reset request: {str(e)}"))


@router.get("/reset-password", response_class=HTMLResponse)
def get_reset_password(request: Request, token: Optional[str] = None, error: Optional[str] = None):
    if not token:
        return HTMLResponse("""
            <html>
                <body style="font-family: sans-serif; text-align: center; padding-top: 100px; background-color: #f4f6f9;">
                    <div style="display: inline-block; background: white; padding: 40px; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); max-width: 400px;">
                        <h2 style="color: #c62828; margin-top: 0;">❌ Missing Reset Token</h2>
                        <p style="color: #666; font-size: 14px; line-height: 1.5;">To reset your password, please use the exact link sent to you or printed in your developer logs.</p>
                        <a href="/login" style="display: inline-block; background: #1a237e; color: white; padding: 10px 20px; text-decoration: none; border-radius: 6px; font-weight: bold; margin-top: 15px;">Return to Login</a>
                    </div>
                </body>
            </html>
        """)
        
    conn = db_router.connect()
    cursor = conn.cursor()
    
    # Validate token
    cursor.execute("""
        SELECT email, is_used, expires_at 
        FROM password_resets 
        WHERE token = ?
    """, (token,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        return HTMLResponse("""
            <html>
                <body style="font-family: sans-serif; text-align: center; padding-top: 100px; background-color: #f4f6f9;">
                    <div style="display: inline-block; background: white; padding: 40px; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); max-width: 400px;">
                        <h2 style="color: #c62828; margin-top: 0;">❌ Invalid Token</h2>
                        <p style="color: #666; font-size: 14px; line-height: 1.5;">The password reset token is invalid, malformed, or does not exist.</p>
                        <a href="/forgot-password" style="display: inline-block; background: #1a237e; color: white; padding: 10px 20px; text-decoration: none; border-radius: 6px; font-weight: bold; margin-top: 15px;">Request New Link</a>
                    </div>
                </body>
            </html>
        """)
        
    email_val, is_used, expires_at_str = row
    
    if is_used == 'YES':
        return HTMLResponse("""
            <html>
                <body style="font-family: sans-serif; text-align: center; padding-top: 100px; background-color: #f4f6f9;">
                    <div style="display: inline-block; background: white; padding: 40px; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); max-width: 400px;">
                        <h2 style="color: #c62828; margin-top: 0;">❌ Token Already Used</h2>
                        <p style="color: #666; font-size: 14px; line-height: 1.5;">This password reset link has already been used to change your credentials and is deactivated.</p>
                        <a href="/login" style="display: inline-block; background: #1a237e; color: white; padding: 10px 20px; text-decoration: none; border-radius: 6px; font-weight: bold; margin-top: 15px;">Proceed to Login</a>
                    </div>
                </body>
            </html>
        """)
        
    # Check expiry
    try:
        expiry_dt = datetime.strptime(expires_at_str, "%Y-%m-%d %H:%M:%S")
        if datetime.now() > expiry_dt:
            return HTMLResponse("""
                <html>
                    <body style="font-family: sans-serif; text-align: center; padding-top: 100px; background-color: #f4f6f9;">
                        <div style="display: inline-block; background: white; padding: 40px; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); max-width: 400px;">
                            <h2 style="color: #c62828; margin-top: 0;">❌ Token Expired</h2>
                            <p style="color: #666; font-size: 14px; line-height: 1.5;">This password reset request has expired. Reset links are only valid for 1 hour from generation.</p>
                            <a href="/forgot-password" style="display: inline-block; background: #1a237e; color: white; padding: 10px 20px; text-decoration: none; border-radius: 6px; font-weight: bold; margin-top: 15px;">Request New Link</a>
                        </div>
                    </body>
                </html>
            """)
    except Exception:
        pass # If expiry date parsing fails, bypass and allow reset

    error_html = f'<div style="background-color: #ffebee; color: #c62828; padding: 12px; border-radius: 6px; margin-bottom: 20px; font-size: 13px; font-weight: bold; border-left: 4px solid #c62828;">❌ {error}</div>' if error else ''

    return f"""
    <html>
        <head>
            <title>Define New Password - LeadGroove \U0001f916</title>
            <style>
                body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; text-align: center; padding-top: 120px; background-color: #f4f6f9; color: #333; }}
                .container {{ display: inline-block; background: white; padding: 40px; border-radius: 12px; box-shadow: 0px 8px 24px rgba(0,0,0,0.08); max-width: 400px; width: 100%; text-align: left; box-sizing: border-box; }}
                h1 {{ color: #1a237e; margin-top: 0; margin-bottom: 8px; font-size: 24px; font-weight: 700; text-align: center; }}
                p {{ color: #666; font-size: 14px; margin-top: 0; margin-bottom: 24px; text-align: center; }}
                .form-group {{ margin-bottom: 18px; }}
                label {{ display: block; font-weight: bold; font-size: 12px; margin-bottom: 6px; color: #1a237e; text-transform: uppercase; letter-spacing: 0.5px; }}
                input[type="password"] {{ width: 100%; padding: 12px; border-radius: 6px; border: 1px solid #ced4da; font-size: 14px; box-sizing: border-box; transition: border-color 0.2s; }}
                input[type="password"]:focus {{ border-color: #1a237e; outline: none; }}
                .btn {{ width: 100%; background-color: #1a237e; color: white; padding: 12px; border: none; font-size: 16px; font-weight: bold; border-radius: 6px; cursor: pointer; margin-top: 10px; transition: background 0.2s; }}
                .btn:hover {{ background-color: #0d1b2a; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Set New Password</h1>
                <p>Define your secure, new password credentials for <strong>{email_val}</strong></p>
                {error_html}
                <form action="/reset-password" method="POST">
                    <input type="hidden" name="token" value="{token}">
                    <div class="form-group">
                        <label for="password">New Password</label>
                        <input type="password" id="password" name="password" required placeholder="••••••••">
                    </div>
                    <div class="form-group" style="margin-bottom: 25px;">
                        <label for="confirm_password">Confirm New Password</label>
                        <input type="password" id="confirm_password" name="confirm_password" required placeholder="••••••••">
                    </div>
                    <button type="submit" class="btn"> Save & Apply Password</button>
                </form>
            </div>
        </body>
    </html>
    """


@router.post("/reset-password")
async def post_reset_password(request: Request):
    try:
        form_data = await request.form()
        token = form_data.get("token", "").strip()
        password = form_data.get("password")
        confirm_password = form_data.get("confirm_password")
        
        if not token or not password or not confirm_password:
            return HTMLResponse(get_reset_password(request, token=token, error="All fields are required."))
            
        if password != confirm_password:
            return HTMLResponse(get_reset_password(request, token=token, error="Passwords do not match."))
            
        if len(password) < 6:
            return HTMLResponse(get_reset_password(request, token=token, error="Password must be at least 6 characters long."))
            
        conn = db_router.connect()
        cursor = conn.cursor()
        
        # Double check token validity
        cursor.execute("SELECT email, is_used, expires_at FROM password_resets WHERE token = ?", (token,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return HTMLResponse(get_reset_password(request, token=token, error="Invalid token."))
            
        email, is_used, expires_at_str = row
        if is_used == 'YES':
            conn.close()
            return HTMLResponse(get_reset_password(request, token=token, error="This reset link has already been used."))
            
        # Parse expiry date
        try:
            expiry_dt = datetime.strptime(expires_at_str, "%Y-%m-%d %H:%M:%S")
            if datetime.now() > expiry_dt:
                conn.close()
                return HTMLResponse(get_reset_password(request, token=token, error="This reset link has expired."))
        except Exception:
            pass
            
        # Hash new password and update database
        hashed = hash_password(password)
        cursor.execute("UPDATE users SET hashed_password = ? WHERE email = ?", (hashed, email))
        
        # Mark token as used
        cursor.execute("UPDATE password_resets SET is_used = 'YES' WHERE token = ?", (token,))
        
        conn.commit()
        conn.close()
        
        # Display Success page
        return HTMLResponse("""
            <html>
                <head>
                    <title>Password Reset Success - LeadGroove \U0001f916</title>
                    <style>
                        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; text-align: center; padding-top: 120px; background-color: #f4f6f9; color: #333; }
                        .container { display: inline-block; background: white; padding: 40px; border-radius: 12px; box-shadow: 0px 8px 24px rgba(0,0,0,0.08); max-width: 400px; width: 100%; box-sizing: border-box; }
                        h1 { color: #2e7d32; margin-top: 0; margin-bottom: 12px; font-size: 24px; font-weight: 700; }
                        p { color: #666; font-size: 14px; margin-top: 0; margin-bottom: 24px; line-height: 1.5; }
                        .btn { display: block; width: 100%; background-color: #1a237e; color: white; padding: 12px; border: none; font-size: 16px; font-weight: bold; border-radius: 6px; cursor: pointer; text-decoration: none; box-sizing: border-box; }
                        .btn:hover { background-color: #0d1b2a; }
                    </style>
                </head>
                <body>
                    <div class="container">
                        <h1>\U0001f389 Password Reset Complete!</h1>
                        <p>Your password credentials have been successfully updated. You can now use your new password to access your dashboard.</p>
                        <a href="/login" class="btn">\U0001f511 Proceed to Login</a>
                    </div>
                </body>
            </html>
        """)
    except Exception as e:
        return HTMLResponse(get_reset_password(request, token=token, error=f"Database update failed: {str(e)}"))


@router.get("/admin/users", response_class=HTMLResponse)
def get_admin_users(request: Request):
    email = is_authenticated(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)
    if email not in ADMIN_EMAILS:
        raise HTTPException(status_code=403, detail="Unauthorized: Access is restricted to site administrators.")
        
    try:
        conn = db_router.connect()
        cursor = conn.cursor()
        cursor.execute("SELECT id, email, created_at FROM users ORDER BY created_at DESC")
        users = cursor.fetchall()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error while querying registered users: {str(e)}")
        
    user_rows_html = ""
    for u_id, u_email, created_at in users:
        role_badge = '<span class="badge-admin">️ Administrator</span>' if u_email in ADMIN_EMAILS else '<span class="badge-user"> Registered User</span>'
        user_rows_html += f"""
        <tr>
            <td><strong>#{u_id}</strong></td>
            <td><code>{u_email}</code></td>
            <td>{role_badge}</td>
            <td><small>{created_at}</small></td>
        </tr>
        """
        
    if not user_rows_html:
        user_rows_html = '<tr><td colspan="4" style="text-align: center; color: #888; padding: 40px;">No registered accounts found in the database.</td></tr>'
        
    total_users = len(users)
    
    admin_link_html = ""
    if email in ADMIN_EMAILS:
        admin_link_html = ' | <a href="/admin/users" style="color: #2e7d32; text-decoration: none; font-weight: bold; margin-left: 5px;">️ Admin User Directory</a>'
        
    user_header_bar = f"""
    <div style="display: flex; justify-content: space-between; align-items: center; background-color: #f1f3f4; padding: 10px 15px; border-radius: 6px; margin-bottom: 20px; font-size: 13px;">
        <div>
            <span style="color: #666; font-weight: bold;"> Active Session:</span> <span style="font-weight: bold; color: #1a237e;">{email}</span>
            {admin_link_html}
        </div>
        <a href="/logout" style="color: #c62828; text-decoration: none; font-weight: bold; display: flex; align-items: center; gap: 4px;"> Log Out</a>
    </div>
    """
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
        <head>
            <title>LeadGrove Admin - User Directory ️</title>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                * {{ box-sizing: border-box; }} body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif; background-color: #f4f6f9; color: #333; margin: 0; padding: 20px; }}
                .container {{ max-width: 1000px; margin: 20px auto; background: white; padding: 40px; border-radius: 12px; box-shadow: 0px 4px 15px rgba(0,0,0,0.05); }}
                header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #eaeaea; padding-bottom: 20px; margin-bottom: 30px; }}
                h1 {{ margin: 0; color: #1a237e; font-size: 24px; }}
                .btn-back {{ display: inline-block; background-color: #1a237e; color: white; padding: 10px 18px; text-decoration: none; border-radius: 5px; font-weight: bold; font-size: 14px; transition: background 0.2s; }}
                .btn-back:hover {{ background-color: #0d1b2a; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 20px; font-size: 14px; }}
                th, td {{ padding: 12px 15px; text-align: left; border-bottom: 1px solid #eaeaea; }}
                th {{ background-color: #f8f9fa; color: #495057; font-weight: 600; text-transform: uppercase; font-size: 11px; letter-spacing: 0.5px; }}
                tr:hover {{ background-color: #fdfdfd; }}
                .badge-admin {{ background-color: #e8f5e9; color: #2e7d32; padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 11px; border: 1px solid #c8e6c9; }}
                .badge-user {{ background-color: #e3f2fd; color: #0d47a1; padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 11px; border: 1px solid #bbdefb; }}
            </style>
        </head>
        <body>
            <div class="container">
                {user_header_bar}
                <header>
                    <div>
                        <h1>️ LeadGrove Registered Users Directory</h1>
                        <p style="margin: 5px 0 0 0; color: #666; font-size: 14px;">Total registered accounts using LeadGrove: <strong>{total_users}</strong></p>
                    </div>
                    <a href="/dashboard" class="btn-back">⬅️ Back to Dashboard</a>
                </header>
                
                <table>
                    <thead>
                        <tr>
                            <th>User ID</th>
                            <th>Email Address</th>
                            <th>Role</th>
                            <th>Registration Date (UTC)</th>
                        </tr>
                    </thead>
                    <tbody>
                        {user_rows_html}
                    </tbody>
                </table>
            </div>
        </body>
    </html>
    """
    return HTMLResponse(html_content)

