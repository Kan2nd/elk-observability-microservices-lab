# ELK2 Microservices Observability Project

A hands-on lab (Semester 5, ELK course) that takes a small pre-built microservices
demo app and wraps it in a full observability stack on Kubernetes: centralized
logging, distributed tracing, APM, and metrics.

## What's in here

- **The app** ([`app/`](app/)) — a 5-service Todo application (forked from
  [elgris/microservice-app-example](https://github.com/elgris/microservice-app-example),
  MIT-licensed, see [`app/LICENSE`](app/LICENSE)):
  - `frontend` — Vue.js UI
  - `auth-api` — Go, handles login and issues JWTs
  - `users-api` — Java/Spring Boot, user storage
  - `todos-api` — Node.js, todo CRUD, persisted to Redis
  - `log-message-processor` — Python, consumes Redis pub/sub events
  - `redis` — storage + pub/sub bus between services
  - Originally wired with Zipkin for distributed tracing.

- **The observability stack** — Elasticsearch, Kibana, Fleet Server + Elastic
  Agent (log/APM ingestion), Fluent-Bit (log shipping), Prometheus +
  kube-state-metrics + a Redis exporter (metrics), Zipkin (tracing), and
  Rancher (cluster management UI).

- **`k8s/`** — the deployment. Everything runs on a real 3-node bare-metal
  Kubernetes cluster, split into an `app` namespace (the microservices) and a
  `monitor` namespace (all observability tooling). Start here:
  [`docs/ALL_K8S_DEPLOYMENT_GUIDE.md`](docs/ALL_K8S_DEPLOYMENT_GUIDE.md).

## Repository structure

```
.
├── app/                 Microservices app (frontend, auth-api, users-api, todos-api,
│                        log-message-processor) + original (unmodified) k8s
│                        manifests from the upstream fork under app/k8s/
├── k8s/                 Kubernetes deployment
│   ├── app/             Manifests for the microservices (app namespace)
│   ├── moni/            Manifests for the observability stack (monitor namespace)
│   ├── rancher/         Rancher management UI manifests
│   ├── scripts/         Helper/one-off scripts (see inline comments before running)
│   ├── deploy-all-k8s.sh / .bat   Quick all-in-one deploy scripts
│   └── namespace.yaml
├── docs/                Deployment guide, changelog, demo scenarios, presentation notes
└── archive/             Local-only backups & superseded material — gitignored, not pushed
```

## Getting started

Follow [`docs/ALL_K8S_DEPLOYMENT_GUIDE.md`](docs/ALL_K8S_DEPLOYMENT_GUIDE.md) phase
by phase, or use the quick scripts:

```bash
cd k8s
./deploy-all-k8s.sh    # or deploy-all-k8s.bat on Windows
```

## Credentials

**No real credentials are committed to this repository.** Kubernetes secret
manifests (`k8s/app/secret-*.yaml`, `k8s/rancher/secret-rancher-token.yaml`)
ship with documented lab-default placeholders (`changeme`, `<PASTE_..._HERE>`,
etc.) — **change these** before using this in anything beyond a local/private
lab cluster.

## Documentation

See [`docs/`](docs/) for the full write-up:

- [`ALL_K8S_DEPLOYMENT_GUIDE.md`](docs/ALL_K8S_DEPLOYMENT_GUIDE.md) — full phase-by-phase Kubernetes deployment guide
- [`FinalSum.md`](docs/FinalSum.md) — final project summary/report
- [`newly_updated.md`](docs/newly_updated.md) — running changelog of fixes and design changes
- [`demo-scenarios.md`](docs/demo-scenarios.md) / [`scena.md`](docs/scena.md) — demo/test scenarios
- [`presentation.md`](docs/presentation.md) — presentation notes
- [`interesting_things.md`](docs/interesting_things.md) / [`suggestions.md`](docs/suggestions.md) — misc notes and ideas
- [`search_tutorial.md`](docs/search_tutorial.md) — Elasticsearch/Kibana search tutorial notes

## Credits

The base microservices application is forked from
[elgris/microservice-app-example](https://github.com/elgris/microservice-app-example)
(MIT license, see [`app/LICENSE`](app/LICENSE)). Everything else — the
Kubernetes deployment, the observability stack, and the accompanying docs —
was built as coursework for an ELK-focused Semester 5 class.
