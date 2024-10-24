import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Query, Response, status
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

from helpers import parse_single_evidence_file, process_raw_evidence

router = APIRouter(tags=["companies"])


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
    query = db.query(CompanyDB).select_from(CompanyDB)
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
    return verify_company_access(db, company_id, current_user)


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

    # Get all users associated with the company
    users_with_roles = (
        db.query(UserDB, UserCompanyAssociation.role)
        .join(UserCompanyAssociation)
        .filter(UserCompanyAssociation.company_id == company_id)
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

    # Query audits associated with the company
    query = db.query(AuditDB).filter(AuditDB.company_id == company_id).order_by(AuditDB.created_at.desc())
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

    # Get the association
    association = (
        db.query(UserCompanyAssociation)
        .filter(
            UserCompanyAssociation.user_id == user_id,
            UserCompanyAssociation.company_id == company_id,
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
            status_code=403,
            detail="Only global administrators can create companies"
        )

    company_data = company.model_dump(exclude_unset=True)
    if "areas_of_focus" in company_data and company_data["areas_of_focus"]:
        company_data["areas_of_focus"] = ",".join(company_data["areas_of_focus"])
    if "size" in company_data and company_data["size"] is not None:
        company_data["size"] = company_data["size"].value

    # Remove None values to prevent SQLAlchemy from explicitly setting NULL
    company_data = {k: v for k, v in company_data.items() if v is not None}
    
    db_company = CompanyDB(**company_data)
    db.add(db_company)
    db.commit()
    db.refresh(db_company)

    # Create initial user-company association for creator as ORGANISATION_LEAD
    association = UserCompanyAssociation(
        user_id=current_user.id,
        company_id=db_company.id,
        role=UserRole.ORGANISATION_LEAD
    )
    db.add(association)
    db.commit()

    response_data = db_company.__dict__
    if response_data["areas_of_focus"]:
        response_data["areas_of_focus"] = response_data["areas_of_focus"].split(",")
    return CompanyResponse(**response_data)


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


@router.post("/companies/{company_id}/evidence", response_model=CompanyResponse)
@authorize_company_access(required_roles=[UserRole.AUDITOR])
async def parse_company_evidence(
    request: Request,
    company_id: str,
    evidence_request: ParseEvidenceRequest,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Parse specified evidence files for a company and extract relevant information"""
    # Set up logging
    logging.basicConfig(level=logging.DEBUG)
    logger = logging.getLogger(__name__)

    # Get the company
    db_company = verify_company_access(db, company_id, current_user, [UserRole.AUDITOR])
    if not db_company:
        raise HTTPException(status_code=404, detail="Company not found")

    # Get current processed file IDs
    processed_file_ids = db_company.processed_file_ids or []
    logger.debug(f"Initial processed_file_ids: {processed_file_ids}")

    # Get all valid evidence files that haven't been parsed yet
    evidence_files = (
        db.query(EvidenceFileDB)
        .join(AuditDB)
        .filter(
            EvidenceFileDB.id.in_(evidence_request.file_ids),
            AuditDB.company_id == company_id,
            EvidenceFileDB.status == "complete",
            EvidenceFileDB.text_content != None,
            ~EvidenceFileDB.id.in_(processed_file_ids if processed_file_ids else []),
        )
        .all()
    )

    logger.debug(f"Number of valid evidence files to process: {len(evidence_files)}")

    if not evidence_files:
        logger.debug("No new files to parse, proceeding to stage 2")
        return process_raw_evidence(db_company, db)

    # Stage 1: Parse each new evidence file
    new_processed_file_ids = processed_file_ids.copy() if processed_file_ids else []
    for file in evidence_files:
        parsed_content = parse_single_evidence_file(file, db_company)
        parsed_content = (
            "=== This is information gathered from the file "
            + file.filename
            + " ==="
            + parsed_content
        )
        if db_company.raw_evidence:
            db_company.raw_evidence += "\n\n" + parsed_content
        else:
            db_company.raw_evidence = parsed_content
        new_processed_file_ids.append(file.id)
        logger.debug(f"Processed file ID appended: {file.id}")

    # Update the processed_file_ids
    db_company.processed_file_ids = new_processed_file_ids
    logger.debug(f"Updated processed_file_ids: {db_company.processed_file_ids}")

    db.commit()
    logger.debug("Changes committed to database")

    # Stage 2: Process the accumulated raw evidence
    return process_raw_evidence(db_company, db)
