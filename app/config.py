from pydantic import BaseModel, Field

class Settings(BaseModel):
    app_name: str = "Receptionist & Outreach Agent Platform"
    environment: str = "development"
    log_level: str = "INFO"
    default_region: str = "US"

settings = Settings()
