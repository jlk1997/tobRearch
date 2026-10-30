import { lazy } from "react";

/** 各页按需加载。悬停侧栏时提前取，空闲时再预热每天会打开的几页。PDF 不在这里。 */
const loaders = {
  home: () => import("./pages/Home.jsx"),
  cockpit: () => import("./pages/Cockpit.jsx"),
  members: () => import("./pages/Members.jsx"),
  dirs: () => import("./pages/Directions.jsx"),
  lit: () => import("./pages/Literature.jsx"),
  reader: () => import("./pages/LiteratureReader.jsx"),
  ms: () => import("./pages/Manuscripts.jsx"),
  kb: () => import("./pages/KB.jsx"),
  assistant: () => import("./pages/Assistant.jsx"),
  lang: () => import("./pages/Language.jsx"),
  writing: () => import("./pages/Writing.jsx"),
  runs: () => import("./pages/Runs.jsx"),
  collab: () => import("./pages/Collab.jsx"),
  data: () => import("./pages/Datasets.jsx"),
  digest: () => import("./pages/Digest.jsx"),
  graph: () => import("./pages/Graph.jsx"),
  settings: () => import("./pages/Settings.jsx"),
  admin: () => import("./pages/Admin.jsx"),
};

function page(key, name) {
  const load = loaders[key];
  return lazy(() => load().then((mod) => ({ default: name ? mod[name] : mod.default })));
}

export const Home = page("home");
export const Cockpit = page("cockpit", "Cockpit");
export const CockpitMember = page("cockpit", "CockpitMember");
export const Members = page("members");
export const Directions = page("dirs");
export const Literature = page("lit");
export const LiteratureReader = page("reader");
export const Manuscripts = page("ms", "Manuscripts");
export const ManuscriptEditor = page("ms", "ManuscriptEditor");
export const KB = page("kb");
export const Assistant = page("assistant");
export const Language = page("lang");
export const Writing = page("writing");
export const Runs = page("runs", "Runs");
export const RunDetail = page("runs", "RunDetail");
export const Reproduces = page("runs", "Reproduces");
export const ReproduceDetail = page("runs", "ReproduceDetail");
export const Tasks = page("collab", "Tasks");
export const Meetings = page("collab", "Meetings");
export const Files = page("collab", "Files");
export const Stats = page("collab", "Stats");
export const Notifications = page("collab", "Notifications");
export const WorkLogs = page("collab", "WorkLogs");
export const Datasets = page("data", "Datasets");
export const DatasetDetail = page("data", "DatasetDetail");
export const Digest = page("digest");
export const Graph = page("graph");
export const Keys = page("settings", "Keys");
export const LabLlm = page("settings", "LabLlm");
export const MePage = page("settings", "MePage");
export const Admin = page("admin");

const ROUTE_LOADER = {
  home: "home",
  assistant: "assistant",
  cockpit: "cockpit",
  members: "members",
  dirs: "dirs",
  lit: "lit",
  ms: "ms",
  kb: "kb",
  writing: "writing",
  lang: "lang",
  data: "data",
  graph: "graph",
  digest: "digest",
  runs: "runs",
  repro: "runs",
  tasks: "collab",
  meet: "collab",
  files: "collab",
  stats: "collab",
  logs: "collab",
  ntf: "collab",
  keys: "settings",
  llab: "settings",
  me: "settings",
  admin: "admin",
  pending: "",
};

const started = new Set();

export function prefetchRoute(routeKey) {
  const key = ROUTE_LOADER[routeKey];
  const load = key && loaders[key];
  if (!load || started.has(key)) return;
  started.add(key);
  load().catch(() => started.delete(key));
}

const WARM = ["home", "cockpit", "assistant", "lit", "ms", "dirs", "members"];

export function prefetchDaily() {
  WARM.forEach((key) => prefetchRoute(key));
}
