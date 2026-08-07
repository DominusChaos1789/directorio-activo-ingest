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
        ├─ 1. GET config from SSM Parameter Store (SecureString, JSON)
        ├─ 2. GET token + expiration_date from Secrets Manager
        ├─ 3. If <= token_expiry_warning_days from expiring: publish to SNS
        ├─ 4. GET <base_url>?domains=<domains>  (from config)
        │        (on-prem host, reachable only via S2S VPN, 10.32.4.58)
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
├── alerting.tf              # SNS topic for token-expiry alerts
├── lambda.tf                # Lambda function, log group, DLQ, packaging
├── eventbridge.tf           # Daily schedule + Lambda permission
├── outputs.tf
└── terraform.tfvars.example
```

## Config (SSM) and credentials (Secrets Manager)

Both already exist and are **not created or managed by this Terraform
config** — they're provisioned externally. Terraform only computes their
expected names/paths (`locals.tf`) to grant the Lambda's IAM role read
access and to pass the paths in as environment variables. If a path changes
on the external side, update `var.stack_id` (or the pattern in `locals.tf`
directly) to match — there's no resource here to drift.

### Config — `/<stack_id>/active-directory/config` (SSM Parameter, SecureString)

Holds the operational bits the Lambda needs. It's a `SecureString`, so the
Lambda reads it with `WithDecryption=True`:

```json
{
  "base_path": "funcionarios/directorio_activo",
  "base_url": "https://10.32.4.58:8453/api/v2/users",
  "domains": ["ventasyservicios.net", "vys"]
}
```

To change the base URL, domains, or S3 path without touching Terraform or
the credential:

```bash
aws ssm put-parameter \
  --name "/<stack_id>/active-directory/config" \
  --type SecureString \
  --value '{"base_path":"funcionarios/directorio_activo","base_url":"https://10.32.4.58:8453/api/v2/users","domains":["ventasyservicios.net","vys"]}' \
  --overwrite
```

The Lambda reads and parses this on every invocation; malformed JSON or
missing keys fail the run loudly (`ApiConfigError`) rather than silently
falling back to stale values.

### Credentials — `/<stack_id>/active-directory/credentials` (Secrets Manager)

Holds the API token and the date it expires:

```json
{
  "api-token": "<token>",
  "expiration_date": "2026-10-31"
}
```

The token has no automatic rotation — it must be regenerated **manually**
by whoever administers the AD export API, then written back here:

```bash
aws secretsmanager put-secret-value \
  --secret-id "/<stack_id>/active-directory/credentials" \
  --secret-string '{"api-token":"<new-token>","expiration_date":"<YYYY-MM-DD>"}'
```

`expiration_date` must be `YYYY-MM-DD` — the Lambda fails loudly
(`TokenUnavailableError`) if it's missing or unparsable, since it drives the
expiry alert below.

## Token-expiry alerting (SNS)

On every invocation, the Lambda compares `expiration_date` (from the secret
above) against today. Once `token_expiry_warning_days` days (default **10**)
or fewer remain — or the token has already expired — it publishes a message
to the `<stack_id>-active-directory-token-expiry` SNS topic, in addition to
logging a `WARNING` in CloudWatch. This keeps firing on every scheduled run
until someone rotates the token.

**Terraform creates the topic but no subscription.** Subscribe an
email/Slack/PagerDuty integration to it manually after the first apply:

```bash
aws sns subscribe \
  --topic-arn "$(terraform output -raw alert_topic_arn)" \
  --protocol email \
  --notification-endpoint you@example.com
```

Publishing is best-effort: if SNS itself is unreachable, the Lambda logs the
error and still completes the ingestion — a broken alert channel shouldn't
block the day's data landing in S3.

## Schedule

**05:00 COT** (Colombia Time, UTC-5) = **10:00 UTC**, daily.

EventBridge cron expression: `cron(0 10 * * ? *)`

> Colombia does not observe daylight saving time, so this offset is constant
> year-round.

## Networking prerequisite

The AD export API (port 8453) is an on-prem host reachable only through the
site-to-site VPN — currently `10.32.4.58`. DevOps already opened the Nexa
firewall for it. For the Lambda to reach it:

- It must be deployed **inside the VPC** (`vpc_subnet_ids` /
  `vpc_security_group_ids`, both required variables — there is no default,
  deploying without them would silently fail to reach the API).
- The attached security group must allow **egress HTTPS to that IP:8453**.
- The subnet's route table must have a route to the S2S VPN covering it —
  narrow `/32` routes per on-prem host are the pattern used here.
- The API's hostname (if referenced instead of a raw IP) must be resolvable
  from inside the VPC — a private hostname with no DNS forwarding rule to
  the on-prem DNS servers will fail fast with an unusual, easy-to-misread
  error (`OSError: [Errno 16] Device or resource busy`) rather than a
  typical DNS failure. If you hit that, using the raw IP directly in
  `base_url` (as done here) sidesteps it.

## Deploying

```bash
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars: stack_id, landing_bucket_name, vpc_subnet_ids, vpc_security_group_ids

