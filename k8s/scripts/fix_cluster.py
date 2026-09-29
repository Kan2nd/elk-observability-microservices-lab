# One-off cluster-repair script written mid-development to patch a batch of
# broken manifests directly on the control-plane node over SSH. Not part of
# the normal deploy flow (see ALL_K8S_DEPLOYMENT_GUIDE.md) — kept here as a
# reference for how the fix was applied. Set real values via env vars before
# running; defaults below match the lab cluster's default Kali credentials.

import os, paramiko, time, textwrap

HOST = os.environ.get('CLUSTER_HOST', '192.168.96.135')
USER = os.environ.get('CLUSTER_SSH_USER', 'kali')
PASS = os.environ.get('CLUSTER_SSH_PASSWORD', 'kali')

# ── helpers ──────────────────────────────────────────────────────────────────
def connect():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASS,
              timeout=30, banner_timeout=60, auth_timeout=30,
              look_for_keys=False, allow_agent=False)
    return c

def run(c, cmd, timeout=60):
    t0 = time.time()
    stdin, stdout, stderr = c.exec_command(cmd, timeout=timeout)
    out, err = stdout.read().decode(errors='replace'), stderr.read().decode(errors='replace')
    rc = stdout.channel.recv_exit_status()
    print(f"  [rc={rc} {time.time()-t0:.1f}s] $ {cmd[:100]}")
    if out.strip(): print(textwrap.indent(out[:600], '    '))
    if err.strip(): print(textwrap.indent(err[:300], '    ERR: '))
    return out, err, rc

def apply_yaml(c, name, yaml_content, timeout=90):
    """Pipe YAML directly into kubectl apply -f -"""
    print(f"\n--- Applying: {name} ---")
    # Escape single quotes in yaml by replacing with '"'"'
    escaped = yaml_content.replace("'", "'\"'\"'")
    cmd = f"echo '{escaped}' | kubectl apply -f -"
    return run(c, cmd, timeout=timeout)

# ── YAML manifests (corrected — node selector uses k8s-worker for monitor) ──

PROMETHEUS_YAML = """
apiVersion: v1
kind: ConfigMap
metadata:
  name: prometheus-monitoring-config
  namespace: monitor
data:
  prometheus.yml: |
    global:
      scrape_interval: 10s
      evaluation_interval: 10s
    remote_write:
      - url: "https://es01.monitor.svc.cluster.local:9200/_prometheus/receive"
        basic_auth:
          username: "elastic"
          password_file: /etc/prometheus/secrets/ELASTIC_PASSWORD
        tls_config:
          insecure_skip_verify: true
    scrape_configs:
    - job_name: prometheus
      static_configs:
      - targets: ['localhost:9090']
    - job_name: frontend
      static_configs:
      - targets: ['frontend.app.svc.cluster.local:8080']
    - job_name: auth-api
      static_configs:
      - targets: ['auth-api.app.svc.cluster.local:8081']
    - job_name: todos-api
      static_configs:
      - targets: ['todos-api.app.svc.cluster.local:8082']
    - job_name: users-api
      static_configs:
      - targets: ['users-api.app.svc.cluster.local:8083']
    - job_name: redis
      static_configs:
      - targets: ['redis.app.svc.cluster.local:9121']
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: prometheus-monitoring
  namespace: monitor
  labels:
    app: prometheus
spec:
  replicas: 1
  selector:
    matchLabels:
      app: prometheus
  template:
    metadata:
      labels:
        app: prometheus
    spec:
      nodeSelector:
        kubernetes.io/hostname: k8s-worker
      serviceAccountName: prometheus
      containers:
      - name: prometheus
        image: prom/prometheus:v2.53.1
        args:
        - --config.file=/etc/prometheus/prometheus.yml
        - --storage.tsdb.path=/prometheus
        - --storage.tsdb.retention.time=7d
        - --web.enable-lifecycle
        ports:
        - containerPort: 9090
        resources:
          limits:
            memory: "512Mi"
          requests:
            memory: "256Mi"
        volumeMounts:
        - name: config
          mountPath: /etc/prometheus
        - name: storage
          mountPath: /prometheus
        - name: elastic-password
          mountPath: /etc/prometheus/secrets
          readOnly: true
        livenessProbe:
          httpGet:
            path: /-/healthy
            port: 9090
          initialDelaySeconds: 30
          periodSeconds: 15
        readinessProbe:
          httpGet:
            path: /-/ready
            port: 9090
          initialDelaySeconds: 10
          periodSeconds: 10
      volumes:
      - name: config
        configMap:
          name: prometheus-monitoring-config
      - name: storage
        emptyDir: {}
      - name: elastic-password
        secret:
          secretName: monitor-secrets
          items:
          - key: ELASTIC_PASSWORD
            path: ELASTIC_PASSWORD
---
apiVersion: v1
kind: Service
metadata:
  name: prometheus
  namespace: monitor
spec:
  type: ClusterIP
  selector:
    app: prometheus
  ports:
  - port: 9090
    targetPort: 9090
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: prometheus
  namespace: monitor
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: prometheus
rules:
- apiGroups: [""]
  resources: [nodes, nodes/proxy, services, endpoints, pods]
  verbs: [get, list, watch]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: prometheus
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: prometheus
subjects:
- kind: ServiceAccount
  name: prometheus
  namespace: monitor
"""

