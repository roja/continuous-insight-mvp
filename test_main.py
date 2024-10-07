import pytest
import time
import uuid
import os
import ffmpeg
import openai
import json
import subprocess

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from unittest.mock import patch, MagicMock, mock_open
from main import app, get_db, Base, settings, EvidenceFileDB, process_file

from pydantic import BaseModel
from typing import Dict, List


# Test database
TEST_SQLALCHEMY_DATABASE_URL = "sqlite:///./test.db"
engine = create_engine(
    TEST_SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

TestingSessionLocal = None  # Add this line


@pytest.fixture(scope="function")
def test_db():
    db_name = f"test_db_{uuid.uuid4()}.db"
    test_db_url = f"sqlite:///{db_name}"
    test_engine = create_engine(test_db_url, connect_args={"check_same_thread": False})

    Base.metadata.create_all(bind=test_engine)

    inspector = inspect(test_engine)
    columns = inspector.get_columns("evidence_files")
    print("\nEvidence Files Table Structure after creation:")
    for col in columns:
        print(f"Column: {col['name']}, Type: {col['type']}")

    TestingSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )

    def override_get_db():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    yield test_engine, TestingSessionLocal

    Base.metadata.drop_all(bind=test_engine)
    app.dependency_overrides.clear()

    os.remove(db_name)


@pytest.fixture(scope="function")
def client(test_db):
    test_engine, TestingSessionLocal = test_db  # Unpack test_db

    def override_get_db():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture
def mock_ffmpeg(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr(ffmpeg, "input", mock.input)
    monkeypatch.setattr(ffmpeg, "output", mock.output)
    monkeypatch.setattr(ffmpeg, "run", mock.run)
    return mock


@pytest.fixture
def mock_openai(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr(openai.Audio, "transcribe", mock.transcribe)
    return mock


@pytest.fixture
def mock_subprocess(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr(subprocess, "run", mock.run)
    return mock


def test_process_audio_file(client, test_db, mock_openai):
    create_audit_response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "Test Description"},
        headers={"X-API-Key": settings.api_key},
    )
    audit_id = create_audit_response.json()["id"]

    mock_openai.transcribe.return_value = {"text": "Transcribed audio content"}

    with open("test_audio.mp3", "wb") as f:
        f.write(b"fake audio content")

    with open("test_audio.mp3", "rb") as f:
        files = {"file": ("test_audio.mp3", f, "audio/mpeg")}
        response = client.post(
            f"/audits/{audit_id}/evidence-files",
            files=files,
            headers={"X-API-Key": settings.api_key},
        )

    assert response.status_code == 200
    file_id = response.json()["id"]

    engine, TestingSessionLocal = test_db

    with TestingSessionLocal() as db:
        try:
            db_file = (
                db.query(EvidenceFileDB).filter(EvidenceFileDB.id == file_id).first()
            )
            print(f"Retrieved file: {db_file}")
            if db_file:
                print(f"File path: {db_file.file_path}")
        except Exception as e:
            print(f"Error retrieving file: {e}")
            raise

        assert db_file.status == "complete"
        assert db_file.text_content == "Transcribed audio content"

    try:
        os.remove("test_audio.mp3")
    except OSError:
        pass


def test_process_video_file(client, test_db, mock_ffmpeg, mock_openai):
    # Create an audit
    create_audit_response = client.post(
        "/audits",
        json={"name": "Test Video Audit", "description": "Testing video processing"},
        headers={"X-API-Key": settings.api_key},
    )
    audit_id = create_audit_response.json()["id"]

    # Mock ffmpeg and OpenAI responses
    mock_ffmpeg.input.return_value = mock_ffmpeg
    mock_ffmpeg.output.return_value = mock_ffmpeg
    mock_ffmpeg.run.return_value = None
    mock_openai.transcribe.return_value = {"text": "Transcribed video content"}

    # Create a fake video file
    with open("test_video.mp4", "wb") as f:
        f.write(b"fake video content")

    # Upload the video file
    with open("test_video.mp4", "rb") as f:
        files = {"file": ("test_video.mp4", f, "video/mp4")}
        response = client.post(
            f"/audits/{audit_id}/evidence-files",
            files=files,
            headers={"X-API-Key": settings.api_key},
        )

    assert response.status_code == 200
    file_id = response.json()["id"]

    # Manually process the file
    engine, TestingSessionLocal = test_db
    with TestingSessionLocal() as db:
        db_file = db.query(EvidenceFileDB).filter(EvidenceFileDB.id == file_id).first()
        file_path = db_file.file_path

        # Since we are mocking ffmpeg.run, we need to create the .mp3 file
        audio_path = file_path.rsplit(".", 1)[0] + ".mp3"
        os.makedirs(os.path.dirname(audio_path), exist_ok=True)
        with open(audio_path, "wb") as f:
            f.write(b"fake audio content")

        # Call process_file directly
        process_file(file_path, db, file_id)
        db.refresh(db_file)

        # Check if the file was processed correctly
        assert db_file.status == "complete"
        assert db_file.text_content == "Transcribed video content"

    try:
        os.remove("test_video.mp4")
    except OSError:
        pass


def test_process_document_file(client, test_db, mock_subprocess):
    # Create an audit
    create_audit_response = client.post(
        "/audits",
        json={
            "name": "Test Document Audit",
            "description": "Testing document processing",
        },
        headers={"X-API-Key": settings.api_key},
    )
    audit_id = create_audit_response.json()["id"]

    # Mock subprocess.run to simulate pandoc conversion
    mock_subprocess.run.return_value = None

    # Create a mock document file
    with open("test_document.docx", "wb") as f:
        f.write(b"This is a test document content")

    # Upload the document file
    with open("test_document.docx", "rb") as f:
        files = {
            "file": (
                "test_document.docx",
                f,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        }
        response = client.post(
            f"/audits/{audit_id}/evidence-files",
            files=files,
            headers={"X-API-Key": settings.api_key},
        )

    assert response.status_code == 200
    file_id = response.json()["id"]

    # Manually process the file
    engine, TestingSessionLocal = test_db
    with TestingSessionLocal() as db:
        db_file = db.query(EvidenceFileDB).filter(EvidenceFileDB.id == file_id).first()
        file_path = db_file.file_path

        # Since we are mocking subprocess.run, we need to create the output text file
        output_path = file_path + ".txt"
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("Converted document content")

        # Call process_file directly
        process_file(file_path, db, file_id)
        db.refresh(db_file)

        # Check if the file was processed correctly
        assert db_file.status == "complete"
        assert db_file.text_content == "Converted document content"

    try:
        os.remove("test_document.docx")
    except OSError:
        pass


def test_process_unsupported_file(client, test_db, mock_subprocess):
    # Create an audit
    create_audit_response = client.post(
        "/audits",
        json={
            "name": "Test Unsupported File Audit",
            "description": "Testing unsupported file processing",
        },
        headers={"X-API-Key": settings.api_key},
    )
    audit_id = create_audit_response.json()["id"]

    # Create a mock unsupported file
    with open("test_unsupported.xyz", "wb") as f:
        f.write(b"Unsupported file content")

    # Upload the unsupported file
    with open("test_unsupported.xyz", "rb") as f:
        files = {"file": ("test_unsupported.xyz", f, "application/octet-stream")}
        response = client.post(
            f"/audits/{audit_id}/evidence-files",
            files=files,
            headers={"X-API-Key": settings.api_key},
        )

    assert response.status_code == 200
    file_id = response.json()["id"]

    # Manually process the file
    engine, TestingSessionLocal = test_db
    with TestingSessionLocal() as db:
        db_file = db.query(EvidenceFileDB).filter(EvidenceFileDB.id == file_id).first()
        file_path = db_file.file_path

        # Mock subprocess.run to simulate pandoc failing
        def mock_run(*args, **kwargs):
            raise subprocess.CalledProcessError(returncode=1, cmd=args[0])

        mock_subprocess.run.side_effect = mock_run

        # Call process_file directly
        process_file(file_path, db, file_id)
        db.refresh(db_file)

        # Check if the file processing failed
        assert db_file.status == "failed"
        assert db_file.text_content is None

    try:
        os.remove("test_unsupported.xyz")
    except OSError:
        pass


def test_add_custom_criteria(client):
    # Create an audit
    create_audit_response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "Test Description"},
        headers={"X-API-Key": settings.api_key},
    )
    audit_id = create_audit_response.json()["id"]

    # Add custom criteria
    custom_criteria = {
        "title": "Custom Criteria",
        "description": "Custom Description",
        "parent_id": None,
        "maturity_definitions": {
            "novice": "Custom Novice",
            "intermediate": "Custom Intermediate",
            "advanced": "Custom Advanced",
        },
    }
    add_custom_response = client.post(
        f"/audits/{audit_id}/criteria",
        json=custom_criteria,
        headers={"X-API-Key": settings.api_key},
    )

    assert add_custom_response.status_code == 200
    added_criteria = add_custom_response.json()
    assert added_criteria["title"] == "Custom Criteria"
    assert added_criteria["description"] == "Custom Description"
    assert (
        added_criteria["maturity_definitions"]
        == custom_criteria["maturity_definitions"]
    )


