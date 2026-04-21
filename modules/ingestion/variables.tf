variable "environment" {}
variable "youtube_channel_id" {}
variable "ingest_schedule" { default = "cron(0 6 ? * MON *)" }
# youtube_api_key is NOT a variable — stored in SSM, fetched at Lambda runtime
