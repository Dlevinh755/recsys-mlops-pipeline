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
    // Workspace cố định tại đường dẫn trùng nhau giữa container Jenkins và Docker
    // của máy host (infra/jenkins/docker-compose.jenkins.yml mount
    // /srv/jenkins-ws:/srv/jenkins-ws). Bắt buộc: mọi bind mount tương đối
    // trong docker-compose.yml (./infra/..., ./airflow/...) và `-v $(pwd)`
    // do daemon của host phân giải; đường dẫn mặc định trong
    // /var/jenkins_home không tồn tại ở đó nên Docker tạo thư mục rỗng
    // (minio-init exit 126). Chỉ một workspace cố định: build nhiều nhánh
    // song song sẽ dùng chung thư mục này.
    agent {
        node {
            label ''
            customWorkspace '/srv/jenkins-ws/recsys-mlops'
        }
    }

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
        // Mọi stage phải nạp cả docker-compose.ci.yml (bỏ cổng host): stage
        // Lint/Unit Test cũng `docker compose run` nên kéo theo catalog-db...
        // và va cổng 5434 với stack dev nếu thiếu file này.
        COMPOSE_FILE = 'docker-compose.yml:docker-compose.override.yml:docker-compose.ci.yml'
        // Every image this build produces is named `local/mlops4rec-<x>:<GIT_SHA>`
        // (docker-compose.yml: `${DOCKERHUB_USERNAME:-local}/mlops4rec-<x>:${IMAGE_TAG:-dev}`).
        // Both are forced here, not inherited: the Jenkins container's own
        // DOCKERHUB_USERNAME (set when started with --env-file) used to leak in,
        // so compose built `dlvinh755/...:dev` while `Build & Push` retagged
        // `local/...:dev` — a stale image from an earlier build (found in build #8).
        // With the commit SHA in the tag a stale image cannot exist for this
        // commit: the image tested below is exactly the one pushed.
        DOCKERHUB_USERNAME = 'local'
        IMAGE_TAG = "${GIT_SHA}"
    }

    stages {
        stage('Prepare') {
            steps {
                sh 'test -f .env || cp .env.example .env'
            }
        }

        stage('Build test images') {
            steps {
                // The 5 job images (the 3 the test stages run inside, plus
                // `extract-job` and `monitoring-job`). `serving`/`airflow`/`ui`
                // are built by `docker compose up -d --build` in the
                // Integration Test stage. `Build & Push Images` only retags
                // and pushes these exact images — it never rebuilds.
                sh 'docker compose build transform-job training-job materialize-job extract-job monitoring-job'
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
                // ADR 0014: retrain decision rule + drift summary parser (evidently lives
                // only in the monitoring image). No stack needed → --no-deps.
                sh 'docker compose --profile jobs run --rm --no-deps --entrypoint python monitoring-job -m pytest tests/monitoring/test_retrain_decision.py tests/monitoring/test_drift_summary.py -v'
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
                // ADR 0014: baseline files + drift-report lookup on the real MinIO
                // (keys under it_test_*, removed afterwards).
                sh 'docker compose --profile jobs run --rm --entrypoint python monitoring-job -m pytest /app/tests/monitoring/test_baseline_minio.py -v'
                // Feed the pipeline for real so the skew test below has
                // both an offline (Gold) and online (Feast/Redis) side to
                // compare — no point testing skew against empty tables.
                // Extract first: without it Bronze is empty and the skew test
                // below fails on "gold.user_features is empty" (build #6).
                // dlt's working dir goes to the container's /tmp, not the
                // bind-mounted data/dlt-pipelines: that dir lives in the fixed
                // workspace and would carry a cursor from a previous build into
                // this build's empty bucket, silently extracting 0 rows.
                sh 'docker compose --profile jobs run --rm -e DLT_PIPELINES_DIR=/tmp/dlt-pipelines extract-job'
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
                // --no-deps: without it `run` starts training-job's depends_on
                // (mlflow, minio, lakekeeper, redis...) AFTER the Integration
                // Test stage already ran `down`, and nothing tears them down
                // again — builds #7 and #8 each left ~9 containers (mlflow
                // 1.6 GB) running, which later starved build #9 (slow tests,
                // DNS errors).
                sh '''
                    docker compose --profile jobs run --rm --no-deps \
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
                // (DOCKERHUB_TOKEN in .env). catchError keeps a failed push
                // from aborting the build, so lint/test/model-smoke-test
                // results are never hidden behind "no registry configured
                // yet". The build ends UNSTABLE (yellow), not SUCCESS: with
                // SUCCESS a push that failed (build #12: `transform`, a
                // proxy timeout to Docker Hub) left the whole build green
                // while an image was missing from the registry.
                catchError(buildResult: 'UNSTABLE', stageResult: 'FAILURE') {
                    sh 'echo "$DOCKERHUB_CREDS_PSW" | docker login -u "$DOCKERHUB_CREDS_USR" --password-stdin'
                    script {
                        // `failFast` left at its declarative default
                        // (false) on purpose — these 8 pushes are fully
                        // independent, so surfacing every failure in one
                        // run beats stopping at the first and re-running to
                        // find the next (PHONG-VAN-QA-BAO-VE.md mục 4).
                        // Retag only, never rebuild for push: "test one
                        // artifact, ship a different one" is the anti-pattern
                        // this avoids. `docker tag` fails loudly if the
                        // `<GIT_SHA>` image of this build does not exist.
                        def images = ['extract', 'transform', 'materialize', 'training',
                                      'serving', 'airflow', 'monitoring', 'ui']
                        parallel images.collectEntries { name ->
                            [(name): {
                                sh """
                                    docker tag local/mlops4rec-${name}:${GIT_SHA} \$DOCKERHUB_CREDS_USR/mlops4rec-${name}:${GIT_SHA}
                                    docker push \$DOCKERHUB_CREDS_USR/mlops4rec-${name}:${GIT_SHA}
                                """
                            }]
                        }
                    }
                }
            }
        }
    }

    post {
        always {
            // Safety net: whatever a stage started under this build's
            // COMPOSE_PROJECT_NAME is removed even if a stage forgets to.
            sh 'docker compose down --volumes --remove-orphans || true'
            sh 'docker logout || true'
        }
    }
}
