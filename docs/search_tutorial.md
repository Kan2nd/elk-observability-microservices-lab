Here's the full cheat sheet — organized by what scenario you want to verify:

Logs (Kibana → Discover → app-*)
Scenario	Field	What to look for
Logs arriving at all	kubernetes.pod_name	All pods appear
Specific service logs	kubernetes.container_name	auth-api, todos-api, users-api, frontend, redis, log-processor
Errors only	stream	stderr
Structured log parsed	level	INFO, ERROR, WARN (only auth-api JSON logs)
Stack trace stitched	log	Should be one multi-line string, not fragmented
Redis pub/sub fired	log	Received: or Processed log event (log-processor)
Todo action happened	log	Published to log_channel (todos-api)
APM Traces (Kibana → APM → Services → Transactions / traces-apm-*)
Scenario	Field	What to look for
Request went through	transaction.name	Route name e.g. POST /auth/login
Request succeeded	transaction.result	HTTP 2xx
Request failed	transaction.result	HTTP 4xx or HTTP 5xx
Slow request	transaction.duration.us	High value (>500000 = >500ms)
Cross-service trace	trace.id	Same ID across auth-api + users-api
Which service	service.name	auth-api, todos-api, users-api, log-processor
Prometheus noise	user_agent.original	Prometheus/2.53.1 (should stop after our fix)
APM Errors (Kibana → APM → Services → Errors / logs-apm-*)
Scenario	Field	What to look for
Any error happened	error.message	Non-empty
Java exception	error.exception.type	NullPointerException, RuntimeException etc
Which line crashed	error.culprit	File + line number
Error rate spike	error.grouping_name	Same error repeated many times
APM Runtime Metrics (metrics-apm.* in Discover)
Service	Field	What to look for
auth-api (Go)	golang.goroutines	Should be ~10-20, spike = leak
auth-api (Go)	golang.heap.allocations.allocated	Growing non-stop = memory leak
auth-api (Go)	golang.heap.gc.total_count	GC running regularly = normal
todos-api / frontend (Node.js)	nodejs.heap.size.used	Growing = memory leak
todos-api / frontend (Node.js)	nodejs.eventloop.delay.avg.ms	High = app is blocked/overloaded
users-api (Java)	jvm.memory.heap.used	Near jvm.memory.heap.max = OOM risk
users-api (Java)	jvm.gc.time	Spikes = GC pressure
All services	system.cpu.total.norm.pct	Near 1.0 = CPU throttled
All services	system.process.memory.rss.bytes	Growing = memory leak
Prometheus Metrics (Prometheus UI → Graph or metrics-* in Kibana)
Scenario	Query	What to look for
Any target down	up	Value 0 = that job is DOWN
Redis alive	redis_up	Must be 1
Redis pub/sub channel	redis_pubsub_channels	Must be 1 (log_channel active)
Redis has subscribers	redis_connected_clients	≥ 2 (todos-api + log-processor)
Redis memory	redis_used_memory_bytes	Growing = data not being consumed
Frontend getting traffic	http_requests_total	Increases as you use the app
K8s API healthy	apiserver_request_total	Present + not all errors
Elastic Agent System Metrics (Kibana → Infrastructure or metrics-*)
Scenario	Field	What to look for
Node CPU	system.cpu.total.pct	Near 1.0 = node is overloaded
Node RAM	system.memory.actual.used.pct	Above 0.9 = memory pressure
Disk I/O	system.diskio.write.bytes	Spike on k8s-worker = ES writing heavily
Network	system.network.in.bytes	Flat = no traffic
Which node	host.hostname	k8s-app or k8s-worker
Pod Down (cross-tool)
Tool	What disappears / changes	Where
Prometheus	up{job="redis"} = 0	Status → Targets shows DOWN
Fluent-Bit	Logs from that kubernetes.pod_name stop	Kibana Logs — just silence
APM	Calling service gets transaction.result: HTTP 5xx + error.message: connection refused	APM → Errors
Rancher	Pod shows 0/1 then disappears	Cluster → Workloads