GRAFANA_YAML = """
apiVersion: v1
kind: ConfigMap
metadata:
  name: grafana-datasources
  namespace: monitor
data:
  datasources.yaml: |
    apiVersion: 1
    datasources:
      - name: Prometheus
        type: prometheus
        access: proxy
        url: http://prometheus:9090
        isDefault: true
        editable: false
      - name: Elasticsearch
        type: elasticsearch
        access: proxy
        url: https://es01:9200
        basicAuth: true
        basicAuthUser: elastic
        basicAuthPassword: ${ELASTIC_PASSWORD}
        jsonData:
          tlsSkipVerify: true
          esVersion: "8.0.0"
          timeField: "@timestamp"
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: grafana
  namespace: monitor
  labels:
    app: grafana
spec:
  replicas: 1
  selector:
    matchLabels:
      app: grafana
  template:
    metadata:
      labels:
        app: grafana
    spec:
      nodeSelector:
        kubernetes.io/hostname: k8s-worker
      containers:
      - name: grafana
        image: grafana/grafana:11.5.0
        ports:
        - containerPort: 3000
        env:
        - name: GF_SECURITY_ADMIN_USER
          value: admin
        - name: GF_SECURITY_ADMIN_PASSWORD
          valueFrom:
            secretKeyRef:
              name: monitor-secrets
              key: GRAFANA_PASSWORD
        - name: GF_USERS_ALLOW_SIGN_UP
          value: "false"
        - name: ELASTIC_PASSWORD
          valueFrom:
            secretKeyRef:
              name: monitor-secrets
              key: ELASTIC_PASSWORD
        resources:
          limits:
            memory: "256Mi"
        volumeMounts:
        - name: grafanadata
          mountPath: /var/lib/grafana
        - name: datasources
          mountPath: /etc/grafana/provisioning/datasources
        livenessProbe:
          httpGet:
            path: /api/health
            port: 3000
          initialDelaySeconds: 40
          periodSeconds: 15
        readinessProbe:
          httpGet:
            path: /api/health
            port: 3000
          initialDelaySeconds: 20
          periodSeconds: 10
      volumes:
      - name: grafanadata
        persistentVolumeClaim:
          claimName: grafanadata
      - name: datasources
        configMap:
          name: grafana-datasources
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: grafanadata
  namespace: monitor
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests:
      storage: 5Gi
---
apiVersion: v1
kind: Service
metadata:
  name: grafana
  namespace: monitor
spec:
  type: ClusterIP
  selector:
    app: grafana
  ports:
  - port: 3000
    targetPort: 3000
"""

