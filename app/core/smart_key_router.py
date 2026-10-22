# ============================================================
# app/core/smart_key_router.py
# SmartKeyPoolRouter [DT §6.2 / AR §7.3]:
# - Quản lý 14 key Gemini 3.8 Flash (14 Google Cloud Project độc lập) + 3 API model khác
# - httpx.ReadTimeout / 429 / 503  ->  COOLDOWN 60 giây, hot-swap endpoint tiếp theo < 50ms
# - UCB1 Bandit theo (model, role), thay thế i % len(candidate_models)
# - Role Isolation Rule: ModelSet(Judges) ∩ ModelSet(Current_Match_Workers) = ∅
# ============================================================
import os
import json
import math
import time
from typing import Dict, List, Optional
import httpx
from app.core.config import settings

COOLDOWN_SECONDS = 60.0          # [DT §6.2] COOLDOWN 60 giây
HOT_SWAP_MAX_MS = 50.0           # [DT §6.2] hot-swap trong 50ms
ERROR_STATE_HINTS = ("readtimeout", "timeout", "429", "503")

# Nhóm role theo vai trò để áp Role Isolation Rule
JUDGE_ROLES = {"judge", "judge_a", "judge_b", "elo_judge"}
WORKER_ROLES = {"worker", "hyp_worker", "practical_critic", "theoretical_critic",
                "structuring_worker", "sandbox_worker"}


def _is_valid_json_response(text: str) -> bool:
    """Kiểm tra nhanh LLM có trả về JSON hợp lệ (dùng cho điểm UCB1)."""
    if not text:
        return False
    candidates = [text.strip()]
    cleaned = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    candidates.append(cleaned)
    for candidate in candidates:
        try:
            json.loads(candidate)
            return True
        except Exception:
            continue
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            json.loads(text[start:end + 1])
            return True
        except Exception:
            pass
    return False


class _Endpoint:
    def __init__(self, name: str, base_url: str, api_key: str, model: str, capabilities: List[str]):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.capabilities = capabilities  # tag năng lực: ["worker", "judge", ...]
        self.state = "ACTIVE"
        self.cooldown_until = 0.0

    def available(self) -> bool:
        if self.state == "ACTIVE":
            return True
        return time.time() >= self.cooldown_until

    def put_cooldown(self):
        self.state = "COOLDOWN"
        self.cooldown_until = time.time() + COOLDOWN_SECONDS


