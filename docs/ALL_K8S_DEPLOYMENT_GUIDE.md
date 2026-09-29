# From-Scratch Deployment Guide — ELK2 on Kubernetes

Everything runs on Kubernetes — no separate Docker Compose machine needed.

---

## Cluster Layout

| Node | IP | Role | What runs there |
|---|---|---|---|
| k8s-control | 192.168.96.135 | Control plane + Rancher | API server, etcd, scheduler, Rancher |
| k8s-worker | 192.168.96.136 | Monitoring workloads | Elasticsearch, Kibana, Fleet Server, Prometheus, Fluent-Bit receiver |
| k8s-app | 192.168.96.137 | Application workloads | All microservices, Fluent-Bit collector, Elastic Agent |

```
┌─────────────────────────────────────────────────────────────────┐
│                KUBERNETES CLUSTER (~11 GB total)                │
│                                                                 │
│  k8s-control (5 GB)                                            │
│    Kubernetes API, etcd, scheduler, Rancher                    │
│                                                                 │
│  k8s-worker (3 GB)       namespace: monitor                    │
│    Elasticsearch (2 GB heap, 10 Gi PVC)                        │
│    Kibana (5 Gi PVC)                                           │
│    Fleet Server — has embedded APM server on :8200             │
│    Prometheus → remote_write → Elasticsearch                   │
│    Fluent-Bit receiver (receives logs from k8s-app)            │
│    kube-state-metrics (pod/deployment health → Prometheus)     │
│                                                                 │
│  k8s-app (3 GB)          namespace: app                        │
│    frontend, auth-api, todos-api, users-api                    │
│    redis (+ redis_exporter sidecar :9121)                      │
│    zipkin, log-message-processor                               │
│    Fluent-Bit collector DaemonSet  ← reads /var/log/containers │
│    Elastic Agent DaemonSet         ← reads /proc + /sys        │
└─────────────────────────────────────────────────────────────────┘
```

**Critical distinction:** Elastic Agent collects host-level system metrics (CPU/RAM/disk/network) and sends them directly to Elasticsearch. It does NOT handle APM. APM data (traces, errors, service metrics) is received by the embedded APM server inside Fleet Server on port 8200.

---

## External Access (NodePorts)

NodePorts are accessible on any node IP. Canonical access:

| Service | NodePort | URL | Notes |
|---|---|---|---|
| Kibana | 30601 | https://192.168.96.136:30601 | Login: elastic / changeme |
| Elasticsearch | 30920 | https://192.168.96.136:30920 | Login: elastic / changeme |
| Fleet Server | 30820 | https://192.168.96.136:30820 | Agent enrollment |
| APM Server | 30200 | https://192.168.96.136:30200 | APM intake (in Fleet Server pod) |
| Prometheus | 30900 | http://192.168.96.136:30900 | No auth |
| kube-state-metrics | 30780 | http://192.168.96.136:30780/metrics | Scraped by Fleet Elastic Agent |
| Redis exporter | 30781 | http://192.168.96.136:30781/metrics | Scraped by Fleet Elastic Agent |
| Rancher | 31842 | https://192.168.96.135:31842 | Set on first login |
| Frontend App | 30880 | http://192.168.96.137:30880 | No auth |
| Zipkin | 30411 | http://192.168.96.137:30411 | No auth |

---

## Phase 0 — VM Setup (all 3 nodes)

Minimum per VM: 4 vCPU, 8 GB RAM, 50 GB disk, Ubuntu 22.04 / Kali Linux, static IP.

Run on **every node:**

```bash
# Set hostname — change the name to match each node
sudo hostnamectl set-hostname k8s-control   # or k8s-worker / k8s-app

# Add all nodes to /etc/hosts on every node
cat <<'EOF' | sudo tee -a /etc/hosts
192.168.96.135 k8s-control
192.168.96.136 k8s-worker
192.168.96.137 k8s-app
EOF

# Disable swap (kubelet requires this)
sudo swapoff -a
sudo sed -i '/swap/d' /etc/fstab

# Kernel modules required for K8s networking
cat <<'EOF' | sudo tee /etc/modules-load.d/k8s.conf
overlay
br_netfilter
EOF
sudo modprobe overlay
sudo modprobe br_netfilter

# Sysctl settings for K8s networking
cat <<'EOF' | sudo tee /etc/sysctl.d/k8s.conf
net.bridge.bridge-nf-call-iptables  = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward                 = 1
EOF
sudo sysctl --system
```

