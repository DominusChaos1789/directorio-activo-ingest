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
