from __future__ import annotations

import threading

from core import services
from core.live_data_refresh import PeriodicTaskScheduler
from core.runtime import Runtime, env_flag

_lock = threading.Lock()
_started = False


def configure_external_refresh(app, runtime: Runtime):
    def refresh_wave_snapshot() -> None:
        if not getattr(services, "wave_service", None) or not services.wave_service.enabled:
            return
        try:
            services.wave_service.probe_current_conditions()
        except Exception as exc:
            app.logger.warning("[external-refresh] Falha ao atualizar ondulação: %s", exc)

    def refresh_local_warning_snapshot() -> None:
        if not getattr(services, "local_warning_service", None) or not services.local_warning_service.enabled:
            return
        try:
            services.local_warning_service.probe_warnings()
        except Exception as exc:
            app.logger.warning("[external-refresh] Falha ao atualizar avisos locais: %s", exc)

    services.wave_refresh_scheduler = PeriodicTaskScheduler(
        name="wave-refresh-hourly",
        callback=refresh_wave_snapshot,
        interval_seconds=runtime.wave_refresh_interval_seconds,
        run_immediately=True,
    )
    services.local_warning_refresh_scheduler = PeriodicTaskScheduler(
        name="local-warning-refresh-hourly",
        callback=refresh_local_warning_snapshot,
        interval_seconds=runtime.local_warning_refresh_interval_seconds,
        run_immediately=True,
    )

    def ensure_external_refresh_started() -> None:
        global _started
        if app.config.get("TESTING") or not env_flag("EXTERNAL_DATA_REFRESH_ENABLED", default="1"):
            return

        with _lock:
            if _started:
                return
            started_labels = []
            if services.wave_refresh_scheduler and services.wave_refresh_scheduler.start():
                started_labels.append(f"ondulação/{runtime.wave_refresh_interval_seconds}s")
            if services.local_warning_refresh_scheduler and services.local_warning_refresh_scheduler.start():
                started_labels.append(f"avisos/{runtime.local_warning_refresh_interval_seconds}s")
            _started = True

        if started_labels:
            app.logger.info(
                "Refresh periódico de dados externos ativo: %s",
                ", ".join(started_labels),
            )

    return ensure_external_refresh_started
