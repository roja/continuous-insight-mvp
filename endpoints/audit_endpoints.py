from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from typing import List

from database import get_db
from db_models import (
    UserDB,
    UserRole,
    AuditDB,
    CompanyDB,
    EvidenceFileDB,
    CriteriaDB,
    UserCompanyAssociation
)
from auth import get_current_user, authorize_company_access
from pydantic_models import (
    AuditCreate,
    AuditResponse,
    AuditListResponse,
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
        # Use existing company
        company = db.query(CompanyDB).filter(CompanyDB.id == audit.company_id).first()
        if not company:
            raise HTTPException(status_code=404, detail="Company not found")
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
@authorize_company_access(required_roles=list(UserRole))
async def get_audit(
    request: Request,
    audit_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """
    Get details of a specific audit
    """
    db_audit = db.query(AuditDB).filter(AuditDB.id == audit_id).first()
    if db_audit is None:
        raise HTTPException(status_code=404, detail="Audit not found")
    return db_audit

@router.delete("/audits/{audit_id}", status_code=status.HTTP_204_NO_CONTENT)
@authorize_company_access(
    audit_id_param="audit_id",
    required_roles=[UserRole.AUDITOR, UserRole.ORGANISATION_LEAD],
)
async def delete_audit(
    request: Request,
    audit_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """
    Delete an audit and all its related data
    """
    db_audit = db.query(AuditDB).filter(AuditDB.id == audit_id).first()
    if db_audit is None:
        raise HTTPException(status_code=404, detail="Audit not found")

    # Delete related evidence files
    db.query(EvidenceFileDB).filter(EvidenceFileDB.audit_id == audit_id).delete()

    # Delete related criteria
    db.query(CriteriaDB).filter(CriteriaDB.audit_id == audit_id).delete()

    # Delete the audit
    db.delete(db_audit)
    db.commit()

    return {"message": "Audit and related data deleted successfully"}

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
    if current_user.is_global_administrator:
        audits = db.query(AuditDB).offset(skip).limit(limit).all()
        return audits

    # Get audits for companies the user has access to
    audits = (
        db.query(AuditDB)
        .join(CompanyDB)
        .join(UserCompanyAssociation)
        .filter(UserCompanyAssociation.user_id == current_user.id)
        .offset(skip)
        .limit(limit)
        .all()
    )
    return audits
