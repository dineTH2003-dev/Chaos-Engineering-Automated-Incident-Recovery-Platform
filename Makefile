# ResilienceLab Makefile
# Single entry point for all project operations.

.PHONY: help setup cluster-start cluster-stop deploy undeploy \
        build test compose-up compose-down logs port-forward clean

NAMESPACE := resiliencelab
MINIKUBE_CPUS := 4
MINIKUBE_MEMORY := 6144
MINIKUBE_DISK := 30g
K6_BIN := $(shell command -v k6 2>/dev/null || echo "$(HOME)/.local/bin/k6")

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ─── Environment ───────────────────────────────────────────────

setup: ## Verify all required tools are installed
	@echo "=== Checking prerequisites ==="
	@docker --version
	@kubectl version --client --short 2>/dev/null || kubectl version --client
	@minikube version
	@helm version --short
	@python3 --version
	@$(K6_BIN) version 2>/dev/null || echo "WARNING: k6 not found. Install it for load testing."
	@echo "\n=== All tools verified ==="

cluster-start: ## Start Minikube cluster
	minikube start \
		--driver=docker \
		--cpus=$(MINIKUBE_CPUS) \
		--memory=$(MINIKUBE_MEMORY) \
		--disk-size=$(MINIKUBE_DISK)
	minikube addons enable metrics-server
	kubectl create namespace $(NAMESPACE) --dry-run=client -o yaml | kubectl apply -f -
	@echo "\n=== Cluster ready ==="
	kubectl get nodes

cluster-stop: ## Stop Minikube cluster
	minikube stop

# ─── Build ─────────────────────────────────────────────────────

build: ## Build Docker images inside Minikube
	@echo "=== Building images in Minikube Docker ==="
	eval $$(minikube docker-env) && \
		docker build -t resiliencelab-api:v1 ./application/api && \
		docker build -t resiliencelab-worker:v1 ./application/worker
	@echo "\n=== Images built ==="

# ─── Deploy ────────────────────────────────────────────────────

deploy-secrets: ## Create K8s secrets (copy from example if needed)
	@if ! kubectl get secret resiliencelab-secrets -n $(NAMESPACE) >/dev/null 2>&1; then \
		echo "Creating secrets from example..."; \
		sed 's/secrets.example.yaml/secrets.yaml/' kubernetes/secrets.example.yaml | \
			kubectl apply -f kubernetes/secrets.example.yaml; \
	else \
		echo "Secrets already exist."; \
	fi

deploy: deploy-secrets ## Deploy all services to Kubernetes
	kubectl apply -f kubernetes/namespace.yaml
	kubectl apply -f kubernetes/configmap.yaml
	kubectl apply -f kubernetes/secrets.example.yaml
	kubectl apply -f kubernetes/postgres.yaml
	@echo "Waiting for PostgreSQL..."
	kubectl rollout status deployment/postgres -n $(NAMESPACE) --timeout=120s
	kubectl apply -f kubernetes/api-deployment.yaml
	kubectl apply -f kubernetes/api-service.yaml
	kubectl apply -f kubernetes/worker-deployment.yaml
	@echo "Waiting for API..."
	kubectl rollout status deployment/api -n $(NAMESPACE) --timeout=120s
	@echo "Waiting for Worker..."
	kubectl rollout status deployment/worker -n $(NAMESPACE) --timeout=120s
	@echo "\n=== All services deployed ==="
	kubectl get pods -n $(NAMESPACE)

undeploy: ## Remove all services from Kubernetes
	kubectl delete -f kubernetes/ --ignore-not-found -n $(NAMESPACE)

# ─── Local Dev ─────────────────────────────────────────────────

compose-up: ## Start services with Docker Compose (local dev)
	docker compose up -d --build

compose-down: ## Stop Docker Compose services
	docker compose down -v

# ─── Operations ────────────────────────────────────────────────

port-forward: ## Port-forward API service to localhost:8000
	kubectl port-forward -n $(NAMESPACE) service/api-service 8000:80

logs: ## Tail logs from all pods
	kubectl logs -n $(NAMESPACE) -l app=resiliencelab-api --tail=50 -f

logs-worker: ## Tail worker logs
	kubectl logs -n $(NAMESPACE) -l app=resiliencelab-worker --tail=50 -f

status: ## Show cluster status
	@echo "=== Nodes ==="
	@kubectl get nodes
	@echo "\n=== Pods ==="
	@kubectl get pods -n $(NAMESPACE) -o wide
	@echo "\n=== Services ==="
	@kubectl get services -n $(NAMESPACE)
	@echo "\n=== Deployments ==="
	@kubectl get deployments -n $(NAMESPACE)

