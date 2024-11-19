import json
import uuid
from sqlalchemy import create_engine, Column, String, JSON, Boolean, ForeignKey, DateTime, func
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.ext.declarative import declarative_base

engine = create_engine("sqlite:///./tech_audit.db", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class CriteriaDB(Base):
    __tablename__ = "criteria"
    id = Column(String, primary_key=True, index=True)
    parent_id = Column(String, ForeignKey("criteria.id"), nullable=True)
    title = Column(String)
    description = Column(String)
    maturity_definitions = Column(JSON)
    is_specific_to_audit = Column(String)
    section = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    children = relationship("CriteriaDB", back_populates="parent")
    parent = relationship("CriteriaDB", back_populates="children", remote_side=[id])

Base.metadata.create_all(bind=engine)

def read_criteria_from_json(file_path):
    with open(file_path, "r") as f:
        return json.load(f)

def process_criteria(criteria, section_name, parent_id=None):
    """Process a criteria and its children, returning a list of all criteria entries."""
    criteria_list = []
    
    # Generate ID for current criteria
    current_id = str(uuid.uuid4())
    
    # Create entry for current criteria
    criteria_entry = {
        "id": current_id,
        "parent_id": parent_id,
        "title": criteria["title"],
        "description": criteria.get("description", ""),
        "maturity_definitions": criteria.get("maturity_definitions", {}),
        "is_specific_to_audit": None,
        "section": section_name
    }
    criteria_list.append(criteria_entry)
    
    # Process children if they exist
    if "children" in criteria:
        for child in criteria["children"]:
            child_entries = process_criteria(child, section_name, current_id)
            criteria_list.extend(child_entries)
            
    return criteria_list

def populate_criteria_from_json(db, json_data):
    all_criteria = []
    
    # Process each section
    for section in json_data:
        section_name = section["section"]
        
        # Process each top-level criteria in the section
        for criteria in section["criteria"]:
            criteria_entries = process_criteria(criteria, section_name)
            all_criteria.extend(criteria_entries)
    
    # Add all criteria to database
    for criteria_entry in all_criteria:
        db_criteria = CriteriaDB(**criteria_entry)
        db.add(db_criteria)
    
    db.commit()

if __name__ == "__main__":
    db = SessionLocal()
    try:
        json_data = read_criteria_from_json("criteria_restructured_tidy.json")
        populate_criteria_from_json(db, json_data)
        print("Criteria populated successfully!")
    except Exception as e:
        print(f"An error occurred: {e}")
        db.rollback()
    finally:
        db.close()
