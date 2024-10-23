"""
Helper functions for file processing, database operations, and general utilities.
"""

import os
import math
import tempfile
import logging
from typing import Optional, List, TypeVar, Type, Any
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from fastapi import HTTPException

import ffmpeg
from pydub import AudioSegment
from bs4 import BeautifulSoup
import pypandoc
from fuzzysearch import find_near_matches

from db_models import (
    EvidenceFileDB,
    CriteriaDB,
    EvidenceDB,
    UserDB,
    UserRole,
    AuditDB,
    CompanyDB,
    UserCompanyAssociation,
)
from pydantic_models import CompanyResponse
from database import SessionLocal
from llm_helpers import (
    init_openai_client,
    analyze_image,
    transcribe_audio_chunk,
    extract_evidence_from_text,
    generate_questions_using_llm,
    analyze_company_evidence,
    parse_evidence_file,
)

T = TypeVar("T")


def process_file(file_path: str, db: Session, file_id: str):
    """Process uploaded files and extract their content."""
    db_file = db.query(EvidenceFileDB).filter(EvidenceFileDB.id == file_id).first()
    if not db_file:
        return

    db_file.status = "processing"
    db.commit()

    try:
        file_extension = os.path.splitext(file_path)[1].lower()

        if file_extension in [".mp3", ".wav", ".m4a", ".flac"]:
            text_content = transcribe_audio(file_path)
        elif file_extension in [".mp4", ".avi", ".mov", ".mkv"]:
            audio_path = extract_audio(file_path)
            text_content = transcribe_audio(audio_path)
            os.remove(audio_path)
        elif file_extension in [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"]:
            text_content = analyze_image(file_path)
        else:
            text_content = convert_with_pandoc(file_path)

        if text_content is None:
            raise Exception("Transcription, analysis, or conversion failed")

        db_file.text_content = text_content
        db_file.status = "complete"
        db_file.processed_at = datetime.now(timezone.utc)
    except Exception as e:
        db_file.status = "failed"
        db_file.processed_at = datetime.now(timezone.utc)
        print(f"Error processing file {file_path}: {str(e)}")

    db.commit()


def extract_audio(video_path: str) -> str:
    """Extract audio from video files."""
    output_path = video_path.rsplit(".", 1)[0] + ".mp3"
    stream = ffmpeg.input(video_path)
    stream = ffmpeg.output(stream, output_path, acodec="libmp3lame")
    ffmpeg.run(stream, overwrite_output=True)
    return output_path


def transcribe_audio(audio_path: str) -> Optional[str]:
    """Transcribe audio content using OpenAI's Whisper API."""
    audio = AudioSegment.from_file(audio_path)
    max_chunk_duration_ms = 15 * 60 * 1000  # 15 minutes in milliseconds
    num_chunks = math.ceil(len(audio) / max_chunk_duration_ms)
    transcripts: List[Optional[str]] = [None] * num_chunks

    for i in range(num_chunks):
        start_ms = i * max_chunk_duration_ms
        end_ms = min((i + 1) * max_chunk_duration_ms, len(audio))
        chunk = audio[start_ms:end_ms]

        with tempfile.NamedTemporaryFile(
            suffix=".mp3", delete=False
        ) as temp_audio_file:
            chunk.export(temp_audio_file.name, format="mp3")
            temp_audio_file.close()

            transcript = transcribe_audio_chunk(temp_audio_file.name)
            if transcript:
                transcripts[i] = transcript

            os.unlink(temp_audio_file.name)

    if None in transcripts:
        print("Transcription failed: Some chunks could not be transcribed.")
        return None

    return "\n".join(transcripts)


def convert_with_pandoc(file_path: str) -> str:
    """Convert documents to text using Pandoc."""
    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            html_content = pypandoc.convert_file(
                file_path, to="html", extra_args=["--extract-media=" + temp_dir]
            )
            processed_content = process_images(html_content, temp_dir)
            markdown_content = pypandoc.convert_text(
                processed_content, to="markdown", format="html"
            )
            return markdown_content
        except Exception as e:
            raise Exception(
                f"Pandoc conversion failed for file: {file_path}. Error: {str(e)}"
            )


def process_images(content: str, image_dir: str) -> str:
    """Process images in converted documents."""
    soup = BeautifulSoup(content, "html.parser")

    for img in soup.find_all("img"):
        src = img.get("src")
        if src and not src.startswith("http"):
            full_image_path = os.path.join(image_dir, src)

            if os.path.exists(full_image_path):
                image_description = analyze_image(full_image_path)
                if image_description:
                    description_p = soup.new_tag("p")
                    description_p.string = f"Image Description: {image_description}"
                    img.insert_after(description_p)

    return str(soup)


def find_quote_start_position(quote: str, document: str) -> Optional[int]:
    """Find the starting position of a quote in a document."""
    max_l_dist = max(2, int(len(quote) * 0.1))
    matches = find_near_matches(quote, document, max_l_dist=max_l_dist)

    if matches:
        best_match = min(matches, key=lambda x: x.dist)
        return best_match.start
    return None


def process_evidence_for_criteria(audit_id: str, criteria_id: str):
    """Process evidence files for specific criteria."""
    db = SessionLocal()
    try:
        criteria = db.query(CriteriaDB).filter(CriteriaDB.id == criteria_id).first()
        if not criteria:
            print(f"Criteria {criteria_id} not found.")
            return

        evidence_files = (
            db.query(EvidenceFileDB).filter(EvidenceFileDB.audit_id == audit_id).all()
        )

        for file in evidence_files:
            # Check if already processed for this criteria
            existing_evidence = (
                db.query(EvidenceDB)
                .filter(
                    EvidenceDB.audit_id == audit_id,
                    EvidenceDB.criteria_id == criteria_id,
                    EvidenceDB.source == "evidence_file",
                    EvidenceDB.source_id == file.id,
                )
                .first()
            )
            if existing_evidence or not file.text_content:
                continue

            summary, extracted_evidence_list = extract_evidence_from_text(
                file.text_content, criteria
            )

            if summary:
                new_summary_evidence = EvidenceDB(
                    audit_id=audit_id,
                    criteria_id=criteria_id,
                    content=summary,
                    evidence_type="summary",
                    source="evidence_file",
                    source_id=file.id,
                )
                db.add(new_summary_evidence)

            for evidence_text in extracted_evidence_list:
                start_position = find_quote_start_position(
                    evidence_text, file.text_content
                )

                new_quote_evidence = EvidenceDB(
                    audit_id=audit_id,
                    criteria_id=criteria_id,
                    content=evidence_text,
                    evidence_type="quote",
                    source="evidence_file",
                    source_id=file.id,
                    start_position=start_position,
                )
                db.add(new_quote_evidence)

            db.commit()

    except Exception as e:
        print(
            f"Error processing evidence for audit {audit_id} and criteria {criteria_id}: {str(e)}"
        )
    finally:
        db.close()


def parse_single_evidence_file(file: EvidenceFileDB, db_company: CompanyDB) -> str:
    """Parse a single evidence file for company information."""
    if not file.text_content:
        print(f"Error parsing file {file.id} - no text contents")
        return ""

    return parse_evidence_file(file.text_content, db_company.name, file.file_type)


def process_raw_evidence(db_company: CompanyDB, db: Session) -> CompanyResponse:
    """Process raw evidence and update company information."""
    if not db_company.raw_evidence:
        raise HTTPException(status_code=400, detail="No raw evidence to process")

    try:
        # Get the analyzed information
        company_info = analyze_company_evidence(db_company.raw_evidence)

        # Update the company record
        for key, value in company_info.items():
            if key == "areas_of_focus" and isinstance(value, list):
                value = ",".join(value[:10])  # Limit to 10 areas
            setattr(db_company, key, value)

        db_company.updated_from_evidence = True
        db.commit()
        db.refresh(db_company)

        return db_company

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def get_or_404(db: Session, model: Type[T], id: str, detail: str = None) -> T:
    """
    Get a database record by ID or raise a 404 exception.

    Args:
        db: Database session
        model: SQLAlchemy model class
        id: Record ID to look up
        detail: Custom error message (optional)

    Returns:
        The found database record

    Raises:
        HTTPException: 404 if record not found
    """
    instance = db.query(model).filter(model.id == id).first()
    if not instance:
        raise HTTPException(
            status_code=404,
            detail=detail or f"{model.__name__.replace('DB', '')} not found",
        )
    return instance


def verify_audit_access(
    db: Session,
    audit_id: str,
    user: UserDB,
    required_roles: Optional[list[UserRole]] = None,
) -> AuditDB:
    """
    Verify a user has access to an audit and optionally check for specific roles.

    Args:
        db: Database session
        audit_id: ID of audit to check
        user: Current user
        required_roles: List of required roles (optional)

    Returns:
        The audit if access is granted

    Raises:
        HTTPException: 403 if access denied, 404 if audit not found
    """
    audit = get_or_404(db, AuditDB, audit_id)

    if user.is_global_administrator:
        return audit

    company_association = (
        db.query(UserCompanyAssociation)
        .filter(
            UserCompanyAssociation.user_id == user.id,
            UserCompanyAssociation.company_id == audit.company_id,
        )
        .first()
    )

    if not company_association:
        raise HTTPException(
            status_code=403, detail="You don't have access to this audit"
        )

    if required_roles and company_association.role not in required_roles:
        raise HTTPException(
            status_code=403,
            detail="You don't have the required role for this operation",
        )

    return audit


def verify_company_access(
    db: Session,
    company_id: str,
    user: UserDB,
    required_roles: Optional[list[UserRole]] = None,
) -> CompanyDB:
    """
    Verify a user has access to a company and optionally check for specific roles.

    Args:
        db: Database session
        company_id: ID of company to check
        user: Current user
        required_roles: List of required roles (optional)

    Returns:
        The company if access is granted

    Raises:
        HTTPException: 403 if access denied, 404 if company not found
    """
    company = get_or_404(db, CompanyDB, company_id)

    if user.is_global_administrator:
        return company

    company_association = (
        db.query(UserCompanyAssociation)
        .filter(
            UserCompanyAssociation.user_id == user.id,
            UserCompanyAssociation.company_id == company_id,
        )
        .first()
    )

    if not company_association:
        raise HTTPException(
            status_code=403, detail="You don't have access to this company"
        )

    if required_roles and company_association.role not in required_roles:
        raise HTTPException(
            status_code=403,
            detail="You don't have the required role for this operation",
        )

    return company


def paginate_query(query: Any, skip: int = 0, limit: int = 100):
    """
    Add pagination to a SQLAlchemy query.

    Args:
        query: SQLAlchemy query object
        skip: Number of records to skip
        limit: Maximum number of records to return

    Returns:
        Query with pagination applied
    """
    return query.offset(skip).limit(limit)


def filter_by_user_company_access(query: Any, user: UserDB, company_join_path=None):
    """
    Filter a query to only show records the user has access to via company associations.

    Args:
        query: SQLAlchemy query object
        user: Current user
        company_join_path: Optional path to join to CompanyDB if not direct

    Returns:
        Filtered query
    """
    if user.is_global_administrator:
        return query

    if company_join_path:
        query = query.join(company_join_path)

    return (
        query.join(CompanyDB)
        .join(UserCompanyAssociation)
        .filter(UserCompanyAssociation.user_id == user.id)
    )
