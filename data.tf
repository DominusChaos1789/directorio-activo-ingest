###############################################################################
# data.tf
#
# Data sources y documentos de politica IAM. Sin resource blocks.
###############################################################################

# ─────────────────────────────────────────────────────────────────────────────
# AWS DATA SOURCES
# ─────────────────────────────────────────────────────────────────────────────
data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

# El landing bucket ya existe, solo lo referenciamos.
data "aws_s3_bucket" "landing" {
  bucket = var.landing_bucket_name
}

# ─────────────────────────────────────────────────────────────────────────────
# TRUST POLICY
# ─────────────────────────────────────────────────────────────────────────────
data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    sid     = "LambdaAssumeRole"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# LAMBDA EXECUTION POLICIES
# Cada statement group por separado para legibilidad, luego mergeados en
# lambda_combined via source_policy_documents. iam.tf solo attachea eso.
# ─────────────────────────────────────────────────────────────────────────────

data "aws_iam_policy_document" "lambda_logs" {
  statement {
    sid    = "WriteCloudWatchLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [
      "${aws_cloudwatch_log_group.lambda.arn}:*",
    ]
  }
}

data "aws_iam_policy_document" "lambda_s3" {
  statement {
    sid    = "WriteDirectorioActivoPrefix"
    effect = "Allow"
    actions = [
      "s3:PutObject",
    ]
    resources = [
      "${data.aws_s3_bucket.landing.arn}/${var.base_path}/*",
    ]
  }
}

data "aws_iam_policy_document" "lambda_ssm" {
  statement {
    sid    = "ReadApiConfig"
    effect = "Allow"
    actions = [
      "ssm:GetParameter",
    ]
    resources = [
      "arn:${data.aws_partition.current.partition}:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter${local.config_parameter_name}",
    ]
  }
}

# El nombre no incluye el sufijo random de 6 caracteres que Secrets Manager
# agrega al ARN real; el wildcard final lo cubre sin tener que conocerlo.
data "aws_iam_policy_document" "lambda_secrets" {
  statement {
    sid    = "ReadTokenSecret"
    effect = "Allow"
    actions = [
      "secretsmanager:GetSecretValue",
    ]
    resources = [
      "arn:${data.aws_partition.current.partition}:secretsmanager:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:secret:${local.secret_name}*",
    ]
  }
}

data "aws_iam_policy_document" "lambda_sns" {
  statement {
    sid    = "PublishTokenExpiryAlert"
    effect = "Allow"
    actions = [
      "sns:Publish",
    ]
    resources = [
      aws_sns_topic.token_expiry.arn,
    ]
  }
}

data "aws_iam_policy_document" "lambda_dlq" {
  statement {
    sid    = "SendToDeadLetterQueue"
    effect = "Allow"
    actions = [
      "sqs:SendMessage",
    ]
    resources = [
      aws_sqs_queue.dlq.arn,
    ]
  }
}

data "aws_iam_policy_document" "lambda_vpc" {
  statement {
    sid    = "ManageLambdaEni"
    effect = "Allow"
    actions = [
      "ec2:CreateNetworkInterface",
      "ec2:DescribeNetworkInterfaces",
      "ec2:DeleteNetworkInterface",
      "ec2:AssignPrivateIpAddresses",
      "ec2:UnassignPrivateIpAddresses",
    ]
    resources = ["*"]
  }
}

data "aws_iam_policy_document" "lambda_combined" {
  source_policy_documents = [
    data.aws_iam_policy_document.lambda_logs.json,
    data.aws_iam_policy_document.lambda_s3.json,
    data.aws_iam_policy_document.lambda_ssm.json,
    data.aws_iam_policy_document.lambda_secrets.json,
    data.aws_iam_policy_document.lambda_sns.json,
    data.aws_iam_policy_document.lambda_dlq.json,
    data.aws_iam_policy_document.lambda_vpc.json,
  ]
}
