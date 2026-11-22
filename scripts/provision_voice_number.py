"""Provision a DB voice route; no Twilio API calls are made."""
import argparse, re
from sqlmodel import select
from app.persistence.session import session_factory
from app.persistence.models import Tenant, Business, VoiceNumberRoute
E164 = re.compile(r"^\+[1-9][0-9]{6,14}$")
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--number", required=True); p.add_argument("--tenant-id", required=True); p.add_argument("--business-id", required=True)
    p.add_argument("--reassign", action="store_true", help="Allow reassignment if number is owned by another scope")
    a=p.parse_args()
    if not E164.fullmatch(a.number): p.error("Number must be E.164")
    with session_factory() as s:
        if not s.get(Tenant,a.tenant_id): p.error("Tenant not found")
        b=s.exec(select(Business).where(Business.id==a.business_id,Business.tenant_id==a.tenant_id)).first()
        if not b: p.error("Business does not belong to selected tenant")
        r=s.exec(select(VoiceNumberRoute).where(VoiceNumberRoute.phone_number==a.number)).first()
        if r and (r.tenant_id!=a.tenant_id or r.business_id!=a.business_id) and not a.reassign: p.error("Number assigned elsewhere; use --reassign to confirm")
        if r: r.tenant_id,r.business_id,r.enabled=a.tenant_id,a.business_id,True
        else: s.add(VoiceNumberRoute(phone_number=a.number,tenant_id=a.tenant_id,business_id=a.business_id))
        s.commit()
    print("Voice route provisioned (number hidden from output).")
if __name__=="__main__": main()
