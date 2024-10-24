from fastapi import APIRouter, Depends, Request, status, HTTPException
from sqlalchemy.orm import Session
from typing import List

from database import get_db
from db_models import UserDB, UserRole, AuditDB, CompanyDB, EvidenceFileDB, CriteriaDB
from helpers import (
    verify_company_access,
    verify_audit_access,
    get_or_404,
    paginate_query,
    filter_by_user_company_access,
)
from auth import get_current_user, authorize_company_access
from pydantic_models import (
    AuditCreate,
    AuditResponse,
    AuditListResponse,
    CompanyResponse,
)

router = APIRouter(tags=["audits"])

@router.post("/audits", response_model=AuditResponse)
@authorize_company_access(required_roles=list(UserRole))
def create_audit(
    request: Request,
    audit: AuditCreate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """
    Create a new audit with either an existing company or a new company
    """
    if audit.company_id:
        # Use existing company and verify access
        company = verify_company_access(db, audit.company_id, current_user)
    elif audit.company_name:
        # Create new company
        company = CompanyDB()
        company.name = audit.company_name
        db.add(company)
        db.flush()  # This assigns an ID to the company without committing the transaction
    else:
        raise HTTPException(
            status_code=400,
            detail="Either company_id or company details must be provided",
        )

    db_audit = AuditDB(
        name=audit.name, description=audit.description, company_id=company.id
    )
    db.add(db_audit)
    db.commit()
    db.refresh(db_audit)

    return db_audit

@router.get("/audits/{audit_id}", response_model=AuditResponse)
async def get_audit(
    request: Request,
    audit_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """
    Get details of a specific audit
    """
    return verify_audit_access(db, audit_id, current_user)

@router.delete("/audits/{audit_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_audit(
    request: Request,
    audit_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """
    Delete an audit and all its related data
    """
    required_roles = [UserRole.AUDITOR, UserRole.ORGANISATION_LEAD]
    db_audit = verify_audit_access(db, audit_id, current_user, required_roles)

    # Delete related evidence files
    db.query(EvidenceFileDB).filter(EvidenceFileDB.audit_id == audit_id).delete()

    # Delete related criteria
    db.query(CriteriaDB).filter(CriteriaDB.audit_id == audit_id).delete()

    # Delete the audit
    db.delete(db_audit)
    db.commit()

    return {"message": "Audit and related data deleted successfully"}


@router.get("/audits/{audit_id}/company", response_model=CompanyResponse)
@authorize_company_access(required_roles=list(UserRole))
async def get_company(
    request: Request,
    audit_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Get company details for an audit"""
    db_audit = verify_audit_access(db, audit_id, current_user)
    db_company = get_or_404(db, CompanyDB, db_audit.company_id, "Company not found for this audit")

    return db_company

@router.get("/audits", response_model=List[AuditListResponse])
async def list_audits(
    request: Request,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """
    List all audits accessible to the current user
    """
    query = db.query(AuditDB)
    query = filter_by_user_company_access(query, current_user)
    return paginate_query(query, skip, limit).all()
