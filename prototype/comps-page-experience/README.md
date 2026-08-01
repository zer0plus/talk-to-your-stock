# Comps Page experience prototype

> **PROTOTYPE — disposable, fixture-only, and not production frontend code.**

Three desktop concepts for the Comps Page and Product Shell on one route:

- `?variant=A` — Guided workspace
- `?variant=B` — Research desk
- `?variant=C` — Narrative canvas

All three use the same clarified product model: a persistent side chat is the
only request surface, while the main canvas is reserved for the Comps Table and
other dynamically selected analysis artifacts. The User names the Target and
Peer Tickers naturally in a Message rather than filling out structured fields.

The floating prototype switcher changes the variant and previews first arrival,
waiting, success, recoverable input error, and failed Run states. The concepts
use local fixtures shaped like the implemented Web BFF responses and make no
network request to the Web BFF.

## Start

From the repository root:

```sh
npm --prefix prototype/comps-page-experience run dev
```

Then open <http://localhost:3000/?variant=A>.
