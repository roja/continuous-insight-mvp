# Standard library imports
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional

# SQLAlchemy imports
from sqlalchemy import (
    Column, Integer, String, Boolean, Text, ForeignKey, JSON, 
    DateTime, func, UniqueConstraint, CheckConstraint
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import relationship, backref

Base = declarative_base()

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
    is_global_administrator = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    company_associations = relationship(
        "UserCompanyAssociation", back_populates="user", overlaps="companies,users"
    )
    companies = relationship(
        "CompanyDB",
        secondary="user_company_associations",
        viewonly=True,
    )

    @property
    def company_roles(self) -> Dict[str, UserRole]:
        return {
            assoc.company_id: UserRole(assoc.role)
            for assoc in self.company_associations
        }

    @property
    def accessible_companies(self) -> List[str]:
        return [assoc.company_id for assoc in self.company_associations]

    def has_company_role(self, company_id: str, required_roles: List[UserRole]) -> bool:
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
    role = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    user = relationship("UserDB", back_populates="company_associations")
    company = relationship("CompanyDB", back_populates="user_associations")

    __table_args__ = (
        UniqueConstraint("user_id", "company_id", name="uq_user_company"),
        CheckConstraint(role.in_([r.value for r in UserRole]), name="valid_role"),
    )

class AuditDB(Base):
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
    custom_criteria = relationship("CriteriaDB", back_populates="specific_audit")

class CompanyDB(Base):
    __tablename__ = "companies"
    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, index=True)
    description = Column(String, nullable=True)
    sector = Column(String, nullable=True)
    size = Column(String, nullable=True)
    business_type = Column(String, nullable=True)
    technology_stack = Column(String, nullable=True)
    areas_of_focus = Column(String, nullable=True)
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
        viewonly=True,
    )

class EvidenceFileDB(Base):
    __tablename__ = "evidence_files"
    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    audit_id = Column(String, ForeignKey("audits.id"))
    filename = Column(String)
    file_type = Column(String)
    status = Column(String)
    file_path = Column(String)
    uploaded_at = Column(DateTime(timezone=True), server_default=func.now())
    processed_at = Column(DateTime(timezone=True), nullable=True)
    text_content = Column(Text, nullable=True)

    audit = relationship("AuditDB", back_populates="evidence_files")

class CriteriaDB(Base):
    __tablename__ = "criteria"
    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    parent_id = Column(String, ForeignKey("criteria.id"), nullable=True)
    title = Column(String)
    description = Column(String)
    maturity_definitions = Column(JSON)
    is_specific_to_audit = Column(String, ForeignKey("audits.id"), nullable=True)
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
    specific_audit = relationship("AuditDB", back_populates="custom_criteria")

    def __repr__(self):
        return f"<Criteria(id='{self.id}', title='{self.title}', parent_id='{self.parent_id}', is_specific_to_audit='{self.is_specific_to_audit}')>"

class AuditCriteriaDB(Base):
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
    __tablename__ = "answers"
    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    question_id = Column(String, ForeignKey("questions.id"))
    text = Column(Text)
    submitted_by = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    question = relationship("QuestionDB", back_populates="answers")

class MaturityAssessmentDB(Base):
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
