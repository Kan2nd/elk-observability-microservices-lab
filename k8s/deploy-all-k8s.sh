#!/bin/bash
# Complete Kubernetes Deployment Script - All-in-One
# Deploys everything: Monitoring stack + Microservices on Kubernetes

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Kubernetes All-in-One Deployment${NC}"
echo -e "${GREEN}========================================${NC}"

# 1. Create Namespaces
echo -e "\n${YELLOW}Step 1: Creating namespaces...${NC}"
kubectl apply -f namespace.yaml
sleep 2

# 2. Create Secrets
echo -e "\n${YELLOW}Step 2: Creating secrets...${NC}"
ELASTIC_PASSWORD=${ELASTIC_PASSWORD:-"elastic-password-123"}
APM_TOKEN=${APM_TOKEN:-"apm-secret-token"}
FLEET_TOKEN=${FLEET_TOKEN:-"fleet-enrollment-token"}

kubectl create secret generic elastic-apm-secret \
  --from-literal=elasticsearch-password="$ELASTIC_PASSWORD" \
  --from-literal=apm-secret-token="$APM_TOKEN" \
  --from-literal=fleet-enrollment-token="$FLEET_TOKEN" \
  -n monitoring \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic elastic-apm-secret \
  --from-literal=elasticsearch-password="$ELASTIC_PASSWORD" \
  --from-literal=apm-secret-token="$APM_TOKEN" \
  --from-literal=fleet-enrollment-token="$FLEET_TOKEN" \
  -n applications \
  --dry-run=client -o yaml | kubectl apply -f -

echo -e "${GREEN}✓ Secrets created with Elasticsearch password: $ELASTIC_PASSWORD${NC}"

# 3. Deploy Monitoring Stack (on k8s-worker node)
echo -e "\n${YELLOW}Step 2: Deploying monitoring stack to k8s-worker...${NC}"

kubectl apply -f deployment-elasticsearch.yaml
echo -e "${GREEN}✓ Elasticsearch deploying...${NC}"
sleep 5

kubectl apply -f deployment-kibana.yaml
echo -e "${GREEN}✓ Kibana deploying...${NC}"

kubectl apply -f deployment-prometheus-monitoring.yaml
echo -e "${GREEN}✓ Prometheus deploying...${NC}"

kubectl apply -f deployment-grafana.yaml
echo -e "${GREEN}✓ Grafana deploying...${NC}"

kubectl apply -f deployment-fleet-server.yaml
echo -e "${GREEN}✓ Fleet Server deploying...${NC}"

# 4. Deploy ConfigMaps
echo -e "\n${YELLOW}Step 3: Deploying configuration...${NC}"
kubectl apply -f configmap-prometheus.yaml
kubectl apply -f configmap-fluent-bit.yaml
echo -e "${GREEN}✓ ConfigMaps created${NC}"

# 5. Deploy Microservices Infrastructure
echo -e "\n${YELLOW}Step 4: Deploying microservices infrastructure...${NC}"

kubectl apply -f deployment-redis.yaml
echo -e "${GREEN}✓ Redis deploying...${NC}"

kubectl apply -f deployment-zipkin.yaml
echo -e "${GREEN}✓ Zipkin deploying...${NC}"

kubectl apply -f deployment-prometheus-client.yaml
echo -e "${GREEN}✓ Prometheus client deploying...${NC}"

sleep 5

# 6. Deploy Microservices
echo -e "\n${YELLOW}Step 5: Deploying microservices to k8s-app...${NC}"

kubectl apply -f deployment-frontend.yaml
echo -e "${GREEN}✓ Frontend deploying...${NC}"

kubectl apply -f deployment-auth-api.yaml
echo -e "${GREEN}✓ Auth API deploying...${NC}"

kubectl apply -f deployment-todos-api.yaml
echo -e "${GREEN}✓ Todos API deploying...${NC}"

kubectl apply -f deployment-users-api.yaml
echo -e "${GREEN}✓ Users API deploying...${NC}"

kubectl apply -f deployment-log-processor.yaml
echo -e "${GREEN}✓ Log processor deploying...${NC}"

sleep 5

# 7. Deploy DaemonSets
echo -e "\n${YELLOW}Step 6: Deploying collectors (runs on all nodes)...${NC}"

kubectl apply -f daemonset-fluent-bit.yaml
echo -e "${GREEN}✓ Fluent Bit DaemonSet deploying...${NC}"

kubectl apply -f daemonset-elastic-agent.yaml
echo -e "${GREEN}✓ Elastic Agent DaemonSet deploying...${NC}"

# 8. Wait for pods to be ready
echo -e "\n${YELLOW}Step 7: Waiting for all pods to be ready...${NC}"
echo "This may take 2-3 minutes..."

kubectl wait --for=condition=ready pod -l app=elasticsearch -n monitoring --timeout=300s 2>/dev/null || echo "Elasticsearch still starting..."
kubectl wait --for=condition=ready pod -l app=kibana -n monitoring --timeout=300s 2>/dev/null || echo "Kibana still starting..."
kubectl wait --for=condition=ready pod -l app=prometheus -n monitoring --timeout=300s 2>/dev/null || echo "Prometheus still starting..."
kubectl wait --for=condition=ready pod -l app=grafana -n monitoring --timeout=300s 2>/dev/null || echo "Grafana still starting..."
kubectl wait --for=condition=ready pod -l app=frontend -n applications --timeout=300s 2>/dev/null || echo "Frontend still starting..."

# 9. Display Summary
echo -e "\n${GREEN}========================================${NC}"
echo -e "${GREEN}✓ Deployment Complete!${NC}"
echo -e "${GREEN}========================================${NC}"

echo -e "\n${YELLOW}Monitoring Stack (k8s-worker):${NC}"
kubectl get pods -n monitoring

echo -e "\n${YELLOW}Microservices (k8s-app):${NC}"
kubectl get pods -n applications

echo -e "\n${YELLOW}DaemonSets (all nodes):${NC}"
kubectl get daemonsets -n applications

echo -e "\n${YELLOW}Node Usage:${NC}"
kubectl top nodes 2>/dev/null || echo "Metrics not available yet (wait 1 min)"

echo -e "\n${YELLOW}Access Services:${NC}"
echo -e "  ${GREEN}Kibana${NC}       : kubectl port-forward svc/kibana -n monitoring 5601:5601"
echo -e "  ${GREEN}Prometheus${NC}   : kubectl port-forward svc/prometheus -n monitoring 9090:9090"
echo -e "  ${GREEN}Grafana${NC}      : kubectl port-forward svc/grafana -n monitoring 3000:3000"
echo -e "  ${GREEN}Frontend${NC}     : kubectl port-forward svc/frontend -n applications 8080:8080"

echo -e "\n${YELLOW}Service Details:${NC}"
kubectl get svc -n monitoring
kubectl get svc -n applications

echo -e "\n${YELLOW}Next Steps:${NC}"
echo "1. Wait 3-5 minutes for Elasticsearch to fully start"
echo "2. Port forward to access services (see commands above)"
echo "3. Check logs if pods aren't starting:"
echo "   kubectl logs <pod-name> -n <namespace>"
echo "4. Delete persistent volume if you need to reset:"
echo "   kubectl delete pvc elasticsearch-data -n monitoring"