---

## Phase 1 — Install containerd (all 3 nodes)

```bash
sudo apt-get update
sudo apt-get install -y containerd

# Configure containerd to use systemd cgroup driver (required for kubeadm)
sudo mkdir -p /etc/containerd
containerd config default | sudo tee /etc/containerd/config.toml
sudo sed -i 's/SystemdCgroup = false/SystemdCgroup = true/' /etc/containerd/config.toml
sudo systemctl restart containerd
sudo systemctl enable containerd
```

---

## Phase 2 — Install kubeadm / kubelet / kubectl (all 3 nodes)

```bash
sudo apt-get install -y apt-transport-https ca-certificates curl gpg
curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.31/deb/Release.key \
  | sudo gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg
echo 'deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.31/deb/ /' \
  | sudo tee /etc/apt/sources.list.d/kubernetes.list
sudo apt-get update
sudo apt-get install -y kubelet kubeadm kubectl
sudo apt-mark hold kubelet kubeadm kubectl
sudo systemctl enable kubelet
```

---

## Phase 3 — Initialize Control Plane (k8s-control only)

```bash
sudo kubeadm init \
  --control-plane-endpoint=192.168.96.135 \
  --pod-network-cidr=10.244.0.0/16 \
  --apiserver-advertise-address=192.168.96.135

# Set up kubeconfig
mkdir -p $HOME/.kube
sudo cp /etc/kubernetes/admin.conf $HOME/.kube/config
sudo chown $(id -u):$(id -g) $HOME/.kube/config

# IMPORTANT: save the "kubeadm join ..." command printed at the end
```

---

## Phase 4 — Install CNI: Flannel (k8s-control only)

```bash
kubectl apply -f https://github.com/flannel-io/flannel/releases/latest/download/kube-flannel.yml

# Wait for control plane node to become Ready before joining workers
kubectl get nodes -w
```

---

## Phase 5 — Join Worker Nodes (run on k8s-worker and k8s-app)

```bash
# Use the exact join command from Phase 3 output:
sudo kubeadm join 192.168.96.135:6443 \
  --token <TOKEN> \
  --discovery-token-ca-cert-hash sha256:<HASH>
```

Back on k8s-control, verify:
```bash
kubectl get nodes
# Expected: k8s-control (control-plane), k8s-worker (worker), k8s-app (worker) — all Ready
```

The `kubernetes.io/hostname` label is set automatically from the OS hostname. The nodeSelector fields in the manifests rely on this being correct.

---

## Phase 6 — Install local-path-provisioner (k8s-control only)

Provides dynamic PVC provisioning on bare metal (used by Elasticsearch, Kibana, Redis, users-api).

```bash
kubectl apply -f https://raw.githubusercontent.com/rancher/local-path-provisioner/v0.0.30/deploy/local-path-storage.yaml

# Make it the default StorageClass
kubectl patch storageclass local-path \
  -p '{"metadata":{"annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}'

kubectl get storageclass
```

---

## Phase 7 — Set vm.max_map_count on k8s-worker (for Elasticsearch)

Elasticsearch requires this kernel parameter to be ≥ 262144. SSH in and run once, then make it persistent:

```bash
ssh kali@192.168.96.136
sudo sysctl -w vm.max_map_count=262144
echo 'vm.max_map_count=262144' | sudo tee /etc/sysctl.d/99-elasticsearch.conf
exit
```

---

## Phase 8 — Generate TLS Certificates (run anywhere with openssl)

The ES cert must include SANs for both `es01` (short name, used inside the cluster) and `es01.monitor.svc.cluster.local` (FQDN, used by elastic-agent across namespaces).

