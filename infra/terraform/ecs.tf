locals {
  image = { for k, repo in aws_ecr_repository.repo : k => "${repo.repository_url}:${var.image_tag}" }

  # Settings shared by the API and the worker (see backend/app/core/config.py).
  aws_backend_env = [
    { name = "B11_ENVIRONMENT", value = "production" },
    { name = "B11_JOB_BACKEND", value = "aws" },
    { name = "B11_AWS_REGION", value = var.region },
    { name = "B11_JOBS_TABLE", value = aws_dynamodb_table.jobs.name },
    { name = "B11_MATERIAL_BUCKET", value = aws_s3_bucket.material.bucket },
    { name = "B11_KMS_KEY_ID", value = aws_kms_key.main.arn },
    { name = "B11_JOB_QUEUE_URL", value = aws_sqs_queue.jobs.url },
    { name = "B11_MAX_CONCURRENT_JOBS_PER_OWNER", value = tostring(var.max_jobs_per_user) },
    { name = "B11_JMETER_MODE", value = "disabled" },
    # Shared durable state (persistence.tf): nothing lives on a task's disk.
    { name = "B11_DATABASE_HOST", value = aws_db_instance.main.address },
    { name = "B11_DATABASE_PORT", value = tostring(aws_db_instance.main.port) },
    { name = "B11_DATABASE_NAME", value = aws_db_instance.main.db_name },
    { name = "B11_DATABASE_USER", value = aws_db_instance.main.username },
    { name = "B11_SESSION_STORE", value = "s3" },
    { name = "B11_STATE_BUCKET", value = aws_s3_bucket.app.bucket },
    { name = "B11_HISTORY_BACKEND", value = "s3" },
    { name = "B11_HISTORY_BUCKET", value = aws_s3_bucket.app.bucket },
  ]

  # Injected by the execution role at task start. A changed secret reaches
  # running tasks only after they are replaced (force a new deployment).
  app_secrets = concat(
    [
      { name = "B11_DATABASE_PASSWORD", valueFrom = "${local.db_secret_arn}:password::" },
      { name = "B11_STATE_SIGNING_KEY", valueFrom = aws_secretsmanager_secret.state_signing_key.arn },
    ],
    [for arn in aws_secretsmanager_secret.smtp_password[*].arn : { name = "B11_SMTP_PASSWORD", valueFrom = arn }],
  )

  log_config = {
    for svc in ["api", "frontend", "worker", "runner"] : svc => {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.service[svc].name
        awslogs-region        = var.region
        awslogs-stream-prefix = svc
      }
    }
  }
}

resource "aws_ecs_cluster" "main" {
  name = local.name
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_cloudwatch_log_group" "service" {
  for_each          = toset(["api", "frontend", "worker", "runner"])
  name              = "/ecs/${local.name}/${each.key}"
  retention_in_days = var.log_retention_days
}

# --- API: stateless (analyses in S3, accounts in PostgreSQL), so it runs as
# several tasks across both AZs and deploys without downtime. ---

resource "aws_ecs_task_definition" "api" {
  family                   = "${local.name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.api_cpu
  memory                   = var.api_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.api.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  container_definitions = jsonencode([{
    name                   = "api"
    image                  = local.image["api"]
    essential              = true
    readonlyRootFilesystem = true
    portMappings           = [{ containerPort = 8000, protocol = "tcp" }]
    environment = concat(local.aws_backend_env, [
      { name = "B11_AUTH_MODE", value = "oidc" },
      { name = "B11_OIDC_ISSUER", value = var.oidc_issuer },
      { name = "B11_OIDC_AUDIENCE", value = var.oidc_audience },
      { name = "B11_CORS_ORIGINS", value = "https://${var.domain_name}" },
      { name = "B11_APP_BASE_URL", value = "https://${var.domain_name}" },
      { name = "B11_BILLING_PROVIDER", value = "disabled" },
      { name = "B11_COOKIE_SECURE", value = "true" },
      { name = "B11_TRUSTED_PROXY_HOPS", value = "1" },
      { name = "B11_SMTP_HOST", value = var.smtp_host },
      { name = "B11_SMTP_USERNAME", value = var.smtp_username },
      { name = "B11_MAIL_FROM_ADDRESS", value = var.mail_from_address },
    ])
    secrets          = local.app_secrets
    mountPoints      = [{ sourceVolume = "tmp", containerPath = "/tmp" }]
    stopTimeout      = 60
    logConfiguration = local.log_config["api"]
  }])
  volume {
    name = "tmp"
  }
}

resource "aws_ecs_service" "api" {
  name            = "api"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.api_desired_count
  launch_type     = "FARGATE"
  # Start replacements before stopping old tasks; roll back a failing release.
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  health_check_grace_period_seconds  = 60
  enable_execute_command             = false
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.api.id]
    assign_public_ip = false
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }
  depends_on = [aws_lb_listener.https]
}

# --- Web UI ---

resource "aws_ecs_task_definition" "frontend" {
  family                   = "${local.name}-frontend"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.execution.arn
  container_definitions = jsonencode([{
    name             = "frontend"
    image            = local.image["frontend"]
    essential        = true
    portMappings     = [{ containerPort = 3000, protocol = "tcp" }]
    logConfiguration = local.log_config["frontend"]
  }])
}

resource "aws_ecs_service" "frontend" {
  name                               = "frontend"
  cluster                            = aws_ecs_cluster.main.id
  task_definition                    = aws_ecs_task_definition.frontend.arn
  desired_count                      = 2
  launch_type                        = "FARGATE"
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.frontend.id]
    assign_public_ip = false
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.frontend.arn
    container_name   = "frontend"
    container_port   = 3000
  }
  depends_on = [aws_lb_listener.https]
}

