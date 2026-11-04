# Azure infrastructure (Terraform)

This deploys the sales assistant API to Azure Container Apps. All data in the project is synthetic.

| Resource | Purpose |
|---|---|
| Resource group `<prefix>-rg` | Holds everything below; deleting it removes the whole stack |
| Log Analytics workspace + Application Insights | Monitoring (`APPLICATIONINSIGHTS_CONNECTION_STRING`) |
| Container Registry (Basic) | Stores the Docker image |
| Key Vault (RBAC) | API keys. **Terraform holds no secret values**; you add them with the Azure CLI. You get *Key Vault Secrets Officer* |
| User-assigned managed identity | The app's identity: *AcrPull* on the registry, *Key Vault Secrets User* on the vault |
| Container Apps environment + app | Consumption plan, 0.25 vCPU / 0.5 GiB, **scales to zero**, max 1 replica (sessions are in memory) |

The app gets these environment variables:
- `LLM_PROVIDER`
- `KEY_VAULT_URL`
- `APPLICATIONINSIGHTS_CONNECTION_STRING` (as a Container Apps secret)
- `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT` and `AZURE_OPENAI_API_VERSION`
- `AZURE_CLIENT_ID`, so `DefaultAzureCredential` uses the managed identity

The Azure OpenAI resource itself is created outside Terraform and only referenced by its endpoint.

## Rough monthly cost (list prices; check the Azure pricing calculator for your region)

| Resource | Cost |
|---|---|
| Resource group, managed identity, role assignments | Free |
| Container Apps environment (Consumption) | No fixed cost |
| Container app (0.25 vCPU / 0.5 GiB, scale to zero) | **$0 while idle.** Each month, 180,000 vCPU-s, 360,000 GiB-s and 2 million requests are free, which is about 200 hours of active time at this size. After that, about $0.03 per active hour plus $0.40 per million requests |
| Container Registry, Basic | **About $5/month (about $0.17/day).** The only fixed cost; includes 10 GB of storage |
| Key Vault, Standard | About $0.03 per 10,000 operations; the app reads 3 secrets per cold start, so cents |
| Log Analytics + Application Insights | About $2.30–2.80 per GB ingested after **5 GB/month free**. Capped at 0.1 GB/day (`log_daily_quota_gb`), which stays inside the free allowance |
| Azure OpenAI `gpt-5-mini` (existing, not in Terraform) | Per token; fractions of a cent per chat message |

**Expected total: about $5/month,** almost all of it the registry, plus usage-based cents. Your $15 budget alert still applies.

## Apply (first time)

Run these from the repo root. They need `az login` to Azure for Students and Docker running.

```bash
cd infra
export ARM_SUBSCRIPTION_ID="$(az account show --query id -o tsv)"
cp terraform.tfvars.example terraform.tfvars      # then set azure_openai_endpoint to your endpoint

# 1) Create the infrastructure. The app starts with a public placeholder image.
terraform init
terraform plan -out tfplan
terraform apply tfplan

# 2) Put the API key into Key Vault. It's read from your .env and never typed or printed.
#    If you get "Forbidden", the new role is still propagating: wait a minute and retry.
set -a; source ../.env; set +a
az keyvault secret set --vault-name "$(terraform output -raw key_vault_name)" \
  --name azure-openai-api-key --value "$AZURE_OPENAI_API_KEY" --output none

# 3) Build the image for Azure (linux/amd64) and push it.
#    Build locally: ACR Tasks (`az acr build`) is often blocked on student subscriptions.
#    Run `python -m aquaassist.ingest` first if index/ is missing, so cost estimates work.
ACR_SERVER="$(terraform output -raw registry_login_server)"
az acr login --name "$(terraform output -raw registry_name)"
docker build --platform linux/amd64 -t "$ACR_SERVER/water-sales-assistant:v1" ..
docker push "$ACR_SERVER/water-sales-assistant:v1"

# 4) Switch the app to the real image. This creates a new revision, which reads the key from Key Vault.
terraform apply -var container_image=water-sales-assistant:v1

# 5) Check it. The first request after idling waits for a cold start of about 10–20 s.
curl "$(terraform output -raw app_url)/health"
```

To deploy a new version later: build and push with a new tag (`:v2`), then run `terraform apply -var container_image=water-sales-assistant:v2`. You can also set `container_image` in `terraform.tfvars`.

**With the Azure DevOps pipeline:** CI deploys images tagged with the build ID (`water-sales-assistant:<BuildId>`). Terraform doesn't know about those deploys. Before running `terraform apply` again, set `container_image` to the tag that is currently deployed, otherwise Terraform will roll the app back to the older image. To see the current image:

```bash
az containerapp show -n "$(terraform output -raw container_app_name)" -g "$(terraform output -raw resource_group_name)" --query "properties.template.containers[0].image" -o tsv
```

## Destroy

```bash
cd infra
export ARM_SUBSCRIPTION_ID="$(az account show --query id -o tsv)"
terraform destroy -var container_image=water-sales-assistant:v1
```

This deletes everything Terraform created, including the images in the registry. The Key Vault is purged, so the same names can be reused. Your Azure OpenAI resource in `rg-water-sales-assistant` isn't managed by Terraform and stays.

## Files

| File | Contents |
|---|---|
| `versions.tf` | Provider versions and Azure provider settings |
| `variables.tf` | Settings, including `prefix` and `location` |
| `main.tf` | All the resources |
| `outputs.tf` | `app_url`, `registry_name`, `key_vault_name` and others |
| `terraform.tfvars.example` | Example settings to copy |

State (`terraform.tfstate`) stays on your machine and is gitignored, because it contains generated values such as the Application Insights connection string.
