import assert from "node:assert/strict";
import test from "node:test";
import {
  documentStatusLabel,
  documentTypeLabel,
  eventDetails,
  eventTitle,
  eventTypeLabel,
  eventTypeStyle,
  formatEventDate,
  formatTimestamp,
  groupByMonth,
  precisionNote,
  reviewStatusLabel,
  sparkline,
  timelinePositions,
  trendLabel,
} from "../src/lib/format.ts";

// --- event text --------------------------------------------------------------------------
test("each event type has a headline in plain words", () => {
  assert.equal(eventTitle("lab", { test_name: "HbA1c", value: 7.2, unit: "%" }), "HbA1c: 7.2 %");
  assert.equal(eventTitle("medication", { medication: "Metformin", dose: "500mg" }), "Metformin 500mg");
  assert.equal(eventTitle("encounter", { encounter_type: "Annual Physical" }), "Annual Physical");
  assert.equal(eventTitle("symptom", { symptom: "headache" }), "Symptom: headache");
});

test("a field the document did not print leaves no gap or 'undefined' in the headline", () => {
  assert.equal(eventTitle("medication", { medication: "Metformin" }), "Metformin");
  assert.equal(eventTitle("lab", { test_name: "HbA1c", value: 7.2 }), "HbA1c: 7.2");
  assert.equal(eventTitle("encounter", {}), "Visit");
  assert.ok(!eventTitle("lab", { test_name: "HbA1c", value: null, unit: undefined }).includes("undefined"));
  assert.equal(eventTitle("something_new", {}), "something_new");
});

test("the rest of what a record says is listed line by line, and only if present", () => {
  assert.deepEqual(eventDetails("lab", { reference_range: "4.0-5.6" }), ["Reference range 4.0-5.6"]);
  assert.deepEqual(eventDetails("medication", { frequency: "twice daily", course_days: 7 }),
    ["twice daily", "for 7 days"]);
  assert.deepEqual(eventDetails("encounter", { provider_or_facility: "Riverside Clinic", reason: "routine checkup" }),
    ["at Riverside Clinic", "Reason: routine checkup"]);
  assert.deepEqual(eventDetails("symptom", { detail: "mild, intermittent" }), ["mild, intermittent"]);
  assert.deepEqual(eventDetails("lab", {}), []);
  assert.deepEqual(eventDetails("unknown", { x: 1 }), []);
});

test("type labels and colours, with a safe fallback for a type we do not know", () => {
  assert.equal(eventTypeLabel("lab"), "Lab result");
  assert.equal(eventTypeLabel("mystery"), "mystery");
  assert.notEqual(eventTypeStyle("lab").dot, eventTypeStyle("medication").dot);
  assert.ok(eventTypeStyle("mystery").badge.length > 0);
});

// --- dates -------------------------------------------------------------------------------
test("a date is shown at exactly the precision the record has", () => {
  assert.equal(formatEventDate("2026-01-10", "day"), "10 Jan 2026");
  assert.equal(formatEventDate("2026-01-01", "month"), "Jan 2026");
  assert.equal(formatEventDate("2026-01-01", "year"), "2026");
  assert.equal(precisionNote("day"), null);
  assert.equal(precisionNote("month"), "month only");
  assert.equal(precisionNote("year"), "year only");
});

test("a date that cannot be read is returned as it came, not as 'Invalid Date'", () => {
  assert.equal(formatEventDate("not a date", "day"), "not a date");
  assert.equal(formatEventDate("2026-13-01", "day"), "2026-13-01");
});

test("a timestamp is shown as a date in the reader's timezone, and unknown when missing", () => {
  assert.equal(formatTimestamp("2026-10-10T07:14:18+00:00", "UTC"), "10 Oct 2026");
  // two zones a day apart, so the answer cannot match by accident on any machine
  assert.equal(formatTimestamp("2026-10-10T12:00:00+00:00", "Pacific/Kiritimati"), "11 Oct 2026");
  assert.equal(formatTimestamp("2026-10-10T12:00:00+00:00", "Pacific/Pago_Pago"), "10 Oct 2026");
  assert.equal(formatTimestamp("2026-10-10T00:30:00+00:00", "Pacific/Pago_Pago"), "9 Oct 2026");
  assert.equal(formatTimestamp(null), "Unknown");
  assert.equal(formatTimestamp("garbage"), "Unknown");
});

