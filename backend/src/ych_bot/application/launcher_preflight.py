"""No-write checks performed before Uvicorn binds the administrator port."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import socket
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ych_bot.application.preflight import DatabasePreflightService
from ych_bot.config import Settings
from ych_bot.domain.readiness import (
    CapabilityScope,
    EvidenceFreshness,
    LauncherPreflightResult,
    ProbeStatus,
    ReadinessProbeResult,
)

LAUNCHER_EVIDENCE_TTL = timedelta(minutes=2)


def safe_configuration_fingerprint(settings: Settings) -> str:
    """Hash non-secret settings that affect launcher safety."""

    payload = {
        "admin_host": settings.admin_host,
        "admin_port": settings.admin_port,
        "bot_qq": settings.bot_qq,
        "database_name": settings.database_path.name,
        "migration_backup_enabled": settings.migration_backup_enabled,
        "outbound_enabled": settings.outbound_enabled,
        "owner_reports_enabled": settings.owner_reports_enabled,
        "proactive_scheduler_enabled": settings.proactive_scheduler_enabled,
        "qzone_publish_enabled": settings.qzone_publish_enabled,
        "reply_runtime_max_mode": settings.reply_runtime_max_mode,
        "reply_worker_enabled": settings.reply_worker_enabled,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class LauncherPreflightService:
    def __init__(
        self,
        settings: Settings,
        *,
        database_preflight: DatabasePreflightService | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._database = database_preflight or DatabasePreflightService(
            database_path=settings.database_path,
            project_root=settings.project_root,
            backup_enabled=settings.migration_backup_enabled,
        )
        self._clock = clock or (lambda: datetime.now(UTC))

    async def inspect(
        self,
        *,
        process_instance_id: str,
        accept_current_listener: bool = False,
    ) -> LauncherPreflightResult:
        if not process_instance_id:
            raise ValueError("process_instance_id is required")
        observed_at = self._clock().astimezone(UTC)
        expires_at = observed_at + LAUNCHER_EVIDENCE_TTL
        freshness = EvidenceFreshness(observed_at=observed_at, expires_at=expires_at)
        database, port = await asyncio.gather(
            self._database.inspect(),
            asyncio.to_thread(
                _inspect_port,
                self._settings.admin_host,
                self._settings.admin_port,
                accept_current_listener,
            ),
        )
        probes = (
            self._configuration_probe(freshness),
            self._database_probe(freshness, database),
            self._managed_roots_probe(freshness),
            self._admin_auth_probe(freshness),
            _probe(
                code="launcher.port",
                status=ProbeStatus.PASS if port[0] else ProbeStatus.BLOCKED,
                freshness=freshness,
                source_revision=f"{self._settings.admin_host}:{self._settings.admin_port}",
                safe_detail=port[1],
                remediation_code="free_admin_port" if not port[0] else "",
            ),
        )
        return LauncherPreflightResult(
            process_instance_id=process_instance_id,
            configuration_fingerprint=safe_configuration_fingerprint(self._settings),
            observed_at=observed_at,
            expires_at=expires_at,
            probes=probes,
        )

    def _configuration_probe(self, freshness: EvidenceFreshness) -> ReadinessProbeResult:
        host_valid = _is_loopback_host(self._settings.admin_host)
        return _probe(
            code="launcher.configuration",
            status=ProbeStatus.PASS if host_valid else ProbeStatus.BLOCKED,
            freshness=freshness,
            source_revision=safe_configuration_fingerprint(self._settings),
            safe_detail=(
                "local configuration is valid"
                if host_valid
                else "administrator listener must use a loopback address"
            ),
            remediation_code="use_loopback_admin_host" if not host_valid else "",
        )

    def _database_probe(
        self, freshness: EvidenceFreshness, inspection: dict[str, object]
    ) -> ReadinessProbeResult:
        ready = bool(inspection.get("ready"))
        revision = (
            f"{inspection.get('current_schema_version', 0)}:"
            f"{inspection.get('latest_schema_version', 0)}:"
            f"{int(bool(inspection.get('needs_migration')))}"
        )
        return _probe(
            code="launcher.database",
            status=ProbeStatus.PASS if ready else ProbeStatus.BLOCKED,
            freshness=freshness,
            source_revision=revision,
            safe_detail=(
                "database is readable and migration backup policy is available"
                if ready
                else "database or required migration backup policy is not ready"
            ),
            remediation_code="repair_database_preflight" if not ready else "",
            evidence=(
                ("database_exists", str(bool(inspection.get("database_exists"))).lower()),
                ("needs_migration", str(bool(inspection.get("needs_migration"))).lower()),
                ("integrity_ok", str(bool(inspection.get("integrity_ok"))).lower()),
            ),
        )

    def _managed_roots_probe(self, freshness: EvidenceFreshness) -> ReadinessProbeResult:
        roots = (
            self._settings.database_path.parent,
            self._settings.project_root / "storage" / "backups" / "migrations",
            self._settings.project_root / "storage" / "exports",
            self._settings.project_root / "storage" / "privacy-backups",
            self._settings.project_root / "storage" / "imports",
            self._settings.project_root / "storage" / "trash",
        )
        valid = all(
            _path_is_confined_and_available(self._settings.project_root, root) for root in roots
        )
        return _probe(
            code="launcher.managed_paths",
            status=ProbeStatus.PASS if valid else ProbeStatus.BLOCKED,
            freshness=freshness,
            source_revision=f"managed-roots:{len(roots)}",
            safe_detail=(
                f"{len(roots)} managed roots are confined"
                if valid
                else "one or more managed roots are unavailable or escape the project"
            ),
            remediation_code="repair_managed_roots" if not valid else "",
        )

    def _admin_auth_probe(self, freshness: EvidenceFreshness) -> ReadinessProbeResult:
        configured = bool(self._settings.admin_access_token)
        return _probe(
            code="launcher.admin_auth",
            status=ProbeStatus.PASS if configured else ProbeStatus.BLOCKED,
            freshness=freshness,
            source_revision=f"configured:{int(configured)}",
            safe_detail=(
                "administrator bootstrap authentication is configured"
                if configured
                else "administrator bootstrap authentication is not configured"
            ),
            remediation_code="configure_admin_access_token" if not configured else "",
            evidence=(("configured", str(configured).lower()),),
        )


def _probe(
    *,
    code: str,
    status: ProbeStatus,
    freshness: EvidenceFreshness,
    source_revision: str,
    safe_detail: str,
    remediation_code: str = "",
    evidence: tuple[tuple[str, str], ...] = (),
) -> ReadinessProbeResult:
    return ReadinessProbeResult(
        probe_code=code,
        status=status,
        capability_scope=CapabilityScope.LOCAL_RUNTIME,
        freshness=freshness,
        source="launcher",
        source_revision=source_revision,
        safe_detail=safe_detail,
        remediation_code=remediation_code,
        evidence=evidence,
    )


def _is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _path_is_confined_and_available(project_root: Path, candidate: Path) -> bool:
    try:
        project = project_root.resolve(strict=True)
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(project)
    except (OSError, ValueError):
        return False
    existing = resolved
    while not existing.exists() and existing != project:
        existing = existing.parent
    return existing.exists() and os.access(existing, os.R_OK | os.W_OK)


def _inspect_port(host: str, port: int, accept_current_listener: bool = False) -> tuple[bool, str]:
    if not _is_loopback_host(host):
        return False, "administrator listener is not loopback-only"
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    address: tuple[object, ...] = (host, port, 0, 0) if family == socket.AF_INET6 else (host, port)
    try:
        with socket.socket(family, socket.SOCK_STREAM) as listener:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(address)
    except OSError:
        if accept_current_listener and _current_process_owns_listen_port(port):
            return True, f"administrator port {port} is owned by the current process"
        return False, f"administrator port {port} is owned by another listener"
    return True, f"administrator port {port} is available"


def _current_process_owns_listen_port(port: int) -> bool:
    return os.getpid() in _listen_owner_pids(port)


def _listen_owner_pids(port: int) -> frozenset[int]:
    if os.name == "nt":
        return _windows_listen_owner_pids(port)
    return frozenset()


def _windows_listen_owner_pids(port: int) -> frozenset[int]:
    try:
        return _windows_listen_owner_pids_unchecked(port)
    except (OSError, ValueError, TypeError, AttributeError, BufferError):
        return frozenset()


def _windows_listen_owner_pids_unchecked(port: int) -> frozenset[int]:
    import ctypes
    from ctypes import wintypes

    iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)
    af_inet = 2
    af_inet6 = 23
    tcp_table_owner_pid_listener = 3
    error_insufficient_buffer = 122

    class TcpRow(ctypes.Structure):
        _fields_ = [
            ("dwState", wintypes.DWORD),
            ("dwLocalAddr", wintypes.DWORD),
            ("dwLocalPort", wintypes.DWORD),
            ("dwRemoteAddr", wintypes.DWORD),
            ("dwRemotePort", wintypes.DWORD),
            ("dwOwningPid", wintypes.DWORD),
        ]

    class Tcp6Row(ctypes.Structure):
        _fields_ = [
            ("ucLocalAddr", ctypes.c_ubyte * 16),
            ("dwLocalScopeId", wintypes.DWORD),
            ("dwLocalPort", wintypes.DWORD),
            ("ucRemoteAddr", ctypes.c_ubyte * 16),
            ("dwRemoteScopeId", wintypes.DWORD),
            ("dwRemotePort", wintypes.DWORD),
            ("dwState", wintypes.DWORD),
            ("dwOwningPid", wintypes.DWORD),
        ]

    def _rows(address_family: int, row_type: type[ctypes.Structure]) -> list[object]:
        size = wintypes.DWORD(0)
        status = iphlpapi.GetExtendedTcpTable(
            None,
            ctypes.byref(size),
            False,
            address_family,
            tcp_table_owner_pid_listener,
            0,
        )
        if status != error_insufficient_buffer or size.value == 0:
            return []

        class Table(ctypes.Structure):
            _fields_ = [
                ("dwNumEntries", wintypes.DWORD),
                ("table", row_type * 1),
            ]

        buf = ctypes.create_string_buffer(size.value)
        status = iphlpapi.GetExtendedTcpTable(
            buf,
            ctypes.byref(size),
            False,
            address_family,
            tcp_table_owner_pid_listener,
            0,
        )
        if status != 0:
            return []
        count = wintypes.DWORD.from_buffer_copy(buf, 0).value
        row_array = (row_type * max(count, 0)).from_buffer_copy(buf, ctypes.sizeof(wintypes.DWORD))
        return list(row_array)

    owners: set[int] = set()
    for row in (*_rows(af_inet, TcpRow), *_rows(af_inet6, Tcp6Row)):
        local_port = socket.ntohs(int(row.dwLocalPort) & 0xFFFF)
        pid = int(row.dwOwningPid)
        if local_port == port and pid > 0:
            owners.add(pid)
    return frozenset(owners)
