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
    "sqlite:///./tech_audit.db", connect_args={"check_same_thread": False}
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
    section = Column(String)  # New column for section

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    children = relationship("CriteriaDB", back_populates="parent")
    parent = relationship("CriteriaDB", back_populates="children", remote_side=[id])


Base.metadata.create_all(bind=engine)


def read_criteria_from_json(file_path):
    with open(file_path, "r") as f:
        return json.load(f)


def allocate_new_ids(json_data):
    id_mapping = {}
    for section in json_data:
        for criteria in section["criteria"]:
            id_mapping[criteria["id"]] = str(uuid.uuid4())
            criteria["new_id"] = id_mapping[criteria["id"]]
    return id_mapping


def update_parent_ids(json_data, id_mapping):
    for section in json_data:
        for criteria in section["criteria"]:
            if "parent" in criteria and criteria["parent"]:
                criteria["new_parent_id"] = id_mapping[criteria["parent"]]
            else:
                criteria["new_parent_id"] = None


def populate_criteria_from_json(db, json_data):
    for section in json_data:
        section_name = section["section"]
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
    db.commit()


if __name__ == "__main__":
    db = SessionLocal()
    try:
        json_data = read_criteria_from_json("criteria.json")
        id_mapping = allocate_new_ids(json_data)
        update_parent_ids(json_data, id_mapping)
        populate_criteria_from_json(db, json_data)
        print("Criteria populated successfully!")
    except Exception as e:
        print(f"An error occurred: {e}")
        db.rollback()
    finally:
        db.close()
