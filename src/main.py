"""Ingesta diaria del directorio activo (Active Directory) hacia S3.

Flujo: SSM Parameter Store (token vigente) -> cache/historial en DynamoDB
-> API REST del directorio activo -> JSON crudo en
s3://<landing_bucket>/funcionarios/directorio_activo/.

El token de la API se rota manualmente cada 6 meses en SSM (ver README).
DynamoDB guarda cada version del token (activa y superseded) para
trazabilidad/auditoria; nunca se borran versiones anteriores.
"""

from __future__ import annotations

import json
import logging
import os
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import boto3

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

TOKEN_SCOPE = "directorio_activo"
DEFAULT_TOKEN_VALIDITY_DAYS = 180


class TokenUnavailableError(RuntimeError):
    """El parametro SSM con el token no existe o esta vacio."""


class ApiConfigError(RuntimeError):
    """El parametro SSM de configuracion (s3_prefix/base_url/domains) falta o es invalido."""


class ApiRequestError(RuntimeError):
    """La API del directorio activo respondio con error o no fue alcanzable."""


@dataclass(frozen=True)
class Config:
    """Config fija por deploy: nombres de recursos AWS y knobs de infraestructura."""

    token_parameter_name: str
    config_parameter_name: str
    token_cache_table_name: str
    s3_bucket: str
    request_timeout_seconds: int = 30
    verify_tls: bool = True
    token_validity_days: int = DEFAULT_TOKEN_VALIDITY_DAYS

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            token_parameter_name=os.environ["TOKEN_PARAMETER_NAME"],
            config_parameter_name=os.environ["CONFIG_PARAMETER_NAME"],
            token_cache_table_name=os.environ["TOKEN_CACHE_TABLE_NAME"],
            s3_bucket=os.environ["S3_BUCKET"],
            request_timeout_seconds=int(os.environ.get("REQUEST_TIMEOUT_SECONDS", "30")),
            verify_tls=os.environ.get("API_TLS_VERIFY", "true").lower() != "false",
            token_validity_days=int(
                os.environ.get("TOKEN_VALIDITY_DAYS", str(DEFAULT_TOKEN_VALIDITY_DAYS))
            ),
        )


@dataclass(frozen=True)
class ApiConfig:
    """Config operacional editable en SSM sin redeploy: s3_prefix/base_url/domains."""

    s3_prefix: str
    base_url: str
    domains: list[str]


def get_api_config(ssm_client: Any, parameter_name: str) -> ApiConfig:
    """Lee y parsea el parametro SSM (String) con s3_prefix/base_url/domains."""
    response = ssm_client.get_parameter(Name=parameter_name)
    raw_value = response["Parameter"]["Value"]

    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ApiConfigError(f"El parametro SSM '{parameter_name}' no es un JSON valido") from exc

    try:
        domains = list(parsed["domains"])
        return ApiConfig(
            s3_prefix=parsed["s3_prefix"],
            base_url=parsed["base_url"],
            domains=domains,
        )
    except (KeyError, TypeError) as exc:
        raise ApiConfigError(
            f"El parametro SSM '{parameter_name}' debe tener s3_prefix, base_url y domains"
        ) from exc


def get_current_token(ssm_client: Any, parameter_name: str) -> str:
    """Lee el token vigente desde SSM Parameter Store (SecureString)."""
    response = ssm_client.get_parameter(Name=parameter_name, WithDecryption=True)
    token = response["Parameter"]["Value"].strip()
    if not token:
        raise TokenUnavailableError(f"El parametro SSM '{parameter_name}' esta vacio")
    return token


def get_latest_cached_token(table: Any, scope: str = TOKEN_SCOPE) -> dict[str, Any] | None:
    """Devuelve el item con la version mas alta cacheada para ese scope, o None."""
    response = table.query(
        KeyConditionExpression="token_scope = :scope",
        ExpressionAttributeValues={":scope": scope},
        ScanIndexForward=False,
        Limit=1,
    )
    items = response.get("Items", [])
    return items[0] if items else None


def sync_token_cache(
    table: Any,
    current_token: str,
    validity_days: int = DEFAULT_TOKEN_VALIDITY_DAYS,
    scope: str = TOKEN_SCOPE,
) -> dict[str, Any]:
    """Sincroniza el token vigente (SSM) con el historial en DynamoDB.

    Si el token no cambio desde la ultima corrida, no escribe nada.
    Si cambio (rotacion manual en SSM), agrega una version nueva ACTIVE
    y marca la anterior como SUPERSEDED, sin borrar historial.
    """
    latest = get_latest_cached_token(table, scope)

    if latest is not None and latest["token_value"] == current_token:
        if latest["expires_at"] < datetime.now(UTC).isoformat():
            logger.warning(
                "El token activo (version %s) supero su vigencia esperada (%s). "
                "Verificar rotacion manual en SSM.",
                latest["version"],
                latest["expires_at"],
            )
        return latest

    now = datetime.now(UTC)
    next_version = int(latest["version"]) + 1 if latest else 1

    if latest is not None:
        table.update_item(
            Key={"token_scope": scope, "version": latest["version"]},
            UpdateExpression="SET #status = :superseded",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":superseded": "SUPERSEDED"},
        )

    new_item = {
        "token_scope": scope,
        "version": next_version,
        "token_value": current_token,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(days=validity_days)).isoformat(),
        "status": "ACTIVE",
        "source": "ssm",
    }
    table.put_item(Item=new_item)
    logger.info("Nueva version de token cacheada en DynamoDB: version=%s", next_version)
    return new_item


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


def upload_to_s3(s3_client: Any, bucket: str, key: str, payload: Any) -> None:
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
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
    dynamodb = boto3.resource("dynamodb")
    s3_client = boto3.client("s3")
    token_table = dynamodb.Table(config.token_cache_table_name)

    api_config = get_api_config(ssm_client, config.config_parameter_name)
    token = get_current_token(ssm_client, config.token_parameter_name)
    sync_token_cache(token_table, token, config.token_validity_days)

    payload = fetch_directorio_activo(
        api_config.base_url,
        api_config.domains,
        token,
        config.request_timeout_seconds,
        config.verify_tls,
    )

    now = datetime.now(UTC)
    key = build_s3_key(api_config.s3_prefix, now)
    upload_to_s3(s3_client, config.s3_bucket, key, payload)

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
    }
