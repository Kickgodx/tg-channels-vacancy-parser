"""«Спросить базу»: вопрос о рынке вакансий → фоновая LLM-задача ``ask_market``."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from backend.api.deps import CurrentUser, get_current_user, verify_api_key
from backend.errors import CODE_LLM_QUOTA_EXCEEDED, CODE_RATE_LIMITED, http_error
from backend.schemas import AskMarketRequest, LlmJobAccepted
from backend.services.llm_job_service import (
    KIND_ASK_MARKET,
    LlmDailyQuotaError,
    LlmJobUserLimitError,
    llm_job_runner,
)

router = APIRouter(prefix="/market-chat", tags=["market-chat"], dependencies=[Depends(verify_api_key)])


@router.post("/ask", status_code=202, response_model=LlmJobAccepted)
def ask_market(body: AskMarketRequest, user: CurrentUser = Depends(get_current_user)):
    """Вопрос о рынке: план по каталогу → цифры → ответ модели только по ним. Итог — в ``/llm-jobs/{id}``.

    Один вызов суточной квоты на вопрос; история (до 3 прошлых вопросов с фильтрами) — от UI.
    """
    try:
        result = llm_job_runner.enqueue(
            user_id=user.id,
            kind=KIND_ASK_MARKET,
            params=body.model_dump(mode="json"),
        )
    except LlmJobUserLimitError as e:
        raise http_error(429, CODE_RATE_LIMITED, str(e), retryable=True) from e
    except LlmDailyQuotaError as e:
        raise http_error(429, CODE_LLM_QUOTA_EXCEEDED, str(e), retryable=False) from e
    accepted = LlmJobAccepted(job_id=result.job_id, status="queued")
    return JSONResponse(status_code=202, content=accepted.model_dump(mode="json"))