class SmartKeyPoolRouter:
    """
    Bộ định tuyến key trung tâm. Endpoint mở tải từ keys_pool.json (bị
    gitignore), có cấu trúc:
      [{"name": "gcp_p01", "base_url": "...", "api_key": "...",
        "model": "gemini-3.8-flash", "capabilities": ["worker", "judge"]}, ...]
    Nếu thiếu file: dựng 14 endpoint Gemini từ settings.GEMINI_API_KEYS
    và 3 endpoint khác từ settings.EXTRA_API_KEYS (định dạng "url|key|model").
    """

    def __init__(self):
        self.endpoints: List[_Endpoint] = []
        # UCB1: mu[(model, role)] = (tổng thành công, số lần), N_role = tổng số lần gọi role
        self._success: Dict[tuple, int] = {}
        self._trials: Dict[tuple, int] = {}
        self._role_calls: Dict[str, int] = {}
        self._judge_models_in_use: set = set()
        self._worker_models_in_use: set = set()
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=30.0))
        self._load_endpoints()

    # ---------- Nạp endpoint ----------
    def _load_endpoints(self):
        pool_file = "keys_pool.json"
        if os.path.exists(pool_file):
            try:
                with open(pool_file, "r", encoding="utf-8") as f:
                    entries = json.load(f)
                for e in entries:
                    self.endpoints.append(_Endpoint(
                        name=e.get("name", e.get("base_url", "")),
                        base_url=e.get("base_url", ""),
                        api_key=e.get("api_key", ""),
                        model=e.get("model", ""),
                        capabilities=e.get("capabilities", ["worker", "judge"]),
                    ))
                print(f"[KEY ROUTER] Nạp {len(self.endpoints)} endpoint từ keys_pool.json")
                return
            except Exception as e:
                print(f"[KEY ROUTER] Lỗi đọc keys_pool.json, chuyển sang .env: {e}")

        # 14 key Gemini (14 Google Cloud Project độc lập)
        gemini_keys = [k.strip() for k in settings.GEMINI_API_KEYS.split(",") if k.strip()]
        for idx, key in enumerate(gemini_keys, start=1):
            self.endpoints.append(_Endpoint(
                name=f"gcp_p{idx:02d}",
                base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                api_key=key,
                model="gemini-3.8-flash",
                capabilities=["worker", "judge"],
            ))
        # 3 API model khác — định dạng "base_url|api_key|model"
        extra = [k.strip() for k in settings.EXTRA_API_KEYS.split(",") if k.strip()]
        for idx, entry in enumerate(extra, start=1):
            parts = entry.split("|")
            if len(parts) == 3:
                self.endpoints.append(_Endpoint(
                    name=f"extra_api_{idx:02d}",
                    base_url=parts[0], api_key=parts[1], model=parts[2],
                    capabilities=["worker", "judge"],
                ))
        print(f"[KEY ROUTER] Nạp {len(self.endpoints)} endpoint từ .env")
        if not self.endpoints:
            print("[KEY ROUTER] CẢNH BÁO: Pool trống — cần keys_pool.json hoặc GEMINI_API_KEYS trong .env")

    # ---------- UCB1 [DT §6.2] ----------
    def _ucb1_score(self, model: str, role: str) -> float:
        n_mr = self._trials.get((model, role), 0)
        n_r = self._role_calls.get(role, 0)
        if n_mr == 0:
            return float("inf")  # đặc quyền thăm dò cho model chưa từng đảm nhiệm role
        mu = self._success.get((model, role), 0) / n_mr
        c = settings.UCB1_EXPLORATION_C
        return mu + c * math.sqrt(math.log(n_r + 1) / n_mr)

    def update_score(self, role: str, model: str, success: bool):
        """Cập nhật điểm UCB1: sinh JSON sai / bị ProbLog phạt -> thất bại."""
        key = (model, role)
        self._trials[key] = self._trials.get(key, 0) + 1
        self._success[key] = self._success.get(key, 0) + (1 if success else 0)
        self._role_calls[role] = self._role_calls.get(role, 0) + 1

    # ---------- Role Isolation Rule [DT §6.2] ----------
    def register_worker_model(self, model: str, role: str = "worker"):
        """Đánh dấu model của Worker vừa sinh hypothesis (để Judges né và ngược lại)."""
        if role in JUDGE_ROLES:
            self._judge_models_in_use.add(model)
        else:
            self._worker_models_in_use.add(model)

    def _isolation_fails(self, candidate: _Endpoint, role: str) -> bool:
        if role in JUDGE_ROLES:
            return candidate.model in self._worker_models_in_use      # Judge ≠ model Worker hiện tại
        if role in WORKER_ROLES:
            return candidate.model in self._judge_models_in_use       # Worker ≠ model Judge
        return False

    # ---------- Chọn + gọi ----------
    @staticmethod
    def _capability_for(role: str) -> str:
        """Ánh xạ role cụ thể sang tag năng lực của endpoint."""
        return "judge" if role in JUDGE_ROLES else "worker"

    def _pick_open_endpoint(self, role: str, preferred_model: Optional[str] = None) -> Optional[_Endpoint]:
        # Gỡ những endpoint đã hết hạn COOLDOWN
        for ep in self.endpoints:
            if ep.state == "COOLDOWN" and ep.available():
                ep.state = "ACTIVE"

        capability = self._capability_for(role)

        # 1a. Nếu chỉ định model ưu tiên (ví dụ Attempt 2 đổi model hoặc Judge cố định)
        if preferred_model:
            ordered = [e for e in self.endpoints
                       if e.model == preferred_model and e.available()
                       and capability in e.capabilities]
        else:
            # 1b. UCB1 ranking + Role Isolation + tag năng lực
            ordered = sorted(
                [e for e in self.endpoints
                 if capability in e.capabilities and e.available() and not self._isolation_fails(e, role)],
                key=lambda e: self._ucb1_score(e.model, role),
                reverse=True,
            )
        if not ordered:
            # Mọi endpoint đều bận COOLDOWN theo rule isolation -> thử không lọc isolation
            ordered = [e for e in self.endpoints
                       if e.available() and capability in e.capabilities]
        return ordered[0] if ordered else None

    async def call_worker(self, role: str, system_prompt: str, user_prompt: str,
                          preferred_model: Optional[str] = None, temperature: float = 0.4) -> str:
        """
        API thống nhất được stage pipeline (mã nguồn §10) sử dụng.
        Lỗi httpx.ReadTimeout / 429 / 503  ->  COOLDOWN 60s + hot-swap < 50ms.
        """
        last_err: Optional[Exception] = None
        attempts = 0
        # Chặn vòng lặp vô hạn khi mọi endpoint đều trả 4xx (401/400/...):
        # các mã này không kích hoạt COOLDOWN nên endpoint sẽ được chọn lại mãi.
        max_attempts = max(len(self.endpoints), 1) * 3
        while True:
            attempts += 1
            if attempts > max_attempts:
                raise RuntimeError(
                    f"KEY ROUTER: Đã thử {attempts - 1} lần cho role '{role}' "
                    f"nhưng toàn bộ endpoint đều lỗi. Lỗi cuối: {last_err}"
                )
            endpoint = self._pick_open_endpoint(role, preferred_model)
            if endpoint is None:
                raise RuntimeError(f"KEY ROUTER: Pool không còn endpoint khả dụng cho role '{role}'")
            swap_started = time.time()
            try:
                res = await self._http.post(
                    f"{endpoint.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {endpoint.api_key}",
                             "Content-Type": "application/json"},
                    json={
                        "model": endpoint.model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": temperature,
                    },
                )
                if res.status_code in (429, 503):
                    # Mã 429/503 (quá tải) -> đưa endpoint vào COOLDOWN
                    endpoint.put_cooldown()
                    self._fail_hot_swap(endpoint, role, f"HTTP {res.status_code}", swap_started)
                    continue
                res.raise_for_status()
                content = res.json()["choices"][0]["message"]["content"]
                # [DT §6.2] UCB1 cập nhật theo tỉ lệ sinh JSON hợp lệ, không chỉ HTTP 200
                self.update_score(role, endpoint.model, _is_valid_json_response(content))
                # Ghi nhận capability để áp Role Isolation cho các lần gọi sau
                self.register_worker_model(endpoint.model, role)
                return content
            except httpx.HTTPStatusError as e:
                code = e.response.status_code if e.response is not None else 0
                # Chỉ COOLDOWN cho 429/503 (đã xử lý ở trên) hoặc lỗi máy chủ;
                # các lỗi 4xx khác (400/401/403) không làm hỏng endpoint.
                if code >= 500:
                    endpoint.put_cooldown()
                self.update_score(role, endpoint.model, False)
                self._fail_hot_swap(endpoint, role, f"HTTP {code}", swap_started)
                last_err = e
            except httpx.ReadTimeout as e:
                endpoint.put_cooldown()
                self.update_score(role, endpoint.model, False)
                self._fail_hot_swap(endpoint, role, "httpx.ReadTimeout", swap_started)
                last_err = e
            except Exception as e:
                msg = str(e).lower()
                if any(h in msg for h in ERROR_STATE_HINTS):
                    endpoint.put_cooldown()
                self.update_score(role, endpoint.model, False)
                self._fail_hot_swap(endpoint, role, str(e)[:60], swap_started)
                last_err = e
        # vòng while sẽ tạm dừng khi pool cạn — raise nội bộ
        raise RuntimeError(f"Từ chối gọi role '{role}': {last_err}")

    def _fail_hot_swap(self, endpoint: _Endpoint, role: str, reason: str, swap_started: float):
        elapsed = (time.time() - swap_started) * 1000.0
        print(f"[KEY ROUTER][COOLDOWN] {endpoint.name} ({endpoint.model}) -> {reason}. "
              f"Đã hot-swap sau {elapsed:.1f}ms (COOLDOWN 60s).")

    async def close(self):
        await self._http.aclose()
