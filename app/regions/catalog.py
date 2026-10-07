"""Deployment region catalog; country law remains in the policy engine."""
from dataclasses import dataclass


@dataclass(frozen=True)
class RegionDefinition:
    region_id: str
    region_group: str
    country_code: str
    timezone: str
    language: str
    data_residency_region: str
    compliance_profile: dict
    allowed_channels: tuple[str, ...]


_EU_COUNTRIES = frozenset("AT BE BG HR CY CZ DK EE FI FR DE GR HU IS IE IT LV LI LT LU MT NL NO PL PT RO SK SI ES SE CH".split())
_AFRICA_COUNTRIES = frozenset("DZ AO BJ BW BF BI CV CM CF TD KM CG CD CI DJ EG GQ ER SZ ET GA GM GH GN GW KE LS LR LY MG MW ML MR MU MA MZ NA NE NG RW ST SN SC SL SO ZA SS SD TZ TG TN UG EH ZM ZW".split())
_DEFINITIONS = {
    "us-east-1": RegionDefinition("us-east-1", "US", "US", "America/New_York", "en-US",
        "us-east-1", {"frameworks": ["TCPA", "DNC"], "state_law_rules_supported": True,
                      "call_recording_consent_required": True},
        ("sms", "email", "whatsapp", "call", "chat")),
    "us-west-2": RegionDefinition("us-west-2", "US", "US", "America/Los_Angeles", "en-US",
        "us-west-2", {"frameworks": ["TCPA", "DNC"], "state_law_rules_supported": True,
                      "call_recording_consent_required": True},
        ("sms", "email", "whatsapp", "call", "chat")),
    "ca-central-1": RegionDefinition("ca-central-1", "CA", "CA", "America/Toronto", "en-CA",
        "ca-central-1", {"frameworks": ["CASL"], "opt_out_required": True},
        ("sms", "email", "whatsapp", "call", "chat")),
    "eu-west-1": RegionDefinition("eu-west-1", "EU", "DE", "Europe/Berlin", "en-GB",
        "eu-west-1", {"frameworks": ["GDPR", "PECR"], "lawful_basis_provenance_required": True,
                      "data_minimization": True, "right_to_deletion": True},
        ("sms", "email", "whatsapp", "call", "chat")),
    "af-south-1": RegionDefinition("af-south-1", "AFRICA", "ZA", "Africa/Johannesburg", "en-ZA",
        "af-south-1", {"country_specific_policy_required": True, "safe_mode": True},
        ("sms", "email", "call", "chat")),
    "af-east-1": RegionDefinition("af-east-1", "AFRICA", "KE", "Africa/Nairobi", "en-KE",
        "af-east-1", {"country_specific_policy_required": True, "safe_mode": True},
        ("sms", "email", "call", "chat")),
}


def list_regions() -> list[RegionDefinition]:
    return list(_DEFINITIONS.values())


def get_region(region_id: str) -> RegionDefinition:
    try:
        return _DEFINITIONS[region_id]
    except KeyError:
        raise ValueError("unsupported deployment region") from None


def validate_country(region_id: str, country_code: str) -> str:
    definition = get_region(region_id)
    code = country_code.strip().upper()
    if (definition.region_group == "US" and code != "US"
            or definition.region_group == "CA" and code != "CA"
            or definition.region_group == "EU" and code not in _EU_COUNTRIES
            or definition.region_group == "AFRICA" and code not in _AFRICA_COUNTRIES):
        raise ValueError("country is not supported by the selected deployment region")
    return code
