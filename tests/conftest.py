import json
import os

import boto3
import pytest
from moto import mock_aws

TOKEN_TABLE_NAME = "test-directorio-activo-token-cache"
TOKEN_PARAMETER_NAME = "/augusta-nexa-dev/active-directory/api-token"
CONFIG_PARAMETER_NAME = "/augusta-nexa-dev/active-directory/api-config"
S3_BUCKET_NAME = "augusta-nexa-dev-landing"

DEFAULT_API_CONFIG = {
    "s3_prefix": "funcionarios/directorio_activo",
    "base_url": "https://v-vsasocs01:8453/api/v2/users",
    "domains": ["ventasyservicios.net", "vys"],
}


@pytest.fixture(autouse=True)
def aws_credentials():
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"


@pytest.fixture(autouse=True)
def lambda_env(monkeypatch):
    monkeypatch.setenv("TOKEN_PARAMETER_NAME", TOKEN_PARAMETER_NAME)
    monkeypatch.setenv("CONFIG_PARAMETER_NAME", CONFIG_PARAMETER_NAME)
    monkeypatch.setenv("TOKEN_CACHE_TABLE_NAME", TOKEN_TABLE_NAME)
    monkeypatch.setenv("S3_BUCKET", S3_BUCKET_NAME)


@pytest.fixture
def aws(aws_credentials):
    with mock_aws():
        yield boto3


@pytest.fixture
def token_table(aws):
    dynamodb = aws.resource("dynamodb", region_name="us-east-1")
    dynamodb.create_table(
        TableName=TOKEN_TABLE_NAME,
        KeySchema=[
            {"AttributeName": "token_scope", "KeyType": "HASH"},
            {"AttributeName": "version", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "token_scope", "AttributeType": "S"},
            {"AttributeName": "version", "AttributeType": "N"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    return dynamodb.Table(TOKEN_TABLE_NAME)


@pytest.fixture
def ssm_parameter(aws):
    client = aws.client("ssm", region_name="us-east-1")
    client.put_parameter(
        Name=TOKEN_PARAMETER_NAME,
        Value="fake-token-for-tests-only",
        Type="SecureString",
    )
    client.put_parameter(
        Name=CONFIG_PARAMETER_NAME,
        Value=json.dumps(DEFAULT_API_CONFIG),
        Type="String",
    )
    return client


@pytest.fixture
def landing_bucket(aws):
    client = aws.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket=S3_BUCKET_NAME)
    return client
