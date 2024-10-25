from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from typing import List

from database import get_db
from db_models import UserDB, UserCompanyAssociation, CompanyDB
from auth import get_current_user
from pydantic_models import CompanyListResponse, UserResponse
from helpers import get_or_404, paginate_query, filter_by_user_company_access

router = APIRouter(tags=["users"])

@router.get("/users/me", response_model=UserResponse)
async def get_current_user_details(
    current_user: UserDB = Depends(get_current_user), 
    db: Session = Depends(get_db)
):
    """
    Get details about the currently authenticated user, including their company associations
    """
    # Get user with associations, excluding soft-deleted companies
    user_with_associations = (
        db.query(UserDB)
        .options(
            joinedload(UserDB.company_associations.and_(
                CompanyDB.deleted_at.is_(None)
            ))
        )
        .filter(UserDB.id == current_user.id)
        .first()
    )
    
    if not user_with_associations:
        raise HTTPException(status_code=404, detail="User not found")
        
    return user_with_associations

@router.get("/users/me/companies", response_model=List[CompanyListResponse])
async def list_user_companies(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """
    Get all companies the current user has access to with pagination
    """
    # Build base query excluding soft-deleted companies
    query = db.query(CompanyDB).filter(CompanyDB.deleted_at.is_(None))
    
    # Filter by user access
    query = filter_by_user_company_access(query, current_user)
    
    # Apply pagination
    companies = paginate_query(query, skip, limit).all()
    return companies

@router.delete("/users/{user_id}", response_model=UserResponse)
async def delete_user(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """
    Soft delete a user. Only admins can delete other users.
    Users can delete their own account.
    """
    # Get the user to delete
    user_to_delete = get_or_404(db, UserDB, user_id, "User not found")
    
    # Check permissions - users can only delete themselves
    if current_user.id != user_id:
        raise HTTPException(
            status_code=403,
            detail="Not authorized to delete other users"
        )
    
    # Implement soft delete
    user_to_delete.deleted_at = datetime.now(timezone.utc)
    
    # Remove all company associations
    db.query(UserCompanyAssociation).filter(
        UserCompanyAssociation.user_id == user_id
    ).delete()
    
    db.commit()
    db.refresh(user_to_delete)
    
    return user_to_delete
