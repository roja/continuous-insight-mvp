from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime, timezone

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
    assessment = (
        db.query(MaturityAssessmentDB)
        .filter(
            MaturityAssessmentDB.audit_id == audit_id,
            MaturityAssessmentDB.criteria_id == criteria_id,
        )
        .first()
    )

    if not assessment:
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
    # Check if the audit and criteria exist
    criteria = (
        db.query(CriteriaDB)
        .filter(CriteriaDB.id == criteria_id, CriteriaDB.audit_id == audit_id)
        .first()
    )
    if not criteria:
        raise HTTPException(status_code=404, detail="Audit or Criteria not found")

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
        existing_assessment.assessed_at = datetime.now(timezone.utc).isoformat()
        db_assessment = existing_assessment
    else:
        # Create new assessment
        db_assessment = MaturityAssessmentDB(
            audit_id=audit_id,
            criteria_id=criteria_id,
            maturity_level=assessment.maturity_level,
            comments=assessment.comments,
            assessed_by=current_user.name,  # Use actual user name
        )
        db.add(db_assessment)

    db.commit()
    db.refresh(db_assessment)
    return db_assessment

@router.get(
    "/audits/{audit_id}/assessments",
    response_model=List[MaturityAssessmentResponse]
)
@authorize_company_access(required_roles=list(UserRole))
async def get_all_maturity_assessments(
    request: Request,
    audit_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Get all maturity assessments for an audit"""
    assessments = (
        db.query(MaturityAssessmentDB)
        .filter(MaturityAssessmentDB.audit_id == audit_id)
        .all()
    )

    return assessments