```bash
mkdir -p certs && cd certs

# ── CA ─────────────────────────────────────────────────────────────────
openssl genrsa -out ca.key 4096
openssl req -x509 -new -nodes -key ca.key -sha256 -days 3650 -out ca.crt \
  -subj "/CN=elastic-ca/O=elastic"

# ── Elasticsearch ───────────────────────────────────────────────────────
cat > es01.cnf <<'EOF'
[req]
req_extensions = v3_req
distinguished_name = req_distinguished_name
[req_distinguished_name]
[v3_req]
subjectAltName = @alt_names
[alt_names]
DNS.1 = es01
DNS.2 = es01.monitor.svc.cluster.local
DNS.3 = localhost
IP.1  = 127.0.0.1
IP.2  = 192.168.96.136
EOF
openssl genrsa -out es01.key 2048
openssl req -new -key es01.key -out es01.csr -subj "/CN=es01" -config es01.cnf
openssl x509 -req -in es01.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out es01.crt -days 3650 -sha256 -extensions v3_req -extfile es01.cnf

# ── Kibana ──────────────────────────────────────────────────────────────
cat > kibana.cnf <<'EOF'
[req]
req_extensions = v3_req
distinguished_name = req_distinguished_name
[req_distinguished_name]
[v3_req]
subjectAltName = @alt_names
[alt_names]
DNS.1 = kibana
DNS.2 = kibana.monitor.svc.cluster.local
DNS.3 = localhost
IP.1  = 127.0.0.1
IP.2  = 192.168.96.136
EOF
openssl genrsa -out kibana.key 2048
openssl req -new -key kibana.key -out kibana.csr -subj "/CN=kibana" -config kibana.cnf
openssl x509 -req -in kibana.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out kibana.crt -days 3650 -sha256 -extensions v3_req -extfile kibana.cnf

# ── Fleet Server ────────────────────────────────────────────────────────
cat > fleet-server.cnf <<'EOF'
[req]
req_extensions = v3_req
distinguished_name = req_distinguished_name
[req_distinguished_name]
[v3_req]
subjectAltName = @alt_names
[alt_names]
DNS.1 = fleet-server
DNS.2 = fleet-server.monitor.svc.cluster.local
DNS.3 = localhost
IP.1  = 127.0.0.1
IP.2  = 192.168.96.136
EOF
openssl genrsa -out fleet-server.key 2048
openssl req -new -key fleet-server.key -out fleet-server.csr \
  -subj "/CN=fleet-server" -config fleet-server.cnf
openssl x509 -req -in fleet-server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out fleet-server.crt -days 3650 -sha256 -extensions v3_req -extfile fleet-server.cnf

cd ..
```

---

## Phase 9 — Copy Manifests to k8s-control

```bash
scp -r k8s/ kali@192.168.96.135:~/k8s/
scp -r certs/ kali@192.168.96.135:~/certs/
```

SSH into k8s-control for the remaining phases:
```bash
ssh kali@192.168.96.135
```

---

## Phase 10 — Create Namespaces and Secrets

```bash
# 1. Namespaces
kubectl apply -f ~/k8s/namespace.yaml

# 2. All TLS certs → monitor namespace
kubectl create secret generic elastic-certs -n monitor \
  --from-file=ca.crt=~/certs/ca.crt \
  --from-file=es01.crt=~/certs/es01.crt \
  --from-file=es01.key=~/certs/es01.key \
  --from-file=kibana.crt=~/certs/kibana.crt \
  --from-file=kibana.key=~/certs/kibana.key \
  --from-file=fleet-server.crt=~/certs/fleet-server.crt \
  --from-file=fleet-server.key=~/certs/fleet-server.key

# 3. CA only → app namespace (elastic-agent needs it to verify Fleet Server TLS)
kubectl create secret generic elastic-certs -n app \
  --from-file=ca.crt=~/certs/ca.crt

# 4. Monitor stack passwords and config
kubectl apply -f ~/k8s/moni/configM/configmap.yaml
# This creates:
#   monitor-config ConfigMap  (STACK_VERSION, CLUSTER_NAME, LICENSE=basic,
#                              MONITOR_VM_IP=192.168.96.136)
#   monitor-secrets Secret    (ELASTIC_PASSWORD, KIBANA_PASSWORD,
#                              ENCRYPTION_KEY, ELASTIC_APM_SECRET_TOKEN)
# NOTE: LICENSE and MONITOR_VM_IP keys are required — without them ES and
#       Kibana fail with CreateContainerConfigError on every pod restart.

# 5. App JWT secret
kubectl apply -f ~/k8s/app/secret-app-jwt.yaml

# 6. APM secret — placeholder for now, real token filled in Phase 13
kubectl apply -f ~/k8s/app/secret-elastic-apm.yaml
```

