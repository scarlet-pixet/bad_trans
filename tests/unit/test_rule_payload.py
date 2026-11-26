from google.protobuf.json_format import MessageToDict, ParseDict

from backend.generated.bank_pb2 import PublishRuleVersionRequest


def test_typed_publication_restores_integer_json_fields_without_rounding(monkeypatch):
    monkeypatch.setenv("SERVICE_NAME", "analysis")
    monkeypatch.setenv("SERVICE_TOKEN", "test-only")
    monkeypatch.setenv("DATABASE_URL", "dbname=unused")
    from backend.infrastructure.server import decode_rule_input

    request = ParseDict({
        "input": {
            "parameters": {"min_samples": 20, "min_active_days": 7.5, "enabled": True, "mad_scale": "1.4826"},
            "controlExamples": [{"count": 0, "expected": False}, {"count": 1.5, "expected": True}],
        },
    }, PublishRuleVersionRequest())
    payload = MessageToDict(request)["input"]
    decode_rule_input(payload)
    assert type(payload["parameters"]["min_samples"]) is int
    assert payload["parameters"]["min_active_days"] == 7.5
    assert payload["parameters"]["enabled"] is True
    assert payload["parameters"]["mad_scale"] == "1.4826"
    assert type(payload["controlExamples"][0]["count"]) is int
    assert payload["controlExamples"][1]["count"] == 1.5
