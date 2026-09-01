import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { redactDeep } from "@hyperset/chat-ui";

// The Hive-Mind explorer is deliberately small: one graph, one selected-node
// inspector. The server supplies the governed walk; the UI only adds the
// document/source nodes that are already named by each domain's pointers so a
// human can follow the lineage without opening a second surface.

const EXCLUSION_LABELS = {
  acl: "not visible to you",
  disabled: "disabled",
  unsynced: "never synced",
};

const NODE_WIDTH = 196;
const NODE_HEIGHT = 72;
const COLUMN_GAP = 260;
const ROW_GAP = 104;
const TOP_INSET = 54;
const MAX_ROWS_PER_LANE = 6;
const LANE_GAP = 48;
const DEPTH_GAP = 64;
const ZOOM_MIN = 0.5;
const ZOOM_MAX = 1.25;
const ZOOM_STEP = 0.1;

// Keep the browser walk useful on a large estate. The API returns an explicit
// warning when one of these caps hides part of the reachable graph; the UI
// surfaces that warning instead of pretending the visible slice is complete.
export const ROOT_WALK_LIMITS = {
  max_hops: 4,
  max_components: 50,
  context_budget: 24000,
};

function slug(nodeId) {
  return typeof nodeId === "string" && nodeId.startsWith("domain:") ? nodeId.slice(7) : nodeId;
}

function sourceLabel(source) {
  if (typeof source === "string") return source;
  if (!source || typeof source !== "object") return "approved source";
  return source.ref || source.name || source.id || "approved source";
}

function nodeKind(id) {
  if (id === "root:default" || String(id).startsWith("root:")) return "root";
  if (String(id).startsWith("domain:")) return "domain";
  if (String(id).startsWith("document:")) return "document";
  if (String(id).startsWith("source:")) return "source";
  return "node";
}

function kindLabel(kind) {
  return {
    root: "Hive-Mind root",
    domain: "Domain",
    document: "Context document",
    source: "Approved source",
    unavailable: "Unavailable domain",
    node: "Graph node",
  }[kind] || "Graph node";
}

function displayLabel(id) {
  if (String(id).startsWith("domain:")) return slug(id);
  if (String(id).startsWith("document:")) return String(id).slice("document:".length);
  if (String(id).startsWith("source:")) return String(id).slice("source:".length);
  return id;
}

function asText(value, fallback = "Not disclosed") {
  if (value === null || value === undefined || value === "") return fallback;
  return typeof value === "string" ? value : JSON.stringify(value);
}

function addDetail(details, label, value) {
  if (value !== null && value !== undefined && value !== "") details.push({ label, value: asText(value) });
}

