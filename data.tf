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
      "${data.aws_s3_bucket.landing.arn}/${var.s3_prefix}/*",
    ]
  }
}

data "aws_iam_policy_document" "lambda_ssm" {
  statement {
    sid    = "ReadApiTokenAndConfig"
    effect = "Allow"
    actions = [
      "ssm:GetParameter",
    ]
    resources = [
      "arn:${data.aws_partition.current.partition}:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter${local.token_parameter_name}",
      "arn:${data.aws_partition.current.partition}:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter${local.config_parameter_name}",
    ]
  }
}

data "aws_iam_policy_document" "lambda_dynamodb" {
  statement {
    sid    = "TokenCacheReadWrite"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:Query",
      "dynamodb:Scan",
    ]
    resources = [
      aws_dynamodb_table.token_cache.arn,
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
    data.aws_iam_policy_document.lambda_dynamodb.json,
    data.aws_iam_policy_document.lambda_dlq.json,
    data.aws_iam_policy_document.lambda_vpc.json,
  ]
}
