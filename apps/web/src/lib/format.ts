// Pure formatting helpers shared by the timeline, document, dashboard and settings pages.
// No imports from the app, so Node's own test runner can load this file (tests/format.test.mjs).

// --- events ------------------------------------------------------------------------------
export type EventType = "lab" | "medication" | "encounter" | "symptom";

export const EVENT_TYPE_LABEL: Record<string, string> = {
  lab: "Lab result",
  medication: "Medication",
  encounter: "Visit",
  symptom: "Symptom",
};

// Tailwind classes, kept here so every page colours a type the same way.
export const EVENT_TYPE_STYLE: Record<string, { badge: string; dot: string }> = {
  lab: { badge: "bg-sky-100 text-sky-800", dot: "bg-sky-500" },
  medication: { badge: "bg-violet-100 text-violet-800", dot: "bg-violet-500" },
  encounter: { badge: "bg-teal-100 text-teal-800", dot: "bg-teal-500" },
  symptom: { badge: "bg-amber-100 text-amber-800", dot: "bg-amber-500" },
};

export function eventTypeLabel(type: string): string {
  return EVENT_TYPE_LABEL[type] ?? type;
}

export function eventTypeStyle(type: string): { badge: string; dot: string } {
  return EVENT_TYPE_STYLE[type] ?? { badge: "bg-slate-100 text-slate-700", dot: "bg-slate-400" };
}

type EventData = Record<string, unknown>;

function text(value: unknown): string {
  return value === null || value === undefined ? "" : String(value).trim();
}

function join(...parts: string[]): string {
  return parts.filter((part) => part !== "").join(" ");
}

// The headline of an event: "HbA1c: 7.2 %", "Metformin 500mg", "Annual Physical", "Symptom: headache".
export function eventTitle(type: string, data: EventData): string {
  switch (type) {
    case "lab":
      return `${text(data.test_name)}: ${join(text(data.value), text(data.unit))}`;
    case "medication":
      return join(text(data.medication), text(data.dose));
    case "encounter":
      return text(data.encounter_type) || "Visit";
    case "symptom":
      return `Symptom: ${text(data.symptom)}`;
    default:
      return eventTypeLabel(type);
  }
}

// The rest of what the record says about it, one short line each.
export function eventDetails(type: string, data: EventData): string[] {
  const lines: string[] = [];
  const add = (label: string, value: unknown) => {
    const v = text(value);
    if (v) lines.push(label ? `${label} ${v}` : v);
  };
  switch (type) {
    case "lab":
      add("Reference range", data.reference_range);
      break;
    case "medication":
      add("", data.frequency);
      if (text(data.course_days)) lines.push(`for ${text(data.course_days)} days`);
      break;
    case "encounter":
      add("at", data.provider_or_facility);
      add("Reason:", data.reason);
      break;
    case "symptom":
      add("", data.detail);
      break;
  }
  return lines;
}

// --- dates -------------------------------------------------------------------------------
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MONTHS_LONG = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

function parts(iso: string): { year: string; month: number; day: number } | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!match) return null;
  const month = Number(match[2]);
  if (month < 1 || month > 12) return null;
  return { year: match[1], month, day: Number(match[3]) };
}

// "10 Jan 2026", "Jan 2026" (month only) or "2026" (year only): never a precision the record lacks.
export function formatEventDate(iso: string, precision: string): string {
  const p = parts(iso);
  if (!p) return iso;
  if (precision === "year") return p.year;
  if (precision === "month") return `${MONTHS[p.month - 1]} ${p.year}`;
  return `${p.day} ${MONTHS[p.month - 1]} ${p.year}`;
}

export function precisionNote(precision: string): string | null {
  if (precision === "month") return "month only";
  if (precision === "year") return "year only";
  return null;
}

// A moment from the server (ISO with a timezone) in the reader's own timezone.
export function formatTimestamp(iso: string | null | undefined, timeZone?: string): string {
  if (!iso) return "Unknown";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "Unknown";
  return date.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone });
}

export type Group<T> = { key: string; label: string; items: T[] };

// Groups events under month headings, keeping the order given. An event known only to the
// year gets its own "2026" heading instead of being placed in January.
export function groupByMonth<T extends { event_date: string; event_date_precision: string }>(
  events: T[],
): Group<T>[] {
  const groups = new Map<string, Group<T>>();
  for (const event of events) {
    const p = parts(event.event_date);
    const yearOnly = event.event_date_precision === "year";
    const key = !p ? "unknown" : yearOnly ? p.year : `${p.year}-${String(p.month).padStart(2, "0")}`;
    const label = !p ? "Unknown date" : yearOnly ? p.year : `${MONTHS_LONG[p.month - 1]} ${p.year}`;
    if (!groups.has(key)) groups.set(key, { key, label, items: [] });
    groups.get(key)!.items.push(event);
  }
  return [...groups.values()];
}

