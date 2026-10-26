# Proposal

## Why

Оценка мэтча говорит, насколько резюме подходит, но не что в нём поменять. Чаще всего
опыт у кандидата есть, просто он назван иначе или спрятан. Нужны конкретные правки
под вакансию — без приписывания навыков, которых нет. Плюс резюме обычно лежит в PDF или
DOCX, а форма принимает только вставленный текст.

## What Changes

- Новый вид LLM-задачи `resume_tailor` по шаблону писем: `POST/GET/DELETE
  /resumes/{id}/tailor/{vacancy_id}`, кэш по паре в `resume_tailorings` (миграция 041),
  `stale` по модели писем и `RESUME_TAILOR_PROMPT_VERSION`, общие квота, лимит и отмена.
  Ответ: вывод, до 8 правок (раздел, «было», «стало», требование, приоритет), термины
  вакансии, которые резюме подтверждает, и «не добавлять без реального опыта». Промпт
  запрещает выдумывать; недостающие навыки сохранённой оценки передаются как запретные.
- `POST /resumes/parse-file` — текст из PDF (`pypdf`) или DOCX (stdlib), тело — сам файл,
  до 5 МБ; формат по сигнатуре; текст не сохраняется.
- Правка текста резюме сбрасывает и его адаптации; `db import --mode replace` учитывает
  их в предупреждении о потерях.
- UI: блок «Адаптация резюме» в карточке вакансии, «Загрузить из файла» в форме резюме.

## Capabilities

### New Capabilities

- `career/resume-tailoring` — адаптация резюме под вакансию.

### Modified Capabilities

- `career/resumes` — текст резюме из файла.

## Impact

Миграция `041_resume_tailorings`; зависимость `pypdf` (poetry.lock);
`tg_jobs_parser/ai/{schema,prompts}.py`, `ai/clients/openrouter.py`,
`tg_jobs_parser/utils/resume_file.py`; backend — модель, репозиторий, сервис, воркер,
роуты `resumes.py` и `llm_jobs.py`, `transfer.py`; frontend — `ResumeTailoringPanel`,
`VacancyModal`, `ResumesPage`.
