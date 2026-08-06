# directorio-activo-ingest

Daily ingestion of the company's Active Directory (`directorio activo`) into
the data lake. A scheduled Lambda calls an internal on-prem API and drops the
raw JSON response into S3.

## Architecture

```
EventBridge Rule (cron 10:00 UTC = 05:00 COT, daily)
        │
        ▼
  Lambda: <stack_id>-active-directory   (VPC-attached, routes over S2S VPN)
        │
        ├─ 1. GET token from SSM Parameter Store (SecureString)
        ├─ 2. GET api-config from SSM Parameter Store (String, JSON)
        ├─ 3. Sync token into DynamoDB cache/history table
        ├─ 4. GET <base_url>?domains=<domains>  (from api-config)
        │        (on-prem host, reachable only via S2S VPN, 10.32.4.82)
        └─ 5. PUT JSON to S3
                 │
                 ▼
     s3://<landing_bucket>/funcionarios/directorio_activo/
        year=<YYYY>/month=<MM>/day=<DD>/directorio_activo_<UTC timestamp>.json

  Failed async invocations -> SQS DLQ (<stack_id>-active-directory-dlq)
```

## File structure

```
.
├── src/main.py            # Lambda handler (src.main.handler)
├── tests/                 # pytest unit tests (moto-mocked AWS)
├── versions.tf            # Terraform + provider requirements
├── variables.tf           # Input variables
├── locals.tf               # Computed names (stack_id-based)
├── data.tf                 # Data sources + IAM policy documents
├── iam.tf                  # Lambda execution role
├── dynamodb.tf              # Token cache/history table
├── lambda.tf                # Lambda function, log group, DLQ, packaging
├── eventbridge.tf           # Daily schedule + Lambda permission
├── outputs.tf
└── terraform.tfvars.example
```

## SSM parameters

Both parameters already exist and are **not created or managed by this
Terraform config** — they're provisioned externally. Terraform only computes
their expected paths (`locals.tf`) to grant the Lambda's IAM role
`ssm:GetParameter` on those two specific ARNs and to pass the paths in as
Lambda environment variables. If a path or key changes on the external side,
update `var.stack_id` (or the path pattern in `locals.tf` directly) to match
— Terraform won't create anything and won't drift, since there's no resource
here at all.

Expected paths, given `stack_id = "augusta-nexa-dev"`:

- `/augusta-nexa-dev/active-directory/api-token`
- `/augusta-nexa-dev/active-directory/api-config`

### Token — `/<stack_id>/active-directory/api-token` (SecureString)

The API token has no automatic rotation — it expires roughly every 6 months
and must be regenerated **manually** by whoever administers the AD export
API. This parameter is the single source of truth for the *current* token.

On every invocation, the Lambda reads the current SSM value and compares it
against the latest item cached in **DynamoDB**
(`<stack_id>-active-directory-token-cache`). If it changed (i.e. someone
rotated it), the Lambda writes a new `ACTIVE` version and flips the previous
one to `SUPERSEDED` — old versions are never deleted, giving you a full
rotation history for audits.

To rotate the token manually:

```bash
aws ssm put-parameter \
  --name "/<stack_id>/active-directory/api-token" \
  --type SecureString \
  --value "<new-token>" \
  --overwrite
```

The next scheduled run (or a manual `aws lambda invoke`) will pick it up and
record the new version in DynamoDB automatically.

### Config — `/<stack_id>/active-directory/api-config` (String, not secret)

Holds the operational, non-sensitive bits the Lambda needs — nothing here is
a credential, so it's a plain `String` parameter, not `SecureString`:

```json
{
  "s3_prefix": "funcionarios/directorio_activo",
  "base_url": "https://v-vsasocs01:8453/api/v2/users",
  "domains": ["ventasyservicios.net", "vys"]
}
```

To change the base URL, domains, or S3 prefix without touching Terraform or
the secret:

```bash
aws ssm put-parameter \
  --name "/<stack_id>/active-directory/api-config" \
  --type String \
  --value '{"s3_prefix":"funcionarios/directorio_activo","base_url":"https://v-vsasocs01:8453/api/v2/users","domains":["ventasyservicios.net","vys"]}' \
  --overwrite
```

The Lambda reads and parses this on every invocation; malformed JSON or
missing keys fail the run loudly (`ApiConfigError`) rather than silently
falling back to stale values.

## Schedule

**05:00 COT** (Colombia Time, UTC-5) = **10:00 UTC**, daily.

EventBridge cron expression: `cron(0 10 * * ? *)`

> Colombia does not observe daylight saving time, so this offset is constant
> year-round.

## Networking prerequisite

`v-vsasocs01` (the AD export API, port 8453) is an on-prem host reachable
only through the site-to-site VPN — resolved IP `10.32.4.82`. DevOps already
opened the Nexa firewall for that IP. For the Lambda to reach it:

