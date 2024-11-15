from fastapi import APIRouter, Depends, HTTPException, Request, status
from typing import Dict, Any, Tuple
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from google.oauth2 import id_token
from google.auth.transport import requests as google_auth_requests
from jose import JWTError
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from database import get_db
from db_models import UserDB
from pydantic_models import GoogleAuthRequest
from auth import (
    oauth,
    create_access_token,
    create_refresh_token,
    verify_jwt_token,
    auth_scheme,
    get_current_user,
)

from config import settings

router = APIRouter(tags=["authentication"])

def verify_google_token(token: str) -> Dict[str, Any]:
    """Verify Google OAuth token and return user info"""
    try:
        idinfo = id_token.verify_oauth2_token(
            token, google_auth_requests.Request(), settings.google_client_id
        )
        
        if idinfo["iss"] not in ["accounts.google.com", "https://accounts.google.com"]:
            raise ValueError("Wrong issuer.")
            
        return idinfo
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid token: {str(e)}")

def get_or_create_user(db: Session, idinfo: Dict[str, Any]) -> UserDB:
    """Get existing user or create new one from Google OAuth info"""
    user = db.query(UserDB).filter(
        UserDB.oauth_id == idinfo["sub"],
        UserDB.oauth_provider == "google",
        UserDB.deleted_at.is_(None)
    ).first()

    if not user:
        user = UserDB(
            email=idinfo["email"],
            name=idinfo.get("name", "Google User"),
            oauth_provider="google",
            oauth_id=idinfo["sub"],
            is_global_administrator=False,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    return user

def create_auth_response(user: UserDB) -> Dict[str, Any]:
    """Create standardized auth response with access and refresh tokens and user info"""
    access_token = create_access_token({"sub": user.id})
    refresh_token = create_refresh_token({"sub": user.id})
    
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "is_global_administrator": user.is_global_administrator,
            "company_associations": [
                {
                    "id": assoc.id,
                    "company_id": assoc.company_id,
                    "user_id": assoc.user_id,
                    "role": assoc.role,
                    "created_at": assoc.created_at,
                    "updated_at": assoc.updated_at,
                }
                for assoc in user.company_associations
            ],
        },
    }


@router.get("/login/google")
async def login_google(request: Request):
    """
    Initiate Google OAuth login flow
    """
    redirect_uri = request.url_for("auth_google")
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/auth/google")
async def auth_google_callback(request: Request, db: Session = Depends(get_db)):
    """
    Handle Google OAuth callback
    """
    try:
        token = await oauth.google.authorize_access_token(request)
        if "id_token" not in token:
            raise ValueError("No id_token found in the OAuth response")

        idinfo = verify_google_token(token["id_token"])
        user = get_or_create_user(db, idinfo)
        return create_auth_response(user)

    except Exception as e:
        print(f"Error in auth_google_callback: {str(e)}")
        error_details = {
            "error": str(e),
            "token_info": token if "token" in locals() else "Token not received",
        }
        return JSONResponse(
            status_code=500,
            content={"message": "Authentication failed", "details": error_details},
        )


@router.post("/auth/google")
async def auth_google(auth_request: GoogleAuthRequest, db: Session = Depends(get_db)):
    """
    Handle Google token authentication
    """
    try:
        idinfo = verify_google_token(auth_request.token)
        user = get_or_create_user(db, idinfo)
        return create_auth_response(user)
    except Exception as e:
        print(f"Error in auth_google: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/auth/refresh")
async def refresh_token(
    auth: HTTPAuthorizationCredentials = Depends(auth_scheme),
    db: Session = Depends(get_db)
):
    """
    Use a refresh token to get a new access token
    """
    try:
        payload = verify_jwt_token(auth.credentials)
        if not payload or payload.get("token_type") != "refresh":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token"
            )
        
        user = db.query(UserDB).filter(UserDB.id == payload.get("sub")).first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found"
            )
        
        # Create new access token
        access_token = create_access_token({"sub": user.id})
        
        return {
            "access_token": access_token,
            "token_type": "bearer"
        }
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token"
        )

@router.get("/api/verify-token")
async def verify_token(current_user: UserDB = Depends(get_current_user)):
    """
    Verify JWT token and return current user details
    """
    return {
        "user": {
            "id": current_user.id,
            "email": current_user.email,
            "name": current_user.name,
            "is_global_administrator": current_user.is_global_administrator,
        }
    }
