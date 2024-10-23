"""
TechAudit: Comprehensive Technology and Product Audit System

This codebase implements a sophisticated system for conducting technology and product audits
of companies across various sectors and sizes. The primary goal is to assess the maturity
and effectiveness of a company's technology stack, processes, and product development approaches.

Key Concepts:

1. Audit Framework:
   The system is built around the concept of the "5 Ps": Product, People, Process, Platform,
   and Protection. These form the core areas of assessment in any audit.

2. Maturity Levels:
   For each area and sub-area of assessment, the system uses three maturity levels:
   Novice, Intermediate, and Advanced. These levels are defined specifically for each
   criterion to provide nuanced evaluation.

3. Evidence-Based Assessment:
   The audit process is driven by evidence gathered from various sources including
   interviews, documentation, code repositories, and system logs. The system supports
   uploading and processing of multiple file types to extract relevant information.

4. Dynamic Questioning:
   Based on the criteria and gathered evidence, the system generates tailored questions
   to fill information gaps. This process is iterative, with follow-up questions generated
   based on previous answers.

5. AI-Assisted Analysis:
   The system leverages large language models to assist in various tasks such as evidence
   extraction, question generation, and initial maturity assessments. However, final
   assessments are made by human auditors.

6. Customizable Criteria:
   While there's a standard set of criteria, the system allows for customization of
   criteria for specific audits, recognizing that different companies and sectors may
   have unique requirements.

7. Company Context:
   The system takes into account the size, sector, and specific context of each company
   being audited, adjusting expectations and assessments accordingly.

8. Comprehensive Reporting:
   The end result of the audit process is a detailed report highlighting the company's
   strengths, areas for improvement, and specific recommendations, tailored to different
   audience levels (e.g., technical teams, management, board level).

Technical Overview:

- The system is built using FastAPI, providing a robust and fast API for all operations.
- SQLAlchemy is used for ORM, with models representing key entities like Audits, Companies,
  Criteria, Evidence, Questions, and Maturity Assessments.
- Asynchronous processing is employed for handling file uploads and content extraction.
- Integration with OpenAI's API allows for AI-assisted analysis and question generation.
- The system is designed to be scalable, supporting concurrent audits and large volumes
  of evidence processing.

This codebase represents a sophisticated tool for technology auditing, combining
structured assessment methodologies with AI-assisted analysis to provide comprehensive
and nuanced evaluations of a company's technology and product maturity.
"""

# Standard library imports
import json
import os
import random
import shutil
import uuid
import time
import math
import tempfile
import ffmpeg
import subprocess
import base64
import pypandoc
import re
import hashlib
import logging
import secrets


from bs4 import BeautifulSoup
from openai import OpenAI
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import List, Optional, Dict, Tuple, Callable
from pydub import AudioSegment

