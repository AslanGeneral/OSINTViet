from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader
from app.core.config import settings

api_key_header = APIKeyHeader(name="X-Admin-Passcode", auto_error=False)

async def verify_admin(passcode: str = Security(api_key_header)):
    """Chặn họng các request không có passcode Admin hợp lệ."""
    if not passcode or passcode != settings.ADMIN_PASSCODE:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="WTF bro, mày không có quyền admin!",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return True