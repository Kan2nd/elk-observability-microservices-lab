# Project Improvement Suggestions

Honest gaps in the current project, with simple tutorials where the fix is straightforward.

---

## Table of Contents

1. [Security](#1-security)
   - 1a. NetworkPolicy — restrict pod-to-pod traffic
   - 1b. ResourceQuota — cap namespace resource usage
2. [Observability](#2-observability)
   - 2a. Prometheus PVC — don't lose metric history on restart
   - 2b. Kibana alert — disk space full
   - 2c. Kibana alert — JVM heap pressure (users-api)
   - 2d. Frontend RUM — wire the browser APM agent
   - 2e. Zipkin → Elasticsearch backend
3. [Architecture](#3-architecture)
   - 3a. replicas: 2 + rolling updates
4. [What Requires More Learning (No Simple Tutorial)](#4-what-requires-more-learning)

---

## 1. Security

### 1a. NetworkPolicy — restrict pod-to-pod traffic

**Problem:** Every pod in every namespace can reach every other pod. A compromised pod can freely call Elasticsearch, Redis, or the Kubernetes API.

**What it does:** Kubernetes NetworkPolicy is like a firewall rule for pods. You declare which pods are allowed to send/receive traffic, and everything else is denied.

**Tutorial — allow only monitor namespace pods to reach Elasticsearch:**

Create `k8s/moni/networkpolicy-elasticsearch.yaml`:

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: elasticsearch-ingress
  namespace: monitor
spec:
  podSelector:
    matchLabels:
      app: elasticsearch
  policyTypes:
  - Ingress
  ingress:
  # allow from any pod in the monitor namespace
  - from:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: monitor
  # allow from any pod in the app namespace (Fluent-Bit receiver needs to write logs)
  - from:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: app
```

Apply it:
```bash
kubectl apply -f k8s/moni/networkpolicy-elasticsearch.yaml
```

> **Note:** NetworkPolicy requires a CNI that supports it. Flannel alone does NOT enforce NetworkPolicy. You would need to add Calico or use `flannel + canal`. This is a learning exercise — implementing it on the current cluster would require replacing Flannel.

---

### 1b. ResourceQuota — cap namespace resource usage

**Problem:** One misbehaving pod (e.g., a memory leak in users-api) can consume all node memory and starve every other pod on the node.

**What it does:** Kubernetes ResourceQuota sets a hard ceiling on how much CPU and memory the entire namespace can request/use.

**Tutorial:**

Create `k8s/app/resourcequota-app.yaml`:

```yaml
apiVersion: v1
kind: ResourceQuota
metadata:
  name: app-quota
  namespace: app
spec:
  hard:
    requests.cpu: "1"        # total CPU requests across all pods in app namespace
    requests.memory: 1Gi     # total memory requests
    limits.cpu: "2"          # total CPU limits
    limits.memory: 2Gi       # total memory limits
```

Apply it:
```bash
kubectl apply -f k8s/app/resourcequota-app.yaml
```

Check current usage vs quota:
```bash
kubectl describe resourcequota app-quota -n app
```

> **Note:** Every container in the namespace must then have `resources.requests` and `resources.limits` defined, or Kubernetes will reject the pod. All current deployments already have these set.

---

## 2. Observability

### 2a. Prometheus PVC — don't lose metric history on restart

**Problem:** Prometheus currently uses `emptyDir` for its data directory. When the pod restarts (node reboot, OOMKill, update), all metric history is gone.

**What it does:** Attach a PersistentVolumeClaim so Prometheus data survives pod restarts.

**Tutorial:**

Edit `k8s/moni/deployment-prometheus.yaml`. Find the `volumes:` section and replace the emptyDir entry:

```yaml
# BEFORE (if it exists as emptyDir):
volumes:
- name: prometheus-data
  emptyDir: {}

# AFTER:
volumes:
- name: prometheus-data
  persistentVolumeClaim:
    claimName: prometheus-data
```

Add a PVC definition at the bottom of the same file (or a separate file):

```yaml
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: prometheus-data
  namespace: monitor
spec:
  accessModes:
  - ReadWriteOnce
  storageClassName: local-path
  resources:
    requests:
      storage: 5Gi
```

Apply:
```bash
kubectl apply -f k8s/moni/deployment-prometheus.yaml
kubectl rollout restart deployment/prometheus -n monitor
```

Verify:
```bash
kubectl get pvc -n monitor
```

---

### 2b. Kibana alert — disk space full

**Problem:** When Elasticsearch's PVC fills up, ES silently goes read-only. No more logs are written. No alert fires. You only notice when Kibana Discover stops showing new data.

**What it does:** Alert when any host's disk is more than 80% full, using data already collected by Elastic Agent.

**Tutorial — create in Kibana UI:**

1. Kibana → **Stack Management** → **Rules** → **Create rule**
2. Name: `Disk Space High`
3. Rule type: **Elasticsearch query**
4. Index: `metrics-*`
5. Time field: `@timestamp`
6. Query (KQL):
   ```
   system.filesystem.used.pct > 0.80 AND host.name: "k8s-app"
   ```
7. Threshold: More than **2** documents in the last **5 minutes**
8. Check every: **1 minute**
9. Action: **Index connector** → write to `kibana-alerts`
10. Save

> The field `system.filesystem.used.pct` is a decimal from 0.0 to 1.0, where 0.80 = 80%.
> Change `host.name` to `"k8s-worker"` to monitor the Elasticsearch node instead.

---

### 2c. Kibana alert — JVM heap pressure (users-api)

**Problem:** users-api runs on the JVM (Spring Boot / H2). If the heap fills up, the pod gets OOMKilled without warning.

**What it does:** Alert when users-api JVM heap usage is high, using data sent by the Elastic APM Java agent.

**Tutorial — create in Kibana UI:**

1. Kibana → **Stack Management** → **Rules** → **Create rule**
2. Name: `users-api JVM Heap High`
3. Rule type: **Elasticsearch query**
4. Index: `metrics-apm.*`
5. Time field: `@timestamp`
6. Query (KQL):
   ```
   service.name: "users-api" AND jvm.memory.heap.used > 90000000
   ```
   > 90000000 bytes = ~85 MB. Adjust to ~80% of your pod's memory limit (128Mi limit → alert at ~100Mi = 104857600).
7. Threshold: More than **3** documents in the last **3 minutes**
8. Action: **Index connector** → write to `kibana-alerts`
9. Save

Verify data exists first (run in Kibana Dev Tools):
```json
GET metrics-apm.*/_search
{
  "query": {
    "term": { "service.name": "users-api" }
  },
  "_source": ["jvm.memory.heap.used", "@timestamp"],
  "size": 3
}
```

---

### 2d. Frontend RUM — wire the browser APM agent

**Problem:** `@elastic/apm-rum` is already in `app/frontend/package.json` but not initialized. You get zero visibility into what happens in the user's browser: page load time, JS errors, user sessions.

**What it does:** The RUM agent runs in the browser, captures page loads and AJAX calls, and sends traces to Fleet Server. They appear in Kibana APM under the `frontend` service.

**Tutorial:**

Edit `app/frontend/src/main.js` (or wherever Vue is initialized). Add at the very top before anything else:

```javascript
import { init as initApm } from '@elastic/apm-rum'

const apm = initApm({
  serviceName: 'frontend',
  serverUrl: 'http://192.168.96.136:30820',   // Fleet Server NodePort
  serviceVersion: '1.0.0',
  environment: 'production',
})
```

Rebuild the frontend image:
```bash
cd app/frontend
docker build -t kan2nd/microapp:frontend-rum .
docker push kan2nd/microapp:frontend-rum
```

Update `k8s/app/deployment-frontend.yaml`:
```yaml
image: kan2nd/microapp:frontend-rum
```

Apply:
```bash
kubectl apply -f k8s/app/deployment-frontend.yaml
```

After deploying, open the app in a browser, then check Kibana APM → Services. You should see a `frontend` service appear within a minute.

> **Note:** The `serverUrl` must be reachable from the browser (not a cluster-internal address). Use the NodePort address.

---

### 2e. Zipkin → Elasticsearch backend

**Problem:** Zipkin stores traces in memory. Every pod restart wipes all trace history.

**What it does:** Zipkin supports Elasticsearch as a storage backend. Since you already have ES running, this costs nothing extra and gives Zipkin persistent trace storage.

**Tutorial:**

Edit `k8s/app/deployment-zipkin.yaml`. Add environment variables to the Zipkin container:

```yaml
env:
- name: STORAGE_TYPE
  value: "elasticsearch"
- name: ES_HOSTS
  value: "https://elasticsearch.monitor.svc.cluster.local:9200"
- name: ES_USERNAME
  value: "elastic"
- name: ES_PASSWORD
  valueFrom:
    secretKeyRef:
      name: monitor-secrets     # this secret is in monitor namespace
      key: ELASTIC_PASSWORD
- name: ES_SSL_NO_VERIFY
  value: "true"                 # self-signed cert
```

Apply:
```bash
kubectl apply -f k8s/app/deployment-zipkin.yaml
```

> **Cross-namespace secret access problem:** The Zipkin pod is in `app` namespace but the secret is in `monitor` namespace. Kubernetes does not allow cross-namespace secret references. Options:
> - Duplicate the secret into `app` namespace: `kubectl get secret monitor-secrets -n monitor -o yaml | sed 's/namespace: monitor/namespace: app/' | kubectl apply -f -`
> - Or hardcode the password as a plain value (acceptable for a learning project)

After applying, open Zipkin UI. New traces will be stored in ES under `zipkin*` indices. Old in-memory traces are gone.

---

## 3. Architecture

### 3a. replicas: 2 + rolling updates

**Problem:** Every deployment has `replicas: 1`. When a pod restarts (during `kubectl apply`, a node reboot, or an OOMKill), the service is down until the new pod is Ready. With `replicas: 2`, Kubernetes keeps one pod running while the other restarts.

**What it does:** Running 2 replicas with a rolling update strategy means zero downtime during normal restarts.

**Tutorial — update any stateless service (e.g., auth-api):**

Edit `k8s/app/deployment-auth-api.yaml`:

```yaml
spec:
  replicas: 2                       # was 1
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxUnavailable: 0             # never go below desired count during update
      maxSurge: 1                   # allow 1 extra pod during the transition
  selector:
    matchLabels:
      app: auth-api
```

Apply:
```bash
kubectl apply -f k8s/app/deployment-auth-api.yaml
```

Watch the rollout:
```bash
kubectl rollout status deployment/auth-api -n app
```

> **Which services can safely use replicas: 2?**
> - `frontend` — yes, stateless
> - `auth-api` — yes, stateless (JWT signing key is env var)
> - `todos-api` — yes, stateless (data is in Redis)
> - `users-api` — yes for reads, but H2 is file-based and does not support concurrent writers from 2 pods. Keep at 1 until migrated to PostgreSQL.
> - `redis` — no, single-instance Redis does not support replicas via Deployment. Use Redis Sentinel or Redis Cluster instead.
> - `log-message-processor` — yes, stateless

---

## 4. What Requires More Learning

These improvements are real and important, but they each require learning a new tool or concept before you can implement them. No short tutorial is included — these are topics for future study.

| Topic | Why It Matters | What to Learn |
|---|---|---|
| **GitOps with ArgoCD** | Every change is currently manual `kubectl apply`. ArgoCD watches a git repo and auto-deploys on push. | ArgoCD docs, Helm charts |
| **Secrets management (Vault / ESO)** | Passwords in YAML files committed to git is the #1 security mistake in Kubernetes projects. | HashiCorp Vault, External Secrets Operator |
| **Ingress + TLS** | NodePorts don't scale past ~10 services and can't do path-based routing or automatic HTTPS. | nginx-ingress, cert-manager, Let's Encrypt |
| **OpenTelemetry** | The industry is moving away from vendor-specific agents (Elastic APM, Zipkin B3) toward the OpenTelemetry standard. One agent, any backend. | OpenTelemetry Collector, OTLP protocol |
| **Service Mesh (Istio)** | Automatic mTLS, traffic shaping, canary deployments, circuit breaking — all without changing app code. | Istio, Envoy proxy |
| **Load testing (k6)** | You can't know if your alerts are calibrated correctly without generating controlled load. k6 scripts simulate hundreds of users. | k6 scripting, Kibana dashboards under load |
| **PostgreSQL in Kubernetes** | H2 is fine for learning but not deployable. Real stateful databases in K8s require init containers, connection pooling (PgBouncer), and migration tooling. | StatefulSet, Flyway, PostgreSQL operator |
| **Chaos engineering (LitmusChaos)** | Beyond manually deleting pods, chaos tools randomly kill pods/nodes/networks on a schedule to test resilience. | LitmusChaos, chaos experiments |
| **Backup with Velero** | Currently there is no backup of any persistent data. If the node disk dies, everything is gone. | Velero, S3-compatible object storage |

---

## Quick Priority Guide

If you had to pick one thing to learn next based on impact:

```
Security focus    → Secrets management (Vault / ESO)
Operations focus  → GitOps with ArgoCD
Networking focus  → Ingress + TLS with nginx-ingress
Observability+    → OpenTelemetry Collector
Architecture+     → Service Mesh (Istio)
```

The tutorials in sections 1–3 above can be done right now with what you already know. The topics in section 4 each need a dedicated learning session first.