def test_get_selected_criteria(client):
    # Create an audit and add criteria
    create_audit_response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "Test Description"},
        headers={"X-API-Key": settings.api_key},
    )
    audit_id = create_audit_response.json()["id"]

    criteria_data = {
        "title": "Test Criteria",
        "description": "Test Description",
        "maturity_definitions": {
            "novice": "Novice level",
            "intermediate": "Intermediate level",
            "advanced": "Advanced level",
        },
    }
    add_criteria_response = client.post(
        f"/audits/{audit_id}/criteria",
        json=criteria_data,
        headers={"X-API-Key": settings.api_key},
    )
    criteria_id = add_criteria_response.json()["id"]

    # Select the criteria
    select_data = {
        "criteria_ids": [criteria_id],
        "expected_maturity_levels": {criteria_id: "intermediate"},
    }
    client.post(
        f"/audits/{audit_id}/criteria/selected",
        json=select_data,
        headers={"X-API-Key": settings.api_key},
    )

    # Get selected criteria
    get_selected_response = client.get(
        f"/audits/{audit_id}/criteria/selected",
        headers={"X-API-Key": settings.api_key},
    )

    assert get_selected_response.status_code == 200
    selected_criteria = get_selected_response.json()
    assert len(selected_criteria) == 1
    assert selected_criteria[0]["id"] == criteria_id
    assert selected_criteria[0]["selected"] == True
    assert selected_criteria[0]["expected_maturity_level"] == "intermediate"


def test_create_audit(client):
    response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "This is a test audit"},
        headers={"X-API-Key": settings.api_key},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Test Audit"
    assert data["description"] == "This is a test audit"
    assert "id" in data

    # Verify that criteria were automatically created
    audit_id = data["id"]
    criteria_response = client.get(
        f"/audits/{audit_id}/criteria",
        headers={"X-API-Key": settings.api_key},
    )
    assert criteria_response.status_code == 200
    criteria_data = criteria_response.json()
    assert len(criteria_data) > 0  # Ensure that criteria were created


def test_get_non_existent_audit(client):
    non_existent_id = "12345678-1234-5678-1234-567812345678"
    response = client.get(
        f"/audits/{non_existent_id}",
        headers={"X-API-Key": settings.api_key},
    )
    assert response.status_code == 404  # Not Found


def test_invalid_api_key(client):
    response = client.get(
        "/audits",
        headers={"X-API-Key": "invalid_key"},
    )
    assert response.status_code == 403  # Forbidden


import pytest
from fastapi.testclient import TestClient
from main import app, get_db, Base, settings

# ... (previous code remains the same)


