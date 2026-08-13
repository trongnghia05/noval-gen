"""Key/value application settings. Two endpoints, one whitelist."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...db.models import AppSetting
from ...db.session import SessionLocal

router = APIRouter()




class SettingRequest(BaseModel):
    value: str = ""



_SETTING_KEYS = {"cms_upload_url"}



@router.get("/settings/{key}")
def get_setting(key: str):
    if key not in _SETTING_KEYS:
        raise HTTPException(404, "setting not found")
    with SessionLocal() as session:
        setting = session.get(AppSetting, key)
        return {"key": key, "value": setting.value if setting else ""}



@router.put("/settings/{key}")
def put_setting(key: str, req: SettingRequest):
    if key not in _SETTING_KEYS:
        raise HTTPException(404, "setting not found")
    with SessionLocal() as session:
        setting = session.get(AppSetting, key)
        if setting is None:
            setting = AppSetting(key=key)
            session.add(setting)
        setting.value = req.value.strip()
        session.commit()
        return {"key": key, "value": setting.value}
