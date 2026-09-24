import os


# These test-process settings keep the module-level Settings instance isolated
# from any developer-provided environment values or repository-local .env file.
os.environ.update(
    {
        "APP_NAME": "Receptionist + Outreach Agent Platform",
        "ENVIRONMENT": "development",
        "LOG_LEVEL": "INFO",
        "DEFAULT_REGION": "US",
        "INBOUND_TENANT_ID": "tenant-1",
        "DATABASE_URL": "postgresql://localhost:5432/agent_platform",
        "REDIS_URL": "redis://localhost:6379/0",
    }
)