from fastapi import (
    FastAPI,
    HTTPException,
    Depends,
    File,
    UploadFile,
    status,
    BackgroundTasks,
    Query,
    Request,
    Body,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from pydantic import BaseModel, Field, field_validator, ConfigDict, validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Boolean,
    Text,
    ForeignKey,
    JSON,
    DateTime,
    func,
    and_,
    UniqueConstraint,
    CheckConstraint,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import (
    sessionmaker,
    Session,
    relationship,
    selectinload,
    backref,
    joinedload,
)
from sqlalchemy.sql import expression
from sqlalchemy.types import JSON

from google.oauth2 import id_token
from google.auth.transport import requests as google_auth_requests

from fuzzysearch import find_near_matches
from authlib.integrations.starlette_client import OAuth
from jose import JWTError, jwt

from starlette.middleware.sessions import SessionMiddleware
from starlette.config import Config

from functools import wraps


class Settings(BaseSettings):
    database_url: str = Field(default="sqlite:///./tech_audit.db")
    api_key: str = Field(default="key")
    openai_api_key: str = Field(default="your_openai_api_key_here")

    google_client_id: str = Field(default="your_google_client_id")
    google_client_secret: str = Field(default="your_google_client_secret")
    apple_client_id: str = Field(default="your_apple_client_id")
    apple_client_secret: str = Field(default="your_apple_client_secret")
    jwt_secret_key: str = Field(default="your_jwt_secret_key")
    jwt_algorithm: str = Field(default="HS256")

    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()


oauth = OAuth()
oauth.register(
    name="google",
    client_id=settings.google_client_id,
    client_secret=settings.google_client_secret,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

oauth.register(
    name="apple",
    client_id=settings.apple_client_id,
    client_secret=settings.apple_client_secret,
    server_metadata_url="https://appleid.apple.com/.well-known/openid-configuration",
    client_kwargs={"scope": "email name"},
)

auth_scheme = HTTPBearer()


openAiClient = OpenAI(api_key=settings.openai_api_key)


# Database setup
engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# SQLAlchemy models
class UserRole(str, Enum):
    AUDITOR = "auditor"
    ORGANISATION_LEAD = "organisation_lead"
    ORGANISATION_USER = "organisation_user"
    DELEGATED_USER = "delegated_user"
    OBSERVER_LEAD = "observer_lead"
    OBSERVER_USER = "observer_user"


class UserDB(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    email = Column(String, unique=True, index=True)
    name = Column(String)
    oauth_provider = Column(String)  # 'google' or 'apple'
    oauth_id = Column(String, unique=True)
    is_global_administrator = Column(
        Boolean, default=False
    )  # Flag for system-wide auditor access
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    company_associations = relationship(
        "UserCompanyAssociation", back_populates="user", overlaps="companies,users"
    )
    companies = relationship(
        "CompanyDB",
        secondary="user_company_associations",
        viewonly=True,  # Make this a read-only relationship
    )

    @property
    def company_roles(self) -> Dict[str, UserRole]:
        """Returns a mapping of company_id to role"""
        return {
            assoc.company_id: UserRole(assoc.role)
            for assoc in self.company_associations
        }

    @property
    def accessible_companies(self) -> List[str]:
        """Returns list of company IDs the user has access to"""
        return [assoc.company_id for assoc in self.company_associations]

    def has_company_role(self, company_id: str, required_roles: List[UserRole]) -> bool:
        """Check if user has any of the required roles for a company"""
        if self.is_global_administrator:
            return True

        for assoc in self.company_associations:
            if assoc.company_id == company_id:
                return UserRole(assoc.role) in required_roles
        return False


class UserCompanyAssociation(Base):
    __tablename__ = "user_company_associations"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"))
    company_id = Column(String, ForeignKey("companies.id", ondelete="CASCADE"))
    role = Column(String, nullable=False)  # Uses UserRole enum values
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    user = relationship("UserDB", back_populates="company_associations")
    company = relationship("CompanyDB", back_populates="user_associations")

    __table_args__ = (
        UniqueConstraint("user_id", "company_id", name="uq_user_company"),
        CheckConstraint(role.in_([r.value for r in UserRole]), name="valid_role"),
    )


class AuditDB(Base):
    """
    Represents an audit in the system, serving as the central model for the audit process.

    This class encapsulates all the core information and relationships related to an audit,
    including the company being audited, criteria being evaluated, evidence files, questions,
    and maturity assessments.

    Attributes:
        id (str): Unique identifier for the audit, auto-generated UUID.
        company_id (str): Foreign key linking to the company being audited.
        name (str): Name of the audit for quick identification.
        description (str): Optional description of the audit's purpose or scope.
        created_at (datetime): Timestamp of audit creation.
        updated_at (datetime): Timestamp of last update to the audit.

    Relationships:
        company: The company being audited.
        audit_criteria: Criteria associated with this audit.
        evidence_files: Evidence files uploaded for this audit.
        questions: Questions generated and answered during the audit.
        maturity_assessments: Maturity assessments made based on audit findings.
        custom_criteria: Custom criteria specific to this audit.

    Usage:
        This model is central to the audit process and is typically used in conjunction
        with other models to create a comprehensive audit. It supports the multi-stage
        audit process including initial data gathering, criteria selection, question
        generation, evidence analysis, and maturity assessment.

    Note:
        When creating a new audit, ensure that a valid company_id is provided.
        The created_at and updated_at fields are automatically managed by the database.

    Example:
        new_audit = AuditDB(name="Q2 2023 Tech Audit",
                            company_id="company_uuid",
                            description="Comprehensive tech stack evaluation")
        db.add(new_audit)
        db.commit()
    """

    __tablename__ = "audits"
    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String, ForeignKey("companies.id"))
    name = Column(String, index=True)
    description = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    company = relationship("CompanyDB", back_populates="audits")
    audit_criteria = relationship("AuditCriteriaDB", back_populates="audit")
    evidence_files = relationship("EvidenceFileDB", back_populates="audit")
    questions = relationship("QuestionDB", back_populates="audit")
    maturity_assessments = relationship("MaturityAssessmentDB", back_populates="audit")

    # New relationship to CriteriaDB
    custom_criteria = relationship("CriteriaDB", back_populates="specific_audit")


class CompanyDB(Base):
    """
    Represents a company in the system, serving as a central model for storing and managing company information.

    This class encapsulates all core information about a company, including its details, technology stack,
    and relationships to audits. It's designed to support the comprehensive evaluation of a company's
    technical maturity and capabilities.

    Attributes:
        id (str): Unique identifier for the company, auto-generated UUID.
        name (str): Name of the company.
        description (str): Optional description of the company.
        sector (str): The industry sector the company operates in.
        size (str): Size category of the company (e.g., "micro", "small", "medium", "large").
        business_type (str): Type of business (e.g., "B2B", "B2C", "mixed").
        technology_stack (str): Overview of the company's technology stack.
        areas_of_focus (str): Comma-separated string of the company's focus areas.
        updated_from_evidence (bool): Indicates if the company info has been updated based on evidence.
        created_at (datetime): Timestamp of when the company record was created.
        updated_at (datetime): Timestamp of the last update to the company record.
        raw_evidence (Text): Raw text evidence about the company, typically from interviews or documents.
        processed_file_ids (List[str]): List of IDs of evidence files that have been processed.

    Relationships:
        audits: All audits associated with this company.

    Usage:
        This model is used to store and retrieve information about companies being audited.
        It supports the initial data gathering phase of the audit process and serves as a
        reference point for all audits related to a specific company.

    Note:
        - The 'areas_of_focus' field is stored as a comma-separated string but is typically
          handled as a list in the application layer.
        - The 'updated_from_evidence' flag helps track whether the company information
          has been automatically updated based on processed evidence.
        - The 'raw_evidence' field can store large amounts of text data, useful for
          maintaining context from interviews or document analysis.

    Example:
        new_company = CompanyDB(
            name="TechCorp Inc.",
            sector="Software Development",
            size="medium",
            business_type="B2B",
            technology_stack="Python, React, AWS",
            areas_of_focus="Cloud Computing,AI/ML,DevOps"
        )
        db.add(new_company)
        db.commit()
    """

    __tablename__ = "companies"
    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, index=True)
    description = Column(String, nullable=True)
    sector = Column(String, nullable=True)
    size = Column(String, nullable=True)
    business_type = Column(String, nullable=True)
    technology_stack = Column(String, nullable=True)
    areas_of_focus = Column(String, nullable=True)  # Store as comma-separated string
    updated_from_evidence = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    raw_evidence = Column(Text, nullable=True)
    processed_file_ids = Column(MutableList.as_mutable(JSON), default=[])

    audits = relationship("AuditDB", back_populates="company")
    user_associations = relationship(
        "UserCompanyAssociation", back_populates="company", overlaps="users"
    )
    users = relationship(
        "UserDB",
        secondary="user_company_associations",
        viewonly=True,  # Make this a read-only relationship
    )


class EvidenceFileDB(Base):
    """
    Represents an evidence file in the audit system, storing metadata and processed content of uploaded files.

    This class is crucial for managing and tracking various types of evidence files (documents, images, audio, video)
    that are uploaded as part of the audit process. It handles the file metadata, processing status, and extracted
    text content, supporting the evidence gathering and analysis phases of the audit.

    Attributes:
        id (str): Unique identifier for the evidence file, auto-generated UUID.
        audit_id (str): Foreign key linking to the associated audit.
        filename (str): Original name of the uploaded file.
        file_type (str): MIME type or general category of the file.
        status (str): Current status of file processing (e.g., "pending", "processing", "complete", "failed").
        file_path (str): Path where the file is stored in the system.
        uploaded_at (datetime): Timestamp when the file was uploaded.
        processed_at (datetime): Timestamp when file processing was completed (or failed).
        text_content (Text): Extracted or transcribed text content from the file.

    Relationships:
        audit: The audit this evidence file is associated with.

    Usage:
        This model is used to track and manage evidence files throughout the audit process. It supports
        the initial data gathering phase, where various types of documents and media files are uploaded
        as evidence. The system processes these files to extract relevant information, which is then
        used in the audit analysis.

    Note:
        - The 'status' field should be updated as the file goes through different processing stages.
        - 'text_content' may contain large amounts of text for document files, transcripts for audio/video,
          or extracted text from images.
        - File processing is typically handled asynchronously, with results updated in this model.

    Example:
        new_evidence = EvidenceFileDB(
            audit_id="audit_uuid",
            filename="company_structure.pdf",
            file_type="application/pdf",
            status="pending",
            file_path="/path/to/evidence/files/company_structure.pdf"
        )
        db.add(new_evidence)
        db.commit()

        # After processing:
        new_evidence.status = "complete"
        new_evidence.processed_at = datetime.now(timezone.utc)
        new_evidence.text_content = "Extracted text from PDF..."
        db.commit()
    """

    __tablename__ = "evidence_files"
    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    audit_id = Column(String, ForeignKey("audits.id"))
    filename = Column(String)
    file_type = Column(String)
    status = Column(String)
    file_path = Column(String)  # Add this line
    uploaded_at = Column(DateTime(timezone=True), server_default=func.now())
    processed_at = Column(DateTime(timezone=True), nullable=True)
    text_content = Column(Text, nullable=True)

    audit = relationship("AuditDB", back_populates="evidence_files")


class CriteriaDB(Base):
    """
    Represents an audit criterion in the system, defining the standards against which companies are evaluated.

    This class is central to the audit process, encapsulating the structure and content of audit criteria.
    It supports both standard criteria applicable across audits and custom criteria specific to particular audits.
    The hierarchical structure allows for main criteria and sub-criteria, enabling detailed and nuanced evaluations.

    Attributes:
        id (str): Unique identifier for the criterion, auto-generated UUID.
        parent_id (str): ID of the parent criterion, if this is a sub-criterion.
        title (str): Title or name of the criterion.
        description (str): Detailed description of what the criterion evaluates.
        maturity_definitions (JSON): JSON object defining maturity levels (e.g., novice, intermediate, advanced).
        is_specific_to_audit (str): ID of the audit if this is a custom criterion, or None if it's a standard criterion.
        section (str): The section or category this criterion belongs to (e.g., "product", "process", "platform").
        created_at (datetime): Timestamp of when the criterion was created.
        updated_at (datetime): Timestamp of the last update to the criterion.

    Relationships:
        parent: The parent criterion (if this is a sub-criterion).
        children: List of sub-criteria (if this is a parent criterion).
        audit_associations: Audits associated with this criterion.
        evidence: Evidence linked to this criterion.
        questions: Questions related to this criterion.
        maturity_assessment: Maturity assessment for this criterion.
        specific_audit: The audit this criterion is specific to (if it's a custom criterion).

    Usage:
        This model is used to define and manage the criteria used in audits. It supports the creation
        of both standard criteria that can be reused across audits and custom criteria tailored for
        specific audits. The hierarchical structure allows for complex, multi-level criteria definitions.

    Note:
        - The 'maturity_definitions' field should contain a structured JSON object defining different
          maturity levels and their descriptions.
        - When creating a sub-criterion, ensure to set the 'parent_id' to link it to its parent.
        - Custom criteria for specific audits should have the 'is_specific_to_audit' field set.

    Example:
        # Creating a main criterion
        main_criterion = CriteriaDB(
            title="Data Management",
            description="Evaluates the company's approach to managing and utilizing data",
            maturity_definitions={
                "novice": "Basic data storage with minimal analysis",
                "intermediate": "Structured data management with regular analysis",
                "advanced": "Advanced data analytics and AI-driven insights"
            },
            section="platform"
        )
        db.add(main_criterion)
        db.commit()

        # Creating a sub-criterion
        sub_criterion = CriteriaDB(
            parent_id=main_criterion.id,
            title="Data Security",
            description="Assesses the measures in place to protect sensitive data",
            maturity_definitions={
                "novice": "Basic access controls",
                "intermediate": "Encryption and access logging",
                "advanced": "Advanced threat detection and prevention"
            },
            section="platform"
        )
        db.add(sub_criterion)
        db.commit()
    """

    __tablename__ = "criteria"
    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    parent_id = Column(String, ForeignKey("criteria.id"), nullable=True)
    title = Column(String)
    description = Column(String)
    maturity_definitions = Column(JSON)
    is_specific_to_audit = Column(
        String, ForeignKey("audits.id"), nullable=True
    )  # New column
    section = Column(String)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    audit_associations = relationship("AuditCriteriaDB", back_populates="criteria")
    evidence = relationship("EvidenceDB", back_populates="criteria")
    questions = relationship("QuestionDB", back_populates="criteria")
    maturity_assessment = relationship(
        "MaturityAssessmentDB", back_populates="criteria", uselist=False
    )

    children = relationship(
        "CriteriaDB",
        backref=backref("parent", remote_side=[id]),
        cascade="all, delete-orphan",
    )

    # New relationship to AuditDB
    specific_audit = relationship("AuditDB", back_populates="custom_criteria")

    def __repr__(self):
        return f"<Criteria(id='{self.id}', title='{self.title}', parent_id='{self.parent_id}', is_specific_to_audit='{self.is_specific_to_audit}')>"


class AuditCriteriaDB(Base):
    """
    Represents the association between an audit and a specific criterion, including the expected maturity level.

    This class serves as a junction table, linking audits with their relevant criteria. It allows for customization
    of criteria for each audit, including setting expected maturity levels. This model is crucial for tailoring
    the audit process to each company's specific context and goals.

    Attributes:
        id (str): Unique identifier for the audit-criteria association, auto-generated UUID.
        audit_id (str): Foreign key linking to the associated audit.
        criteria_id (str): Foreign key linking to the associated criterion.
        expected_maturity_level (str): The expected maturity level for this criterion in this specific audit.
        created_at (datetime): Timestamp of when the association was created.
        updated_at (datetime): Timestamp of the last update to the association.

    Relationships:
        audit: The audit this criterion is associated with.
        criteria: The criterion associated with this audit.

    Usage:
        This model is used to associate specific criteria with an audit and set expected maturity levels.
        It supports the customization of the audit process by allowing auditors to select which criteria
        are relevant for each audit and what level of maturity is expected for each criterion.

    Note:
        - The 'expected_maturity_level' should align with the maturity levels defined in the CriteriaDB model
          (typically 'novice', 'intermediate', or 'advanced').
        - This model allows for the same criterion to be used in multiple audits with different expected
          maturity levels, tailoring the assessment to each company's context.
        - When creating a new audit, you would typically create multiple AuditCriteriaDB instances to
          associate all relevant criteria with the audit.

    Example:
        # Assuming we have an audit and a criterion
        audit_id = "existing_audit_uuid"
        criteria_id = "existing_criteria_uuid"

        audit_criteria = AuditCriteriaDB(
            audit_id=audit_id,
            criteria_id=criteria_id,
            expected_maturity_level="intermediate"
        )
        db.add(audit_criteria)
        db.commit()

        # Later, updating the expected maturity level
        audit_criteria.expected_maturity_level = "advanced"
        db.commit()
    """

    __tablename__ = "audit_criteria"
    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    audit_id = Column(String, ForeignKey("audits.id"))
    criteria_id = Column(String, ForeignKey("criteria.id"))
    expected_maturity_level = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    audit = relationship("AuditDB", back_populates="audit_criteria")
    criteria = relationship("CriteriaDB", back_populates="audit_associations")


class EvidenceDB(Base):
    """
    Represents a piece of evidence collected during the audit process, linked to specific criteria and audits.

    This class is crucial for storing and managing the various pieces of evidence gathered throughout
    the audit. It supports different types of evidence (e.g., quotes, summaries) from various sources,
    allowing for a comprehensive and structured approach to evidence collection and analysis.

    Attributes:
        id (str): Unique identifier for the evidence, auto-generated UUID.
        audit_id (str): Foreign key linking to the associated audit.
        criteria_id (str): Foreign key linking to the associated criterion.
        content (Text): The actual content of the evidence (e.g., a quote, summary, or observation).
        source (str): The source of the evidence (e.g., "interview", "document", "system_log").
        source_id (str): Identifier for the specific source (e.g., file ID, interview ID).
        evidence_type (str): Type of evidence (e.g., "quote", "summary", "observation").
        start_position (Integer): For quoted evidence, the starting position in the source text.
        created_at (datetime): Timestamp of when the evidence was recorded.

    Relationships:
        criteria: The criterion this evidence is associated with.

    Usage:
        This model is used to store and retrieve evidence collected during the audit process.
        It supports the evidence gathering and analysis phases, allowing auditors to link
        specific pieces of evidence to relevant criteria and track their sources.

    Note:
        - The 'content' field may contain substantial text, especially for summaries or long quotes.
        - 'evidence_type' helps categorize different forms of evidence, which can be useful for
          analysis and reporting.
        - 'start_position' is particularly useful for quotes, allowing traceability back to the
          original source document.
        - When adding evidence, ensure it's linked to both the correct audit and criterion.

    Example:
        # Adding a quote as evidence
        new_evidence = EvidenceDB(
            audit_id="audit_uuid",
            criteria_id="criteria_uuid",
            content="The company uses a microservices architecture with 20 separate services.",
            source="interview",
            source_id="interview_123",
            evidence_type="quote",
            start_position=1542
        )
        db.add(new_evidence)
        db.commit()

        # Adding a summary as evidence
        summary_evidence = EvidenceDB(
            audit_id="audit_uuid",
            criteria_id="criteria_uuid",
            content="The development team follows Agile methodologies, with two-week sprints and daily stand-ups.",
            source="document",
            source_id="process_doc_456",
            evidence_type="summary"
        )
        db.add(summary_evidence)
        db.commit()
    """

    __tablename__ = "evidence"
    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    audit_id = Column(String, ForeignKey("audits.id"))
    criteria_id = Column(String, ForeignKey("criteria.id"))
    content = Column(Text)
    source = Column(String)
    source_id = Column(String)
    evidence_type = Column(String, nullable=False, default="quote")
    start_position = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    criteria = relationship("CriteriaDB", back_populates="evidence")


class QuestionDB(Base):
    """
    Represents a question generated during the audit process, linked to specific criteria and audits.

    This class is central to the interactive audit process, storing questions generated based on
    criteria and evidence gaps. It supports the iterative nature of the audit, allowing for
    dynamic question generation and answer collection to gather comprehensive information.

    Attributes:
        id (str): Unique identifier for the question, auto-generated UUID.
        audit_id (str): Foreign key linking to the associated audit.
        criteria_id (str): Foreign key linking to the associated criterion.
        text (Text): The actual text of the question.
        created_at (datetime): Timestamp of when the question was created.

    Relationships:
        audit: The audit this question is associated with.
        criteria: The criterion this question is related to.
        answers: List of answers provided for this question.

    Usage:
        This model is used to store and manage questions generated during the audit process.
        It supports the dynamic and iterative nature of information gathering, allowing the
        system to generate follow-up questions based on previous answers and evidence gaps.

    Note:
        - Questions are typically generated automatically by the system based on criteria
          and existing evidence, but can also be manually added by auditors.
        - The 'text' field may contain complex or multi-part questions.
        - Questions are linked to both an audit and a specific criterion, allowing for
          targeted information gathering.
        - The relationship with answers allows for tracking responses and potentially
          generating follow-up questions.

    Example:
        # Generating a question for a specific audit and criterion
        new_question = QuestionDB(
            audit_id="audit_uuid",
            criteria_id="criteria_uuid",
            text="What kind of datastore approach do you have for your main application data?"
        )
        db.add(new_question)
        db.commit()

        # Generating a follow-up question based on an answer
        follow_up_question = QuestionDB(
            audit_id="audit_uuid",
            criteria_id="criteria_uuid",
            text="You mentioned using MongoDB. How is your MongoDB cluster set up for scalability and redundancy?"
        )
        db.add(follow_up_question)
        db.commit()
    """

    __tablename__ = "questions"
    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    audit_id = Column(String, ForeignKey("audits.id"))
    criteria_id = Column(String, ForeignKey("criteria.id"))
    text = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    audit = relationship("AuditDB", back_populates="questions")
    criteria = relationship("CriteriaDB", back_populates="questions")
    answers = relationship("AnswerDB", back_populates="question")


class AnswerDB(Base):
    """
    Represents an answer to a specific question in the audit process.

    This class is crucial for storing and managing the responses provided by the audited company
    to the questions generated during the audit. It forms a key part of the evidence gathering
    process, providing detailed insights into the company's practices and maturity levels.

    Attributes:
        id (str): Unique identifier for the answer, auto-generated UUID.
        question_id (str): Foreign key linking to the associated question.
        text (Text): The actual content of the answer.
        submitted_by (str): Identifier or name of the person who provided the answer.
        created_at (datetime): Timestamp of when the answer was submitted.

    Relationships:
        question: The question this answer is associated with.

    Usage:
        This model is used to store and retrieve answers provided by the audited company.
        It supports the interactive nature of the audit process, allowing for the collection
        and analysis of detailed responses to specific questions about the company's practices,
        technologies, and processes.

    Note:
        - The 'text' field may contain lengthy and detailed responses, potentially including
          technical details or explanations of company practices.
        - The 'submitted_by' field helps in tracking the source of information within the
          audited company, which can be useful for follow-up or clarification.
        - Answers are linked to specific questions, which in turn are linked to criteria,
          allowing for structured analysis of the gathered information.
        - The system may use these answers to generate follow-up questions or as evidence
          for assessing maturity levels.

    Example:
        # Recording an answer to a specific question
        new_answer = AnswerDB(
            question_id="question_uuid",
            text="We use a MongoDB cluster with three replica sets for our main application data. "
                 "The cluster is hosted on AWS and configured for automatic failover and scaling.",
            submitted_by="John Doe, Lead DevOps Engineer"
        )
        db.add(new_answer)
        db.commit()

        # Recording a follow-up answer
        follow_up_answer = AnswerDB(
            question_id="follow_up_question_uuid",
            text="Our MongoDB cluster is set up with three shards, each having a primary and two "
                 "secondary nodes. We use MongoDB Atlas for managed hosting, which provides "
                 "automated backups, monitoring, and horizontal scaling capabilities.",
            submitted_by="Jane Smith, Database Administrator"
        )
        db.add(follow_up_answer)
        db.commit()
    """

    __tablename__ = "answers"
    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    question_id = Column(String, ForeignKey("questions.id"))
    text = Column(Text)
    submitted_by = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    question = relationship("QuestionDB", back_populates="answers")


class MaturityAssessmentDB(Base):
    """
    Represents a maturity assessment for a specific criterion within an audit.

    This class is crucial for capturing the final evaluation of a company's maturity level
    for each criterion assessed during the audit. It encapsulates the auditor's judgment
    based on the evidence collected, questions answered, and the overall context of the company.

    Attributes:
        id (str): Unique identifier for the maturity assessment, auto-generated UUID.
        audit_id (str): Foreign key linking to the associated audit.
        criteria_id (str): Foreign key linking to the associated criterion.
        maturity_level (str): The assessed maturity level (e.g., "novice", "intermediate", "advanced").
        comments (Text): Additional comments or justification for the assessment.
        assessed_by (str): Identifier or name of the auditor who made the assessment.
        assessed_at (datetime): Timestamp of when the assessment was made.

    Relationships:
        audit: The audit this maturity assessment is part of.
        criteria: The specific criterion being assessed.

    Usage:
        This model is used to record the final maturity assessments for each criterion in an audit.
        It represents the culmination of the evidence gathering and analysis process, where an
        auditor determines the company's maturity level based on all available information.

    Note:
        - The 'maturity_level' should align with the levels defined in the criteria (typically
          "novice", "intermediate", "advanced", but could be customized).
        - The 'comments' field is crucial for providing context and justification for the
          assessment, especially in cases where the determination might not be straightforward.
        - Each criterion in an audit should have one maturity assessment.
        - The assessment can be updated if new evidence comes to light or if a reassessment
          is needed.

    Example:
        # Recording a maturity assessment for a specific criterion in an audit
        new_assessment = MaturityAssessmentDB(
            audit_id="audit_uuid",
            criteria_id="criteria_uuid",
            maturity_level="intermediate",
            comments="The company demonstrates a structured approach to data management with regular "
                     "analysis, but lacks advanced analytics capabilities. They have implemented basic "
                     "data governance policies and use cloud-based data storage solutions effectively.",
            assessed_by="Alice Johnson, Lead Auditor"
        )
        db.add(new_assessment)
        db.commit()

        # Updating an existing assessment based on new information
        existing_assessment = db.query(MaturityAssessmentDB).filter_by(id="assessment_uuid").first()
        existing_assessment.maturity_level = "advanced"
        existing_assessment.comments += "\n\nUpdate: After reviewing additional evidence of their "
                                        "machine learning initiatives and data-driven decision making "
                                        "processes, the maturity level has been upgraded to advanced."
        existing_assessment.assessed_at = datetime.now(timezone.utc)
        db.commit()
    """

    __tablename__ = "maturity_assessments"
    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    audit_id = Column(String, ForeignKey("audits.id"))
    criteria_id = Column(String, ForeignKey("criteria.id"))
    maturity_level = Column(String)
    comments = Column(Text, nullable=True)
    assessed_by = Column(String)
    assessed_at = Column(DateTime(timezone=True), server_default=func.now())

    audit = relationship("AuditDB", back_populates="maturity_assessments")
    criteria = relationship("CriteriaDB", back_populates="maturity_assessment")


# Pydantic models
class CompanyUserResponse(BaseModel):
    id: str
    email: str
    name: str
    role: UserRole
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AddUserToCompanyRequest(BaseModel):
    user_id: str
    role: UserRole


class MaturityLevel(str, Enum):
    novice = "novice"
    intermediate = "intermediate"
    advanced = "advanced"


class UserCompanyAssociationCreate(BaseModel):
    company_id: str
    role: UserRole


class UserCompanyAssociationResponse(BaseModel):
    id: str
    company_id: str
    user_id: str
    role: UserRole
    created_at: datetime
    updated_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    is_global_administrator: bool
    company_associations: List[UserCompanyAssociationResponse]
    created_at: datetime
    updated_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class AuditResponse(BaseModel):
    id: str
    name: str
    description: str = Field(default=None)
    company_id: str
    created_at: datetime
    updated_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class EvidenceFileResponse(BaseModel):
    id: str
    audit_id: str
    filename: str
    file_type: str
    status: str

    model_config = ConfigDict(from_attributes=True)


class CriteriaCreate(BaseModel):
    title: str
    description: str
    parent_id: Optional[str] = None
    maturity_definitions: Dict[MaturityLevel, str]
    section: str
    expected_maturity_level: MaturityLevel


class CompanySize(str, Enum):
    unknown = "unknown"
    micro = "micro"
    small = "small"
    medium = "medium"
    large = "large"


class CompanyCreate(BaseModel):
    name: str = Field(..., min_length=1)
    description: Optional[str] = Field(None)
    sector: Optional[str] = Field(None)
    size: Optional[CompanySize] = None
    business_type: Optional[str] = Field(None)
    technology_stack: Optional[str] = Field(None)
    areas_of_focus: Optional[List[str]] = Field(None)

    @field_validator("size")
    def validate_size(cls, v):
        if isinstance(v, str):
            try:
                return CompanySize(v.lower())
            except ValueError:
                raise ValueError(
                    f"Invalid size. Must be one of: {', '.join(CompanySize.__members__)}"
                )
        return v


class AuditCreate(BaseModel):
    name: str
    description: str = Field(default=None)
    company_id: Optional[str] = Field(default=None)
    company_name: Optional[str] = Field(default=None)


class CompanyResponse(CompanyCreate):
    id: str
    created_at: datetime
    updated_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)

    @field_validator("areas_of_focus", mode="before")
    @classmethod
    def split_areas_of_focus(cls, v):
        if isinstance(v, str):
            return v.split(",") if v else []
        return v


