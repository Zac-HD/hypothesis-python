RELEASE_TYPE: patch

This patch improves :func:`~hypothesis.strategies.one_of`\ 's
deduplication logic, which can make both generation and shrinking
somewhat more efficient (:issue:`2291`).
