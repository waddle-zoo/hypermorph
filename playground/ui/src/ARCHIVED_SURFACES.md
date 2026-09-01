# Archived playground surfaces

The product MVP intentionally exposes five concerns: Live chat, Explore the Hive-Mind,
Review, admin Settings, and the login/auth endpoint. The former diagnostic views remain in
`main.jsx` and their focused component modules only because they are useful regression and
developer coverage; they have no primary navigation entry and legacy URLs redirect to Live chat.

Archived from the product shell:

- MCP setup (available inside admin Settings)
- Environment, Catalog, Discover candidates, Bundle resolver, Plan validation
- API console, Agent Builder, Agent Evaluator, Domain graph
- Home, Docs, Help, Profile, and the standalone Recent threads page
- Standalone write-back target navigation (available inside admin Settings)

Recent chats are intentionally embedded in Live chat. The archive is a product-surface
boundary, not a deletion of tested implementation: removing those modules outright would
discard coverage for the context, review, graph, and MCP contracts that power the MVP.
