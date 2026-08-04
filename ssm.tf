###############################################################################
# ssm.tf
#
# Parametro SecureString que guarda el token vigente de la API del
# directorio activo. El valor real NUNCA se pone en Terraform ni en git:
# se carga manualmente por CLI/consola despues del primer apply, y se
# rota manualmente cada ~6 meses (el token no tiene rotacion automatica).
#
# lifecycle.ignore_changes evita que un futuro `apply` pise el valor real
# con el placeholder inicial.
###############################################################################

resource "aws_ssm_parameter" "api_token" {
  name        = local.token_parameter_name
  description = "Token de autenticacion de la API del directorio activo. Rotar manualmente cada 6 meses (ver README)."
  type        = "SecureString"
  value       = "REPLACE_ME_MANUALLY"

  tags = local.common_tags

  lifecycle {
    ignore_changes = [value]
  }
}

# Configuracion no sensible de la API (s3_prefix, base_url, domains) como
# JSON en un parametro String (no SecureString: no hay nada secreto aca).
# Terraform solo siembra el valor inicial (a partir de var.s3_prefix,
# var.api_base_url, var.api_domains); despues, operaciones puede editarlo
# directo en SSM para cambiar la URL/dominios/prefijo sin volver a aplicar
# Terraform. lifecycle.ignore_changes evita que un futuro apply lo pise.
resource "aws_ssm_parameter" "api_config" {
  name        = local.config_parameter_name
  description = "Configuracion (no sensible) de la API del directorio activo: s3_prefix, base_url, domains."
  type        = "String"
  value = jsonencode({
    s3_prefix = var.s3_prefix
    base_url  = var.api_base_url
    domains   = var.api_domains
  })

  tags = local.common_tags

  lifecycle {
    ignore_changes = [value]
  }
}
