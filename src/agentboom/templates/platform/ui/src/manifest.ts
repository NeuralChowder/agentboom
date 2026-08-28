/**
 * UI manifest types — the contract between a mini-app's `.miniapp.json`
 * `ui` field and the renderers in this package.
 *
 * A mini-app declares its screens declaratively; the dashboard renders
 * them. This keeps the frontend generic: new mini-apps show up with real
 * UI without any frontend code changes.
 */

/** How a cell/value should be rendered. */
export type CellFormat =
  | "text"
  | "badge"
  | "relative"      // timestamp -> "3m ago"
  | "date"
  | "number"
  | "currency"
  | "boolean"
  | "link";

/** A column in a table view. */
export interface ColumnSpec {
  field: string;
  label?: string;
  width?: string;
  format?: CellFormat;
  sortable?: boolean;
  primary?: boolean;      // the row's identity (used for row click / nav)
  href?: string;          // template, e.g. "/api/proxy{{preview_url}}"
}

/** An action button on a row, view, or item. */
export interface ActionSpec {
  label: string;
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  path: string;                     // may contain {{placeholders}} from the row
  body?: Record<string, unknown>;   // may contain {{placeholders}}
  style?: "primary" | "danger" | "default";
  overflow?: boolean;               // hide in a "more" menu on small screens
  confirm?: string;                 // confirmation prompt text (templated)
  confirmWhen?: string;             // only confirm if this row field is truthy
  promptFor?: string;               // ask the user for this field before sending
  promptFrom?: string;              // prefill the prompt from this row field
  promptHint?: string;
  promptMultiline?: boolean;
  promptOptionsFrom?: {             // prompt is a dropdown fed by an endpoint
    source: string;
    rows: string;
    value: string;
    label: string;
  };
  showResult?: {                    // render the response in a dialog
    field: string;
    titleField?: string;
    subtitleField?: string;
  };
}

/** Bulk-select configuration for a table/list. */
export interface SelectSpec {
  key: string;                      // row field used as the id
  noun: string;                     // "emails", "items", ...
  field: string;                    // request body field receiving the ids
  actions: ActionSpec[];
}

/** A list-view item template. */
export interface ListItemSpec {
  title: string;                    // templated
  subtitle?: string;
  body?: string;
  meta?: Array<{ value: string; icon?: string; format?: CellFormat }>;
  badges?: string[];
  options?: {                       // per-item actions/options block
    from: string;                   // row field holding the options array
    label: string;
    badge?: string;
    detail?: string;
    heading?: string;
    description?: string;
    action: ActionSpec;
  };
}

/** A field in a form view. */
export interface FieldSpec {
  name: string;
  label?: string;
  type?: "text" | "textarea" | "number" | "password" | "select" | "checkbox" | "date";
  required?: boolean;
  placeholder?: string;
  default?: string | number | boolean;
  options?: Array<string | { value: string; label: string }>;
}

/** The common base of every view. */
export interface ViewBase {
  id: string;
  title: string;
  description?: string;
  refreshMs?: number;
}

export interface TableViewSpec extends ViewBase {
  type: "table";
  source: string;                   // endpoint returning { [rows]: [...] }
  rows: string;                     // key of the array in the response
  columns: ColumnSpec[];
  actions?: ActionSpec[];           // per-row actions
  empty?: string;
  select?: SelectSpec;
}

export interface ListViewSpec extends ViewBase {
  type: "list";
  source: string;
  rows: string;
  item: ListItemSpec;
  actions?: ActionSpec[];           // per-item actions
  empty?: string;
  select?: SelectSpec;
}

export interface FormViewSpec extends ViewBase {
  type: "form";
  fields: FieldSpec[];
  submit: { method: ActionSpec["method"]; path: string; label?: string };
  formAs?: "inline" | "dialog";
  formButton?: string;
}

export interface StatsViewSpec extends ViewBase {
  type: "stats";
  source: string;                   // endpoint returning an object of scalars
}

export type ViewSpec = TableViewSpec | ListViewSpec | FormViewSpec | StatsViewSpec;

/** Navigation placement for a mini-app. */
export interface NavSpec {
  label: string;
  icon?: string;                    // icon name (renderer maps to an icon set)
  group?: string;                   // nav grouping, e.g. "Email", "System"
  order?: number;
}

/** The top-level `ui` object in a `.miniapp.json` manifest. */
export interface MiniAppUiSpec {
  nav?: NavSpec;
  views?: ViewSpec[];
}

/** A loaded mini-app as seen by the dashboard (catalog entry + ui). */
export interface MiniAppEntry {
  name: string;
  description?: string;
  version?: string;
  loaded?: boolean;
  base_url?: string;
  ui?: MiniAppUiSpec | null;
}
