// Phase 8 — CI/CD (ADR 0005: Jenkins instead of GitHub Actions; full design
// rationale for every decision below is in
// ../docs/decisions/0005-jenkins-thay-github-actions.md and
// ../PHONG-VAN-QA-BAO-VE.md mục 4). A Multibranch Pipeline job points
// straight at this repo root — everything below runs with cwd =
// `recommendation-mlops/`, same as every `make`/`docker compose` command a
// developer runs by hand (CLAUDE.md).
//
// One agent for the whole pipeline — no per-stage Docker agents, this
// container already mounts the host's docker.sock
// (infra/docker/jenkins/Dockerfile) and talks straight to the host Docker
// daemon (sibling-container pattern, same as Airflow's DockerOperator,
// Phase 6).
pipeline {
    agent any

    options {
        // Two builds of the *same* branch shouldn't race each other over
        // the same workspace; different branches/PRs still run in parallel
        // fine (each gets its own `COMPOSE_PROJECT_NAME` below).
        disableConcurrentBuilds(abortPrevious: false)
        timestamps()
    }

    triggers {
        // No public endpoint to receive a GitHub webhook (this Jenkins runs
        // fully local) — Jenkins polls the remote instead. ~5 min latency,
        // acceptable at thesis-project commit frequency; see ADR 0005 for
        // the upgrade path (tunnel + githubPush()) if that ever matters.
        pollSCM('H/5 * * * *')
    }

    environment {
        GIT_SHA = "${sh(script: 'git rev-parse --short HEAD', returnStdout: true).trim()}"
        // Isolates every build's containers/network/volumes from every other
        // build (and from a developer's own `make up`) — docker-compose.yml's
        // `name:`/`networks.reco-net.name` already key off this var, so
        // nothing else needs to change for two builds to run in parallel
        // without colliding on Compose project names.
        COMPOSE_PROJECT_NAME = "reco-ci-${env.BUILD_NUMBER}"
    }

    stages {
        stage('Prepare') {
            steps {
                sh 'test -f .env || cp .env.example .env'
            }
        }

        stage('Build test images') {
            steps {
                // Only the 3 images the test stages below actually run
                // inside — local only, not pushed. `serving`/`monitoring`/
                // `airflow`/`ui` have no test stage exercising them yet, so
                // building them only happens in `Build & Push Images`.
                sh 'docker compose build transform-job training-job materialize-job'
            }
        }

        stage('Lint') {
            steps {
                // Bind-mount the real checkout instead of relying on what
                // got COPY'd into the image — `serving/`/`airflow/`/`ui/`
                // aren't part of transform-job's own image, but ruff still
                // needs to see them.
                sh '''
                    docker compose --profile jobs run --rm \
                      -v "$(pwd):/repo:ro" --entrypoint python transform-job \
                      -m ruff check --no-cache /repo/jobs /repo/libs /repo/serving /repo/airflow /repo/ui
                '''
            }
        }

        stage('Unit Test') {
            // Split across the 3 images that actually have the modules
            // each test file imports (`jobs.extract`/`jobs.training` aren't
            // part of `transform-job`'s image, and vice versa) — no single
            // image carries every job group's dependencies.
            steps {
                sh 'docker compose --profile jobs run --rm --entrypoint python transform-job -m pytest tests/unit/test_package_metadata.py -v'
                sh 'docker compose --profile jobs run --rm --entrypoint python extract-job -m pytest tests/unit/extract -v'
                sh 'docker compose --profile jobs run --rm --entrypoint python training-job -m pytest tests/unit/training -v'
            }
        }

        stage('Integration Test') {
            // Highest technical risk (PyIceberg + REST Catalog) — runs
            // against real MinIO/Lakekeeper, no mock (cau-truc-project.md).
            environment {
                // Every `docker compose` call below must resolve the exact
                // same config (all 3 files) — mixing a call that omits
                // `docker-compose.ci.yml` back in makes Compose think ports
                // changed on an already-running service and try to
                // recreate it, which then fights the real dev stack (or
                // another CI build) for the same host port. Verified
                // empirically hitting exactly this while writing this
                // stage — not a hypothetical concern.
                COMPOSE_FILE = 'docker-compose.yml:docker-compose.override.yml:docker-compose.ci.yml'
            }
            steps {
                sh 'docker compose up -d --build'
                sh 'docker compose --profile jobs run --rm --entrypoint python transform-job -m pytest /app/tests/integration -v'
                // Feed the pipeline for real so the skew test below has
                // both an offline (Gold) and online (Feast/Redis) side to
                // compare — no point testing skew against empty tables.
                sh 'docker compose --profile jobs run --rm transform-job jobs.transform.build_bronze'
                sh 'docker compose --profile jobs run --rm transform-job jobs.transform.build_silver'
                sh 'docker compose --profile jobs run --rm transform-job jobs.transform.build_gold'
                sh 'docker compose --profile jobs run --rm transform-job jobs.features.build_user_features'
                sh 'docker compose --profile jobs run --rm materialize-job jobs.materialize.export_to_parquet'
                sh 'docker compose --profile jobs run --rm materialize-job jobs.materialize.feast_materialize'
                sh 'docker compose --profile jobs run --rm --entrypoint python materialize-job -m pytest /app/tests/data_quality -v'
            }
            post {
                always {
                    // Always tear down, even on failure — don't leave a
                    // "reco-ci-<N>" container/volume set orphaned on the CI
                    // machine.
                    sh 'docker compose down --volumes --remove-orphans'
                }
            }
        }

        stage('Model Smoke Test') {
            // File-based MLflow, no tracking server, no Iceberg, no
            // `docker compose up` (ADR 0005 update 2026-09-15):
            // tests/model/sample_data/ is a small fixed-seed fixture, not
            // read from Gold.
            environment {
                MLFLOW_TRACKING_URI = 'file:///tmp/mlruns'
            }
            steps {
                sh '''
                    docker compose --profile jobs run --rm \
                      -e MLFLOW_TRACKING_URI=$MLFLOW_TRACKING_URI \
                      --entrypoint python training-job -m pytest tests/model -v
                '''
            }
        }

        stage('Build & Push Images') {
            environment {
                DOCKERHUB_CREDS = credentials('dockerhub-creds') // -> DOCKERHUB_CREDS_USR / _PSW
            }
            steps {
                // `dockerhub-creds` (infra/jenkins/casc.yaml) starts empty
                // until a real Docker Hub Access Token is set
                // (DOCKERHUB_TOKEN in .env). catchError lets every stage
                // above still report a clean pipeline while this one stays
                // visibly failed/unstable instead of aborting the whole
                // build — lint/test/model-smoke-test results are never
                // hidden behind "no registry configured yet".
                catchError(buildResult: 'SUCCESS', stageResult: 'FAILURE') {
                    sh 'echo "$DOCKERHUB_CREDS_PSW" | docker login -u "$DOCKERHUB_CREDS_USR" --password-stdin'
                    script {
                        // `failFast` left at its declarative default
                        // (false) on purpose — these 8 builds are fully
                        // independent, so surfacing every failure in one
                        // run beats stopping at the first and re-running to
                        // find the next (PHONG-VAN-QA-BAO-VE.md mục 4).
                        // transform/materialize/training: retag the exact
                        // image already built+tested above — never rebuild
                        // for push ("test one artifact, ship a different
                        // one" is the anti-pattern this avoids).
                        parallel(
                            extract: {
                                sh """
                                    docker build -t \$DOCKERHUB_CREDS_USR/mlops4rec-extract:${GIT_SHA} -f infra/docker/extract/Dockerfile .
                                    docker push \$DOCKERHUB_CREDS_USR/mlops4rec-extract:${GIT_SHA}
                                """
                            },
                            transform: {
                                sh """
                                    docker tag local/mlops4rec-transform:dev \$DOCKERHUB_CREDS_USR/mlops4rec-transform:${GIT_SHA}
                                    docker push \$DOCKERHUB_CREDS_USR/mlops4rec-transform:${GIT_SHA}
                                """
                            },
                            materialize: {
                                sh """
                                    docker tag local/mlops4rec-materialize:dev \$DOCKERHUB_CREDS_USR/mlops4rec-materialize:${GIT_SHA}
                                    docker push \$DOCKERHUB_CREDS_USR/mlops4rec-materialize:${GIT_SHA}
                                """
                            },
                            training: {
                                sh """
                                    docker tag local/mlops4rec-training:dev \$DOCKERHUB_CREDS_USR/mlops4rec-training:${GIT_SHA}
                                    docker push \$DOCKERHUB_CREDS_USR/mlops4rec-training:${GIT_SHA}
                                """
                            },
                            serving: {
                                sh """
                                    docker build -t \$DOCKERHUB_CREDS_USR/mlops4rec-serving:${GIT_SHA} -f infra/docker/serving/Dockerfile .
                                    docker push \$DOCKERHUB_CREDS_USR/mlops4rec-serving:${GIT_SHA}
                                """
                            },
                            airflow: {
                                sh """
                                    docker build -t \$DOCKERHUB_CREDS_USR/mlops4rec-airflow:${GIT_SHA} -f infra/docker/airflow/Dockerfile .
                                    docker push \$DOCKERHUB_CREDS_USR/mlops4rec-airflow:${GIT_SHA}
                                """
                            },
                            monitoring: {
                                sh """
                                    docker build -t \$DOCKERHUB_CREDS_USR/mlops4rec-monitoring:${GIT_SHA} -f infra/docker/monitoring/Dockerfile .
                                    docker push \$DOCKERHUB_CREDS_USR/mlops4rec-monitoring:${GIT_SHA}
                                """
                            },
                            ui: {
                                sh """
                                    docker build -t \$DOCKERHUB_CREDS_USR/mlops4rec-ui:${GIT_SHA} -f infra/docker/ui/Dockerfile .
                                    docker push \$DOCKERHUB_CREDS_USR/mlops4rec-ui:${GIT_SHA}
                                """
                            },
                        )
                    }
                }
            }
        }
    }

    post {
        always {
            sh 'docker logout || true'
        }
    }
}
