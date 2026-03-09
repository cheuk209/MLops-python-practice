# Azure Container Apps (ACA) -- SRE/DevOps Deep-Dive

## 1. Architecture Under the Hood

### The Managed Kubernetes Layer

Azure Container Apps is a **fully managed serverless container platform built on top of AKS (Azure Kubernetes Service)**. When you create a Container App Environment, Azure provisions a hidden, abstracted AKS cluster that you never see or interact with directly. Microsoft manages the entire control plane, worker nodes, patching, and upgrades.

**Key infrastructure components:**

- **Kubernetes (AKS)**: The underlying orchestration layer. You get zero `kubectl` access. The AKS cluster is in a Microsoft-managed subscription, not yours. The `MC_` prefixed resource group that Azure auto-creates in your subscription contains the load balancers, NSGs, and public IPs -- similar to how AKS creates node resource groups.
- **Envoy Proxy**: Serves as the **edge ingress proxy** and handles TLS termination, traffic splitting between revisions, load balancing, HTTP/1.1 and HTTP/2 support with automatic upgrade detection. The environment starts with 2 ingress proxy instances and can scale up to 10, each allocated up to 1 vCPU and 2 GB memory. Envoy also handles mTLS for inter-app communication within the environment.
- **KEDA (Kubernetes Event-Driven Autoscaling)**: Powers all scaling. ACA exposes KEDA through a simplified declarative model. You can use any [ScaledObject-based KEDA scaler](https://keda.sh/docs/latest/scalers/). Default polling interval is 30 seconds, cool-down period is 300 seconds.
- **Dapr (Distributed Application Runtime)**: Optional sidecar for service invocation, state management, pub/sub messaging, and bindings. When enabled, Dapr runs as a sidecar alongside your container.

### Relationship: Container App Environment to AKS Cluster

One Container App Environment maps to **one underlying AKS cluster**. All container apps within that environment share the same cluster, networking, and Log Analytics workspace. This is analogous to how multiple Kubernetes namespaces share a cluster, though the mapping is not 1:1 with K8s namespaces -- the isolation model is managed by the platform.

### Traffic Flow

1. Incoming HTTP/S traffic hits Azure's edge load balancers
2. Traffic routes through the Envoy-based ingress layer (TLS terminated here)
3. Envoy forwards to the correct container replica based on revision traffic weights
4. App-to-app calls via FQDN go through the edge ingress proxy (TLS encrypted)
5. App-to-app calls via internal app name go directly (still TLS encrypted)

Sources:

- [Azure Container Apps vs AKS Decision Guide](https://developersvoice.com/blog/azure/azure_container_apps_vs_aks_framework/)
- [Architecting with Azure Container Apps](https://mikerodionov.medium.com/architecting-with-azure-container-apps-aca-4d9658356a78)
- [Networking in Azure Container Apps | Microsoft Learn](https://learn.microsoft.com/en-us/azure/container-apps/networking)

---

## 2. Container App Environments

### What Is an Environment?

A Container App Environment is the **secure boundary** around a group of container apps. It provides:

- A shared virtual network
- A shared Log Analytics workspace
- A shared Dapr configuration scope
- Internal DNS for app-to-app communication
- A common domain suffix: `<UNIQUE_IDENTIFIER>.<REGION>.azurecontainerapps.io`

Think of it as a **cluster + namespace + ingress controller** bundled together. All apps in the same environment can discover and communicate with each other via internal DNS.

### Environment Types (v1 vs v2)

As of May 2025, Microsoft **removed the "Consumption-only" (v1) environment** from the Azure Portal. All new environments are **Workload Profiles (v2)** environments, which provide maximum flexibility.


| Environment Type                | Plan Types                     | Subnet Minimum | UDR Support | NAT Gateway |
| ------------------------------- | ------------------------------ | -------------- | ----------- | ----------- |
| Workload Profiles (v2, default) | Consumption + Dedicated + Flex | `/27`          | Yes         | Yes         |
| Consumption Only (v1, legacy)   | Consumption only               | `/23`          | Limited     | No          |

### Workload Profile Types

**Consumption Profile** (always included by default):

- Serverless, scale-to-zero, pay-per-use
- vCPU range: 0.25-4, Memory: 0.5-8 GiB per replica
- GPU variant available (NVIDIA T4, A100)

**Dedicated Profiles** (reserved compute):


| Profile         | vCPU  | Memory      | Use Case         |
| --------------- | ----- | ----------- | ---------------- |
| D4              | 4     | 16 GiB      | General purpose  |
| D8              | 8     | 32 GiB      | General purpose  |
| D16             | 16    | 64 GiB      | General purpose  |
| D32             | 32    | 128 GiB     | General purpose  |
| E4-E32          | 4-32  | 32-256 GiB  | Memory optimized |
| NC24/48/96-A100 | 24-96 | 220-880 GiB | GPU (A100)       |

Key facts about Dedicated profiles:

- Billed per node instance (not per replica)
- Multiple replicas are bin-packed onto nodes
- Node scaling is driven by unschedulable replicas, not CPU-time metrics
- Maximum 21 instances across all workload profiles
- Runtime reserves some resources on each node for system overhead

**Flexible Profile** (preview):

- Consumption-style billing + dedicated single-tenant compute
- Requires `/25` subnet minimum
- Cannot scale to zero
- Available in limited regions (North Europe included)

### Auto-Created Infrastructure

When you deploy an environment into your own VNet, Azure creates a `MC_` prefixed resource group containing:

- 2 load balancers
- Network security groups
- Public IPs (for external environments)

This is the AKS node resource group pattern. You cannot currently customize the name of this resource group (feature request exists).

**Terraform gotcha**: Azure auto-populates `infrastructure_resource_group_name` on the CAE resource. If you don't set it in your config, Terraform sees `"ME_..." -> null` on every plan and **forces replacement** (~18 min destroy+recreate). Fix: `lifecycle { ignore_changes = [infrastructure_resource_group_name] }`.

Sources:

- [Workload profiles in Azure Container Apps | Microsoft Learn](https://learn.microsoft.com/en-us/azure/container-apps/workload-profiles-overview)
- [Azure Container Apps environments | Azure Docs](https://docs.azure.cn/en-us/container-apps/environment)
- [Azure Container Apps Plan Types | Microsoft Learn](https://learn.microsoft.com/en-us/azure/container-apps/plans)
- [MC_ resource group issue #207](https://github.com/microsoft/azure-container-apps/issues/207)

---

## 3. Networking Deep-Dive

### VNet Integration

- You provide a **dedicated subnet** exclusively for the Container App environment -- no other resources can share it
- Once created with a VNet (or without), the network type **cannot be changed**
- Default (no custom VNet): publicly accessible, can only communicate with internet-accessible endpoints
- Custom VNet enables: NSGs, App Gateway integration, Azure Firewall, private endpoints, access to resources in your VNet

### Subnet Sizing Requirements


| Environment Type       | Minimum CIDR    | Infrastructure IPs Reserved | IP Model                                                              |
| ---------------------- | --------------- | --------------------------- | --------------------------------------------------------------------- |
| Workload Profiles (v2) | `/27` (32 IPs)  | 11 IPs for infrastructure   | Dedicated profiles: 1 IP per node; Consumption: 1 IP per ~10 replicas |
| Consumption Only (v1)  | `/23` (512 IPs) | 60 IPs minimum, up to 256   | More aggressive reservation                                           |
| Flexible Profile       | `/25`           | TBD                         | Per replica                                                           |

**Critical sizing consideration**: During single-revision-mode deployments, the required address space **doubles temporarily** for zero-downtime deployments. Plan for 2x the expected IP usage.

### Internal vs External Ingress

**External Environment:**

- Virtual IP on a public-facing IP address
- Inbound traffic routes through the public IP in the `MC_` managed resource group (not through your subnet)
- NSG-based inbound filtering on external environments is **not supported** because traffic bypasses your subnet
- You can disable public network access and use private endpoints instead

**Internal Environment:**

- No public endpoints; uses an Azure Internal Load Balancer (ILB)
- VIP mapped to an internal IP from your VNet
- Requires Azure Private DNS zones for FQDN resolution
- Public network access cannot be changed to enabled

**Per-App Ingress Settings:**

- `External`: accepts traffic from public internet AND internal environment
- `Internal`: only accepts traffic from within the environment
- `Disabled`: no ingress at all

### Envoy and mTLS

- Inbound TLS terminates at the Envoy edge proxy
- Traffic **within the environment** between ingress proxy and apps is TLS encrypted with private certificates
- App A calling App B via FQDN: traffic goes through the edge proxy, TLS encrypted
- App A calling App B via app name directly: traffic goes directly, still TLS encrypted
- Full mTLS can be enabled via `peerTrafficConfiguration` encryption settings

### DNS Resolution Within an Environment

- All apps in the same environment get automatic internal DNS resolution
- Default suffix: `<app-name>.<env-unique-id>.<region>.azurecontainerapps.io`
- Custom DNS suffix is supported
- For internal environments, Azure registers internal DNS records in a system-managed private DNS zone
- If using custom DNS servers: **must forward unresolved queries to `168.63.129.16`** (Azure DNS)
- Never block `168.63.129.16` in NSG rules

### NSG Rules (Workload Profiles Environment)

**Inbound (only applies to VNet-routed traffic, not public internet):**


| Protocol | Source            | Dest Ports  | Purpose                                       |
| -------- | ----------------- | ----------- | --------------------------------------------- |
| TCP      | Client IPs        | 80, 31080   | HTTP traffic (31080 = edge proxy behind ILB)  |
| TCP      | Client IPs        | 443, 31443  | HTTPS traffic (31443 = edge proxy behind ILB) |
| TCP      | AzureLoadBalancer | 30000-32767 | Health probes                                 |

**Outbound (required):**


| Protocol | Destination                | Dest Ports    | Purpose                                                |
| -------- | -------------------------- | ------------- | ------------------------------------------------------ |
| TCP      | MicrosoftContainerRegistry | 443           | System container pulls                                 |
| TCP      | AzureFrontDoor.FirstParty  | 443           | MCR dependency                                         |
| Any      | Container app subnet       | *             | Intra-environment communication                        |
| TCP      | AzureActiveDirectory       | 443           | If using managed identity                              |
| TCP      | AzureMonitor               | 443           | If using Azure Monitor                                 |
| TCP+UDP  | 168.63.129.16              | 53            | Azure DNS                                              |
| TCP      | Your ACR                   | Registry port | Image pulls (use private endpoints to avoid this rule) |
| TCP      | Storage.\<Region\>         | 443           | If using ACR                                           |

**Important**: For ACR with NSGs, create a **private endpoint on your ACR** so Container Apps pull images through VNet -- no NSG rule needed for ACR with private endpoints.

### UDR Support

- Fully supported in Workload Profiles environments
- Can route all egress through Azure Firewall
- Not supported (or very limited) in legacy Consumption-only environments
- NAT Gateway integration supported in Workload Profiles environments

Sources:

- [Networking in Azure Container Apps | Microsoft Learn](https://learn.microsoft.com/en-us/azure/container-apps/networking)
- [Securing a virtual network in Azure Container Apps | Microsoft Learn](https://learn.microsoft.com/en-us/azure/container-apps/firewall-integration)
- [Configuring virtual networks | Microsoft Learn](https://learn.microsoft.com/en-us/azure/container-apps/custom-virtual-networks)
- [Minimal NSG for Container Apps | Rootknecht.net](https://rootknecht.net/blog/azure-container-app-nsg/)
- [Networking Landing Zone Accelerator | Microsoft Learn](https://learn.microsoft.com/en-us/azure/cloud-adoption-framework/scenarios/app-platform/container-apps/networking)

---

## 4. IAM and RBAC

### Built-in Roles for Container Apps

Azure provides the following Container Apps-specific built-in roles:


| Role                           | ID                                     | Description                                                |
| ------------------------------ | -------------------------------------- | ---------------------------------------------------------- |
| **Container Apps Contributor** | `358470bc-b998-42bd-ab17-a7e34c199c0f` | Full management of Container Apps (create, update, delete) |

For broader access, you can also use:

- **Contributor**: Full resource management (includes Container Apps)
- **Reader**: Read-only access to all resources
- **Custom Roles**: Define fine-grained `Microsoft.App/*` permissions

**Best practice for role segmentation:**


| Persona                 | Recommended Roles                                                                                             |
| ----------------------- | ------------------------------------------------------------------------------------------------------------- |
| SRE/Platform team       | Container Apps Contributor + Network Contributor + ACR Contributor on environment RG                          |
| Developers              | Custom role with`Microsoft.App/containerApps/read`, `Microsoft.App/containerApps/write`, Log Analytics Reader |
| CI/CD service principal | Container Apps Contributor + AcrPush on ACR + Reader on environment                                           |
| Read-only operations    | Reader + Log Analytics Reader                                                                                 |

### Managed Identities

**System-Assigned Identity:**

- Tied to the container app lifecycle
- One per container app
- Automatically deleted when the app is deleted
- Best for single-resource, independent workloads

**User-Assigned Identity:**

- Standalone Azure resource, independent lifecycle
- Can be shared across multiple container apps
- Best for multi-resource scenarios (e.g., shared ACR pull identity)
- A container app can have multiple user-assigned identities

### Using Managed Identity for ACR Pulls (No Admin Credentials)

This is the recommended pattern -- eliminates username/password management entirely.

**Terraform/ARM configuration pattern:**

```json
{
  "identity": {
    "type": "UserAssigned",
    "userAssignedIdentities": {
      "<ACR_PULL_IDENTITY_RESOURCE_ID>": {}
    }
  },
  "properties": {
    "configuration": {
      "registries": [{
        "server": "myregistry.azurecr.io",
        "identity": "<ACR_PULL_IDENTITY_RESOURCE_ID>"
      }],
      "identitySettings": [{
        "identity": "<ACR_PULL_IDENTITY_RESOURCE_ID>",
        "lifecycle": "None"
      }]
    }
  }
}
```

The `lifecycle: "None"` setting is critical -- it means the identity is used **only for image pull** and is not available to the application code, following least-privilege principles. Available options: `Init`, `Main`, `All`, `None`.

**Required role assignment:** The identity needs `AcrPull` role on the Azure Container Registry.

### Identity Lifecycle Controls (API 2024-02-02-preview+)


| Lifecycle Value | Available To           | Use Case                                                   |
| --------------- | ---------------------- | ---------------------------------------------------------- |
| `All` (default) | Init + Main containers | Identity needed everywhere                                 |
| `Init`          | Init containers only   | Initialization that needs identity, main container doesn't |
| `Main`          | Main containers only   | Init doesn't need identity                                 |
| `None`          | No containers          | ACR pull only, scale rules, Key Vault secrets              |

Sources:

- [Managed identities in Azure Container Apps | Microsoft Learn](https://learn.microsoft.com/en-us/azure/container-apps/managed-identity)
- [Security overview | Microsoft Learn](https://learn.microsoft.com/en-us/azure/container-apps/security)
- [Azure built-in roles for Containers | Microsoft Learn](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/containers)

---

## 5. Debugging and Troubleshooting

### Log Types and Tables


| Log Type     | Source                          | Log Analytics Table          | What It Contains                                                        |
| ------------ | ------------------------------- | ---------------------------- | ----------------------------------------------------------------------- |
| Console Logs | Your containers (stdout/stderr) | `ContainerAppConsoleLogs_CL` | App output, error messages, debug info                                  |
| System Logs  | Container Apps platform         | `ContainerAppSystemLogs_CL`  | Container starts/stops, health probes, scaling, image pulls, Dapr, auth |

**Important table column names:**


| Column                 | Description          |
| ---------------------- | -------------------- |
| `ContainerAppName_s`   | Container app name   |
| `EnvironmentName_s`    | Environment name     |
| `RevisionName_s`       | Revision name        |
| `ContainerGroupName_g` | Replica name         |
| `ContainerId_s`        | Container identifier |
| `ContainerImage_s`     | Container image name |
| `Log_s`                | Log message          |
| `LogLevel_s`           | Log level            |

Note: If your environment is configured with Azure Monitor (not the default Log Analytics), the table names change to `ContainerAppConsoleLogs` (without `_CL`).

### Essential KQL Queries

**View recent console logs for an app:**

```kusto
ContainerAppConsoleLogs_CL
| where ContainerAppName_s == 'my-app'
| project Time=TimeGenerated, Revision=RevisionName_s,
          Container=ContainerName_s, Message=Log_s
| take 100
```

**Find errors across all apps in the last 24 hours:**

```kusto
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(24h)
| where Log_s contains "error" or Log_s contains "Error"
        or Log_s contains "exception"
| project Time=TimeGenerated, App=ContainerAppName_s,
          Revision=RevisionName_s, Message=Log_s
| order by Time desc
```

**System logs -- find provisioning errors:**

```kusto
ContainerAppSystemLogs_CL
| where Log_s contains "Error" or Log_s contains "error"
| project Time=TimeGenerated, App=ContainerAppName_s,
          Revision=RevisionName_s, Message=Log_s
| order by Time desc
```

**Track scaling events:**

```kusto
ContainerAppSystemLogs_CL
| where Log_s contains "replica" or Log_s contains "scaling"
| project Time=TimeGenerated, App=ContainerAppName_s, Message=Log_s
| order by Time desc
```

**Find image pull failures:**

```kusto
ContainerAppSystemLogs_CL
| where Log_s contains "ImagePull" or Log_s contains "ErrImagePull"
| project Time=TimeGenerated, App=ContainerAppName_s,
          Revision=RevisionName_s, Message=Log_s
```

**Identify which apps are producing the most errors:**

```kusto
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(1h)
| where Log_s contains "error" or Log_s contains "exception"
| summarize ErrorCount=count() by ContainerAppName_s
| order by ErrorCount desc
```

**Monitor revision provisioning status:**

```kusto
ContainerAppSystemLogs_CL
| where Log_s contains "revision"
| project Time=TimeGenerated, App=ContainerAppName_s, Message=Log_s
| order by Time desc
| take 50
```

### Getting a Shell into a Container

**Basic connection (single container app):**

```bash
az containerapp exec \
  --name <APP_NAME> \
  --resource-group <RG_NAME>
```

**Targeting specific revision/replica/container:**

```bash
# 1. List revisions
az containerapp revision list \
  --name <APP_NAME> \
  --resource-group <RG_NAME> \
  --query "[].name"

# 2. List replicas for a revision
az containerapp replica list \
  --name <APP_NAME> \
  --resource-group <RG_NAME> \
  --revision <REVISION_NAME> \
  --query "[].{Containers:properties.containers[].name, Name:name}"

# 3. Connect to specific replica and container
az containerapp exec \
  --name <APP_NAME> \
  --resource-group <RG_NAME> \
  --revision <REVISION_NAME> \
  --replica <REPLICA_NAME> \
  --container <CONTAINER_NAME>
```

**Debug console for distroless images:**

```bash
az containerapp debug \
  --name <APP_NAME> \
  --resource-group <RG_NAME> \
  --revision <REVISION_NAME> \
  --replica <REPLICA_NAME>
```

The debug console creates a **separate sidecar container** with debugging tools, sharing underlying resources with your app container. This is invaluable for distroless images that don't include a shell.

**Inspecting images without a running container (ACR trick we used):**

```bash
az acr run --registry <ACR_NAME> \
  --cmd "<IMAGE>:<TAG> grep -rn 'os.environ' /app/settings.py" \
  /dev/null
```

This runs a command inside an ACR image directly -- useful when `az containerapp exec` fails due to no TTY.

### Streaming Logs in Real Time

```bash
# Console logs (follow mode)
az containerapp logs show \
  --name <APP_NAME> \
  --resource-group <RG_NAME> \
  --type console \
  --follow

# System logs
az containerapp logs show \
  --name <APP_NAME> \
  --resource-group <RG_NAME> \
  --type system \
  --follow
```

### Checking Replica Status

```bash
# List replicas with their status
az containerapp replica list \
  --name <APP_NAME> \
  --resource-group <RG_NAME> \
  --revision <REVISION_NAME>
```

In the Azure Portal: Navigate to your Container App -> Revision management -> click on a revision to see replica count, health status, and restart counts.

### Common Failure Modes and Diagnosis

**1. OOM Kills**

- Symptom: Container restarts unexpectedly, system logs show container termination
- Diagnosis: Check Container Apps metrics in Azure Monitor for memory usage approaching limits
- Fix: Increase memory allocation in container resource settings or fix memory leaks
- KQL: `ContainerAppSystemLogs_CL | where Log_s contains "OOM" or Log_s contains "killed"`

**2. Image Pull Failures**

- Symptom: Revision stuck in provisioning, `ErrImagePull` or `ImagePullFailure` in system logs
- Common causes: incorrect image name/tag, wrong ACR credentials, missing `AcrPull` role assignment, DNS/network restrictions blocking ACR access
- Fix: Verify image exists with exact name/tag, check managed identity role assignments, use ACR private endpoints if NSGs are configured
- **Never use `latest` tag in production** -- always use specific tags or SHA digests

**3. Health Probe Failures**

- Symptom: Container keeps restarting, replica marked unhealthy
- Common cause: port mismatch (container listens on 3000, probe targets 8080), endpoint takes too long to respond
- Diagnosis: Check system logs for health probe failure messages
- Fix: Align probe port with container's actual listening port, increase `initialDelaySeconds` and `timeoutSeconds`

**4. Secret Reference Errors**

- Symptom: Container fails to start, system logs reference missing secrets
- Fix: Ensure all referenced secrets exist in the Container App's configuration

**5. Port Configuration Issues**

- Symptom: Ingress returns 502/503, health probes fail
- Fix: Ensure target port in ingress configuration matches the port your application actually listens on

**6. DNS Resolution Issues**

- Symptom: App-to-app calls fail
- Diagnosis: Use `az containerapp exec` to get a shell, then `nslookup <other-app-fqdn>`
- Verify `168.63.129.16` is not blocked, custom DNS forwards correctly

**7. Missing Environment Variables (what we hit with core-api)**

- Symptom: Container starts but immediately crashes with `KeyError` in Python
- Diagnosis: Stream console logs to see the exact traceback
- Root cause: Image env var names diverged from what's in your Terraform config
- Fix: Use `az acr run` to grep all `os.environ[` calls in the actual image, then align your Terraform env vars

Sources:

- [Monitor logs in Azure Container Apps with Log Analytics | Microsoft Learn](https://learn.microsoft.com/en-us/azure/container-apps/log-monitoring)
- [Troubleshooting in Azure Container Apps | Microsoft Learn](https://learn.microsoft.com/en-us/azure/container-apps/troubleshooting)
- [Troubleshoot image pull failures | Microsoft Learn](https://learn.microsoft.com/en-us/azure/container-apps/troubleshoot-image-pull-failures)
- [Troubleshoot container create failures | Microsoft Learn](https://learn.microsoft.com/en-us/azure/container-apps/troubleshoot-container-create-failures)
- [Troubleshoot start failures | Microsoft Learn](https://learn.microsoft.com/en-us/azure/container-apps/troubleshoot-container-start-failures)
- [Connect to a container console | Microsoft Learn](https://learn.microsoft.com/en-us/azure/container-apps/container-console)
- [Connect to a container debug console | Microsoft Learn](https://learn.microsoft.com/en-us/azure/container-apps/container-debug-console)

---

## 6. Performance Tuning

### CPU/Memory Allocation and Kubernetes Mapping

Container Apps CPU and memory settings map directly to **Kubernetes resource requests and limits** under the hood. The values you specify are both the request and the limit -- there is no overcommit.

**Consumption profile allocations (per replica):**


| vCPU | Memory Options |
| ---- | -------------- |
| 0.25 | 0.5 GiB        |
| 0.5  | 1.0 GiB        |
| 0.75 | 1.5 GiB        |
| 1.0  | 2.0 GiB        |
| 1.5  | 3.0 GiB        |
| 2.0  | 4.0 GiB        |
| 4.0  | 8.0 GiB        |

**Dedicated profile allocations (per node):**
On a D8 node (8 vCPU, 32 GiB), the runtime reserves some resources for system overhead. Multiple replicas are bin-packed onto the node until its capacity is exhausted. When replicas cannot be scheduled, new nodes are provisioned.

### Scaling Rules

**HTTP Scaling (preferred for web apps):**

- Metric: concurrent HTTP requests per replica
- Default threshold: 10 concurrent requests
- Calculated every 15 seconds: `requests_in_last_15s / 15`
- Supports scale to zero
- Formula: `desiredReplicas = ceil(currentMetricValue / targetMetricValue)`

**TCP Scaling:**

- Metric: concurrent TCP connections per replica
- Default threshold: 10 concurrent connections
- Supports scale to zero
- Not configurable in Azure Portal (CLI/ARM/Bicep only)

**Custom KEDA Scalers:**

- Any [ScaledObject-based KEDA scaler](https://keda.sh/docs/latest/scalers/)
- Common: Azure Service Bus, Event Hubs, Storage Queues, Kafka, Redis
- Support managed identity authentication (preferred) or secrets
- Polling interval: 30s, Cool-down period: 300s

**CPU/Memory Scaling:**

- Scale based on average CPU or memory utilization across replicas
- **Cannot scale to zero** (unlike HTTP/TCP/custom)
- Threshold is a percentage (e.g., 50% CPU utilization)
- Use HTTP scaling when possible -- CPU/memory scaling is a last resort

**Default scale rule** (when no rule is defined): HTTP, 0-10 replicas.

**Scale behavior:**


| Parameter                       | Value                              |
| ------------------------------- | ---------------------------------- |
| Polling interval                | 30 seconds                         |
| Cool-down period (to zero)      | 300 seconds                        |
| Scale-up stabilization window   | 0 seconds                          |
| Scale-down stabilization window | 300 seconds                        |
| Scale-up step                   | 1, 4, 8, 16, 32... up to max       |
| Scale-down step                 | 100% of replicas that need removal |

**Important**: If ingress is disabled and you don't define `minReplicas` or a custom scale rule, your app scales to zero and **cannot start back up**. Always set `minReplicas >= 1` or define a scale rule if ingress is disabled.

### Revision Management

**Single Revision Mode (default):**

- One active revision at a time
- New revision deploys alongside old one; traffic shifts automatically once readiness probes pass
- Old revision deactivated after new one is ready
- Zero-downtime by default (temporarily doubles IP usage)

**Multiple Revision Mode:**

- Multiple revisions active simultaneously
- Manual traffic weight control (percentage-based)
- Enables blue/green deployments and A/B testing
- Revision labels: `blue` (production) and `green` (new version)
- Use `revisionSuffix` for deterministic naming

**Blue/Green deployment pattern:**

1. Deploy new revision with a `green` label, 0% traffic
2. Test via label URL: `https://<app>---green.<env>.<region>.azurecontainerapps.io`
3. Gradually shift traffic: 10% -> 50% -> 100%
4. Swap labels: green becomes blue

### Health Probe Tuning

**Probe types and defaults (when ingress is enabled):**


| Probe     | Default Protocol | Default Port        | Period | Timeout | Initial Delay | Failure Threshold |
| --------- | ---------------- | ------------------- | ------ | ------- | ------------- | ----------------- |
| Startup   | TCP              | Ingress target port | 1s     | 3s      | 1s            | 240               |
| Liveness  | TCP              | Ingress target port | (10s)  | (1s)    | (0s)          | (3)               |
| Readiness | TCP              | Ingress target port | 5s     | 5s      | 3s            | 48                |

**Configurable fields per probe:**

- `type`: Liveness, Readiness, or Startup
- `httpGet.path` / `httpGet.port` / `httpGet.httpHeaders` (for HTTP probes)
- `tcpSocket.port` (for TCP probes)
- `initialDelaySeconds`: delay before first probe
- `periodSeconds`: how often to probe
- `timeoutSeconds`: per-probe timeout
- `failureThreshold`: failures before action is taken
- `successThreshold`: successes needed to mark healthy

**Restrictions:**

- One probe of each type per container
- `exec` probes are NOT supported
- gRPC is NOT supported
- Named ports not supported (must be integers)

**Best practices:**

- **Always use startup probes for slow-starting apps** (Java, .NET can take 30+ seconds)
- Set `timeoutSeconds` to at least 2x your endpoint's p99 response time under load
- Put dependency checks (DB, cache, external services) in **readiness probes**, not liveness
- Total restart time = `initialDelaySeconds + (periodSeconds * failureThreshold)`
- The default startup probe allows 240 seconds (240 * 1s) before killing the container

### Connection Pooling and Keep-Alive

- Envoy handles connection pooling at the edge proxy level
- Downstream: HTTP/1.1 and HTTP/2 with automatic upgrade
- Upstream: configurable via the `transport` property on ingress
- For database connections, implement connection pooling in your application code
- Consider sidecar containers for connection proxies (e.g., PgBouncer)

Sources:

- [Scaling in Azure Container Apps | Microsoft Learn](https://learn.microsoft.com/en-us/azure/container-apps/scale-app)
- [Health probes in Azure Container Apps | Microsoft Learn](https://learn.microsoft.com/en-us/azure/container-apps/health-probes)
- [Update and deploy changes (Revisions) | Microsoft Learn](https://learn.microsoft.com/en-us/azure/container-apps/revisions)
- [Traffic splitting | Microsoft Learn](https://learn.microsoft.com/en-us/azure/container-apps/traffic-splitting)
- [Blue-Green Deployment | Microsoft Learn](https://learn.microsoft.com/en-us/azure/container-apps/blue-green-deployment)

---

## 7. Platform Engineering Perspective

### Structuring Container Apps for Multi-Team Orgs

**Environment-per-team vs Shared Environments:**


| Pattern                                      | Pros                                                            | Cons                                                                    | Best For                                                    |
| -------------------------------------------- | --------------------------------------------------------------- | ----------------------------------------------------------------------- | ----------------------------------------------------------- |
| **Environment per team**                     | Strong isolation, independent networking, team autonomy         | Higher cost (each env = separate AKS cluster), more management overhead | Teams with different security/compliance requirements       |
| **Shared environment, separate apps**        | Cost efficient, shared networking, easy inter-app communication | Noisy neighbor risk, shared Log Analytics, blast radius                 | Teams that frequently communicate, similar trust boundaries |
| **Environment per stage** (dev/staging/prod) | Clear separation of environments, standard practice             | Multiple environments to manage                                         | Most organizations                                          |

**Recommended pattern for your setup** (given your foundation/app layering):

- One environment per subscription-stage (dev, uat, prod)
- Within each environment, use separate container apps per service
- Use internal ingress for backend services, external only for frontends/APIs
- Leverage workspace-based ephemeral environments (which you're already building with `cli/eph.sh`)

### Developer Experience

**Self-service patterns:**

- Terraform modules that abstract environment creation (which you're building)
- Standardized container app templates with sensible defaults for probes, scaling, and resource limits
- CI/CD pipelines that developers trigger but platform team maintains
- `az containerapp up` for rapid prototyping (builds and deploys from source)

**Developer-friendly features:**

- Log streaming via `az containerapp logs show --follow`
- Console access via `az containerapp exec`
- Debug console for distroless images
- Revision-based rollbacks (instant, no rebuild needed)
- Traffic splitting for canary testing

### CI/CD Patterns

**GitHub Actions:**

```yaml
# Recommended pattern
- name: Build and push
  uses: docker/build-push-action@v5
  with:
    tags: ${{ env.ACR_LOGIN_SERVER }}/myapp:${{ github.sha }}

- name: Deploy to Container App
  uses: azure/container-apps-deploy-action@v2
  with:
    containerAppName: myapp
    resourceGroup: myRG
    imageToDeploy: ${{ env.ACR_LOGIN_SERVER }}/myapp:${{ github.sha }}
```

**Key CI/CD practices:**

- **Always tag with git SHA or build ID** -- never rely solely on `latest`
- Use dual-tagging: `latest` + specific tag (commit SHA)
- Terraform for environment/infrastructure, CI/CD for app deployments
- Use revision suffixes for deterministic revision names
- Blue/green via revision labels for zero-downtime production deployments

### Cost Optimization Strategies

**1. Scale to zero aggressively:**

- Consumption profile apps with no traffic scale to zero automatically
- Zero replicas = zero compute charges
- Set `minReplicas: 0` for non-production apps
- Cool-down period is 300 seconds (5 minutes) by default

**2. Right-size your resource allocations:**

- Don't over-provision CPU/memory -- you pay for what you request
- Start small (0.25 vCPU, 0.5 GiB) and increase based on monitoring data
- Use Azure Monitor metrics to track actual usage vs allocated

**3. Use Consumption profiles for bursty workloads:**

- Pay per second of actual compute used
- Avoid Dedicated profiles unless you have steady-state workloads

**4. Use Dedicated profiles for predictable workloads:**

- When apps rarely scale to zero and have consistent load, Dedicated profiles can be cheaper
- Bin-packing multiple apps on shared nodes reduces per-app cost
- D4 profile running 24/7 is often cheaper than Consumption for always-on services

**5. Shared environments reduce overhead:**

- Each environment is an AKS cluster with management overhead costs
- Consolidate related apps into fewer environments where security permits

**6. Non-production cost reduction:**

- Use ephemeral environments that are destroyed after testing (your `cli/eph.sh` pattern)
- Set `minReplicas: 0` everywhere in dev/staging
- Use smaller workload profiles (D4 instead of D16) in non-prod

**7. Avoid idle billing:**

- Replicas that are not processing but remain in memory may be billed at a lower "idle" rate
- Configure aggressive scale-down settings for non-critical services

Sources:

- [Architecture Best Practices for Azure Container Apps | Microsoft Learn](https://learn.microsoft.com/en-us/azure/well-architected/service-guides/azure-container-apps)
- [Deploy to Azure Container Apps with GitHub Actions | Microsoft Learn](https://learn.microsoft.com/en-us/azure/container-apps/github-actions)
- [Container Apps Build and Deploy Action | GitHub](https://github.com/Azure/container-apps-deploy-action)

---

## Quick Reference: Relevance to Your AECOM AI Infra Project

Given your project context (North Europe, azurerm Terraform, thin wrapper modules, `appNet` subnet with App Service delegation):

1. **Subnet sizing**: Your `appNet` subnet currently has App Service delegation. For Container Apps, you need a **dedicated subnet** (no other delegations). With a Workload Profiles (v2) environment, minimum `/27`. Recommend at least `/24` to allow comfortable scaling and account for the temporary IP doubling during deployments.
2. **Terraform module**: You'll want a thin `azurerm_container_app_environment` and `azurerm_container_app` wrapper module, consistent with your existing patterns. The `azurerm` provider has full support for Container Apps resources. Don't forget the `lifecycle { ignore_changes = [infrastructure_resource_group_name] }` on the CAE resource.
3. **ACR integration**: Your existing `container-registry` module should work well. Add a user-assigned managed identity with `AcrPull` role for the Container Apps environment, and use `lifecycle: "None"` to restrict the identity from being accessible in container code.
4. **Networking**: Since you're in a custom VNet (`consigli-vnet-dev`, 10.0.0.0/16), you'll get full NSG/UDR support with Workload Profiles environments. Consider using internal ingress for backend services and routing external traffic through your existing network topology.
5. **Observability**: Your existing `app-insights` and `log-analytics` modules will integrate naturally -- the Container App Environment takes a Log Analytics workspace ID as a parameter.