def test_create_company_invalid_input(client):
    # First, create an audit
    create_audit_response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "This is a test audit"},
        headers={"X-API-Key": settings.api_key},
    )
    audit_id = create_audit_response.json()["id"]

    # Try to create a company with invalid input
    invalid_company_data = {
        "name": "Test Company",
        "size": "Invalid Size",  # Assuming 'size' should be a specific enum or int
    }
    response = client.post(
        f"/audits/{audit_id}/company",
        json=invalid_company_data,
        headers={"X-API-Key": settings.api_key},
    )

    # Check if the response is successful (200 OK)
    assert response.status_code == 200

    # Verify that the API doesn't store invalid data
    get_company_response = client.get(
        f"/audits/{audit_id}/company",
        headers={"X-API-Key": settings.api_key},
    )
    assert get_company_response.status_code == 200
    company_data = get_company_response.json()
    assert company_data["name"] == "Test Company"
    assert (
        company_data["size"] == "Invalid Size"
    )  # This might need to be changed based on how your API handles this field

    print(
        "Note: The API accepted invalid input. Consider implementing stricter input validation."
    )


# Additional error handling tests


def test_update_non_existent_company(client):
    non_existent_audit_id = "12345678-1234-5678-1234-567812345678"
    response = client.put(
        f"/audits/{non_existent_audit_id}/company",
        json={"name": "Updated Company"},
        headers={"X-API-Key": settings.api_key},
    )
    assert response.status_code == 404  # Not Found


def test_delete_non_existent_evidence_file(client):
    # First, create an audit
    create_audit_response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "This is a test audit"},
        headers={"X-API-Key": settings.api_key},
    )
    audit_id = create_audit_response.json()["id"]

    non_existent_file_id = "12345678-1234-5678-1234-567812345678"
    response = client.delete(
        f"/audits/{audit_id}/evidence-files/{non_existent_file_id}",
        headers={"X-API-Key": settings.api_key},
    )
    assert response.status_code == 404  # Not Found


def test_select_non_existent_criteria(client):
    # First, create an audit
    create_audit_response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "This is a test audit"},
        headers={"X-API-Key": settings.api_key},
    )
    audit_id = create_audit_response.json()["id"]

    non_existent_criteria_id = "12345678-1234-5678-1234-567812345678"
    select_data = {
        "criteria_ids": [non_existent_criteria_id],
        "expected_maturity_levels": {non_existent_criteria_id: "intermediate"},
    }
    response = client.post(
        f"/audits/{audit_id}/criteria/selected",
        json=select_data,
        headers={"X-API-Key": settings.api_key},
    )
    assert response.status_code == 404  # Not Found


def test_submit_answer_to_non_existent_question(client):
    # First, create an audit
    create_audit_response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "This is a test audit"},
        headers={"X-API-Key": settings.api_key},
    )
    audit_id = create_audit_response.json()["id"]

    non_existent_question_id = "12345678-1234-5678-1234-567812345678"
    answer_data = {"text": "This is a test answer", "submitted_by": "Test User"}
    response = client.post(
        f"/audits/{audit_id}/questions/{non_existent_question_id}/answers",
        json=answer_data,
        headers={"X-API-Key": settings.api_key},
    )
    assert response.status_code == 404  # Not Found


# Add more error handling tests as needed

# Add more error handling tests as needed


def test_create_audit(client):
    response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "This is a test audit"},
        headers={"X-API-Key": settings.api_key},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Test Audit"
    assert data["description"] == "This is a test audit"
    assert "id" in data


def test_get_audit(client):
    # First, create an audit
    create_response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "This is a test audit"},
        headers={"X-API-Key": settings.api_key},
    )
    assert create_response.status_code == 200
    audit_id = create_response.json()["id"]

    # Now, retrieve the audit
    get_response = client.get(
        f"/audits/{audit_id}", headers={"X-API-Key": settings.api_key}
    )
    assert get_response.status_code == 200
    data = get_response.json()
    assert data["name"] == "Test Audit"
    assert data["description"] == "This is a test audit"
    assert data["id"] == audit_id


def test_list_audits(client):
    # Create multiple audits
    audit_names = ["Audit 1", "Audit 2", "Audit 3"]
    for name in audit_names:
        client.post(
            "/audits",
            json={"name": name, "description": f"Description for {name}"},
            headers={"X-API-Key": settings.api_key},
        )

    # List all audits
    response = client.get("/audits", headers={"X-API-Key": settings.api_key})
    assert response.status_code == 200
    data = response.json()
    assert len(data) == len(audit_names)
    assert all(audit["name"] in audit_names for audit in data)


def test_delete_audit(client):
    # First, create an audit
    create_response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "This is a test audit"},
        headers={"X-API-Key": settings.api_key},
    )
    assert create_response.status_code == 200
    audit_id = create_response.json()["id"]

    # Now, delete the audit
    delete_response = client.delete(
        f"/audits/{audit_id}", headers={"X-API-Key": settings.api_key}
    )
    assert delete_response.status_code == 204

    # Try to get the deleted audit
    get_response = client.get(
        f"/audits/{audit_id}", headers={"X-API-Key": settings.api_key}
    )
    assert get_response.status_code == 404


def test_create_company_invalid_input(client):
    # First, create an audit
    create_audit_response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "This is a test audit"},
        headers={"X-API-Key": settings.api_key},
    )
    audit_id = create_audit_response.json()["id"]

    # Try to create a company with invalid input
    invalid_company_data = {
        "name": "Test Company",
        "size": "Invalid Size",
    }
    response = client.post(
        f"/audits/{audit_id}/company",
        json=invalid_company_data,
        headers={"X-API-Key": settings.api_key},
    )

    assert response.status_code == 422  # Unprocessable Entity
    assert "size" in response.json()["detail"][0]["loc"]


def test_create_company(client):
    # First, create an audit
    create_audit_response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "This is a test audit"},
        headers={"X-API-Key": settings.api_key},
    )
    assert create_audit_response.status_code == 200
    audit_id = create_audit_response.json()["id"]

    # Now, create a company for this audit
    company_data = {
        "name": "Test Company",
        "description": "A company for testing",
        "sector": "Technology",
        "size": "medium",
        "business_type": "B2B",
        "technology_stack": "Python, FastAPI, SQLAlchemy",
        "areas_of_focus": ["API Development", "Database Design"],
    }
    create_company_response = client.post(
        f"/audits/{audit_id}/company",
        json=company_data,
        headers={"X-API-Key": settings.api_key},
    )
    assert create_company_response.status_code == 200
    company = create_company_response.json()
    assert company["name"] == company_data["name"]
    assert company["description"] == company_data["description"]
    assert company["audit_id"] == audit_id
    assert company["size"] == company_data["size"]
    assert set(company["areas_of_focus"]) == set(company_data["areas_of_focus"])


