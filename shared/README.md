# shared/

Files used by both the backend and the frontend.

## llm/

Definitions for each LLM call are named after the call's `name`:

- `<name>.md`: system prompt (required)
- `<name>.input.md`: user-message template (optional; use `{{input}}` once for
  the call's input)
- `<name>.json`: response schema (optional; without it the call returns plain text)

The backend (`backend/llm_calls` and `backend/harness`) reads these at runtime
and serves each call at `POST /llm/calls/<name>`. A frontend feature that needs
a schema can import it at build time:

```js
import narratorSchema from '@shared/llm/game_narrator.json'
```

The backend caches definitions per call class for the life of the process.
Definition edits therefore require a backend process reload to take effect;
new CGI processes read the current definitions. If a schema is imported by the
frontend, its bundled copy requires a frontend rebuild.
`deploy.sh` copies this folder to the server, and `.htaccess` blocks web access
to it.
