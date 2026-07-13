variable "aws_region" {
  type        = string
  description = "Production AWS region. The pilot defaults to Malaysia."
  default     = "ap-southeast-5"
}

variable "project_name" {
  type    = string
  default = "duducar-signage-production"
}

variable "domain_name" {
  type    = string
  default = "duducaradmin.com"
}

variable "dashboard_hostname" {
  type    = string
  default = "marketing.duducaradmin.com"
}

variable "api_hostname" {
  type    = string
  default = "api.marketing.duducaradmin.com"
}

variable "container_image" {
  type        = string
  description = "Immutable ECR image URI including digest or release tag."
  default     = ""
}

variable "enable_services" {
  type        = bool
  description = "Enable only after images and secret values are present."
  default     = false
}

variable "required_app_version" {
  type    = string
  default = "0.1.0"
}

variable "play_integrity_project_number" {
  type        = string
  description = "Non-secret Google Cloud numeric project number."
  default     = ""
}

variable "smtp_host" {
  type        = string
  description = "Non-secret company SMTP hostname."
  default     = ""
}

variable "smtp_port" {
  type    = number
  default = 587
}

variable "default_from_email" {
  type    = string
  default = "no-reply@duducar.co"
}

variable "operations_email" {
  type        = string
  description = "Address receiving operational and budget notifications."
}

variable "monthly_budget_usd" {
  type        = number
  description = "USD equivalent of the RM500 ceiling; review the exchange rate."
  default     = 115
}