def test_update_company(client):
    # First, create an audit and a company
    create_audit_response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "This is a test audit"},
        headers={"X-API-Key": settings.api_key},
    )
    audit_id = create_audit_response.json()["id"]

    initial_company_data = {
        "name": "Initial Company",
        "description": "Initial description",
        "sector": "Technology",
    }
    client.post(
        f"/audits/{audit_id}/company",
        json=initial_company_data,
        headers={"X-API-Key": settings.api_key},
    )

    # Now, update the company
    updated_company_data = {
        "name": "Updated Company",
        "description": "Updated description",
        "sector": "Finance",
    }
    update_response = client.put(
        f"/audits/{audit_id}/company",
        json=updated_company_data,
        headers={"X-API-Key": settings.api_key},
    )
    assert update_response.status_code == 200
    updated_company = update_response.json()
    assert updated_company["name"] == updated_company_data["name"]
    assert updated_company["description"] == updated_company_data["description"]
    assert updated_company["sector"] == updated_company_data["sector"]


def test_get_company(client):
    # First, create an audit and a company
    create_audit_response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "This is a test audit"},
        headers={"X-API-Key": settings.api_key},
    )
    audit_id = create_audit_response.json()["id"]

    company_data = {
        "name": "Test Company",
        "description": "A company for testing",
        "sector": "Technology",
    }
    client.post(
        f"/audits/{audit_id}/company",
        json=company_data,
        headers={"X-API-Key": settings.api_key},
    )

    # Now, get the company
    get_response = client.get(
        f"/audits/{audit_id}/company",
        headers={"X-API-Key": settings.api_key},
    )
    assert get_response.status_code == 200
    retrieved_company = get_response.json()
    assert retrieved_company["name"] == company_data["name"]
    assert retrieved_company["description"] == company_data["description"]
    assert retrieved_company["sector"] == company_data["sector"]


def test_upload_evidence_file(client):
    # First, create an audit
    create_audit_response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "This is a test audit"},
        headers={"X-API-Key": settings.api_key},
    )
    audit_id = create_audit_response.json()["id"]

    # Now, upload an evidence file
    file_content = b"This is a test file content"
    files = {"file": ("test_file.txt", file_content, "text/plain")}
    upload_response = client.post(
        f"/audits/{audit_id}/evidence-files",
        files=files,
        headers={"X-API-Key": settings.api_key},
    )
    assert upload_response.status_code == 200
    uploaded_file = upload_response.json()
    assert uploaded_file["filename"] == "test_file.txt"
    assert uploaded_file["file_type"] == "text/plain"
    assert uploaded_file["status"] == "pending"


def test_list_evidence_files(client):
    # First, create an audit and upload a file
    create_audit_response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "This is a test audit"},
        headers={"X-API-Key": settings.api_key},
    )
    audit_id = create_audit_response.json()["id"]

    files = {"file": ("test_file.txt", b"content", "text/plain")}
    client.post(
        f"/audits/{audit_id}/evidence-files",
        files=files,
        headers={"X-API-Key": settings.api_key},
    )

    # Now, list the evidence files
    list_response = client.get(
        f"/audits/{audit_id}/evidence-files",
        headers={"X-API-Key": settings.api_key},
    )
    assert list_response.status_code == 200
    file_list = list_response.json()
    assert len(file_list) == 1
    assert file_list[0]["filename"] == "test_file.txt"


def test_get_evidence_file(client):
    # First, create an audit and upload a file
    create_audit_response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "This is a test audit"},
        headers={"X-API-Key": settings.api_key},
    )
    audit_id = create_audit_response.json()["id"]

    files = {"file": ("test_file.txt", b"content", "text/plain")}
    upload_response = client.post(
        f"/audits/{audit_id}/evidence-files",
        files=files,
        headers={"X-API-Key": settings.api_key},
    )
    file_id = upload_response.json()["id"]

    # Now, get the evidence file
    get_response = client.get(
        f"/audits/{audit_id}/evidence-files/{file_id}",
        headers={"X-API-Key": settings.api_key},
    )
    assert get_response.status_code == 200
    file_data = get_response.json()
    assert file_data["filename"] == "test_file.txt"
    assert file_data["file_type"] == "text/plain"


def test_delete_evidence_file(client):
    # First, create an audit and upload a file
    create_audit_response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "This is a test audit"},
        headers={"X-API-Key": settings.api_key},
    )
    audit_id = create_audit_response.json()["id"]

    files = {"file": ("test_file.txt", b"content", "text/plain")}
    upload_response = client.post(
        f"/audits/{audit_id}/evidence-files",
        files=files,
        headers={"X-API-Key": settings.api_key},
    )
    file_id = upload_response.json()["id"]

    # Now, delete the evidence file
    delete_response = client.delete(
        f"/audits/{audit_id}/evidence-files/{file_id}",
        headers={"X-API-Key": settings.api_key},
    )
    assert delete_response.status_code == 204

    # Verify the file is deleted
    get_response = client.get(
        f"/audits/{audit_id}/evidence-files/{file_id}",
        headers={"X-API-Key": settings.api_key},
    )
    assert get_response.status_code == 404


def test_add_criteria(client):
    # First, create an audit
    create_audit_response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "This is a test audit"},
        headers={"X-API-Key": settings.api_key},
    )
    audit_id = create_audit_response.json()["id"]

    # Now, add criteria
    criteria_data = {
        "title": "Test Criteria",
        "description": "This is a test criteria",
        "parent_id": None,
        "maturity_definitions": {
            "novice": "Novice definition",
            "intermediate": "Intermediate definition",
            "advanced": "Advanced definition",
        },
    }
    add_criteria_response = client.post(
        f"/audits/{audit_id}/criteria",
        json=criteria_data,
        headers={"X-API-Key": settings.api_key},
    )
    assert add_criteria_response.status_code == 200
    added_criteria = add_criteria_response.json()
    assert added_criteria["title"] == criteria_data["title"]
    assert added_criteria["description"] == criteria_data["description"]


