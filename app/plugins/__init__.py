# ============================================================
# app/plugins/__init__.py
# Tự động nạp toàn bộ plugin trong app/plugins/ (AR §7.1):
# thành viên chỉ cần thả file vào đúng thư mục con và thêm @hub.register_...
# ============================================================
import importlib
import pkgutil

_loaded = False


def load_plugins(force: bool = False):
    """Import mọi module con để các decorator @hub.register_... tự đăng ký."""
    global _loaded
    if _loaded and not force:
        return
    import app.plugins as pkg
    for module_info in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."):
        try:
            importlib.import_module(module_info.name)
        except Exception as e:
            print(f"[PLUGINS] Bỏ qua '{module_info.name}' do lỗi nạp: {e}")
    _loaded = True
