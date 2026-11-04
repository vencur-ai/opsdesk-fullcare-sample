# OpsDesk FullCare fullcare-0.6.4 – one workspace per container, offline, no external assets.
FROM python:3.12-slim
WORKDIR /app
COPY runtime/requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY runtime/opsdesk ./opsdesk
COPY data /srv/data
RUN useradd --system --uid 10001 --home /app opsdesk && mkdir -p /workspace-out && chown opsdesk /workspace-out \
    && chmod -R go-w /srv/data /app
USER opsdesk
ENV PYTHONPATH="/app" PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0 OPSDESK_TASKS_ROOT=/srv/data/tasks
EXPOSE 8080
# OPSDESK_TASK selects the workspace; OPSDESK_HOST_KEY protects /host/* (finish, export, reset, care state).
CMD ["sh", "-c", "python -m opsdesk.fullcare.server --host 0.0.0.0 --port 8080 --tasks-root $OPSDESK_TASKS_ROOT --task $OPSDESK_TASK"]
