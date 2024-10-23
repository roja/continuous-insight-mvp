from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from typing import List

from database import get_db
from db_models import UserDB, UserCompanyAssociation, CompanyDB
from auth import get_current_user
from pydantic_models import CompanyListResponse, UserResponse

router = APIRouter(tags=["users"])

@router.get("/users/me", response_model=UserResponse)
async def get_current_user_details(
    current_user: UserDB = Depends(get_current_user), 
    db: Session = Depends(get_db)
):
    """
    Get details about the currently authenticated user, including their company associations
    """
    # Fetch the user with company associations
    user_with_associations = (
        db.query(UserDB)
        .options(
            joinedload(UserDB.company_associations).joinedload(
                UserCompanyAssociation.company
            )
        )
        .filter(UserDB.id == current_user.id)
        .first()
    )

    if not user_with_associations:
        raise HTTPException(status_code=404, detail="User not found")

    return user_with_associations

@router.get("/users/me/companies", response_model=List[CompanyListResponse])
async def list_user_companies(
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """
    Get all companies the current user has access to
    """
    if current_user.is_global_administrator:
        # System auditors can see all companies
        companies = db.query(CompanyDB).all()
    else:
        # Get companies through associations
        companies = (
            db.query(CompanyDB)
            .join(UserCompanyAssociation)
            .filter(UserCompanyAssociation.user_id == current_user.id)
            .all()
        )

    return companies
