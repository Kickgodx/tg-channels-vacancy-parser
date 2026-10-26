from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, get_db, verify_api_key
from backend.db.company_profile import company_profile
from backend.schemas import CompanyProfile

router = APIRouter(
    prefix="/companies",
    tags=["companies"],
    dependencies=[Depends(verify_api_key), Depends(get_current_user)],
)


@router.get("/profile", response_model=CompanyProfile)
def get_company_profile(
    name: str = Query(min_length=1, max_length=256, description="Название в любом написании."),
    db: Session = Depends(get_db),
) -> CompanyProfile:
    """Профиль компании: все её вакансии (с архивом), роли, зарплаты, каналы, последние вакансии."""
    profile = company_profile(db, name)
    if profile is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return profile