---

## Phase 11 — Deploy Monitoring Stack (k8s-worker node)

Deploy in strict order — each service depends on the previous one.

```bash
# Kibana ConfigMap first (Fleet config, SSL settings)
kubectl apply -f ~/k8s/moni/configM/kibana-configmap.yaml

# Elasticsearch — everything depends on it; takes 2–3 minutes to become healthy
kubectl apply -f ~/k8s/moni/deployment-elasticsearch.yaml
kubectl wait --for=condition=ready pod -l app=es01 -n monitor --timeout=300s

# Verify ES is healthy before continuing
curl -sk -u elastic:changeme https://192.168.96.136:30920/_cluster/health?pretty
# Must show "status": "green" before proceeding

# Kibana — Fleet Server depends on it
kubectl apply -f ~/k8s/moni/deployment-kibana.yaml
kubectl wait --for=condition=ready pod -l app=kibana -n monitor --timeout=300s

# Fleet Server (calls Kibana Fleet setup API on startup; may restart 1–2x — normal)
kubectl apply -f ~/k8s/moni/deployment-fleet-server.yaml

# Remaining monitor components
kubectl apply -f ~/k8s/moni/deployment-fluent-bit-receiver.yaml
kubectl apply -f ~/k8s/moni/deployment-prometheus-monitoring.yaml
kubectl apply -f ~/k8s/moni/nodeport-services.yaml

# kube-state-metrics — enables pod health alerting in Kibana
kubectl apply -f ~/k8s/moni/kube-state-metrics.yaml

kubectl get pods -n monitor
# Expected: es01, kibana, fleet-server, fluent-bit-receiver, prometheus-monitoring,
#           kube-state-metrics — all Running
# Note: Grafana removed — use Kibana for all dashboards and alerting
```

---

## Phase 12 — Build todos-api Image with Redis Storage

todos-api was rewritten to store todos in Redis (persistent) instead of memory-cache (lost on restart).
The new image must be built and pushed before deploying the app stack.

```bash
# On any machine with Docker and access to the source code
cd app/todos-api

docker build -t kan2nd/microapp:todos-api-redis .
docker push kan2nd/microapp:todos-api-redis
```

The deployment YAML already references `kan2nd/microapp:todos-api-redis`.
If you use a different registry or tag, update `deployment-todos-api.yaml` accordingly.

---

## Phase 12b — Deploy Application Stack (k8s-app node)

```bash
kubectl apply -f ~/k8s/app/

# Watch pods — users-api takes ~3.5 minutes (JPA + Hibernate + Spring Security init)
kubectl get pods -n app -w
# Press Ctrl+C when all pods show Running

# All expected pods:
# frontend, auth-api, todos-api, users-api, redis (2/2 containers),
# zipkin, log-message-processor, fluent-bit-collector, elastic-agent

# Verify PVCs were created and bound
kubectl get pvc -n app
# Expected: redis-data (Bound, 1Gi), users-api-data (Bound, 1Gi)
```

**What persists after this phase:**
- User accounts: H2 database written to `/data/usersdb` on the users-api PVC
- Redis pub/sub events: AOF log written to `/data` on the Redis PVC (replayed on restart)
- Todos: stored as JSON in Redis keys `todos:{username}`, survive Redis pod restarts via AOF

---

## Phase 13 — Set Real Fleet Enrollment Token

The elastic-agent needs a real enrollment token from Kibana Fleet. The placeholder from Phase 10 won't work.

```
1. Open https://192.168.96.136:30601
2. Login: elastic / changeme
3. Go to: Management → Fleet → Enrollment Tokens
4. Find the token for "Default Policy"
5. Copy the full token string
```