# ─── Test ──────────────────────────────────────────────────────

test: ## Run unit tests
	cd application/api && python3 -m pytest tests/ -v

test-health: ## Quick health check against running API
	@echo "=== Health Check ==="
	curl -s http://localhost:8000/health | python3 -m json.tool
	@echo "\n=== Readiness Check ==="
	curl -s http://localhost:8000/ready | python3 -m json.tool
	@echo "\n=== Metrics Check ==="
	curl -s http://localhost:8000/metrics | head -20

load-test-baseline: ## Run k6 baseline load test
	$(K6_BIN) run load-testing/baseline.js

load-test-stress: ## Run k6 stress load test
	$(K6_BIN) run load-testing/stress.js

# ─── Observability ─────────────────────────────────────────────

obs-status: ## Check status of observability stack
	@echo "=== Monitoring Pods ==="
	@kubectl get pods -n monitoring
	@echo "\n=== ServiceMonitors ==="
	@kubectl get servicemonitors -A
	@echo "\n=== PrometheusRules ==="
	@kubectl get prometheusrules -A

obs-port-forward-grafana: ## Port-forward Grafana to localhost:3001 (admin / prom-operator)
	@echo "Opening Grafana at http://localhost:3001 (Credentials: admin / prom-operator)..."
	kubectl port-forward -n monitoring svc/kube-prometheus-stack-grafana 3001:80

obs-port-forward-prometheus: ## Port-forward Prometheus to localhost:9091
	@echo "Opening Prometheus at http://localhost:9091..."
	kubectl port-forward -n monitoring svc/kube-prometheus-stack-prometheus 9091:9090

obs-port-forward-alertmanager: ## Port-forward Alertmanager to localhost:9093
	kubectl port-forward -n monitoring svc/kube-prometheus-stack-alertmanager 9093:9093

obs-port-forward-webhook: ## Port-forward Webhook Receiver to localhost:5001
	kubectl port-forward -n $(NAMESPACE) svc/webhook-receiver 5001:5001

obs-incidents: ## View recorded incidents from webhook receiver
	@kubectl exec -n $(NAMESPACE) deploy/webhook-receiver -- cat /reports/incidents/incidents.log 2>/dev/null || cat reports/incidents/incidents.log

# ─── Chaos Engineering ──────────────────────────────────────────

chaos-install: ## Install Chaos Mesh v2.8.6 with Minikube optimizations
	./chaos/installation/install.sh

chaos-verify-install: ## Verify Chaos Mesh installation and CRDs
	./chaos/installation/verify-installation.sh

chaos-status: ## List active chaos experiments and controller status
	@echo "=== Chaos Mesh Pods ==="
	@kubectl get pods -n chaos-mesh
	@echo "\n=== Active Fault Injections in $(NAMESPACE) ==="
	@kubectl get podchaos,networkchaos,stresschaos -n $(NAMESPACE)

chaos-ce01: ## Run CE-01: API Pod Failure & Self-Healing
	./chaos/scripts/run_experiment.sh ce01

chaos-ce02: ## Run CE-02: API-to-Database Network Latency
	./chaos/scripts/run_experiment.sh ce02

chaos-ce03: ## Run CE-03: PostgreSQL Dependency Outage & Recovery
	./chaos/scripts/run_experiment.sh ce03

chaos-ce04: ## Run CE-04: API CPU Saturation & Scaling
	./chaos/scripts/run_experiment.sh ce04

chaos-all: ## Run full automated chaos test suite (CE-01 to CE-04)
	./chaos/scripts/run_experiment.sh all

chaos-load-test: ## Run continuous k6 background load test for chaos experiments
	$(K6_BIN) run load-testing/chaos-test.js

chaos-verify-recovery: ## Verify cluster steady-state and health
	./chaos/scripts/verify_recovery.sh

chaos-abort: ## Emergency abort: kill all active chaos experiments immediately
	./chaos/scripts/cleanup_experiment.sh

chaos-reports: ## List all generated experiment and incident reports
	@echo "=== JSON Experiment Reports ==="
	@ls -lh reports/experiments/
	@echo "\n=== Markdown Incident Reports ==="
	@ls -lh reports/incidents/

# ─── Clean ─────────────────────────────────────────────────────

clean: ## Full cleanup — stop cluster, remove compose volumes
	-docker compose down -v
	-minikube delete
	@echo "=== Cleaned ==="
