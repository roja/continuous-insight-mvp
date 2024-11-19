from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime, timezone
import uuid

from database import get_db
from db_models import (
    UserDB,
    UserRole,
    MaturityAssessmentDB,
    CriteriaDB,
)
from auth import get_current_user, authorize_company_access
from pydantic_models import (
    MaturityAssessmentCreate,
    MaturityAssessmentResponse,
)
from helpers import (
    verify_audit_access,
    get_or_404,
    paginate_query,
    filter_by_user_company_access,
)

router = APIRouter(tags=["maturity assessments"])


@router.get(
    "/audits/{audit_id}/criteria/{criteria_id}/maturity",
    response_model=MaturityAssessmentResponse,
)
@authorize_company_access(required_roles=list(UserRole))
async def get_maturity_assessment(
    request: Request,
    audit_id: str,
    criteria_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Get maturity assessment for specific criteria in an audit"""
    # Verify audit access
    audit = verify_audit_access(db, audit_id, current_user)

    # Verify criteria exists
    criteria = get_or_404(db, CriteriaDB, criteria_id, "Criteria not found")

    # Get assessment or 404
    assessment = get_or_404(
        db,
        MaturityAssessmentDB,
        criteria_id,
        "Maturity assessment not found",
    )

    # Verify assessment belongs to audit
    if assessment.audit_id != audit_id:
        raise HTTPException(status_code=404, detail="Maturity assessment not found")

    return assessment


@router.post(
    "/audits/{audit_id}/criteria/{criteria_id}/maturity",
    response_model=MaturityAssessmentResponse,
)
@authorize_company_access(required_roles=[UserRole.AUDITOR])
async def set_maturity_assessment(
    request: Request,
    audit_id: str,
    criteria_id: str,
    assessment: MaturityAssessmentCreate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Create or update maturity assessment for specific criteria"""
    # Verify audit access with required role
    audit = verify_audit_access(db, audit_id, current_user, [UserRole.AUDITOR])

    # Verify criteria exists
    criteria = get_or_404(db, CriteriaDB, criteria_id, "Criteria not found")

    current_time = datetime.now(timezone.utc)

    # Check if an assessment already exists
    existing_assessment = (
        db.query(MaturityAssessmentDB)
        .filter(
            MaturityAssessmentDB.audit_id == audit_id,
            MaturityAssessmentDB.criteria_id == criteria_id,
        )
        .first()
    )

    if existing_assessment:
        # Update existing assessment
        existing_assessment.maturity_level = assessment.maturity_level
        existing_assessment.comments = assessment.comments
        existing_assessment.assessed_at = current_time
        existing_assessment.updated_at = current_time
        db_assessment = existing_assessment
    else:
        # Create new assessment
        db_assessment = MaturityAssessmentDB(
            id=str(uuid.uuid4()),
            audit_id=audit_id,
            criteria_id=criteria_id,
            maturity_level=assessment.maturity_level,
            comments=assessment.comments,
            assessed_by=current_user.name,
            assessed_at=current_time,
            created_at=current_time,
            updated_at=current_time,
        )
        db.add(db_assessment)

    db.commit()
    db.refresh(db_assessment)

    # Create a response that matches MaturityAssessmentResponse
    response = MaturityAssessmentResponse(
        id=db_assessment.id,
        criteria_id=db_assessment.criteria_id,
        maturity_level=db_assessment.maturity_level,
        comments=db_assessment.comments,
        assessed_by=db_assessment.assessed_by,
        assessed_at=db_assessment.assessed_at,
        created_at=db_assessment.created_at or current_time,  # Fallback if not set
        updated_at=db_assessment.updated_at,
    )

    return response


@router.get(
    "/audits/{audit_id}/assessments", response_model=List[MaturityAssessmentResponse]
)
@authorize_company_access(required_roles=list(UserRole))
async def get_all_maturity_assessments(
    request: Request,
    audit_id: str,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Get all maturity assessments for an audit with pagination"""
    # Verify audit access
    audit = verify_audit_access(db, audit_id, current_user)

    # Build base query
    query = db.query(MaturityAssessmentDB).filter(
        MaturityAssessmentDB.audit_id == audit_id
    )

    # Apply pagination
    assessments = paginate_query(query, skip, limit).all()

    return assessments