class CompanyListResponse(BaseModel):
    id: str
    name: str
    sector: Optional[str]
    description: Optional[str] = Field(None)
    size: Optional[str]
    business_type: Optional[str]
    created_at: datetime
    updated_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class CriteriaSelect(BaseModel):
    criteria_id: str
    expected_maturity_level: Optional[MaturityLevel] = None


class CriteriaSelectionResponse(BaseModel):
    id: str
    audit_id: str
    criteria_id: str
    expected_maturity_level: Optional[MaturityLevel]

    model_config = ConfigDict(from_attributes=True)


class CriteriaResponse(BaseModel):
    id: str
    title: str
    description: str
    parent_id: str | None
    maturity_definitions: dict
    section: str
    is_specific_to_audit: str | None

    model_config = ConfigDict(from_attributes=True)


class EvidenceCreate(BaseModel):
    content: str
    source: str
    source_id: str


class EvidenceResponse(BaseModel):
    id: str
    audit_id: str
    criteria_id: str
    content: str
    source: str
    source_id: str
    evidence_type: str
    start_position: Optional[int] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AnswerCreate(BaseModel):
    text: str
    submitted_by: str


class AnswerResponse(BaseModel):
    id: str
    text: str
    submitted_by: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AuditListResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    created_at: datetime
    updated_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class QuestionCreate(BaseModel):
    text: str


