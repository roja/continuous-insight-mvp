from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    Query,
    BackgroundTasks,
    status,
)
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional

from database import get_db
from db_models import (
    UserDB,
    UserRole,
    CriteriaDB,
    AuditDB,
    CompanyDB,
    AuditCriteriaDB,
    EvidenceDB,
    EvidenceFileDB,
    QuestionDB,
    UserCompanyAssociation,
)
from auth import get_current_user, authorize_company_access
from pydantic_models import (
    CriteriaCreate,
    CriteriaResponse,
    CriteriaSelect,
    CriteriaSelectionResponse,
    CriteriaEvidenceResponse,
    DeleteCustomCriteriaResponse,
    UpdateCustomCriteriaRequest,
    EvidenceResponse,
    QuestionResponse,
    AnswerResponse,
    UpdateAuditCriteriaRequest,
    UpdateAuditCriteriaResponse,
    MaturityLevel,
    DeleteAuditCriteriaResponse,
    EvidenceFileResponse,
)
from helpers import (
    process_evidence_files_for_criteria,
    verify_company_access,
    verify_audit_access,
    get_or_404,
    paginate_query,
    filter_by_user_company_access,
    get_unprocessed_evidence_files_for_criteria,
)

router = APIRouter(tags=["criteria"])


@router.get("/criteria", response_model=List[CriteriaResponse])
async def list_base_criteria(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """List all base criteria"""
    query = db.query(CriteriaDB).filter(CriteriaDB.is_specific_to_audit == None)
    return paginate_query(query, skip, limit)


@router.get("/criteria/custom", response_model=List[CriteriaResponse])
@authorize_company_access(required_roles=[UserRole.AUDITOR])
async def list_custom_criteria(
    request: Request,
    audit_id: Optional[str] = Query(
        None, description="Filter criteria by specific audit"
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """List custom criteria with optional audit filter"""
    query = db.query(CriteriaDB).filter(CriteriaDB.is_specific_to_audit != None)

    if not current_user.is_global_administrator:
        # Filter by audits the user has access to
        query = (
            query.join(AuditDB)
            .join(CompanyDB)
            .join(UserCompanyAssociation)
            .filter(UserCompanyAssociation.user_id == current_user.id)
        )

    if audit_id:
        query = query.filter(CriteriaDB.is_specific_to_audit == audit_id)

    custom_criteria = query.offset(skip).limit(limit).all()
    return custom_criteria


@router.get("/audits/{audit_id}/criteria", response_model=List[CriteriaResponse])
@authorize_company_access(required_roles=list(UserRole))
async def get_audit_criteria(
    request: Request,
    audit_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Get all criteria associated with an audit"""
    audit = get_or_404(db, AuditDB, audit_id, "Audit not found")
    verify_audit_access(db, audit_id, current_user)

    audit_criteria = (
        db.query(AuditCriteriaDB)
        .filter(AuditCriteriaDB.audit_id == audit_id)
        .options(joinedload(AuditCriteriaDB.criteria))
        .all()
    )

    # Create response with expected maturity levels
    criteria_responses = []
    for ac in audit_criteria:
        criteria_dict = ac.criteria.__dict__
        criteria_dict["expected_maturity_level"] = ac.expected_maturity_level
        criteria_responses.append(criteria_dict)

    return criteria_responses


@router.post("/audits/{audit_id}/criteria/custom", response_model=CriteriaResponse)
@authorize_company_access(
    audit_id_param="audit_id",
    required_roles=[UserRole.AUDITOR, UserRole.ORGANISATION_LEAD],
)
async def add_custom_criteria(
    request: Request,
    audit_id: str,
    criteria: CriteriaCreate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Add custom criteria to an audit"""
    audit = get_or_404(db, AuditDB, audit_id, "Audit not found")

    # Create custom criteria
    db_criteria = CriteriaDB(
        title=criteria.title,
        description=criteria.description,
        parent_id=criteria.parent_id,
        maturity_definitions=criteria.maturity_definitions,
        is_specific_to_audit=audit_id,
        section=criteria.section,
    )
    db.add(db_criteria)
    db.flush()

    # Associate it with the audit
    audit_criteria = AuditCriteriaDB(
        audit_id=audit_id,
        criteria_id=db_criteria.id,
        expected_maturity_level=criteria.expected_maturity_level,
    )
    db.add(audit_criteria)

    db.commit()
    db.refresh(db_criteria)

    return db_criteria


@router.put("/criteria/custom/{criteria_id}", response_model=CriteriaResponse)
@authorize_company_access(required_roles=[UserRole.AUDITOR, UserRole.ORGANISATION_LEAD])
async def update_custom_criteria(
    request: Request,
    criteria_id: str,
    update_data: UpdateCustomCriteriaRequest,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Update custom criteria"""
    criteria = get_or_404(db, CriteriaDB, criteria_id, "Criteria not found")
    if criteria.is_specific_to_audit is None:
        raise HTTPException(status_code=400, detail="Cannot update base criteria")

    update_dict = update_data.dict(exclude_unset=True)
    if not update_dict:
        raise HTTPException(status_code=400, detail="No update data provided")

    for key, value in update_dict.items():
        setattr(criteria, key, value)

    db.commit()
    db.refresh(criteria)

    return db.query(CriteriaDB).filter(CriteriaDB.id == criteria_id).first()


@router.delete(
    "/criteria/custom/{criteria_id}", response_model=DeleteCustomCriteriaResponse
)
@authorize_company_access(required_roles=[UserRole.AUDITOR, UserRole.ORGANISATION_LEAD])
async def delete_custom_criteria(
    request: Request,
    criteria_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Delete custom criteria"""
    criteria = get_or_404(db, CriteriaDB, criteria_id, "Criteria not found")
    if criteria.is_specific_to_audit is None:  # This was reversed in the original
        raise HTTPException(status_code=400, detail="Cannot delete base criteria")

    associations = (
        db.query(AuditCriteriaDB)
        .filter(AuditCriteriaDB.criteria_id == criteria_id)
        .all()
    )
    if associations:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete criteria as it is still in use by one or more audits",
        )

    db.delete(criteria)
    db.commit()

    return DeleteCustomCriteriaResponse(
        message="Custom criteria successfully deleted", criteria_id=criteria_id
    )


@router.put(
    "/audits/{audit_id}/criteria/selected", response_model=UpdateAuditCriteriaResponse
)
@authorize_company_access(
    audit_id_param="audit_id",
    required_roles=[UserRole.AUDITOR, UserRole.ORGANISATION_LEAD],
)
async def update_audit_criteria(
    request: Request,
    audit_id: str,
    criteria_update: UpdateAuditCriteriaRequest,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Replace all criteria selections for an audit with the provided list"""

    # Verify audit exists
    audit = get_or_404(db, AuditDB, audit_id, "Audit not found")

    # Verify all criteria exist
    criteria_ids = [c.criteria_id for c in criteria_update.criteria_selections]
    existing_criteria = (
        db.query(CriteriaDB).filter(CriteriaDB.id.in_(criteria_ids)).all()
    )

    if len(existing_criteria) != len(criteria_ids):
        found_ids = {c.id for c in existing_criteria}
        missing_ids = [cid for cid in criteria_ids if cid not in found_ids]
        raise HTTPException(
            status_code=400,
            detail=f"Some criteria were not found: {', '.join(missing_ids)}",
        )

    try:
        # Remove all existing associations
        db.query(AuditCriteriaDB).filter(AuditCriteriaDB.audit_id == audit_id).delete()

        # Create new associations
        new_associations = []
        for selection in criteria_update.criteria_selections:
            new_association = AuditCriteriaDB(
                audit_id=audit_id,
                criteria_id=selection.criteria_id,
                expected_maturity_level=selection.expected_maturity_level
                or MaturityLevel.novice,
            )
            db.add(new_association)
            new_associations.append(new_association)

        db.commit()

        # Refresh associations to get their IDs
        for assoc in new_associations:
            db.refresh(assoc)

        # Debug: Print the attributes of the first association
        if new_associations:
            print("Association attributes:", vars(new_associations[0]))

        response = UpdateAuditCriteriaResponse(
            message="Audit criteria successfully updated",
            audit_id=audit_id,
            selected_criteria=[
                CriteriaSelectionResponse.model_validate(
                    {
                        "id": assoc.id,
                        "audit_id": assoc.audit_id,
                        "criteria_id": assoc.criteria_id,
                        "expected_maturity_level": assoc.expected_maturity_level,
                        "created_at": assoc.created_at,
                        "updated_at": assoc.updated_at,
                    }
                )
                for assoc in new_associations
            ],
        )
        return response

    except Exception as e:
        db.rollback()
        print(f"Error details: {str(e)}")  # Add debug logging
        raise HTTPException(
            status_code=500, detail=f"Failed to update audit criteria: {str(e)}"
        )


@router.post(
    "/audits/{audit_id}/criteria/{criteria_id}/actions/extract-evidence",
    status_code=status.HTTP_202_ACCEPTED,
)
@authorize_company_access(required_roles=[UserRole.AUDITOR])
async def extract_evidence_for_criteria(
    request: Request,
    audit_id: str,
    criteria_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Extract evidence for specific criteria"""
    criteria = get_or_404(db, CriteriaDB, criteria_id, "Criteria not found")

    # Check for unprocessed evidence files
    unprocessed_files = get_unprocessed_evidence_files_for_criteria(
        db, audit_id, criteria_id
    )
    if not unprocessed_files:
        raise HTTPException(
            status_code=400,
            detail="No new evidence files to process for this criteria",
        )

    # Add task to process evidence for each unprocessed file
    for file in unprocessed_files:
        background_tasks.add_task(
            process_evidence_files_for_criteria, audit_id, criteria_id
        )

    return {"message": "Evidence extraction started for new files"}


@router.get(
    "/audits/{audit_id}/criteria/{criteria_id}/evidence",
    response_model=CriteriaEvidenceResponse,
)
@authorize_company_access(required_roles=list(UserRole))
async def get_evidence_for_criteria(
    request: Request,
    audit_id: str,
    criteria_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Get evidence and questions for specific criteria"""
    # First, verify the audit exists and user has access
    audit = get_or_404(db, AuditDB, audit_id, "Audit not found")

    # Modified query to properly handle the join
    evidence = (
        db.query(EvidenceDB, EvidenceFileDB.filename)
        .outerjoin(
            EvidenceFileDB,
            (EvidenceDB.source_id == EvidenceFileDB.id)
            & (EvidenceDB.source == "evidence_file"),
        )
        .filter(EvidenceDB.audit_id == audit_id, EvidenceDB.criteria_id == criteria_id)
        .all()
    )

    questions = (
        db.query(QuestionDB)
        .options(joinedload(QuestionDB.answers))
        .filter(QuestionDB.audit_id == audit_id, QuestionDB.criteria_id == criteria_id)
        .all()
    )

    if not evidence and not questions:
        raise HTTPException(
            status_code=404,
            detail="No evidence or questions found for the given criteria and audit",
        )

    response = CriteriaEvidenceResponse(
        evidence=[
            EvidenceResponse(
                **{
                    **e[0].__dict__,
                    "source_name": e[1] if e[0].source == "evidence_file" else None,
                }
            )
            for e in evidence
        ],
        questions=[
            QuestionResponse(
                id=q.id,
                text=q.text,
                created_at=q.created_at,
                answers=[AnswerResponse.model_validate(a) for a in q.answers],
            )
            for q in questions
        ],
    )

    return response


@router.delete(
    "/audits/{audit_id}/criteria/{criteria_id}",
    response_model=DeleteAuditCriteriaResponse,
)
@authorize_company_access(
    audit_id_param="audit_id",
    required_roles=[UserRole.AUDITOR, UserRole.ORGANISATION_LEAD],
)
async def delete_audit_criteria(
    request: Request,
    audit_id: str,
    criteria_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Remove a specific criteria from an audit"""
    # Verify audit exists
    audit = get_or_404(db, AuditDB, audit_id, "Audit not found")

    # Find and delete the association
    association = (
        db.query(AuditCriteriaDB)
        .filter(
            AuditCriteriaDB.audit_id == audit_id,
            AuditCriteriaDB.criteria_id == criteria_id,
        )
        .first()
    )

    if not association:
        raise HTTPException(status_code=404, detail="Criteria not found in this audit")

    db.delete(association)
    db.commit()

    return DeleteAuditCriteriaResponse(
        message="Criteria successfully removed from audit",
        audit_id=audit_id,
        criteria_id=criteria_id,
    )


@router.get(
    "/audits/{audit_id}/criteria/{criteria_id}/unextracted-evidence",
    response_model=List[EvidenceFileResponse],
)
@authorize_company_access(required_roles=list(UserRole))
async def get_unextracted_evidence_files(
    request: Request,
    audit_id: str,
    criteria_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Get a list of unextracted evidence files for a specific criteria."""
    unprocessed_files = get_unprocessed_evidence_files_for_criteria(
        db, audit_id, criteria_id
    )

    if not unprocessed_files:
        raise HTTPException(
            status_code=404,
            detail="No unextracted evidence files found for the given criteria and audit",
        )

    return unprocessed_files
