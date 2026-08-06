import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

from src.main import (
    ApiConfigError,
    ApiRequestError,
    Config,
    TokenUnavailableError,
    build_api_url,
    build_s3_key,
    count_records,
    fetch_directorio_activo,
    get_api_config,
    get_current_token,
    handler,
    sync_token_cache,
    upload_to_s3,
)

FAKE_TOKEN = "fake-token-for-tests-only"
EVENTS_DIR = Path(__file__).parent / "events"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def test_config_from_env_reads_resource_names_and_defaults():
    config = Config.from_env()

    assert config.token_parameter_name == "/augusta-nexa-dev/active-directory/api-token"
    assert config.config_parameter_name == "/augusta-nexa-dev/active-directory/api-config"
    assert config.verify_tls is True
    assert config.token_validity_days == 180


# ---------------------------------------------------------------------------
# get_api_config
# ---------------------------------------------------------------------------
def test_get_api_config_parses_json():
    ssm_client = MagicMock()
    ssm_client.get_parameter.return_value = {
        "Parameter": {
            "Value": json.dumps(
                {
                    "s3_prefix": "funcionarios/directorio_activo",
                    "base_url": "https://v-vsasocs01:8453/api/v2/users",
                    "domains": ["ventasyservicios.net", "vys"],
                }
            )
        }
    }

    config = get_api_config(ssm_client, "/some/config")

    assert config.s3_prefix == "funcionarios/directorio_activo"
    assert config.base_url == "https://v-vsasocs01:8453/api/v2/users"
    assert config.domains == ["ventasyservicios.net", "vys"]


def test_get_api_config_raises_on_invalid_json():
    ssm_client = MagicMock()
    ssm_client.get_parameter.return_value = {"Parameter": {"Value": "not-json"}}

    with pytest.raises(ApiConfigError):
        get_api_config(ssm_client, "/some/config")


def test_get_api_config_raises_on_missing_keys():
    ssm_client = MagicMock()
    ssm_client.get_parameter.return_value = {"Parameter": {"Value": json.dumps({"s3_prefix": "x"})}}

    with pytest.raises(ApiConfigError):
        get_api_config(ssm_client, "/some/config")


# ---------------------------------------------------------------------------
# build_api_url / build_s3_key / count_records
# ---------------------------------------------------------------------------
def test_build_api_url_encodes_domains():
    url = build_api_url("https://v-vsasocs01:8453/api/v2/users", ["ventasyservicios.net", "vys"])

    assert url == "https://v-vsasocs01:8453/api/v2/users?domains=ventasyservicios.net%2Cvys"


def test_build_s3_key_uses_prefix_and_timestamp():
    timestamp = datetime(2026, 8, 4, 10, 0, 0, tzinfo=UTC)

    key = build_s3_key("funcionarios/directorio_activo", timestamp)

    assert key == "funcionarios/directorio_activo/directorio_activo_20260804T100000Z.json"


def test_build_s3_key_strips_trailing_slash_in_prefix():
    timestamp = datetime(2026, 8, 4, 10, 0, 0, tzinfo=UTC)

    key = build_s3_key("funcionarios/directorio_activo/", timestamp)

    assert key.startswith("funcionarios/directorio_activo/directorio_activo_")


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ([{"id": 1}, {"id": 2}], 2),
        ({"users": [{"id": 1}]}, 1),
        ({"no_users_key": True}, None),
        ("not-a-collection", None),
    ],
)
def test_count_records(payload, expected):
    assert count_records(payload) == expected


# ---------------------------------------------------------------------------
# get_current_token
# ---------------------------------------------------------------------------
def test_get_current_token_returns_stripped_value():
    ssm_client = MagicMock()
    ssm_client.get_parameter.return_value = {"Parameter": {"Value": f"  {FAKE_TOKEN}  "}}

    token = get_current_token(ssm_client, "/some/param")

    assert token == FAKE_TOKEN
    ssm_client.get_parameter.assert_called_once_with(Name="/some/param", WithDecryption=True)


def test_get_current_token_raises_when_empty():
    ssm_client = MagicMock()
    ssm_client.get_parameter.return_value = {"Parameter": {"Value": "   "}}

    with pytest.raises(TokenUnavailableError):
        get_current_token(ssm_client, "/some/param")


# ---------------------------------------------------------------------------
# sync_token_cache (DynamoDB via moto)
# ---------------------------------------------------------------------------
def test_sync_token_cache_creates_first_version(token_table):
    item = sync_token_cache(token_table, FAKE_TOKEN)

    assert item["version"] == 1
    assert item["status"] == "ACTIVE"
    assert item["token_value"] == FAKE_TOKEN


def test_sync_token_cache_is_noop_when_token_unchanged(token_table):
    first = sync_token_cache(token_table, FAKE_TOKEN)
    second = sync_token_cache(token_table, FAKE_TOKEN)

    assert first["version"] == second["version"] == 1
    stored_items = token_table.scan()["Items"]
    assert len(stored_items) == 1