test("events are grouped under month headings in the order given", () => {
  const events = [
    { id: 1, event_date: "2026-04-10", event_date_precision: "day" },
    { id: 2, event_date: "2026-04-02", event_date_precision: "day" },
    { id: 3, event_date: "2026-01-10", event_date_precision: "day" },
  ];
  const groups = groupByMonth(events);
  assert.deepEqual(groups.map((g) => [g.key, g.label, g.items.map((e) => e.id)]), [
    ["2026-04", "April 2026", [1, 2]],
    ["2026-01", "January 2026", [3]],
  ]);
});

test("an event known only to the year is not filed under January", () => {
  const groups = groupByMonth([
    { event_date: "2026-01-10", event_date_precision: "day" },
    { event_date: "2026-01-01", event_date_precision: "year" },
    { event_date: "oops", event_date_precision: "day" },
  ]);
  assert.deepEqual(groups.map((g) => g.label), ["January 2026", "2026", "Unknown date"]);
  assert.deepEqual(groupByMonth([]), []);
});

// --- documents and review ----------------------------------------------------------------
test("document types and statuses read as words", () => {
  assert.equal(documentTypeLabel("lab_report"), "Lab report");
  assert.equal(documentTypeLabel(null), "Not classified yet");
  assert.equal(documentTypeLabel("new_kind"), "new_kind");
  assert.equal(documentStatusLabel("processing"), "Processing...");
  assert.equal(documentStatusLabel("odd"), "odd");
});

test("review statuses say what they mean for the timeline", () => {
  assert.equal(reviewStatusLabel("pending"), "Awaiting your review");
  assert.equal(reviewStatusLabel("edited"), "Confirmed (edited)");
  assert.equal(reviewStatusLabel("rejected"), "Rejected");
});

// --- trends ------------------------------------------------------------------------------
test("a trend is described as rising or falling, never as better or worse", () => {
  assert.equal(trendLabel("increasing"), "Rising");
  assert.equal(trendLabel("decreasing"), "Falling");
  assert.equal(trendLabel("stable"), "Stable");
  for (const d of ["increasing", "decreasing", "stable", "insufficient_data", "other"]) {
    assert.ok(!/improv|worse|better|bad|good/i.test(trendLabel(d)), d);
  }
  assert.equal(trendLabel("insufficient_data"), "Not enough results");
});

test("a sparkline spaces points by date and flips the axis so higher is up", () => {
  const { path, dots } = sparkline(
    [{ date: "2026-01-01", value: 10 }, { date: "2026-01-11", value: 20 }, { date: "2026-03-02", value: 15 }],
    100, 40, 4);
  assert.equal(dots.length, 3);
  assert.equal(dots[0].x, 4);                 // first date at the left edge
  assert.equal(dots[2].x, 96);                // last date at the right edge
  assert.ok(dots[1].x < 30);                  // 10 days of 60 sits near the left, not in the middle
  assert.ok(dots[1].y < dots[0].y);           // 20 is drawn above 10
  assert.equal(dots[0].y, 36);                // lowest value at the bottom edge
  assert.equal(dots[1].y, 4);                 // highest at the top edge
  assert.ok(path.startsWith("M4,36 L") && path.split("L").length === 3);
});

test("a sparkline survives one point, a flat series and no points", () => {
  assert.deepEqual(sparkline([], 100, 40).dots, []);
  assert.equal(sparkline([], 100, 40).path, "");
  const one = sparkline([{ date: "2026-01-01", value: 7 }], 100, 40);
  assert.deepEqual(one.dots, [{ x: 50, y: 20 }]);
  const flat = sparkline([{ date: "2026-01-01", value: 7 }, { date: "2026-02-01", value: 7 }], 100, 40);
  assert.ok(flat.dots.every((d) => d.y === 20));
  assert.ok(!flat.path.includes("NaN"));
});

test("timeline positions run from 0 to 100 percent by date", () => {
  const p = timelinePositions([{ event_date: "2026-01-01" }, { event_date: "2026-01-11" }, { event_date: "2026-01-21" }]);
  assert.deepEqual(p.map((x) => x.percent), [0, 50, 100]);
  assert.deepEqual(timelinePositions([{ event_date: "2026-01-01" }]).map((x) => x.percent), [50]);
  assert.deepEqual(timelinePositions([]), []);
});
