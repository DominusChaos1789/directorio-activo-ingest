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

output "token_parameter_name" {
  description = "Nombre del parametro SSM que guarda el token. Cargar el valor real manualmente despues del primer apply."
  value       = aws_ssm_parameter.api_token.name
}

output "config_parameter_name" {
  description = "Nombre del parametro SSM (String, no sensible) con s3_prefix/base_url/domains. Editable manualmente sin redeploy."
  value       = aws_ssm_parameter.api_config.name
}

output "token_cache_table_name" {
  description = "Nombre de la tabla DynamoDB con el historial de versiones del token."
  value       = aws_dynamodb_table.token_cache.name
}

output "dlq_url" {
  description = "URL de la dead-letter queue para invocaciones fallidas."
  value       = aws_sqs_queue.dlq.url
}

output "eventbridge_rule_arn" {
  description = "ARN de la regla de EventBridge que dispara la ingesta diaria."
  value       = aws_cloudwatch_event_rule.daily_schedule.arn
}
