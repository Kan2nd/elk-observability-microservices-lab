@echo off
REM Complete Kubernetes Deployment Script - All-in-One (Windows PowerShell)
REM Deploys everything: Monitoring stack + Microservices on Kubernetes

setlocal enabledelayedexpansion

echo.
echo ========================================
echo Kubernetes All-in-One Deployment
echo ========================================

REM 1. Create Namespaces
echo.
echo [Step 1] Creating namespaces...
kubectl apply -f namespace.yaml
timeout /t 2 /nobreak

REM 2. Create Secrets
echo.
echo [Step 2] Creating secrets...
set ELASTIC_PASSWORD=elastic-password-123
set APM_TOKEN=apm-secret-token
set FLEET_TOKEN=fleet-enrollment-token

kubectl create secret generic elastic-apm-secret ^
  --from-literal=elasticsearch-password=%ELASTIC_PASSWORD% ^
  --from-literal=apm-secret-token=%APM_TOKEN% ^
  --from-literal=fleet-enrollment-token=%FLEET_TOKEN% ^
  -n monitoring ^
  --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic elastic-apm-secret ^
  --from-literal=elasticsearch-password=%ELASTIC_PASSWORD% ^
  --from-literal=apm-secret-token=%APM_TOKEN% ^
  --from-literal=fleet-enrollment-token=%FLEET_TOKEN% ^
  -n applications ^
  --dry-run=client -o yaml | kubectl apply -f -

echo [OK] Secrets created

REM 3. Deploy Monitoring Stack
echo.
echo [Step 3] Deploying monitoring stack...
kubectl apply -f deployment-elasticsearch.yaml
echo [OK] Elasticsearch
timeout /t 5 /nobreak

kubectl apply -f deployment-kibana.yaml
echo [OK] Kibana

kubectl apply -f deployment-prometheus-monitoring.yaml
echo [OK] Prometheus

kubectl apply -f deployment-grafana.yaml
echo [OK] Grafana

kubectl apply -f deployment-fleet-server.yaml
echo [OK] Fleet Server

REM 4. Deploy ConfigMaps
echo.
echo [Step 4] Deploying configuration...
kubectl apply -f configmap-prometheus.yaml
kubectl apply -f configmap-fluent-bit.yaml
echo [OK] ConfigMaps

REM 5. Deploy Microservices Infrastructure
echo.
echo [Step 5] Deploying microservices infrastructure...
kubectl apply -f deployment-redis.yaml
echo [OK] Redis

kubectl apply -f deployment-zipkin.yaml
echo [OK] Zipkin

kubectl apply -f deployment-prometheus-client.yaml
echo [OK] Prometheus client

timeout /t 5 /nobreak

REM 6. Deploy Microservices
echo.
echo [Step 6] Deploying microservices...
kubectl apply -f deployment-frontend.yaml
echo [OK] Frontend

kubectl apply -f deployment-auth-api.yaml
echo [OK] Auth API

kubectl apply -f deployment-todos-api.yaml
echo [OK] Todos API

kubectl apply -f deployment-users-api.yaml
echo [OK] Users API

kubectl apply -f deployment-log-processor.yaml
echo [OK] Log processor

timeout /t 5 /nobreak

REM 7. Deploy DaemonSets
echo.
echo [Step 7] Deploying collectors...
kubectl apply -f daemonset-fluent-bit.yaml
echo [OK] Fluent Bit

kubectl apply -f daemonset-elastic-agent.yaml
echo [OK] Elastic Agent

REM 8. Display Status
echo.
echo ========================================
echo Deployment Complete!
echo ========================================
echo.
echo Monitoring Stack:
kubectl get pods -n monitoring
echo.
echo Microservices:
kubectl get pods -n applications
echo.
echo To access services, use:
echo   kubectl port-forward svc/kibana -n monitoring 5601:5601
echo   kubectl port-forward svc/prometheus -n monitoring 9090:9090
echo   kubectl port-forward svc/grafana -n monitoring 3000:3000
echo   kubectl port-forward svc/frontend -n applications 8080:8080
echo.
echo Wait 3-5 minutes for Elasticsearch to fully start.
