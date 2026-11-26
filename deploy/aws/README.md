# AWS deployment reference (configuration supplied, not deployed)

SundaeVale's local demo remains the primary tested workflow. These templates describe a possible private AWS environment; they are not infrastructure-as-code and do not create AWS resources. No paid cloud service or Docker build is required to publish the source code. The S3 integration is real and tested offline with a fake object store and Botocore Stubber, not with a live bucket.

## Reference topology

```text
Employee browser -> restricted HTTPS application load balancer
                 -> Next.js ECS service (server-side API proxy)
                 -> private FastAPI ECS service (private DNS / service discovery)
                       |-> RDS PostgreSQL (jobs, extraction, reviews, audit)
                       |-> private S3 (original PDF writes / preview)
Single ECS worker -> RDS queued jobs -> S3 original -> Docling / OCR
                  -> optional external Groq API -> deterministic validation
                  -> RDS results -> employee review and approved-only export
GitHub Actions -> ECR images -> ECS services
ECS stdout/stderr -> CloudWatch Logs
```

The load balancer should expose only the frontend to a trusted internal network or an authenticated access layer. The current shared API-key protection authenticates the server-to-server hop; it is **not** employee authentication or role-based access control. The Next.js proxy must not be exposed to untrusted users. Use synthetic invoices for the demo, and treat employee identity, permissions, privacy, malicious-document isolation, database migrations, recovery and operational monitoring as further production work.

## Manual prerequisites (not created by this repository)

- Private S3 bucket, Block Public Access, SSE-S3, HTTPS-only policy, appropriate lifecycle/retention and access logging. Prefix `invoices/originals` must match the policy and tasks. The uploaded bytes and original reference are persisted independently; failure cleanup is best-effort, not a distributed transaction.
- RDS PostgreSQL in private subnets; security-group access only from the application and worker; use secrets and TLS verification. Start with a new database or apply `../migrations/001_widen_extraction_method.postgresql.sql` to an existing PostgreSQL database before enabling the longer automatic OCR method. `create_all()` is a development bootstrap, not a schema migration system. Bootstrap the API first before the worker.
- Place an official RDS CA bundle in `deploy/certs/rds-ca-bundle.pem` before building the backend image (the Dockerfile copies the directory). Configure `sslmode=verify-full` as in `rds.env.example`. The CA certificate is public, not a password. Verify its source and maintain rotation.
- ECR repositories for backend and frontend; GitHub `aws-publish` environment with an OIDC trust relationship scoped to this repository and environment, `AWS_PUBLISH_ROLE_ARN` secret and `AWS_REGION`, `ECR_BACKEND_REPOSITORY`, `ECR_FRONTEND_REPOSITORY` variables. The manual workflow publishes SHA-tagged images only; it does not deploy them. The publishing role needs scoped ECR push permissions separately from application roles.
- ECS cluster and services, Fargate networking, private API discovery/DNS, restricted HTTPS ALB and target group for frontend. Private tasks need access to ECR, S3, Logs and Secrets Manager through suitable endpoints or egress. Optional Groq inference additionally requires outbound HTTPS.
- Create CloudWatch log groups `/ecs/sundaevale/api`, `/ecs/sundaevale/worker`, `/ecs/sundaevale/frontend` with a retention period. ECS execution role needs ECR pull, CloudWatch log delivery and access to its specific Secrets Manager values (and KMS decrypt where required). The task role is separate. Attach the sample S3 policies to the respective API and worker roles after substituting the private bucket name. No credentials are stored in the task templates.
- Store the actual password-containing PostgreSQL connection string and private API key as Secrets Manager secret values. The templates default to **rules** mode. To enable hybrid or Groq, set `INVOICE_EXTRACTION_MODE` and `GROQ_MODEL` in the worker task, add a `GROQ_API_KEY` secret reference, and explicitly authorize the use of the external provider for the document data.

## Registration and operation (manual)

Replace every placeholder (`ACCOUNT_ID`, `REGION`, `GIT_SHA`, `PRIVATE_BUCKET_NAME`, `PRIVATE_API_DNS`, secret suffixes and role names). Set the correct ECR repository names. Confirm credentials, CPU/memory and networking in your AWS account. Register each task definition with `aws ecs register-task-definition --cli-input-json file://...`, and configure services with the AWS console or CLI. Only one worker replica is supported: stop the old worker before starting the replacement (no overlapping deployments). The job claim implementation has not been validated for concurrent workers; a crash can leave a processing job requiring operator recovery. Do not enable autoscaling of workers on this MVP. The example API/frontend health endpoints are shallow liveness checks, not dependency-aware readiness checks. Validate upload, processing, PDF preview, approval/export and logs before claiming any cloud deployment. No frontend employee login, TLS termination, ALB, service discovery, secrets, role trust, or CloudWatch alarms are provisioned automatically.

## Storage configuration

Local is the default. For S3, install `backend/requirements-aws.txt` and set the same `INVOICE_STORAGE_BACKEND=s3`, `INVOICE_S3_BUCKET` and `INVOICE_S3_PREFIX` on API and worker. Boto3 uses its standard credential chain; prefer roles. The DB stores `s3://bucket/prefix/uuid.pdf`. The worker downloads to a temporary directory and removes it after parsing. The API retrieves bounded bytes through its existing protected PDF route. Do not switch an existing local-storage DB to S3 without migrating its files and stored references; use a fresh DB for the S3 demonstration. No S3 data migration is included.
