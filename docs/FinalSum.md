# ELK2 Project — Complete Technical Summary (Kubernetes)

## Table of Contents
1. [Architecture Overview](#1-architecture-overview)
2. [The Application (Microservices)](#2-the-application-microservices)
3. [Elasticsearch](#3-elasticsearch)
4. [Kibana](#4-kibana)
5. [Fleet Server & Elastic Agent](#5-fleet-server--elastic-agent)
6. [Fluent-Bit (Log Pipeline)](#6-fluent-bit-log-pipeline)
7. [Prometheus](#7-prometheus)
8. [Zipkin (Distributed Tracing)](#8-zipkin-distributed-tracing)
9. [Redis (Message Queue)](#9-redis-message-queue)
11. [Rancher (K8s Management UI)](#11-rancher-k8s-management-ui)
12. [What Each Tool Monitors](#12-what-each-tool-monitors)
13. [Data Flow Summary](#13-data-flow-summary)
14. [Monitoring Gaps](#14-monitoring-gaps)
15. [Where to Check Each Data Type](#15-where-to-check-each-data-type)
16. [Secrets & Credentials Reference](#16-secrets--credentials-reference)

---

## 1. Architecture Overview

A 3-node Kubernetes cluster split into two namespaces. Application pods run on one node; all observability tooling runs on another.

### Nodes

| Node | Role | What runs on it |
|---|---|---|
| `k8s-control` | Control plane | API server, etcd, scheduler — no application pods |
| `k8s-app` | Application workloads | All microservices + fluent-bit-collector DaemonSet + elastic-agent DaemonSet |
| `k8s-worker` | Monitoring workloads | Elasticsearch, Kibana, Fleet Server, Prometheus, Fluent-Bit receiver, kube-state-metrics |

Every Deployment and DaemonSet uses a `nodeSelector` (`kubernetes.io/hostname: k8s-app` or `k8s-worker`) to enforce this placement. Pods never land on the wrong node.

### Namespaces

| Namespace | Contents |
|---|---|
| `app` | frontend, auth-api, todos-api, users-api, redis, zipkin, log-processor, fluent-bit-collector DaemonSet, elastic-agent DaemonSet |
| `monitor` | Elasticsearch (es01), Kibana, Fleet Server, Prometheus, Fluent-Bit receiver |
| `cattle-system` | Rancher (installed via Helm) |

Cross-namespace communication uses Kubernetes DNS: `<service>.<namespace>.svc.cluster.local`. For example, Prometheus in `monitor` reaches Redis in `app` at `redis.app.svc.cluster.local:9121` with no special networking configuration.

### External Access (NodePorts — accessible on any node IP, canonical access below)

| Service | NodePort | URL | Runs on |
|---|---|---|---|
| Kibana | 30601 | https://192.168.96.136:30601 | k8s-worker |
| Elasticsearch | 30920 | https://192.168.96.136:30920 | k8s-worker |
| Prometheus | 30900 | http://192.168.96.136:30900 | k8s-worker |
| Fleet Server | 30820 | https://192.168.96.136:30820 | k8s-worker |
| APM Server | 30200 | https://192.168.96.136:30200 | k8s-worker (Fleet Server pod) |
| kube-state-metrics | 30780 | http://192.168.96.136:30780/metrics | k8s-worker |
| Redis exporter | 30781 | http://192.168.96.136:30781/metrics | k8s-app |
| Zipkin | 30411 | http://192.168.96.137:30411 | k8s-app |
| Frontend App | 30880 | http://192.168.96.137:30880 | k8s-app |
| Rancher | 31842 | https://192.168.96.135:31842 | k8s-control |

---

## 2. The Application (Microservices)

A simple Todo web application split into 5 microservices communicating over HTTP and a Redis pub/sub channel.

```
Browser
  └─► frontend       (Vue.js,           :8080, NodePort 30880)
        ├─► auth-api  (Go,               :8081)  — login, JWT issuance
        │     └─► users-api (Java Spring Boot, :8083)  — user data store
        └─► todos-api (Node.js,          :8082)  — CRUD on todos
              └─► redis             (:6379)  — PUBLISH to "log_channel"
                    └─► log-message-processor  — SUBSCRIBE, process log events
```

**Service communication:**
1. Browser loads the Vue.js frontend from NodePort 30880.
2. Frontend calls `auth-api` to log in. `auth-api` calls `users-api` to validate credentials, then issues a signed JWT.
3. Frontend calls `todos-api` with the JWT to read/write todos. `todos-api` verifies the JWT locally.
4. Every todo write causes `todos-api` to `PUBLISH` a JSON event to the Redis channel `log_channel`.
5. `log-message-processor` has an active `SUBSCRIBE` on `log_channel`, receives every message, processes and prints it — picked up by Fluent-Bit.

**All inter-service addresses (`k8s/app/configmap-app-config.yaml`):**
```
AUTH_API_ADDRESS   = http://auth-api:8081
USERS_API_ADDRESS  = http://users-api:8083
TODOS_API_ADDRESS  = http://todos-api:8082
SPRING_REDIS_HOST  = redis
ZIPKIN_URL         = http://zipkin:9411/api/v2/spans
```

**Observability on every service:**

| Signal | What it does | Where it sends |
|---|---|---|
| Zipkin client | Records timing per request, propagates trace ID via HTTP headers | `http://zipkin:9411/api/v2/spans` |
| APM agent | Captures transactions, errors, performance metrics | `https://fleet-server.monitor.svc.cluster.local:8200` |
| Prometheus `/metrics` | Exposes numeric metrics (only frontend, todos-api have this scraped) | Pulled by Prometheus on demand |

**Resource limits:**

| Service | Memory req/limit | CPU req/limit | Startup probe delay |
|---|---|---|---|
| frontend | 256Mi / 512Mi | 100m / 500m | TCP :8080 after 120s (webpack compile) |
| auth-api | 64Mi / 128Mi | 25m / 100m | HTTP /version after 10s |
| todos-api | 128Mi / 256Mi | 50m / 150m | TCP :8082 after 15s |
| users-api | 512Mi / 1Gi | 200m / 500m | TCP :8083 after 210s (JPA/Hibernate init) |
| redis | 128Mi / 256Mi | 50m / 150m | redis-cli ping |
| zipkin | 256Mi / 512Mi | 100m / 250m | HTTP /health after 30s |

---

## 3. Elasticsearch

**What it is:** A distributed full-text search and analytics engine. In this project it serves as both the **central log store** and the **metrics time-series database**.

### How it runs in K8s

- **Image:** `docker.elastic.co/elasticsearch/elasticsearch:9.3.1`
- **Deployment:** Single-replica, pinned to `k8s-worker`
- **Discovery:** `discovery.type=single-node`
- **JVM heap:** `-Xms1g -Xmx1g`, container limit 2 GB
- **Storage:** PVC `esdata` (10 Gi) — data survives pod restarts
- **Port:** 9200 (ClusterIP `es01`), NodePort 30920 externally

### TLS and Security

- `xpack.security.enabled=true` — all connections require credentials
- TLS on both HTTP and transport layers
- Certificates in `elastic-certs` Secret: `ca.crt`, `es01.crt/key`, `kibana.crt/key`, `fleet-server.crt/key`
- User `elastic` (superuser), password from `monitor-secrets` → `ELASTIC_PASSWORD`
- User `kibana_system` (restricted), used by Kibana only

### What data lands in Elasticsearch

| Index Pattern | Data Type | Written by | Mechanism |
|---|---|---|---|
| `app-YYYY.MM.DD` | Container logs from all pods | Fluent-Bit receiver | ES output plugin, daily Logstash format |
| `metrics-*` | Prometheus scraped metrics + Elastic Agent system metrics | Prometheus + Elastic Agent | Prometheus `remote_write` / Agent direct write |
| `traces-apm-*` | Application distributed traces | **Fleet Server** (embedded APM server) | APM intake protocol |
| `metrics-apm-*` | APM service metrics (JVM, HTTP rate) | **Fleet Server** (embedded APM server) | APM intake protocol |
| `logs-apm-*` | Logs correlated to APM traces | **Fleet Server** (embedded APM server) | APM intake protocol |
| `.fleet-*` | Fleet Server state, agent enrollment | Fleet Server | Internal Fleet ES client |

### How to verify

```bash
curl -k -u elastic:changeme https://192.168.96.136:30920/_cat/indices?v&s=index
curl -k -u elastic:changeme https://192.168.96.136:30920/_cluster/health?pretty
curl -k -u elastic:changeme "https://192.168.96.136:30920/app-$(date +%Y.%m.%d)/_count"
```

---

## 4. Kibana

**What it is:** The official web UI for Elasticsearch — data visualization, query interface, Fleet management, and APM console.

### How it runs in K8s

- **Image:** `docker.elastic.co/kibana/kibana:9.3.1`
- **Deployment:** Single-replica, pinned to `k8s-worker`
- **Storage:** PVC `kibanadata` (5 Gi)
- **Port:** 5601 (ClusterIP `kibana`), NodePort 30601 externally
- Connects to ES as `kibana_system` user via `https://es01:9200`
- Three encryption keys from `monitor-secrets` required for saved objects and sessions to survive restarts

**Pre-configured via `kibana.yml`:**
```yaml
xpack.fleet.fleetServerHosts:
  - host_urls: ["https://fleet-server.monitor.svc.cluster.local:8220"]
xpack.fleet.packages: [fleet_server, system, elastic_agent, apm]
xpack.fleet.agentPolicies:
  - Fleet-Server-Policy   ← used by the fleet-server container itself
  - Default Policy        ← used by elastic-agent on k8s-app
xpack.apm.enabled: true
```

### What you use Kibana for

| Section | What it shows |
|---|---|
| **Discover** | Raw log documents. Select index `app-*` to browse container logs. Filter by `kubernetes.pod_name`, `kubernetes.namespace_name`, `level`, `stream`. Full-text search and KQL work here. |
| **Observability → Logs** | Purpose-built log viewer — readable line format, live-tail mode, sidebar filters. Better than Discover for reading logs. |
| **APM → Services** | Transactions/min, p50/p95/p99 latency, error rate per service. Click a transaction for the full trace waterfall. |
| **APM → Service Map** | Auto-generated topology diagram from trace data. |
| **APM → Traces** | Search all individual traces across all services. |
| **Fleet → Agents** | Enrolled Elastic Agents, their status, last check-in, policy version. |
| **Fleet → Enrollment Tokens** | Tokens needed to enroll new agents. Copy from here → paste into `elastic-apm-secret`. |
| **Stack Monitoring** | Internal health of ES and Kibana: JVM heap, indexing rate, shard counts. |
| **Dashboards** | Build custom visualizations from any ES index. |

**Access:** `https://192.168.96.136:30601` — Login: `elastic` / `changeme`

---

## 5. Fleet Server & Elastic Agent

**IMPORTANT DISTINCTION:** These are two separate pods with completely different roles. Elastic Agent does NOT handle APM. Fleet Server has the embedded APM server.

```
ELASTIC AGENT pod (k8s-app)           FLEET SERVER pod (k8s-worker)
──────────────────────────────         ──────────────────────────────
collects /proc + /sys metrics          runs embedded APM server :8200
sends metrics → ES DIRECTLY            receives APM from 4 app services
enrolls at Fleet Server :8220          IS the Fleet control plane
policy-managed by Fleet Server         manages Elastic Agent policies
```

### Fleet Server (`k8s/moni/deployment-fleet-server.yaml`)

**What it is:** Elastic Agent in server mode. Acts as the control plane for all other agents AND runs the embedded APM server.

**Ports:**
- `:8220` — Fleet enrollment and policy delivery (agents connect here)
- `:8200` — APM Server (receives traces/metrics/errors from app services)

**What it does:**
1. Connects to Kibana on startup → calls Fleet setup API → registers itself and activates policies
2. Stores state in Elasticsearch at `.fleet-*` indices
3. Listens on `:8220` for agent enrollment. Validates token → assigns policy → agent starts
4. Agents poll Fleet Server every ~30s for policy changes
5. Embedded APM server on `:8200` receives APM data from `auth-api`, `todos-api`, `users-api`, `log-processor` and writes directly to Elasticsearch as `traces-apm-*`, `metrics-apm-*`, `logs-apm-*`

**Key env vars:**
```
FLEET_SERVER_ENABLE=1
FLEET_SERVER_POLICY_ID=fleet-server-policy
FLEET_SERVER_ELASTICSEARCH_HOST=https://es01:9200
FLEET_SERVER_CERT=/certs/fleet-server/fleet-server.crt
KIBANA_FLEET_SETUP=1
```

### Elastic Agent (`k8s/app/daemonset-elastic-agent.yaml`)

**What it is:** The monitoring agent on the application node. Collects host-level system metrics and sends them **directly to Elasticsearch** — not through Fleet Server.

**How it runs:**
- DaemonSet pinned to `k8s-app`, runs as `root` with `privileged: true`
- `enableServiceLinks: false` — prevents K8s from injecting conflicting env vars

**Host mounts:**
```
/proc → /hostfs/proc   (read-only)
/sys  → /hostfs/sys    (read-only)
```

**Enrollment flow (one-time setup):**
1. Fleet Server is healthy and "Default Policy" exists in Kibana
2. Kibana → Fleet → Enrollment Tokens → copy token for "Default Policy"
3. Store in `elastic-apm-secret` Secret, key `fleet-enrollment-token`
4. Pod starts with `FLEET_ENROLL=1`, `FLEET_URL`, `FLEET_ENROLLMENT_TOKEN`
5. Agent POSTs enrollment to Fleet Server `:8220`, gets policy back
6. Agent begins collecting system metrics and sends directly to Elasticsearch

**What Elastic Agent collects and how it formats it:**

From `/proc/stat` → CPU:
```json
{
  "@timestamp": "2024-01-15T10:30:00.000Z",
  "event": { "dataset": "system.cpu" },
  "host": { "hostname": "k8s-app" },
  "system.cpu.total.pct": 0.15,
  "system.cpu.user.pct": 0.08,
  "system.cpu.system.pct": 0.04,
  "system.cpu.iowait.pct": 0.02
}
```

From `/proc/meminfo` → memory:
```json
{
  "event": { "dataset": "system.memory" },
  "system.memory.total": 8589934592,
  "system.memory.used.bytes": 3221225472,
  "system.memory.actual.used.pct": 0.375,
  "system.memory.free": 2147483648
}
```

From `/proc/diskstats` → disk I/O:
```json
{
  "event": { "dataset": "system.diskio" },
  "system.diskio.name": "sda",
  "system.diskio.read.bytes": 104857600,
  "system.diskio.write.bytes": 52428800
}
```

From `/proc/net/dev` → network:
```json
{
  "event": { "dataset": "system.network" },
  "system.network.name": "eth0",
  "system.network.in.bytes": 1073741824,
  "system.network.out.bytes": 536870912,
  "system.network.in.errors": 0
}
```

All sent in ECS (Elastic Common Schema) JSON format directly to Elasticsearch → `metrics-*` index.

**Where to verify:**
- Kibana → Fleet → Agents: k8s-app agent listed as Healthy
- `kubectl logs -n app daemonset/elastic-agent` — enrollment and collection logs

---

## 6. Fluent-Bit (Log Pipeline)

Fluent-Bit is a lightweight C-based log processor. This project uses a **two-stage pipeline**: Collector on the app node, Receiver on the monitor node.

### What actually goes into `/var/log/containers/`

Every container's stdout/stderr is written there by containerd automatically. The raw line format on disk (CRI format):
```
2024-01-15T10:30:00.123456789Z stdout F <the actual log message>
```
Fields: `timestamp  stream(stdout/stderr)  logtag(F=full/P=partial)  message`

**What each service actually prints:**

**frontend** (Vue.js webpack dev server) — plain text:
```
2024-01-15T10:30:00.123Z stdout F  wds: Project is running at http://0.0.0.0:8080/
2024-01-15T10:30:01.456Z stdout F  wds: webpack compiled successfully
2024-01-15T10:30:05.789Z stdout F GET /api/login 200 5.2ms
```

**auth-api** (Go) — structured JSON:
```
2024-01-15T10:30:05.456Z stdout F {"time":"2024-01-15T10:30:05Z","level":"INFO","msg":"Login attempt","username":"user1","status":200,"latency":"3ms"}
2024-01-15T10:30:06.789Z stdout F {"time":"2024-01-15T10:30:06Z","level":"WARN","msg":"Invalid credentials","username":"hacker","status":401}
```

**todos-api** (Node.js) — mix of plain text:
```
2024-01-15T10:30:00.123Z stdout F Server running on port 8082
2024-01-15T10:30:05.456Z stdout F POST /todos 201 - 4ms
2024-01-15T10:30:05.460Z stdout F Published to log_channel: {"userId":"42","action":"create","todoId":"99"}
```

**users-api** (Java Spring Boot) — Spring format + multi-line stack traces:
```
2024-01-15T10:30:12.456Z stdout F 2024-01-15 10:30:12  INFO 1 --- [main] c.e.UsersApplication : Started in 12.3 seconds
2024-01-15T10:30:15.789Z stdout F 2024-01-15 10:30:15  INFO 1 --- [exec-1] c.e.UsersController : GET /users/42 200 OK
2024-01-15T10:30:16.000Z stderr P java.lang.NullPointerException: User not found
2024-01-15T10:30:16.000Z stderr P     at com.example.UsersController.getUser(UsersController.java:88)
2024-01-15T10:30:16.000Z stderr F     at com.example.UsersController.handleRequest(UsersController.java:55)
```
The `P` lines are partial — without the multiline fix these become 3 separate records. **After the multiline fix** they are stitched into one.

**redis** — Redis server internal messages:
```
2024-01-15T10:30:00.123Z stdout F 1:M 15 Jan 2024 10:30:00.000 * Ready to accept connections
2024-01-15T10:30:05.789Z stdout F 1:M 15 Jan 2024 10:30:05.789 - Accepted 10.244.1.16:52341
```

**log-processor** — processed Redis message output:
```
2024-01-15T10:30:00.123Z stdout F Connected to Redis at redis:6379
2024-01-15T10:30:05.460Z stdout F Received: {"userId":"42","action":"create","todoId":"99"}
2024-01-15T10:30:05.461Z stdout F Processed log event userId=42
```

---

### Stage 1 — Fluent-Bit Collector DaemonSet (`k8s/app/daemonset-fluent-bit.yaml`)

Pinned to `k8s-app`. Mounts `/var/log` from host (read-only).

**Pipeline:**
```ini
[INPUT]
    Name        tail
    Path        /var/log/containers/*.log
    Parser      cri                          ← splits CRI line into time/stream/logtag/log
    Tag         kube.<namespace>.<pod>.<container>
    Mem_Buf_Limit  5MB
    Skip_Long_Lines On

[FILTER]
    Name                  multiline          ← NEW: stitches Java stack traces into one record
    Match                 kube.*
    multiline.key_content log
    multiline.parser      java               ← built-in parser: recognises Spring Boot timestamps as line starters

[FILTER]
    Name               kubernetes           ← calls K8s API to add pod metadata
    Match              kube.*
    Kube_URL           https://kubernetes.default.svc:443
    Merge_Log          On                   ← if log field is valid JSON, promotes fields to top level
    Keep_Log           On                   ← always keep the original log string (fixed: was Off)
    Labels             On

[OUTPUT]
    Name    forward
    Match   kube.*
    Host    fluent-bit-receiver.monitor.svc.cluster.local
    Port    24224                            ← plain TCP within cluster, no TLS needed
```

ServiceAccount `fluent-bit-collector` has ClusterRole to `get/list/watch` pods and namespaces — required by the kubernetes filter.

---

### Stage 2 — Fluent-Bit Receiver (`k8s/moni/deployment-fluent-bit-receiver.yaml`)

Single-replica Deployment on `k8s-worker`. Receives from Collector, writes to Elasticsearch.

```ini
[INPUT]
    Name    forward
    Listen  0.0.0.0
    Port    24224

[FILTER]
    Name    modify
    Match   forwarded.*
    Add     observer.type fluent-bit-receiver

[OUTPUT]
    Name            es
    Host            es01.monitor.svc.cluster.local
    Port            9200
    HTTP_User       elastic
    HTTP_Passwd     ${ELASTIC_PASSWORD}     ← injected from monitor-secrets Secret
    Logstash_Format On
    Logstash_Prefix app
    Logstash_DateFormat %Y.%m.%d           ← creates app-YYYY.MM.DD daily indices
    Time_Key        @timestamp
    Suppress_Type_Name On                  ← required for ES 8+
    Generate_ID     On                     ← prevents duplicate docs on retry
    tls             On
    tls.verify      On
    tls.ca_file     /certs/ca/ca.crt       ← verifies ES self-signed cert
```

### What the final ES document looks like

**auth-api (structured JSON log) — `Merge_Log On` promotes JSON fields:**
```json
{
  "@timestamp": "2024-01-15T10:30:05.456Z",
  "stream": "stdout",
  "logtag": "F",
  "log": "{\"level\":\"INFO\",\"msg\":\"Login attempt\",...}",
  "level": "INFO",
  "msg": "Login attempt",
  "username": "user1",
  "status": 200,
  "latency": "3ms",
  "kubernetes": {
    "pod_name": "auth-api-xxx",
    "namespace_name": "app",
    "container_name": "auth-api",
    "pod_ip": "10.244.1.17",
    "host": "k8s-app",
    "labels": { "app": "auth-api" }
  },
  "observer.type": "fluent-bit-receiver"
}
```

**frontend (plain text log) — no JSON to merge, `log` field is the whole message:**
```json
{
  "@timestamp": "2024-01-15T10:30:05.789Z",
  "stream": "stdout",
  "logtag": "F",
  "log": "GET /api/login 200 5.2ms",
  "kubernetes": {
    "pod_name": "frontend-7d9f-xkzp2",
    "namespace_name": "app",
    "container_name": "frontend",
    "host": "k8s-app"
  }
}
```

**users-api (stack trace after multiline stitching) — all lines joined into one `log` field:**
```json
{
  "@timestamp": "2024-01-15T10:30:16.000Z",
  "stream": "stderr",
  "logtag": "F",
  "log": "java.lang.NullPointerException: User not found\n    at com.example.UsersController.getUser(UsersController.java:88)\n    at com.example.UsersController.handleRequest(UsersController.java:55)",
  "kubernetes": {
    "pod_name": "users-api-xxx",
    "namespace_name": "app",
    "container_name": "users-api",
    "host": "k8s-app"
  }
}
```

**Fields every document always has regardless of service:**
- `@timestamp` — from the CRI timestamp on the log line (the time the app wrote it, not ingestion time)
- `stream` — stdout or stderr
- `logtag` — F (full line)
- `log` — the raw original message string
- `kubernetes.pod_name`, `kubernetes.namespace_name`, `kubernetes.container_name`
- `kubernetes.pod_ip`, `kubernetes.host`, `kubernetes.labels.app`

---

## 7. Prometheus

**What it is:** A pull-based time-series metrics database. Prometheus sends HTTP GET to each target's `/metrics` every 10 seconds and stores the numeric response.

**Image:** `prom/prometheus:v3.11.3-distroless` — distroless means no shell (`date`, `sh` don't exist inside the container).

### Only ONE Prometheus in K8s

The single Prometheus runs in `monitor` namespace on `k8s-worker`. It reaches across to `app` namespace using cluster-wide Kubernetes DNS. There is no second Prometheus on k8s-app (that was only in the Docker Compose setup).

### Scrape Targets and What They Return

**`redis.app.svc.cluster.local:9121/metrics`** — redis_exporter sidecar (calls Redis `INFO ALL`, translates output):
```
redis_up 1
redis_connected_clients 3
redis_blocked_clients 0
redis_used_memory_bytes 1048576
redis_used_memory_peak_bytes 2097152
redis_total_commands_processed_total 4827
redis_keyspace_hits_total 200
redis_keyspace_misses_total 12
redis_pubsub_channels 1
redis_net_input_bytes_total 98304
```
Note: Prometheus scrapes port **9121**, NOT 6379. Redis itself has no `/metrics` endpoint.

**`localhost:9090/metrics`** — Prometheus self-monitoring:
```
prometheus_tsdb_head_series 1243
prometheus_scrape_duration_seconds{job="redis"} 0.003
up{job="redis"} 1
up{job="kube-state-metrics"} 1
```

**`kube-state-metrics.monitor.svc.cluster.local:8080/metrics`** — Kubernetes object state (pod health, deployment status, restart counts):
```
kube_pod_status_phase{namespace="app",pod="redis-xxx",phase="Running"} 1
kube_pod_container_status_restarts_total{namespace="app",container="redis"} 0
kube_deployment_status_replicas_unavailable{namespace="app",deployment="auth-api"} 0
kube_node_status_condition{node="k8s-app",condition="Ready",status="true"} 1
kube_persistentvolumeclaim_status_phase{namespace="app",persistentvolumeclaim="redis-data",phase="Bound"} 1
```
Unlike the other targets, kube-state-metrics does not scrape a running application — it reads the Kubernetes API directly and translates object state into Prometheus metrics. This is the only way to get automated alerts on pod crashes, CrashLoopBackOff, and node NotReady.

### Kibana Alerts Enabled by kube-state-metrics

Kube-state-metrics data flows into Elasticsearch via the **Fleet Prometheus integration** (Elastic Agent scrapes port 30780 NodePort). Metrics land in `metrics-prometheus.collector-default-*` index.

**Actual field structure in Elasticsearch:** `prometheus.<metric_name>.value` — e.g.:
- `prometheus.kube_deployment_status_replicas_unavailable`
- `prometheus.kube_pod_container_status_restarts_total`
- `prometheus.redis_up`

Create alerts in Kibana → Stack Management → Rules → Create rule → **Elasticsearch query**:

| Alert | Index | Field | Condition | Fires when |
|---|---|---|---|---|
| **Pod Down** *(active)* | `metrics-*` | `prometheus.kube_deployment_status_replicas_unavailable > 0` | >0 docs in last 2 min | Any deployment has unavailable replicas |
| CrashLoopBackOff | `metrics-*` | `prometheus.kube_pod_container_status_restarts_total > 5` | >1 doc in last 5 min | Container restarted more than 5 times |
| Node NotReady | `metrics-*` | `prometheus.kube_node_status_condition` | filter condition="Ready", value=0 | A node leaves the Ready state |

**`kubelet cAdvisor` (all nodes via API proxy)** — per-container CPU and memory, scraped from kubelet's built-in cAdvisor:
```
container_cpu_usage_seconds_total{namespace="app", pod="todos-api-xxx", container="todos-api"} 4.2
container_memory_working_set_bytes{namespace="app", pod="users-api-xxx", container="users-api"} 524288000
container_memory_usage_bytes{namespace="app", pod="redis-xxx", container="redis"} 10485760
```
Every metric has `namespace`, `pod`, and `container` labels — so you can filter in Kibana to see CPU/RAM for a specific pod. Scraped via the Kubernetes API server proxy (`/api/v1/nodes/{name}/proxy/metrics/cadvisor`) so no direct node port access is needed. The Prometheus ClusterRole grants `nodes/proxy` and `nodes/metrics` for this.

### Services NOT scraped by Prometheus

`frontend`, `auth-api`, `todos-api`, `users-api`, and `log-processor` have no Prometheus scrape job. The frontend's port 8080 serves the Vue.js UI and returns HTML at `/metrics` — not valid Prometheus format. All app service performance is visible through APM (Fleet Server) instead. The Kubernetes API server and Rancher scrape jobs were removed — their data is covered by kube-state-metrics and cAdvisor.

### Why remote_write is NOT used

Prometheus remote_write to Elasticsearch is **permanently incompatible** with ES 9.3.1:
- Prometheus (Go) uses `snappy.Encode()` which produces **snappy block** format
- ES 9.3.1 Netty `HttpContentDecoder` uses `SnappyFrameDecoder` which expects **snappy framing** format
- These two formats are fundamentally different — Prometheus sends `0xfe` as first byte (varint), ES expects `0xff 0x73 0x4e 0x61 0x50 0x70 0x59` (STREAM_IDENTIFIER)
- No Prometheus config option changes the snappy variant — `protobuf_message` only changes the protobuf schema, not the compression

**Result:** All metrics are visible in the **Prometheus UI only** (`http://192.168.96.136:30900`), except for data pulled via the Fleet Prometheus integration (see below).

### Fleet Prometheus Integration (Metrics → Kibana)

To get metrics into Kibana, Elastic Agent (running inside Fleet Server pod) scrapes the NodePort endpoints directly:

| Endpoint | NodePort | What it scrapes |
|---|---|---|
| `http://192.168.96.136:30780/metrics` | 30780 | kube-state-metrics: pod health, restart counts, deployment status |
| `http://192.168.96.136:30781/metrics` | 30781 | Redis exporter: redis_up, connected clients, memory, commands |

Data lands in Elasticsearch index: `.ds-metrics-prometheus.collector-default-*`

Configure via: Kibana → Fleet → Agent Policies → Fleet-Server-Policy → Add integration → **Prometheus**

### What `up=0` means

When a scraped pod is deleted or crashes, the next scrape fails. Prometheus records:
```
up{job="redis", instance="redis.app.svc.cluster.local:9121"} 0
```
This metric is stored in Prometheus local TSDB only (NOT in Elasticsearch). Visible in Prometheus UI → Status → Targets as `DOWN`.

### Where to Check

- `http://192.168.96.136:30900` → Status → Targets (UP/DOWN per job)
- Graph: type `up` to see all targets, `redis_connected_clients`, `rate(http_requests_total[5m])`
- Status → TSDB Status: cardinality / active series count

---

## 8. Zipkin (Distributed Tracing)

**What it is:** Tracks how a single request flows across multiple services and measures timing at each hop.

### How it runs in K8s

- **Image:** `openzipkin/zipkin:3.4`, pinned to `k8s-app`
- **Storage:** `STORAGE_TYPE=mem` — **all data lost on pod restart**
- **Port:** 9411 (NodePort 30411)

### How tracing works

All 5 app services (frontend, auth-api, todos-api, users-api, log-processor) have Zipkin client libraries. When a request comes in, the first service generates a **Trace ID** and sends it downstream via HTTP headers:
```
X-B3-TraceId: 4bf92f3577b34da6
X-B3-SpanId:  00f067aa0ba902b7
X-B3-Sampled: 1
```

Each service creates its own child span (same Trace ID) and POSTs it to `http://zipkin:9411/api/v2/spans`. Zipkin assembles all spans into a waterfall:
```
TraceId: 4bf92f3577b34da6  (total: 245ms)
│
├── frontend: POST /api/login       [0ms → 245ms]
│   ├── auth-api: POST /auth/login  [5ms → 240ms]
│   │   └── users-api: GET /users   [10ms → 180ms]
│   └── auth-api: JWT sign          [181ms → 200ms]
```

**Zipkin UI at `http://192.168.96.137:30411`:**
- Find Traces: filter by service, duration, time range
- Click a trace: waterfall view
- Dependencies: service graph from aggregated trace data

---

## 9. Redis (Pub/Sub + Todo Storage)

**What it is:** Key-value store used for two purposes: pub/sub event channel between services, and persistent storage for todos.

### How it runs in K8s

- **Image:** `redis:7-alpine`, AOF persistence enabled
- **Port:** 6379 (ClusterIP `redis`)
- **Sidecar:** `oliver006/redis_exporter:v1.62.0-alpine` on port 9121
- **Storage:** PVC `redis-data` (1 Gi, local-path) — mounted at `/data`
- **Config:** ConfigMap `redis-config` — `appendonly yes`, `appendfsync everysec`

### What Redis stores

```
todos:{username}  →  JSON blob of all todos for that user
                      e.g. todos:john = {"items":{"4":{"id":4,"content":"buy milk"}}, "lastInsertedID":5}
```

Written by `todos-api` on every create/delete. Survives Redis pod restarts via AOF replay.

### Pub/Sub flow (unchanged)

```
todos-api  ──PUBLISH log_channel──►  Redis  ──SUBSCRIBE──►  log-message-processor
```

Fire-and-forget: if log-processor is down when a message is published, the message is permanently lost. This is separate from todo storage — pub/sub messages are ephemeral by design.

### Redis Exporter sidecar

Both containers share the same pod network namespace. The exporter connects to Redis at `localhost:6379`, runs `INFO ALL`, translates to Prometheus format, exposes at `:9121/metrics`. Prometheus scrapes this. Metrics flow: Prometheus → Elasticsearch → Kibana.

---

## 11. Rancher (K8s Management UI)

**What it is:** Web-based Kubernetes management. GUI equivalent of `kubectl`.

- Installed via Helm into `cattle-system` namespace
- NodePort auto-created by Helm: HTTP:31307, HTTPS:31842
- Prometheus scrapes `/metrics` at `:443` using Bearer token from `rancher-token` Secret
- **Access:** `https://192.168.96.135:31842`

---

## 12. What Each Tool Monitors

| Tool | Monitors | Data comes from | Does NOT see |
|---|---|---|---|
| **Fluent-Bit** | Application logs (stdout/stderr) | `/var/log/containers/` on k8s-app host | Metrics, health status, pod lifecycle events |
| **Prometheus** | Numeric metrics over time | redis:9121, kube-state-metrics, kubelet cAdvisor (per-pod CPU/RAM) | auth-api, users-api, log-processor, frontend app metrics |
| **Elastic Agent** | Node CPU/RAM/disk/network | `/proc` and `/sys` on k8s-app host | Individual pod metrics, app logs |
| **Fleet Server APM** | Code-level traces and errors | auth-api, todos-api, users-api, log-processor push to `:8200` | frontend, redis — no APM agents |
| **Zipkin** | Request timing across services | All 5 services push spans to `:9411` | Logs, system resources, errors outside traced requests |
| **Kibana** | Displays logs + APM + metrics | Reads from Elasticsearch indices | Nothing — display only |
| **Rancher** | K8s pod/node/deployment state | Kubernetes API directly | Application-level data |

---

## 13. Data Flow Summary

### Container Log Flow
```
Pod stdout/stderr
    ↓ containerd writes → /var/log/containers/<pod>_<ns>_<container>-<id>.log (CRI format)
Fluent-Bit Collector (DaemonSet, k8s-app)
    ├─ [INPUT tail] reads files, cri parser splits into time/stream/logtag/log
    ├─ [FILTER multiline] stitches Java stack traces into single records
    ├─ [FILTER kubernetes] adds pod_name, namespace, labels from K8s API
    └─ [OUTPUT forward] → TCP:24224 → fluent-bit-receiver.monitor.svc.cluster.local
Fluent-Bit Receiver (Deployment, k8s-worker)
    ├─ [INPUT forward] receives MessagePack records
    ├─ [FILTER modify] adds observer.type
    └─ [OUTPUT es] → HTTPS:9200 → es01 (TLS verified, basic auth)
Elasticsearch → index app-YYYY.MM.DD
    ↓
Kibana → Observability → Logs  /  Discover → app-*
```

### Metrics Flow
```
redis:9121/metrics, kube-state-metrics:8080/metrics, kubelet cAdvisor (all nodes)
    ↓ HTTP GET every 10s (pull/scrape)
Prometheus (monitor namespace, k8s-worker)
    └─ stores in local TSDB only (7-day retention, LOST on pod restart — no remote_write)
       Viewable ONLY in Prometheus UI → http://192.168.96.136:30900

Elastic Agent (inside Fleet Server pod, k8s-worker) — Fleet Prometheus integration
    ├─ scrapes http://192.168.96.136:30780/metrics (kube-state-metrics NodePort)
    ├─ scrapes http://192.168.96.136:30781/metrics (Redis exporter NodePort)
    └─ writes → Elasticsearch → .ds-metrics-prometheus.collector-default-*
       Field structure: prometheus.<metric_name>.value
       Viewable in Kibana → Discover → metrics-prometheus.collector-default-*

Elastic Agent (DaemonSet, k8s-app)
    reads /proc + /sys → ECS JSON → DIRECTLY to Elasticsearch → metrics-*
    Viewable in Kibana → Infrastructure / Discover → metrics-*
```

### APM Flow
```
auth-api / todos-api / users-api / log-processor
    ↓ APM intake v2 (JSON over HTTPS)
    ↓ ELASTIC_APM_SERVER_URL = https://fleet-server.monitor.svc.cluster.local:8200
Fleet Server (k8s-worker) — embedded APM server on :8200
    ↓ validates APM secret token
    ↓ writes directly to Elasticsearch:
Elasticsearch → traces-apm-* / metrics-apm-* / logs-apm-*
    ↓
Kibana → APM → Services / Traces / Service Map
```

### Distributed Trace Flow (Zipkin)
```
Browser → frontend (creates TraceId)
    frontend POSTs span to zipkin:9411
    frontend calls auth-api (X-B3-TraceId header)
        auth-api creates child span, POSTs to zipkin
        auth-api calls users-api (X-B3-TraceId header)
            users-api creates child span, POSTs to zipkin
All spans (same TraceId) assembled by Zipkin → waterfall view
Storage: in-memory only — lost on restart
```

---

## 14. Monitoring Gaps

The current setup has **one active alert rule** and passive collection for everything else.

### Active Alert Rule (Kibana)

| Alert | Rule Type | Index | Condition | Status |
|---|---|---|---|---|
| **Pod Down** | Elasticsearch query | `metrics-*` | `prometheus.kube_deployment_status_replicas_unavailable > 0`, >0 docs in last 2 min | **Active** |

### What happens when a pod is deleted

| Tool | What it records | Where to see it |
|---|---|---|
| Prometheus | `up=0` recorded every 10s for that target | Status → Targets shows DOWN + error |
| kube-state-metrics | `kube_deployment_status_replicas_unavailable` goes to 1 | Prometheus UI + Kibana alert fires |
| Fluent-Bit | Last log lines before crash indexed, then silence | Kibana Logs — logs just stop |
| APM | Callers of the deleted service get connection errors → error traces | Kibana APM → Errors tab |
| Rancher | Pod shows Terminating → 0/1 Running in real time | Cluster → Workloads |
| Zipkin | Traces from callers show failed spans | Find Traces |

### What is still missing

| Missing | What it would provide |
|---|---|
| CrashLoopBackOff alert | Alert when `kube_pod_container_status_restarts_total > 5` |
| Node NotReady alert | Alert when `kube_node_status_condition` drops to 0 |
| Kubernetes Events integration | Explicit "pod deleted/crashlooped" events in Elasticsearch |
| Prometheus persistence | Prometheus loses all metrics on pod restart (using emptyDir) |

### Manual check commands when something seems wrong

```bash
# check which pods are actually running
kubectl get pods -n app
kubectl get pods -n monitor

# check Prometheus sees all targets as UP
# open http://192.168.96.136:30900/targets

# see last logs from a crashed pod
kubectl logs -n app deployment/todos-api --previous

# check ES is healthy
curl -k -u elastic:changeme https://192.168.96.136:30920/_cluster/health?pretty
```

---

## 15. Where to Check Each Data Type

| Data Type | Tool | Exact Location |
|---|---|---|
| Container logs | Kibana | Observability → Logs → index `app-*` |
| Specific pod logs | Kibana | Discover → filter `kubernetes.pod_name: todos-api-*` |
| Application errors | Kibana | APM → Services → [service] → Errors tab |
| APM traces | Kibana | APM → Services → [service] → Transactions → click one |
| Service call graph | Kibana | APM → Service Map |
| Prometheus metrics live | Prometheus UI | http://192.168.96.136:30900 → Graph → PromQL |
| Scrape job health | Prometheus UI | Status → Targets (UP/DOWN + last error) |
| `up=0` (pod down) | Prometheus UI | Graph: query `up` |
| Pod health (Kibana) | Kibana Discover | `metrics-prometheus.collector-default-*` → field `prometheus.kube_deployment_status_replicas_unavailable` |
| Metric dashboards | Kibana | Discover → `.ds-metrics-prometheus.collector-default-*` |
| Redis metrics (Kibana) | Kibana Discover | `metrics-prometheus.collector-default-*` → field `prometheus.redis_up` |
| Redis metrics (Prometheus) | Prometheus UI | PromQL: `redis_connected_clients`, `redis_used_memory_bytes` |
| Active alert status | Kibana | Stack Management → Rules → Pod Down Alert |
| Request trace waterfall | Zipkin | http://192.168.96.137:30411 → Find Traces → click trace |
| Service dependencies | Zipkin | Dependencies tab |
| Elastic Agent status | Kibana | Fleet → Agents |
| K8s cluster overview | Rancher | https://192.168.96.135:31842 |
| All ES indices + sizes | Terminal | `curl -k -u elastic:changeme "https://192.168.96.136:30920/_cat/indices?v&s=index"` |
| Log pipeline health | Terminal | `kubectl logs -n app daemonset/fluent-bit-collector` |
| Pod last logs before crash | Terminal | `kubectl logs -n app deployment/<name> --previous` |
| Prometheus config reload | Terminal | `curl -X POST http://192.168.96.136:30900/-/reload` |

---

## 16. Data Persistence Summary

| Service | Storage Type | What Is Stored | Survives Pod Restart? |
|---|---|---|---|
| **users-api** | H2 file mode (PVC `users-api-data`, 1Gi) | User accounts (username + bcrypt hash) | Yes |
| **todos-api** | Redis keys `todos:{username}` | Todo items per user as JSON | Yes (via Redis AOF) |
| **Redis** | AOF log (PVC `redis-data`, 1Gi) | Todos + pub/sub AOF journal | Yes |
| **Elasticsearch** | PVC `esdata01` (10Gi) | All logs, APM traces, metrics | Yes |
| **Kibana** | PVC `kibanadata` (5Gi) | Dashboards, saved searches, rules | Yes |
| **Zipkin** | None (`STORAGE_TYPE=mem`) | Distributed traces | No — lost on restart |
| **Prometheus** | None (`emptyDir`) | Time-series metrics scrape buffer | No — lost on restart |

**StorageClass used:** `local-path` (provisioned by `local-path-provisioner` on k8s-app).
All app PVCs bind to k8s-app because that is where the pods run (nodeSelector enforced).

### todos-api image rebuild required

todos-api was changed from `memory-cache` (JS heap) to Redis GET/SET for todo storage.
The new image must be built before deploying:

```bash
cd app/todos-api
docker build -t kan2nd/microapp:todos-api-redis .
docker push kan2nd/microapp:todos-api-redis
```

Deployment YAML already references `kan2nd/microapp:todos-api-redis`.

---

## 17. Secrets & Credentials Reference

| Secret | Namespace | Keys | Used by |
|---|---|---|---|
| `monitor-secrets` | `monitor` | `ELASTIC_PASSWORD`, `KIBANA_PASSWORD`, `ENCRYPTION_KEY`, `ELASTIC_APM_SECRET_TOKEN` | ES, Kibana, Fluent-Bit receiver, Prometheus (file-mounted) |
| `elastic-certs` | `monitor` | `ca.crt`, `es01.crt/key`, `kibana.crt/key`, `fleet-server.crt/key` | ES, Kibana, Fleet Server, Fluent-Bit receiver |
| `elastic-certs` | `app` | `ca.crt` only (copied from monitor namespace) | Elastic Agent (verify Fleet Server TLS on enrollment) |
| `elastic-apm-secret` | `app` | `elastic-password`, `apm-secret-token`, `fleet-enrollment-token` | Elastic Agent (enrollment token) |
| `app-secrets` | `app` | `JWT_SECRET` | auth-api, todos-api, users-api |
| `rancher-token` | `monitor` | `token` | **Unused** — Rancher scrape job was removed (token file was never created) |