```bash
kubectl create secret generic elastic-apm-secret -n app \
  --from-literal=apm-secret-token=supersecrettoken \
  --from-literal=fleet-enrollment-token=<PASTE_TOKEN_HERE> \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl rollout restart daemonset/elastic-agent -n app
kubectl logs -n app daemonset/elastic-agent | grep -i "enroll"
# Must see: "Successfully enrolled"
```

---

## Phase 13b — Install Rancher (optional, if not already installed)

If the cluster was provisioned via Rancher, skip to Step 3.

```bash
# Step 1: Install cert-manager (skip if cert-manager namespace already exists)
kubectl get ns cert-manager || \
  kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.14.4/cert-manager.yaml
kubectl wait --for=condition=ready pod -l app.kubernetes.io/instance=cert-manager \
  -n cert-manager --timeout=300s

# Install Helm
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash

helm repo add rancher-stable https://releases.rancher.com/server-charts/stable
helm repo update
helm install rancher rancher-stable/rancher \
  --namespace cattle-system --create-namespace \
  --set hostname=192.168.96.135 \
  --set bootstrapPassword=changeme \
  --set replicas=1

# Step 2: Scale rancher-webhook FIRST (Rancher calls it during init)
kubectl scale deployment rancher-webhook -n cattle-system --replicas=1
kubectl rollout status deployment/rancher-webhook -n cattle-system --timeout=120s

# Step 3: Pin Rancher to k8s-control (k8s-worker is too full for it)
kubectl patch deployment rancher -n cattle-system --type=json -p='[
  {"op": "add", "path": "/spec/template/spec/nodeSelector",
   "value": {"kubernetes.io/hostname": "k8s-control"}},
  {"op": "add", "path": "/spec/template/spec/tolerations",
   "value": [{"key": "node-role.kubernetes.io/control-plane",
              "operator": "Exists", "effect": "NoSchedule"}]}
]'

# Step 4: Scale Rancher up
kubectl scale deployment rancher -n cattle-system --replicas=1
kubectl rollout status deployment/rancher -n cattle-system --timeout=300s
```

**First Rancher login:**
1. Open `https://192.168.96.135:31842` (accept cert warning)
2. Enter bootstrap password `changeme`
3. Set a permanent password
4. Confirm server URL as `https://192.168.96.135:31842`

**Enable Rancher → Prometheus metrics:**
```bash
# Rancher UI → avatar (top right) → API Keys → Add Key → copy the Bearer Token
kubectl create secret generic rancher-token -n monitor \
  --from-literal=token='<PASTE_BEARER_TOKEN>' \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl rollout restart deployment/prometheus-monitoring -n monitor
# Status → Targets in Prometheus should show rancher job as UP
```

---

## Phase 14 — End-to-End Verification

```bash
# No pods in CrashLoopBackOff or Pending
kubectl get pods -A | grep -vE "Running|Completed"

# ES cluster health
curl -sk -u elastic:changeme https://192.168.96.136:30920/_cluster/health?pretty
# "status": "green"

# Indices exist (give it 1–2 minutes after deploy)
curl -sk -u elastic:changeme "https://192.168.96.136:30920/_cat/indices?v&s=index"
# Should show: app-YYYY.MM.DD, metrics-*, .fleet-*

# Prometheus targets
# Open http://192.168.96.136:30900/targets
# UP: prometheus, redis, kube-state-metrics, kubelet-cadvisor (one entry per node)

# Generate traffic — open the frontend
# http://192.168.96.137:30880 → Sign up → Log in → Create a todo

# After traffic:
# Kibana → Observability → Logs → app-* → container logs visible
# Kibana → Observability → APM → Services → todos-api, auth-api, users-api visible
# Kibana → Management → Fleet → Agents → elastic-agent shows "Healthy"

# Zipkin traces
# http://192.168.96.137:30411 → Find Traces → select service → Run Query
```

---

## Day-2 Operations

### After VM reboot — startup order

Start VMs in this order:
1. k8s-control — wait ~60 seconds for API server
2. k8s-worker
3. k8s-app

Kubernetes automatically reschedules all pods — no `kubectl apply` needed.

