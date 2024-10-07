# Standard library imports
import json
import os
import random
import shutil
import uuid
import time
import openai
import ffmpeg
import subprocess

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional, Dict


# Third-party imports
from fastapi import (
    FastAPI,
    HTTPException,
    Depends,
    File,
    UploadFile,
    status,
    BackgroundTasks,
)
from fastapi.security import APIKeyHeader
from fastapi.responses import FileResponse
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
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship, selectinload


class Settings(BaseSettings):
    database_url: str = Field(default="sqlite:///./tech_audit.db")
    api_key: str = Field(default="your_api_key_here")
    openai_api_key: str = Field(default="your_openai_api_key_here")

    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()


# Database setup
engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# SQLAlchemy models
class AuditDB(Base):
    __tablename__ = "audits"
    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, index=True)
    description = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    company = relationship("CompanyDB", back_populates="audit", uselist=False)
    evidence_files = relationship("EvidenceFileDB", back_populates="audit")
    criteria = relationship("CriteriaDB", back_populates="audit")
    questions = relationship("QuestionDB", back_populates="audit")
    maturity_assessments = relationship("MaturityAssessmentDB", back_populates="audit")


class CompanyDB(Base):
    __tablename__ = "companies"
    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    audit_id = Column(String, ForeignKey("audits.id"), unique=True)
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

    audit = relationship("AuditDB", back_populates="company")


class EvidenceFileDB(Base):
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
    __tablename__ = "criteria"
    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    audit_id = Column(String, ForeignKey("audits.id"))
    parent_id = Column(String, nullable=True)
    title = Column(String)
    description = Column(String)
    maturity_definitions = Column(JSON)
    selected = Column(Boolean, default=False)
    expected_maturity_level = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    audit = relationship("AuditDB", back_populates="criteria")
    evidence = relationship("EvidenceDB", back_populates="criteria")
    questions = relationship("QuestionDB", back_populates="criteria")
    maturity_assessment = relationship(
        "MaturityAssessmentDB", back_populates="criteria", uselist=False
    )


class EvidenceDB(Base):
    __tablename__ = "evidence"
    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    audit_id = Column(String, ForeignKey("audits.id"))
    criteria_id = Column(String, ForeignKey("criteria.id"))
    content = Column(Text)
    source = Column(String)
    source_id = Column(String)
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


# Pydantic models
class AuditCreate(BaseModel):
    name: str
    description: str = Field(default=None)


class AuditResponse(BaseModel):
    id: str
    name: str
    description: str = Field(default=None)

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
    parent_id: Optional[str] = Field(default=None)
    maturity_definitions: dict


class CriteriaResponse(CriteriaCreate):
    id: str
    audit_id: str

    model_config = ConfigDict(from_attributes=True)


class CompanySize(str, Enum):
    small = "small"
    medium = "medium"
    large = "large"


class CompanyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    sector: Optional[str] = Field(None, max_length=50)
    size: Optional[CompanySize] = None
    business_type: Optional[str] = Field(None, max_length=50)
    technology_stack: Optional[str] = Field(None, max_length=200)
    areas_of_focus: Optional[List[str]] = Field(None, max_length=10)

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

    @field_validator("areas_of_focus")
    def check_areas_of_focus(cls, v):
        if v:
            if len(v) > 10:
                raise ValueError("Maximum of 10 areas of focus allowed")
            if any(len(area) > 50 for area in v):
                raise ValueError("Each area of focus must be 50 characters or less")
        return v


class CompanyResponse(CompanyCreate):
    id: str
    audit_id: str

    model_config = ConfigDict(from_attributes=True)

    @field_validator("areas_of_focus", mode="before")
    @classmethod
    def split_areas_of_focus(cls, v):
        if isinstance(v, str):
            return v.split(",") if v else []
        return v


class CriteriaSelect(BaseModel):
    criteria_ids: List[str]
    expected_maturity_levels: dict


class CriteriaSelectionResponse(BaseModel):
    id: str
    title: str
    selected: bool
    expected_maturity_level: Optional[str]

    model_config = ConfigDict(from_attributes=True)


