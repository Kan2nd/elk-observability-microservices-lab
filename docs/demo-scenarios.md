# Demo Scenarios — Step by Step

How to trigger each scenario and exactly where to see the result in Kibana, Prometheus, and Zipkin.

---

## Scenario 1 — Login with Wrong Account (401 Error)

**What it tests:** APM error capture, log pipeline, Fluent-Bit delivery.

### Trigger
Open the frontend `http://192.168.96.137:30880` and try to log in with a username that does not exist.

Or use curl:
```bash
curl -s -X POST http://192.168.96.137:30880/api/login \
  -H "Content-Type: application/json" \
  -d '{"username":"nonexistent","password":"wrongpass"}'
```

### Where to see it

**Kibana APM → Services → auth-api**
- Transactions → `POST /login` → status 401
- Error rate spikes

**Kibana Discover → `app-*`**
- Filter: `kubernetes.container_name: auth-api`
- Look for log line: `WARN: Invalid credentials` or similar

**Zipkin** `http://192.168.96.137:30411`
- Find Traces → Service: `auth-api` → Run Query
- Click the trace → waterfall shows auth-api calling users-api, returns 401

---

## Scenario 2 — Happy Path (Register → Login → Add Todo)

**What it tests:** Full service map, distributed trace across 3 services, Redis storage, pub/sub pipeline.

### Trigger
1. Open `http://192.168.96.137:30880`
2. Click **Register** → create a new account
3. Log in with the new account
4. Add a todo item

### Where to see it

**Kibana APM → Services**
- All three services appear: `auth-api`, `users-api`, `todos-api`
- Click any service → Transactions → see the individual request

**Kibana APM → Service Map** (if available)
- Shows `frontend → auth-api → users-api` and `frontend → todos-api` connected

**Zipkin** `http://192.168.96.137:30411`
- Find Traces → Run Query → click any trace
- Waterfall shows: frontend → auth-api → users-api (for login)
- Second trace: todos-api → log-message-processor (for todo add)

**Kibana Discover → `app-*`**
- Filter: `kubernetes.namespace: app`
- See logs from all services for that timestamp

**Verify Redis stored the todo:**
```bash
kubectl exec -n app -it $(kubectl get pod -n app -l app=redis -o jsonpath='{.items[0].metadata.name}') \
  -c redis -- redis-cli KEYS "todos:*"
# Shows: todos:<your-username>

kubectl exec -n app -it $(kubectl get pod -n app -l app=redis -o jsonpath='{.items[0].metadata.name}') \
  -c redis -- redis-cli GET "todos:<your-username>"
# Shows: JSON with your todo items
```

---

## Scenario 3 — Brute Force Login Simulation

**What it tests:** Alert 2 (Brute Force) and Alert 3 (Login Rate Flood), APM error rate spike.

### Trigger
```bash
# 10 rapid failed login attempts
for i in $(seq 1 10); do
  curl -s -X POST http://192.168.96.137:30880/api/login \
    -H "Content-Type: application/json" \
    -d '{"username":"hacker","password":"wrongpass"}' &
done
wait
echo "Done"
```

### Where to see it

**Kibana APM → Services → auth-api**
- Transaction error rate jumps sharply
- Transactions list shows a burst of `POST /login` with status 401

**Kibana Discover → `traces-apm-*`**
- KQL: `service.name: "auth-api" AND http.response.status_code: 401`
- See all the 401 transactions with timestamps

**Kibana → Stack Management → Rules → Brute Force Login**
- If > 5 failures in 5 min: status changes from `OK` → `Active`
- Check Execution History tab

**Kibana Discover → `kibana-alerts`**
- See the fired alert document if the Index connector is configured

---

## Scenario 4 — Kill Redis Pod (Service Failure)

**What it tests:** Alert 4 (Redis Down), Prometheus `redis_up` metric drop, APM 500 errors, pod self-healing.

### Trigger
```bash
kubectl delete pod -n app -l app=redis
```

### What happens
1. Redis pod is deleted — Kubernetes immediately starts a new one
2. During the ~10-30 seconds until Redis restarts, any todo request returns 500
3. After Redis restarts, it replays the AOF file and restores all todos

