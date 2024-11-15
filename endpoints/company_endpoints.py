import logging
from datetime import datetime, timezone
import json
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    Query,
    Response,
    status,
    BackgroundTasks,
)
from sqlalchemy.orm import Session
from typing import List

from database import get_db
from db_models import (
    UserDB,
    UserRole,
    CompanyDB,
    UserCompanyAssociation,
    AuditDB,
    EvidenceFileDB,
)
from helpers import (
    verify_company_access,
    verify_audit_access,
    get_or_404,
    paginate_query,
    filter_by_user_company_access,
)
from auth import get_current_user, authorize_company_access
from pydantic_models import (
    CompanyCreate,
    CompanyResponse,
    CompanyListResponse,
    CompanyUserResponse,
    AddUserToCompanyRequest,
    UserCompanyAssociationResponse,
    AuditListResponse,
    ParseEvidenceRequest,
)

from llm_helpers import (
    parse_evidence_file,
)
from background_tasks import process_company_evidence_task

router = APIRouter(tags=["companies"])


def load_constants():
    constants_path = Path(__file__).parent.parent / "constants.json"
    with open(constants_path, "r") as f:
        return json.load(f)


@router.get("/companies/constants")
async def get_constants():
    """Return the application constants"""
    return load_constants()


@router.post(
    "/companies/{company_id}/users", response_model=UserCompanyAssociationResponse
)
@authorize_company_access(required_roles=[UserRole.AUDITOR, UserRole.ORGANISATION_LEAD])
async def add_user_to_company(
    request: Request,
    company_id: str,
    request_body: AddUserToCompanyRequest,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Add a user to a company with a specific role"""
    # Check if current user has permission to manage users
    if not current_user.has_company_role(
        company_id, [UserRole.ORGANISATION_LEAD, UserRole.AUDITOR]
    ):
        raise HTTPException(
            status_code=403,
            detail="Only organization leads and auditors can manage users",
        )

    # Check if the company exists
    company = db.query(CompanyDB).filter(CompanyDB.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    # Check if the user exists
    user = db.query(UserDB).filter(UserDB.id == request_body.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if association already exists
    existing_association = (
        db.query(UserCompanyAssociation)
        .filter(
            UserCompanyAssociation.user_id == request_body.user_id,
            UserCompanyAssociation.company_id == company_id,
        )
        .first()
    )

    if existing_association:
        raise HTTPException(
            status_code=400, detail="User is already associated with this company"
        )

    # Create new association
    association = UserCompanyAssociation(
        user_id=request_body.user_id, company_id=company_id, role=request_body.role
    )
    db.add(association)
    db.commit()
    db.refresh(association)

    return association


@router.delete("/companies/{company_id}/users/{user_id}", status_code=204)
@authorize_company_access(required_roles=[UserRole.AUDITOR, UserRole.ORGANISATION_LEAD])
async def remove_user_from_company(
    request: Request,
    company_id: str,
    user_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Remove a user from a company"""
    # Check if current user has permission to manage users
    if not current_user.has_company_role(
        company_id, [UserRole.ORGANISATION_LEAD, UserRole.AUDITOR]
    ):
        raise HTTPException(
            status_code=403,
            detail="Only organization leads and auditors can manage users",
        )

    # Prevent removing the last organization lead
    if user_id != current_user.id:
        association = (
            db.query(UserCompanyAssociation)
            .filter(
                UserCompanyAssociation.company_id == company_id,
                UserCompanyAssociation.user_id == user_id,
            )
            .first()
        )

        if association and association.role == UserRole.ORGANISATION_LEAD:
            # Count remaining org leads
            remaining_leads = (
                db.query(UserCompanyAssociation)
                .filter(
                    UserCompanyAssociation.company_id == company_id,
                    UserCompanyAssociation.role == UserRole.ORGANISATION_LEAD,
                    UserCompanyAssociation.user_id != user_id,
                )
                .count()
            )

            if remaining_leads == 0:
                raise HTTPException(
                    status_code=400, detail="Cannot remove the last organization lead"
                )

    # Remove the association
    result = (
        db.query(UserCompanyAssociation)
        .filter(
            UserCompanyAssociation.user_id == user_id,
            UserCompanyAssociation.company_id == company_id,
        )
        .delete()
    )

    if result == 0:
        raise HTTPException(
            status_code=404, detail="User is not associated with this company"
        )

    db.commit()
    return Response(status_code=204)


@router.get("/companies", response_model=List[CompanyListResponse])
async def list_companies(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """List all companies accessible to the current user"""
    query = (
        db.query(CompanyDB)
        .select_from(CompanyDB)
        .filter(CompanyDB.deleted_at.is_(None))
    )
    query = filter_by_user_company_access(query, current_user)
    return paginate_query(query, skip, limit).all()


@router.get("/companies/{company_id}", response_model=CompanyResponse)
@authorize_company_access(required_roles=list(UserRole))
async def get_company_detail(
    request: Request,
    company_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Get detailed information about a specific company"""
    company = verify_company_access(db, company_id, current_user)
    if company.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


@router.get("/companies/{company_id}/users", response_model=List[CompanyUserResponse])
@authorize_company_access(required_roles=list(UserRole))
async def list_company_users(
    request: Request,
    company_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """List all users associated with a company"""
    # Verify access to company
    verify_company_access(db, company_id, current_user)

    # Get all users associated with the company, excluding soft-deleted users and companies
    users_with_roles = (
        db.query(UserDB, UserCompanyAssociation.role)
        .join(UserCompanyAssociation)
        .join(CompanyDB)
        .filter(
            UserCompanyAssociation.company_id == company_id,
            UserDB.deleted_at.is_(None),
            CompanyDB.deleted_at.is_(None),
        )
        .all()
    )

    return [
        CompanyUserResponse(
            id=user.id,
            email=user.email,
            name=user.name,
            role=role,
            created_at=user.created_at,
        )
        for user, role in users_with_roles
    ]


@router.get("/companies/{company_id}/audits", response_model=List[AuditListResponse])
@authorize_company_access(required_roles=list(UserRole))
async def list_company_audits(
    request: Request,
    company_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
):
    """List all audits associated with a company"""
    # Verify access to company
    verify_company_access(db, company_id, current_user)

    # Query audits associated with the company, excluding soft-deleted ones
    query = (
        db.query(AuditDB)
        .filter(AuditDB.company_id == company_id, AuditDB.deleted_at.is_(None))
        .order_by(AuditDB.created_at.desc())
    )
    audits = paginate_query(query, skip, limit).all()

    return audits


@router.put(
    "/companies/{company_id}/users/{user_id}/role",
    response_model=UserCompanyAssociationResponse,
)
@authorize_company_access(required_roles=[UserRole.AUDITOR, UserRole.ORGANISATION_LEAD])
async def update_user_role(
    request: Request,
    company_id: str,
    user_id: str,
    role: UserRole,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Update a user's role in a company"""
    # Check if current user has permission to manage users
    if not current_user.has_company_role(
        company_id, [UserRole.ORGANISATION_LEAD, UserRole.AUDITOR]
    ):
        raise HTTPException(
            status_code=403,
            detail="Only organization leads and auditors can manage user roles",
        )

    # Get the association, excluding soft-deleted companies
    association = (
        db.query(UserCompanyAssociation)
        .join(CompanyDB)
        .filter(
            UserCompanyAssociation.user_id == user_id,
            UserCompanyAssociation.company_id == company_id,
            CompanyDB.deleted_at.is_(None),
        )
        .first()
    )

    if not association:
        raise HTTPException(
            status_code=404, detail="User is not associated with this company"
        )

    # Prevent removing the last organization lead
    if (
        association.role == UserRole.ORGANISATION_LEAD
        and role != UserRole.ORGANISATION_LEAD
    ):
        remaining_leads = (
            db.query(UserCompanyAssociation)
            .filter(
                UserCompanyAssociation.company_id == company_id,
                UserCompanyAssociation.role == UserRole.ORGANISATION_LEAD,
                UserCompanyAssociation.user_id != user_id,
            )
            .count()
        )

        if remaining_leads == 0:
            raise HTTPException(
                status_code=400,
                detail="Cannot change role of the last organization lead",
            )

    # Update the role
    association.role = role
    db.commit()
    db.refresh(association)

    return association


@router.post("/companies", response_model=CompanyResponse)
async def create_company(
    request: Request,
    company: CompanyCreate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Create a new company (Global Admin only)"""
    if not current_user.is_global_administrator:
        raise HTTPException(
            status_code=403, detail="Only global administrators can create companies"
        )

    # Convert Pydantic model to dict and handle special fields
    company_data = company.model_dump(exclude_unset=True)
    if company_data.get("areas_of_focus"):
        company_data["areas_of_focus"] = ",".join(company_data["areas_of_focus"])
    if company_data.get("size"):
        company_data["size"] = company_data["size"].value

    # Create and save company
    db_company = CompanyDB(**company_data)
    db.add(db_company)
    db.commit()
    db.refresh(db_company)

    # Convert areas_of_focus back to list for response
    if db_company.areas_of_focus:
        db_company.areas_of_focus = db_company.areas_of_focus.split(",")

    return db_company


@router.put("/companies/{company_id}", response_model=CompanyResponse)
@authorize_company_access(required_roles=[UserRole.AUDITOR, UserRole.ORGANISATION_LEAD])
async def update_company(
    request: Request,
    company_id: str,
    company: CompanyCreate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Update company details"""
    db_company = verify_company_access(
        db, company_id, current_user, [UserRole.AUDITOR, UserRole.ORGANISATION_LEAD]
    )

    company_data = company.model_dump(exclude_unset=True)
    if "areas_of_focus" in company_data:
        company_data["areas_of_focus"] = ",".join(company_data["areas_of_focus"])
    if "size" in company_data and company_data["size"] is not None:
        company_data["size"] = company_data["size"].value

    for key, value in company_data.items():
        setattr(db_company, key, value)

    db.commit()
    db.refresh(db_company)
    return db_company


@router.delete("/companies/{company_id}", status_code=204)
async def delete_company(
    request: Request,
    company_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Delete a company (Global Admin only)"""
    if not current_user.is_global_administrator:
        raise HTTPException(
            status_code=403, detail="Only global administrators can delete companies"
        )

    # Get the company, including soft-deleted ones since we're in delete process
    company = get_or_404(db, CompanyDB, company_id, "Company not found")

    # Soft delete the company
    company.deleted_at = datetime.now(timezone.utc)
    db.commit()

    return Response(status_code=204)


@router.post("/companies/{company_id}/evidence", status_code=status.HTTP_202_ACCEPTED)
@authorize_company_access(required_roles=[UserRole.AUDITOR])
async def parse_company_evidence(
    request: Request,
    company_id: str,
    evidence_request: ParseEvidenceRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Parse evidence files and/or direct text content for a company"""
    # Get the company
    db_company = verify_company_access(db, company_id, current_user, [UserRole.AUDITOR])
    if not db_company:
        raise HTTPException(status_code=404, detail="Company not found")

    # Verify company is not soft-deleted
    if db_company.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Company not found")

    # Check if both fields are empty
    is_empty_request = (
        not evidence_request.file_ids or len(evidence_request.file_ids) == 0
    ) and (
        not evidence_request.text_content
        or len(evidence_request.text_content.strip()) == 0
    )

    # Add the processing task to background tasks
    background_tasks.add_task(
        process_company_evidence_task,
        db=db,
        company_id=company_id,
        file_ids=evidence_request.file_ids if not is_empty_request else None,
        text_content=evidence_request.text_content if not is_empty_request else None,
        reprocess_only=is_empty_request,
    )

    return {"message": "Evidence processing started", "company_id": company_id}
