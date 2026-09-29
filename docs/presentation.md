# ELK2 Project — Presentation Guide

---

## Slide 1 — Title

**Microservices Observability on Kubernetes**
Full-stack monitoring with ELK, Prometheus, and APM

> 3-node Kubernetes cluster · 6 microservices · 5 observability tools

---

## Slide 2 — The Problem & Goal

### The Problem

Modern applications are built from many small services. When something breaks:

```
User reports: "The app is slow / I can't login / my todos disappeared"

Developer asks:
  - Which service failed?
  - What was the error message?
  - Was it slow? Was it down? Was it attacked?
  - When exactly did it happen?

Without observability → answer is: "I don't know. Check all the logs manually."
```

Traditional single-app logging does not scale to microservices:
- Each service writes logs separately
- No way to trace a request across 4 services at once
- No alert when a service goes down
- No visibility into CPU / memory pressure

### The Goal

Build a **centralized observability platform** that:

| Need | Solution |
|---|---|
| Collect logs from all services | Fluent-Bit pipeline → Elasticsearch |
| Trace one request across all services | Elastic APM + Zipkin |
| Monitor CPU, memory, Redis health | Elastic Agent + Prometheus |
| Visualize and search everything | Kibana |
| Alert on attacks and failures | Kibana alert rules |
| Persist application data | Redis AOF + H2 file mode |

---

## Slide 3 — Tools & Stack

### Infrastructure
| Tool | Role |
|---|---|
| **Kubernetes** (kubeadm, 3 nodes) | Container orchestration |
| **Flannel** | Pod networking (CNI) |
| **local-path-provisioner** | Dynamic PVC storage on bare metal |
| **Rancher** | Kubernetes management UI |

### Observability
| Tool | Version | Role |
|---|---|---|
| **Elasticsearch** | 9.3.1 | Central data store for all logs, traces, metrics |
| **Kibana** | 9.3.1 | Visualization, APM UI, alerting |
| **Fleet Server** | 9.3.1 | Elastic Agent control plane + APM intake (:8200) |
| **Elastic Agent** | 9.3.1 | Host-level system metrics (CPU, RAM, disk) |
| **Fluent-Bit** | 3.1.8 | Log collection and forwarding pipeline |
| **Prometheus** | 2.53.1 | Redis + pod health metrics scraping |
| **kube-state-metrics** | v2.12.0 | Kubernetes object state → pod health, restart counts |
| **Redis Exporter** | v1.62.0 | Translates Redis INFO → Prometheus format |
| **Zipkin** | 3.4 | Distributed request tracing UI |

### Application
| Service | Language | Role |
|---|---|---|
| **frontend** | Vue.js | User interface |
| **auth-api** | Go (Echo) | JWT login / signup |
| **todos-api** | Node.js (Express) | Todo CRUD |
| **users-api** | Java (Spring Boot) | User account storage (H2 DB) |
| **Redis** | — | Todo storage + pub/sub channel |
| **log-message-processor** | Python | Receives Redis events → Zipkin |

---

## Slide 4 — Cluster Architecture

*(Show your drawn diagram here)*

```
┌─────────────────────────────────────────────────────────────────────┐
│                     KUBERNETES CLUSTER                              │
│                                                                     │
│  ┌──────────────────┐    ┌──────────────────┐   ┌───────────────┐  │
│  │  k8s-control     │    │  k8s-worker      │   │  k8s-app      │  │
│  │  192.168.96.135  │    │  192.168.96.136  │   │ 192.168.96.137│  │
│  │                  │    │                  │   │               │  │
│  │  API Server      │    │  namespace:      │   │ namespace:    │  │
│  │  etcd            │    │  monitor         │   │ app           │  │
│  │  Scheduler       │    │                  │   │               │  │
│  │  Rancher         │    │  Elasticsearch   │   │ frontend      │  │
│  │                  │    │  Kibana          │   │ auth-api      │  │
│  └──────────────────┘    │  Fleet Server    │   │ todos-api     │  │
│                          │  Prometheus      │   │ users-api     │  │
│                          │  Fluent-Bit      │   │ redis         │  │
│                          │  receiver        │   │ log-processor │  │
│                          └──────────────────┘   │ fluent-bit    │  │
│                                                 │ elastic-agent │  │
│                                                 └───────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

Two namespaces enforce separation:
- **`app`** — application workloads, pinned to k8s-app
- **`monitor`** — all observability tools, pinned to k8s-worker

---

## Slide 5 — Application Flow

How a user action travels through the system:

```
Browser
  │
  ▼
