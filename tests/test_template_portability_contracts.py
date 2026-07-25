import json
from pathlib import Path

import pytest
from jsonschema import validate
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError

from app.api_route_audit import AccessTier, audit_cases


def _structured_bundle(section_orders: list[int]) -> dict:
    keys = ["problem", "history", "tasks"]
    return {
        "format": "openscribe-template-bundle",
        "format_version": 1,
        "templates": [
            {
                "name": "Portable structured template",
                "description": None,
                "latest_version": {
                    "mode": "structured",
                    "prompt_text": "Write an assessment.",
                    "config_json": {
                        "profile": "emis",
                        "sections": [
                            {
                                "section_key": keys[index],
                                "instruction": "Write this section.",
                                "section_order": order,
                            }
                            for index, order in enumerate(section_orders)
                        ],
                    },
                },
            }
        ],
    }


def test_public_schema_requires_consecutive_section_orders_in_array_order():
    schema = json.loads(Path("app/static/schemas/openscribe-template-bundle-v1.schema.json").read_text(encoding="utf-8"))

    validate(_structured_bundle([1, 2, 3]), schema)
    for invalid_orders in ([2], [2, 1], [1, 3]):
        with pytest.raises(JsonSchemaValidationError):
            validate(_structured_bundle(invalid_orders), schema)


def test_public_schema_rejects_duplicate_structured_section_keys():
    schema = json.loads(Path("app/static/schemas/openscribe-template-bundle-v1.schema.json").read_text(encoding="utf-8"))
    bundle = _structured_bundle([1, 2, 3])
    sections = bundle["templates"][0]["latest_version"]["config_json"]["sections"]
    sections[1]["section_key"] = sections[0]["section_key"]

    with pytest.raises(JsonSchemaValidationError):
        validate(bundle, schema)


def test_negative_audit_includes_every_template_portability_route_as_full_access():
    cases = {(case.method, case.path): case for case in audit_cases()}

    for method, path in (
        ("POST", "/api/v1/templates/export"),
        ("POST", "/api/v1/templates/import/preflight"),
        ("POST", "/api/v1/templates/import"),
    ):
        assert cases[(method, path)].access_tier is AccessTier.full
