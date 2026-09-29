# Project Change Log

All significant changes made after initial deployment, in chronological order.

---

## Change 1 — Grafana Removed

**Reason:** Grafana required a LoadBalancer service type which doesn't work on bare-metal Kubernetes. Kibana already provides dashboards, alerting, and APM visualization — Grafana was redundant.

### Files changed

| File | What changed |
|---|---|
| `K8s/moni/deployment-grafana.yaml` | **Deleted entirely** |
| `K8s/moni/nodeport-services.yaml` | Removed `grafana-nodeport` section (was port 30300) |
| `K8s/moni/configM/configmap.yaml` | Removed `GRAFANA_USER` from ConfigMap, removed `GRAFANA_PASSWORD` from Secret |
| `K8s/ALL_K8S_DEPLOYMENT_GUIDE.md` | Removed Grafana from cluster layout, NodePort table, Phase 11 deploy step, Known Issues |
| `FinalSum.md` | Removed entire Grafana section, removed from all tables and data flow diagrams |

### How to apply (if re-deploying from scratch)

Simply do not apply `deployment-grafana.yaml` — it no longer exists. All other monitor deployments are unchanged.

If you had Grafana running and want to remove it:
```bash
kubectl delete deployment grafana -n monitor
kubectl delete service grafana -n monitor
```

---

## Change 2 — Redis Data Persistence (AOF + PVC)

**Reason:** Redis was running with no persistence — all todo data was lost every time the pod restarted or the node rebooted.

### What changed

Redis now starts with `appendonly yes` mode. Every write command is logged to `/data/appendonly.aof`. On restart, Redis replays the AOF file to restore state.

| File | What changed |
|---|---|
| `K8s/app/deployment-redis.yaml` | Added ConfigMap `redis-config` with AOF settings, added PVC `redis-data` (1Gi), changed container command to load the config file, added volumeMounts for `/data` and `/usr/local/etc/redis` |

### AOF config written to Redis

```
appendonly yes
appendfsync everysec
dir /data
```

- `appendonly yes` — enable AOF persistence
- `appendfsync everysec` — flush to disk every second (balance between safety and performance)
- `dir /data` — write AOF file to the mounted PVC path

### Where data is stored

On k8s-app node at:
```
/opt/local-path-provisioner/pvc-<uid>_app_redis-data/appendonly.aof
```

### How to apply

```bash
kubectl apply -f K8s/app/deployment-redis.yaml
kubectl rollout restart deployment/redis -n app
kubectl get pvc -n app   # redis-data should show Bound
```

---

## Change 3 — H2 Database File Mode (users-api persistence)

**Reason:** users-api was using H2 in in-memory mode — all registered user accounts were lost every time the pod restarted.

### What changed

Spring Boot reads `SPRING_DATASOURCE_URL` at startup. Overriding it with a file-mode JDBC URL switches H2 from memory to disk with no image rebuild required.

| File | What changed |
|---|---|
| `K8s/app/deployment-users-api.yaml` | Added `SPRING_DATASOURCE_URL` env var pointing to `/data/usersdb`, added volumeMount at `/data`, added PVC `users-api-data` (1Gi) |

### The env var added

```yaml
- name: SPRING_DATASOURCE_URL
  value: "jdbc:h2:file:/data/usersdb;DB_CLOSE_ON_EXIT=FALSE"
```

### Where data is stored

On k8s-app node at:
```
/opt/local-path-provisioner/pvc-<uid>_app_users-api-data/usersdb.mv.db
```

### How to apply

```bash
kubectl apply -f K8s/app/deployment-users-api.yaml
kubectl rollout restart deployment/users-api -n app
kubectl get pvc -n app   # users-api-data should show Bound
```

---

## Change 4 — todos-api: Todos Now Stored in Redis

**Reason:** todos-api was using `memory-cache` (an in-process Node.js memory store) to hold todos. Data was lost on every pod restart. Redis was only used for pub/sub PUBLISH — not for actual storage.

### What changed

`app-vm/App/todos-api/todoController.js` was completely rewritten:
- Removed `memory-cache` dependency
- Added `redisClient.get` / `redisClient.set` calls using `util.promisify`
- Todos stored as JSON at Redis key `todos:{username}`
- `lastInsertedID` bug fixed: was `3` (would overwrite the third default todo), corrected to `4`
- All route handlers made `async`
- `_logOperation` (PUBLISH) unchanged — same Redis client handles both storage and pub/sub

