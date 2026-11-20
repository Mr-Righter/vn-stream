"""Localization must preserve message identity across HTTP and durable state."""

from tests.auth_helpers import TestClient, api_data, sign_in
from vn_stream.api.app import create_app


def test_english_api_errors_are_structured_and_do_not_echo_credentials(tmp_path):
    with TestClient(create_app(tmp_path / "data", [tmp_path / "games"], "secret")) as client:
        sign_in(client, "secret")
        response = client.get("/api/v2/projects/" + "a" * 32, headers={"Accept-Language": "en"})
        assert response.status_code == 404
        assert api_data(response)["error"]["code"] == "errors.gameNotFound"
        assert api_data(response)["request_id"]
        assert "不存在" not in response.text
        rejected = client.post(
            "/api/v2/projects/" + "a" * 32 + "/start",
            json={"password": "DO-NOT-ECHO"},
            headers={"Accept-Language": "en"},
        )
        assert rejected.status_code == 422
        assert "DO-NOT-ECHO" not in rejected.text
        assert api_data(rejected)["error"]["code"] == "errors.invalidRequestParameters"


def test_message_rendering_keeps_params_and_has_safe_fallbacks():
    from vn_stream.i18n import descriptor, render, text

    value = text("library.refreshSummary", added=2, warnings=1)
    assert descriptor(value) == {
        "code": "library.refreshSummary",
        "params": {"added": 2, "warnings": 1},
    }
    assert render(descriptor(value), "en") == "Library refreshed · 2 added · 1 warnings"
    assert "请求失败" in render({"code": "unknown", "params": {}}, "zh-CN")
    assert "Request failed" in render({"code": "library.refreshSummary", "params": {}}, "en")


def test_legacy_job_messages_are_localized_without_mutating_record():
    from vn_stream.i18n import localize_payload

    record = {"id": "old", "error": "服务器空间不足", "warning": None, "state": "failed"}
    localized = localize_payload(record, "en")
    assert localized["error"] == "Not enough server storage"
    assert localized["messages"]["error"]["code"]
    assert record["error"] == "服务器空间不足"
    unknown = localize_payload({"error": "内部信息：password=SECRET"}, "en")
    assert "SECRET" not in unknown["error"]
    assert unknown["error"] == "Request failed. Please retry."


def test_locale_quality_and_unsupported_preferences_have_deterministic_fallbacks():
    from vn_stream.i18n import language

    assert language("en;q=0, zh-CN;q=1") == "zh-CN"
    assert language("fr, en-US;q=0.8, zh;q=0.4") == "en"
    assert language("fr") == "zh-CN"


def test_durable_message_parameters_survive_record_serialization():
    import json

    from vn_stream.i18n import durable_messages, localize_payload, text

    original = durable_messages({"error": text("runtime.notInstalled", version="8.3.2")})
    record = json.loads(json.dumps(original))
    assert (
        localize_payload(record, "en")["error"]
        == "Runtime 8.3.2 is not installed; install it or select another installed runtime"
    )
    assert durable_messages(record)["messages"] == original["messages"]
    assert localize_payload({"name": "中文游戏", "path": "/目录", "version": "原版"}, "en") == {
        "name": "中文游戏",
        "path": "/目录",
        "version": "原版",
    }


def test_catalogs_have_matching_keys_and_parameter_contracts():
    import re

    from vn_stream.i18n import CATALOGS

    assert CATALOGS["en"].keys() == CATALOGS["zh-CN"].keys()
    for code in CATALOGS["en"]:
        assert set(re.findall(r"\{(\w+)\}", CATALOGS["en"][code])) == set(
            re.findall(r"\{(\w+)\}", CATALOGS["zh-CN"][code])
        ), code
