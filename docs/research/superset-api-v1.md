# Apache Superset REST API v1: dataset, chart, and dashboard

> **Checked version:** Apache Superset **6.1.0**, tag commit
> [`c83fb2bb1dcfac41ac51bcebd82471f4a7180d18`](https://github.com/apache/superset/tree/c83fb2bb1dcfac41ac51bcebd82471f4a7180d18),
> reviewed 2026-07-25.
>
> **Scope:** the JSON REST CRUD and list/detail contracts under
> `/api/v1/dataset`, `/api/v1/chart`, and `/api/v1/dashboard`. Import/export,
> screenshots, favorites, chart data execution, and dashboard embedded-state
> APIs are mentioned only where they clarify the core resource contract.

This is a source- and OpenAPI-level inventory, not a claim that Hyperset serves
these APIs. Hyperset v0 consumes Superset read-only. Runtime payloads can still
vary with permissions, feature flags, and instance configuration, so connector
support must ultimately be proven against the pinned real Superset instance.

## Sources and confidence

The field inventories below use the 6.1.0 tagged REST views and Marshmallow
schemas as the source of truth:

- [official Superset 6.1.0 API reference](https://superset.apache.org/developer-docs/6.1.0/api/);
- [dataset REST view](https://github.com/apache/superset/blob/6.1.0/superset/datasets/api.py)
  and [dataset schemas](https://github.com/apache/superset/blob/6.1.0/superset/datasets/schemas.py);
- [chart REST view](https://github.com/apache/superset/blob/6.1.0/superset/charts/api.py)
  and [chart schemas](https://github.com/apache/superset/blob/6.1.0/superset/charts/schemas.py);
- [dashboard REST view](https://github.com/apache/superset/blob/6.1.0/superset/dashboards/api.py)
  and [dashboard schemas](https://github.com/apache/superset/blob/6.1.0/superset/dashboards/schemas.py);
- [Superset REST base](https://github.com/apache/superset/blob/6.1.0/superset/views/base_api.py);
- Superset 6.1.0 pins Flask-AppBuilder 5.0.2; its
  [list-query schema](https://github.com/dpgaspar/Flask-AppBuilder/blob/v5.0.2/flask_appbuilder/api/schemas.py)
  defines the common `q` object.

The generated OpenAPI examples occasionally omit implementation fields. For
example, the tagged `DatasetPostSchema` includes `currency_code_column`, and
the dataset create implementation returns a top-level `data` member in addition
to the OpenAPI-documented `id` and `result`. These are recorded below, but
clients should preserve unknown fields rather than treat this document as a
closed-world schema.

## Authentication and authorization

### Primary API authentication: JWT bearer

The official 6.1.0 API reference specifies HTTP bearer JWT authentication for
the resource endpoints.

1. `POST /api/v1/security/login` with JSON:

   | Field | Type | Meaning |
   |---|---|---|
   | `username` | string | Superset username |
   | `password` | string | Password |
   | `provider` | string | `db` or `ldap` |
   | `refresh` | boolean | Return a refresh token when true |

2. A successful response is:

   ```json
   {
     "access_token": "<JWT>",
     "refresh_token": "<JWT when requested>"
   }
   ```

3. Send the access token on API requests:

   ```http
   Authorization: Bearer <access_token>
   ```

4. `POST /api/v1/security/refresh` with the refresh token as the bearer token
   returns a new access token.

See the official [login contract](https://superset.apache.org/developer-docs/api/create-security-login/)
and [6.1 authentication overview](https://superset.apache.org/developer-docs/6.1.0/api/#authentication).

### Browser session and CSRF

All three tagged REST views set `allow_browser_login = True`, so an authenticated
Superset browser session may also satisfy authentication. Superset 6.1.0 enables
CSRF protection by default, and `BaseSupersetApiMixin` is not CSRF-exempt.
For state-changing requests, a client should retain the session cookie, call
`GET /api/v1/security/csrf_token/`, and send the returned `result` using the
deployment's CSRF header convention (normally `X-CSRFToken`). This applies in
particular to cookie-authenticated writes and should be supported for bearer
automation as well because CSRF configuration is deployment-sensitive.

### OAuth and “API tokens”

OAuth is a configurable Superset/FAB interactive authentication provider, not
a separate core-resource OAuth contract. An OAuth deployment can establish a
browser session, after which the same REST routes and RBAC checks apply. The
documented username/password login endpoint itself accepts `db` or `ldap`; it
does not advertise `oauth` as a `provider` value.

The 6.1.0 core API reference does not define a long-lived personal API-token
scheme for these routes. For service access, the documented contract is the
short-lived JWT access token (optionally refreshed). Guest tokens are for
embedded-dashboard access and must not be treated as general dataset/chart/
dashboard CRUD credentials.

Authentication does not imply authorization. Dataset, chart, and dashboard
base filters enforce the caller's permissions; expect `401` for missing/invalid
authentication, `403` for forbidden mutations or inaccessible objects where
the view exposes that distinction, and sometimes `404` to avoid revealing an
inaccessible object.

## Shared collection conventions

### Paths and identifiers

The collection routes have a trailing slash:

| Operation | Dataset | Chart | Dashboard |
|---|---|---|---|
| List | `GET /api/v1/dataset/` | `GET /api/v1/chart/` | `GET /api/v1/dashboard/` |
| Create | `POST /api/v1/dataset/` | `POST /api/v1/chart/` | `POST /api/v1/dashboard/` |
| Read | `GET /api/v1/dataset/{id_or_uuid}` | `GET /api/v1/chart/{id_or_uuid}` | `GET /api/v1/dashboard/{id_or_slug}` |
| Update | `PUT /api/v1/dataset/{pk}` | `PUT /api/v1/chart/{pk}` | `PUT /api/v1/dashboard/{pk}` |
| Delete | `DELETE /api/v1/dataset/{pk}` | `DELETE /api/v1/chart/{pk}` | `DELETE /api/v1/dashboard/{pk}` |

The asymmetry is intentional: detail reads accept the alternate stable
identifier shown above, while update/delete use an integer primary key.

`POST` and `PUT` require `Content-Type: application/json`. Unknown/invalid
fields are rejected by the Marshmallow write schema rather than silently
becoming resource attributes.

### Pagination, ordering, field selection, and filtering

List requests accept one `q` query parameter containing a URL-encoded Rison
object (the generated description also calls this “Rison or JSON”). Its common
shape is:

```text
{
  page: integer,
  page_size: integer,
  order_column: string,
  order_direction: "asc" | "desc",
  filters: [
    {col: string, opr: string, value: number | string | boolean | scalar[]}
  ],
  columns: string[],
  select_columns: string[],
  keys: string[]
}
```

Example Rison:

```text
q=(page:0,page_size:100,order_column:changed_on,order_direction:desc,filters:!((col:published,opr:eq,value:!t)),select_columns:!(id,uuid,dashboard_title))
```

Important behavior:

- `page` is zero-based and defaults to `0`.
- `page_size` defaults to `20`.
- Flask-AppBuilder 5.0.2 defaults `FAB_API_MAX_PAGE_SIZE` to `100`; an instance
  may override it.
- An invalid `order_column` is rejected. Each resource exposes its allowed
  columns via `GET /api/v1/{resource}/_info`.
- Each filter object requires `col`, `opr`, and `value`. Valid operators depend
  on the resource column/filter implementation and are discoverable from
  `_info`; clients should not hard-code one global operator set.
- `columns` selects response fields. `select_columns` separately controls
  selected model columns while remaining restricted to the view's allowlist.
- Superset applies permission base filters in addition to caller-supplied
  filters. A smaller `count` can therefore mean limited visibility, not source
  deletion.

A successful list response has at least:

```json
{
  "count": 123,
  "result": [
    {}
  ]
}
```

Depending on requested `keys`, FAB may also return `ids`, `list_columns`,
`order_columns`, `label_columns`, `description_columns`, and `list_title`.
`count` is the total matching visible rows before page slicing.

### Common mutation and error envelopes

Create:

```json
{
  "id": 123,
  "result": {}
}
```

Update:

```json
{
  "id": 123,
  "result": {}
}
```

`result` is the validated/normalized write payload, not the full detail
representation. Dashboard update additionally returns
`last_modified_time` as a Unix timestamp.

Delete:

```json
{"message": "OK"}
```

Validation and other errors use a `message` envelope. The common documented
statuses are `400`, `401`, `403`, `404`, `422`, and `500`, though an individual
route may document only the relevant subset.

## Dataset

Source: [`DatasetRestApi`](https://github.com/apache/superset/blob/6.1.0/superset/datasets/api.py)
and [`DatasetPostSchema` / `DatasetPutSchema`](https://github.com/apache/superset/blob/6.1.0/superset/datasets/schemas.py).

### Create: `POST /api/v1/dataset/`

Request body:

| Field | Type | Required/default | Notes |
|---|---|---|---|
| `database` | integer | required | Database primary key |
| `table_name` | string | required | 1–250 characters |
| `catalog` | string/null | optional | 0–250 characters |
| `schema` | string/null | optional | 0–250 characters |
| `sql` | string/null | optional | Virtual-dataset SQL |
| `owners` | integer[] | optional | User primary keys |
| `is_managed_externally` | boolean/null | default false on dump | External-management marker |
| `external_url` | string/null | optional | External management URL |
| `normalize_columns` | boolean | default false | Normalize reflected columns |
| `always_filter_main_dttm` | boolean | default false | Always apply main time filter |
| `currency_code_column` | string/null | optional | 0–250 characters |
| `template_params` | string/null | optional | JSON/Jinja template parameter text |
| `uuid` | UUID/null | optional | Caller-provided stable UUID |

Success is `201` with `id`, `result`, and, in the tagged implementation, a
top-level `data` member sourced from `new_model.data`. The OpenAPI docstring
only declares `id` and `result`, so consumers should treat `data` as an
implementation extension.

### Read: `GET /api/v1/dataset/{id_or_uuid}`

Optional query parameters:

- `q`: the common item-selection object; `columns` is pruned to the allowed
  detail columns.
- `include_rendered_sql=true|false`: when true, render Jinja in dataset SQL,
  metric expressions, and column expressions before returning them.

Success is:

```json
{
  "id": 123,
  "result": {}
}
```

The full `result` surface is:

- identity and source: `id`, `uuid`, `uid`, `name`, `datasource_name`,
  `datasource_type`, `kind`, `table_name`, `catalog`, `schema`, `sql`;
- database: `database.database_name`, `database.id`, `database.uuid`,
  `database.backend`, `database.allow_multi_catalog`;
- behavior/configuration: `filter_select_enabled`, `fetch_values_predicate`,
  `main_dttm_col`, `currency_code_column`, `normalize_columns`,
  `always_filter_main_dttm`, `offset`, `default_endpoint`, `cache_timeout`,
  `is_sqllab_view`, `template_params`, `select_star`, `extra`,
  `is_managed_externally`;
- derived UI/transport fields: `url`, `column_formats`, `granularity_sqla`,
  `time_grain_sqla`, `order_by_choices`, `verbose_map`;
- audit/ownership: `owners.{id,first_name,last_name}`, `created_on`,
  `created_on_humanized`, `created_by.{first_name,last_name}`, `changed_on`,
  `changed_on_humanized`, `changed_by.{first_name,last_name}`;
- `columns[]`: `id`, `uuid`, `column_name`, `verbose_name`, `type`,
  `type_generic`, `advanced_data_type`, `description`, `expression`, `extra`,
  `filterable`, `groupby`, `is_active`, `is_dttm`, `python_date_format`,
  `created_on`, `changed_on`;
- `metrics[]`: `id`, `uuid`, `metric_name`, `verbose_name`, `metric_type`,
  `expression`, `description`, `d3format`, `currency`, `extra`,
  `warning_text`, `created_on`, `changed_on`;
- `folders` when the `DATASET_FOLDERS` feature is enabled.

When `DATASET_FOLDERS` is disabled, the view explicitly removes `folders` from
the response. A connector should record that capability distinction rather
than interpret a missing field as an empty folder tree.

The dataset list representation is smaller than detail. Its allowed fields are:

```text
id, uuid, database.{id,database_name,uuid},
changed_by.{id,first_name,last_name}, changed_by_name,
changed_on_utc, changed_on_delta_humanized,
default_endpoint, description, datasource_type, explore_url, extra, kind,
owners.{id,first_name,last_name}, catalog, schema, sql, table_name
```

### Update: `PUT /api/v1/dataset/{pk}`

All body fields are optional; only provided fields are changed:

```text
table_name, database_id, sql, filter_select_enabled, fetch_values_predicate,
catalog, schema, description, main_dttm_col, currency_code_column,
normalize_columns, always_filter_main_dttm, offset, default_endpoint,
cache_timeout, is_sqllab_view, template_params, owners, columns, metrics,
folders, extra, is_managed_externally, external_url, uuid
```

`override_columns=true|false` is an additional query parameter. When true, the
update command is followed by a dataset refresh.

Nested `columns[]` fields:

```text
id, column_name (required), type, advanced_data_type, verbose_name,
description, expression, extra, filterable, groupby, is_active, is_dttm,
python_date_format, datetime_format, uuid
```

Nested `metrics[]` fields:

```text
id, expression (required), metric_name (required), description, extra,
metric_type, d3format, currency.{symbol,symbolPosition}, verbose_name,
warning_text, uuid
```

Nested `folders[]` are recursive objects with `uuid` and optional `type`
(`metric`, `column`, or `folder`), `name`, `description`, and `children`.
A UUID-only object references an existing metric/column; a folder with `name`
must include `children`.

Success is `200 {"id": <pk>, "result": <validated partial body>}`.

### Delete: `DELETE /api/v1/dataset/{pk}`

The path parameter is an integer dataset primary key. Success is
`200 {"message":"OK"}`. Expected failures include `401`, `403`, `404`, `422`,
and `500`.

The API also exposes bulk delete at `DELETE /api/v1/dataset/?q=!(1,2,3)`,
plus separate column and metric deletes:
`DELETE /api/v1/dataset/{pk}/column/{column_id}` and
`DELETE /api/v1/dataset/{pk}/metric/{metric_id}`.

## Chart

Superset's chart resource is backed by the historical `Slice` model.
Source: [`ChartRestApi`](https://github.com/apache/superset/blob/6.1.0/superset/charts/api.py)
and [`ChartPostSchema` / `ChartPutSchema` / `ChartGetResponseSchema`](https://github.com/apache/superset/blob/6.1.0/superset/charts/schemas.py).

### Create: `POST /api/v1/chart/`

Request body:

| Field | Type | Required | Notes |
|---|---|---|---|
| `slice_name` | string | yes | 1–250 characters |
| `datasource_id` | integer | yes | Dataset/datasource primary key |
| `datasource_type` | enum string | yes | `table`, `dataset`, `query`, `saved_query`, or `view` |
| `description` | string/null | no | |
| `viz_type` | string | no | Up to 250 characters |
| `owners` | integer[] | no | User primary keys |
| `params` | JSON string/null | no | Validated as JSON text |
| `query_context` | JSON string/null | no | Validated as JSON text on create |
| `query_context_generation` | boolean/null | no | |
| `cache_timeout` | integer/null | no | Seconds |
| `datasource_name` | string/null | no | |
| `dashboards` | integer[] | no | Dashboard primary keys |
| `certified_by` | string/null | no | |
| `certification_details` | string/null | no | |
| `is_managed_externally` | boolean/null | no | |
| `external_url` | string/null | no | |
| `uuid` | UUID/null | no | |

Success is `201 {"id": <pk>, "result": <validated body>}`.

### Read: `GET /api/v1/chart/{id_or_uuid}`

Success is `200` with this `result`:

```text
id, uuid, url, slice_name, description, viz_type, params, query_context,
cache_timeout, thumbnail_url, changed_on_delta_humanized,
certified_by, certification_details, is_managed_externally,
owners[].{id,first_name,last_name,email},
dashboards[].{id,dashboard_title,json_metadata},
tags[].{id,name,type},
datasource_id, datasource_name_text, datasource_type, datasource_url,
datasource_uuid
```

The list representation is broader in audit/UI metadata but omits some detail
relationships. Its allowed fields are:

```text
id, uuid, is_managed_externally, certified_by, certification_details,
cache_timeout,
changed_by.{id,first_name,last_name}, changed_by_name,
changed_on_delta_humanized, changed_on_dttm, changed_on_utc,
created_by.{id,first_name,last_name}, created_by_name,
created_on_delta_humanized,
last_saved_at, last_saved_by.{id,first_name,last_name},
datasource_id, datasource_name_text, datasource_type, datasource_url,
description, description_markeddown, edit_url, form_data, params,
slice_name, slice_url, thumbnail_url, url, viz_type,
owners.{id,first_name,last_name,email},
dashboards.{id,dashboard_title},
table.{default_endpoint,table_name},
tags.{id,name,type}
```

### Update: `PUT /api/v1/chart/{pk}`

All body fields are optional:

```text
slice_name, description, viz_type, owners, params, query_context,
query_context_generation, cache_timeout, datasource_id, datasource_type,
dashboards, certified_by, certification_details, is_managed_externally,
external_url, tags, uuid
```

`owners`, `dashboards`, and `tags` are integer primary-key arrays. `params` is
validated JSON text. Unlike create, the tagged update schema does not attach
the JSON validator to `query_context`; a connector should still preserve it as
opaque JSON text.

Success is `200 {"id": <pk>, "result": <validated partial body>}`.

### Delete: `DELETE /api/v1/chart/{pk}`

The path parameter is an integer chart primary key. Success is
`200 {"message":"OK"}`. Expected failures include `401`, `403`, `404`, `422`,
and `500`.

Bulk delete is also available as `DELETE /api/v1/chart/?q=!(1,2,3)`.

## Dashboard

Source: [`DashboardRestApi`](https://github.com/apache/superset/blob/6.1.0/superset/dashboards/api.py)
and [`DashboardPostSchema` / `DashboardPutSchema` / `DashboardGetResponseSchema`](https://github.com/apache/superset/blob/6.1.0/superset/dashboards/schemas.py).

### Create: `POST /api/v1/dashboard/`

The JSON body is required, but the tagged schema does not mark an individual
field as required:

| Field | Type | Notes |
|---|---|---|
| `dashboard_title` | string/null | Up to 500 characters |
| `slug` | string/null | 1–255 characters; trimmed, spaces become `-`, other non-word/non-hyphen characters removed |
| `owners` | integer[] | User primary keys |
| `roles` | integer[] | Role primary keys |
| `position_json` | JSON string | Layout JSON |
| `css` | string | Dashboard CSS |
| `theme_id` | integer/null | Theme primary key |
| `json_metadata` | JSON string | Dashboard metadata/native-filter JSON |
| `published` | boolean | Published state |
| `certified_by` | string/null | |
| `certification_details` | string/null | |
| `is_managed_externally` | boolean/null | |
| `external_url` | string/null | |
| `uuid` | UUID/null | |

Success is `201 {"id": <pk>, "result": <validated body>}`.

### Read: `GET /api/v1/dashboard/{id_or_slug}`

The optional `q` item object may contain `columns` to select a subset of the
detail schema. Success is `200` with:

```text
result.{
  id, uuid, slug, url, dashboard_title, thumbnail_url, published,
  css, json_metadata, position_json, certified_by, certification_details,
  changed_by_name,
  changed_by.{id,first_name,last_name},
  changed_on,
  changed_on_delta_humanized,
  created_by.{id,first_name,last_name},
  created_on_delta_humanized,
  charts[],
  owners[].{id,first_name,last_name},
  roles[].{id,name},
  tags[].{id,name,type},
  is_managed_externally,
  theme.{id,theme_name,json_data}
}
```

For a guest user, the schema post-processor removes `owners`, `changed_by_name`,
and `changed_by`. This is a concrete example of permission-dependent response
shape.

The list representation is:

```text
id, uuid, published, status, slug, url, thumbnail_url,
certified_by, certification_details,
changed_by.{id,first_name,last_name}, changed_by_name,
changed_on_utc, changed_on_delta_humanized,
created_on_delta_humanized,
created_by.{id,first_name,last_name},
dashboard_title,
owners.{id,first_name,last_name,email},
roles.{id,name},
is_managed_externally,
tags.{id,name,type}
```

When `DASHBOARD_LIST_CUSTOM_TAGS_ONLY` is enabled, the implementation loads
`custom_tags` and renames it to the response key `tags`; the public shape stays
`tags`, but observed membership may be capability/configuration-dependent.

### Update: `PUT /api/v1/dashboard/{pk}`

All body fields are optional:

```text
dashboard_title, slug, owners, roles, position_json, css, theme_id,
json_metadata, published, certified_by, certification_details,
is_managed_externally, external_url, tags, uuid
```

`owners`, `roles`, and `tags` are integer primary-key arrays. The update slug
allows an empty string (0–255 characters) and receives the same normalization
as create.

Success is:

```json
{
  "id": 123,
  "result": {},
  "last_modified_time": 1785000000
}
```

`last_modified_time` is the updated model's `changed_on`, truncated to whole
seconds and converted to a Unix timestamp.

### Delete: `DELETE /api/v1/dashboard/{pk}`

The path parameter is an integer dashboard primary key. Success is
`200 {"message":"OK"}`. Expected failures include `401`, `403`, `404`, `422`,
and `500`.

Bulk delete is also available as `DELETE /api/v1/dashboard/?q=!(1,2,3)`.

## Connector implications for Hyperset v0

1. Use list endpoints for discovery and detail endpoints for complete observed
   assets. List and detail are separate contracts and expose different fields.
2. Persist the raw list/detail payloads before normalization, including unknown
   fields and top-level envelope extensions.
3. Prefer UUID for cross-transport identity, but retain integer IDs because
   relationship arrays and mutation routes use primary keys.
4. Treat missing objects/relationships conservatively. Permission base filters,
   guest redaction, feature flags, and partial access can all produce absence
   without deletion.
5. Record source version, transport, feature/capability observations, and
   collection checkpoint per sync.
6. Fetch pages until the number of collected visible records matches `count`;
   do not assume a page size above the configured maximum was honored.
7. Discover allowed filter/order fields from `_info` for the exact instance,
   and contract-test any operator used by the connector.
8. Keep `position_json`, `json_metadata`, chart `params`, `query_context`, and
   dataset `extra`/template fields as lossless source strings alongside any
   parsed representation.

## Minimal real-instance verification still required

Before calling the REST connector compatible with Superset 6.1.0, collect from
the pinned instance:

- the reported application version and generated OpenAPI spec exposed by that
  build at `/api/v1/_openapi` (`/api/{version}/_openapi`);
- one list and detail payload for each resource with deterministic ownership,
  tags, UUIDs, and relationships;
- pagination across at least two pages and filters discovered through `_info`;
- guest/limited-role payloads to establish redaction and partial-access behavior;
- repeated snapshots proving stable identity and no deletion inference after a
  simulated `401`, `403`, or partial page failure.

Those runtime artifacts, not this static inventory alone, establish connector
support.
