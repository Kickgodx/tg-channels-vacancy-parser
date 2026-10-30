"""Запуск MCP-сервера по stdio: ``python -m jobdesk_mcp`` (или ``jobdesk-mcp``).

Env: ``JOBDESK_TOKEN`` (персональный токен со страницы «Токены»), ``JOBDESK_API_URL``
(по умолчанию http://127.0.0.1:9000/api/v1), ``JOBDESK_API_KEY`` (если у API задан API_KEY),
``JOBDESK_LLM_WAIT_SECONDS`` (сколько ждать LLM-задачу, по умолчанию 60).
Прежние имена ``TGJOBS_*`` тоже читаются (если ``JOBDESK_*`` не задана).
"""

from __future__ import annotations

import sys

import anyio


def main() -> None:
    from jobdesk_mcp.client import ApiClient, Settings
    from jobdesk_mcp.server import build_server

    try:
        settings = Settings.from_env()
    except ValueError as e:
        # stdout занят протоколом MCP — диагностика только в stderr.
        print(f"jobdesk-mcp: {e}", file=sys.stderr)
        sys.exit(2)
    if not settings.token.startswith("tgj_"):
        print("jobdesk-mcp: JOBDESK_TOKEN не похож на персональный токен (tgj_…)", file=sys.stderr)

    async def run() -> None:
        client = ApiClient(settings)
        try:
            await build_server(client).run_stdio_async()
        finally:
            await client.aclose()

    anyio.run(run)


if __name__ == "__main__":
    main()
