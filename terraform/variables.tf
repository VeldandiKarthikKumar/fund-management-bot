variable "aws_region"           { default = "ap-south-1" }
variable "environment"          { default = "prod" }
variable "broker"               { default = "angel_one" }  # "angel_one" or "zerodha"

# ── Angel One ─────────────────────────────────────────────────────────────
variable "angel_one_api_key"     { sensitive = true }
variable "angel_one_client_id"   {}
variable "angel_one_password"    { sensitive = true }
variable "angel_one_totp_secret" { sensitive = true }

# ── Zerodha (optional — only needed when broker = "zerodha") ──────────────
variable "zerodha_api_key" {
  sensitive = true
  default   = ""
}
variable "zerodha_api_secret" {
  sensitive = true
  default   = ""
}
variable "zerodha_redirect_url" { default = "" }

# ── Slack ─────────────────────────────────────────────────────────────────
variable "slack_bot_token"      { sensitive = true }
variable "slack_app_token"      { sensitive = true }
variable "slack_signing_secret" { sensitive = true }
variable "slack_trading_channel"{}

# ── Database ──────────────────────────────────────────────────────────────
variable "db_username"          { default = "fundbot" }
variable "db_password"          { sensitive = true }

# ── Fund settings ─────────────────────────────────────────────────────────
variable "fund_size_inr"        { default = "500000" }
variable "ecr_repo_name"        { default = "fund-management-bot" }
