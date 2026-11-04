# Azure infrastructure for the Norvale sales assistant (synthetic demo data).
# No secret values are defined here: API keys are added to Key Vault with the Azure CLI.

data "azurerm_client_config" "current" {}

# Registry and vault names must be globally unique.
resource "random_string" "suffix" {
  length  = 5
  upper   = false
  special = false
}

locals {
  suffix = random_string.suffix.result
  tags   = merge({ project = "water-sales-assistant", managed_by = "terraform" }, var.tags)

  # Until an image has been pushed, run a small public placeholder (it listens on port 80).
  use_registry_image = var.container_image != ""
  image              = local.use_registry_image ? "${azurerm_container_registry.acr.login_server}/${var.container_image}" : "mcr.microsoft.com/k8se/quickstart:latest"
  target_port        = local.use_registry_image ? 8000 : 80
}

resource "azurerm_resource_group" "rg" {
  name     = "${var.prefix}-rg"
  location = var.location
  tags     = local.tags
}

# --- monitoring ------------------------------------------------------------------------

resource "azurerm_log_analytics_workspace" "logs" {
  name                = "${var.prefix}-logs"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  sku                 = "PerGB2018" # pay per GB ingested; the first 5 GB/month are free
  retention_in_days   = 30          # retention up to 31 days is included in the ingestion price
  daily_quota_gb      = var.log_daily_quota_gb
  tags                = local.tags
}

resource "azurerm_application_insights" "appi" {
  name                = "${var.prefix}-appi"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  workspace_id        = azurerm_log_analytics_workspace.logs.id # data is stored (and billed) in the workspace
  application_type    = "web"
  tags                = local.tags
}

# --- container registry ------------------------------------------------------------------

resource "azurerm_container_registry" "acr" {
  name                = "${replace(var.prefix, "-", "")}acr${local.suffix}"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  sku                 = "Basic"
  admin_enabled       = false # pulls use the managed identity, pushes use `az acr login`
  tags                = local.tags
}

# --- key vault -----------------------------------------------------------------------------

resource "azurerm_key_vault" "kv" {
  name                       = "${var.prefix}-kv-${local.suffix}"
  location                   = azurerm_resource_group.rg.location
  resource_group_name        = azurerm_resource_group.rg.name
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = true  # access via Azure roles, not access policies
  soft_delete_retention_days = 7     # the minimum
  purge_protection_enabled   = false # allows `terraform destroy` to remove it completely
  tags                       = local.tags
}

# Lets the signed-in user (you) add and update secrets with the Azure CLI.
resource "azurerm_role_assignment" "me_kv_secrets_officer" {
  scope                = azurerm_key_vault.kv.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}

# --- identity used by the app ---------------------------------------------------------------

resource "azurerm_user_assigned_identity" "app" {
  name                = "${var.prefix}-app-identity"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  tags                = local.tags
}

resource "azurerm_role_assignment" "app_acr_pull" {
  scope                = azurerm_container_registry.acr.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "app_kv_secrets_user" {
  scope                = azurerm_key_vault.kv.id
  role_definition_name = "Key Vault Secrets User" # read-only access to secret values
  principal_id         = azurerm_user_assigned_identity.app.principal_id
  principal_type       = "ServicePrincipal"
}

# --- container apps --------------------------------------------------------------------------

resource "azurerm_container_app_environment" "env" {
  name                       = "${var.prefix}-env"
  location                   = azurerm_resource_group.rg.location
  resource_group_name        = azurerm_resource_group.rg.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.logs.id # Consumption plan: no fixed cost
  tags                       = local.tags
}

resource "azurerm_container_app" "app" {
  name                         = "${var.prefix}-app"
  container_app_environment_id = azurerm_container_app_environment.env.id
  resource_group_name          = azurerm_resource_group.rg.name
  revision_mode                = "Single"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  dynamic "registry" {
    for_each = local.use_registry_image ? [1] : []
    content {
      server   = azurerm_container_registry.acr.login_server
      identity = azurerm_user_assigned_identity.app.id
    }
  }

  # Generated by Azure (not one of your API keys); kept as an app secret so it isn't shown in plain text.
  secret {
    name  = "appinsights-connection-string"
    value = azurerm_application_insights.appi.connection_string
  }

  ingress {
    external_enabled = true
    target_port      = local.target_port
    transport        = "auto"
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 0 # scale to zero when idle: no compute cost
    max_replicas = 1 # sessions are kept in memory, so one replica (see aquaassist/sessions.py)

    http_scale_rule {
      name                = "http"
      concurrent_requests = "10"
    }

    container {
      name   = "api"
      image  = local.image
      cpu    = var.cpu
      memory = var.memory

      env {
        name  = "LLM_PROVIDER"
        value = var.llm_provider
      }
      env {
        name  = "KEY_VAULT_URL"
        value = azurerm_key_vault.kv.vault_uri
      }
      env {
        # Tells DefaultAzureCredential which (user-assigned) managed identity to use.
        name  = "AZURE_CLIENT_ID"
        value = azurerm_user_assigned_identity.app.client_id
      }
      env {
        name        = "APPLICATIONINSIGHTS_CONNECTION_STRING"
        secret_name = "appinsights-connection-string"
      }
      env {
        name  = "AZURE_OPENAI_ENDPOINT"
        value = var.azure_openai_endpoint
      }
      env {
        name  = "AZURE_OPENAI_DEPLOYMENT"
        value = var.azure_openai_deployment
      }
      env {
        name  = "AZURE_OPENAI_API_VERSION"
        value = "v1"
      }

      dynamic "readiness_probe" {
        for_each = local.use_registry_image ? [1] : []
        content {
          transport = "HTTP"
          port      = 8000
          path      = "/health"
        }
      }

      dynamic "liveness_probe" {
        for_each = local.use_registry_image ? [1] : []
        content {
          transport        = "HTTP"
          port             = 8000
          path             = "/health"
          initial_delay    = 15 # start-up reads Key Vault first
          interval_seconds = 30
        }
      }
    }
  }

  # The identity must be allowed to pull images and read secrets before the app starts.
  depends_on = [
    azurerm_role_assignment.app_acr_pull,
    azurerm_role_assignment.app_kv_secrets_user,
  ]
}
