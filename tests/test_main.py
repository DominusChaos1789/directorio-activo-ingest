import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest
from botocore.exceptions import ClientError

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
    get_token_secret,
    handler,
    maybe_alert_token_expiry,
    upload_to_s3,
)

FAKE_TOKEN = "fake-token-for-tests-only"
EVENTS_DIR = Path(__file__).parent / "events"
ALERT_TOPIC_ARN = "arn:aws:sns:us-east-1:123456789012:augusta-nexa-dev-active-directory-token-expiry"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def test_config_from_env_reads_resource_names_and_defaults():
    config = Config.from_env()

    assert config.config_parameter_name == "/augusta-nexa-dev/active-directory/config"
    assert config.secret_name == "/augusta-nexa-dev/active-directory/credentials"
    assert config.alert_topic_arn == ALERT_TOPIC_ARN
    assert config.verify_tls is True
    assert config.token_expiry_warning_days == 10


# ---------------------------------------------------------------------------
# get_api_config
# ---------------------------------------------------------------------------
def test_get_api_config_parses_json():
    ssm_client = MagicMock()
    ssm_client.get_parameter.return_value = {
        "Parameter": {
            "Value": json.dumps(
                {
                    "base_path": "funcionarios/directorio_activo",
                    "base_url": "https://10.32.4.58:8453/api/v2/users",
                    "domains": ["ventasyservicios.net", "vys"],
                }
            )
        }
    }

    config = get_api_config(ssm_client, "/some/config")

    assert config.base_path == "funcionarios/directorio_activo"
    assert config.base_url == "https://10.32.4.58:8453/api/v2/users"
    assert config.domains == ["ventasyservicios.net", "vys"]


def test_get_api_config_raises_on_invalid_json():
    ssm_client = MagicMock()
    ssm_client.get_parameter.return_value = {"Parameter": {"Value": "not-json"}}

    with pytest.raises(ApiConfigError):
        get_api_config(ssm_client, "/some/config")


def test_get_api_config_raises_on_missing_keys():
    ssm_client = MagicMock()
    ssm_client.get_parameter.return_value = {"Parameter": {"Value": json.dumps({"base_path": "x"})}}

    with pytest.raises(ApiConfigError):
        get_api_config(ssm_client, "/some/config")


# ---------------------------------------------------------------------------
# build_api_url / build_s3_key / count_records
# ---------------------------------------------------------------------------
def test_build_api_url_encodes_domains():
    url = build_api_url("https://v-vsasocs01:8453/api/v2/users", ["ventasyservicios.net", "vys"])

    assert url == "https://v-vsasocs01:8453/api/v2/users?domains=ventasyservicios.net%2Cvys"


def test_build_s3_key_uses_hive_partitioning_and_timestamp():
    timestamp = datetime(2026, 8, 4, 10, 0, 0, tzinfo=UTC)

    key = build_s3_key("funcionarios/directorio_activo", timestamp)

    assert key == (
        "funcionarios/directorio_activo/year=2026/month=08/day=04/"
        "directorio_activo_20260804T100000Z.json"
    )


def test_build_s3_key_strips_trailing_slash_in_prefix():
    timestamp = datetime(2026, 8, 4, 10, 0, 0, tzinfo=UTC)

    key = build_s3_key("funcionarios/directorio_activo/", timestamp)

    assert key.startswith("funcionarios/directorio_activo/year=2026/month=08/day=04/")


def test_build_s3_key_zero_pads_single_digit_month_and_day():
    timestamp = datetime(2026, 1, 5, 10, 0, 0, tzinfo=UTC)

    key = build_s3_key("funcionarios/directorio_activo", timestamp)

    assert "year=2026/month=01/day=05/" in key


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
# get_token_secret
# ---------------------------------------------------------------------------
def test_get_token_secret_parses_token_and_expiration():
    secrets_client = MagicMock()
    secrets_client.get_secret_value.return_value = {
        "SecretString": json.dumps({"api-token": FAKE_TOKEN, "expiration_date": "2026-10-31"})
    }

    secret = get_token_secret(secrets_client, "/some/secret")

    assert secret.token == FAKE_TOKEN
    assert secret.expiration_date.isoformat() == "2026-10-31"


def test_get_token_secret_raises_on_invalid_json():
    secrets_client = MagicMock()
    secrets_client.get_secret_value.return_value = {"SecretString": "not-json"}

    with pytest.raises(TokenUnavailableError):
        get_token_secret(secrets_client, "/some/secret")


def test_get_token_secret_raises_when_token_missing():
    secrets_client = MagicMock()
    secrets_client.get_secret_value.return_value = {
        "SecretString": json.dumps({"expiration_date": "2026-10-31"})
    }

    with pytest.raises(TokenUnavailableError):
        get_token_secret(secrets_client, "/some/secret")


def test_get_token_secret_raises_when_expiration_missing():
    secrets_client = MagicMock()
    secrets_client.get_secret_value.return_value = {
        "SecretString": json.dumps({"api-token": FAKE_TOKEN})
    }

    with pytest.raises(TokenUnavailableError):
        get_token_secret(secrets_client, "/some/secret")