frontend :8080  (Vue.js — serves UI, proxies API calls)
  │
  ├──► POST /auth/login ──► auth-api :8081 (Go — validates credentials)
  │                               │
  │                               └──► GET /users/{name} ──► users-api :8083
  │                                                              │
  │                                                         H2 database
  │                                                      (file: /data/usersdb)
  │                               ◄── JWT token ──────────────────┘
  │
  └──► POST /todos ──────► todos-api :8082 (Node.js — CRUD)
                                │
                         Redis GET/SET
                         todos:{username}   ← stored in Redis (AOF persistent)
                                │
                         PUBLISH log_channel
                                │
                                ▼
                      log-message-processor (Python)
                                │
                                ▼
                           Zipkin :9411
```

### Data Persistence
```
users-api  →  H2 file /data/usersdb  →  PVC users-api-data (1Gi)
todos-api  →  Redis SET todos:{user}  →  PVC redis-data (1Gi) via AOF
```

---

## Slide 6 — Log Pipeline

Every line printed by any container is collected and sent to Elasticsearch.

```
k8s-app node                          k8s-worker node
─────────────────────────────────     ────────────────────────────────────
Container writes to stdout/stderr
        │
        ▼
/var/log/containers/*.log
(CRI format: timestamp stream tag msg)
        │
        ▼
Fluent-Bit Collector (DaemonSet)
  [INPUT]  tail /var/log/containers
  [FILTER] multiline → stitch Java
           stack traces into 1 doc
  [FILTER] kubernetes → add pod name,
           namespace, labels
  [OUTPUT] forward ──────────────────► Fluent-Bit Receiver (Deployment)
                                              │
                                       [FILTER] add observer.type
                                       [OUTPUT] Elasticsearch
                                              │
                                              ▼
                                    index: app-YYYY.MM.DD
                                    (e.g. app-2026.05.09)
```

**What you see in Kibana → Discover → `app-*`:**
```
@timestamp            kubernetes.pod_name    log
2026-05-09 00:24:01   auth-api-5fd5-xyz      INFO: User 'john' authenticated
2026-05-09 00:24:01   auth-api-5fd5-xyz      WARN: Invalid credentials, status=401
```

---

## Slide 7 — APM & Distributed Tracing

Every HTTP request is automatically captured as a trace without changing app logic.

```
Browser request: POST /api/login
                        │
              ┌─────────▼──────────┐
              │  auth-api          │  ← Elastic APM Java/Go agent
              │  POST /login       │    captures: url, status, duration
              │                    │    injects trace.id into headers
              └─────────┬──────────┘
                        │  calls users-api
              ┌─────────▼──────────┐
              │  users-api         │  ← APM agent captures child span
              │  GET /users/john   │    same trace.id links them together
              └─────────┬──────────┘
                        │
              All spans sent to ──► Fleet Server :8200 (APM intake)
                                            │
                                            ▼
                              traces-apm-*        (HTTP spans)
                              metrics-apm-*       (JVM, CPU)
                              logs-apm.error-*    (exceptions)
```

**Same trace visible in 2 places:**
```
Kibana APM → waterfall view  (visual timeline of spans)
Zipkin UI  → waterfall view  (B3 propagation, same trace)
```

**Key APM fields for searching:**
```
trace.id                   → links all spans of one request
service.name               → "auth-api", "todos-api", "users-api"
http.response.status_code  → 200, 401, 500
event.outcome              → "success" or "failure"
transaction.duration.us    → response time in microseconds
```

---

## Slide 8 — Metrics

Two separate pipelines collect numeric metrics.

```
Pipeline A — System metrics (Elastic Agent)
─────────────────────────────────────────────
k8s-app host
  /proc/stat   ─┐
  /sys/         ─┴─► Elastic Agent (DaemonSet)
                           │
                     Fleet Server :8200
                           │
                           ▼
                     metrics-system.*      (CPU, RAM, disk, network)
                     metrics-elastic_agent.*

Fields:  system.cpu.total.norm.pct   (0.0 – 1.0)
         system.memory.actual.used.pct
         host.name: "k8s-app"


Pipeline B — Application metrics (Prometheus)
─────────────────────────────────────────────
Prometheus (k8s-worker) scrapes every 10s:

  redis :9121/metrics           → redis_up, redis_connected_clients
  kube-state-metrics :8080      → pod health, restart counts,
                                  deployment replica status, node status
  kubelet cAdvisor (all nodes)  → per-pod CPU usage, per-pod memory
                                  (labels: namespace, pod, container)

      │
      └─► remote_write ──► Elasticsearch /_prometheus/receive
                                    │
                              prometheus-*
```

---

## Slide 9 — Kibana: Visualization & Alerting

### Where to find each data type

| What | Kibana Path | Index |
|---|---|---|
| Application logs | Discover → `app-*` | `app-YYYY.MM.DD` |
| APM traces / errors | APM → Services | `traces-apm-*` |
| System CPU / RAM | Discover → `metrics-*` | `metrics-system.*` |
| Redis / Prometheus | Discover → `prometheus-*` | `prometheus-*` |
| Exception details | APM → Services → Errors | `logs-apm.error.*` |

### Active Alert Rules

```
Alert 1 — CPU High
  Index: metrics-*
  KQL:   system.cpu.total.norm.pct > 0.80
  Fires: > 8 matching docs in last 5 min (sustained high CPU)

Alert 2 — Brute Force Login
  Index: traces-apm-*
  KQL:   service.name:"auth-api" AND url.path:"/login" AND status_code:401
  Fires: > 5 failures in last 5 min

Alert 3 — Login Rate Flood
  Index: traces-apm-*
  KQL:   service.name:"auth-api" AND url.path:"/login"
  Fires: > 20 requests in last 1 min (any outcome)

Alert 4 — Redis Down
  Index: traces-apm-*
  KQL:   service.name:"todos-api" AND status_code:500
  Fires: > 2 errors in last 2 min
```

All rules use **Elasticsearch query** rule type — available on Basic (free) license.
Alerts write to `kibana-alerts` index (Index connector).

---

## Slide 10 — Demo Scenarios

| # | Scenario | What to trigger | What you see |
|---|---|---|---|
| 1 | Login with wrong account | Try login with unregistered user | APM: 401 on auth-api, WARN log in Fluent-Bit |
| 2 | Happy path | Register → login → add todo | APM: service map lights up, trace waterfall shows all 3 services |
| 3 | Brute force simulation | `for i in {1..10}; do curl POST /login...` | APM: spike in error rate, Alert 2 fires |
| 4 | Kill Redis pod | `kubectl delete pod -n app -l app=redis` | Prometheus: `redis_up=0`, APM: 500s on todos-api, Alert 4 fires |
| 5 | CPU stress | Hammer users-api with 50 parallel logins | APM: latency climbs on auth-api waterfall, Alert 1 fires |

**For each scenario, show in this order:**
1. Kibana APM → Services → transaction list
2. Kibana Discover → `app-*` → filter by container name
3. Prometheus UI → relevant metric graph
4. Kibana → Stack Management → Rules → alert fired

---

## Quick Reference — NodePorts

| Service | URL | Login |
|---|---|---|
| Kibana | https://192.168.96.136:30601 | elastic / changeme |
| Elasticsearch | https://192.168.96.136:30920 | elastic / changeme |
| Prometheus | http://192.168.96.136:30900 | none |
| Fleet Server | https://192.168.96.136:30820 | none |
| Frontend App | http://192.168.96.137:30880 | register first |
| Zipkin | http://192.168.96.137:30411 | none |
| Rancher | https://192.168.96.135:31842 | set on first login |
