from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy.orm import Session, selectinload, joinedload
from typing import List

from database import get_db
from db_models import (
    UserDB,
    UserRole,
    QuestionDB,
    AuditDB,
    CriteriaDB,
    EvidenceDB,
    AnswerDB,
)
from auth import get_current_user, authorize_company_access
from pydantic_models import (
    QuestionCreate,
    QuestionResponse,
    AnswerCreate,
    AnswerResponse,
)
from helpers import (
    verify_audit_access,
    get_or_404,
    paginate_query,
)
from llm_helpers import generate_questions_using_llm

router = APIRouter(tags=["questions"])

@router.post("/audits/{audit_id}/criteria/{criteria_id}/questions", response_model=List[QuestionResponse])
@authorize_company_access(
    audit_id_param="audit_id",
    required_roles=[UserRole.AUDITOR, UserRole.ORGANISATION_LEAD],
)
async def generate_questions(
    request: Request,
    audit_id: str,
    criteria_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Generate questions for specific criteria based on evidence"""
    # Verify audit access
    audit = verify_audit_access(db, audit_id, current_user, [UserRole.AUDITOR, UserRole.ORGANISATION_LEAD])
    
    # Get criteria or 404
    criteria = get_or_404(db, CriteriaDB, criteria_id, "Criteria not found")

    # Fetch all evidence for this criteria and audit
    evidence_entries = (
        db.query(EvidenceDB)
        .filter(
            EvidenceDB.audit_id == audit_id,
            EvidenceDB.criteria_id == criteria_id,
        )
        .all()
    )

    # Collect evidence content
    evidence_content = ""
    for evidence in evidence_entries:
        if evidence.evidence_type == "summary":
            evidence_content += f"Summary: {evidence.content}\n\n"
        elif evidence.evidence_type == "quote":
            evidence_content += f"Quote: {evidence.content}\n\n"

    # Generate questions using LLM
    questions = generate_questions_using_llm(criteria, evidence_content)

    # Save generated questions to the database
    db_questions = []
    for question_text in questions:
        db_question = QuestionDB(
            audit_id=audit_id, criteria_id=criteria_id, text=question_text
        )
        db.add(db_question)
        db_questions.append(db_question)

    db.commit()
    for question in db_questions:
        db.refresh(question)

    return db_questions

@router.get("/audits/{audit_id}/questions/unanswered", response_model=List[QuestionResponse])
@authorize_company_access(required_roles=list(UserRole))
async def get_unanswered_questions(
    request: Request,
    audit_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Get all unanswered questions for an audit"""
    # Verify audit access
    audit = verify_audit_access(db, audit_id, current_user)
    
    # Build and execute query
    query = db.query(QuestionDB).filter(
        QuestionDB.audit_id == audit_id,
        ~QuestionDB.answers.any()
    )
    questions = query.all()
    return questions

@router.get("/audits/{audit_id}/questions/{question_id}", response_model=QuestionResponse)
@authorize_company_access(required_roles=list(UserRole))
async def get_question_details(
    request: Request,
    audit_id: str,
    question_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Get details of a specific question"""
    # Verify audit access
    audit = verify_audit_access(db, audit_id, current_user)
    
    # Get question or 404
    question = get_or_404(
        db,
        QuestionDB,
        question_id,
        "Question not found"
    )
    
    # Verify question belongs to audit
    if question.audit_id != audit_id:
        raise HTTPException(status_code=404, detail="Question not found")

    return question

@router.post("/audits/{audit_id}/questions/{question_id}/answers", response_model=AnswerResponse)
@authorize_company_access(
    audit_id_param="audit_id",
    required_roles=[UserRole.ORGANISATION_USER, UserRole.ORGANISATION_LEAD],
)
async def submit_answer(
    request: Request,
    audit_id: str,
    question_id: str,
    answer: AnswerCreate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Submit an answer to a question"""
    # Verify audit access with required roles
    audit = verify_audit_access(
        db, 
        audit_id, 
        current_user,
        [UserRole.ORGANISATION_USER, UserRole.ORGANISATION_LEAD]
    )
    
    # Get question or 404
    question = get_or_404(
        db,
        QuestionDB,
        question_id,
        "Question not found"
    )
    
    # Verify question belongs to audit
    if question.audit_id != audit_id:
        raise HTTPException(status_code=404, detail="Question not found")

    db_answer = AnswerDB(
        question_id=question_id, text=answer.text, submitted_by=answer.submitted_by
    )
    db.add(db_answer)
    db.commit()
    db.refresh(db_answer)

    return db_answer

@router.get("/audits/{audit_id}/questions", response_model=List[QuestionResponse])
@authorize_company_access(required_roles=list(UserRole))
async def get_all_questions(
    request: Request,
    audit_id: str,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Get all questions for an audit"""
    # Verify audit access
    audit = verify_audit_access(db, audit_id, current_user)
    
    # Build base query
    query = (
        db.query(QuestionDB)
        .options(selectinload(QuestionDB.answers))
        .filter(QuestionDB.audit_id == audit_id)
    )
    
    # Apply pagination
    questions = paginate_query(query, skip, limit).all()
    return questions

@router.get("/audits/{audit_id}/questions/{question_id}/answers", response_model=List[AnswerResponse])
@authorize_company_access(required_roles=list(UserRole))
async def get_answers_for_question(
    request: Request,
    audit_id: str,
    question_id: str,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Get all answers for a specific question"""
    # Verify audit access
    audit = verify_audit_access(db, audit_id, current_user)
    
    # Get question or 404
    question = get_or_404(
        db,
        QuestionDB,
        question_id,
        "Question not found"
    )
    
    # Verify question belongs to audit
    if question.audit_id != audit_id:
        raise HTTPException(status_code=404, detail="Question not found")
        
    # Get answers
    query = db.query(AnswerDB).filter(AnswerDB.question_id == question_id)
    answers = paginate_query(query, skip, limit).all()
    return answers

@router.get("/audits/{audit_id}/questions/{question_id}/answers/{answer_id}", response_model=AnswerResponse)
@authorize_company_access(required_roles=list(UserRole))
async def get_answer_details(
    request: Request,
    audit_id: str,
    question_id: str,
    answer_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Get details of a specific answer"""
    # Verify audit access
    audit = verify_audit_access(db, audit_id, current_user)
    
    # Get answer or 404
    answer = get_or_404(
        db,
        AnswerDB,
        answer_id,
        "Answer not found"
    )
    
    # Verify answer belongs to question and audit
    if answer.question_id != question_id:
        raise HTTPException(status_code=404, detail="Answer not found")
        
    question = get_or_404(
        db,
        QuestionDB,
        question_id,
        "Question not found"
    )
    
    if question.audit_id != audit_id:
        raise HTTPException(status_code=404, detail="Answer not found")

    return answer