def test_get_token_secret_raises_on_invalid_expiration_format():
    secrets_client = MagicMock()
    secrets_client.get_secret_value.return_value = {
        "SecretString": json.dumps({"api-token": FAKE_TOKEN, "expiration_date": "10/31/2026"})
    }

    with pytest.raises(TokenUnavailableError):
        get_token_secret(secrets_client, "/some/secret")


# ---------------------------------------------------------------------------
# maybe_alert_token_expiry
# ---------------------------------------------------------------------------
def test_maybe_alert_token_expiry_does_nothing_when_far_from_expiry():
    sns_client = MagicMock()
    today = datetime(2026, 8, 6, tzinfo=UTC).date()
    expiration_date = today + timedelta(days=30)

    alerted = maybe_alert_token_expiry(sns_client, ALERT_TOPIC_ARN, expiration_date, 10, today=today)

    assert alerted is False
    sns_client.publish.assert_not_called()


def test_maybe_alert_token_expiry_publishes_at_the_warning_threshold():
    sns_client = MagicMock()
    today = datetime(2026, 8, 6, tzinfo=UTC).date()
    expiration_date = today + timedelta(days=10)

    alerted = maybe_alert_token_expiry(sns_client, ALERT_TOPIC_ARN, expiration_date, 10, today=today)

    assert alerted is True
    sns_client.publish.assert_called_once()
    message = sns_client.publish.call_args.kwargs["Message"]
    assert "vence en 10 dia" in message


def test_maybe_alert_token_expiry_publishes_when_already_expired():
    sns_client = MagicMock()
    today = datetime(2026, 8, 6, tzinfo=UTC).date()
    expiration_date = today - timedelta(days=3)

    alerted = maybe_alert_token_expiry(sns_client, ALERT_TOPIC_ARN, expiration_date, 10, today=today)

    assert alerted is True
    message = sns_client.publish.call_args.kwargs["Message"]
    assert "VENCIO" in message


def test_maybe_alert_token_expiry_swallows_publish_errors():
    sns_client = MagicMock()
    sns_client.publish.side_effect = ClientError(
        {"Error": {"Code": "NotFound", "Message": "Topic does not exist"}}, "Publish"
    )
    today = datetime(2026, 8, 6, tzinfo=UTC).date()
    expiration_date = today + timedelta(days=1)

    alerted = maybe_alert_token_expiry(sns_client, ALERT_TOPIC_ARN, expiration_date, 10, today=today)

    assert alerted is True


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
def test_handler_happy_path(ssm_config_parameter, token_secret, landing_bucket):
    body = json.dumps({"users": [{"id": 1}, {"id": 2}]}).encode("utf-8")

    with patch("src.main.urlopen", return_value=_mock_response(200, body)):
        result = handler({}, context=None)

    assert result["bucket"] == "augusta-nexa-dev-landing"
    assert result["record_count"] == 2
    assert result["token_expiration_date"] == "2026-10-31"
    assert result["key"].startswith("funcionarios/directorio_activo/year=")
    assert "/month=" in result["key"]
    assert "/day=" in result["key"]
    assert re.search(r"/directorio_activo_\d{8}T\d{6}Z\.json$", result["key"])

    stored = landing_bucket.get_object(Bucket="augusta-nexa-dev-landing", Key=result["key"])
    assert json.loads(stored["Body"].read()) == {"users": [{"id": 1}, {"id": 2}]}


def test_handler_propagates_api_errors_without_writing_to_s3(
    ssm_config_parameter, token_secret, landing_bucket
):
    with patch("src.main.urlopen", side_effect=URLError("timeout")):
        with pytest.raises(ApiRequestError):
            handler({}, context=None)

    assert landing_bucket.list_objects_v2(Bucket="augusta-nexa-dev-landing").get("Contents") is None


def test_handler_accepts_real_eventbridge_event_shape(ssm_config_parameter, token_secret, landing_bucket):
    scheduled_event = json.loads((EVENTS_DIR / "scheduled_event.json").read_text())
    body = json.dumps({"users": []}).encode("utf-8")

    with patch("src.main.urlopen", return_value=_mock_response(200, body)):
        result = handler(scheduled_event, context=None)

    assert result["record_count"] == 0


def test_handler_publishes_alert_when_token_near_expiry(
    ssm_config_parameter, token_secret_factory, sns_topic, landing_bucket
):
    _sns_client, topic_arn = sns_topic
    assert topic_arn == ALERT_TOPIC_ARN

    near_expiry = (datetime.now(UTC).date() + timedelta(days=5)).isoformat()
    token_secret_factory(expiration_date=near_expiry)

    body = json.dumps({"users": []}).encode("utf-8")
    with patch("src.main.urlopen", return_value=_mock_response(200, body)):
        result = handler({}, context=None)

    assert result["record_count"] == 0
