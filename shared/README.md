# shared/

Files used by both the backend and the frontend.

## llm/

One prompt and an optional JSON schema per LLM call, named after the call's `name`:

- `<name>.md`: system prompt (required)
- `<name>.json`: response schema (optional; without it the call returns plain text)

The backend (`backend/llm_calls`) reads these at runtime and serves each call at
`POST /llm/calls/<name>`. The frontend imports schemas at build time:

```js
import roomSchema from '@shared/llm/describe_room.json'
```

Backend edits take effect on restart; schema changes need a frontend rebuild.
`deploy.sh` copies this folder to the server, and `.htaccess` blocks web access
to it.
