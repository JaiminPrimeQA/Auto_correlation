output "load_balancer_dns_name" {
  description = "Point domain_name (CNAME/alias) at this."
  value       = aws_lb.main.dns_name
}

output "ecr_repositories" {
  description = "Push the api, frontend and newman-runner images here, tagged image_tag."
  value       = { for k, repo in aws_ecr_repository.repo : k => repo.repository_url }
}

output "jobs_table" {
  value = aws_dynamodb_table.jobs.name
}

output "material_bucket" {
  value = aws_s3_bucket.material.bucket
}

output "job_queue_url" {
  value = aws_sqs_queue.jobs.url
}

output "ecs_cluster" {
  value = aws_ecs_cluster.main.name
}

output "alarm_topic_arn" {
  value = aws_sns_topic.alarms.arn
}

output "database_endpoint" {
  value = aws_db_instance.main.address
}

output "app_bucket" {
  description = "Analyses in progress (analyses/) and saved history (history/)."
  value       = aws_s3_bucket.app.bucket
}

output "smtp_password_secret_arn" {
  description = "Put the SMTP password here (when smtp_host is set)."
  value       = try(aws_secretsmanager_secret.smtp_password[0].arn, null)
}