class QuestionResponse(BaseModel):
    id: str
    text: str
    created_at: datetime
    answers: List[AnswerResponse]

    model_config = ConfigDict(from_attributes=True)


class CriteriaEvidenceResponse(BaseModel):
    evidence: List[EvidenceResponse]
    questions: List[QuestionResponse]

    model_config = ConfigDict(from_attributes=True)


class MaturityAssessmentCreate(BaseModel):
    maturity_level: MaturityLevel
    comments: Optional[str] = Field(default=None)


class MaturityAssessmentResponse(MaturityAssessmentCreate):
    id: str
    criteria_id: str
    assessed_by: str
    assessed_at: datetime  # Changed from 'str' to 'datetime'

    model_config = ConfigDict(from_attributes=True)


class RemoveCriteriaRequest(BaseModel):
    criteria_id: str


class RemoveCriteriaResponse(BaseModel):
    message: str
    audit_id: str
    criteria_id: str


class DeleteCustomCriteriaResponse(BaseModel):
    message: str
    criteria_id: str


class UpdateCustomCriteriaRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    parent_id: Optional[str] = None
    maturity_definitions: Optional[Dict[str, str]] = None
    section: Optional[str] = None


class GoogleAuthRequest(BaseModel):
    token: str


# Create tables
Base.metadata.create_all(bind=engine)


# Database Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Authentication
async def get_current_user(
    auth: HTTPAuthorizationCredentials = Depends(auth_scheme),
    db: Session = Depends(get_db),
):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        token = auth.credentials
        payload = verify_jwt_token(token)
        if payload is None:
            raise credentials_exception
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    user = db.query(UserDB).filter(UserDB.id == user_id).first()
    if user is None:
        raise credentials_exception
    return user


def create_jwt_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    return jwt.encode(
        to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm
    )


def verify_jwt_token(token: str):
    try:
        payload = jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
        return payload
    except JWTError:
        return None


