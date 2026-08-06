###############################################################################
# alerting.tf
#
# Topico SNS para avisar cuando al token le quedan pocos dias antes de
# vencer (ver TOKEN_EXPIRY_WARNING_DAYS). No se crea ninguna suscripcion
# aca: suscribir un email/canal de Slack/etc. manualmente despues del
# primer apply (consola SNS o `aws sns subscribe`).
###############################################################################

resource "aws_sns_topic" "token_expiry" {
  name = local.alert_topic_name

  tags = local.common_tags
}
