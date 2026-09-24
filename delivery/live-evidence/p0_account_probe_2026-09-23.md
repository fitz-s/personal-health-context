# P0 account/client probe — 2026-09-23 (read-only, no content sent)

Method: Chrome (user's signed-in profile), read-only page reads; nothing clicked that changes settings.

| Item | Observation | Evidence |
|---|---|---|
| ChatGPT plan | Sidebar/account badge shows "Pro"; model picker "6 Pro". | page text read of chatgpt.com |
| Developer mode | Settings → Security and login → Developer mode switch is ON ("Allows you to add unverified connectors that could modify or erase data permanently"). | chatgpt_developer_mode_on_2026-09-23.png |
| Existing tunnel-backed app | Plugins list contains "WebCodex Demo"; Platform → Organization "Personal" → Tunnels lists tunnel `webcodex-demo-mac` (belongs to another project; NOT used or modified). This shows the account can create tunnels and developer-mode apps, but it is not a phctx test. | Platform tunnels page text |
| Platform org | Personal organization; banner "Add credits — Run your next API request by adding credits" ⇒ no prepaid API credit visible, so the Responses-API background path is not available without a paid change. | Platform page text |
| Plugin permission setting | "Allow low-risk tools" (host may still confirm write tools). | Plugins settings page |
| phctx tunnel / app | NOT created (account change needs the user's approval + a runtime API key only the user should create). | — |

## Oura official access probe (2026-09-23)
| Check | Result |
|---|---|
| Oura API agreement (cloud.ouraring.com/legal/api-agreement) | Effective June 8, 2026. 3(m) "shall not store or cache any data"; 4(a) "solely for the Authorized Purposes"; 4(d) "solely through the MCP Server". Agreement describes an Oura-operated MCP Server but gives no URL/access procedure. |
| ChatGPT plugin directory search "Oura" (chatgpt.com/plugins?search=Oura) | Only an unrelated app ("AllXAi — Chartes, transits, entourage") returned; no Oura plugin/connector. |
| Web search (official domains) | No official Oura MCP endpoint/enablement page found. |
| Conclusion | `oura_official_mcp`: BLOCKED (no official route available to this account). Oura stays `not_connected`, nothing persisted. |
| Note | ChatGPT shows an installed first-party "Health" plugin ("Explore your health data in ChatGPT"); it is separate from this system and not used by it. |
