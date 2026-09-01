# `@hyperset/chat-ui`

Reusable Hyperset chat surface for applications that want a governed, observable
conversation without hosting the playground shell.

The package owns the message stream, Markdown rendering, execution stages,
ContextBundle observability, SQL result display, clear-chat behavior, and the
Agent/Model composer controls. The host supplies the backend URL and runtime
configuration:

```jsx
import { HypersetChat } from "@hyperset/chat-ui";
import "@hyperset/chat-ui/styles.css";

<HypersetChat
  apiRoot="https://hyperset.example.com/playground/api"
  agent={agent}
  model={model}
  agents={agents}
  models={models}
  backendHealthy={backendHealthy}
  setAgent={setAgent}
  setModel={setModel}
/>
```

The backend is a connection target, not a UI host requirement. The playground
uses the same component with `apiRoot="/playground/api"`.
