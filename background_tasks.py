import logging
from sqlalchemy.orm import Session
from db_models import CompanyDB, EvidenceFileDB, AuditDB
from llm_helpers import parse_evidence_file
from helpers import process_raw_evidence

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

async def process_company_evidence_task(
    db: Session,
    company_id: str,
    file_ids: list[str] | None = None,
    text_content: str | None = None,
    reprocess_only: bool = False
) -> None:
    """Background task to process company evidence"""
    try:
        # Get the company
        db_company = db.query(CompanyDB).filter(CompanyDB.id == company_id).first()
        if not db_company or db_company.deleted_at is not None:
            logger.error(f"Company {company_id} not found or deleted")
            return

        # Initialize raw evidence if needed
        if db_company.raw_evidence is None:
            db_company.raw_evidence = ""

        # Process file IDs if provided
        if file_ids:
            processed_file_ids = db_company.processed_file_ids or []
            logger.debug(f"Initial processed_file_ids: {processed_file_ids}")

            # Get all valid evidence files that haven't been parsed yet
            evidence_files = (
                db.query(EvidenceFileDB)
                .join(AuditDB)
                .filter(
                    EvidenceFileDB.id.in_(file_ids),
                    AuditDB.company_id == company_id,
                    EvidenceFileDB.status == "complete",
                    EvidenceFileDB.text_content != None,
                    ~EvidenceFileDB.id.in_(processed_file_ids if processed_file_ids else []),
                )
                .all()
            )

            logger.debug(f"Number of valid evidence files to process: {len(evidence_files)}")

            # Process each new evidence file
            new_processed_file_ids = processed_file_ids.copy() if processed_file_ids else []
            for file in evidence_files:
                if not file.text_content:
                    logger.debug(f"Error parsing file {file.id} - no text contents")
                    continue
                    
                parsed_content = parse_evidence_file(file.text_content, db_company.name, file.file_type)
                parsed_content = (
                    "=== This is information gathered from the file "
                    + file.filename
                    + " ===\n\n"
                    + parsed_content
                )
                
                # Append to raw evidence with proper spacing
                if db_company.raw_evidence:
                    db_company.raw_evidence += "\n\n" + parsed_content
                else:
                    db_company.raw_evidence = parsed_content
                    
                new_processed_file_ids.append(file.id)
                logger.debug(f"Processed file ID appended: {file.id}")

            # Update the processed_file_ids
            db_company.processed_file_ids = new_processed_file_ids
            logger.debug(f"Updated processed_file_ids: {db_company.processed_file_ids}")

        # Process direct text content if provided
        if text_content:
            logger.debug("Processing direct text content")
            parsed_content = parse_evidence_file(
                text_content,
                db_company.name,
                "text"  # Default type for direct text input
            )
            parsed_content = (
                "=== This is information provided as direct text ===\n\n"
                + parsed_content
            )
            
            # Append to raw evidence with proper spacing
            if db_company.raw_evidence:
                db_company.raw_evidence += "\n\n" + parsed_content
            else:
                db_company.raw_evidence = parsed_content

        # If this is a reprocess-only request, skip the raw evidence accumulation
        if not reprocess_only:
            # Process the accumulated raw evidence
            process_raw_evidence(db_company, db)
            db.commit()
            logger.debug("Evidence processing completed successfully")
        else:
            # Just reprocess existing raw evidence
            if db_company.raw_evidence:
                process_raw_evidence(db_company, db)
                db.commit()
                logger.debug("Raw evidence reprocessing completed successfully")
            else:
                logger.debug("No raw evidence to reprocess")

    except Exception as e:
        logger.error(f"Error processing evidence: {str(e)}")
        db.rollback()
        raise
