###############################################################################
# lambda.tf
#
# Empaqueta src/main.py preservando la carpeta "src/" dentro del zip, para
# que el handler configurado (src.main.handler) resuelva via import normal
# de Python (import src.main -> .handler).
###############################################################################

data "archive_file" "lambda_package" {
  type        = "zip"
  output_path = "${path.module}/build/${local.function_name}.zip"

  source {
    content  = file("${path.module}/src/__init__.py")
    filename = "src/__init__.py"
  }

  source {
    content  = file("${path.module}/src/main.py")
    filename = "src/main.py"
  }
}

resource "aws_cloudwatch_log_group" "lambda" {
  name              = local.log_group_name
  retention_in_days = var.log_retention_days

  tags = local.common_tags
}

# DLQ para invocaciones asincronas fallidas (EventBridge invoca async).
resource "aws_sqs_queue" "dlq" {
  name                      = local.dlq_name
  message_retention_seconds = 1209600 # 14 dias, para poder investigar fallos

  tags = local.common_tags
}

resource "aws_lambda_function" "directorio_activo" {
  function_name = local.function_name
  description   = "Ingesta diaria del directorio activo hacia S3 (funcionarios/directorio_activo)."

  role    = aws_iam_role.lambda_exec.arn
  handler = "src.main.handler"
  runtime = "python3.12"

  filename         = data.archive_file.lambda_package.output_path
  source_code_hash = data.archive_file.lambda_package.output_base64sha256

  timeout     = var.lambda_timeout_seconds
  memory_size = var.lambda_memory_mb

  vpc_config {
    subnet_ids         = var.vpc_subnet_ids
    security_group_ids = var.vpc_security_group_ids
  }

  dead_letter_config {
    target_arn = aws_sqs_queue.dlq.arn
  }

  environment {
    variables = {
      CONFIG_PARAMETER_NAME     = local.config_parameter_name
      SECRET_NAME               = local.secret_name
      S3_BUCKET                 = data.aws_s3_bucket.landing.bucket
      ALERT_TOPIC_ARN           = aws_sns_topic.token_expiry.arn
      REQUEST_TIMEOUT_SECONDS   = tostring(var.api_request_timeout_seconds)
      API_TLS_VERIFY            = tostring(var.api_tls_verify)
      TOKEN_EXPIRY_WARNING_DAYS = tostring(var.token_expiry_warning_days)
    }
  }

  tags = local.common_tags

  depends_on = [
    aws_cloudwatch_log_group.lambda,
    aws_iam_role_policy.lambda_combined,
  ]
}