def authorize_company_access(
    company_id_param: str = "company_id",
    audit_id_param: str = "audit_id",
    required_roles: Optional[List[UserRole]] = None,
):
    """
    A decorator to authorize access to endpoints based on user roles associated with a company.

    Parameters:
    - company_id_param: The name of the company ID parameter in the path.
    - audit_id_param: The name of the audit ID parameter in the path.
    - required_roles: List of UserRole enums that are allowed to access the endpoint.

    If required_roles is None or empty, the function will raise an exception, enforcing explicit role specification.
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(
            *args,
            current_user: UserDB = Depends(get_current_user),
            db: Session = Depends(get_db),
            **kwargs,
        ):
            # System administrators have unrestricted access
            if current_user.is_global_administrator:
                return await func(*args, current_user=current_user, db=db, **kwargs)

            # Ensure that required_roles is provided
            if not required_roles:
                raise HTTPException(
                    status_code=500,
                    detail="Access control misconfiguration: required_roles must be specified.",
                )

            # Extract path parameters
            request: Request = kwargs.get("request")
            if not request:
                for arg in args:
                    if isinstance(arg, Request):
                        request = arg
                        break

            if not request:
                raise HTTPException(status_code=500, detail="Request object not found")

            path_params = request.path_params
            company_id = path_params.get(company_id_param)
            audit_id = path_params.get(audit_id_param)

            # Determine the company ID if only audit ID is provided
            if not company_id and audit_id:
                audit = db.query(AuditDB).filter(AuditDB.id == audit_id).first()
                if not audit:
                    raise HTTPException(status_code=404, detail="Audit not found")
                company_id = audit.company_id

            if not company_id:
                raise HTTPException(
                    status_code=400, detail="No company_id or audit_id provided in path"
                )

            # Check if the user has the required role
            if not current_user.has_company_role(company_id, required_roles):
                raise HTTPException(
                    status_code=403,
                    detail="Insufficient permissions to access this resource",
                )

            # Call the original endpoint function
            return await func(*args, current_user=current_user, db=db, **kwargs)

        return wrapper

    return decorator


# FastAPI app
app = FastAPI()

# Add CORS middleware
origins = [
    "http://localhost:3000",  # React app
    "http://127.0.0.1:3000",  # Alternate localhost
    # Add other origins if necessary
]

app.add_middleware(SessionMiddleware, secret_key=secrets.token_urlsafe(32))

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],  # Or specify the methods you want to allow
    allow_headers=["*"],  # Or specify the headers you want to allow
)


# Helper functions
def process_file(file_path: str, db: Session, file_id: str):
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
    output_path = video_path.rsplit(".", 1)[0] + ".mp3"
    stream = ffmpeg.input(video_path)
    stream = ffmpeg.output(stream, output_path, acodec="libmp3lame")
    ffmpeg.run(stream, overwrite_output=True)
    return output_path


def analyze_image(image_path: str) -> Optional[str]:
    max_retries = 3
    retry_delay = 5  # seconds

    # Function to encode the image
    def encode_image(image_path):
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode("utf-8")

    # Encoding the image
    base64_image = encode_image(image_path)

    # Defining the function for GPT to use
    functions = [
        {
            "name": "describe_image",
            "description": "Describes the content of an image relevant to a technical and product audit",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "A detailed description of the image content, or 'irrelevant' if the image is not relevant to the audit",
                    }
                },
                "required": ["description"],
            },
        }
    ]

    for attempt in range(max_retries):
        try:
            response = openAiClient.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert technical and product auditor. You reply in british english. Your task is to analyse images for a technical and product audit process.",
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Analyze this image for our technical and product audit. If it's irrelevant (like a logo or unrelated picture), respond with 'irrelevant'. Otherwise, provide a detailed description of the content, especially if it's a system screenshot, architecture diagram, process chart, or documentation. Focus on factual information without assessing maturity.",
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{base64_image}"
                                },
                            },
                        ],
                    },
                ],
                functions=functions,
                function_call={"name": "describe_image"},
            )

            # Extract the function call result
            function_call = response.choices[0].message.function_call
            if function_call and function_call.name == "describe_image":
                description = eval(function_call.arguments)["description"]
                return description if description != "irrelevant" else None

        except Exception as e:
            print(f"Error analyzing image, attempt {attempt + 1}: {str(e)}")
            if attempt < max_retries - 1:
                print(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
            else:
                print(f"Failed to analyze image after {max_retries} attempts.")
                return None

    return None


def transcribe_audio(audio_path: str) -> Optional[str]:
    # Load audio file using pydub
    audio = AudioSegment.from_file(audio_path)

    # Calculate the number of chunks needed
    max_chunk_duration_ms = 15 * 60 * 1000  # 15 minutes in milliseconds
    num_chunks = math.ceil(len(audio) / max_chunk_duration_ms)

    transcripts: List[Optional[str]] = [None] * num_chunks
    max_retries = 3
    retry_delay = 5  # seconds

    for i in range(num_chunks):
        start_ms = i * max_chunk_duration_ms
        end_ms = min((i + 1) * max_chunk_duration_ms, len(audio))
        chunk = audio[start_ms:end_ms]

        # Export chunk to a temporary file
        with tempfile.NamedTemporaryFile(
            suffix=".mp3", delete=False
        ) as temp_audio_file:
            chunk.export(temp_audio_file.name, format="mp3")
            temp_audio_file.close()

            # Transcribe the chunk with retry logic
            for attempt in range(max_retries):
                try:
                    with open(temp_audio_file.name, "rb") as audio_file:
                        transcript = openAiClient.audio.transcriptions.create(
                            model="whisper-1", file=audio_file
                        )
                    transcripts[i] = transcript.text
                    break
                except Exception as e:
                    print(
                        f"Error transcribing chunk {i + 1}, attempt {attempt + 1}: {str(e)}"
                    )
                    if attempt < max_retries - 1:
                        print(f"Retrying in {retry_delay} seconds...")
                        time.sleep(retry_delay)
                    else:
                        print(
                            f"Failed to transcribe chunk {i + 1} after {max_retries} attempts."
                        )

            # Delete the temporary file
            os.unlink(temp_audio_file.name)

    # Check if all chunks were transcribed successfully
    if None in transcripts:
        print("Transcription failed: Some chunks could not be transcribed.")
        return None

    # Combine all transcripts
    combined_transcript = "\n".join(transcripts)

    return combined_transcript


def convert_with_pandoc(file_path: str) -> str:
    # Create a temporary directory to store extracted images
    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            # Convert document to HTML, extracting images
            html_content = pypandoc.convert_file(
                file_path, to="html", extra_args=["--extract-media=" + temp_dir]
            )

            # Process and replace image references
            processed_content = process_images(html_content, temp_dir)

            # Convert processed HTML back to Markdown
            markdown_content = pypandoc.convert_text(
                processed_content, to="markdown", format="html"
            )

            return markdown_content

        except Exception as e:
            raise Exception(
                f"Pandoc conversion failed for file: {file_path}. Error: {str(e)}"
            )


def process_images(content: str, image_dir: str) -> str:
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


def save_text_content(db_file: EvidenceFileDB, content: str):
    # Assuming you have a column to store the text content in EvidenceFileDB
    # If not, you might need to create a new table or add a column
    db_file.text_content = content


# Endpoints
@app.get("/login/google")
async def login_google(request: Request):
    redirect_uri = request.url_for("auth_google")
    return await oauth.google.authorize_redirect(request, redirect_uri)


@app.get("/auth/google")
async def auth_google_callback(request: Request, db: Session = Depends(get_db)):
    try:
        token = await oauth.google.authorize_access_token(request)

        if "id_token" not in token:
            raise ValueError("No id_token found in the OAuth response")

        idinfo = id_token.verify_oauth2_token(
            token["id_token"], google_auth_requests.Request(), settings.google_client_id
        )

        # Get or create user
        user = (
            db.query(UserDB)
            .filter(UserDB.oauth_id == idinfo["sub"], UserDB.oauth_provider == "google")
            .first()
        )

        if not user:
            user = UserDB(
                email=idinfo["email"],
                name=idinfo.get("name", "Google User"),
                oauth_provider="google",
                oauth_id=idinfo["sub"],
                is_global_administrator=False,  # Default new users to not be system auditors
            )
            db.add(user)
            db.commit()
            db.refresh(user)

        # Create JWT token
        access_token = create_jwt_token({"sub": user.id})

        return {"access_token": access_token, "token_type": "bearer"}

    except Exception as e:
        print(f"Error in auth_google_callback: {str(e)}")
        error_details = {
            "error": str(e),
            "token_info": token if "token" in locals() else "Token not received",
        }
        return JSONResponse(
            status_code=500,
            content={"message": "Authentication failed", "details": error_details},
        )


@app.post("/auth/google")
async def auth_google(auth_request: GoogleAuthRequest, db: Session = Depends(get_db)):
    try:
        token = auth_request.token

        idinfo = id_token.verify_oauth2_token(
            token, google_auth_requests.Request(), settings.google_client_id
        )

        if idinfo["iss"] not in ["accounts.google.com", "https://accounts.google.com"]:
            raise ValueError("Wrong issuer.")

        # Get or create user
        user = (
            db.query(UserDB)
            .filter(UserDB.oauth_id == idinfo["sub"], UserDB.oauth_provider == "google")
            .first()
        )

        if not user:
            user = UserDB(
                email=idinfo["email"],
                name=idinfo["name"],
                oauth_provider="google",
                oauth_id=idinfo["sub"],
                role="user",
            )
            db.add(user)
            db.commit()
            db.refresh(user)

        # Create JWT token
        access_token = create_jwt_token({"sub": user.id})
        
        # Return both token and user info
        return {
            "access_token": access_token, 
            "token_type": "bearer",
            "user": {
                "id": user.id,
                "email": user.email,
                "name": user.name,
                "is_global_administrator": user.is_global_administrator
            }
        }

    except ValueError as e:
        print(f"Error in auth_google: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print(f"Error in auth_google: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/verify-token")
async def verify_token(current_user: UserDB = Depends(get_current_user)):
    return {
        "user": {
            "id": current_user.id,
            "email": current_user.email,
            "name": current_user.name,
        }
    }


@app.post("/audits", response_model=AuditResponse)
@authorize_company_access(required_roles=list(UserRole))
def create_audit(
    request: Request,
    audit: AuditCreate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
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


@app.get("/audits/{audit_id}", response_model=AuditResponse)
@authorize_company_access(required_roles=list(UserRole))
async def get_audit(
    request: Request,
    audit_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    db_audit = db.query(AuditDB).filter(AuditDB.id == audit_id).first()
    if db_audit is None:
        raise HTTPException(status_code=404, detail="Audit not found")
    return db_audit


@app.delete("/audits/{audit_id}", status_code=status.HTTP_204_NO_CONTENT)
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


@app.get("/audits", response_model=List[AuditResponse])
async def list_audits(
    request: Request,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
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


@app.post("/audits/{audit_id}/company", response_model=CompanyResponse)
@authorize_company_access(
    audit_id_param="audit_id",
    required_roles=[UserRole.AUDITOR, UserRole.ORGANISATION_LEAD],
)
async def create_company(
    request: Request,
    audit_id: str,
    company: CompanyCreate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    db_audit = db.query(AuditDB).filter(AuditDB.id == audit_id).first()
    if db_audit is None:
        raise HTTPException(status_code=404, detail="Audit not found")

    company_data = company.model_dump(exclude_unset=True)
    if "areas_of_focus" in company_data:
        company_data["areas_of_focus"] = ",".join(company_data["areas_of_focus"])
    if "size" in company_data and company_data["size"] is not None:
        company_data["size"] = company_data["size"].value

    db_company = CompanyDB(audit_id=audit_id, **company_data)
    db.add(db_company)
    db.commit()
    db.refresh(db_company)

    response_data = db_company.__dict__
    if response_data["areas_of_focus"]:
        response_data["areas_of_focus"] = response_data["areas_of_focus"].split(",")
    return CompanyResponse(**response_data)


@app.get("/companies", response_model=List[CompanyListResponse])
async def list_companies(
    request: Request,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    # System administrators can see all companies
    if current_user.is_global_administrator:
        companies = db.query(CompanyDB).offset(skip).limit(limit).all()
        return companies

    # Regular users can only see companies they're associated with
    companies = (
        db.query(CompanyDB)
        .join(UserCompanyAssociation)
        .filter(UserCompanyAssociation.user_id == current_user.id)
        .offset(skip)
        .limit(limit)
        .all()
    )
    return companies


@app.get("/companies/{company_id}", response_model=CompanyResponse)
@authorize_company_access(required_roles=list(UserRole))
async def get_company_detail(
    request: Request,
    company_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    company = db.query(CompanyDB).filter(CompanyDB.id == company_id).first()
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


@app.post(
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


@app.delete("/companies/{company_id}/users/{user_id}", status_code=204)
@authorize_company_access(required_roles=[UserRole.AUDITOR, UserRole.ORGANISATION_LEAD])
async def remove_user_from_company(
    request: Request,
    company_id: str,
    user_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
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


@app.get("/companies/{company_id}/users", response_model=List[CompanyUserResponse])
@authorize_company_access(required_roles=list(UserRole))
async def list_company_users(
    request: Request,
    company_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    if not current_user.is_global_administrator:
        # Verify user has access to this company
        user_association = (
            db.query(UserCompanyAssociation)
            .filter(
                UserCompanyAssociation.user_id == current_user.id,
                UserCompanyAssociation.company_id == company_id,
            )
            .first()
        )
        if not user_association:
            raise HTTPException(
                status_code=403, detail="You don't have access to this company's users"
            )

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


@app.get("/users/me", response_model=UserResponse)
async def get_current_user_details(
    current_user: UserDB = Depends(get_current_user), db: Session = Depends(get_db)
):
    """
    Get details about the currently authenticated user, including their company associations
    """
    # Fetch the user with company associations
    user_with_associations = (
        db.query(UserDB)
        .options(
            joinedload(UserDB.company_associations).joinedload(
                UserCompanyAssociation.company
            )
        )
        .filter(UserDB.id == current_user.id)
        .first()
    )

    if not user_with_associations:
        raise HTTPException(status_code=404, detail="User not found")

    return user_with_associations


@app.get("/users/me/companies", response_model=List[CompanyListResponse])
async def list_user_companies(
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Get all companies the current user has access to"""
    if current_user.is_global_administrator:
        # System auditors can see all companies
        companies = db.query(CompanyDB).all()
    else:
        # Get companies through associations
        companies = (
            db.query(CompanyDB)
            .join(UserCompanyAssociation)
            .filter(UserCompanyAssociation.user_id == current_user.id)
            .all()
        )

    return companies


