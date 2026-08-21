# Axiom APL queries -- CloudAgent

Reference queries for the `cloudagent` dataset (Axiom -> Datasets -> `cloudagent`,
or Query view with the dataset picker set to `cloudagent`). Every log line
from both `backend` and `agent_loop` carries the same schema:

| field | meaning |
|---|---|
| `_time` | timestamp |
| `level` | `INFO` / `WARNING` / `ERROR` |
| `service` | `"backend"` or `"agent-loop"` |
| `logger` | the Python module that emitted it |
| `message` | the log message |
| `correlation_id` | the session's own `session_id` -- one id per whole transaction, shared by both services |
| `github_login` | who triggered it (`"-"` if nothing was bound, e.g. a background sweep tick) |
| `exception` | only present on `logger.exception(...)` calls -- the full traceback |
| `http_method` / `http_path` | only present on the one "request received" entry line per request |

Swap the placeholder values (`<...>`) before running.

---

## 1. Everything for one transaction (the main one)

Every log line -- both services, entry + every error -- for a single session/transaction, in order. This is the query you reach for first: paste in the `session_id` from the frontend URL or your bug report.

```kusto
['cloudagent']
| where correlation_id == "<session_id>"
| order by _time asc
```

## 2. All errors, last 24h, most recent first

```kusto
['cloudagent']
| where _time > ago(1d) and level == "ERROR"
| project _time, service, correlation_id, github_login, message, exception
| order by _time desc
```

## 3. Errors *and* warnings, last 24h

Widen #2 to catch the LLM retry warnings too (a session that recovered after a retry, not just ones that outright failed).

```kusto
['cloudagent']
| where _time > ago(1d) and level in ("ERROR", "WARNING")
| order by _time desc
```

## 4. Tail the most recent errors overall (quick triage, no time filter)

```kusto
['cloudagent']
| where level == "ERROR"
| order by _time desc
| take 50
```

## 5. Everything one user has triggered

```kusto
['cloudagent']
| where github_login == "<github-username>"
| order by _time desc
```

## 6. Which sessions actually had an error (jump from "something broke" to *which* transaction)

```kusto
['cloudagent']
| where _time > ago(7d) and level == "ERROR"
| summarize error_count = count() by correlation_id, github_login
| order by error_count desc
```

## 7. Who's using it, how much (traffic by user)

Based on the single "request received" line logged once per incoming request.

```kusto
['cloudagent']
| where message == "request received"
| summarize requests = count() by github_login
| order by requests desc
```

## 8. Traffic + error volume over time, bucketed

Good for a dashboard panel -- shows request/error rate trending, not just a snapshot.

```kusto
['cloudagent']
| where _time > ago(1d)
| summarize count() by bin_auto(_time), level
```

## 9. Recurring errors -- same failure happening repeatedly

Groups by message text so one flaky thing showing up 40 times doesn't get lost among one-off errors.

```kusto
['cloudagent']
| where _time > ago(7d) and level == "ERROR"
| summarize occurrences = count() by message
| order by occurrences desc
| take 20
```

## 10. LLM retry visibility

Surfaces the retry-with-backoff logging built into `agent_loop/app/llm/openai_compatible.py` (429/5xx and transport-error retries) -- lets you see how often the LLM provider is actually flaking, even for sessions that ultimately succeeded.

```kusto
['cloudagent']
| where message has "retrying"
| order by _time desc
```

## 11. Sanity-check: is cross-service propagation actually working?

A correlation_id that only ever shows `service == "backend"` and never `"agent-loop"` (or vice versa) means the header propagation broke somewhere -- this should normally return every active session, since almost all of them touch both services.

```kusto
['cloudagent']
| where correlation_id != "-"
| summarize services_seen = dcount(service) by correlation_id
| where services_seen > 1
```
