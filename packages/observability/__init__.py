"""Observability and reliability building blocks (M16).

  context     the request id carried through every log line, downstream call and background task
  logs        JSON logging that never carries health content
  middleware  one request-scoped wrapper: request id, timing, an access log line, metrics
  metrics     in-process latency metrics per stage
  trace       what one assistant call did, kept as metadata only (no question, answer or evidence)
  resilience  the timeout and retry policy of every outside dependency, and the retry helper

Rule for everything here and everything that logs through it: metadata only. Ids, counts, durations,
status and error class are fine; a question, an answer, a quote, a claim, a filename or any field
of a health record is not.
"""
