"""Ingesta diaria del directorio activo (Active Directory) hacia S3.

Flujo: Secrets Manager (token + fecha de expiracion) + SSM Parameter Store
(config no sensible: base_path/base_url/domains) -> API REST del directorio
activo -> JSON crudo en s3://<landing_bucket>/<base_path>/.

El token no tiene rotacion automatica: se rota manualmente en Secrets
Manager (ver README). Cuando faltan <= TOKEN_EXPIRY_WARNING_DAYS para que
venza (segun el campo expiration_date del secreto), se publica una alerta
en SNS en cada invocacion hasta que se rote.
"""

from __future__ import annotations

import json
import logging
import os
import ssl
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

DEFAULT_TOKEN_EXPIRY_WARNING_DAYS = 10


class TokenUnavailableError(RuntimeError):
    """El secreto de Secrets Manager no existe, esta vacio, o le falta un campo requerido."""


class ApiConfigError(RuntimeError):
    """El parametro SSM de configuracion (base_path/base_url/domains) falta o es invalido."""


class ApiRequestError(RuntimeError):
    """La API del directorio activo respondio con error o no fue alcanzable."""


@dataclass(frozen=True)
class Config:
    """Config fija por deploy: nombres/ARNs de recursos AWS y knobs de infraestructura."""

    config_parameter_name: str
    secret_name: str
    s3_bucket: str
    aws_account_id: str
    alert_topic_arn: str
    request_timeout_seconds: int = 30
    verify_tls: bool = True
    token_expiry_warning_days: int = DEFAULT_TOKEN_EXPIRY_WARNING_DAYS

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            config_parameter_name=os.environ["CONFIG_PARAMETER_NAME"],
            secret_name=os.environ["SECRET_NAME"],
            s3_bucket=os.environ["S3_BUCKET"],
            aws_account_id=os.environ["AWS_ACCOUNT_ID"],
            alert_topic_arn=os.environ["ALERT_TOPIC_ARN"],
            request_timeout_seconds=int(os.environ.get("REQUEST_TIMEOUT_SECONDS", "30")),
            verify_tls=os.environ.get("API_TLS_VERIFY", "true").lower() != "false",
            token_expiry_warning_days=int(
                os.environ.get("TOKEN_EXPIRY_WARNING_DAYS", str(DEFAULT_TOKEN_EXPIRY_WARNING_DAYS))
            ),
        )


@dataclass(frozen=True)
class ApiConfig:
    """Config operacional editable en SSM sin redeploy: base_path/base_url/domains."""

    base_path: str
    base_url: str
    domains: list[str]


@dataclass(frozen=True)
class TokenSecret:
    """Token vigente + fecha de expiracion, leidos desde Secrets Manager."""

    token: str
    expiration_date: date


def get_api_config(ssm_client: Any, parameter_name: str) -> ApiConfig:
    """Lee y parsea el parametro SSM (SecureString) con base_path/base_url/domains."""
    response = ssm_client.get_parameter(Name=parameter_name, WithDecryption=True)
    raw_value = response["Parameter"]["Value"]

    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ApiConfigError(f"El parametro SSM '{parameter_name}' no es un JSON valido") from exc

    try:
        domains = list(parsed["domains"])
        return ApiConfig(
            base_path=parsed["base_path"],
            base_url=parsed["base_url"],
            domains=domains,
        )
    except (KeyError, TypeError) as exc:
        raise ApiConfigError(
            f"El parametro SSM '{parameter_name}' debe tener base_path, base_url y domains"
        ) from exc


def get_token_secret(secrets_client: Any, secret_name: str) -> TokenSecret:
    """Lee el token y su fecha de expiracion desde Secrets Manager.

    El secreto es un JSON: {"api-token": "...", "expiration_date": "YYYY-MM-DD"}.
    """
    response = secrets_client.get_secret_value(SecretId=secret_name)
    raw_value = response["SecretString"]

    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise TokenUnavailableError(f"El secreto '{secret_name}' no es un JSON valido") from exc

    token = str(parsed.get("api-token") or "").strip()
    if not token:
        raise TokenUnavailableError(f"El secreto '{secret_name}' no tiene 'api-token'")

    expiration_raw = parsed.get("expiration_date")
    if not expiration_raw:
        raise TokenUnavailableError(f"El secreto '{secret_name}' no tiene 'expiration_date'")

    try:
        expiration_date = date.fromisoformat(expiration_raw)
    except ValueError as exc:
        raise TokenUnavailableError(
            f"El secreto '{secret_name}' tiene un 'expiration_date' invalido: {expiration_raw!r}"
        ) from exc

    return TokenSecret(token=token, expiration_date=expiration_date)