def test_list_criteria(client):
    # Create an audit (criteria will be automatically imported)
    create_audit_response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "This is a test audit"},
        headers={"X-API-Key": settings.api_key},
    )
    audit_id = create_audit_response.json()["id"]

    # Now, list the criteria
    list_criteria_response = client.get(
        f"/audits/{audit_id}/criteria",
        headers={"X-API-Key": settings.api_key},
    )
    assert list_criteria_response.status_code == 200
    criteria_list = list_criteria_response.json()

    # Update the expected number of criteria to 647
    assert len(criteria_list) == 647

    # Check if the first criteria in the list matches the expected structure
    first_criteria = criteria_list[0]
    assert "id" in first_criteria
    assert "title" in first_criteria
    assert "description" in first_criteria
    assert "maturity_definitions" in first_criteria


def test_select_criteria(client):
    # First, create an audit and add criteria
    create_audit_response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "This is a test audit"},
        headers={"X-API-Key": settings.api_key},
    )
    audit_id = create_audit_response.json()["id"]

    criteria_data = {
        "title": "Test Criteria",
        "description": "This is a test criteria",
        "parent_id": None,
        "maturity_definitions": {
            "novice": "Novice definition",
            "intermediate": "Intermediate definition",
            "advanced": "Advanced definition",
        },
    }
    add_criteria_response = client.post(
        f"/audits/{audit_id}/criteria",
        json=criteria_data,
        headers={"X-API-Key": settings.api_key},
    )
    criteria_id = add_criteria_response.json()["id"]

    # Now, select the criteria
    select_data = {
        "criteria_ids": [criteria_id],
        "expected_maturity_levels": {criteria_id: "intermediate"},
    }
    select_response = client.post(
        f"/audits/{audit_id}/criteria/selected",
        json=select_data,
        headers={"X-API-Key": settings.api_key},
    )
    assert select_response.status_code == 200
    selected_criteria = select_response.json()
    assert len(selected_criteria) == 1
    assert selected_criteria[0]["id"] == criteria_id
    assert selected_criteria[0]["selected"] == True
    assert selected_criteria[0]["expected_maturity_level"] == "intermediate"


def test_deselect_criteria(client):
    # First, create an audit, add criteria, and select it
    create_audit_response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "This is a test audit"},
        headers={"X-API-Key": settings.api_key},
    )
    audit_id = create_audit_response.json()["id"]

    criteria_data = {
        "title": "Test Criteria",
        "description": "This is a test criteria",
        "parent_id": None,
        "maturity_definitions": {
            "novice": "Novice definition",
            "intermediate": "Intermediate definition",
            "advanced": "Advanced definition",
        },
    }
    add_criteria_response = client.post(
        f"/audits/{audit_id}/criteria",
        json=criteria_data,
        headers={"X-API-Key": settings.api_key},
    )
    criteria_id = add_criteria_response.json()["id"]

    select_data = {
        "criteria_ids": [criteria_id],
        "expected_maturity_levels": {criteria_id: "intermediate"},
    }
    client.post(
        f"/audits/{audit_id}/criteria/selected",
        json=select_data,
        headers={"X-API-Key": settings.api_key},
    )

    # Now, deselect the criteria
    deselect_response = client.delete(
        f"/audits/{audit_id}/criteria/selected/{criteria_id}",
        headers={"X-API-Key": settings.api_key},
    )
    assert deselect_response.status_code == 204


def test_extract_evidence_for_criteria(client):
    # First, create an audit, add criteria, and upload an evidence file
    create_audit_response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "This is a test audit"},
        headers={"X-API-Key": settings.api_key},
    )
    audit_id = create_audit_response.json()["id"]

    criteria_data = {
        "title": "Test Criteria",
        "description": "This is a test criteria",
        "parent_id": None,
        "maturity_definitions": {
            "novice": "Novice definition",
            "intermediate": "Intermediate definition",
            "advanced": "Advanced definition",
        },
    }
    add_criteria_response = client.post(
        f"/audits/{audit_id}/criteria",
        json=criteria_data,
        headers={"X-API-Key": settings.api_key},
    )
    criteria_id = add_criteria_response.json()["id"]

    files = {"file": ("test_file.txt", b"content", "text/plain")}
    client.post(
        f"/audits/{audit_id}/evidence-files",
        files=files,
        headers={"X-API-Key": settings.api_key},
    )

    # Now, extract evidence for criteria
    extract_response = client.post(
        f"/audits/{audit_id}/criteria/{criteria_id}/actions/extract-evidence",
        headers={"X-API-Key": settings.api_key},
    )
    assert extract_response.status_code == 202
    assert "message" in extract_response.json()


def test_get_evidence_for_criteria(client):
    # First, create an audit, add criteria, upload an evidence file, and extract evidence
    create_audit_response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "This is a test audit"},
        headers={"X-API-Key": settings.api_key},
    )
    audit_id = create_audit_response.json()["id"]

    criteria_data = {
        "title": "Test Criteria",
        "description": "This is a test criteria",
        "parent_id": None,
        "maturity_definitions": {
            "novice": "Novice definition",
            "intermediate": "Intermediate definition",
            "advanced": "Advanced definition",
        },
    }
    add_criteria_response = client.post(
        f"/audits/{audit_id}/criteria",
        json=criteria_data,
        headers={"X-API-Key": settings.api_key},
    )
    criteria_id = add_criteria_response.json()["id"]

    files = {"file": ("test_file.txt", b"content", "text/plain")}
    client.post(
        f"/audits/{audit_id}/evidence-files",
        files=files,
        headers={"X-API-Key": settings.api_key},
    )

    client.post(
        f"/audits/{audit_id}/criteria/{criteria_id}/actions/extract-evidence",
        headers={"X-API-Key": settings.api_key},
    )

    # Now, get evidence for criteria
    get_evidence_response = client.get(
        f"/audits/{audit_id}/criteria/{criteria_id}/evidence",
        headers={"X-API-Key": settings.api_key},
    )
    assert get_evidence_response.status_code == 200
    evidence_list = get_evidence_response.json()
    assert len(evidence_list) > 0
    assert "content" in evidence_list[0]
    assert "source" in evidence_list[0]


