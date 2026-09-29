Scenario 1 — Login with unregistered account
What happens in the system:


Browser → frontend → auth-api POST /auth/login
                          → users-api GET /users/{username}
                          ← 404 Not Found (user doesn't exist)
                     ← 401 Unauthorized
← "Invalid credentials"
Tool	Where	What to search/filter	What you should see
Kibana APM	APM → Services → auth-api → Transactions	POST /auth/login	Transaction with HTTP 4xx, latency spike to users-api
Kibana APM	APM → Services → auth-api → Overview	—	Error rate % increases
Kibana Logs	Discover → app-*	kubernetes.container_name: auth-api + level: WARN	Log line: "msg":"Invalid credentials","status":401
Zipkin	Find Traces → service: auth-api	—	Trace waterfall: auth-api span → users-api span → failed


Scenario 2 — Signup → Login → Add Todo (happy path)
What happens:


POST /users/register   → users-api (creates user)
POST /auth/login       → auth-api → users-api → JWT issued
POST /todos            → todos-api → PUBLISH to Redis log_channel
                                   → log-processor receives it
Tool	Where	What to search/filter	What you should see
Kibana APM	APM → Service Map	—	Arrows lighting up: frontend→auth-api→users-api, frontend→todos-api
Kibana APM	APM → Services → todos-api → Transactions	POST /todos	HTTP 2xx, short latency
Kibana APM	APM → Traces	—	Full trace: all service spans under one trace.id
Kibana Logs	Discover → app-*	kubernetes.container_name: log-processor	"Received:" then "Processed log event userId=..."
Kibana Logs	Discover → app-*	kubernetes.container_name: todos-api	"Published to log_channel"
Zipkin	Find Traces → frontend	—	Full waterfall: all 3 services in one trace
Prometheus	Graph	redis_total_commands_processed_total	Counter increments with each PUBLISH


Scenario 3 — 10 failed logins in rapid succession (brute force sim)
Do this by sending 10 rapid wrong-credential logins, either from the browser or:


for i in {1..10}; do
  curl -s -X POST http://192.168.96.137:30880/api/login \
    -H "Content-Type: application/json" \
    -d '{"username":"fake'$i'","password":"wrong"}' &
done
Tool	Where	What to search/filter	What you should see
Kibana APM	APM → Services → auth-api → Overview	—	Sudden spike in error rate % and transaction throughput
Kibana APM	APM → Services → auth-api → Transactions → POST /auth/login	—	10 transactions all HTTP 4xx within seconds
Kibana Logs	Discover → app-*	kubernetes.container_name: auth-api AND level: WARN	10 "Invalid credentials" lines clustered in the same second
Kibana APM	APM → Services → auth-api → Transactions → set time to "Last 5 min"	Sort by count	POST /auth/login jumps to top with high count
Zipkin	Find Traces → auth-api → set time range narrow	—	10 traces all failed, all near-identical timestamps
Scenario 4 — Kill a pod
Suggested pod: redis — gives the most visual coverage across the most tools:


kubectl delete pod -n app -l app=redis
# K8s immediately restarts it, but during ~30s downtime:
Tool	Where	What to search/filter	What you should see
Prometheus	Graph	redis_up	Drops to 0, recovers when pod restarts
Prometheus	Status → Targets	redis job	Shows DOWN + last error message
Kibana APM	APM → Services → todos-api → Errors	—	"connection refused" or "ECONNREFUSED redis:6379" errors appear
Kibana APM	APM → Services → todos-api → Transactions	POST /todos	Transactions switch from HTTP 2xx to HTTP 5xx
Kibana Logs	Discover → app-*	kubernetes.container_name: log-processor	"Connection lost" or reconnect messages
Kibana Logs	Discover → app-*	kubernetes.container_name: redis	Logs stop → then restart sequence messages
Rancher	Cluster → Workloads → app namespace	—	Redis pod: Terminating → ContainerCreating → Running
Prometheus	Graph	redis_pubsub_channels	Drops to 0 during downtime
Why redis is best: it cascades — killing it breaks todos-api (APM errors), kills the pub/sub channel (Prometheus metric), stops log-processor (Fluent-Bit logs stop), AND Prometheus shows it immediately with redis_up=0.

Scenario 5 — Stress CPU to show latency impact
Suggested target: users-api — already tight on CPU (500m limit, Java JVM). Stressing it shows latency ripple into auth-api traces.

Run a CPU stress inside the pod:


kubectl exec -n app deployment/users-api -- sh -c \
  "apt-get install -y stress 2>/dev/null; stress --cpu 2 --timeout 60s &"
Or simply hammer it with login requests (each login hits users-api):


for i in {1..50}; do
  curl -s -X POST http://192.168.96.137:30880/api/login \
    -H "Content-Type: application/json" \
    -d '{"username":"testuser","password":"password"}' &
done
Tool	Where	What to search/filter	Distinguishes CPU vs Network?
Kibana APM	APM → Services → users-api → Overview	—	transaction.duration.us climbs → this is total latency
Kibana APM	APM → Services → auth-api → Transactions	POST /auth/login	Latency increases because auth-api waits for users-api
Kibana APM	APM → Traces → click one → waterfall	—	See which span is long: if users-api span is long = CPU; if the gap between spans is long = network
Kibana Infrastructure	Observability → Infrastructure → k8s-app	—	system.cpu.total.pct spiking on the node
APM Metrics	Discover → metrics-apm.*	service.name: users-api + system.cpu.total.norm.pct	High value = CPU is the problem
Elastic Agent	Kibana → Infrastructure → Hosts	host: k8s-app	Node-level CPU — confirms it's the node, not just the pod
How to tell CPU vs Network from APM:


CPU problem:   users-api span itself is long (wide bar in waterfall)
               + system.cpu.total.norm.pct is high

Network problem: users-api span is normal length
                 but the GAP before it starts is long (time travelling between services)
                 + CPU metrics are normal