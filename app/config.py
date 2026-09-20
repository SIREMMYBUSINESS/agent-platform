from pydantic import BaseModel, Field

class Settings(BaseModel):
    app_name: str = "Receptionist & Outreach Agent Platform"
    environment: str = "development"
    log_level: str = "INFO"
    default_region: str = "US"

    database_url: str = "postgresql://postgres:postgres@localhost:5432/agent_platform"
    redis_url: str = "redis://localhost:6379/0"

settings = Settings()
