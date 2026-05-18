"""Project CLI entrypoints (PR #20).

Tiny wrappers around long-running async tasks that are too small
to deserve a dedicated Celery beat schedule. Currently houses
:mod:`app.cli.healthcheck` — the cron-friendly way to invoke
:class:`app.agents.healthcheck.HealthCheckAgent` outside the
admin UI.
"""