FLUENT_BIT_RECEIVER_YAML = """
apiVersion: v1
kind: ConfigMap
metadata:
  name: fluent-bit-receiver-config
  namespace: monitor
data:
  fluent-bit.conf: |
    [SERVICE]
        Flush              1
        Daemon             Off
        Log_Level          info
    [INPUT]
        Name               forward
        Listen             0.0.0.0
        Port               24224
        Tag                forwarded.*
    [FILTER]
        Name               modify
        Match              forwarded.*
        Add                observer.type fluent-bit-receiver
    [OUTPUT]
        Name               es
        Match              forwarded.*
        Host               es01.monitor.svc.cluster.local
        Port               9200
        HTTP_User          elastic
        HTTP_Passwd        ${ELASTIC_PASSWORD}
        Index              app-logs
        Logstash_Format    On
        Logstash_Prefix    app
        Suppress_Type_Name On
        Generate_ID        On
        Retry_Limit        5
        tls                On
        tls.verify         On
        tls.ca_file        /certs/ca/ca.crt
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: fluent-bit-receiver
  namespace: monitor
  labels:
    app: fluent-bit-receiver
spec:
  replicas: 1
  selector:
    matchLabels:
      app: fluent-bit-receiver
  template:
    metadata:
      labels:
        app: fluent-bit-receiver
    spec:
      nodeSelector:
        kubernetes.io/hostname: k8s-worker
      containers:
      - name: fluent-bit
        image: cr.fluentbit.io/fluent/fluent-bit:3.1.8
        ports:
        - name: forward-tcp
          containerPort: 24224
          protocol: TCP
        - name: forward-udp
          containerPort: 24224
          protocol: UDP
        env:
        - name: ELASTIC_PASSWORD
          valueFrom:
            secretKeyRef:
              name: monitor-secrets
              key: ELASTIC_PASSWORD
        volumeMounts:
        - name: config
          mountPath: /fluent-bit/etc/
        - name: certs
          mountPath: /certs
          readOnly: true
        resources:
          limits:
            memory: "128Mi"
      volumes:
      - name: config
        configMap:
          name: fluent-bit-receiver-config
      - name: certs
        secret:
          secretName: elastic-certs
          items:
          - key: ca.crt
            path: ca/ca.crt
---
apiVersion: v1
kind: Service
metadata:
  name: fluent-bit-receiver
  namespace: monitor
spec:
  type: ClusterIP
  selector:
    app: fluent-bit-receiver
  ports:
  - name: forward-tcp
    port: 24224
    targetPort: 24224
    protocol: TCP
"""

NODEPORT_MONITOR_YAML = """
apiVersion: v1
kind: Service
metadata:
  name: es01-nodeport
  namespace: monitor
spec:
  type: NodePort
  selector:
    app: es01
  ports:
  - port: 9200
    targetPort: 9200
    nodePort: 30920
---
apiVersion: v1
kind: Service
metadata:
  name: kibana-nodeport
  namespace: monitor
spec:
  type: NodePort
  selector:
    app: kibana
  ports:
  - port: 5601
    targetPort: 5601
    nodePort: 30601
---
apiVersion: v1
kind: Service
metadata:
  name: fleet-server-nodeport
  namespace: monitor
spec:
  type: NodePort
  selector:
    app: fleet-server
  ports:
  - name: fleet
    port: 8220
    targetPort: 8220
    nodePort: 30820
  - name: apm
    port: 8200
    targetPort: 8200
    nodePort: 30200
---
apiVersion: v1
kind: Service
metadata:
  name: prometheus-nodeport
  namespace: monitor
spec:
  type: NodePort
  selector:
    app: prometheus
  ports:
  - port: 9090
    targetPort: 9090
    nodePort: 30900
---
apiVersion: v1
kind: Service
metadata:
  name: grafana-nodeport
  namespace: monitor
spec:
  type: NodePort
  selector:
    app: grafana
  ports:
  - port: 3000
    targetPort: 3000
    nodePort: 30300
---
apiVersion: v1
kind: Service
metadata:
  name: fluent-bit-receiver-nodeport
  namespace: monitor
spec:
  type: NodePort
  selector:
    app: fluent-bit-receiver
  ports:
  - port: 24224
    targetPort: 24224
    nodePort: 30224
    protocol: TCP
"""