```bash
# If monitor pods stay Pending (node was cordoned):
kubectl uncordon k8s-worker

# If fleet-server CrashLoopBackOff after reboot (ES took too long):
kubectl rollout restart deployment/fleet-server -n monitor

# If Rancher pods do not come back:
kubectl scale deployment rancher-webhook -n cattle-system --replicas=1
kubectl scale deployment rancher -n cattle-system --replicas=1
```

### Re-apply a config change

```bash
# Edit the file locally, copy it up, then apply:
scp k8s/app/deployment-users-api.yaml kali@192.168.96.135:~/k8s/app/
ssh kali@192.168.96.135 \
  "kubectl apply -f ~/k8s/app/deployment-users-api.yaml && \
   kubectl rollout restart deployment/users-api -n app"
```

### Delete old log indices (manual log rotation)

Fluent-Bit creates one index per day: `app-YYYY.MM.DD`. Delete when disk is low.

```bash
# List indices with sizes
curl -sk -u elastic:changeme \
  "https://192.168.96.136:30920/_cat/indices/app-*?v&s=index&h=index,store.size,docs.count"

# Delete a specific day
curl -sk -u elastic:changeme -X DELETE \
  "https://192.168.96.136:30920/app-2024.01.01"
```

---

## Troubleshooting

### Elasticsearch not starting

```bash
kubectl logs -n monitor -l app=es01 --tail=50
# Common fix: vm.max_map_count too low on k8s-worker
ssh kali@192.168.96.136 "sudo sysctl -w vm.max_map_count=262144"

kubectl get pvc -n monitor
# esdata01 must show Bound — if Pending, check local-path-provisioner
```

### No logs appearing in Kibana

```bash
kubectl logs -n app daemonset/fluent-bit-collector --tail=30
# Look for: "failed to connect to fluent-bit-receiver" or parse errors

kubectl logs -n monitor deployment/fluent-bit-receiver --tail=30
# Look for: "es: failed to flush" or TLS/auth errors

curl -sk -u elastic:changeme "https://192.168.96.136:30920/_cat/indices/app-*?v"
# If no index exists after 2 minutes, receiver is not reaching ES
```

### Elastic Agent not enrolling

```bash
kubectl logs -n app daemonset/elastic-agent | grep -iE "enroll|error|fleet"
# "Fleet Server is not healthy" → Fleet Server not ready yet, wait and retry
# "x509" error → CA cert not mounted correctly, check elastic-certs secret in app namespace

# Recreate secret with correct token
kubectl delete secret elastic-apm-secret -n app
kubectl create secret generic elastic-apm-secret -n app \
  --from-literal=apm-secret-token=supersecrettoken \
  --from-literal=fleet-enrollment-token=<CORRECT_TOKEN>
kubectl rollout restart daemonset/elastic-agent -n app
```

### No APM data in Kibana

```bash
kubectl logs -n monitor deployment/fleet-server --tail=30 | grep -i "apm\|error"

# Test APM endpoint reachable from app namespace
kubectl exec -n app deployment/auth-api -- \
  curl -sk https://fleet-server.monitor.svc.cluster.local:8200/ | head -5
# Should return {"name":"fleet-server","version":"...","build_date":"..."}
```

### Pod in CrashLoopBackOff

```bash
kubectl describe pod <pod-name> -n <namespace>
# Check Events at the bottom — OOMKilled, ImagePullBackOff, etc.

kubectl logs <pod-name> -n <namespace> --previous
# Logs from the crashed container instance
```

### users-api keeps restarting

This is the most common startup issue. Root causes and fixes:

| Symptom | Cause | Fix |
|---|---|---|
| OOMKilled | JVM heap exceeded 512Mi limit | Memory limit must be 1Gi; add `JAVA_OPTS=-Xmx384m -XX:MaxMetaspaceSize=128m` |
| Probe failing immediately | Hibernate schema init takes 150s+ | `initialDelaySeconds: 210` for liveness, `180` for readiness |
| HTTP 500 on liveness path | JwtAuthenticationFilter blocks `/health` | Use `tcpSocket` probe instead of `httpGet` |

### Memory pressure on k8s-app

