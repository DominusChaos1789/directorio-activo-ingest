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
  description = "Ruta del parametro SSM (SecureString, pre-existente) que la Lambda lee para el token."
  value       = local.token_parameter_name
}

output "config_parameter_name" {
  description = "Ruta del parametro SSM (String, pre-existente) que la Lambda lee para s3_prefix/base_url/domains."
  value       = local.config_parameter_name
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
