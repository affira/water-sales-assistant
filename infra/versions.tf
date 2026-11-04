terraform {
  required_version = ">= 1.6"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

# The subscription comes from ARM_SUBSCRIPTION_ID (see infra/README.md); authentication from `az login`.
provider "azurerm" {
  features {
    resource_group {
      # The resource group is dedicated to this stack, and Azure adds resources to it automatically
      # (e.g. the "Application Insights Smart Detection" action group), which otherwise block destroy.
      prevent_deletion_if_contains_resources = false
    }
    key_vault {
      # Destroy removes the vault completely, so the stack can be recreated with the same name.
      purge_soft_delete_on_destroy    = true
      recover_soft_deleted_key_vaults = true
    }
  }

  # Register only the resource providers this stack needs (Owner rights are enough for this).
  resource_provider_registrations = "none"
  resource_providers_to_register = [
    "Microsoft.App",
    "Microsoft.ContainerRegistry",
    "Microsoft.Insights",
    "Microsoft.KeyVault",
    "Microsoft.ManagedIdentity",
    "Microsoft.OperationalInsights",
  ]
}
