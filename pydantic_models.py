from datetime import datetime
from enum import Enum
from typing import List, Optional, Dict
from pydantic import BaseModel, Field, field_validator, ConfigDict

class MaturityLevel(str, Enum):
    novice = "novice"
    intermediate = "intermediate"
    advanced = "advanced"

class CompanySize(str, Enum):
    unknown = "unknown"
    micro = "micro"
    small = "small"
    medium = "medium"
    large = "large"

class UserRole(str, Enum):
    AUDITOR = "auditor"
    ORGANISATION_LEAD = "organisation_lead"
    ORGANISATION_USER = "organisation_user"
    DELEGATED_USER = "delegated_user"
    OBSERVER_LEAD = "observer_lead"
    OBSERVER_USER = "observer_user"

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
    assessed_at: datetime

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
