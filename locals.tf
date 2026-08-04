###############################################################################
# locals.tf
###############################################################################

locals {
  name_prefix = "${var.stack_id}-active-directory"

  function_name        = local.name_prefix
  token_parameter_name = "/${var.stack_id}/active-directory/api-token"
  token_cache_table    = "${local.name_prefix}-token-cache"
  dlq_name             = "${local.name_prefix}-dlq"
  log_group_name       = "/aws/lambda/${local.function_name}"

  common_tags = merge(var.tags, {
    Environment = var.environment
    Project     = "directorio-activo-ingest"
    ManagedBy   = "terraform"
  })
}