def test_sync_token_cache_adds_new_version_and_supersedes_previous(token_table):
    sync_token_cache(token_table, "old-token")
    new_item = sync_token_cache(token_table, "rotated-token")

    assert new_item["version"] == 2
    assert new_item["status"] == "ACTIVE"

    old_item = token_table.get_item(Key={"token_scope": "directorio_activo", "version": 1})["Item"]
    assert old_item["status"] == "SUPERSEDED"

    stored_items = token_table.scan()["Items"]
    assert len(stored_items) == 2


# ---------------------------------------------------------------------------
# fetch_directorio_activo
# ---------------------------------------------------------------------------
def _mock_response(status: int, body: bytes):
    response = MagicMock()
    response.status = status
    response.read.return_value = body
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


def test_fetch_directorio_activo_returns_parsed_json():
    body = json.dumps({"users": [{"id": 1}]}).encode("utf-8")

    with patch("src.main.urlopen", return_value=_mock_response(200, body)) as mocked_urlopen:
        payload = fetch_directorio_activo(
            "https://v-vsasocs01:8453/api/v2/users",
            ["ventasyservicios.net", "vys"],
            FAKE_TOKEN,
        )

    assert payload == {"users": [{"id": 1}]}
    request = mocked_urlopen.call_args.args[0]
    assert request.get_header("Authorization") == FAKE_TOKEN
    assert request.get_header("Host") == "API"


def test_fetch_directorio_activo_raises_on_http_error():
    with patch(
        "src.main.urlopen",
        side_effect=HTTPError(url="", code=401, msg="Unauthorized", hdrs=None, fp=None),
    ):
        with pytest.raises(ApiRequestError):
            fetch_directorio_activo(
                "https://v-vsasocs01:8453/api/v2/users", ["vys"], FAKE_TOKEN
            )


def test_fetch_directorio_activo_raises_on_connection_error():
    with patch("src.main.urlopen", side_effect=URLError("no route to host")):
        with pytest.raises(ApiRequestError):
            fetch_directorio_activo(
                "https://v-vsasocs01:8453/api/v2/users", ["vys"], FAKE_TOKEN
            )


def test_fetch_directorio_activo_raises_on_invalid_json():
    with patch("src.main.urlopen", return_value=_mock_response(200, b"not-json")):
        with pytest.raises(ApiRequestError):
            fetch_directorio_activo(
                "https://v-vsasocs01:8453/api/v2/users", ["vys"], FAKE_TOKEN
            )


# ---------------------------------------------------------------------------
# upload_to_s3 (S3 via moto)
# ---------------------------------------------------------------------------
def test_upload_to_s3_writes_expected_key_and_body(landing_bucket):
    bucket = "augusta-nexa-dev-landing"
    key = "funcionarios/directorio_activo/f.json"

    upload_to_s3(landing_bucket, bucket, key, {"a": 1})

    stored = landing_bucket.get_object(Bucket=bucket, Key=key)
    assert json.loads(stored["Body"].read()) == {"a": 1}
    assert stored["ContentType"] == "application/json"


# ---------------------------------------------------------------------------
# handler (end-to-end, all AWS services mocked via moto + urlopen patched)
# ---------------------------------------------------------------------------
def test_handler_happy_path(token_table, ssm_parameter, landing_bucket):
    body = json.dumps({"users": [{"id": 1}, {"id": 2}]}).encode("utf-8")

    with patch("src.main.urlopen", return_value=_mock_response(200, body)):
        result = handler({}, context=None)

    assert result["bucket"] == "augusta-nexa-dev-landing"
    assert result["record_count"] == 2
    assert result["key"].startswith("funcionarios/directorio_activo/directorio_activo_")

    stored = landing_bucket.get_object(Bucket="augusta-nexa-dev-landing", Key=result["key"])
    assert json.loads(stored["Body"].read()) == {"users": [{"id": 1}, {"id": 2}]}

    cached_items = token_table.scan()["Items"]
    assert len(cached_items) == 1
    assert cached_items[0]["status"] == "ACTIVE"


def test_handler_propagates_api_errors_without_writing_to_s3(token_table, ssm_parameter, landing_bucket):
    with patch("src.main.urlopen", side_effect=URLError("timeout")):
        with pytest.raises(ApiRequestError):
            handler({}, context=None)

    assert landing_bucket.list_objects_v2(Bucket="augusta-nexa-dev-landing").get("Contents") is None


def test_handler_accepts_real_eventbridge_event_shape(token_table, ssm_parameter, landing_bucket):
    scheduled_event = json.loads((EVENTS_DIR / "scheduled_event.json").read_text())
    body = json.dumps({"users": []}).encode("utf-8")

    with patch("src.main.urlopen", return_value=_mock_response(200, body)):
        result = handler(scheduled_event, context=None)

    assert result["record_count"] == 0