```bash
kubectl top pods -n app --sort-by=memory
# users-api is the heaviest (~600–900Mi)

# If OOMKilled events appear:
kubectl get events -n app --sort-by='.lastTimestamp' | grep -i oom

# Scale down non-critical pods temporarily
kubectl scale deployment log-message-processor --replicas=0 -n app
```

---

## Known Issues and Fixes (Applied During Development)

| Issue | Root Cause | Fix |
|---|---|---|
| users-api CrashLoopBackOff — wrong probe path | Probe used `/actuator/health` (Spring Boot 2.x). Spring Boot 1.5.x serves health at `/health` | Changed probe path; then switched to tcpSocket |
| users-api CrashLoopBackOff — JWT filter blocks probe | `JwtAuthenticationFilter` applies to all paths except `/users/register` and `/users/login` | Switched to `tcpSocket` probe |
| users-api OOMKill | JVM + Hibernate + APM agent exceeds 512Mi | Raised limit to 1Gi; added `JAVA_OPTS=-Xmx384m` |
| users-api slow startup — probe fires too early | JPA + schema export takes 150s+ under CPU throttle | `initialDelaySeconds=210`; CPU limit raised to 500m |
| frontend OOMKill | webpack + Vue.js compilation needs ~400–600MB; 256Mi kills it | Raised limit to 512Mi; CPU to 500m; probe delays to 180s |
| Elasticsearch Init:ImagePullBackOff | busybox initContainer rate-limited by Docker Hub | Removed initContainer; set `vm.max_map_count=262144` on host |
| fluent-bit-collector CrashLoopBackOff | `DB` directive attempted write to read-only `/var/log` mount | Removed `DB` directive from config |
| All pods using wrong namespaces | Original manifests used `monitoring`/`applications` | Renamed to `monitor`/`app` throughout |
| Prometheus no auth for remote_write | ES requires basic auth on `/_prometheus/receive` | Added `password_file` mount from monitor-secrets Secret |
| Grafana removed | Kibana Basic license covers all alerting needs; Grafana removed to free 256Mi RAM on k8s-worker | Deleted deployment-grafana.yaml and grafana nodeport |
| fleet-server "service_tokens not available" | ES was down when Kibana Fleet API was called | Symptom of ES being down — fix ES first, fleet-server self-heals |
| monitor pods Pending after reboot | k8s-worker was cordoned by a Rancher drain event | `kubectl uncordon k8s-worker` |
| x509 hostname mismatch (agent→ES) | ES cert SANs only included `es01` and `localhost`; agents connect via FQDN | Added `verification_mode: certificate` in Fleet output config |
| Prometheus scraping auth-api → 404 | Go Echo app has no `/metrics` endpoint | Removed auth-api scrape job; covered by APM instead |
| Prometheus scraping users-api → 500 | `JwtAuthenticationFilter` blocks `/metrics` | Removed users-api scrape job; covered by APM instead |
| Rancher evicted from k8s-worker (SIGTERM 2s) | k8s-worker already full; memory pressure causes immediate pod eviction | Patched Rancher with `nodeSelector: k8s-control` + control-plane toleration |
| Rancher CrashLoopBackOff — webhook refused | `rancher-webhook` at replicas=0; Rancher calls it during init | Scale `rancher-webhook` to 1 before scaling `rancher` to 1 |
| cert-manager duplicate ImagePullBackOff | Re-applying cert-manager.yaml when it already exists creates duplicate ReplicaSet | Skip cert-manager install if namespace already exists |
| fluent-bit 409 version_conflict on retry | Fluent-Bit retrying already-indexed chunk after ES restart | Harmless — self-clears once fluent-bit advances past the chunk |
| Prometheus remote_write EOF | Transient connection reset during ES restart | Harmless — self-heals once ES stabilizes |
| Todo list not refreshing after create | Vue.js state not updated after mutation | Expected behavior — app re-fetches on re-login only; designed as APM demo |

---

## Repository File Structure

