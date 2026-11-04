output "app_url" {
  description = "Public HTTPS URL of the API."
  value       = "https://${azurerm_container_app.app.ingress[0].fqdn}"
}

output "registry_name" {
  description = "Container registry name (for `az acr login --name ...`)."
  value       = azurerm_container_registry.acr.name
}

output "registry_login_server" {
  description = "Registry host to tag and push images to."
  value       = azurerm_container_registry.acr.login_server
}

output "key_vault_name" {
  description = "Key Vault name (for `az keyvault secret set --vault-name ...`)."
  value       = azurerm_key_vault.kv.name
}

output "resource_group_name" {
  value = azurerm_resource_group.rg.name
}

output "container_app_name" {
  value = azurerm_container_app.app.name
}

output "pipeline_identity_name" {
  description = "Managed identity to select when creating the Azure DevOps service connection."
  value       = azurerm_user_assigned_identity.pipeline.name
}
