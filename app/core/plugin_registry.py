# ============================================================
# app/core/plugin_registry.py
# Khung mở trung tâm (framework mở @hub.register_...) [AR §7.2]
# Giữ nguyên tên decorator; bổ sung type hint rõ ràng cho plugin registry.
# ============================================================
from typing import Any, Callable, Dict

RegistryFunction = Callable[..., Any]


class OpenSystemRegistry:
    """
    Trạm Đăng kí Trung ương: cho phép cắm bất kì Tool, Algorithm, Embedding,
    data source hoặc test suite nào vào hệ thống MÀ KHÔNG SỬA CORE CODE.
    """

    def __init__(self) -> None:
        self.data_ingestors: Dict[str, RegistryFunction] = {}
        self.embedding_engines: Dict[str, RegistryFunction] = {}
        self.practical_tools: Dict[str, RegistryFunction] = {}
        self.theory_algorithms: Dict[str, RegistryFunction] = {}
        self.logic_verifiers: Dict[str, RegistryFunction] = {}
        self.sandbox_simulators: Dict[str, RegistryFunction] = {}

    def register_ingestor(
        self, name: str
    ) -> Callable[[RegistryFunction], RegistryFunction]:
        def wrapper(func: RegistryFunction) -> RegistryFunction:
            self.data_ingestors[name] = func
            return func

        return wrapper

    def register_embedding(
        self, name: str
    ) -> Callable[[RegistryFunction], RegistryFunction]:
        def wrapper(func: RegistryFunction) -> RegistryFunction:
            self.embedding_engines[name] = func
            return func

        return wrapper

    def register_practical_tool(
        self, name: str
    ) -> Callable[[RegistryFunction], RegistryFunction]:
        def wrapper(func: RegistryFunction) -> RegistryFunction:
            self.practical_tools[name] = func
            return func

        return wrapper

    def register_theory_algo(
        self, name: str
    ) -> Callable[[RegistryFunction], RegistryFunction]:
        def wrapper(func: RegistryFunction) -> RegistryFunction:
            self.theory_algorithms[name] = func
            return func

        return wrapper

    def register_verifier(
        self, name: str
    ) -> Callable[[RegistryFunction], RegistryFunction]:
        def wrapper(func: RegistryFunction) -> RegistryFunction:
            self.logic_verifiers[name] = func
            return func

        return wrapper

    def register_sandbox(
        self, name: str
    ) -> Callable[[RegistryFunction], RegistryFunction]:
        def wrapper(func: RegistryFunction) -> RegistryFunction:
            self.sandbox_simulators[name] = func
            return func

        return wrapper


# Khởi tạo global object cho toàn bộ repo OSINTViet
hub = OpenSystemRegistry()

__all__ = ["OpenSystemRegistry", "RegistryFunction", "hub"]