### Where to see it

**Prometheus UI** `http://192.168.96.136:30900/graph`
- Query: `redis_up`
- Value drops from 1 → 0, then returns to 1 when pod is back

**Kibana Discover → `metrics-*`**
- KQL: `redis_up: 0`
- See the exact timestamp when Redis was down

**Kibana APM → Services → todos-api**
- Error rate spike during the Redis-down window
- Transactions show `POST /todos` returning 500

**Kibana → Stack Management → Rules → Redis Down**
- Alert fires if > 2 errors from todos-api in 2 minutes

**Watch pod restart:**
```bash
kubectl get pods -n app -l app=redis -w
# STATUS: Terminating → Pending → ContainerCreating → Running
```

**Verify data survived (AOF replay):**
```bash
kubectl exec -n app -it $(kubectl get pod -n app -l app=redis -o jsonpath='{.items[0].metadata.name}') \
  -c redis -- redis-cli KEYS "todos:*"
# All todos are still there — AOF file was replayed on startup
```

---

## Scenario 5 — CPU Stress (Alert 1)

**What it tests:** Alert 1 (CPU High), Elastic Agent node metrics, APM latency increase.

### Trigger
Hammer users-api with parallel login requests:

```bash
# 50 concurrent requests — runs for ~30 seconds
for i in $(seq 1 50); do
  curl -s -X POST http://192.168.96.137:30880/api/login \
    -H "Content-Type: application/json" \
    -d '{"username":"loadtest","password":"test"}' &
done
wait
echo "Done"
```

Or use a loop to sustain the load longer:
```bash
for round in $(seq 1 5); do
  for i in $(seq 1 20); do
    curl -s http://192.168.96.137:30880/api/login \
      -H "Content-Type: application/json" \
      -d '{"username":"x","password":"x"}' &
  done
  wait
  sleep 2
done
```

### Where to see it

**Kibana Discover → `metrics-system.*`**
- KQL: `host.name: "k8s-app"`
- Field: `system.cpu.total.norm.pct` — watch it climb toward 1.0

**Kibana APM → Services → auth-api**
- Average latency increases as CPU is saturated
- Transaction waterfall shows longer spans for each step

**Kibana APM → Services → users-api**
- JVM metrics show increased CPU and possibly GC pressure

**Kibana → Stack Management → Rules → CPU High**
- Alert fires if `system.cpu.total.norm.pct > 0.80` sustained over 5 min

**Prometheus UI** `http://192.168.96.136:30900/graph`
```
rate(container_cpu_usage_seconds_total{namespace="app", container="users-api"}[2m])
```
Shows per-container CPU rate during the stress test.

---

## Scenario 6 — Pod Crash (kube-state-metrics alert)

**What it tests:** kube-state-metrics alert (Pod Down), pod self-healing.

### Trigger
```bash
kubectl delete pod -n app -l app=todos-api
```

### Where to see it

**Kibana → Stack Management → Rules → Pod Down**
- `kube_deployment_status_replicas_unavailable` goes from 0 → 1
- Alert fires within 2 minutes

**Prometheus UI** `http://192.168.96.136:30900/graph`
- Query: `kube_deployment_status_replicas_unavailable{namespace="app"}`
- Spikes to 1, returns to 0 once Kubernetes replaces the pod

**kubectl watch:**
```bash
kubectl get pods -n app -w
# todos-api: Terminating → ContainerCreating → Running
```

---

## Quick Reference — Show Order for Each Scenario

For any scenario, present in this order:

```
1. Kibana APM → Services → transaction list / error rate
2. Kibana Discover → app-* → container logs at that timestamp
3. Prometheus UI → relevant metric graph
4. Kibana → Stack Management → Rules → alert status
5. Kibana Discover → kibana-alerts → fired alert document
```

---

## NodePorts Quick Reference

| Service | URL |
|---|---|
| Frontend | http://192.168.96.137:30880 |
| Kibana | https://192.168.96.136:30601 (elastic / changeme) |
| Prometheus | http://192.168.96.136:30900 |
| Zipkin | http://192.168.96.137:30411 |
| Elasticsearch | https://192.168.96.136:30920 (elastic / changeme) |