function graphData(walk) {
  const nodeMap = new Map();
  const edgeMap = new Map();
  const addNode = (node) => {
    if (!node?.id) return;
    const existing = nodeMap.get(node.id);
    if (existing) {
      nodeMap.set(node.id, { ...existing, ...node, details: node.details || existing.details });
      return;
    }
    nodeMap.set(node.id, node);
  };
  const addEdge = (edge) => {
    if (!edge?.from || !edge?.to) return;
    const relation = edge.relation || "related";
    const attribution = JSON.stringify([edge.evidence ?? null, edge.provenance ?? null]);
    const key = `${edge.from}|${edge.to}|${relation}|${attribution}`;
    if (!edgeMap.has(key)) edgeMap.set(key, { ...edge, relation });
  };

  const root = walk?.root || { id: "root:default", workspace: "default" };
  addNode({
    id: root.id || "root:default",
    kind: "root",
    label: "Hive-Mind root",
    subtitle: `workspace: ${root.workspace || "default"}`,
    details: [
      { label: "Workspace", value: root.workspace || "default" },
      { label: "Role", value: "Entry point for governed context" },
    ],
  });

  for (const edge of walk?.edges || []) {
    if (edge.from && !nodeMap.has(edge.from)) {
      addNode({ id: edge.from, kind: nodeKind(edge.from), label: displayLabel(edge.from), subtitle: kindLabel(nodeKind(edge.from)) });
    }
    if (edge.to && !nodeMap.has(edge.to)) {
      addNode({ id: edge.to, kind: nodeKind(edge.to), label: displayLabel(edge.to), subtitle: kindLabel(nodeKind(edge.to)) });
    }
    addEdge(edge);
  }

  const domainEntries = Array.isArray(walk?.domains) ? walk.domains : [];
  for (const entry of domainEntries) {
    if (!entry?.domain) continue;
    const domainId = `domain:${entry.domain}`;
    const pointers = entry.pointers || {};
    const available = entry.available !== false;
    const details = [];
    addDetail(details, "Status", available ? "Visible" : EXCLUSION_LABELS[entry.exclusion] || entry.exclusion || "Unavailable");
    addDetail(details, "Domain", entry.domain);
    addDetail(details, "Repository", pointers.repository);
    addDetail(details, "Context document", pointers.context_doc);
    addDetail(details, "Snapshot", pointers.snapshot_id);
    addDetail(details, "Serving commit", pointers.commit_sha);
    if (Array.isArray(pointers.approved_sources)) addDetail(details, "Approved sources", pointers.approved_sources.length);
    if (!available) addDetail(details, "Reason", entry.reason);
    addNode({
      id: domainId,
      kind: available ? "domain" : "unavailable",
      label: entry.domain,
      subtitle: available ? entry.title || "Governed domain" : EXCLUSION_LABELS[entry.exclusion] || "Unavailable",
      details,
    });

    if (!available) continue;
    const sourceId = pointers.source_id || `${entry.domain}:${pointers.context_doc || "context"}`;
    const documentId = `document:${sourceId}`;
    addNode({
      id: documentId,
      kind: "document",
      label: pointers.context_doc || "context document",
      subtitle: entry.domain,
      details: [
        { label: "Domain", value: entry.domain },
        { label: "Source id", value: sourceId },
        { label: "Repository", value: pointers.repository || "Not disclosed" },
        { label: "Path", value: pointers.context_doc || "Not disclosed" },
        { label: "Snapshot", value: pointers.snapshot_id || "Not disclosed" },
        { label: "Serving commit", value: pointers.commit_sha || "Not disclosed" },
      ],
    });
    addEdge({ from: domainId, to: documentId, relation: "context_doc", provenance: "domain pointer" });

    for (const source of pointers.approved_sources || []) {
      const label = sourceLabel(source);
      const sourceNodeId = `source:${label}`;
      addNode({
        id: sourceNodeId,
        kind: "source",
        label,
        subtitle: entry.domain,
        details: [
          { label: "Domain", value: entry.domain },
          { label: "Source", value: label },
          { label: "Role", value: typeof source === "object" ? source.role || "Approved source" : "Approved source" },
        ],
      });
      addEdge({ from: domainId, to: sourceNodeId, relation: "approved_source", provenance: "domain pointer" });
    }
  }

  // The root is the visible entry point. If a server response lists a domain
  // without an explicit root edge, surface it as a top-level node rather than
  // leaving it stranded outside the graph. Child domains still remain under
  // their explicit parent edge.
  const incomingDomainEdges = new Set(
    [...edgeMap.values()]
      .filter((edge) => String(edge.from).startsWith("domain:") && String(edge.to).startsWith("domain:"))
      .map((edge) => edge.to),
  );
  const rootId = root.id || "root:default";
  for (const entry of domainEntries) {
    if (!entry?.domain) continue;
    const domainId = `domain:${entry.domain}`;
    const hasRootEdge = [...edgeMap.values()].some((edge) => edge.from === rootId && edge.to === domainId);
    if (!hasRootEdge && !incomingDomainEdges.has(domainId)) {
      addEdge({
        from: rootId,
        to: domainId,
        relation: entry.available === false ? "catalog_hidden" : "catalog_contains",
        provenance: "navigation fallback",
      });
    }
  }

  const nodes = [...nodeMap.values()];
  const edges = [...edgeMap.values()];
  const depths = new Map([[rootId, 0]]);
  const queue = [rootId];
  while (queue.length) {
    const current = queue.shift();
    const currentDepth = depths.get(current) || 0;
    for (const edge of edges.filter((candidate) => candidate.from === current)) {
      if (!depths.has(edge.to)) {
        depths.set(edge.to, currentDepth + 1);
        queue.push(edge.to);
      }
    }
  }
  for (const node of nodes) {
    if (!depths.has(node.id)) depths.set(node.id, node.kind === "domain" || node.kind === "unavailable" ? 1 : 2);
  }

  const columns = new Map();
  for (const node of nodes) {
    const depth = depths.get(node.id);
    if (!columns.has(depth)) columns.set(depth, []);
    columns.get(depth).push(node);
  }
  const positions = new Map();
  let nextDepthX = 56;
  let rightEdge = 0;
  let visibleRows = 1;
  for (const depth of [...columns.keys()].sort((a, b) => a - b)) {
    const column = columns.get(depth);
    const laneCount = Math.max(1, Math.ceil(column.length / MAX_ROWS_PER_LANE));
    visibleRows = Math.max(visibleRows, Math.min(column.length, MAX_ROWS_PER_LANE));
    column.forEach((node, index) => {
      const lane = Math.floor(index / MAX_ROWS_PER_LANE);
      const row = index % MAX_ROWS_PER_LANE;
      positions.set(node.id, {
        x: nextDepthX + lane * (NODE_WIDTH + LANE_GAP),
        y: TOP_INSET + row * ROW_GAP,
      });
    });
    const depthWidth = laneCount * NODE_WIDTH + (laneCount - 1) * LANE_GAP;
    rightEdge = nextDepthX + depthWidth;
    nextDepthX = rightEdge + DEPTH_GAP;
  }

  const rootPosition = positions.get(rootId);
  if (rootPosition) {
    const rootChildren = edges.filter((edge) => edge.from === rootId).length;
    const centeredRootOffset = Math.min(2.5, Math.max(0, (rootChildren - 1) / 2));
    positions.set(rootId, {
      ...rootPosition,
      y: TOP_INSET + centeredRootOffset * ROW_GAP,
    });
  }

  return {
    nodes: nodes.map((node) => ({ ...node, ...(positions.get(node.id) || {}) })),
    edges: edges.map((edge) => ({
      ...edge,
      fromPosition: positions.get(edge.from),
      toPosition: positions.get(edge.to),
    })).filter((edge) => edge.fromPosition && edge.toPosition),
    width: Math.max(1060, rightEdge + 56),
    height: Math.max(500, TOP_INSET * 2 + (visibleRows - 1) * ROW_GAP + NODE_HEIGHT),
  };
}