- It must be deployed **inside the VPC** (`vpc_subnet_ids` /
  `vpc_security_group_ids`, both required variables — there is no default,
  deploying without them would silently fail to reach the API).
- The attached security group must allow **egress HTTPS to
  10.32.4.82:8453**.
- The subnets must have a route to the S2S VPN.

## Deploying

```bash
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars: stack_id, landing_bucket_name, vpc_subnet_ids, vpc_security_group_ids

terraform init
terraform plan
terraform apply
```

This assumes both SSM parameters already exist at the paths described in
[SSM parameters](#ssm-parameters) above — Terraform doesn't create them, only
grants the Lambda read access. If `terraform plan` shows the IAM policy ARNs
pointing at paths that don't exist yet, create them out-of-band before
`apply` (the Lambda will fail at runtime otherwise, not at plan/apply time —
`ssm:GetParameter` permissions can be granted on a nonexistent parameter path
without error).

## Testing

```bash
pip install -r requirements-dev.txt
pytest
```

Tests mock all AWS calls with `moto` (SSM/DynamoDB/S3) and mock the HTTP
call to the AD API — no real AWS credentials or network access needed.

## Testing in the AWS Console

[`tests/events/scheduled_event.json`](tests/events/scheduled_event.json) is
the same event shape EventBridge actually sends to trigger this Lambda (the
handler ignores its contents — the invocation is schedule-driven, not
payload-driven — but using the real shape catches anything that assumes
the wrong event structure).

1. Open the function in the Lambda console → **Test** tab.
2. **Create new event** → template `hello-world` → name it e.g.
   `scheduled-event` → replace the body with the contents of
   `tests/events/scheduled_event.json` → **Save**.
3. Click **Test**.

This is a real, unmocked invocation: it will read the actual SSM parameters,
write to the actual DynamoDB table, call the real on-prem API, and write to
the real S3 bucket. Before testing this way, make sure:

- Terraform has been applied (function, role, table).
- Both SSM parameters exist at the expected paths with real values (see
  [SSM parameters](#ssm-parameters)) — they're managed outside this repo.
- The function's VPC/subnets/security groups actually route to
  `10.32.4.82:8453` (see [Networking prerequisite](#networking-prerequisite))
  — otherwise the invocation will hang until `REQUEST_TIMEOUT_SECONDS` and
  then fail with `ApiRequestError`.

Check **CloudWatch Logs** (linked from the console's execution results) for
the `Invocacion recibida: source=... detail_type=... id=...` line the
handler logs on every run, followed by either the completion summary or the
specific exception (`TokenUnavailableError`, `ApiConfigError`, or
`ApiRequestError`) if something's misconfigured.

## Resources created

| Resource | Purpose |
|---|---|
| `aws_lambda_function` | Runs the ingestion (`src.main.handler`) |
| `aws_iam_role` + inline policy | Least-privilege execution role (scoped S3 prefix, one SSM parameter, one DynamoDB table, log group, DLQ, VPC ENI mgmt) |
| `aws_cloudwatch_log_group` | Lambda logs, retention configurable |
| `aws_dynamodb_table` | Token version cache/history |
| `aws_sqs_queue` (DLQ) | Captures failed async invocations |
| `aws_cloudwatch_event_rule` + target | Daily 05:00 COT trigger |
| `aws_lambda_permission` | Allows EventBridge to invoke the Lambda |
| `data.aws_s3_bucket` | References the **existing** landing bucket (not created here) |

Not created here: the SSM parameters (`api-token`, `api-config`) and the
landing bucket itself already exist — this config only reads/references
them, it never manages their lifecycle.

## Security notes

1. **No secrets in git or Terraform state values.** The API token lives in a
   pre-existing SSM parameter managed entirely outside this repo; Terraform
   never creates, reads, or writes its value — it only grants the Lambda
   role permission to read it at runtime.
2. IAM is least-privilege: S3 write access is scoped to
   `<landing_bucket>/funcionarios/directorio_activo/*` only, SSM read is
   scoped to just the two parameter ARNs (token + config), DynamoDB access
   is scoped to the one cache table.
3. TLS verification is on by default (`api_tls_verify = true`); only
   disable it if the internal CA truly isn't distributable to Lambda.
4. This repository is public — no hostnames, tokens, cookies, or other
   credentials from the original request were committed. `terraform.tfvars`
   (which would contain real subnet/SG IDs) is gitignored.

## Outputs

| Name | Description |
|---|---|
| `lambda_function_name` / `lambda_function_arn` | The deployed function |
| `lambda_role_arn` | Execution role ARN |
| `token_parameter_name` | Expected path of the pre-existing token parameter |
| `config_parameter_name` | Expected path of the pre-existing config parameter |
| `token_cache_table_name` | DynamoDB history table name |
| `dlq_url` | Dead-letter queue URL |
| `eventbridge_rule_arn` | Schedule rule ARN |
