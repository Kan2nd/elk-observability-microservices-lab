{
  "_index": ".ds-traces-apm-default-2026.04.24-000001",
  "_id": "827x9Z0BSjShlmhOtBnR",
  "_version": 1,
  "_score": null,
  "fields": {
    "transaction.name.text": [
      "GET unknown route"
    ],
    "transaction.representative_count": [
      1
    ],
    "host.name.text": [
      "todos-api-6c769f4577-bcdxg"
    ],
    "user_agent.original.text": [
      "Prometheus/2.53.1"
    ],
    "host.hostname": [
      "todos-api-6c769f4577-bcdxg"
    ],
    "url.original.text": [
      "/metrics"
    ],
    "process.pid": [
      1
    ],
    "service.language.name": [
      "javascript"
    ],
    "transaction.result": [
      "HTTP 4xx"
    ],
    "process.title.text": [
      "node"
    ],
    "transaction.id": [
      "6004a7b5372ef812"
    ],
    "http.request.method": [
      "GET"
    ],
    "agent.name.text": [
      "nodejs"
    ],
    "processor.event": [
      "transaction"
    ],
    "agent.name": [
      "nodejs"
    ],
    "host.name": [
      "todos-api-6c769f4577-bcdxg"
    ],
    "http.response.status_code": [
      401
    ],
    "http.version": [
      "1.1"
    ],
    "event.outcome": [
      "success"
    ],
    "user_agent.original": [
      "Prometheus/2.53.1"
    ],
    "service.language.name.text": [
      "javascript"
    ],
    "agent.activation_method": [
      "preload"
    ],
    "http.request.headers": [
      {
        "Accept": "application/openmetrics-text;version=1.0.0;q=0.5,application/openmetrics-text;version=0.0.1;q=0.4,text/plain;version=0.0.4;q=0.3,*/*;q=0.2",
        "User-Agent": "Prometheus/2.53.1",
        "Host": "todos-api.app.svc.cluster.local:8082",
        "Accept-Encoding": "gzip",
        "X-Prometheus-Scrape-Timeout-Seconds": "10"
      }
    ],
    "service.runtime.name.text": [
      "node"
    ],
    "transaction.duration.us": [
      983
    ],
    "service.runtime.version": [
      "20.20.2"
    ],
    "span.id": [
      "6004a7b5372ef812"
    ],
    "user_agent.name": [
      "Other"
    ],
    "data_stream.type": [
      "traces"
    ],
    "host.architecture": [
      "x64"
    ],
    "timestamp.us": [
      1777947749200033
    ],
    "url.path": [
      "/metrics"
    ],
    "observer.type": [
      "apm-server"
    ],
    "observer.version": [
      "9.3.1"
    ],
    "service.name.text": [
      "todos-api"
    ],
    "agent.version": [
      "4.15.0"
    ],
    "transaction.name": [
      "GET unknown route"
    ],
    "process.title": [
      "node"
    ],
    "service.framework.version": [
      "4.22.1"
    ],
    "service.node.name": [
      "todos-api-6c769f4577-bcdxg"
    ],
    "url.scheme": [
      "http"
    ],
    "transaction.sampled": [
      true
    ],
    "service.node.name.text": [
      "todos-api-6c769f4577-bcdxg"
    ],
    "host.ip": [
      "10.244.2.75"
    ],
    "trace.id": [
      "b821cc2f21c23a4617428e7598f41f4d"
    ],
    "event.success_count": [
      1
    ],
    "url.port": [
      8082
    ],
    "http.response.headers": [
      {
        "Keep-Alive": "timeout=5",
        "Etag": "W/\"1b-vdbCRHEl3J3b81u/YMssBiTkS2w\"",
        "Connection": "keep-alive",
        "Content-Length": "27",
        "Date": "Tue, 05 May 2026 02:22:29 GMT",
        "Content-Type": "application/json; charset=utf-8",
        "X-Powered-By": "Express"
      }
    ],
    "url.full": [
      "http://todos-api.app.svc.cluster.local:8082/metrics"
    ],
    "service.environment": [
      "development"
    ],
    "service.name": [
      "todos-api"
    ],
    "service.framework.name": [
      "express"
    ],
    "data_stream.namespace": [
      "default"
    ],
    "service.runtime.name": [
      "node"
    ],
    "process.args": [
      "/usr/local/bin/node",
      "/usr/src/app/server.js"
    ],
    "observer.hostname": [
      "fleet-server-69f64bd6c5-fcmdh"
    ],
    "user_agent.device.name.text": [
      "Other"
    ],
    "url.full.text": [
      "http://todos-api.app.svc.cluster.local:8082/metrics"
    ],
    "service.framework.name.text": [
      "express"
    ],
    "transaction.type": [
      "request"
    ],
    "transaction.span_count.started": [
      0
    ],
    "url.original": [
      "/metrics"
    ],
    "@timestamp": [
      "2026-05-05T02:22:29.200Z"
    ],
    "service.version": [
      "1.0.0"
    ],
    "user_agent.name.text": [
      "Other"
    ],
    "host.os.platform": [
      "linux"
    ],
    "data_stream.dataset": [
      "apm"
    ],
    "url.domain": [
      "todos-api.app.svc.cluster.local"
    ],
    "user_agent.device.name": [
      "Other"
    ]
  },
  "sort": [
    "2026-05-05T02:22:29.200Z",
    0
  ]
}

What happened:

Prometheus (user_agent.original: "Prometheus/2.53.1") sent a GET /metrics request to todos-api
todos-api returned HTTP 401 Unauthorized (http.response.status_code: 401)
The request took 983 microseconds (transaction.duration.us: 983)
Who recorded this:

The Elastic APM agent inside todos-api (Node.js) intercepted this incoming HTTP request and sent it to Fleet Server as a trace
observer.hostname: "fleet-server-69f64bd6c5-fcmdh" — Fleet Server received and stored it
transaction.name: "GET unknown route" — the APM agent saw /metrics but didn't recognise it as a named route in the Express app, so it labelled it "unknown route"
Why 401?

todos-api requires a JWT to access its routes — Prometheus doesn't send any auth header, so the app rejects it
Yet event.outcome: "success" — from APM's perspective the app handled the request correctly (it returned a proper response), even though the status was 4xx
The interesting problem this reveals:


Prometheus scrapes todos-api:8082/metrics every 10s
    ↓
todos-api JWT middleware blocks it → 401
    ↓
APM agent inside todos-api records EVERY scrape as a trace
    ↓
traces-apm-* index fills up with Prometheus noise
Every 10 seconds, Prometheus generates one of these trace records. That's 6 per minute, 8,640 per day — all useless noise in your APM data.