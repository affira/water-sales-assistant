variable "prefix" {
  description = "Short name prefix for every resource (lowercase letters, digits and hyphens)."
  type        = string
  default     = "norvale"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,11}$", var.prefix))
    error_message = "prefix must be 3-12 characters: lowercase letters, digits and hyphens, starting with a letter."
  }
}

variable "location" {
  description = "Azure region. Azure for Students only allows some regions (here: belgiumcentral, francecentral, germanywestcentral, norwayeast, polandcentral)."
  type        = string
  default     = "norwayeast"
}

variable "llm_provider" {
  description = "Value of LLM_PROVIDER in the app: anthropic, azure_openai or foundry_claude."
  type        = string
  default     = "azure_openai"

  validation {
    condition     = contains(["anthropic", "azure_openai", "foundry_claude"], var.llm_provider)
    error_message = "llm_provider must be anthropic, azure_openai or foundry_claude."
  }
}

variable "azure_openai_endpoint" {
  description = "Model endpoint, e.g. https://<resource>.openai.azure.com (not a secret; the API key goes in Key Vault)."
  type        = string
  default     = ""
}

variable "azure_openai_deployment" {
  description = "Azure OpenAI deployment name."
  type        = string
  default     = "gpt-5-mini"
}

variable "container_image" {
  description = "Image in the registry as name:tag, e.g. water-sales-assistant:v1. Empty = a public placeholder image, used for the first apply before anything has been pushed."
  type        = string
  default     = ""
}

variable "cpu" {
  description = "vCPU per replica. 0.25 is the smallest Container Apps size."
  type        = number
  default     = 0.25
}

variable "memory" {
  description = "Memory per replica; must match the CPU size (0.25 -> 0.5Gi, 0.5 -> 1Gi)."
  type        = string
  default     = "0.5Gi"
}

variable "log_daily_quota_gb" {
  description = "Hard daily cap on log ingestion (GB). 0.1 GB x 31 days stays inside the 5 GB/month free allowance. -1 = no cap."
  type        = number
  default     = 0.1
}

variable "tags" {
  description = "Extra tags for every resource."
  type        = map(string)
  default     = {}
}
