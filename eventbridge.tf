###############################################################################
# eventbridge.tf
#
# Corre la ingesta diariamente a las 05:00 COT (10:00 UTC).
# Colombia no observa horario de verano, asi que el offset es constante
# todo el ano.
#
# cron expression: cron(0 10 * * ? *)
###############################################################################

resource "aws_cloudwatch_event_rule" "daily_schedule" {
  name                = "${local.name_prefix}-schedule"
  description         = "Dispara la ingesta del directorio activo a las 05:00 COT"
  schedule_expression = var.schedule_expression
  state               = var.schedule_enabled ? "ENABLED" : "DISABLED"

  tags = local.common_tags
}

resource "aws_cloudwatch_event_target" "lambda" {
  rule      = aws_cloudwatch_event_rule.daily_schedule.name
  target_id = "${local.name_prefix}-target"
  arn       = aws_lambda_function.directorio_activo.arn
}

resource "aws_lambda_permission" "allow_eventbridge" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.directorio_activo.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.daily_schedule.arn
}
