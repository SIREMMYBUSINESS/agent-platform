import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select
from app.persistence.models import Tenant, Business, VoiceNumberRoute
from app.services.voice_number_lookup_service import VoiceNumberLookupService
@pytest.fixture
def db():
    e=create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool)
    SQLModel.metadata.create_all(e); s=Session(e)
    s.add(Tenant(id="t",name="Tenant")); s.commit()
    s.add(Business(id="b",tenant_id="t",name="Business",vertical="general")); s.commit()
    yield s
    s.close(); e.dispose()
def test_exact_number_and_disabled_route(db):
    db.add(VoiceNumberRoute(phone_number="+15555550123",tenant_id="t",business_id="b")); db.commit()
    service=VoiceNumberLookupService(db)
    assert service.resolve(dialed_number="+15555550123")=={"tenant_id":"t","business_id":"b"}
    r=db.exec(select(VoiceNumberRoute)).one(); r.enabled=False; db.commit()
    assert service.resolve(dialed_number="+15555550123") is None
def test_invalid_number(db):
    with pytest.raises(ValueError): VoiceNumberLookupService(db).resolve(dialed_number="invalid")
