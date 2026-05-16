output "transcript_bucket_arn" { value = aws_s3_bucket.transcripts.arn }
output "transcript_bucket_name" { value = aws_s3_bucket.transcripts.bucket }
output "ingest_lambda_arn" { value = aws_lambda_function.ingest.arn }
output "ingest_lambda_name" { value = aws_lambda_function.ingest.function_name }
output "ingest_queue_arn" { value = aws_sqs_queue.ingest_queue.arn }
output "ingest_queue_url" { value = aws_sqs_queue.ingest_queue.url }
output "ingest_dlq_arn" { value = aws_sqs_queue.ingest_dlq.arn }
output "ingest_dlq_url" { value = aws_sqs_queue.ingest_dlq.url }
