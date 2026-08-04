###############################################################################
# dynamodb.tf
#
# Historial/cache de versiones del token. Cada vez que el token en SSM
# cambia (rotacion manual), la Lambda escribe una version nueva ACTIVE y
# marca la anterior como SUPERSEDED. Nunca se borran versiones viejas.
###############################################################################

resource "aws_dynamodb_table" "token_cache" {
  name         = local.token_cache_table
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "token_scope"
  range_key    = "version"

  attribute {
    name = "token_scope"
    type = "S"
  }

  attribute {
    name = "version"
    type = "N"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = local.common_tags
}