NODEPORT_APP_YAML = """
apiVersion: v1
kind: Service
metadata:
  name: zipkin-nodeport
  namespace: app
spec:
  type: NodePort
  selector:
    app: zipkin
  ports:
  - port: 9411
    targetPort: 9411
    nodePort: 30411
---
apiVersion: v1
kind: Service
metadata:
  name: frontend-nodeport
  namespace: app
spec:
  type: NodePort
  selector:
    app: frontend
  ports:
  - port: 8080
    targetPort: 8080
    nodePort: 30880
"""

ELASTIC_AGENT_APP_YAML = """
apiVersion: v1
kind: ServiceAccount
metadata:
  name: elastic-agent
  namespace: app
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: elastic-agent-app
rules:
- apiGroups: [""]
  resources: [nodes, namespaces, events, pods, services]
  verbs: [get, list, watch]
- apiGroups: [apps]
  resources: [replicasets, deployments, statefulsets, daemonsets]
  verbs: [get, list, watch]
- nonResourceURLs: [/metrics]
  verbs: [get]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: elastic-agent-app
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: elastic-agent-app
subjects:
- kind: ServiceAccount
  name: elastic-agent
  namespace: app
---
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: elastic-agent
  namespace: app
  labels:
    app: elastic-agent
spec:
  selector:
    matchLabels:
      app: elastic-agent
  template:
    metadata:
      labels:
        app: elastic-agent
    spec:
      nodeSelector:
        kubernetes.io/hostname: k8s-app
      enableServiceLinks: false
      serviceAccountName: elastic-agent
      terminationGracePeriodSeconds: 30
      containers:
      - name: elastic-agent
        image: docker.elastic.co/elastic-agent/elastic-agent:9.3.1
        securityContext:
          runAsUser: 0
          privileged: true
        env:
        - name: FLEET_ENROLL
          value: "1"
        - name: FLEET_URL
          value: https://fleet-server.monitor.svc.cluster.local:8220
        - name: FLEET_CA
          value: /certs/ca/ca.crt
        - name: FLEET_ENROLLMENT_TOKEN
          valueFrom:
            secretKeyRef:
              name: elastic-apm-secret
              key: fleet-enrollment-token
        - name: ELASTIC_APM_SERVER_URL
          value: https://fleet-server.monitor.svc.cluster.local:8200
        - name: ELASTIC_APM_SECRET_TOKEN
          valueFrom:
            secretKeyRef:
              name: elastic-apm-secret
              key: apm-secret-token
        - name: NODE_NAME
          valueFrom:
            fieldRef:
              fieldPath: spec.nodeName
        volumeMounts:
        - name: certs
          mountPath: /certs
          readOnly: true
        - name: proc
          mountPath: /hostfs/proc
          readOnly: true
        - name: sys
          mountPath: /hostfs/sys
          readOnly: true
        resources:
          limits:
            memory: "512Mi"
      volumes:
      - name: certs
        secret:
          secretName: elastic-certs
          items:
          - key: ca.crt
            path: ca/ca.crt
      - name: proc
        hostPath:
          path: /proc
      - name: sys
        hostPath:
          path: /sys
"""

# ── main ─────────────────────────────────────────────────────────────────────
print("Connecting to cluster...")
c = connect()
print("Connected.\n")