def test_generate_questions(client):
    # First, create an audit and add criteria
    create_audit_response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "This is a test audit"},
        headers={"X-API-Key": settings.api_key},
    )
    audit_id = create_audit_response.json()["id"]

    criteria_data = {
        "title": "Test Criteria",
        "description": "This is a test criteria",
        "parent_id": None,
        "maturity_definitions": {
            "novice": "Novice definition",
            "intermediate": "Intermediate definition",
            "advanced": "Advanced definition",
        },
    }
    add_criteria_response = client.post(
        f"/audits/{audit_id}/criteria",
        json=criteria_data,
        headers={"X-API-Key": settings.api_key},
    )
    criteria_id = add_criteria_response.json()["id"]

    # Now, generate questions
    generate_questions_response = client.post(
        f"/audits/{audit_id}/criteria/{criteria_id}/questions",
        headers={"X-API-Key": settings.api_key},
    )

    assert generate_questions_response.status_code == 200
    questions = generate_questions_response.json()
    assert isinstance(questions, list)
    assert len(questions) > 0
    for question in questions:
        assert "id" in question
        assert "text" in question
        assert isinstance(question["text"], str)


def test_get_question_details(client):
    # First, create an audit, add criteria, and generate questions
    create_audit_response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "This is a test audit"},
        headers={"X-API-Key": settings.api_key},
    )
    audit_id = create_audit_response.json()["id"]

    criteria_data = {
        "title": "Test Criteria",
        "description": "This is a test criteria",
        "parent_id": None,
        "maturity_definitions": {
            "novice": "Novice definition",
            "intermediate": "Intermediate definition",
            "advanced": "Advanced definition",
        },
    }
    add_criteria_response = client.post(
        f"/audits/{audit_id}/criteria",
        json=criteria_data,
        headers={"X-API-Key": settings.api_key},
    )
    criteria_id = add_criteria_response.json()["id"]

    generate_questions_response = client.post(
        f"/audits/{audit_id}/criteria/{criteria_id}/questions",
        headers={"X-API-Key": settings.api_key},
    )
    question_id = generate_questions_response.json()[0]["id"]

    # Now, get question details
    get_question_response = client.get(
        f"/audits/{audit_id}/questions/{question_id}",
        headers={"X-API-Key": settings.api_key},
    )
    assert get_question_response.status_code == 200
    question_details = get_question_response.json()
    assert question_details["id"] == question_id
    assert "text" in question_details
    assert "created_at" in question_details


def test_submit_answer(client):
    # First, create an audit, add criteria, and generate questions
    create_audit_response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "This is a test audit"},
        headers={"X-API-Key": settings.api_key},
    )
    audit_id = create_audit_response.json()["id"]

    criteria_data = {
        "title": "Test Criteria",
        "description": "This is a test criteria",
        "parent_id": None,
        "maturity_definitions": {
            "novice": "Novice definition",
            "intermediate": "Intermediate definition",
            "advanced": "Advanced definition",
        },
    }
    add_criteria_response = client.post(
        f"/audits/{audit_id}/criteria",
        json=criteria_data,
        headers={"X-API-Key": settings.api_key},
    )
    criteria_id = add_criteria_response.json()["id"]

    generate_questions_response = client.post(
        f"/audits/{audit_id}/criteria/{criteria_id}/questions",
        headers={"X-API-Key": settings.api_key},
    )
    question_id = generate_questions_response.json()[0]["id"]

    # Now, submit an answer
    answer_data = {"text": "This is a test answer", "submitted_by": "Test User"}
    submit_answer_response = client.post(
        f"/audits/{audit_id}/questions/{question_id}/answers",
        json=answer_data,
        headers={"X-API-Key": settings.api_key},
    )
    assert submit_answer_response.status_code == 200
    submitted_answer = submit_answer_response.json()
    assert submitted_answer["text"] == answer_data["text"]
    assert submitted_answer["submitted_by"] == answer_data["submitted_by"]


def test_get_unanswered_questions(client):
    # First, create an audit, add criteria, and generate questions
    create_audit_response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "This is a test audit"},
        headers={"X-API-Key": settings.api_key},
    )
    audit_id = create_audit_response.json()["id"]

    criteria_data = {
        "title": "Test Criteria",
        "description": "This is a test criteria",
        "parent_id": None,
        "maturity_definitions": {
            "novice": "Novice definition",
            "intermediate": "Intermediate definition",
            "advanced": "Advanced definition",
        },
    }
    add_criteria_response = client.post(
        f"/audits/{audit_id}/criteria",
        json=criteria_data,
        headers={"X-API-Key": settings.api_key},
    )
    criteria_id = add_criteria_response.json()["id"]

    client.post(
        f"/audits/{audit_id}/criteria/{criteria_id}/questions",
        headers={"X-API-Key": settings.api_key},
    )

    # Now, get unanswered questions
    get_unanswered_response = client.get(
        f"/audits/{audit_id}/questions/unanswered",
        headers={"X-API-Key": settings.api_key},
    )
    assert get_unanswered_response.status_code == 200
    unanswered_questions = get_unanswered_response.json()
    assert len(unanswered_questions) > 0
    assert all(not question.get("answered", False) for question in unanswered_questions)


def test_get_all_questions(client):
    # First, create an audit, add criteria, and generate questions
    create_audit_response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "This is a test audit"},
        headers={"X-API-Key": settings.api_key},
    )
    audit_id = create_audit_response.json()["id"]

    criteria_data = {
        "title": "Test Criteria",
        "description": "This is a test criteria",
        "parent_id": None,
        "maturity_definitions": {
            "novice": "Novice definition",
            "intermediate": "Intermediate definition",
            "advanced": "Advanced definition",
        },
    }
    add_criteria_response = client.post(
        f"/audits/{audit_id}/criteria",
        json=criteria_data,
        headers={"X-API-Key": settings.api_key},
    )
    criteria_id = add_criteria_response.json()["id"]

    client.post(
        f"/audits/{audit_id}/criteria/{criteria_id}/questions",
        headers={"X-API-Key": settings.api_key},
    )

    # Now, get all questions
    get_all_questions_response = client.get(
        f"/audits/{audit_id}/questions",
        headers={"X-API-Key": settings.api_key},
    )
    assert get_all_questions_response.status_code == 200
    all_questions = get_all_questions_response.json()
    assert len(all_questions) > 0
    assert all("id" in question and "text" in question for question in all_questions)


