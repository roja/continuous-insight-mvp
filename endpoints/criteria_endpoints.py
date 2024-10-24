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
    RemoveCriteriaRequest,
    RemoveCriteriaResponse,
    DeleteCustomCriteriaResponse,
    UpdateCustomCriteriaRequest,
    EvidenceResponse,
    QuestionResponse,
    AnswerResponse,
)
from helpers import (
    process_evidence_for_criteria,
    verify_company_access,
    verify_audit_access,
    get_or_404,
    paginate_query,
    filter_by_user_company_access,
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
    return [ac.criteria for ac in audit_criteria]


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


@router.post(
    "/audits/{audit_id}/criteria/selected", response_model=CriteriaSelectionResponse
)
@authorize_company_access(
    audit_id_param="audit_id",
    required_roles=[UserRole.AUDITOR, UserRole.ORGANISATION_LEAD],
)
async def select_criteria(
    request: Request,
    audit_id: str,
    criteria_select: CriteriaSelect,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Select criteria for an audit"""
    audit = get_or_404(db, AuditDB, audit_id, "Audit not found")
    criteria = get_or_404(db, CriteriaDB, criteria_select.criteria_id, "Criterion not found")

    existing_association = (
        db.query(AuditCriteriaDB)
        .filter(
            AuditCriteriaDB.audit_id == audit_id,
            AuditCriteriaDB.criteria_id == criteria_select.criteria_id,
        )
        .first()
    )

    if existing_association:
        existing_association.expected_maturity_level = (
            criteria_select.expected_maturity_level
        )
        db.commit()
        return existing_association
    else:
        new_association = AuditCriteriaDB(
            audit_id=audit_id,
            criteria_id=criteria_select.criteria_id,
            expected_maturity_level=criteria_select.expected_maturity_level,
        )
        db.add(new_association)
        db.commit()
        db.refresh(new_association)
        return new_association


@router.delete(
    "/audits/{audit_id}/criteria/selected", response_model=RemoveCriteriaResponse
)
@authorize_company_access(
    audit_id_param="audit_id",
    required_roles=[UserRole.AUDITOR, UserRole.ORGANISATION_LEAD],
)
async def remove_selected_criteria(
    request: Request,
    audit_id: str,
    criteria_remove: RemoveCriteriaRequest,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Remove selected criteria from an audit"""
    audit = get_or_404(db, AuditDB, audit_id, "Audit not found")

    existing_association = (
        db.query(AuditCriteriaDB)
        .filter(
            AuditCriteriaDB.audit_id == audit_id,
            AuditCriteriaDB.criteria_id == criteria_remove.criteria_id,
        )
        .first()
    )

    if not existing_association:
        raise HTTPException(
            status_code=404, detail="Criteria is not associated with this audit"
        )

    db.delete(existing_association)
    db.commit()

    return RemoveCriteriaResponse(
        message="Criteria successfully removed from the audit",
        audit_id=audit_id,
        criteria_id=criteria_remove.criteria_id,
    )


@router.post(
    "/audits/{audit_id}/criteria/selected/actions/preselect",
    response_model=List[CriteriaSelectionResponse],
)
@authorize_company_access(required_roles=[UserRole.AUDITOR])
async def preselect_criteria(
    request: Request,
    audit_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Preselect criteria for an audit based on analysis"""
    criteria_to_preselect = (
        db.query(CriteriaDB).filter(CriteriaDB.audit_id == audit_id).limit(5).all()
    )

    for criteria in criteria_to_preselect:
        criteria.selected = True
        criteria.expected_maturity_level = "intermediate"

    db.commit()
    return criteria_to_preselect


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

    background_tasks.add_task(process_evidence_for_criteria, audit_id, criteria_id)

    return {"message": "Evidence extraction started"}


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
    evidence = (
        db.query(EvidenceDB)
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
        evidence=[EvidenceResponse.model_validate(e) for e in evidence],
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
