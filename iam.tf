###############################################################################
# iam.tf
###############################################################################

resource "aws_iam_role" "lambda_exec" {
  name               = "${local.name_prefix}-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
  description        = "Execution role for the ${local.function_name} Lambda"

  tags = local.common_tags
}

resource "aws_iam_role_policy" "lambda_combined" {
  name   = "${local.name_prefix}-policy"
  role   = aws_iam_role.lambda_exec.id
  policy = data.aws_iam_policy_document.lambda_combined.json
}