// --- documents ---------------------------------------------------------------------------
const DOCUMENT_TYPE_LABEL: Record<string, string> = {
  lab_report: "Lab report",
  prescription: "Prescription",
  discharge_summary: "Discharge summary",
  doctor_note: "Doctor note",
  other: "Other",
};

export function documentTypeLabel(classification: string | null | undefined): string {
  if (!classification) return "Not classified yet";
  return DOCUMENT_TYPE_LABEL[classification] ?? classification;
}

const STATUS_LABEL: Record<string, string> = {
  uploaded: "Uploaded",
  processing: "Processing...",
  processed: "Processed",
  failed: "Failed",
};

// Where processing stopped (the API's `failed_stage`), in words, and what the person can do about it.
const STAGE_LABEL: Record<string, string> = {
  read_file: "reading the stored file",
  parse: "opening the document",
  ocr: "reading scanned text",
  classify: "working out the document type",
  extract: "finding values in the document",
  save: "saving the results",
  interrupted: "processing was interrupted",
};

export function failedStageLabel(stage: string | null | undefined): string | null {
  if (!stage) return null;
  return STAGE_LABEL[stage] ?? null;
}

const STAGE_HINT: Record<string, string> = {
  read_file: "Try again. If it keeps failing, upload the file again.",
  parse: "The file may be damaged or protected. Try again, or upload a fresh copy.",
  ocr: "Scanned pages can be slow to read. Try again; a clearer scan helps.",
  interrupted: "This usually follows a restart. Nothing is lost: try again.",
};

export function failedStageHint(stage: string | null | undefined): string {
  return (stage && STAGE_HINT[stage]) || "Try again.";
}

export function documentStatusLabel(status: string): string {
  return STATUS_LABEL[status] ?? status;
}

export function documentStatusStyle(status: string): string {
  if (status === "failed") return "bg-red-100 text-red-700";
  if (status === "processed") return "bg-green-100 text-green-700";
  return "bg-slate-100 text-slate-600";
}

const REVIEW_LABEL: Record<string, string> = {
  pending: "Awaiting your review",
  confirmed: "Confirmed",
  edited: "Confirmed (edited)",
  rejected: "Rejected",
};

export function reviewStatusLabel(status: string): string {
  return REVIEW_LABEL[status] ?? status;
}

export function reviewStatusStyle(status: string): string {
  if (status === "confirmed" || status === "edited") return "bg-green-100 text-green-700";
  if (status === "rejected") return "bg-red-100 text-red-700";
  return "bg-amber-100 text-amber-800";
}

// --- trends ------------------------------------------------------------------------------
// Deliberately neutral: "rising" and "falling" say what the numbers did, not whether that is good.
export function trendLabel(direction: string): string {
  switch (direction) {
    case "increasing":
      return "Rising";
    case "decreasing":
      return "Falling";
    case "stable":
      return "Stable";
    default:
      return "Not enough results";
  }
}

export type SparkPoint = { x: number; y: number };

// Points for a small line chart, spaced by date (a long gap looks like one) and scaled to the box.
export function sparkline(
  points: { date: string; value: number }[],
  width: number,
  height: number,
  pad = 4,
): { path: string; dots: SparkPoint[] } {
  if (points.length === 0) return { path: "", dots: [] };
  const times = points.map((p) => Date.parse(p.date));
  const values = points.map((p) => p.value);
  const t0 = Math.min(...times);
  const t1 = Math.max(...times);
  const v0 = Math.min(...values);
  const v1 = Math.max(...values);
  const inner = (size: number) => Math.max(size - 2 * pad, 0);
  const dots = points.map((_, i) => ({
    x: t1 === t0 ? width / 2 : pad + ((times[i] - t0) / (t1 - t0)) * inner(width),
    y: v1 === v0 ? height / 2 : pad + (1 - (values[i] - v0) / (v1 - v0)) * inner(height),
  }));
  const round = (n: number) => Math.round(n * 100) / 100;
  const path = dots.map((d, i) => `${i === 0 ? "M" : "L"}${round(d.x)},${round(d.y)}`).join(" ");
  return { path, dots };
}

// Where each event sits along a strip from the earliest to the latest date (0 to 100 percent).
export function timelinePositions<T extends { event_date: string }>(
  events: T[],
): { event: T; percent: number }[] {
  const times = events.map((e) => Date.parse(e.event_date));
  const t0 = Math.min(...times);
  const t1 = Math.max(...times);
  return events.map((event, i) => ({
    event,
    percent: t1 === t0 ? 50 : ((times[i] - t0) / (t1 - t0)) * 100,
  }));
}
