from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, Optional

from storage import BaseStore


class BaseAuthService(ABC):
    backend_name = "base"

    @abstractmethod
    def authenticate(self, username: str, password: str) -> Optional[Dict]:
        raise NotImplementedError

    @abstractmethod
    def register(self, username: str, password: str, role: str, profile_data: Optional[Dict] = None) -> Dict:
        raise NotImplementedError


class LocalAuthService(BaseAuthService):
    backend_name = "local"

    def __init__(self, store: BaseStore) -> None:
        self.store = store

    def authenticate(self, username: str, password: str) -> Optional[Dict]:
        return self.store.authenticate(username, password)

    def register(self, username: str, password: str, role: str, profile_data: Optional[Dict] = None) -> Dict:
        if role == "admin":
            raise ValueError("O perfil admin deve ser atribuído fora do registo público.")
        payload = profile_data or {}
        return self.store.create_user(
            username=username,
            password=password,
            role=role,
            full_name=payload.get("full_name", ""),
            organization=payload.get("organization", ""),
            email=payload.get("email", ""),
            phone=payload.get("phone", ""),
            whatsapp_number=payload.get("whatsapp_number", ""),
            whatsapp_opt_in=bool(payload.get("whatsapp_opt_in", False)),
            whatsapp_opt_in_at=payload.get("whatsapp_opt_in_at", ""),
        )


def create_auth_service(store: BaseStore) -> BaseAuthService:
    return LocalAuthService(store=store)
