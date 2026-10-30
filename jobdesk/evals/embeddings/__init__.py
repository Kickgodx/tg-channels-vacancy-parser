"""Eval моделей эмбеддингов на данных проекта: поиск, похожие, дубли, подбор под резюме.

Набор данных выгружает ``scripts/build_embedding_eval.py`` (только чтение основной БД) в
``evals/embeddings/data`` — каталог в .gitignore: там тексты вакансий и резюме. Запуск — ``python -m
jobdesk.evals.embeddings --help``.
"""