| File | What changed |
|---|---|
| `app-vm/App/todos-api/todoController.js` | Full rewrite — memory-cache → Redis GET/SET |
| `K8s/app/deployment-todos-api.yaml` | Image tag changed: `todos-api` → `todos-api-redis` |

### Redis key structure

```
todos:{username}  →  JSON string
{
  "items": {
    "1": {"id": 1, "text": "Learn Kubernetes", "userId": 1},
    "2": {"id": 2, "text": "Set up ELK", "userId": 1},
    "3": {"id": 3, "text": "Delete example ones", "userId": 1}
  },
  "lastInsertedID": 4
}
```

### Build and push the new image

```bash
cd app-vm/App/todos-api
docker build -t kan2nd/microapp:todos-api-redis .
docker push kan2nd/microapp:todos-api-redis
```

### How to apply

```bash
kubectl apply -f K8s/app/deployment-todos-api.yaml
kubectl rollout restart deployment/todos-api -n app
```

---

## Change 5 — log-message-processor: APM Config Fixed

**Reason:** The Elastic APM Python agent was instrumented in `main.py` but the deployment YAML had no APM env vars. The agent defaulted to `localhost:8200` which is a dead address inside a Kubernetes pod.

### What was missing

The deployment had no `ELASTIC_APM_SERVER_URL`, `ELASTIC_APM_SECRET_TOKEN`, or `ELASTIC_APM_SERVICE_NAME` environment variables, so the agent could not connect to Fleet Server.

| File | What changed |
|---|---|
| `K8s/app/deployment-log-processor.yaml` | Added `ELASTIC_APM_SERVER_URL`, `ELASTIC_APM_SECRET_TOKEN` (from secret), `ELASTIC_APM_SERVICE_NAME` |

### Env vars added

```yaml
- name: ELASTIC_APM_SERVER_URL
  value: "http://fleet-server.monitor.svc.cluster.local:8200"
- name: ELASTIC_APM_SECRET_TOKEN
  valueFrom:
    secretKeyRef:
      name: elastic-apm-secret
      key: apm-secret-token
- name: ELASTIC_APM_SERVICE_NAME
  value: "log-message-processor"
```

### How to apply

```bash
kubectl apply -f K8s/app/deployment-log-processor.yaml
kubectl rollout restart deployment/log-message-processor -n app
```

After restarting, `log-message-processor` should appear as a service in Kibana APM → Services.

---

## Change 6 — kube-state-metrics Added

**Reason:** There was no way to get automated alerts when a pod crashed, entered CrashLoopBackOff, or when a node became NotReady. Rancher UI shows this visually but not automatically. kube-state-metrics exposes Kubernetes object state as Prometheus metrics, which then flow into Elasticsearch via `remote_write` and can trigger Kibana alerts.

### Files changed

| File | What changed |
|---|---|
| `K8s/moni/kube-state-metrics.yaml` | **New file** — ServiceAccount, ClusterRole, ClusterRoleBinding, Deployment, Service |
| `K8s/moni/deployment-prometheus-monitoring.yaml` | Added `kube-state-metrics` scrape job to `prometheus.yml` ConfigMap |
| `K8s/ALL_K8S_DEPLOYMENT_GUIDE.md` | Added deploy step in Phase 11, updated Phase 14 verification targets, updated cluster layout |
| `FinalSum.md` | Added kube-state-metrics to Prometheus scrape targets section, added Kibana alert examples |
| `doc/presentation.md` | Added kube-state-metrics to tools table (slide 3) and metrics diagram (slide 8) |

### Key metrics now available

| Metric | Meaning |
|---|---|
| `kube_pod_status_phase` | Running / Pending / Failed / Unknown per pod |
| `kube_pod_container_status_restarts_total` | Restart count per container (CrashLoopBackOff detection) |
| `kube_deployment_status_replicas_unavailable` | How many replicas are missing from any deployment |
| `kube_node_status_condition` | Node Ready / NotReady |
| `kube_persistentvolumeclaim_status_phase` | PVC Bound / Pending / Lost |

### How to apply

```bash
kubectl apply -f K8s/moni/kube-state-metrics.yaml
kubectl rollout restart deployment/prometheus-monitoring -n monitor   # picks up new scrape job
kubectl get pods -n monitor   # kube-state-metrics pod should be Running
```

Verify it appears in Prometheus:
```
http://192.168.96.136:30900/targets
# Look for: kube-state-metrics — State: UP
```

### Kibana alerts to create after applying

Go to Kibana → Stack Management → Rules → Create rule → **Elasticsearch query**:

**Alert: Pod Down**
- Index: `metrics-*` (Kibana) — same data also visible in Prometheus UI
- KQL: `kube_deployment_status_replicas_unavailable > 0`
- Fires: more than 1 doc in last 2 minutes