class EvidenceCreate(BaseModel):
    content: str
    source: str
    source_id: str


class EvidenceResponse(EvidenceCreate):
    id: str
    audit_id: str
    criteria_id: str

    model_config = ConfigDict(from_attributes=True)


class QuestionCreate(BaseModel):
    text: str


class QuestionResponse(QuestionCreate):
    id: str
    audit_id: str
    criteria_id: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class QuestionResponse(BaseModel):
    id: str
    audit_id: str
    criteria_id: str
    text: str
    created_at: datetime
    answered: bool = Field(default=False)

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_orm(cls, obj):
        # Create a dictionary representation of the object
        dict_obj = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
        # Add the 'answered' field based on whether answers exist
        dict_obj["answered"] = bool(obj.answers)
        return cls(**dict_obj)


class AnswerCreate(BaseModel):
    text: str
    submitted_by: str


class AnswerResponse(AnswerCreate):
    id: str
    question_id: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MaturityLevel(str, Enum):
    novice = "novice"
    intermediate = "intermediate"
    advanced = "advanced"


class MaturityAssessmentCreate(BaseModel):
    maturity_level: MaturityLevel
    comments: Optional[str] = Field(default=None)


from datetime import datetime


class MaturityAssessmentResponse(MaturityAssessmentCreate):
    id: str
    criteria_id: str
    assessed_by: str
    assessed_at: datetime  # Changed from 'str' to 'datetime'

    model_config = ConfigDict(from_attributes=True)


class CriteriaSelect(BaseModel):
    criteria_ids: List[str]
    expected_maturity_levels: Dict[str, str]


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
API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)


async def get_api_key(api_key_header: str = Depends(api_key_header)):
    if api_key_header == settings.api_key:
        return api_key_header
    raise HTTPException(status_code=403, detail="Could not validate credentials")


# FastAPI app
app = FastAPI()


def read_criteria_from_json():
    json_path = os.path.join(os.path.dirname(__file__), "criteria.json")
    with open(json_path, "r") as f:
        return json.load(f)


def create_criteria_from_json(db: Session, audit_id: int):
    criteria_data = read_criteria_from_json()
    for section in criteria_data:
        for criteria in section["criteria"]:
            db_criteria = CriteriaDB(
                audit_id=audit_id,
                title=criteria["title"],
                description=criteria.get("description", ""),
                parent_id=criteria.get("parent"),
                maturity_definitions={
                    "novice": criteria.get("novice", ""),
                    "intermediate": criteria.get("intermediate", ""),
                    "advanced": criteria.get("advanced", ""),
                },
            )
            db.add(db_criteria)
    db.commit()


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
        else:
            text_content = convert_with_pandoc(file_path)

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


def transcribe_audio(audio_path: str) -> str:
    with open(audio_path, "rb") as audio_file:
        transcript = openai.Audio.transcribe("whisper-1", audio_file)
    return transcript["text"]


def convert_with_pandoc(file_path: str) -> str:
    output_path = file_path + ".txt"
    try:
        subprocess.run(["pandoc", "-o", output_path, file_path], check=True)
        with open(output_path, "r", encoding="utf-8") as f:
            content = f.read()
        os.remove(output_path)  # Clean up temporary text file
        return content
    except subprocess.CalledProcessError:
        raise Exception(f"Pandoc conversion failed for file: {file_path}")


def save_text_content(db_file: EvidenceFileDB, content: str):
    # Assuming you have a column to store the text content in EvidenceFileDB
    # If not, you might need to create a new table or add a column
    db_file.text_content = content


def simulate_evidence_extraction(db: Session, audit_id: str, criteria_id: str):
    # Simulate processing delay
    import time

    # time.sleep(5)

    # Get all evidence files for the audit
    evidence_files = (
        db.query(EvidenceFileDB).filter(EvidenceFileDB.audit_id == audit_id).all()
    )

    # Simulate extracting evidence from each file
    for file in evidence_files:
        # Generate some random "extracted" content
        extracted_content = f"Simulated evidence extracted from {file.filename}"

        # Create a new evidence entry
        new_evidence = EvidenceDB(
            audit_id=audit_id,
            criteria_id=criteria_id,
            content=extracted_content,
            source="evidence_file",
            source_id=file.id,
        )
        db.add(new_evidence)

    # Commit the changes
    db.commit()