@app.put(
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
    association.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(association)

    return association


@app.get("/audits/{audit_id}/company", response_model=CompanyResponse)
@authorize_company_access(required_roles=list(UserRole))
async def get_company(
    request: Request,
    audit_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    db_audit = db.query(AuditDB).filter(AuditDB.id == audit_id).first()
    if db_audit is None:
        raise HTTPException(status_code=404, detail="Audit not found")

    db_company = db_audit.company
    if db_company is None:
        raise HTTPException(status_code=404, detail="Company not found for this audit")

    return db_company


@app.get("/companies/{company_id}/audits", response_model=List[AuditListResponse])
@authorize_company_access(required_roles=list(UserRole))
async def list_company_audits(
    request: Request,
    company_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
):
    if not current_user.is_global_administrator:
        # Verify user has access to this company
        user_association = (
            db.query(UserCompanyAssociation)
            .filter(
                UserCompanyAssociation.user_id == current_user.id,
                UserCompanyAssociation.company_id == company_id,
            )
            .first()
        )
        if not user_association:
            raise HTTPException(
                status_code=403, detail="You don't have access to this company's audits"
            )

    # Query audits associated with the company
    audits = (
        db.query(AuditDB)
        .filter(AuditDB.company_id == company_id)
        .order_by(AuditDB.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )

    return audits


@app.put("/audits/{audit_id}/company", response_model=CompanyResponse)
@authorize_company_access(
    audit_id_param="audit_id",
    required_roles=[UserRole.AUDITOR, UserRole.ORGANISATION_LEAD],
)
async def update_company(
    request: Request,
    audit_id: str,
    company: CompanyCreate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    db_audit = db.query(AuditDB).filter(AuditDB.id == audit_id).first()
    if db_audit is None:
        raise HTTPException(status_code=404, detail="Audit not found")

    db_company = db_audit.company
    if db_company is None:
        raise HTTPException(status_code=404, detail="Company not found for this audit")

    company_data = company.model_dump(exclude_unset=True)
    if "areas_of_focus" in company_data:
        company_data["areas_of_focus"] = ",".join(company_data["areas_of_focus"])

    for key, value in company_data.items():
        setattr(db_company, key, value)

    db.commit()
    db.refresh(db_company)
    return db_company


@app.post(
    "/audits/{audit_id}/company/actions/parse-evidence", response_model=CompanyResponse
)
@authorize_company_access(required_roles=[UserRole.AUDITOR])
async def parse_evidence_for_company(
    request: Request,
    audit_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    # Set up logging
    logging.basicConfig(level=logging.DEBUG)
    logger = logging.getLogger(__name__)

    # Get the audit
    db_audit = db.query(AuditDB).filter(AuditDB.id == audit_id).first()
    if db_audit is None:
        raise HTTPException(status_code=404, detail="Audit not found")

    # Get the company associated with the audit
    db_company = db_audit.company
    if db_company is None:
        raise HTTPException(status_code=404, detail="Company not found for this audit")

    # Get all processed evidence files that haven't been parsed yet
    processed_file_ids = db_company.processed_file_ids or []
    logger.debug(f"Initial processed_file_ids: {processed_file_ids}")

    evidence_files = (
        db.query(EvidenceFileDB)
        .filter(
            EvidenceFileDB.audit_id == audit_id,
            EvidenceFileDB.status == "complete",
            EvidenceFileDB.text_content != None,
            ~EvidenceFileDB.id.in_(processed_file_ids if processed_file_ids else []),
        )
        .all()
    )

    logger.debug(f"Number of evidence files to process: {len(evidence_files)}")

    if not evidence_files:
        logger.debug("No new files to parse, proceeding to stage 2")
        return process_raw_evidence(db_company, db)

    # Stage 1: Parse each new evidence file
    new_processed_file_ids = processed_file_ids.copy() if processed_file_ids else []
    for file in evidence_files:
        if file.text_content:  # Ensure there's content to parse
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


def parse_single_evidence_file(file: EvidenceFileDB, db_company: CompanyDB) -> str:

    if file.text_content is None or file.text_content == "":
        print(f"Error parsing file {file.id} - no text contents")
        return ""

    system_prompt = (
        "Within the following content find company information based on the following areas. If unable to determine high quality and accurate response from text then don't include that area of information in your response."
        + "A description of the company. Approx 200 words which would enable someone with no knowledge of the company to understand the company and what they do / are known for. It should focus on what the companies product / offering is rather than its technology or implementation unless that is core to it's offering."
        + "The sector the company operates in. i.e. consumer electronics, financial markets..."
        + "The size of the company (unknown, micro, small, medium, large). Roughly aligned with; Micro-enterprise: A business with up to 10 employees < £1.5 million revenue, Small enterprise: A business with 10 to 49 employees < £15 million revenue, Medium-sized enterprise: A business with 50 to 249 employees < £54 million revenue, Large enterprise: A business with 250 or more employees "
        + "The type of business the company is. Is it a b2b, b2c maybe a mix of multiple"
        + "The main technologies used by the company and it's platforms."
        + "Areas of focus of the company. The markets it focuses on i.e education, consumer, finance, entertainment and the types of business it does i.e. product manufacture, software development..."
        + "The company is called "
        + db_company.name
        + " and the file your extracting data from is a "
        + file.file_type
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": file.text_content},
    ]

    try:
        response = openAiClient.chat.completions.create(
            model="gpt-4o-mini", messages=messages, max_tokens=500
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"Error parsing file {file.id}: {str(e)}")
        return ""


def process_raw_evidence(db_company: CompanyDB, db: Session) -> CompanyResponse:
    if not db_company.raw_evidence:
        raise HTTPException(status_code=400, detail="No raw evidence to process")

    # Define the function schema for function calling
    company_info_function = {
        "name": "extract_company_info",
        "description": "Extracts company information from the provided text. Response should be 'unknown' if unable to determine high quality and accurate response from text. Always use british english.",
        "parameters": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "A description of the company. Approx 200 words which would enable someone with no knowledge of the company to understand the company and what they do / are known for. It should focus on what the companies product / offering is rather than its technology or implementation unless that is core to it's offering.",
                },
                "sector": {
                    "type": "string",
                    "description": "The sector the company operates in. i.e. consumer electronics, financial markets...",
                },
                "size": {
                    "type": "string",
                    "description": "The size of the company (unknown, micro, small, medium, large). Roughly aligned with; Micro-enterprise: A business with up to 10 employees < £1.5 million revenue, Small enterprise: A business with 10 to 49 employees < £15 million revenue, Medium-sized enterprise: A business with 50 to 249 employees < £54 million revenue, Large enterprise: A business with 250 or more employees ",
                    "enum": ["unknown", "micro", "small", "medium", "large"],
                },
                "business_type": {
                    "type": "string",
                    "description": "The type of business the company is. Is it a b2b, b2c maybe a mix of multiple",
                },
                "technology_stack": {
                    "type": "string",
                    "description": "The main technologies used by the company and it's platforms.",
                },
                "areas_of_focus": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Areas of focus of the company. The markets it focuses on i.e education, consumer, finance, entertainment and the types of business it does i.e. product manufacture, software development...",
                },
            },
            "required": [
                "description",
                "sector",
                "size",
                "business_type",
                "technology_stack",
                "areas_of_focus",
            ],
        },
    }

    system_prompt = (
        "You are an expert that systematically reads understand and consolidates company information from a body of text. Always use british english. "
        "Below is such a body of text made up from summaries of multiple files. "
        "Extract the information and format it as per the specified function schema. "
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": db_company.raw_evidence},
    ]

    try:
        response = openAiClient.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            functions=[company_info_function],
            function_call={"name": "extract_company_info"},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"OpenAI API error: {str(e)}")

    # Extract and process the function call arguments
    function_call = response.choices[0].message.function_call
    arguments = json.loads(function_call.arguments)

    # Update the company record
    for key, value in arguments.items():
        if key == "areas_of_focus" and isinstance(value, list):
            value = ",".join(value[:10])  # Limit to 10 areas, join as string
        setattr(db_company, key, value)

    db_company.updated_from_evidence = True
    db.commit()
    db.refresh(db_company)

    return db_company


@app.post("/audits/{audit_id}/evidence-files", response_model=EvidenceFileResponse)
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


