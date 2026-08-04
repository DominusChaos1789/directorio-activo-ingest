###############################################################################
# variables.tf
###############################################################################

variable "stack_id" {
  description = "Identificador del stack, ej: augusta-nexa-dev. Se usa como prefijo de todos los nombres de recursos y cambia entre ambientes (dev/qa/prod)."
  type        = string
}

variable "environment" {
  description = "Ambiente: dev, qa, prod. Usado solo para tagging."
  type        = string
  default     = "dev"
}

variable "aws_region" {
  description = "Region de AWS donde se despliegan los recursos."
  type        = string
  default     = "us-east-1"
}

# ─────────────────────────────────────────────────────────────────────────────
# S3 (landing bucket ya existente)
# ─────────────────────────────────────────────────────────────────────────────
variable "landing_bucket_name" {
  description = "Nombre del bucket S3 de landing ya existente donde se guardan los JSON (ej: augusta-nexa-dev-landing). No se crea con Terraform, solo se referencia."
  type        = string
}

variable "s3_prefix" {
  description = "Prefijo dentro del landing bucket donde se escriben los JSON del directorio activo."
  type        = string
  default     = "funcionarios/directorio_activo"
}

# ─────────────────────────────────────────────────────────────────────────────
# API DEL DIRECTORIO ACTIVO
# ─────────────────────────────────────────────────────────────────────────────
variable "api_base_url" {
  description = "URL base de la API del directorio activo."
  type        = string
  default     = "https://v-vsasocs01:8453/api/v2/users"
}

variable "api_domains" {
  description = "Dominios a consultar en la API (query param 'domains')."
  type        = list(string)
  default     = ["ventasyservicios.net", "vys"]
}

variable "api_request_timeout_seconds" {
  description = "Timeout (segundos) para el request HTTP a la API del directorio activo."
  type        = number
  default     = 30
}

variable "api_tls_verify" {
  description = "Si es false, desactiva la verificacion del certificado TLS del host interno. Dejar en true salvo que el CA interno no este disponible para Lambda."
  type        = bool
  default     = true
}

# ─────────────────────────────────────────────────────────────────────────────
# TOKEN (SSM Parameter Store + cache/historial en DynamoDB)
# ─────────────────────────────────────────────────────────────────────────────
variable "token_validity_days" {
  description = "Vigencia esperada del token (dias). El token real se rota manualmente en SSM; este valor solo se usa para calcular expires_at en el historial de DynamoDB y emitir un warning en logs si se vence."
  type        = number
  default     = 180
}

# ─────────────────────────────────────────────────────────────────────────────
# EVENTBRIDGE SCHEDULE
# ─────────────────────────────────────────────────────────────────────────────
variable "schedule_expression" {
  description = "Cron de EventBridge (siempre en UTC). Default = 05:00 America/Bogota (UTC-5, sin horario de verano) = 10:00 UTC."
  type        = string
  default     = "cron(0 10 * * ? *)"
}

variable "schedule_enabled" {
  description = "Poner en false para desactivar la regla de EventBridge (util en ambientes bajos)."
  type        = bool
  default     = true
}

# ─────────────────────────────────────────────────────────────────────────────
# LAMBDA
# ─────────────────────────────────────────────────────────────────────────────
variable "lambda_timeout_seconds" {
  type    = number
  default = 60
}

variable "lambda_memory_mb" {
  type    = number
  default = 256
}

variable "log_retention_days" {
  type    = number
  default = 30
}

# ─────────────────────────────────────────────────────────────────────────────
# NETWORKING
# La API del directorio activo (v-vsasocs01, 10.32.4.82) solo es alcanzable
# on-prem via VPN site-to-site. El equipo de DevOps ya habilito el firewall
# Nexa para la IP 10.32.4.82. La Lambda debe correr dentro de la VPC con
# ruteo hacia esa VPN.
# ─────────────────────────────────────────────────────────────────────────────
variable "vpc_subnet_ids" {
  description = "Subnets privadas con ruta hacia la VPN S2S, donde corre la Lambda."
  type        = list(string)
}

variable "vpc_security_group_ids" {
  description = "Security groups que permiten egress HTTPS hacia 10.32.4.82:8453 (API del directorio activo) via la VPN S2S."
  type        = list(string)
}

# ─────────────────────────────────────────────────────────────────────────────
# TAGGING
# ─────────────────────────────────────────────────────────────────────────────
variable "tags" {
  description = "Tags aplicados a todos los recursos."
  type        = map(string)
  default     = {}
}
