# ELK2 Microservices Observability Project

A hands-on lab (Semester 5, ELK course) that takes a small pre-built microservices
demo app and wraps it in a full observability stack: centralized logging, distributed
tracing, APM, and metrics — first prototyped with Docker Compose on two VMs, then
migrated to a real 3-node Kubernetes cluster.

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

- **Two deployment approaches**, built one after the other:
  1. **`k8s/`** — the current, canonical deployment. Everything runs on a real
     3-node bare-metal Kubernetes cluster, split into an `app` namespace (the
     microservices) and a `monitor` namespace (all observability tooling).
     Start here: [`docs/ALL_K8S_DEPLOYMENT_GUIDE.md`](docs/ALL_K8S_DEPLOYMENT_GUIDE.md).
  2. **`app/` + `monitoring-vm/`** — the earlier, superseded approach: plain
     Docker Compose across two separate VMs (one running the app, one running
     the monitoring stack), talking to each other over the network. Kept for
     reference / comparison; not actively maintained. Note this version still
     uses Grafana for dashboards, which was dropped in the Kubernetes version
     in favor of Kibana (see [`docs/newly_updated.md`](docs/newly_updated.md)).

## Repository structure

```
.
├── app/                 Microservices app (frontend, auth-api, users-api, todos-api,
│                        log-message-processor) + its own docker-compose.yaml and
│                        original (unmodified) k8s manifests under app/k8s/
├── k8s/                 Current Kubernetes deployment (canonical)
│   ├── app/             Manifests for the microservices (app namespace)
│   ├── moni/            Manifests for the observability stack (monitor namespace)
│   ├── rancher/         Rancher management UI manifests
│   ├── scripts/         Helper/one-off scripts (see inline comments before running)
│   ├── deploy-all-k8s.sh / .bat   Quick all-in-one deploy scripts
│   └── namespace.yaml
├── monitoring-vm/       Legacy Docker Compose monitoring stack (superseded by k8s/moni)
├── docs/                Deployment guide, changelog, demo scenarios, presentation notes
└── archive/             Local-only backups & anything sensitive — gitignored, not pushed
```

## Getting started

### Option A — Kubernetes (recommended, current)

Follow [`docs/ALL_K8S_DEPLOYMENT_GUIDE.md`](docs/ALL_K8S_DEPLOYMENT_GUIDE.md) phase
by phase, or use the quick scripts:

```bash
cd k8s
./deploy-all-k8s.sh    # or deploy-all-k8s.bat on Windows
```

### Option B — Docker Compose (legacy, two-VM setup)

```bash
# On the monitor VM
cd monitoring-vm
cp .env.example .env      # fill in real values
docker compose up -d

# On the app VM
cd app
cp .env.example .env      # fill in real values, incl. MONITOR_VM_IP and the
                           # Fleet enrollment token from the monitor VM's Kibana
docker compose up -d
```

## Credentials

**No real credentials are committed to this repository.** Every `.env` file has
been replaced with a `.env.example` containing placeholder values — copy it to
`.env` and fill in your own before deploying. Kubernetes secret manifests
(`k8s/app/secret-*.yaml`, `k8s/rancher/secret-rancher-token.yaml`) ship with
documented lab-default placeholders (`changeme`, `<PASTE_..._HERE>`, etc.) —
**change these** before using this in anything beyond a local/private lab
cluster.

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
