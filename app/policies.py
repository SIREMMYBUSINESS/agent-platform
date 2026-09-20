class PolicyChecker:
    def __init__(self):
        self.region_rules = {
            "US": {"sms": "requires_consent", "call": "recording_notice_required"},
            "CA": {"email": "casl_consent_required"},
            "EU": {"email": "gdpr_consent_required", "sms": "pecr_consent_required"},
            "AFRICA": {"default": "country_specific_rules_required"}
        }

    def validate(self, tenant_id: str, channel: str, region: str, consent_status: str):
        rules = self.region_rules.get(region, self.region_rules["AFRICA"])
        if channel in rules:
            if consent_status != "granted":
                return False, f"{channel} requires consent in {region}"
        return True, "ok"