# Endpoints


@app.post("/audits", response_model=AuditResponse)
def create_audit(audit: AuditCreate, db: Session = Depends(get_db)):
    db_audit = AuditDB(**audit.model_dump())
    db.add(db_audit)
    db.commit()
    db.refresh(db_audit)

    # Automatically import criteria after creating the audit
    create_criteria_from_json(db, db_audit.id)

    return db_audit


@app.get("/audits/{audit_id}", response_model=AuditResponse)
async def get_audit(
    audit_id: str, db: Session = Depends(get_db), api_key: str = Depends(get_api_key)
):
    db_audit = db.query(AuditDB).filter(AuditDB.id == audit_id).first()
    if db_audit is None:
        raise HTTPException(status_code=404, detail="Audit not found")
    return db_audit


@app.delete("/audits/{audit_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_audit(
    audit_id: str, db: Session = Depends(get_db), api_key: str = Depends(get_api_key)
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
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    api_key: str = Depends(get_api_key),
):
    audits = db.query(AuditDB).offset(skip).limit(limit).all()
    return audits


@app.post("/audits/{audit_id}/company", response_model=CompanyResponse)
async def create_company(
    audit_id: str,
    company: CompanyCreate,
    db: Session = Depends(get_db),
    api_key: str = Depends(get_api_key),
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


@app.get("/audits/{audit_id}/company", response_model=CompanyResponse)
async def get_company(
    audit_id: str, db: Session = Depends(get_db), api_key: str = Depends(get_api_key)
):
    db_company = db.query(CompanyDB).filter(CompanyDB.audit_id == audit_id).first()
    if db_company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return db_company


@app.put("/audits/{audit_id}/company", response_model=CompanyResponse)
async def update_company(
    audit_id: str,
    company: CompanyCreate,
    db: Session = Depends(get_db),
    api_key: str = Depends(get_api_key),
):
    db_company = db.query(CompanyDB).filter(CompanyDB.audit_id == audit_id).first()
    if db_company is None:
        raise HTTPException(status_code=404, detail="Company not found")

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
async def parse_evidence_for_company(
    audit_id: str, db: Session = Depends(get_db), api_key: str = Depends(get_api_key)
):
    import json

    # Get the audit
    db_audit = db.query(AuditDB).filter(AuditDB.id == audit_id).first()
    if db_audit is None:
        raise HTTPException(status_code=404, detail="Audit not found")

    # Get all processed evidence files with text content
    evidence_files = (
        db.query(EvidenceFileDB)
        .filter(
            EvidenceFileDB.audit_id == audit_id,
            EvidenceFileDB.status == "complete",
            EvidenceFileDB.text_content != None,
        )
        .all()
    )

    if not evidence_files:
        raise HTTPException(status_code=404, detail="No processed evidence files found")

    # Combine text contents
    texts = [file.text_content for file in evidence_files]
    combined_text = "\n".join(texts)

    # Define the function schema for function calling
    company_info_function = {
        "name": "extract_company_info",
        "description": "Extracts company information from the provided text.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The name of the company."},
                "description": {
                    "type": "string",
                    "description": "A description of the company.",
                },
                "sector": {
                    "type": "string",
                    "description": "The sector the company operates in.",
                },
                "size": {
                    "type": "string",
                    "description": "The size of the company (small, medium, large).",
                    "enum": ["small", "medium", "large"],
                },
                "business_type": {
                    "type": "string",
                    "description": "The type of business the company is.",
                },
                "technology_stack": {
                    "type": "string",
                    "description": "Technologies used by the company.",
                },
                "areas_of_focus": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Areas of focus of the company.",
                },
            },
            "required": ["name"],
        },
    }

    # Define the system prompt
    system_prompt = (
        "You are an AI assistant that extracts company information from the provided text. "
        "Extract the information and format it as per the specified JSON schema."
    )

    # Prepare the messages
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": combined_text},
    ]

    # Set the OpenAI API key
    openai.api_key = settings.openai_api_key

    # Call the OpenAI API
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=messages,
            functions=[company_info_function],
            function_call={"name": "extract_company_info"},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"OpenAI API error: {str(e)}")

    # Extract the function call arguments
    function_call = response["choices"][0]["message"]["function_call"]
    arguments_str = function_call["arguments"]

    # Parse the arguments as JSON
    try:
        arguments = json.loads(arguments_str)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail="Failed to parse LLM response")

    # Map the extracted data to company fields
    company_data = {}
    if "name" in arguments:
        company_data["name"] = arguments["name"]
    if "description" in arguments:
        company_data["description"] = arguments["description"]
    if "sector" in arguments:
        company_data["sector"] = arguments["sector"]
    if "size" in arguments:
        size = arguments["size"].lower()
        if size in ["small", "medium", "large"]:
            company_data["size"] = size
    if "business_type" in arguments:
        company_data["business_type"] = arguments["business_type"]
    if "technology_stack" in arguments:
        company_data["technology_stack"] = arguments["technology_stack"]
    if "areas_of_focus" in arguments:
        areas_of_focus = arguments["areas_of_focus"]
        if isinstance(areas_of_focus, list):
            if len(areas_of_focus) > 10:
                areas_of_focus = areas_of_focus[:10]
            areas_of_focus = [area for area in areas_of_focus if len(area) <= 50]
            company_data["areas_of_focus"] = ",".join(areas_of_focus)

    if not company_data:
        raise HTTPException(status_code=500, detail="No valid company data extracted")

    # Get or create the company record
    db_company = db.query(CompanyDB).filter(CompanyDB.audit_id == audit_id).first()
    if db_company is None:
        db_company = CompanyDB(audit_id=audit_id)

    # Update the company record
    for key, value in company_data.items():
        setattr(db_company, key, value)

    # Mark that the company was updated from evidence
    db_company.updated_from_evidence = True

    db.add(db_company)
    db.commit()
    db.refresh(db_company)

    # Return the updated company data
    return db_company