print("=" * 60)
print("STEP 1: Check current state")
print("=" * 60)
run(c, 'kubectl get nodes --no-headers')
run(c, 'kubectl get pods -n monitor -o wide --no-headers')
run(c, 'kubectl get pods -n app -o wide --no-headers')

print("\n" + "=" * 60)
print("STEP 2: Check why todos-api / users-api / auth-api crash")
print("=" * 60)
run(c, 'kubectl logs -n app deployment/todos-api --tail=20 2>&1 | head -30')
run(c, 'kubectl logs -n app deployment/users-api --tail=20 2>&1 | head -30')
run(c, 'kubectl logs -n app deployment/auth-api --tail=20 2>&1 | head -30')

print("\n" + "=" * 60)
print("STEP 3: Delete wrong/stuck pods and DaemonSets")
print("=" * 60)
# elastic-agent DaemonSet has no nodeSelector → runs on k8s-worker too
run(c, 'kubectl delete daemonset elastic-agent -n app --ignore-not-found')
# prometheus-client is stuck in ContainerCreating — no ConfigMap
run(c, 'kubectl delete deployment prometheus-client -n app --ignore-not-found')
run(c, 'kubectl delete service prometheus-client -n app --ignore-not-found')

print("\n" + "=" * 60)
print("STEP 4: Fix nodeSelector on existing monitor deployments")
print("=" * 60)
# Patch ES, Kibana, Fleet to use k8s-worker (not k8s-moni)
patch = '{"spec":{"template":{"spec":{"nodeSelector":{"kubernetes.io/hostname":"k8s-worker"}}}}}'
run(c, f"kubectl patch deployment es01 -n monitor -p '{patch}'")
run(c, f"kubectl patch deployment kibana -n monitor -p '{patch}'")
run(c, f"kubectl patch deployment fleet-server -n monitor -p '{patch}'")

print("\n" + "=" * 60)
print("STEP 5: Deploy Prometheus to monitor namespace")
print("=" * 60)
apply_yaml(c, "Prometheus", PROMETHEUS_YAML, timeout=120)

print("\n" + "=" * 60)
print("STEP 6: Deploy Grafana to monitor namespace")
print("=" * 60)
apply_yaml(c, "Grafana", GRAFANA_YAML, timeout=120)

print("\n" + "=" * 60)
print("STEP 7: Deploy fluent-bit receiver to monitor namespace")
print("=" * 60)
apply_yaml(c, "Fluent-bit receiver", FLUENT_BIT_RECEIVER_YAML, timeout=120)

print("\n" + "=" * 60)
print("STEP 8: Create NodePort services")
print("=" * 60)
apply_yaml(c, "Monitor NodePorts", NODEPORT_MONITOR_YAML, timeout=60)
apply_yaml(c, "App NodePorts", NODEPORT_APP_YAML, timeout=60)

print("\n" + "=" * 60)
print("STEP 9: Re-deploy elastic-agent DaemonSet with nodeSelector k8s-app")
print("=" * 60)
# First copy elastic-certs CA to app namespace so elastic-agent can reach fleet-server
run(c, "kubectl get secret elastic-certs -n monitor -o jsonpath='{.data.ca\\.crt}' | base64 -d > /tmp/ca.crt && kubectl create secret generic elastic-certs -n app --from-file=ca.crt=/tmp/ca.crt --dry-run=client -o yaml | kubectl apply -f -")
apply_yaml(c, "Elastic-agent app DaemonSet", ELASTIC_AGENT_APP_YAML, timeout=120)

print("\n" + "=" * 60)
print("STEP 10: Wait 30s then check final state")
print("=" * 60)
import time; time.sleep(30)
run(c, 'kubectl get pods -n monitor -o wide')
run(c, 'kubectl get pods -n app -o wide')
run(c, 'kubectl get services -n monitor | grep -v kubernetes')
run(c, 'kubectl get services -n app | grep -v kubernetes')

c.close()
print("\nDone.")