**Alert: CrashLoopBackOff**
- Index: `metrics-*` (Kibana) — Prometheus UI: `kube_pod_container_status_restarts_total`
- KQL: `kube_pod_container_status_restarts_total > 5`
- Fires: more than 1 doc in last 5 minutes

**Alert: Node NotReady**
- Index: `metrics-*` (Kibana) — Prometheus UI: `kube_node_status_condition`
- KQL: `kube_node_status_condition{condition="Ready"} == 0`
- Fires: more than 1 doc in last 2 minutes

> **Two ways to view kube-state-metrics data:**
> - **Prometheus UI** `http://192.168.96.136:30900/graph` — type any metric name directly, instant graph, good for quick checks
> - **Kibana Discover → `metrics-*`** — search with KQL, combine with timestamps, build dashboards and alerts

---

## Summary of All Persistent Storage (After Changes 2–4)

| Data | Storage | Survives pod restart | Survives node reboot |
|---|---|---|---|
| User accounts | H2 file `/data/usersdb` on PVC `users-api-data` | Yes | Yes (PVC on node disk) |
| Todos | Redis key `todos:{username}` in AOF on PVC `redis-data` | Yes | Yes (PVC on node disk) |
| Logs | Elasticsearch PVC `esdata01` (10Gi) | Yes | Yes |
| Kibana dashboards | Kibana PVC `kibanadata` (5Gi) | Yes | Yes |
| Fleet Server state | Fleet PVC `fleetserverdata` (5Gi) | Yes | Yes |
| Zipkin traces | In-memory only | **No** | **No** |
| Prometheus metrics | In-memory only (no PVC) | **No** | **No** |

> Zipkin and Prometheus both lose data on pod restart. See `doc/suggestions.md` for how to fix these.

---

## Change 7 — LICENSE Key Added to monitor-config ConfigMap

**Reason:** After rebooting k8s-worker, Elasticsearch and Kibana pods failed with `CreateContainerConfigError: couldn't find key LICENSE in ConfigMap monitor/monitor-config`. The Elasticsearch deployment references a `LICENSE` key to set the license type, but it was never present in the ConfigMap.

### What changed

| File | What changed |
|---|---|
| `K8s/moni/configM/configmap.yaml` | Added `LICENSE: "basic"` to the `monitor-config` ConfigMap |

### Keys added

```yaml
data:
  STACK_VERSION: "9.3.1"
  CLUSTER_NAME: "monitor-cluster"
  LICENSE: "basic"              ← required by Elasticsearch
  MONITOR_VM_IP: "192.168.96.136"  ← required by Kibana (k8s-worker IP)
```

`basic` is the free self-generated license tier. It enables all features used in this project (security, TLS, Fleet, APM, alerting with Elasticsearch query rule type). Do not use `trial` — it expires after 30 days.

### How to apply (if hitting this error)

```bash
# Patch the live ConfigMap immediately
kubectl patch configmap monitor-config -n monitor \
  --type merge \
  -p '{"data":{"LICENSE":"basic"}}'

# Then delete the stuck pods so they restart and pick up the key
kubectl delete pod -n monitor -l app=es01
kubectl delete pod -n monitor -l app=kibana
kubectl delete pod -n monitor -l app=fleet-server

# Watch recovery (ES takes 2-3 minutes)
kubectl get pods -n monitor -w
```

### Why this only appeared after a node reboot

The ConfigMap had been applied before this key existed. Kubernetes does not crash running pods when a referenced ConfigMap key is missing — it only fails when the pod is (re)created and tries to inject the env var. The reboot forced all pods to restart, which is when the missing key was finally caught.

---

## Change 8 — Prometheus Scrape Jobs Cleaned Up + cAdvisor Added

**Reason:** Three scrape jobs were broken or unnecessary. Frontend returned HTML at `/metrics`, the Kubernetes API server returned 403 (missing RBAC), and Rancher had a missing token file. All three were removed. cAdvisor was added to give per-pod CPU and memory metrics — something none of the removed jobs provided.

### Files changed

| File | What changed |
|---|---|
| `K8s/moni/deployment-prometheus-monitoring.yaml` | Removed `frontend`, `kubernetes-apiservers`, `rancher` scrape jobs; added `kubelet-cadvisor` scrape job; removed `rancher-token` volume and volumeMount; added `nodes/metrics` to ClusterRole |

### Scrape jobs removed