# --- Worker: each task waits for one job, runs it, exits (fresh task per job) ---

resource "aws_ecs_task_definition" "worker" {
  family                   = "${local.name}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.worker.arn
  container_definitions = jsonencode([{
    name                   = "worker"
    image                  = local.image["api"]
    essential              = true
    readonlyRootFilesystem = true
    command                = ["python", "-m", "app.worker", "--once"]
    environment = concat(local.aws_backend_env, [
      { name = "B11_NEWMAN_RUNNER", value = "ecs" },
      { name = "B11_ECS_CLUSTER", value = aws_ecs_cluster.main.name },
      { name = "B11_RUNNER_TASK_DEFINITION", value = aws_ecs_task_definition.runner.family },
      { name = "B11_RUNNER_CONTAINER_NAME", value = "newman-runner" },
      { name = "B11_RUNNER_SUBNETS", value = join(",", aws_subnet.runner[*].id) },
      { name = "B11_RUNNER_SECURITY_GROUPS", value = aws_security_group.runner.id },
    ])
    secrets          = local.app_secrets
    mountPoints      = [{ sourceVolume = "tmp", containerPath = "/tmp" }]
    stopTimeout      = 120
    logConfiguration = local.log_config["worker"]
  }])
  volume {
    name = "tmp"
  }
}

resource "aws_ecs_service" "worker" {
  name            = "worker"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = 0
  launch_type     = "FARGATE"
  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.worker.id]
    assign_public_ip = false
  }
  lifecycle {
    ignore_changes = [desired_count] # owned by autoscaling
  }
}

resource "aws_appautoscaling_target" "worker" {
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.worker.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = 0
  max_capacity       = var.worker_max_tasks
}

# Scale out while jobs wait; scale in once the queue has been empty a while.
resource "aws_appautoscaling_policy" "worker_out" {
  name               = "${local.name}-worker-out"
  service_namespace  = aws_appautoscaling_target.worker.service_namespace
  resource_id        = aws_appautoscaling_target.worker.resource_id
  scalable_dimension = aws_appautoscaling_target.worker.scalable_dimension
  policy_type        = "StepScaling"
  step_scaling_policy_configuration {
    adjustment_type         = "ExactCapacity"
    cooldown                = 60
    metric_aggregation_type = "Maximum"
    step_adjustment {
      metric_interval_lower_bound = 0
      metric_interval_upper_bound = 2
      scaling_adjustment          = min(1, var.worker_max_tasks)
    }
    step_adjustment {
      metric_interval_lower_bound = 2
      metric_interval_upper_bound = 5
      scaling_adjustment          = min(2, var.worker_max_tasks)
    }
    step_adjustment {
      metric_interval_lower_bound = 5
      scaling_adjustment          = var.worker_max_tasks
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "jobs_waiting" {
  alarm_name          = "${local.name}-jobs-waiting"
  alarm_description   = "Scale workers out while execution jobs wait in the queue"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.jobs.name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  alarm_actions       = [aws_appautoscaling_policy.worker_out.arn]
}

resource "aws_appautoscaling_policy" "worker_in" {
  name               = "${local.name}-worker-in"
  service_namespace  = aws_appautoscaling_target.worker.service_namespace
  resource_id        = aws_appautoscaling_target.worker.resource_id
  scalable_dimension = aws_appautoscaling_target.worker.scalable_dimension
  policy_type        = "StepScaling"
  step_scaling_policy_configuration {
    adjustment_type         = "ExactCapacity"
    cooldown                = 300
    metric_aggregation_type = "Maximum"
    step_adjustment {
      metric_interval_upper_bound = 0
      scaling_adjustment          = 0
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "queue_idle" {
  alarm_name          = "${local.name}-queue-idle"
  alarm_description   = "Scale workers in when no job is waiting or running"
  evaluation_periods  = 15
  threshold           = 0
  comparison_operator = "LessThanOrEqualToThreshold"
  alarm_actions       = [aws_appautoscaling_policy.worker_in.arn]
  metric_query {
    id          = "in_flight"
    expression  = "visible + running"
    return_data = true
  }
  metric_query {
    id = "visible"
    metric {
      namespace   = "AWS/SQS"
      metric_name = "ApproximateNumberOfMessagesVisible"
      dimensions  = { QueueName = aws_sqs_queue.jobs.name }
      stat        = "Maximum"
      period      = 60
    }
  }
  metric_query {
    id = "running"
    metric {
      namespace   = "AWS/SQS"
      metric_name = "ApproximateNumberOfMessagesNotVisible"
      dimensions  = { QueueName = aws_sqs_queue.jobs.name }
      stat        = "Maximum"
      period      = 60
    }
  }
}

# --- Newman runner: started per run by the worker (RunTask); never a service ---

resource "aws_ecs_task_definition" "runner" {
  family                   = "${local.name}-newman-runner"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 1024
  memory                   = 2048
  execution_role_arn       = aws_iam_role.execution.arn
  # No task_role_arn on purpose: collection scripts must not reach AWS credentials.
  container_definitions = jsonencode([{
    name                   = "newman-runner"
    image                  = local.image["newman-runner"]
    essential              = true
    readonlyRootFilesystem = true
    user                   = "node"
    linuxParameters = {
      initProcessEnabled = true
      capabilities       = { drop = ["ALL"] }
    }
    mountPoints      = [{ sourceVolume = "tmp", containerPath = "/tmp" }]
    logConfiguration = local.log_config["runner"]
  }])
  volume {
    name = "tmp"
  }
}
