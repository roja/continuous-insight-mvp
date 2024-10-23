import secrets
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from fastapi import FastAPI

def setup_middleware(app: FastAPI) -> None:
    """Setup all middleware for the application."""
    
    # CORS configuration
    origins = [
        "http://localhost:3000",  # React app
        "http://127.0.0.1:3000",  # Alternate localhost
        # Add other origins if necessary
    ]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Session middleware
    app.add_middleware(
        SessionMiddleware, 
        secret_key=secrets.token_urlsafe(32)
    )