| Job | Reason removed |
|---|---|
| `frontend` | Port 8080 serves Vue.js HTML — not a Prometheus metrics endpoint |
| `kubernetes-apiservers` | Returned 403 — missing `nonResourceURLs` RBAC; not needed since kube-state-metrics covers cluster health |
| `rancher` | Token file `/etc/prometheus/rancher/token` never existed — secret was never created |

### Scrape job added — kubelet cAdvisor

Routes through the Kubernetes API server proxy so no firewall changes needed on nodes:

```yaml
- job_name: 'kubelet-cadvisor'
  scheme: https
  tls_config:
    ca_file: /var/run/secrets/kubernetes.io/serviceaccount/ca.crt
    insecure_skip_verify: true
  bearer_token_file: /var/run/secrets/kubernetes.io/serviceaccount/token
  kubernetes_sd_configs:
  - role: node
  relabel_configs:
  - action: labelmap
    regex: __meta_kubernetes_node_label_(.+)
  - target_label: __address__
    replacement: kubernetes.default.svc:443
  - source_labels: [__meta_kubernetes_node_name]
    regex: (.+)
    target_label: __metrics_path__
    replacement: /api/v1/nodes/${1}/proxy/metrics/cadvisor
```

### New metrics available (per-pod CPU and memory)

```
container_cpu_usage_seconds_total{namespace="app", pod="todos-api-xxx", container="todos-api"}
container_memory_working_set_bytes{namespace="app", pod="users-api-xxx", container="users-api"}
```

**View in Prometheus UI:**
```
rate(container_cpu_usage_seconds_total{namespace="app"}[2m]) * 100
container_memory_working_set_bytes{namespace="app"} / 1024 / 1024
```

**View in Kibana:** Discover → `metrics-*` → filter `container_cpu_usage_seconds_total : * AND kubernetes_namespace : "app"`

### How to apply

```bash
kubectl apply -f ~/k8s/moni/deployment-prometheus-monitoring.yaml
kubectl rollout restart deployment/prometheus-monitoring -n monitor
```

Verify in Prometheus UI → Status → Targets:
- `kubelet-cadvisor` shows UP (one entry per node: k8s-control, k8s-worker, k8s-app)
- `frontend`, `kubernetes-apiservers`, `rancher` are gone

---

## Change 9 — Prometheus Upgraded to v3.11.3-distroless, remote_write Removed

**Reason:** Two issues resolved in one change:
1. `remote_write` to ES 9.3.1 is permanently broken due to a snappy encoding incompatibility (see below).
2. Prometheus was upgraded from `v2.53.1` to `v3.11.3-distroless` because `v2.53.1` had a GC overhead issue and `v3` is the current stable release.

### Why remote_write cannot work with ES 9.3.1

Prometheus (all versions, both RW 1.0 and RW 2.0) uses Go's `snappy.Encode()` function which produces **snappy block** encoding. When sending remote_write data, Prometheus sets `Content-Encoding: snappy` on the HTTP request.

ES 9.3.1's Netty HTTP transport uses `HttpContentDecoder` which, upon seeing `Content-Encoding: snappy`, applies `SnappyFrameDecoder`. The `SnappyFrameDecoder` expects **snappy framing** format (starts with `0xff sNaPpY` stream identifier).

Snappy block format does not start with the framing header — its first byte is a varint for the uncompressed data length (often `0xfe` = 254), which the framing decoder interprets as a `RESERVED_SKIPPABLE` chunk type. This causes:

```
DecompressionException: Received RESERVED_SKIPPABLE tag before STREAM_IDENTIFIER
```

ES then closes the connection before routing to the `/_prometheus/receive` handler. Prometheus sees EOF or "use of closed network connection".

**This is not fixable via Prometheus config.** The `protobuf_message: "io.prometheus.write.v2.Request"` setting only changes the protobuf schema — it does not change the snappy variant. Both RW 1.0 and RW 2.0 use the same `snappy.Encode()` call in Prometheus source code.

### Where to view Prometheus metrics instead

All scraped metrics (kube-state-metrics, Redis, cAdvisor, Prometheus self) are visible directly in the Prometheus UI:

```
http://192.168.96.136:30900/graph
```

Key queries:
| What | Query |
|---|---|
| Pod health | `kube_deployment_status_replicas_unavailable{namespace="app"}` |
| Redis up | `redis_up` |
| Pod CPU | `rate(container_cpu_usage_seconds_total{namespace="app"}[2m]) * 100` |
| Pod memory | `container_memory_working_set_bytes{namespace="app"} / 1024 / 1024` |

### To get metrics into Kibana (future fix)

