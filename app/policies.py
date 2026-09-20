from __future__ import annotations

from typing import Tuple

class PolicyChecker:
    REGION_RULES = {
        "US": {
            "sms": "requires_consent",
            "call": "recording_notice_required",
            "email": "requires_consent",
        },
        "CA": {
            "email": "casl_consent_required",
            "sms": "consent_required",
        },
        "EU": {
            "email": "gdpr_consent_required",
            "sms": "pecr_consent_required",
            "chat": "consent_record_required",
        },
        "AFRICA": {
            "default": "country_specific_rules_required",
        },
    }

    def validate(self, region: str, channel: str, consent_status: str) -> Tuple[bool, str]:
        rules = self.REGION_RULES.get(region, self.REGION_RULES["AFRICA"])
        rule = rules.get(channel, rules.get("default"))

        if rule is None:
            return True, "ok"

        if consent_status != "granted":
            return False, f"{channel} requires consent in {region}: {rule}"

        return True, "ok"