def test_get_answers_for_question(client):
    # First, create an audit, add criteria, generate questions, and submit an answer
    create_audit_response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "This is a test audit"},
        headers={"X-API-Key": settings.api_key},
    )
    audit_id = create_audit_response.json()["id"]

    criteria_data = {
        "title": "Test Criteria",
        "description": "This is a test criteria",
        "parent_id": None,
        "maturity_definitions": {
            "novice": "Novice definition",
            "intermediate": "Intermediate definition",
            "advanced": "Advanced definition",
        },
    }
    add_criteria_response = client.post(
        f"/audits/{audit_id}/criteria",
        json=criteria_data,
        headers={"X-API-Key": settings.api_key},
    )
    criteria_id = add_criteria_response.json()["id"]

    generate_questions_response = client.post(
        f"/audits/{audit_id}/criteria/{criteria_id}/questions",
        headers={"X-API-Key": settings.api_key},
    )
    question_id = generate_questions_response.json()[0]["id"]

    answer_data = {"text": "This is a test answer", "submitted_by": "Test User"}
    client.post(
        f"/audits/{audit_id}/questions/{question_id}/answers",
        json=answer_data,
        headers={"X-API-Key": settings.api_key},
    )

    # Now, get answers for the question
    get_answers_response = client.get(
        f"/audits/{audit_id}/questions/{question_id}/answers",
        headers={"X-API-Key": settings.api_key},
    )
    assert get_answers_response.status_code == 200
    answers = get_answers_response.json()
    assert len(answers) > 0
    assert answers[0]["text"] == answer_data["text"]
    assert answers[0]["submitted_by"] == answer_data["submitted_by"]


def test_get_answer_details(client):
    # First, create an audit, add criteria, generate questions, and submit an answer
    create_audit_response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "This is a test audit"},
        headers={"X-API-Key": settings.api_key},
    )
    audit_id = create_audit_response.json()["id"]


def test_parse_evidence_for_company(client):
    # Create an audit and a company
    create_audit_response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "Testing parse evidence"},
        headers={"X-API-Key": settings.api_key},
    )
    audit_id = create_audit_response.json()["id"]

    company_data = {
        "name": "Original Company",
        "description": "Original description",
        "sector": "Technology",
    }
    client.post(
        f"/audits/{audit_id}/company",
        json=company_data,
        headers={"X-API-Key": settings.api_key},
    )

    # Upload an evidence file
    files = {"file": ("evidence.txt", b"Evidence content", "text/plain")}
    client.post(
        f"/audits/{audit_id}/evidence-files",
        files=files,
        headers={"X-API-Key": settings.api_key},
    )

    # Parse evidence for company
    parse_response = client.post(
        f"/audits/{audit_id}/company/actions/parse-evidence",
        headers={"X-API-Key": settings.api_key},
    )
    assert parse_response.status_code == 200
    assert (
        parse_response.json()["message"]
        == "Evidence parsed and company information updated"
    )

    # Retrieve updated company information
    get_company_response = client.get(
        f"/audits/{audit_id}/company",
        headers={"X-API-Key": settings.api_key},
    )
    updated_company = get_company_response.json()
    assert updated_company["name"] == "Parsed Company Name"
    assert updated_company["description"] == "Description extracted from evidence"


def test_get_evidence_file_content(client, test_db):
    _, TestingSessionLocal = test_db  # Unpack TestingSessionLocal

    # Create an audit and upload a file
    create_audit_response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "Testing file content"},
        headers={"X-API-Key": settings.api_key},
    )
    audit_id = create_audit_response.json()["id"]

    file_content = b"Test file content for retrieval"
    files = {"file": ("test_file.txt", file_content, "text/plain")}
    upload_response = client.post(
        f"/audits/{audit_id}/evidence-files",
        files=files,
        headers={"X-API-Key": settings.api_key},
    )
    file_id = upload_response.json()["id"]

    # Manually set the file status to 'processed'
    with TestingSessionLocal() as db:
        db_file = db.query(EvidenceFileDB).filter(EvidenceFileDB.id == file_id).first()
        db_file.status = "processed"
        db.commit()

    # Retrieve the file content
    get_content_response = client.get(
        f"/audits/{audit_id}/evidence-files/{file_id}/content",
        headers={"X-API-Key": settings.api_key},
    )
    assert get_content_response.status_code == 200
    assert get_content_response.content == file_content


def test_preselect_criteria(client):
    # Create an audit and add multiple criteria
    create_audit_response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "Testing preselect criteria"},
        headers={"X-API-Key": settings.api_key},
    )
    audit_id = create_audit_response.json()["id"]

    criteria_ids = []
    for i in range(10):
        criteria_data = {
            "title": f"Criteria {i}",
            "description": f"Description {i}",
            "maturity_definitions": {
                "novice": "Novice level",
                "intermediate": "Intermediate level",
                "advanced": "Advanced level",
            },
        }
        response = client.post(
            f"/audits/{audit_id}/criteria",
            json=criteria_data,
            headers={"X-API-Key": settings.api_key},
        )
        criteria_ids.append(response.json()["id"])

    # Preselect criteria
    preselect_response = client.post(
        f"/audits/{audit_id}/criteria/selected/actions/preselect",
        headers={"X-API-Key": settings.api_key},
    )
    assert preselect_response.status_code == 200
    preselected_criteria = preselect_response.json()
    assert len(preselected_criteria) == 5
    for criteria in preselected_criteria:
        assert criteria["selected"] is True
        assert criteria["expected_maturity_level"] == "intermediate"