function edgePath(edge) {
  const startX = edge.fromPosition.x + NODE_WIDTH;
  const startY = edge.fromPosition.y + NODE_HEIGHT / 2;
  const endX = edge.toPosition.x;
  const endY = edge.toPosition.y + NODE_HEIGHT / 2;
  const bend = Math.max(42, Math.abs(endX - startX) * 0.42);
  return `M ${startX} ${startY} C ${startX + bend} ${startY}, ${endX - bend} ${endY}, ${endX} ${endY}`;
}

function NodeInspector({ node, edges, nodesById }) {
  if (!node) {
    return (
      <aside className="hive-node-inspector" aria-label="Selected node details">
        <div className="hive-inspector-empty">
          <span className="hive-inspector-placeholder" aria-hidden="true">⌁</span>
          <span>Select a node</span>
          <small>Click any node in the graph to inspect its lineage and governed metadata.</small>
        </div>
      </aside>
    );
  }
  const related = edges.filter((edge) => edge.from === node.id || edge.to === node.id);
  return (
    <aside className="hive-node-inspector" aria-label="Selected node details">
      <div className="hive-inspector-heading">
        <span className="hive-node-kind">{kindLabel(node.kind)}</span>
        <span className="hive-inspector-node-id">{node.id}</span>
      </div>
      <h2>{node.label}</h2>
      <p className="hive-inspector-subtitle">{node.subtitle}</p>
      <dl className="hive-node-details">
        {(node.details || []).map((detail) => (
          <React.Fragment key={detail.label}>
            <dt>{detail.label}</dt>
            <dd>{detail.value}</dd>
          </React.Fragment>
        ))}
      </dl>
      <div className="hive-connections">
        <div className="hive-connections-heading">
          <span>Connections</span>
          <small>{related.length}</small>
        </div>
        {related.length ? (
          <ul>
            {related.map((edge) => {
              const otherId = edge.from === node.id ? edge.to : edge.from;
              const attribution = edge.evidence !== undefined
                ? `evidence: ${asText(edge.evidence)}`
                : edge.provenance !== undefined ? `provenance: ${asText(edge.provenance)}` : "";
              return <li key={`${edge.from}-${edge.to}-${edge.relation}-${attribution}`}><span>{edge.relation}{attribution && ` · ${attribution}`}</span><b>{nodesById.get(otherId)?.label || displayLabel(otherId)}</b></li>;
            })}
          </ul>
        ) : <p>No connected nodes.</p>}
      </div>
    </aside>
  );
}

