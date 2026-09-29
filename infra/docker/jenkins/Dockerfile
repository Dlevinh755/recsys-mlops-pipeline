# Phase 8 — Jenkins CI (ADR 0005: Jenkins instead of GitHub Actions).
#
# Adds the Docker CLI + Compose plugin (not in the base image) so pipeline
# steps can run `docker build`/`docker compose ...` directly. The container
# mounts the host's `/var/run/docker.sock` at runtime
# (`infra/jenkins/docker-compose.jenkins.yml`) — sibling-container pattern,
# same as `infra/docker/airflow/Dockerfile` (Phase 6): Jenkins has no Docker
# daemon of its own, every command it runs talks straight to the host
# daemon, so images it builds land in the host's normal image store.
FROM jenkins/jenkins:lts-jdk17

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable" \
        > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Pinned so a fresh `docker compose build jenkins` doesn't silently change
# which plugins are installed between runs.
COPY infra/docker/jenkins/plugins.txt /usr/share/jenkins/ref/plugins.txt
RUN jenkins-plugin-cli --plugin-file /usr/share/jenkins/ref/plugins.txt

USER jenkins

# Skip the interactive setup wizard — this instance is only ever reached
# from inside the local Docker network (not exposed publicly, same
# "chạy hoàn toàn local" stance as every other service here), and the admin
# user is provisioned once via Jenkins Configuration as Code
# (`infra/jenkins/casc.yaml`) instead of the browser wizard.
ENV JAVA_OPTS="-Djenkins.install.runSetupWizard=false"
ENV CASC_JENKINS_CONFIG=/var/jenkins_home/casc.yaml