Use Fleet → Agent Policies → Add integration → **Prometheus**. Configure Elastic Agent to scrape:
- `http://kube-state-metrics.monitor.svc.cluster.local:8080` — pod health metrics
- `http://redis.app.svc.cluster.local:9121` — Redis metrics

Data will appear in `metrics-prometheus.*` index, queryable in Kibana Discover and usable for alerts. This bypasses `remote_write` entirely.

### Files changed

| File | What changed |
|---|---|
| `K8s/moni/deployment-prometheus-monitoring.yaml` | Image changed to `prom/prometheus:v3.11.3-distroless`; `remote_write` block removed; `elastic-password` volume/volumeMount removed; `--enable-feature=native-histograms` arg kept (no-op in v3 but harmless) |

### How to apply

```bash
kubectl apply -f ~/k8s/moni/deployment-prometheus-monitoring.yaml
kubectl rollout restart deployment/prometheus-monitoring -n monitor
kubectl get pods -n monitor   # prometheus should show Running
```

---

## Change 10 — Fleet Prometheus Integration (Metrics → Kibana via Elastic Agent)

**Reason:** Prometheus `remote_write` to Elasticsearch is permanently broken due to a snappy encoding incompatibility (Go snappy block vs Java Netty SnappyFrameDecoder framing format). Metrics were only visible in the Prometheus UI and not in Kibana. The fix is to have Elastic Agent (running inside the Fleet Server pod) scrape the Prometheus-format endpoints directly via NodePort, bypassing `remote_write` entirely.

### Files changed

| File | What changed |
|---|---|
| `K8s/moni/kube-state-metrics.yaml` | Added `kube-state-metrics-nodeport` Service (NodePort 30780, targetPort 8080) |
| `K8s/app/deployment-redis.yaml` | Added `redis-exporter-nodeport` Service (NodePort 30781, targetPort 9121) |

### How it works

Elastic Agent running inside the Fleet Server pod on k8s-worker scrapes:
- `http://192.168.96.136:30780/metrics` — kube-state-metrics (pod health, deployment status, restart counts)
- `http://192.168.96.136:30781/metrics` — Redis exporter (redis_up, memory, connected clients)

Data lands in Elasticsearch index: `.ds-metrics-prometheus.collector-default-*`

### Actual field structure in Elasticsearch

Metrics are stored as flat fields under `prometheus`, NOT nested under `prometheus.metrics`:

```json
{
  "prometheus": {
    "kube_deployment_status_replicas_unavailable": { "value": 0 },
    "kube_deployment_status_replicas_available": { "value": 1 },
    "redis_up": { "value": 1 },
    "labels": {
      "instance": "192.168.96.136:30780",
      "namespace": "app",
      "deployment": "todos-api"
    }
  }
}
```

### How to configure (Fleet UI)

1. Kibana → Fleet → Agent Policies → Fleet-Server-Policy
2. Add integration → search **Prometheus**
3. Add two hosts:
   - `http://192.168.96.136:30780/metrics` — kube-state-metrics
   - `http://192.168.96.136:30781/metrics` — Redis exporter
4. Save and deploy

### How to apply NodePort services

```bash
kubectl apply -f K8s/moni/kube-state-metrics.yaml
kubectl apply -f K8s/app/deployment-redis.yaml
```

Verify endpoints return metrics:
```bash
curl http://192.168.96.136:30780/metrics | head -5
curl http://192.168.96.136:30781/metrics | head -5
```

Verify data in Kibana Dev Tools:
```json
GET /.ds-metrics-prometheus.collector-default-*/_search
{
  "size": 1,
  "query": { "exists": { "field": "prometheus.kube_deployment_status_replicas_unavailable" } }
}
```

---

## Change 11 — Kibana Alert: Pod Down

**Reason:** After kube-state-metrics data confirmed flowing into Elasticsearch (164+ documents for `kube_deployment_status_replicas_unavailable`), a Kibana alert rule was created to notify when any deployment has unavailable replicas.

### Alert configuration

- **Rule name:** Pod Down Alert
- **Rule type:** Elasticsearch query
- **Index:** `metrics-*`
- **KQL condition:** `prometheus.kube_deployment_status_replicas_unavailable > 0`
- **Threshold:** More than 0 documents in the last 2 minutes
- **Location:** Kibana → Stack Management → Rules → Pod Down Alert

### How to test

```bash
kubectl delete pod -n app -l app=redis
# Pod restarts automatically in ~30s
# During restart: kube_deployment_status_replicas_unavailable = 1
# Alert fires within 2 minutes
# After pod recovers: metric returns to 0, alert resolves
```
