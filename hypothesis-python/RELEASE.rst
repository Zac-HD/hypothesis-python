RELEASE_TYPE: patch

This patch improves the shrinking behaviour of :func:`~hypothesis.strategies.floats`
with two bounds (:issue:`1704`).  You may also notice small performance improvements
for 32-bit and 16-bit floats.
