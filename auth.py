from datetime import datetime, timedelta
from functools import wraps
from typing import List, Optional

from fastapi import HTTPException, Depends, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from authlib.integrations.starlette_client import OAuth
from sqlalchemy.orm import Session

from db_models import UserDB, UserRole
from database import get_db

from config import settings

# OAuth setup
oauth = OAuth()
oauth.register(
    name="google",
    client_id=settings.google_client_id,
    client_secret=settings.google_client_secret,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

oauth.register(
    name="apple",
    client_id=settings.apple_client_id,
    client_secret=settings.apple_client_secret,
    server_metadata_url="https://appleid.apple.com/.well-known/openid-configuration",
    client_kwargs={"scope": "email name"},
)

# Authentication schemes
auth_scheme = HTTPBearer()


def create_jwt_token(data: dict) -> str:
    """
    Create a new JWT token with the provided data and expiration time.
    """
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    return jwt.encode(
        to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm
    )


def verify_jwt_token(token: str) -> Optional[dict]:
    """
    Verify a JWT token and return its payload if valid.
    """
    try:
        payload = jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
        return payload
    except JWTError:
        return None


async def get_current_user(
    auth: HTTPAuthorizationCredentials = Depends(auth_scheme),
    db: Session = Depends(get_db),
) -> UserDB:
    """
    Get the current authenticated user from the JWT token.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        token = auth.credentials
        payload = verify_jwt_token(token)
        if payload is None:
            raise credentials_exception
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.query(UserDB).filter(UserDB.id == user_id).first()
    if user is None:
        raise credentials_exception
    return user


def authorize_company_access(
    company_id_param: str = "company_id",
    audit_id_param: str = "audit_id",
    required_roles: Optional[List[UserRole]] = None,
):
    """
    A decorator to authorize access to endpoints based on user roles associated with a company.

    Parameters:
    - company_id_param: The name of the company ID parameter in the path.
    - audit_id_param: The name of the audit ID parameter in the path.
    - required_roles: List of UserRole enums that are allowed to access the endpoint.
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(
            *args,
            current_user: UserDB = Depends(get_current_user),
            db: Session = Depends(
                None
            ),  # This will be replaced with the actual get_db dependency
            **kwargs,
        ):
            # System administrators have unrestricted access
            if current_user.is_global_administrator:
                return await func(*args, current_user=current_user, db=db, **kwargs)

            # Ensure that required_roles is provided
            if not required_roles:
                raise HTTPException(
                    status_code=500,
                    detail="Access control misconfiguration: required_roles must be specified.",
                )

            # Extract path parameters
            request: Request = kwargs.get("request")
            if not request:
                for arg in args:
                    if isinstance(arg, Request):
                        request = arg
                        break

            if not request:
                raise HTTPException(status_code=500, detail="Request object not found")

            path_params = request.path_params
            company_id = path_params.get(company_id_param)
            audit_id = path_params.get(audit_id_param)

            # Determine the company ID if only audit ID is provided
            if not company_id and audit_id:
                from db_models import AuditDB  # Import here to avoid circular imports

                audit = db.query(AuditDB).filter(AuditDB.id == audit_id).first()
                if not audit:
                    raise HTTPException(status_code=404, detail="Audit not found")
                company_id = audit.company_id

            if not company_id:
                raise HTTPException(
                    status_code=400, detail="No company_id or audit_id provided in path"
                )

            # Check if the user has the required role
            if not current_user.has_company_role(company_id, required_roles):
                raise HTTPException(
                    status_code=403,
                    detail="Insufficient permissions to access this resource",
                )

            # Call the original endpoint function
            return await func(*args, current_user=current_user, db=db, **kwargs)

        return wrapper

    return decorator
