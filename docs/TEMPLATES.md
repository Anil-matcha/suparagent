# Templates

Templates are ready-made starting points for new agents. Open **Agents → Templates…** to browse a bundled list of roles, filter by category (Operations, Growth, Sales, Marketing, Support, Web) and search by name, role, description or category.

## What choosing one does

**Add** only fills the new-agent form: name, title, description, instructions and optional starter notes. Every field can be edited. Nothing is created until you press **Save**, and **Cancel** returns to the gallery with nothing written. A template cannot choose your engine, model, workspace folder or approval mode, enable a tool, connect an account or schedule anything: those stay your choices, with the usual defaults.

After Save the new agent is selected, ready to chat. Adding the same template again names the next copy `Name 2`, `Name 3` and so on; copies are independent.

## Suggested skills

Some templates suggest skills (text instructions the agent can be asked to follow). They are listed with a checkbox in the form and saved on the new agent after it is created. If saving a skill fails, or this version has no skills library, the agent stays and a notice names what was not added.

## Workers

The bundled templates are eight **workers**: Operations analyst, GTM engineer, Sales, SEO, Social media, Ad manager, Customer support and Web developer. Every worker can use MuAPI's read-only data tools (and Social media and Ad manager also the capped picture tools) for paid lookups under the workspace spend limits; without MuAPI each does its free steps and says what needs MuAPI. Customer support also reads your support inbox and Web developer reads your own sites without MuAPI. Each card also shows its **starter routines (paused)** and a **setup depth** (Express, Standard or Guided). These are descriptions only: choosing a worker fills the form like any other template and creates no routine, schedules nothing and grants no tool. A worker's instructions are its short role text followed once by shared run rules (drafts only, a fixed order for paid lookups, one run record per run). Suggested MuAPI tools are named for you to grant later; they are never granted by choosing a template.

## Roles that need a connection

A template built around a connection this app does not have yet (email, community feeds) is shown with "Needs … · not available yet" and its Add button is disabled. Roles that only need Muapi can still be added before it is connected.

A role built around a connection you can set up in this app (Muapi) can be added before the connection exists. It shows "Needs Muapi · Connect" until the Muapi listing is installed from the marketplace and its token is saved; Connect opens the marketplace. Adding the role grants nothing and calls nothing, and the role cannot use Muapi tools until you grant them to the agent. `GET /templates` reports each template's `missing` connections for this workspace; the check is local and makes no Muapi request.

## Where the catalog lives

`backend/app/templates/catalog.py` (bundled, versioned by `CATALOG_VERSION`) served read-only by `GET /api/v1/templates` (`q`, `category`) and `GET /api/v1/templates/{id}`. Browsing makes no model call and no network request. Local access is required, like the rest of the agent API.

## Not included yet

Saved presets, a public or organisation catalog, team packages, importing from GitHub, and creating a team from a project folder.

## Adding several at once

Setup has a "Choose your team" step, and Agents → Templates has "Hire several". Setup shows up to eight workers under the lead form (when the lead leads a team); Hire several lists every bundled worker by category with its setup depth and any connection it needs, and let the person tick none, some or all (up to eight). Adding calls `POST /templates/hire` once with template ids only: every agent, skill, note and paused routine is created together, or none are. Workers ask first, hold no connected-app access and have no schedule.

**Help me choose.** The Choose your team screen has a Help me choose button with three questions: what you want help with (a short list), what the business does (optional) and whether to show only workers that start with no questions. The answer ticks matching workers for you to change. Ranking uses only the bundled catalog (`backend/app/templates/suggest.py`): no model call, no network, nothing created, nothing granted. The team lead has a read-only `suggest_workers` tool with the same ranking. The first version asks its questions on the screen; a lead interview in chat that fills in the ticks is not built.

**Chat with your team lead instead.** Next to Help me choose, this opens your team lead in a new tab with a short interview already typed in (an unsent message you already had is kept). When the lead calls `suggest_workers`, the server remembers only the latest answer: template ids and short reasons, no chat text, up to 8, for 24 hours (`GET /templates/suggest/latest`). The screen checks for a suggestion newer than the one that existed when the chat was opened and offers **Use these suggestions**, which ticks exactly those workers (only ones that can be added now). You still confirm with Add; chatting creates and grants nothing. If no team lead is set up, the screen says so and the three questions remain.

`POST /templates/hire/undo` takes the agent ids from the receipt and removes them only if every one is still exactly as added: not edited, no conversation, and every routine still paused and never run. Otherwise nothing is removed and the error names the agent that blocked it.

## Business profile

Workers start from one shared business profile: what the business sells, who it is for, the tone to write in, links, hours and things never to say. The person writes it once (after adding workers in Choose your team, or later in Memory → Business profile). It is stored beside About me, outside every agent's folder, and goes into every turn's system prompt as background that grants no permission. Agents cannot change it; a worker that wants a change says so in its notes for the owner. Text that looks like a key, token or password is refused, not stored. Each field has a size limit. Proposed drafts from workers that the owner accepts are not built yet.

A worker cannot edit the profile. To suggest a change it calls `suggest_profile_change` (one field, the complete new text and a short reason); the suggestion waits in the Business profile tab of the memory panel until the person accepts or dismisses it. Accepting saves through the profile's own size, secret and conflict checks, and asks before replacing a field that changed after the suggestion was made. Up to 10 suggestions wait at once (a full queue refuses new ones, and a worker has one per field), text that looks like a key, token or password is refused, and dismissed text is not suggested again.