@app.post("/audits/{audit_id}/evidence-files", response_model=EvidenceFileResponse)
async def upload_evidence_file(
    audit_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    api_key: str = Depends(get_api_key),
    background_tasks: BackgroundTasks = BackgroundTasks(),
):
    # Create directory for audit if it doesn't exist
    os.makedirs(f"evidence_files/{audit_id}", exist_ok=True)

    # Generate a unique filename to avoid conflicts
    unique_filename = f"{uuid.uuid4()}_{file.filename}"
    file_path = f"evidence_files/{audit_id}/{unique_filename}"

    # Save file
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

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

    # Start processing in background
    background_tasks.add_task(process_file, file_path, db, db_file.id)

    return db_file


@app.get("/audits/{audit_id}/evidence-files", response_model=List[EvidenceFileResponse])
async def list_evidence_files(
    audit_id: str, db: Session = Depends(get_db), api_key: str = Depends(get_api_key)
):
    files = db.query(EvidenceFileDB).filter(EvidenceFileDB.audit_id == audit_id).all()
    return files


@app.get(
    "/audits/{audit_id}/evidence-files/{file_id}", response_model=EvidenceFileResponse
)
async def get_evidence_file(
    audit_id: str,
    file_id: str,
    db: Session = Depends(get_db),
    api_key: str = Depends(get_api_key),
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
async def get_evidence_file_content(
    audit_id: str,
    file_id: str,
    db: Session = Depends(get_db),
    api_key: str = Depends(get_api_key),
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
async def delete_evidence_file(
    audit_id: str,
    file_id: str,
    db: Session = Depends(get_db),
    api_key: str = Depends(get_api_key),
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
async def check_evidence_file_status(
    audit_id: str,
    file_id: str,
    db: Session = Depends(get_db),
    api_key: str = Depends(get_api_key),
):
    file = (
        db.query(EvidenceFileDB)
        .filter(EvidenceFileDB.id == file_id, EvidenceFileDB.audit_id == audit_id)
        .first()
    )
    if file is None:
        raise HTTPException(status_code=404, detail="Evidence file not found")
    return file


@app.post("/audits/{audit_id}/criteria", response_model=CriteriaResponse)
async def add_criteria(
    audit_id: str,
    criteria: CriteriaCreate,
    db: Session = Depends(get_db),
    api_key: str = Depends(get_api_key),
):
    db_criteria = CriteriaDB(**criteria.model_dump(), audit_id=audit_id)
    db.add(db_criteria)
    db.commit()
    db.refresh(db_criteria)
    return db_criteria


@app.get("/audits/{audit_id}/criteria", response_model=List[CriteriaResponse])
async def list_criteria(
    audit_id: str, db: Session = Depends(get_db), api_key: str = Depends(get_api_key)
):
    criteria = db.query(CriteriaDB).filter(CriteriaDB.audit_id == audit_id).all()
    return criteria


@app.get("/audits/{audit_id}/criteria", response_model=List[CriteriaResponse])
async def get_all_criteria(
    audit_id: str, db: Session = Depends(get_db), api_key: str = Depends(get_api_key)
):
    criteria = db.query(CriteriaDB).filter(CriteriaDB.audit_id == audit_id).all()
    return criteria


@app.post("/audits/{audit_id}/criteria", response_model=CriteriaResponse)
async def add_custom_criteria(
    audit_id: str,
    criteria: CriteriaCreate,
    db: Session = Depends(get_db),
    api_key: str = Depends(get_api_key),
):
    db_criteria = CriteriaDB(
        audit_id=audit_id,
        parent_id=criteria.parent_id,
        title=criteria.title,
        description=criteria.description,
        maturity_definitions=criteria.maturity_definitions,
    )
    db.add(db_criteria)
    db.commit()
    db.refresh(db_criteria)
    return db_criteria


@app.put("/audits/{audit_id}/criteria/{criteria_id}", response_model=CriteriaResponse)
async def update_existing_criteria(
    audit_id: str,
    criteria_id: str,
    criteria: CriteriaCreate,
    db: Session = Depends(get_db),
    api_key: str = Depends(get_api_key),
):
    db_criteria = (
        db.query(CriteriaDB)
        .filter(CriteriaDB.id == criteria_id, CriteriaDB.audit_id == audit_id)
        .first()
    )
    if db_criteria is None:
        raise HTTPException(status_code=404, detail="Criteria not found")

    db_criteria.title = criteria.title
    db_criteria.description = criteria.description
    db_criteria.parent_id = criteria.parent_id
    db_criteria.maturity_definitions = criteria.maturity_definitions

    db.commit()
    db.refresh(db_criteria)
    return db_criteria


@app.get(
    "/audits/{audit_id}/criteria/selected",
    response_model=List[CriteriaSelectionResponse],
)
async def get_selected_criteria(
    audit_id: str, db: Session = Depends(get_db), api_key: str = Depends(get_api_key)
):
    selected_criteria = (
        db.query(CriteriaDB)
        .filter(CriteriaDB.audit_id == audit_id, CriteriaDB.selected == True)
        .all()
    )
    return selected_criteria


@app.post(
    "/audits/{audit_id}/criteria/selected",
    response_model=List[CriteriaSelectionResponse],
)
async def select_criteria(
    audit_id: str,
    criteria_select: CriteriaSelect,
    db: Session = Depends(get_db),
    api_key: str = Depends(get_api_key),
):
    criteria_to_update = (
        db.query(CriteriaDB)
        .filter(
            CriteriaDB.audit_id == audit_id,
            CriteriaDB.id.in_(criteria_select.criteria_ids),
        )
        .all()
    )

    if len(criteria_to_update) != len(criteria_select.criteria_ids):
        raise HTTPException(status_code=404, detail="One or more criteria not found")

    for criteria in criteria_to_update:
        criteria.selected = True
        criteria.expected_maturity_level = criteria_select.expected_maturity_levels.get(
            criteria.id
        )

    db.commit()
    return criteria_to_update


@app.delete(
    "/audits/{audit_id}/criteria/selected/{criteria_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def deselect_criteria(
    audit_id: str,
    criteria_id: str,
    db: Session = Depends(get_db),
    api_key: str = Depends(get_api_key),
):
    criteria = (
        db.query(CriteriaDB)
        .filter(CriteriaDB.audit_id == audit_id, CriteriaDB.id == criteria_id)
        .first()
    )

    if criteria is None:
        raise HTTPException(status_code=404, detail="Criteria not found")

    criteria.selected = False
    criteria.expected_maturity_level = None
    db.commit()

    return {"message": "Criteria deselected successfully"}


@app.post(
    "/audits/{audit_id}/criteria/selected/actions/preselect",
    response_model=List[CriteriaSelectionResponse],
)
async def preselect_criteria(
    audit_id: str, db: Session = Depends(get_db), api_key: str = Depends(get_api_key)
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


# Modify the existing get_all_criteria endpoint to include selection information
@app.get("/audits/{audit_id}/criteria", response_model=List[CriteriaSelectionResponse])
async def get_all_criteria(
    audit_id: str, db: Session = Depends(get_db), api_key: str = Depends(get_api_key)
):
    criteria = db.query(CriteriaDB).filter(CriteriaDB.audit_id == audit_id).all()
    return criteria


@app.post(
    "/audits/{audit_id}/criteria/{criteria_id}/actions/extract-evidence",
    status_code=status.HTTP_202_ACCEPTED,
)
async def extract_evidence_for_criteria(
    audit_id: str,
    criteria_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    api_key: str = Depends(get_api_key),
):
    # Check if the audit and criteria exist
    criteria = (
        db.query(CriteriaDB)
        .filter(CriteriaDB.id == criteria_id, CriteriaDB.audit_id == audit_id)
        .first()
    )
    if not criteria:
        raise HTTPException(status_code=404, detail="Audit or Criteria not found")

    # Simulate evidence extraction in the background
    background_tasks.add_task(simulate_evidence_extraction, db, audit_id, criteria_id)

    return {"message": "Evidence extraction started"}


@app.get(
    "/audits/{audit_id}/criteria/{criteria_id}/evidence",
    response_model=List[EvidenceResponse],
)
async def get_evidence_for_criteria(
    audit_id: str,
    criteria_id: str,
    db: Session = Depends(get_db),
    api_key: str = Depends(get_api_key),
):
    evidence = (
        db.query(EvidenceDB)
        .filter(EvidenceDB.audit_id == audit_id, EvidenceDB.criteria_id == criteria_id)
        .all()
    )

    if not evidence:
        raise HTTPException(
            status_code=404, detail="No evidence found for the given criteria"
        )

    return evidence


# Add this function to simulate evidence extraction
def simulate_evidence_extraction(db: Session, audit_id: str, criteria_id: str):
    # Simulate processing delay
    # time.sleep(5)

    # Get all evidence files for the audit
    evidence_files = (
        db.query(EvidenceFileDB).filter(EvidenceFileDB.audit_id == audit_id).all()
    )

    # Simulate extracting evidence from each file
    for file in evidence_files:
        # Generate some random "extracted" content
        extracted_content = f"Simulated evidence extracted from {file.filename}"

        # Create a new evidence entry
        new_evidence = EvidenceDB(
            audit_id=audit_id,
            criteria_id=criteria_id,
            content=extracted_content,
            source="evidence_file",
            source_id=file.id,
        )
        db.add(new_evidence)

    # Commit the changes
    db.commit()


@app.post(
    "/audits/{audit_id}/criteria/{criteria_id}/questions",
    response_model=List[QuestionResponse],
)
async def generate_questions(
    audit_id: str,
    criteria_id: str,
    db: Session = Depends(get_db),
    api_key: str = Depends(get_api_key),
):
    # Check if the audit and criteria exist
    criteria = (
        db.query(CriteriaDB)
        .filter(CriteriaDB.id == criteria_id, CriteriaDB.audit_id == audit_id)
        .first()
    )
    if not criteria:
        raise HTTPException(status_code=404, detail="Audit or Criteria not found")

    # Simulate question generation (in a real scenario, this would be done by an LLM)
    questions = simulate_question_generation(criteria)

    # Save generated questions to the database
    db_questions = []
    for question in questions:
        db_question = QuestionDB(
            audit_id=audit_id, criteria_id=criteria_id, text=question
        )
        db.add(db_question)
        db_questions.append(db_question)

    db.commit()
    for question in db_questions:
        db.refresh(question)

    return db_questions


@app.get(
    "/audits/{audit_id}/questions/unanswered", response_model=List[QuestionResponse]
)
async def get_unanswered_questions(
    audit_id: str, db: Session = Depends(get_db), api_key: str = Depends(get_api_key)
):
    questions = (
        db.query(QuestionDB)
        .filter(QuestionDB.audit_id == audit_id)
        .filter(~QuestionDB.answers.any())
        .all()
    )
    return questions


@app.get("/audits/{audit_id}/questions/{question_id}", response_model=QuestionResponse)
async def get_question_details(
    audit_id: str,
    question_id: str,
    db: Session = Depends(get_db),
    api_key: str = Depends(get_api_key),
):
    question = (
        db.query(QuestionDB)
        .filter(QuestionDB.id == question_id, QuestionDB.audit_id == audit_id)
        .first()
    )

    if not question:
        raise HTTPException(status_code=404, detail="Question not found")

    return question


def simulate_question_generation(criteria: CriteriaDB) -> List[str]:
    maturity_levels = criteria.maturity_definitions
    if isinstance(maturity_levels, str):
        try:
            maturity_levels = json.loads(maturity_levels)
        except json.JSONDecodeError:
            # If it's not a valid JSON string, use it as is
            maturity_levels = {
                "novice": maturity_levels,
                "intermediate": maturity_levels,
                "advanced": maturity_levels,
            }
    elif not isinstance(maturity_levels, dict):
        # If it's neither a string nor a dict, use a default dictionary
        maturity_levels = {
            "novice": "Novice level",
            "intermediate": "Intermediate level",
            "advanced": "Advanced level",
        }

    questions = [
        f"How does the company's approach align with the '{criteria.title}' criteria?",
        f"What evidence supports the company's maturity level in '{criteria.title}'?",
        f"How does the company plan to improve in the area of '{criteria.title}'?",
        f"What challenges does the company face in achieving the '{maturity_levels['advanced']}' level for this criteria?",
        f"Can you provide specific examples of how the company demonstrates the '{maturity_levels['intermediate']}' level in this area?",
    ]

    return random.sample(questions, random.randint(3, 5))


@app.post(
    "/audits/{audit_id}/questions/{question_id}/answers", response_model=AnswerResponse
)
async def submit_answer(
    audit_id: str,
    question_id: str,
    answer: AnswerCreate,
    db: Session = Depends(get_db),
    api_key: str = Depends(get_api_key),
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
async def get_all_questions(
    audit_id: str, db: Session = Depends(get_db), api_key: str = Depends(get_api_key)
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
async def get_answers_for_question(
    audit_id: str,
    question_id: str,
    db: Session = Depends(get_db),
    api_key: str = Depends(get_api_key),
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
async def get_answer_details(
    audit_id: str,
    question_id: str,
    answer_id: str,
    db: Session = Depends(get_db),
    api_key: str = Depends(get_api_key),
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
async def get_maturity_assessment(
    audit_id: str,
    criteria_id: str,
    db: Session = Depends(get_db),
    api_key: str = Depends(get_api_key),
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
async def set_maturity_assessment(
    audit_id: str,
    criteria_id: str,
    assessment: MaturityAssessmentCreate,
    db: Session = Depends(get_db),
    api_key: str = Depends(get_api_key),
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
async def get_all_maturity_assessments(
    audit_id: str, db: Session = Depends(get_db), api_key: str = Depends(get_api_key)
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
