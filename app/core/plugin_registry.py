# ============================================================
# app/core/plugin_registry.py
# Khung mở trung tâm (framework mở @hub.register_...) [AR §7.2]
# Mã giữ nguyên theo tài liệu nguồn («source code được cung cấp, giữ nguyên như cũ»).
# ============================================================
from typing import Callable, Dict, Any, List

class OpenSystemRegistry:
    """
    Trạm Đăng kí Trung ương: cho phép cắm bất kì Tool, Algorithm, Embedding,
    data source hoặc test suite nào vào hệ thống MÀ KHÔNG SỬA CORE CODE.
    """
    def __init__(self):
        self.data_ingestors: Dict[str, Callable] = {}      # Head 1: Data Access & Loading
        self.embedding_engines: Dict[str, Callable] = {}   # Head 2: Embedding/Rerank Algorithms
        self.practical_tools: Dict[str, Callable] = {}     # Head 3A: Tool thực nghiệm
        self.theory_algorithms: Dict[str, Callable] = {}   # Head 3B: Thuật toán bắt rủi ro
        self.logic_verifiers: Dict[str, Callable] = {}     # Head 3C: Logic scanners (Fuzzy Prolog, Z3, SymPy...)
        self.sandbox_simulators: Dict[str, Callable] = {}  # Head 5: Tool ép thông số trong Sandbox

    # Các decorator để thành viên đảm bảo chỉ việc thêm dấu @ vào trên đầu hàm sẽ được tự động nhận diện!
    def register_ingestor(self, name: str):
        def wrapper(func: Callable):
            self.data_ingestors[name] = func
            return func
        return wrapper

    def register_embedding(self, name: str):
        def wrapper(func: Callable):
            self.embedding_engines[name] = func
            return func
        return wrapper

    def register_practical_tool(self, name: str):
        def wrapper(func: Callable):
            self.practical_tools[name] = func
            return func
        return wrapper

    def register_theory_algo(self, name: str):
        def wrapper(func: Callable):
            self.theory_algorithms[name] = func
            return func
        return wrapper

    def register_verifier(self, name: str):
        def wrapper(func: Callable):
            self.logic_verifiers[name] = func
            return func
        return wrapper

    def register_sandbox(self, name: str):
        def wrapper(func: Callable):
            self.sandbox_simulators[name] = func
            return func
        return wrapper

# Khởi tạo global object cho toàn bộ repo OSINTViet
hub = OpenSystemRegistry()
