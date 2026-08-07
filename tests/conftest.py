import json
import os

import boto3
import pytest
from moto import mock_aws

CONFIG_PARAMETER_NAME = "/augusta-nexa-dev/active-directory/config"
SECRET_NAME = "/augusta-nexa-dev/active-directory/credentials"
S3_BUCKET_NAME = "augusta-nexa-dev-landing"
ALERT_TOPIC_ARN = "arn:aws:sns:us-east-1:123456789012:augusta-nexa-dev-active-directory-token-expiry"

DEFAULT_API_CONFIG = {
    "base_path": "funcionarios/directorio_activo",
    "base_url": "https://10.32.4.58:8453/api/v2/users",
    "domains": ["ventasyservicios.net", "vys"],
}

FAKE_TOKEN = "fake-token-for-tests-only"


@pytest.fixture(autouse=True)
def aws_credentials():
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"


@pytest.fixture(autouse=True)
def lambda_env(monkeypatch):
    monkeypatch.setenv("CONFIG_PARAMETER_NAME", CONFIG_PARAMETER_NAME)
    monkeypatch.setenv("SECRET_NAME", SECRET_NAME)
    monkeypatch.setenv("S3_BUCKET", S3_BUCKET_NAME)
    monkeypatch.setenv("ALERT_TOPIC_ARN", ALERT_TOPIC_ARN)


@pytest.fixture
def aws(aws_credentials):
    with mock_aws():
        yield boto3


@pytest.fixture
def ssm_config_parameter(aws):
    client = aws.client("ssm", region_name="us-east-1")
    client.put_parameter(
        Name=CONFIG_PARAMETER_NAME,
        Value=json.dumps(DEFAULT_API_CONFIG),
        Type="SecureString",
    )
    return client


def _put_token_secret(client, expiration_date: str, token: str = FAKE_TOKEN):
    client.create_secret(
        Name=SECRET_NAME,
        SecretString=json.dumps({"api-token": token, "expiration_date": expiration_date}),
    )
    return client


@pytest.fixture
def token_secret(aws):
    client = aws.client("secretsmanager", region_name="us-east-1")
    return _put_token_secret(client, expiration_date="2026-10-31")


@pytest.fixture
def token_secret_factory(aws):
    client = aws.client("secretsmanager", region_name="us-east-1")

    def _make(expiration_date: str, token: str = FAKE_TOKEN):
        return _put_token_secret(client, expiration_date=expiration_date, token=token)

    return _make


@pytest.fixture
def sns_topic(aws):
    client = aws.client("sns", region_name="us-east-1")
    topic = client.create_topic(Name="augusta-nexa-dev-active-directory-token-expiry")
    return client, topic["TopicArn"]


@pytest.fixture
def landing_bucket(aws):
    client = aws.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket=S3_BUCKET_NAME)
    return client
