variable "environment" {}
variable "youtube_channel_id" {}
variable "youtube_api_key" { sensitive = true }
variable "ingest_schedule" { default = "cron(0 6 ? * MON *)" }
