## Project 2 · CPU interface check

Commit: `e891241e1fa690d18e8ab57a2bbbf2309b1ca520`

Diagnostic only · GPU not used · formal score unchanged

| Check | Interface | Result |
| --- | --- | --- |
| fixture | GET /__fixture/state | pass |
| health | GET /healthz | pass |
| auth | GET /v1/models /info /v1/usage | pass |
| models | GET /v1/models | pass |
| info | GET /info | pass |
| metrics | GET /metrics | pass |
| agent_request_validation | POST /agent/run | pass |
| nonstream | POST /v1/chat/completions | pass |
| stream | POST /v1/chat/completions | pass |
| console | GET /console | pass |
| pricing | PUT /admin/pricing | pass |
| tenant | POST /admin/tenants | pass |
| issue | POST /admin/tenants/{tenant_id}/keys | pass |
| billing | GET /v1/usage | pass |
| admin_isolation | MULTI /admin/ | pass |
| rotate | POST /admin/keys/{key_id}/rotate | pass |
| revoke | POST /admin/keys/{key_id}/revoke | pass |
| disable | PATCH /admin/tenants/{tenant_id} | pass |