def maybe_alert_token_expiry(
    sns_client: Any,
    topic_arn: str,
    expiration_date: date,
    warning_days: int,
    today: date | None = None,
) -> bool:
    """Publica una alerta en SNS si al token le quedan <= warning_days (o ya vencio).

    Es best-effort: si falla el publish, se loguea el error pero no se
    interrumpe la ingesta (la alerta no es mas critica que el dato en si).
    Devuelve True si se publico una alerta.
    """
    today = today or datetime.now(UTC).date()
    days_left = (expiration_date - today).days

    if days_left > warning_days:
        return False

    if days_left < 0:
        message = (
            f"El token de la API del directorio activo VENCIO hace {-days_left} dia(s) "
            f"(vencio el {expiration_date.isoformat()}). Rotar manualmente en Secrets Manager."
        )
    else:
        message = (
            f"El token de la API del directorio activo vence en {days_left} dia(s) "
            f"({expiration_date.isoformat()}). Rotar manualmente en Secrets Manager antes de esa fecha."
        )

    logger.warning(message)

    try:
        sns_client.publish(
            TopicArn=topic_arn,
            Subject="Token del directorio activo por vencer",
            Message=message,
        )
    except (BotoCoreError, ClientError):
        logger.exception("No se pudo publicar la alerta de expiracion del token en SNS")

    return True


def build_api_url(base_url: str, domains: list[str]) -> str:
    query = urlencode({"domains": ",".join(domains)})
    return f"{base_url}?{query}"


def fetch_directorio_activo(
    base_url: str,
    domains: list[str],
    token: str,
    timeout_seconds: int = 30,
    verify_tls: bool = True,
) -> Any:
    """Hace el GET contra la API del directorio activo y devuelve el JSON parseado."""
    url = build_api_url(base_url, domains)
    request = Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "Host": "API",
            "Authorization": token,
        },
    )
    # verify_tls=False is a reviewed exception (SonarQube will flag this line as a
    # Security Hotspot, S4830): v-vsasocs01 serves a self-signed cert with no CA
    # distributable to Lambda, and this endpoint is only reachable over the
    # private S2S VPN, never the public internet. See README "Networking
    # prerequisite" / api_tls_verify.
    context = ssl.create_default_context() if verify_tls else ssl._create_unverified_context()

    try:
        with urlopen(request, timeout=timeout_seconds, context=context) as response:
            status = response.status
            body = response.read()
    except HTTPError as exc:
        raise ApiRequestError(f"La API del directorio activo respondio HTTP {exc.code}") from exc
    except URLError as exc:
        raise ApiRequestError(f"No se pudo contactar la API del directorio activo: {exc.reason}") from exc

    if status != 200:
        raise ApiRequestError(f"La API del directorio activo respondio con status inesperado {status}")

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise ApiRequestError("La respuesta de la API no es un JSON valido") from exc


def build_s3_key(prefix: str, timestamp: datetime) -> str:
    partition = f"year={timestamp:%Y}/month={timestamp:%m}/day={timestamp:%d}"
    file_name = f"directorio_activo_{timestamp.strftime('%Y%m%dT%H%M%SZ')}.json"
    return f"{prefix.rstrip('/')}/{partition}/{file_name}"


def upload_to_s3(
    s3_client: Any, bucket: str, key: str, payload: Any, expected_bucket_owner: str
) -> None:
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
        ExpectedBucketOwner=expected_bucket_owner,
    )


def count_records(payload: Any) -> int | None:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        users = payload.get("users")
        if isinstance(users, list):
            return len(users)
    return None


def handler(event: dict[str, Any] | None, context: Any) -> dict[str, Any]:
    event = event or {}
    logger.info(
        "Invocacion recibida: source=%s detail_type=%s id=%s",
        event.get("source"),
        event.get("detail-type"),
        event.get("id"),
    )

    config = Config.from_env()

    ssm_client = boto3.client("ssm")
    secrets_client = boto3.client("secretsmanager")
    sns_client = boto3.client("sns")
    s3_client = boto3.client("s3")

    api_config = get_api_config(ssm_client, config.config_parameter_name)
    token_secret = get_token_secret(secrets_client, config.secret_name)

    maybe_alert_token_expiry(
        sns_client,
        config.alert_topic_arn,
        token_secret.expiration_date,
        config.token_expiry_warning_days,
    )

    payload = fetch_directorio_activo(
        api_config.base_url,
        api_config.domains,
        token_secret.token,
        config.request_timeout_seconds,
        config.verify_tls,
    )

    now = datetime.now(UTC)
    key = build_s3_key(api_config.base_path, now)
    upload_to_s3(s3_client, config.s3_bucket, key, payload, config.aws_account_id)

    record_count = count_records(payload)
    logger.info(
        "Ingesta de directorio_activo completada: bucket=%s key=%s records=%s",
        config.s3_bucket,
        key,
        record_count,
    )

    return {
        "bucket": config.s3_bucket,
        "key": key,
        "record_count": record_count,
        "token_expiration_date": token_secret.expiration_date.isoformat(),
    }
