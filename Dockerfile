# Exact version tags; resolve and record registry digests in clean CI before release.
# No host .local files, credentials, raw data or old finance project enter this build.
ARG PYTHON_IMAGE=python:3.12.16-slim-bookworm
ARG JAVA_IMAGE=eclipse-temurin:21.0.13_9-jdk-jammy
FROM ${JAVA_IMAGE} AS java
FROM ${PYTHON_IMAGE} AS base
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates libstdc++6 zlib1g \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=java /opt/java/openjdk /app/.local/java-toolchain/jdk-runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false

FROM base AS build
COPY requirements-agent.lock /app/
RUN python -m venv /opt/agent-venv \
    && /opt/agent-venv/bin/python -m pip --isolated install --no-cache-dir --require-hashes -r requirements-agent.lock
COPY scripts/bootstrap_java.py /tmp/bootstrap_java.py
RUN python - <<'PY'
import hashlib, importlib.util, pathlib, tarfile, urllib.request
spec=importlib.util.spec_from_file_location('bootstrap','/tmp/bootstrap_java.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
package=next(p for p in module.PACKAGES if p['name'].startswith('apache-maven'))
target=pathlib.Path('/tmp')/package['name']
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open(package['url'],timeout=120) as source: target.write_bytes(source.read())
assert hashlib.new(package['algorithm'],target.read_bytes()).hexdigest()==package['digest']
with tarfile.open(target) as archive: archive.extractall('/app/.local/java-toolchain',filter='data')
PY
COPY services/business-api/pom.xml services/business-api/pom.xml
COPY services/business-api/src services/business-api/src
COPY deployment/maven-settings.xml /tmp/maven-settings.xml
RUN JAVA_HOME=/app/.local/java-toolchain/jdk-runtime MAVEN_SKIP_RC=true \
    /app/.local/java-toolchain/apache-maven-3.9.16/bin/mvn -B -ntp \
    -s /tmp/maven-settings.xml -gs /tmp/maven-settings.xml -Dmaven.repo.local=/tmp/m2 \
    -f services/business-api/pom.xml package

FROM base AS application
COPY --from=build /opt/agent-venv /opt/agent-venv
COPY --from=build /app/services/business-api/target/business-api-0.1.0.jar /app/services/business-api/target/business-api-0.1.0.jar
COPY commerce_agent commerce_agent
COPY commerce_data commerce_data
COPY commerce_import commerce_import
COPY commerce_worker commerce_worker
COPY scripts scripts
COPY examples/ledger examples/ledger
COPY requirements-agent.lock requirements-agent.in requirements-data.txt requirements-ledger.txt ./
COPY deployment deployment
COPY LICENSE THIRD_PARTY_NOTICES.md ./
COPY third_party third_party
COPY docs/dependency-licenses.json docs/dependency-licenses.json
RUN groupadd --gid 1000 commerce && useradd --uid 1000 --gid commerce --no-create-home commerce \
    && mkdir -p .local/owner-demo .local/single-agent .local/state-control .local/task-loop \
    && chown 1000:1000 .local .local/owner-demo .local/single-agent .local/state-control .local/task-loop
USER 1000:1000
EXPOSE 18080
HEALTHCHECK --interval=15s --timeout=5s --start-period=45s --retries=3 \
    CMD ["/opt/agent-venv/bin/python", "deployment/healthcheck.py"]
ENTRYPOINT ["/opt/agent-venv/bin/python", "deployment/entrypoint.py"]

# A clean Linux contract image. No model credential is supplied to verification.
FROM application AS verification
COPY services/business-api/src services/business-api/src
COPY tests tests
COPY experiments experiments
COPY docs/probes docs/probes
COPY examples examples
ENTRYPOINT ["/opt/agent-venv/bin/python"]