export function HiveMindGraph({ requestJson }) {
  const [walk, setWalk] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const nodeRefs = useRef(new Map());
  const viewportRef = useRef(null);

  const loadRoot = useCallback(() => {
    setBusy(true);
    setError("");
    requestJson(
      "/v0/expand_analytics_context",
      { query: "explore", from_root: true, ...ROOT_WALK_LIMITS },
      "POST",
    )
      // Keep the single display boundary: the graph and inspector must never
      // interpolate a credential-bearing repository, pointer, or error.
      .then((result) => {
        setWalk(redactDeep(result));
        setSelectedId(null);
      })
      .catch((exc) => setError(redactDeep(exc.message || "the graph could not be loaded")))
      .finally(() => setBusy(false));
  }, [requestJson]);

  useEffect(() => {
    loadRoot();
  }, [loadRoot]);

  const graph = useMemo(() => graphData(walk), [walk]);
  const nodesById = useMemo(() => new Map(graph.nodes.map((node) => [node.id, node])), [graph.nodes]);
  const selectedNode = selectedId ? nodesById.get(selectedId) : null;
  const [zoom, setZoom] = useState(1);

  const keepNodeVisible = useCallback((nodeId) => {
    nodeRefs.current.get(nodeId)?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, []);

  const selectNode = (node) => setSelectedId(node.id);
  const zoomToFit = useCallback(() => {
    const viewport = viewportRef.current;
    if (!viewport || !graph.width) return;
    const availableWidth = Math.max(1, viewport.clientWidth - 24);
    setZoom(Math.max(ZOOM_MIN, Math.min(1, availableWidth / graph.width)));
  }, [graph.width]);
  const onNodeKeyDown = (event, node) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      selectNode(node);
    }
  };

  return (
    <div className="hive-mind">
      <div className="hive-graph-toolbar">
        <div>
          <span className="eyebrow">Explore the Hive-Mind</span>
          <h1>Context lineage</h1>
        </div>
        {walk && <div className="hive-graph-toolbar-actions">
          <span className="hive-graph-count">{graph.nodes.length} nodes · {graph.edges.length} edges</span>
          <div className="hive-graph-view-controls" role="group" aria-label="Graph view controls">
            <button type="button" aria-label="Zoom out" disabled={zoom <= ZOOM_MIN} onClick={() => setZoom((value) => Math.max(ZOOM_MIN, value - ZOOM_STEP))}>−</button>
            <span aria-label={`Graph zoom ${Math.round(zoom * 100)} percent`}>{Math.round(zoom * 100)}%</span>
            <button type="button" aria-label="Zoom in" disabled={zoom >= ZOOM_MAX} onClick={() => setZoom((value) => Math.min(ZOOM_MAX, value + ZOOM_STEP))}>+</button>
            <button type="button" aria-label="Fit graph" onClick={zoomToFit}>Fit</button>
          </div>
        </div>}
      </div>
      {busy && <div className="hive-graph-status" role="status">Loading the context graph…</div>}
      {error && <div className="hive-graph-status hive-error" role="alert">{error}</div>}
      {!busy && !error && walk?.warnings?.length > 0 && (
        <div className="hive-graph-status hive-graph-warning" role="status">
          <strong>Some graph branches need attention</strong>
          <ul>{walk.warnings.map((warning, index) => <li key={`${warning.code || "warning"}-${index}`}>{warning.message || warning.code || "The graph walk was limited."}</li>)}</ul>
        </div>
      )}
      {!busy && !error && walk && (
        <div className="hive-explorer-layout">
          <div ref={viewportRef} className="hive-graph-viewport" role="region" aria-label="Hive-Mind knowledge graph" tabIndex={0}>
            <div className="hive-graph-stage" style={{ width: graph.width * zoom, height: graph.height * zoom }}>
              <div className="hive-graph-canvas" style={{ width: graph.width, height: graph.height, transform: `scale(${zoom})` }}>
                <svg className="hive-graph-edges" width={graph.width} height={graph.height} aria-hidden="true">
                  <defs>
                    <marker id="hive-edge-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
                      <path d="M0,0 L8,4 L0,8 z" />
                    </marker>
                  </defs>
                  {graph.edges.map((edge, index) => <path key={`${edge.from}-${edge.to}-${edge.relation}-${index}`} className="hive-graph-edge" d={edgePath(edge)} markerEnd="url(#hive-edge-arrow)" />)}
                </svg>
                {graph.nodes.map((node) => (
                  <button
                    key={node.id}
                    ref={(element) => {
                      if (element) nodeRefs.current.set(node.id, element);
                      else nodeRefs.current.delete(node.id);
                    }}
                    type="button"
                    className={`hive-graph-node hive-graph-node-${node.kind}${selectedId === node.id ? " selected" : ""}`}
                    style={{ left: node.x, top: node.y }}
                    aria-label={`${kindLabel(node.kind)}: ${node.label}`}
                    aria-pressed={selectedId === node.id}
                    onClick={() => selectNode(node)}
                    onFocus={() => keepNodeVisible(node.id)}
                    onKeyDown={(event) => onNodeKeyDown(event, node)}
                  >
                    <span className="hive-graph-node-type">{kindLabel(node.kind)}</span>
                    <strong title={node.label}>{node.label}</strong>
                    <small>{node.subtitle}</small>
                  </button>
                ))}
              </div>
            </div>
          </div>
          <NodeInspector node={selectedNode} edges={graph.edges} nodesById={nodesById} />
        </div>
      )}
      {!busy && !error && walk && !graph.nodes.length && <div className="hive-graph-status">No visible context nodes.</div>}
    </div>
  );
}

// Exposed so any future persisted explorer state passes through the same
// redaction boundary. The current explorer intentionally persists nothing.
export function redactForPersist(state) {
  return redactDeep(state);
}
