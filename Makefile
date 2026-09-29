SHELL := /bin/bash
.DEFAULT_GOAL := help

.PHONY: help env up down clean ps logs validate test smoke extract install-dev bronze silver gold test-integration user-features train-dataset train evaluate promote similar-items airflow-up airflow-down

help:
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "%-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

env: ## Create local .env from the safe development template when missing
	@test -f .env || cp .env.example .env

up: env ## Start Phase 0 infrastructure in the background
	docker compose up -d --build

down: ## Stop containers and retain persistent volumes
	docker compose down --remove-orphans

clean: ## Stop containers and DELETE local Docker volumes
	docker compose down --volumes --remove-orphans

ps: ## Show Compose service status
	docker compose ps

logs: ## Follow logs for all services
	docker compose logs -f --tail=200

install-dev: ## Install the internal package and development dependencies
	python3 -m pip install -e ./libs -r requirements/dev.txt

validate: ## Validate Phase 0 files and Compose when Docker is available
	python3 scripts/validate_phase0.py

test: ## Run unit tests
	python3 -m unittest discover -s tests/unit -p 'test_*.py' -v

smoke: ## Verify running Phase 0 services and seed data
	bash scripts/smoke_test.sh

extract: ## Run the Phase 1 incremental extract job
	bash scripts/run_job_locally.sh extract

bronze: ## Run the Phase 2 build_bronze job (extract output -> Bronze Iceberg)
	docker compose --profile jobs run --rm transform-job jobs.transform.build_bronze

silver: ## Run the Phase 2 build_silver job (Bronze -> Silver Iceberg)
	docker compose --profile jobs run --rm transform-job jobs.transform.build_silver

gold: ## Run the Phase 2 build_gold job (Silver -> Gold Iceberg)
	docker compose --profile jobs run --rm transform-job jobs.transform.build_gold

test-integration: ## Run tests/integration against real MinIO + Lakekeeper (needs `make up`)
	docker compose --profile jobs run --rm --entrypoint python transform-job -m pytest /app/tests/integration -v

user-features: ## Run the Phase 3 build_user_features job (Silver -> Gold user sequence)
	docker compose --profile jobs run --rm transform-job jobs.features.build_user_features

train-dataset: ## Run the Phase 4 build_sequence_dataset job (prints train/val/vocab stats)
	docker compose --profile jobs run --rm training-job jobs.training.build_sequence_dataset

train: ## Run the Phase 4 train_sequence job (GRU4Rec, logs to MLflow)
	docker compose --profile jobs run --rm training-job jobs.training.train_sequence $(ARGS)

evaluate: ## Re-evaluate a trained run: make evaluate ARGS="--run-id <id>"
	docker compose --profile jobs run --rm training-job jobs.training.evaluate $(ARGS)

promote: ## Run the Phase 4 promote job (production alias gate)
	docker compose --profile jobs run --rm training-job jobs.training.promote $(ARGS)

similar-items: ## Run the Phase 5 similar_items job (GRU embeddings -> Redis)
	docker compose --profile jobs run --rm training-job jobs.candidates.similar_items $(ARGS)

airflow-up: env ## Start Phase 6 Airflow (webserver+scheduler+db) — separate from `make up`
	docker compose build airflow-init airflow-webserver airflow-scheduler
	docker compose up -d airflow-db airflow-init airflow-webserver airflow-scheduler

airflow-down: ## Stop Airflow containers only
	docker compose stop airflow-webserver airflow-scheduler airflow-init airflow-db