terraform init
terraform plan
terraform apply
```

This assumes the SSM parameter and Secrets Manager secret already exist at
the paths described [above](#config-ssm-and-credentials-secrets-manager) —
Terraform doesn't create them, only grants the Lambda read access. If
`terraform plan` shows the IAM policy ARNs pointing at paths that don't
exist yet, create them out-of-band before `apply` (the Lambda will fail at
runtime otherwise, not at plan/apply time).

After the first apply, subscribe to the SNS alert topic (see
[Token-expiry alerting](#token-expiry-alerting-sns) above).

## Testing

```bash
pip install -r requirements-dev.txt
pytest
```

Tests mock all AWS calls with `moto` (SSM/Secrets Manager/SNS/S3) and mock
the HTTP call to the AD API — no real AWS credentials or network access
needed.

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

This is a real, unmocked invocation: it will read the actual SSM parameter
and secret, call the real on-prem API, and write to the real S3 bucket.
Before testing this way, make sure:

- Terraform has been applied (function, role, SNS topic).
- The SSM parameter and Secrets Manager secret exist at the expected paths
  with real values (see [above](#config-ssm-and-credentials-secrets-manager))
  — they're managed outside this repo.
- The function's VPC/subnets/security groups actually route to the API host
  (see [Networking prerequisite](#networking-prerequisite)) — otherwise the
  invocation will hang until `REQUEST_TIMEOUT_SECONDS` and then fail with
  `ApiRequestError`.

Check **CloudWatch Logs** (linked from the console's execution results) for
the `Invocacion recibida: source=... detail_type=... id=...` line the
handler logs on every run, followed by either the completion summary or the
specific exception (`TokenUnavailableError`, `ApiConfigError`, or
`ApiRequestError`) if something's misconfigured.

## Resources created

| Resource | Purpose |
|---|---|
| `aws_lambda_function` | Runs the ingestion (`src.main.handler`) |
| `aws_iam_role` + inline policy | Least-privilege execution role (scoped S3 path, one SSM parameter, one secret, one SNS topic, log group, DLQ, VPC ENI mgmt) |
| `aws_cloudwatch_log_group` | Lambda logs, retention configurable |
| `aws_sns_topic` | Token-expiry alert topic (no subscription created) |
| `aws_sqs_queue` (DLQ) | Captures failed async invocations |
| `aws_cloudwatch_event_rule` + target | Daily 05:00 COT trigger |
| `aws_lambda_permission` | Allows EventBridge to invoke the Lambda |
| `data.aws_s3_bucket` | References the **existing** landing bucket (not created here) |

Not created here: the SSM config parameter, the Secrets Manager credential,
and the landing bucket itself already exist — this config only
reads/references them, it never manages their lifecycle.

## Security notes

1. **No secrets in git or Terraform state values.** The API token lives in
   a pre-existing Secrets Manager secret managed entirely outside this repo;
   Terraform never creates, reads, or writes its value — it only grants the
   Lambda role permission to read it at runtime.
2. IAM is least-privilege: S3 write access is scoped to
   `<landing_bucket>/funcionarios/directorio_activo/*` only, SSM read is
   scoped to the single config parameter ARN, Secrets Manager read is
   scoped to the single credentials secret (by name prefix, since Secrets
   Manager appends a random suffix to the real ARN), SNS publish is scoped
   to the one alert topic.
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
| `config_parameter_name` | Expected path of the pre-existing SSM config parameter |
| `secret_name` | Expected name of the pre-existing Secrets Manager credential |
| `alert_topic_arn` | SNS topic ARN — subscribe your alert channel to this |
| `dlq_url` | Dead-letter queue URL |
| `eventbridge_rule_arn` | Schedule rule ARN |
