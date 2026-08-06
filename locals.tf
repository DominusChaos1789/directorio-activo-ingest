###############################################################################
# locals.tf
###############################################################################

locals {
  name_prefix = "${var.stack_id}-active-directory"

  function_name         = local.name_prefix
  config_parameter_name = "/${var.stack_id}/active-directory/config"
  secret_name           = "/${var.stack_id}/active-directory/credentials"
  alert_topic_name      = "${local.name_prefix}-token-expiry"
  dlq_name              = "${local.name_prefix}-dlq"
  log_group_name        = "/aws/lambda/${local.function_name}"

  common_tags = merge(var.tags, {
    Environment = var.environment
    Project     = "directorio-activo-ingest"
    ManagedBy   = "terraform"
  })
}