```
k8s/
├── namespace.yaml                         ← Creates monitor + app namespaces
│
├── app/
│   ├── configmap-app-config.yaml          ← Service addresses, ports, Redis/Zipkin URLs
│   ├── secret-app-jwt.yaml                ← JWT_SECRET
│   ├── secret-elastic-apm.yaml            ← APM token + Fleet enrollment token (update in Phase 13)
│   ├── deployment-frontend.yaml           ← Vue.js (tcpSocket probe, 512Mi RAM, 500m CPU)
│   ├── deployment-auth-api.yaml           ← Go JWT service (httpGet /version probe)
│   ├── deployment-todos-api.yaml          ← Node.js todos (tcpSocket probe, image: todos-api-redis)
│   ├── deployment-users-api.yaml          ← Spring Boot 1.5 (tcpSocket, 1Gi RAM, JAVA_OPTS, H2 file PVC)
│   ├── deployment-redis.yaml              ← Redis + redis_exporter sidecar on :9121 + AOF PVC
│   ├── deployment-log-processor.yaml      ← Redis subscriber
│   ├── deployment-zipkin.yaml             ← Zipkin (in-memory storage)
│   ├── daemonset-fluent-bit.yaml          ← Log collector → fluent-bit-receiver
│   ├── daemonset-elastic-agent.yaml       ← System metrics → Elasticsearch (via Fleet)
│   ├── service-*.yaml                     ← ClusterIP services per microservice
│   └── nodeport-services.yaml             ← frontend:30880, zipkin:30411
│
├── rancher/
│   └── secret-rancher-token.yaml          ← Template: fill in Rancher API token for Prometheus
│
└── moni/
    ├── configM/
    │   ├── configmap.yaml                 ← monitor-config + monitor-secrets (set passwords here)
    │   └── kibana-configmap.yaml          ← kibana.yml — Fleet config, SSL, APM enabled
    ├── deployment-elasticsearch.yaml      ← ES 9.3.1, TLS, 10Gi PVC, single-node
    ├── deployment-kibana.yaml             ← Kibana 9.3.1, HTTPS, 5Gi PVC
    ├── deployment-fleet-server.yaml       ← Fleet control plane + APM intake on :8200
    ├── deployment-fluent-bit-receiver.yaml ← Receives logs on :24224 → writes to ES
    ├── deployment-prometheus-monitoring.yaml ← Prometheus + RBAC + all scrape jobs
    └── nodeport-services.yaml             ← External NodePort services for all monitor components

app/                                       ← Application source code (pre-built Docker images on Hub)
├── frontend/                              ← Vue.js
├── auth-api/                              ← Go (Echo framework)
├── todos-api/                             ← Node.js Express
├── users-api/                             ← Java Spring Boot 1.5.6
└── log-message-processor/                ← Python Redis subscriber
```

---

## Quick Reference

```bash
# Pod status
kubectl get pods -n app
kubectl get pods -n monitor
kubectl get pods -A | grep -vE "Running|Completed"

# Restart a deployment
kubectl rollout restart deployment/<name> -n app
kubectl rollout restart deployment/<name> -n monitor

# Live logs
kubectl logs -n app deployment/todos-api -f
kubectl logs -n monitor deployment/fluent-bit-receiver -f

# Describe (shows events, resource usage, probe failures)
kubectl describe pod -n app -l app=users-api
kubectl describe node k8s-app

# ES health and indices
curl -sk -u elastic:changeme https://192.168.96.136:30920/_cluster/health?pretty
curl -sk -u elastic:changeme "https://192.168.96.136:30920/_cat/indices?v&s=index"

# Count today's logs
curl -sk -u elastic:changeme \
  "https://192.168.96.136:30920/app-$(date +%Y.%m.%d)/_count"

# Prometheus targets
curl http://192.168.96.136:30900/api/v1/targets \
  | python3 -m json.tool | grep -E '"job"|"health"'

# Node resource usage
kubectl top nodes
kubectl top pods -n app --sort-by=memory
kubectl top pods -n monitor --sort-by=memory

# Recent events (useful after crashes)
kubectl get events -n app --sort-by='.lastTimestamp' | tail -20
kubectl get events -n monitor --sort-by='.lastTimestamp' | tail -20

# Previous container logs (after CrashLoopBackOff)
kubectl logs -n app deployment/users-api --previous
```
