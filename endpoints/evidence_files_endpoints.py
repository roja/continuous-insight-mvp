from fastapi import APIRouter, Depends, HTTPException, Request, File, UploadFile, status, BackgroundTasks, Query
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session
from sqlalchemy import and_
from typing import List
import os
import hashlib

from database import get_db
from db_models import UserDB, UserRole, EvidenceFileDB, AuditDB
from auth import get_current_user, authorize_company_access
from pydantic_models import EvidenceFileResponse
from helpers import (
    process_file,
    verify_audit_access,
    get_or_404,
    paginate_query,
    filter_by_user_company_access,
)

router = APIRouter(tags=["evidence files"])

@router.post("/audits/{audit_id}/evidence-files", response_model=EvidenceFileResponse)
@authorize_company_access(
    audit_id_param="audit_id",
    required_roles=[
        UserRole.ORGANISATION_USER,
        UserRole.ORGANISATION_LEAD,
        UserRole.AUDITOR,
    ],
)
async def upload_evidence_file(
    request: Request,
    audit_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
    background_tasks: BackgroundTasks = BackgroundTasks(),
):
    """Upload a new evidence file for an audit"""
    # Verify audit exists and isn't deleted
    audit = get_or_404(db, AuditDB, audit_id, "Audit not found")
    if audit.deleted_at is not None:
        raise HTTPException(
            status_code=404,
            detail="Audit not found"
        )

    # Create directory for evidence files if it doesn't exist
    evidence_dir = "evidence_files"
    os.makedirs(evidence_dir, exist_ok=True)

    # Read file content and compute hash
    file_content = await file.read()
    file_hash = hashlib.sha256(file_content).hexdigest()

    # Determine file extension and create the new filename
    file_extension = os.path.splitext(file.filename)[1]
    hash_filename = f"{file_hash}{file_extension}"
    file_path = os.path.join(evidence_dir, hash_filename)

    # Check if this file is already associated with this audit
    existing_association = (
        db.query(EvidenceFileDB)
        .filter(
            and_(
                EvidenceFileDB.audit_id == audit_id,
                EvidenceFileDB.file_path == file_path,
            )
        )
        .first()
    )

    if existing_association:
        raise HTTPException(
            status_code=400,
            detail="This file has already been uploaded for this audit.",
        )

    # Check if a processed file with this hash already exists in the database
    existing_file = (
        db.query(EvidenceFileDB)
        .filter(
            and_(
                EvidenceFileDB.file_path == file_path,
                EvidenceFileDB.status == "complete",
                EvidenceFileDB.text_content != None,
            )
        )
        .first()
    )

    if existing_file:
        # File exists and has been processed, create a new entry with existing content
        db_file = EvidenceFileDB(
            audit_id=audit_id,
            filename=file.filename,  # Keep the original filename in the database
            file_type=file.content_type,
            status="complete",
            file_path=file_path,
            text_content=existing_file.text_content,
            processed_at=existing_file.processed_at,
        )
    else:
        # File doesn't exist or hasn't been processed, save it and queue for processing
        if not os.path.exists(file_path):
            with open(file_path, "wb") as buffer:
                buffer.write(file_content)

        db_file = EvidenceFileDB(
            audit_id=audit_id,
            filename=file.filename,
            file_type=file.content_type,
            status="pending",
            file_path=file_path,
        )

    db.add(db_file)
    db.commit()
    db.refresh(db_file)

    # Start processing in background only if it's a new file that needs processing
    if db_file.status == "pending":
        background_tasks.add_task(process_file, file_path, db, db_file.id)

    return db_file

@router.get("/audits/{audit_id}/evidence-files", response_model=List[EvidenceFileResponse])
@authorize_company_access(required_roles=list(UserRole))
async def list_evidence_files(
    request: Request,
    audit_id: str,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """List all evidence files for an audit with pagination"""
    # Verify audit access
    audit = verify_audit_access(db, audit_id, current_user)
    
    # Build query
    query = db.query(EvidenceFileDB).filter(EvidenceFileDB.audit_id == audit_id)
    
    # Apply pagination
    files = paginate_query(query, skip, limit).all()
    return files

@router.get(
    "/audits/{audit_id}/evidence-files/{file_id}", 
    response_model=EvidenceFileResponse
)
@authorize_company_access(required_roles=list(UserRole))
async def get_evidence_file(
    request: Request,
    audit_id: str,
    file_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Get details of a specific evidence file"""
    # Verify audit access
    audit = verify_audit_access(db, audit_id, current_user)
    
    # Get file or raise 404
    file = get_or_404(
        db, 
        EvidenceFileDB,
        file_id,
        "Evidence file not found"
    )
    
    # Verify file belongs to audit
    if file.audit_id != audit_id:
        raise HTTPException(status_code=404, detail="Evidence file not found")
        
    return file

@router.get("/audits/{audit_id}/evidence-files/{file_id}/content")
@authorize_company_access(required_roles=list(UserRole))
async def get_evidence_file_content(
    request: Request,
    audit_id: str,
    file_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Get the content of an evidence file"""
    file = (
        db.query(EvidenceFileDB)
        .filter(EvidenceFileDB.id == file_id, EvidenceFileDB.audit_id == audit_id)
        .first()
    )
    if file is None:
        raise HTTPException(status_code=404, detail="Evidence file not found")

    if file.status != "processed":
        raise HTTPException(status_code=400, detail="File not processed yet")

    return FileResponse(file.file_path, filename=file.filename)

@router.delete(
    "/audits/{audit_id}/evidence-files/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
@authorize_company_access(
    audit_id_param="audit_id",
    required_roles=[UserRole.AUDITOR, UserRole.ORGANISATION_LEAD],
)
async def delete_evidence_file(
    request: Request,
    audit_id: str,
    file_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Delete an evidence file"""
    # Verify audit access with required roles
    audit = verify_audit_access(
        db, 
        audit_id, 
        current_user, 
        [UserRole.AUDITOR, UserRole.ORGANISATION_LEAD]
    )
    
    # Get file or raise 404
    file = get_or_404(
        db, 
        EvidenceFileDB,
        file_id,
        "Evidence file not found"
    )
    
    # Verify file belongs to audit
    if file.audit_id != audit_id:
        raise HTTPException(status_code=404, detail="Evidence file not found")

    # Delete file from filesystem
    if os.path.exists(file.file_path):
        os.remove(file.file_path)

    # Delete from database
    db.delete(file)
    db.commit()

    return Response(status_code=204)

@router.get(
    "/audits/{audit_id}/evidence-files/{file_id}/status",
    response_model=EvidenceFileResponse,
)
@authorize_company_access(required_roles=list(UserRole))
async def check_evidence_file_status(
    request: Request,
    audit_id: str,
    file_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Check the processing status of an evidence file"""
    file = (
        db.query(EvidenceFileDB)
        .filter(EvidenceFileDB.id == file_id, EvidenceFileDB.audit_id == audit_id)
        .first()
    )
    if file is None:
        raise HTTPException(status_code=404, detail="Evidence file not found")
    return file