@app.get("/audits/{audit_id}/evidence-files", response_model=List[EvidenceFileResponse])
@authorize_company_access(required_roles=list(UserRole))
async def list_evidence_files(
    request: Request,
    audit_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    files = db.query(EvidenceFileDB).filter(EvidenceFileDB.audit_id == audit_id).all()
    return files


@app.get(
    "/audits/{audit_id}/evidence-files/{file_id}", response_model=EvidenceFileResponse
)
@authorize_company_access(required_roles=list(UserRole))
async def get_evidence_file(
    request: Request,
    audit_id: str,
    file_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    file = (
        db.query(EvidenceFileDB)
        .filter(EvidenceFileDB.id == file_id, EvidenceFileDB.audit_id == audit_id)
        .first()
    )
    if file is None:
        raise HTTPException(status_code=404, detail="Evidence file not found")
    return file


@app.get("/audits/{audit_id}/evidence-files/{file_id}/content")
@authorize_company_access(required_roles=list(UserRole))
async def get_evidence_file_content(
    request: Request,
    audit_id: str,
    file_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
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


@app.delete(
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
    file = (
        db.query(EvidenceFileDB)
        .filter(EvidenceFileDB.id == file_id, EvidenceFileDB.audit_id == audit_id)
        .first()
    )
    if file is None:
        raise HTTPException(status_code=404, detail="Evidence file not found")

    # Delete file from filesystem
    if os.path.exists(file.file_path):
        os.remove(file.file_path)

    # Delete from database
    db.delete(file)
    db.commit()

    return {"message": "Evidence file deleted successfully"}


@app.get(
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
    file = (
        db.query(EvidenceFileDB)
        .filter(EvidenceFileDB.id == file_id, EvidenceFileDB.audit_id == audit_id)
        .first()
    )
    if file is None:
        raise HTTPException(status_code=404, detail="Evidence file not found")
    return file


@app.get("/criteria", response_model=List[CriteriaResponse])
async def list_base_criteria(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    base_criteria = (
        db.query(CriteriaDB)
        .filter(CriteriaDB.is_specific_to_audit == None)
        .offset(skip)
        .limit(limit)
        .all()
    )
    return base_criteria


@app.get("/criteria/custom", response_model=List[CriteriaResponse])
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


@app.get("/audits/{audit_id}/criteria", response_model=List[CriteriaResponse])
@authorize_company_access(required_roles=list(UserRole))
async def get_audit_criteria(
    request: Request,
    audit_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    audit_criteria = (
        db.query(AuditCriteriaDB)
        .filter(AuditCriteriaDB.audit_id == audit_id)
        .options(joinedload(AuditCriteriaDB.criteria))
        .all()
    )
    return [ac.criteria for ac in audit_criteria]


@app.post("/audits/{audit_id}/criteria/custom", response_model=CriteriaResponse)
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
    # Verify the audit exists
    audit = db.query(AuditDB).filter(AuditDB.id == audit_id).first()
    if not audit:
        raise HTTPException(status_code=404, detail="Audit not found")

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
    db.flush()  # This assigns an ID to db_criteria without committing the transaction

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


@app.put("/criteria/custom/{criteria_id}", response_model=CriteriaResponse)
@authorize_company_access(required_roles=[UserRole.AUDITOR, UserRole.ORGANISATION_LEAD])
async def update_custom_criteria(
    request: Request,
    criteria_id: str,
    update_data: UpdateCustomCriteriaRequest,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    # Verify the criteria exists and is custom
    criteria = db.query(CriteriaDB).filter(CriteriaDB.id == criteria_id).first()
    if not criteria:
        raise HTTPException(status_code=404, detail="Criteria not found")
    if criteria.is_specific_to_audit is None:
        raise HTTPException(status_code=400, detail="Cannot update base criteria")

    # Prepare the update data
    update_dict = update_data.dict(exclude_unset=True)
    if not update_dict:
        raise HTTPException(status_code=400, detail="No update data provided")

    # Update the criteria
    for key, value in update_dict.items():
        setattr(criteria, key, value)

    db.commit()
    db.refresh(criteria)

    return db.query(CriteriaDB).filter(CriteriaDB.id == criteria_id).first()


@app.delete(
    "/criteria/custom/{criteria_id}", response_model=DeleteCustomCriteriaResponse
)
@authorize_company_access(required_roles=[UserRole.AUDITOR, UserRole.ORGANISATION_LEAD])
async def delete_custom_criteria(
    request: Request,
    criteria_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    # Verify the criteria exists and is custom
    criteria = db.query(CriteriaDB).filter(CriteriaDB.id == criteria_id).first()
    if not criteria:
        raise HTTPException(status_code=404, detail="Criteria not found")
    if criteria.is_specific_to_audit:
        raise HTTPException(status_code=400, detail="Cannot delete base criteria")

    # Check if the criteria is in use by any audit
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

    # If not in use, delete the criteria
    db.delete(criteria)
    db.commit()

    return DeleteCustomCriteriaResponse(
        message="Custom criteria successfully deleted", criteria_id=criteria_id
    )


@app.post(
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
    # Verify the audit exists
    audit = db.query(AuditDB).filter(AuditDB.id == audit_id).first()
    if not audit:
        raise HTTPException(status_code=404, detail="Audit not found")

    # Fetch the criterion
    criteria = (
        db.query(CriteriaDB)
        .filter(CriteriaDB.id == criteria_select.criteria_id)
        .first()
    )
    if not criteria:
        raise HTTPException(status_code=404, detail="Criterion not found")

    # Check if an association already exists
    existing_association = (
        db.query(AuditCriteriaDB)
        .filter(
            AuditCriteriaDB.audit_id == audit_id,
            AuditCriteriaDB.criteria_id == criteria_select.criteria_id,
        )
        .first()
    )

    if existing_association:
        # Update existing association
        existing_association.expected_maturity_level = (
            criteria_select.expected_maturity_level
        )
        db.commit()
        return existing_association
    else:
        # Create new association
        new_association = AuditCriteriaDB(
            audit_id=audit_id,
            criteria_id=criteria_select.criteria_id,
            expected_maturity_level=criteria_select.expected_maturity_level,
        )
        db.add(new_association)
        db.commit()
        db.refresh(new_association)
        return new_association


@app.delete(
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
    # Verify the audit exists
    audit = db.query(AuditDB).filter(AuditDB.id == audit_id).first()
    if not audit:
        raise HTTPException(status_code=404, detail="Audit not found")

    # Check if the association exists
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

    # Remove the association
    db.delete(existing_association)
    db.commit()

    return RemoveCriteriaResponse(
        message="Criteria successfully removed from the audit",
        audit_id=audit_id,
        criteria_id=criteria_remove.criteria_id,
    )


@app.post(
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
    # This is a placeholder implementation. In a real-world scenario,
    # you would implement logic to analyze the audit data and preselect criteria.

    # For this example, we'll just select the first 5 criteria and set their expected maturity level to "intermediate"
    criteria_to_preselect = (
        db.query(CriteriaDB).filter(CriteriaDB.audit_id == audit_id).limit(5).all()
    )

    for criteria in criteria_to_preselect:
        criteria.selected = True
        criteria.expected_maturity_level = "intermediate"

    db.commit()
    return criteria_to_preselect


@app.post(
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
    # Check if the criteria exists
    criteria = db.query(CriteriaDB).filter(CriteriaDB.id == criteria_id).first()
    if not criteria:
        raise HTTPException(status_code=404, detail="Criteria not found")

    # Start evidence extraction in the background
    background_tasks.add_task(process_evidence_for_criteria, audit_id, criteria_id)

    return {"message": "Evidence extraction started"}


def find_quote_start_position(quote: str, document: str) -> Optional[int]:
    # Set the maximum allowed Levenshtein distance (10% of the quote length or at least 2)
    max_l_dist = max(2, int(len(quote) * 0.1))

    # Find approximate matches
    matches = find_near_matches(quote, document, max_l_dist=max_l_dist)

    if matches:
        # Return the start position of the best match (smallest Levenshtein distance)
        best_match = min(matches, key=lambda x: x.dist)
        return best_match.start
    else:
        return None


def process_evidence_for_criteria(audit_id: str, criteria_id: str):
    db = SessionLocal()
    try:
        # Get the criteria
        criteria = db.query(CriteriaDB).filter(CriteriaDB.id == criteria_id).first()
        if not criteria:
            print(f"Criteria {criteria_id} not found.")
            return

        # Process evidence files
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
            if existing_evidence:
                continue  # Already processed this file for this criteria

            if not file.text_content:
                continue  # No content to process

            # Extract evidence using LLM
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

            # Store the extracted quotes
            for evidence_text in extracted_evidence_list:
                # Find the start position
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


def extract_evidence_from_text(
    content: str, criteria: CriteriaDB
) -> Tuple[str, List[str]]:
    # Build the messages for the LLM
    system_prompt = (
        "You are an expert auditor tasked with extracting relevant evidence from documents based on specific criteria. Always use british english."
        "Given the criteria and a document, extract and return a summary and relevant quotes or references from the document that pertain to the criteria. "
        "The summary should be a concise overview of the relevant content, and the quotes should be sentences to paragraphs in length that help an expert auditor assess the maturity of the organization's technology and product functions. "
        "Provide the output in a structured JSON format as per the function schema.\n\n"
        # Include examples in the prompt
        "Example Criteria:\n"
        "Title: Data-Driven Decision Making and Analytics\n"
        "Description: The use of data and analytics to inform product decisions and measure success.\n"
        "Maturity Definitions:\n"
        "novice: Data collection is minimal or inconsistent. Decisions are made based on intuition rather than data.\n"
        "intermediate: Basic analytics are implemented, with key metrics being tracked. However, data is not yet fully integrated into decision-making processes.\n"
        "advanced: Data is deeply integrated into the decision-making process, with advanced analytics and metrics tracking. Decisions are always data-driven.\n\n"
        "Example Document Excerpt:\n"
        "\"Sometimes we go out for dinner together as a team. We make use of ruby on rails for our website. Our team occasionally looks at customer feedback, but we mostly rely on our experience to decide on new features. We haven't set up any analytics tools yet. Our product is live in Italy and Spain. It's been lovely to meet you all.\"\n\n"
        "Example Output:\n"
        "{\n"
        '  "has_relevant_content": true,\n'
        '  "summary": "The company relies mainly on experience rather than data analytics for product decisions, indicating minimal use of data-driven decision-making.",\n'
        '  "quotes": [\n'
        '    "Our team occasionally looks at customer feedback, but we mostly rely on our experience to decide on new features. We haven\'t set up any analytics tools yet."\n'
        "  ]\n"
        "}\n\n"
        "If the document is irrelevant, set 'has_relevant_content' to false, and provide empty 'summary' and 'quotes'. Now, systematically read through the source document and perform the identification and extraction based on the provided criteria."
    )

    # Convert maturity definitions to string
    maturity_definitions_str = (
        "\n".join(
            [
                f"{level}: {desc}"
                for level, desc in criteria.maturity_definitions.items()
            ]
        )
        if isinstance(criteria.maturity_definitions, dict)
        else str(criteria.maturity_definitions)
    )

    user_message = (
        f"Criteria:\nTitle: {criteria.title}\nDescription: {criteria.description}\n"
        f"Maturity Definitions:\n{maturity_definitions_str}\n\n"
        f"Source Document Content:\n{content}"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    # Define the function schema
    functions = [
        {
            "name": "extract_relevant_content",
            "description": "Extracts relevant content from the document that pertains to the criteria. Always use british english.",
            "parameters": {
                "type": "object",
                "properties": {
                    "has_relevant_content": {
                        "type": "boolean",
                        "description": "True if the document has relevant content for the criteria, false otherwise.",
                    },
                    "summary": {
                        "type": "string",
                        "description": "A concise summary of the relevant content within the document pertaining to the criteria. Should be empty if 'has_relevant_content' is false.",
                    },
                    "quotes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "A list of highly relevant exact quotes from the source. Each one would help an expert auditor to assess the maturity of a company's tech/product function. Should be empty if 'has_relevant_content' is false.",
                    },
                },
                "required": ["has_relevant_content"],
            },
        }
    ]

    # Call the OpenAI API with function calling
    try:
        response = openAiClient.chat.completions.create(
            model="gpt-4o-mini",  # Update to the appropriate model
            messages=messages,
            functions=functions,
            function_call={"name": "extract_relevant_content"},
            max_tokens=2000,
        )

        # Extract the function call arguments
        function_call = response.choices[0].message.function_call
        if function_call and function_call.name == "extract_relevant_content":
            arguments = json.loads(function_call.arguments)
            has_relevant_content = arguments.get("has_relevant_content", False)
            if has_relevant_content:
                summary = arguments.get("summary", "")
                quotes = arguments.get("quotes", [])
                return summary, quotes
            else:
                return "", []
        else:
            print("Function call did not return as expected.")
            return "", []

    except Exception as e:
        print(f"Error extracting evidence: {str(e)}")
        return "", []


@app.get(
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
    # Fetch evidence
    evidence = (
        db.query(EvidenceDB)
        .filter(EvidenceDB.audit_id == audit_id, EvidenceDB.criteria_id == criteria_id)
        .all()
    )

    # Fetch questions with their answers
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

    # Prepare the response
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


@app.post(
    "/audits/{audit_id}/criteria/{criteria_id}/questions",
    response_model=List[QuestionResponse],
)
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
    # Check if the audit exists
    audit = db.query(AuditDB).filter(AuditDB.id == audit_id).first()
    if not audit:
        raise HTTPException(status_code=404, detail="Audit not found")

    # Check if the criteria exists
    criteria = db.query(CriteriaDB).filter(CriteriaDB.id == criteria_id).first()
    if not criteria:
        raise HTTPException(status_code=404, detail="Criteria not found")

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


def generate_questions_using_llm(
    criteria: CriteriaDB, evidence_content: str
) -> List[str]:
    # Build the prompt
    system_prompt = (
        "You are an expert auditor tasked with assessing the maturity of an organization's technical and product departments based on specific criteria and available evidence. Always use british english. "
        "Your goal is to determine whether the current evidence is sufficient to assess the maturity level. "
        "If the evidence is sufficient, generate a number of additional questions to dig deeper into the most relevant areas of the current evidence. "
        "If the evidence is not sufficient, generate questions that, when answered, would fill the gaps in knowledge so that an expert auditor could assess the maturity. "
        "Provide the output in a structured JSON format as per the function schema."
    )

    # Convert maturity definitions to string
    maturity_definitions_str = (
        "\n".join(
            [
                f"{level}: {desc}"
                for level, desc in criteria.maturity_definitions.items()
            ]
        )
        if isinstance(criteria.maturity_definitions, dict)
        else str(criteria.maturity_definitions)
    )

    user_message = (
        f"Criteria:\n"
        f"Title: {criteria.title}\n"
        f"Description: {criteria.description}\n"
        f"Maturity Definitions:\n{maturity_definitions_str}\n\n"
        f"Available Evidence:\n{evidence_content}"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    # Define the function schema
    functions = [
        {
            "name": "generate_questions",
            "description": "Generates questions to help assess the maturity level based on the criteria and available evidence. Always use british english.",
            "parameters": {
                "type": "object",
                "properties": {
                    "evidence_sufficient": {
                        "type": "boolean",
                        "description": "True if the current evidence is sufficient to assess the maturity level, False otherwise.",
                    },
                    "questions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "A list of questions. If evidence_sufficient is True, the questions should dig deeper into the most relevant areas of the current evidence. If evidence_sufficient is False, the questions should be the the minimal set of questions needed to fill the gaps in knowledge.",
                    },
                },
                "required": ["evidence_sufficient", "questions"],
            },
        }
    ]

    # Call the OpenAI API with function calling
    try:
        response = openAiClient.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            functions=functions,
            function_call={"name": "generate_questions"},
            max_tokens=2000,
            temperature=0.7,
        )

        # Extract the function call arguments
        function_call = response.choices[0].message.function_call
        if function_call and function_call.name == "generate_questions":
            arguments = json.loads(function_call.arguments)
            questions = arguments.get("questions", [])
            return questions
        else:
            print("Function call did not return as expected.")
            return []

    except Exception as e:
        print(f"Error generating questions: {str(e)}")
        return []


@app.get(
    "/audits/{audit_id}/questions/unanswered", response_model=List[QuestionResponse]
)
@authorize_company_access(required_roles=list(UserRole))
async def get_unanswered_questions(
    request: Request,
    audit_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    questions = (
        db.query(QuestionDB)
        .filter(QuestionDB.audit_id == audit_id)
        .filter(~QuestionDB.answers.any())
        .all()
    )
    return questions


@app.get("/audits/{audit_id}/questions/{question_id}", response_model=QuestionResponse)
@authorize_company_access(required_roles=list(UserRole))
async def get_question_details(
    request: Request,
    audit_id: str,
    question_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    question = (
        db.query(QuestionDB)
        .filter(QuestionDB.id == question_id, QuestionDB.audit_id == audit_id)
        .first()
    )

    if not question:
        raise HTTPException(status_code=404, detail="Question not found")

    return question


@app.post(
    "/audits/{audit_id}/questions/{question_id}/answers", response_model=AnswerResponse
)
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
    question = (
        db.query(QuestionDB)
        .filter(QuestionDB.id == question_id, QuestionDB.audit_id == audit_id)
        .first()
    )

    if not question:
        raise HTTPException(status_code=404, detail="Question not found")

    db_answer = AnswerDB(
        question_id=question_id, text=answer.text, submitted_by=answer.submitted_by
    )
    db.add(db_answer)
    db.commit()
    db.refresh(db_answer)

    return db_answer


@app.get("/audits/{audit_id}/questions", response_model=List[QuestionResponse])
@authorize_company_access(required_roles=list(UserRole))
async def get_all_questions(
    request: Request,
    audit_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    questions = (
        db.query(QuestionDB)
        .options(selectinload(QuestionDB.answers))
        .filter(QuestionDB.audit_id == audit_id)
        .all()
    )
    return questions


@app.get(
    "/audits/{audit_id}/questions/{question_id}/answers",
    response_model=List[AnswerResponse],
)
@authorize_company_access(required_roles=list(UserRole))
async def get_answers_for_question(
    request: Request,
    audit_id: str,
    question_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    question = (
        db.query(QuestionDB)
        .filter(QuestionDB.id == question_id, QuestionDB.audit_id == audit_id)
        .first()
    )

    if not question:
        raise HTTPException(status_code=404, detail="Question not found")

    answers = db.query(AnswerDB).filter(AnswerDB.question_id == question_id).all()
    return answers


@app.get(
    "/audits/{audit_id}/questions/{question_id}/answers/{answer_id}",
    response_model=AnswerResponse,
)
@authorize_company_access(required_roles=list(UserRole))
async def get_answer_details(
    request: Request,
    audit_id: str,
    question_id: str,
    answer_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    answer = (
        db.query(AnswerDB)
        .filter(AnswerDB.id == answer_id, AnswerDB.question_id == question_id)
        .join(QuestionDB)
        .filter(QuestionDB.audit_id == audit_id)
        .first()
    )

    if not answer:
        raise HTTPException(status_code=404, detail="Answer not found")

    return answer


@app.get(
    "/audits/{audit_id}/criteria/{criteria_id}/maturity",
    response_model=MaturityAssessmentResponse,
)
@authorize_company_access(required_roles=list(UserRole))
async def get_maturity_assessment(
    request: Request,
    audit_id: str,
    criteria_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    assessment = (
        db.query(MaturityAssessmentDB)
        .filter(
            MaturityAssessmentDB.audit_id == audit_id,
            MaturityAssessmentDB.criteria_id == criteria_id,
        )
        .first()
    )

    if not assessment:
        raise HTTPException(status_code=404, detail="Maturity assessment not found")

    return assessment


@app.post(
    "/audits/{audit_id}/criteria/{criteria_id}/maturity",
    response_model=MaturityAssessmentResponse,
)
@authorize_company_access(required_roles=[UserRole.AUDITOR])
async def set_maturity_assessment(
    request: Request,
    audit_id: str,
    criteria_id: str,
    assessment: MaturityAssessmentCreate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    # Check if the audit and criteria exist
    criteria = (
        db.query(CriteriaDB)
        .filter(CriteriaDB.id == criteria_id, CriteriaDB.audit_id == audit_id)
        .first()
    )
    if not criteria:
        raise HTTPException(status_code=404, detail="Audit or Criteria not found")

    # Check if an assessment already exists
    existing_assessment = (
        db.query(MaturityAssessmentDB)
        .filter(
            MaturityAssessmentDB.audit_id == audit_id,
            MaturityAssessmentDB.criteria_id == criteria_id,
        )
        .first()
    )

    if existing_assessment:
        # Update existing assessment
        existing_assessment.maturity_level = assessment.maturity_level
        existing_assessment.comments = assessment.comments
        existing_assessment.assessed_at = datetime.now(timezone.utc).isoformat()
        db_assessment = existing_assessment
    else:
        # Create new assessment
        db_assessment = MaturityAssessmentDB(
            audit_id=audit_id,
            criteria_id=criteria_id,
            maturity_level=assessment.maturity_level,
            comments=assessment.comments,
            assessed_by="Current User",  # Replace with actual user identification logic
        )
        db.add(db_assessment)

    db.commit()
    db.refresh(db_assessment)
    return db_assessment


@app.get(
    "/audits/{audit_id}/assessments", response_model=List[MaturityAssessmentResponse]
)
@authorize_company_access(required_roles=list(UserRole))
async def get_all_maturity_assessments(
    request: Request,
    audit_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    assessments = (
        db.query(MaturityAssessmentDB)
        .filter(MaturityAssessmentDB.audit_id == audit_id)
        .all()
    )

    return assessments


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
