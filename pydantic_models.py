from datetime import datetime
from enum import Enum
from typing import List, Optional, Dict, TypeVar, Generic
from pydantic import BaseModel, Field, field_validator, ConfigDict, create_model

# Enums
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

# Mixins
class TimestampMixin(BaseModel):
    created_at: datetime
    updated_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None

class IDMixin(BaseModel):
    id: str

class AuditRelatedMixin(BaseModel):
    audit_id: str

class CompanyRelatedMixin(BaseModel):
    company_id: str

# Base Models
class BaseRequestModel(BaseModel):
    """Base class for all request models"""
    model_config = ConfigDict(extra="forbid")

class BaseResponseModel(IDMixin, TimestampMixin):
    """Base class for all response models"""
    model_config = ConfigDict(from_attributes=True)

T = TypeVar("T")

class ListResponse(BaseModel, Generic[T]):
    """Generic list response wrapper"""
    items: List[T]
    total: int
    skip: int
    limit: int

# User Models
class UserBase(BaseModel):
    email: str
    name: str

class UserCompanyAssociationBase(BaseModel):
    role: UserRole

class UserCompanyAssociationCreate(UserCompanyAssociationBase):
    company_id: str

class UserCompanyAssociationResponse(UserCompanyAssociationBase, BaseResponseModel):
    user_id: str
    company_id: str

class UserResponse(UserBase, BaseResponseModel):
    is_global_administrator: bool
    company_associations: List[UserCompanyAssociationResponse]

class AddUserToCompanyRequest(BaseRequestModel):
    user_id: str
    role: UserRole

# Company Models
class CompanyBase(BaseModel):
    name: str = Field(..., min_length=1)
    description: Optional[str] = None
    sector: Optional[str] = None
    size: Optional[CompanySize] = Field(default=None, validate_default=True)
    business_type: Optional[str] = None
    technology_stack: Optional[str] = None
    areas_of_focus: Optional[List[str]] = None

    @field_validator("size", mode="before")
    def validate_size(cls, v):
        if isinstance(v, str):
            if not v.strip():  # Handle empty strings
                return None
            try:
                return CompanySize(v.lower())
            except ValueError:
                raise ValueError(
                    f"Invalid size. Must be one of: {', '.join(CompanySize.__members__)}"
                )
        return v

    @field_validator("areas_of_focus", mode="before")
    @classmethod
    def split_areas_of_focus(cls, v):
        if isinstance(v, str):
            return v.split(",") if v else []
        return v

class CompanyCreate(CompanyBase, BaseRequestModel):
    pass

class CompanyResponse(CompanyBase, BaseResponseModel):
    pass

class CompanyListResponse(BaseResponseModel):
    name: str
    sector: Optional[str]
    description: Optional[str] = None
    size: Optional[str]
    business_type: Optional[str]

class CompanyUserResponse(BaseModel):
    id: str
    email: str
    name: str
    role: UserRole

# Audit Models
class AuditBase(BaseModel):
    name: str
    description: Optional[str] = None

class AuditCreate(AuditBase, BaseRequestModel):
    company_id: str

class AuditResponse(AuditBase, BaseResponseModel, CompanyRelatedMixin):
    pass

class AuditListResponse(AuditBase, IDMixin):
    created_at: datetime
    updated_at: Optional[datetime] = None

# Criteria Models
class CriteriaBase(BaseModel):
    title: str
    description: str
    section: str

class CriteriaCreate(CriteriaBase, BaseRequestModel):
    parent_id: Optional[str] = None
    maturity_definitions: Dict[MaturityLevel, str]
    expected_maturity_level: MaturityLevel

class CriteriaResponse(CriteriaBase, BaseResponseModel):
    parent_id: Optional[str]
    maturity_definitions: dict
    is_specific_to_audit: Optional[str]

class CriteriaSelect(BaseRequestModel):
    criteria_id: str
    expected_maturity_level: Optional[MaturityLevel] = None

class CriteriaSelectionResponse(BaseResponseModel, AuditRelatedMixin):
    criteria_id: str
    expected_maturity_level: Optional[MaturityLevel]

# Evidence Models
class EvidenceBase(BaseModel):
    content: str
    source: str
    source_id: str

class EvidenceCreate(EvidenceBase, BaseRequestModel):
    pass

class EvidenceResponse(EvidenceBase, BaseResponseModel, AuditRelatedMixin):
    criteria_id: str
    evidence_type: str
    start_position: Optional[int] = None

class EvidenceFileResponse(IDMixin, AuditRelatedMixin):
    filename: str
    file_type: str
    status: str
    uploaded_at: datetime
    processed_at: Optional[datetime] = None
    
    model_config = ConfigDict(from_attributes=True)

# Question and Answer Models
class QuestionBase(BaseModel):
    text: str

class QuestionCreate(QuestionBase, BaseRequestModel):
    pass

class AnswerBase(BaseModel):
    text: str
    submitted_by: str

class AnswerCreate(AnswerBase, BaseRequestModel):
    pass

class AnswerResponse(AnswerBase, BaseResponseModel):
    pass

class QuestionResponse(QuestionBase, BaseResponseModel):
    answers: List[AnswerResponse]

# Assessment Models
class MaturityAssessmentBase(BaseModel):
    maturity_level: MaturityLevel
    comments: Optional[str] = None

class MaturityAssessmentCreate(MaturityAssessmentBase, BaseRequestModel):
    pass

class MaturityAssessmentResponse(MaturityAssessmentBase, BaseResponseModel):
    criteria_id: str
    assessed_by: str
    assessed_at: datetime

# Composite Response Models
class CriteriaEvidenceResponse(BaseModel):
    evidence: List[EvidenceResponse]
    questions: List[QuestionResponse]

# Operation Response Models
class RemoveCriteriaRequest(BaseRequestModel):
    criteria_id: str

class RemoveCriteriaResponse(BaseModel):
    message: str
    audit_id: str
    criteria_id: str

class DeleteCustomCriteriaResponse(BaseModel):
    message: str
    criteria_id: str

class ParseEvidenceRequest(BaseRequestModel):
    file_ids: Optional[List[str]] = None
    text_content: Optional[str] = None

    @field_validator('text_content')
    @classmethod
    def validate_input_provided(cls, v, info):
        if v is None and not info.data.get('file_ids'):
            raise ValueError("Either file_ids or text_content must be provided")
        return v

class UpdateCustomCriteriaRequest(BaseRequestModel):
    title: Optional[str] = None
    description: Optional[str] = None
    parent_id: Optional[str] = None
    maturity_definitions: Optional[Dict[str, str]] = None
    section: Optional[str] = None

# Auth Models
class GoogleAuthRequest(BaseRequestModel):
    token: str
