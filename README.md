# Continuous Insight API

Continuous Insight API is a FastAPI-based backend service that powers the technical and product audit system. This API enables organisations to:

- Conduct comprehensive technical and product maturity assessments
- Generate data-driven insights through AI-powered analysis
- Track and measure improvement over time
- Manage evidence collection and validation systematically

Built with a robust Python-based architecture, it provides RESTful endpoints for managing company audits, evidence processing, and maturity assessments.

## 🎯 Core Features

- **Authentication & Authorization**
  - Google OAuth integration
  - Role-based access control (RBAC)
  - JWT token-based session management
  
- **Company Management**
  - Company profile creation and management
  - User-company associations
  - Multi-tenant architecture
  
- **Audit Framework**
  - Comprehensive audit lifecycle management
  - Maturity criteria definition and assessment
  - Evidence collection and processing
  - Dynamic question generation
  
- **AI Integration**
  - OpenAI-powered analysis with GPT-4 capabilities
  - Automated evidence processing and classification
  - Intelligent data extraction from multiple file formats
  - Context-aware assessment support with historical learning
  - Automated recommendation generation

## 🚀 Getting Started

### Prerequisites

- Python 3.10 or higher
- SQLite (development) / PostgreSQL (production)
- OpenAI API access
- Google OAuth credentials

### Installation

1. Clone the repository:

```bash
git clone [repository-url]
cd continuous-insight-api
```

2. Create and activate a virtual environment:

```bash
python -m venv venv
source venv/bin/activate  # Unix
# or
.\venv\Scripts\activate  # Windows
```

3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Create a `.env` file in the root directory:

```env
# Database
DATABASE_URL=sqlite:///./database/tech_audit.db

# Authentication
JWT_SECRET_KEY=your_secret_key
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret

# OpenAI
OPENAI_API_KEY=your_openai_api_key

# Application Settings
ENVIRONMENT=development
ALLOWED_ORIGINS=http://localhost:3000
```

5. Initialize the database:

```bash
python init_db.py
```

6. Start the development server:

```bash
uvicorn main:app --reload
```

## 🏗️ Project Structure

```
continuous-insight-api/
├── main.py                 # Application entry point
├── config.py              # Configuration settings
├── database.py            # Database connection management
├── middleware.py          # CORS and authentication middleware
├── endpoints/             # API route handlers
│   ├── auth_endpoints.py
│   ├── company_endpoints.py
│   ├── audit_endpoints.py
│   └── ...
├── db_models/            # SQLAlchemy models
│   ├── user.py
│   ├── company.py
│   ├── audit.py
│   └── ...
├── pydantic_models/      # Request/Response models
├── helpers/              # Utility functions
└── tests/               # Test suite
```

## 🔑 Core Models

### User Roles

- Global Administrator
- Auditor
- Organisation Lead
- Organisation User
- Delegated User
- Observer roles

### Maturity Levels

- Novice
- Intermediate
- Advanced

## 📡 API Endpoints

### Companies

- `POST /companies` - Create company
- `GET /companies` - List companies
- `GET /companies/{id}` - Get company details
- `PUT /companies/{id}` - Update company
- `DELETE /companies/{id}` - Delete company

### Audits

- `POST /audits` - Create audit
- `GET /audits` - List audits
- `GET /audits/{id}` - Get audit details
- `PUT /audits/{id}` - Update audit
- `DELETE /audits/{id}` - Delete audit

### Evidence

- `POST /audits/{id}/evidence-files` - Upload evidence
- `GET /audits/{id}/evidence-files` - List evidence files
- `POST /companies/{id}/evidence` - Process evidence

## 🚢 Deployment

### Production Setup

1. Configure production database:

```bash
export DATABASE_URL=your_production_db_url
```

2. Set up environment variables:

```bash
export ENVIRONMENT=production
export ALLOWED_ORIGINS=https://your-frontend-domain.com
```

3. Run migrations:

```bash
alembic upgrade head
```

4. Start the production server:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

### Docker Deployment

1. Build the image:

```bash
docker build -t continuous-insight-api .
```

2. Run the container:

```bash
docker run -p 8000:8000 \
  --env-file .env \
  continuous-insight-api
```

## 🔍 Health Monitoring

The API includes a health check endpoint at `/health` that returns:

- Database connection status
- OpenAI API status
- System uptime
- Resource usage

## 🧪 Testing

Run the test suite:

```bash
pytest
```

Run with coverage:

```bash
pytest --cov=app tests/
```

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to the branch
5. Create a Pull Request

### Development Guidelines

- Follow PEP 8 style guide
- Write unit tests for new features
- Update documentation for API changes
- Validate role-based access control
- Test evidence processing workflows

## 💡 Support

For support or inquiries:

- Email: <hello@rational.partners>

## 🔒 Security

- All endpoints are protected with JWT authentication
- Role-based access control (RBAC) for fine-grained permissions
- Data encryption at rest and in transit
- Regular security audits and penetration testing
- Rate limiting and brute force protection
- Secure file handling and validation

Report security vulnerabilities to <security@rational.partners>

## 📚 API Documentation

Interactive API documentation is available at:

- Swagger UI: `/docs`
- ReDoc: `/redoc`

The API follows RESTful principles and includes:

- Comprehensive endpoint descriptions
- Request/response examples
- Authentication details
- Schema definitions

## 🔧 Environment Variables

### Database

DATABASE_URL=sqlite:///./database/tech_audit.db
DATABASE_POOL_SIZE=20
DATABASE_MAX_OVERFLOW=10

### Authentication

JWT_SECRET_KEY=your_secret_key
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret

### OpenAI

OPENAI_API_KEY=your_openai_api_key
OPENAI_MODEL=gpt-4
OPENAI_MAX_TOKENS=2000

### Application Settings

ENVIRONMENT=development
ALLOWED_ORIGINS=<http://localhost:3000>
LOG_LEVEL=INFO
WORKER_PROCESSES=4

## 📊 Performance Monitoring

The API includes comprehensive monitoring:

- Prometheus metrics at `/metrics`
- Health check endpoint at `/health`
- Detailed logging with correlation IDs
- Performance tracing with OpenTelemetry
- Resource usage monitoring
- Error rate tracking
- Response time metrics
