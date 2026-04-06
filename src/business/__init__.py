"""
Package `business` — Lớp nghiệp vụ (Business Logic) của SV-PRO.

Export các singleton chính để dùng từ bất kỳ module nào trong dự án:
  from src.business import alert_manager, blacklist_engine, object_linker, audit_logger, access_controller
"""

from .alert_manager    import alert_manager, AlertManager
from .blacklist_engine import blacklist_engine, BlacklistEngine, BlacklistEvent, Severity
from .object_linker    import object_linker, ObjectLinker, LinkedEvent, VehicleObservation, PersonObservation
from .audit_logger     import audit_logger, AuditLogger
from .access_control   import access_controller, AccessController, DoorConfig, DoorEvent

__all__ = [
    # Singletons
    "alert_manager",
    "blacklist_engine",
    "object_linker",
    "audit_logger",
    "access_controller",
    # Classes
    "AlertManager",
    "BlacklistEngine",
    "BlacklistEvent",
    "Severity",
    "ObjectLinker",
    "LinkedEvent",
    "VehicleObservation",
    "PersonObservation",
    "AuditLogger",
    "AccessController",
    "DoorConfig",
    "DoorEvent",
]