def test_update_existing_criteria(client):
    # Create an audit and add criteria
    create_audit_response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "Testing update criteria"},
        headers={"X-API-Key": settings.api_key},
    )
    audit_id = create_audit_response.json()["id"]

    criteria_data = {
        "title": "Original Criteria",
        "description": "Original description",
        "maturity_definitions": {
            "novice": "Original novice",
            "intermediate": "Original intermediate",
            "advanced": "Original advanced",
        },
    }
    add_response = client.post(
        f"/audits/{audit_id}/criteria",
        json=criteria_data,
        headers={"X-API-Key": settings.api_key},
    )
    criteria_id = add_response.json()["id"]

    # Update the criteria
    updated_criteria_data = {
        "title": "Updated Criteria",
        "description": "Updated description",
        "maturity_definitions": {
            "novice": "Updated novice",
            "intermediate": "Updated intermediate",
            "advanced": "Updated advanced",
        },
    }
    update_response = client.put(
        f"/audits/{audit_id}/criteria/{criteria_id}",
        json=updated_criteria_data,
        headers={"X-API-Key": settings.api_key},
    )
    assert update_response.status_code == 200
    updated_criteria = update_response.json()
    assert updated_criteria["title"] == "Updated Criteria"
    assert updated_criteria["description"] == "Updated description"
    assert (
        updated_criteria["maturity_definitions"]
        == updated_criteria_data["maturity_definitions"]
    )


def test_get_answer_details(client):
    # Create an audit, criteria, question, and submit an answer
    create_audit_response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "Testing get answer details"},
        headers={"X-API-Key": settings.api_key},
    )
    audit_id = create_audit_response.json()["id"]

    criteria_data = {
        "title": "Test Criteria",
        "description": "Criteria for testing answers",
        "maturity_definitions": {
            "novice": "Novice level",
            "intermediate": "Intermediate level",
            "advanced": "Advanced level",
        },
    }
    criteria_response = client.post(
        f"/audits/{audit_id}/criteria",
        json=criteria_data,
        headers={"X-API-Key": settings.api_key},
    )
    criteria_id = criteria_response.json()["id"]

    # Generate a question
    question_response = client.post(
        f"/audits/{audit_id}/criteria/{criteria_id}/questions",
        headers={"X-API-Key": settings.api_key},
    )
    question_id = question_response.json()[0]["id"]

    # Submit an answer
    answer_data = {"text": "Test Answer", "submitted_by": "Tester"}
    answer_response = client.post(
        f"/audits/{audit_id}/questions/{question_id}/answers",
        json=answer_data,
        headers={"X-API-Key": settings.api_key},
    )
    answer_id = answer_response.json()["id"]

    # Get answer details
    get_answer_response = client.get(
        f"/audits/{audit_id}/questions/{question_id}/answers/{answer_id}",
        headers={"X-API-Key": settings.api_key},
    )
    assert get_answer_response.status_code == 200
    answer_details = get_answer_response.json()
    assert answer_details["id"] == answer_id
    assert answer_details["text"] == "Test Answer"
    assert answer_details["submitted_by"] == "Tester"


def test_set_and_get_maturity_assessment(client):
    # Create an audit and criteria
    create_audit_response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "Testing maturity assessment"},
        headers={"X-API-Key": settings.api_key},
    )
    audit_id = create_audit_response.json()["id"]

    criteria_data = {
        "title": "Maturity Criteria",
        "description": "Criteria for maturity testing",
        "maturity_definitions": {
            "novice": "Novice level",
            "intermediate": "Intermediate level",
            "advanced": "Advanced level",
        },
    }
    criteria_response = client.post(
        f"/audits/{audit_id}/criteria",
        json=criteria_data,
        headers={"X-API-Key": settings.api_key},
    )
    criteria_id = criteria_response.json()["id"]

    # Set maturity assessment
    assessment_data = {
        "maturity_level": "advanced",
        "comments": "Excellent performance",
    }
    set_response = client.post(
        f"/audits/{audit_id}/criteria/{criteria_id}/maturity",
        json=assessment_data,
        headers={"X-API-Key": settings.api_key},
    )
    assert set_response.status_code == 200
    assessment = set_response.json()
    assert assessment["maturity_level"] == "advanced"
    assert assessment["comments"] == "Excellent performance"

    # Get maturity assessment
    get_response = client.get(
        f"/audits/{audit_id}/criteria/{criteria_id}/maturity",
        headers={"X-API-Key": settings.api_key},
    )
    assert get_response.status_code == 200
    retrieved_assessment = get_response.json()
    assert retrieved_assessment["maturity_level"] == "advanced"
    assert retrieved_assessment["comments"] == "Excellent performance"


def test_get_all_maturity_assessments(client):
    # Create an audit and multiple criteria with assessments
    create_audit_response = client.post(
        "/audits",
        json={"name": "Test Audit", "description": "Testing all assessments"},
        headers={"X-API-Key": settings.api_key},
    )
    audit_id = create_audit_response.json()["id"]

    criteria_ids = []
    for i in range(3):
        criteria_data = {
            "title": f"Criteria {i}",
            "description": f"Description {i}",
            "maturity_definitions": {
                "novice": "Novice level",
                "intermediate": "Intermediate level",
                "advanced": "Advanced level",
            },
        }
        response = client.post(
            f"/audits/{audit_id}/criteria",
            json=criteria_data,
            headers={"X-API-Key": settings.api_key},
        )
        criteria_ids.append(response.json()["id"])

    # Set maturity assessments
    for idx, criteria_id in enumerate(criteria_ids):
        assessment_data = {
            "maturity_level": "intermediate",
            "comments": f"Assessment {idx}",
        }
        client.post(
            f"/audits/{audit_id}/criteria/{criteria_id}/maturity",
            json=assessment_data,
            headers={"X-API-Key": settings.api_key},
        )

    # Get all maturity assessments
    get_assessments_response = client.get(
        f"/audits/{audit_id}/assessments",
        headers={"X-API-Key": settings.api_key},
    )
    assert get_assessments_response.status_code == 200
    assessments = get_assessments_response.json()
    assert len(assessments) == 3
    for idx, assessment in enumerate(assessments):
        assert assessment["maturity_level"] == "intermediate"
        assert assessment["comments"] == f"Assessment {idx}"
