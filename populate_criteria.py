import json
import uuid
from sqlalchemy import (
    create_engine,
    Column,
    String,
    JSON,
    Boolean,
    ForeignKey,
    DateTime,
    func,
)
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.ext.declarative import declarative_base

# Database setup
engine = create_engine(
    "sqlite:///./database/tech_audit.db", connect_args={"check_same_thread": False}
)
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


def allocate_new_ids(json_data):
    # Create separate id mappings for each section
    section_id_mappings = {}

    for section in json_data:
        section_name = section["section"]
        section_id_mappings[section_name] = {}

        for criteria in section["criteria"]:
            new_id = str(uuid.uuid4())
            section_id_mappings[section_name][criteria["id"]] = new_id
            criteria["new_id"] = new_id

    return section_id_mappings


def update_parent_ids(json_data, section_id_mappings):
    for section in json_data:
        section_name = section["section"]
        section_mapping = section_id_mappings[section_name]

        for criteria in section["criteria"]:
            if "parent" in criteria and criteria["parent"]:
                # Only look up parent IDs within the same section
                if criteria["parent"] in section_mapping:
                    criteria["new_parent_id"] = section_mapping[criteria["parent"]]
                else:
                    print(
                        f"Warning: Parent ID {criteria['parent']} not found in section {section_name}"
                    )
                    criteria["new_parent_id"] = None
            else:
                criteria["new_parent_id"] = None


def populate_criteria_from_json(db, json_data):
    for section in json_data:
        section_name = section["section"]

        # First pass: Create all criteria entries
        for criteria in section["criteria"]:
            db_criteria = CriteriaDB(
                id=criteria["new_id"],
                parent_id=criteria.get("new_parent_id"),
                title=criteria["title"],
                description=criteria.get("description", ""),
                maturity_definitions=criteria.get("maturity_definitions", {}),
                is_specific_to_audit=None,
                section=section_name,
            )
            db.add(db_criteria)

        # Commit after each section to ensure all parents exist
        db.commit()


if __name__ == "__main__":
    db = SessionLocal()
    try:
        json_data = read_criteria_from_json("criteria.json")
        section_id_mappings = allocate_new_ids(json_data)
        update_parent_ids(json_data, section_id_mappings)
        populate_criteria_from_json(db, json_data)
        print("Criteria populated successfully!")
    except Exception as e:
        print(f"An error occurred: {e}")
        db.rollback()
    finally:
        db.close()
