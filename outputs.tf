###############################################################################
# outputs.tf
###############################################################################

output "lambda_function_name" {
  description = "Nombre de la funcion Lambda."
  value       = aws_lambda_function.directorio_activo.function_name
}

output "lambda_function_arn" {
  description = "ARN de la funcion Lambda."
  value       = aws_lambda_function.directorio_activo.arn
}

output "lambda_role_arn" {
  description = "ARN del rol de ejecucion de la Lambda."
  value       = aws_iam_role.lambda_exec.arn
}

output "config_parameter_name" {
  description = "Ruta del parametro SSM (String, pre-existente) que la Lambda lee para base_path/base_url/domains."
  value       = local.config_parameter_name
}

output "secret_name" {
  description = "Nombre del secreto de Secrets Manager (pre-existente) con el token y su expiration_date."
  value       = local.secret_name
}

output "alert_topic_arn" {
  description = "ARN del topico SNS de alerta de expiracion del token. Suscribir un email/canal manualmente."
  value       = aws_sns_topic.token_expiry.arn
}

output "dlq_url" {
  description = "URL de la dead-letter queue para invocaciones fallidas."
  value       = aws_sqs_queue.dlq.url
}

output "eventbridge_rule_arn" {
  description = "ARN de la regla de EventBridge que dispara la ingesta diaria."
  value       = aws_cloudwatch_event_rule.daily_schedule.arn
}
