# Standard library imports
from typing import List

# Third-party imports
from fastapi import FastAPI

# Local imports
from db_models import Base
from database import engine
from config import settings
from middleware import setup_middleware
from helpers import init_openai_client
from endpoints import (
    auth_endpoints,
    company_endpoints,
    user_endpoints,
    evidence_files_endpoints,
    criteria_endpoints,
    questions_endpoints,
    maturity_endpoints,
    audit_endpoints,
)


def create_app() -> FastAPI:
    # Create database tables
    Base.metadata.create_all(bind=engine)

    # Initialize FastAPI app
    app = FastAPI(
        title="Continuous Insight API",
        description="API for managing technical and product audits",
        version="1.0.0",
    )

    # Setup middleware
    setup_middleware(app)

    # Initialize OpenAI client
    init_openai_client(settings.openai_api_key)

    # Register routers
    routers = [
        auth_endpoints.router,
        company_endpoints.router,
        user_endpoints.router,
        evidence_files_endpoints.router,
        criteria_endpoints.router,
        questions_endpoints.router,
        maturity_endpoints.router,
        audit_endpoints.router,
    ]

    for router in routers:
        app.include_router(router)

    return app


# Create the application instance
app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
