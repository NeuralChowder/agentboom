/**
 * 5-field cron parsing + next-fire — port of agentboom_sdk.cron.
 *
 * `minute hour day month weekday`, with `*`, lists, ranges, `*/n` steps.
 * Weekday is 0-6 (0 = Sunday) and 7 is accepted as Sunday, including at
 * the end of ranges ("5-7" keeps Sunday). When both day-of-month and
 * day-of-week are restricted, either matching satisfies (Vixie cron).
 */

export class CronError extends Error {}

const FIELD_RANGES: Array<[string, number, number]> = [
  ["minute", 0, 59],
  ["hour", 0, 23],
  ["day", 1, 31],
  ["month", 1, 12],
  ["weekday", 0, 6],
];

const MAX_SEARCH_DAYS = 366 * 4;

function parseField(field: string, name: string, minVal: number, maxVal: number): number[] {
  const values = new Set<number>();
  // Weekday expands in 0..7 and wraps afterwards — normalising 7→0 before
  // expansion would turn "5-7" into "5-0" and silently drop Sunday.
  const expandMax = name === "weekday" ? 7 : maxVal;

  for (let rawPart of field.split(",")) {
    let part = rawPart.trim();
    if (!part) throw new CronError(`${name}: empty element in ${field}`);
    let step = 1;
    const slash = part.indexOf("/");
    if (slash !== -1) {
      const stepStr = part.slice(slash + 1);
      part = part.slice(0, slash);
      step = Number.parseInt(stepStr, 10);
      if (!Number.isInteger(step) || step <= 0) {
        throw new CronError(`${name}: step must be a positive integer in ${field}`);
      }
    }
    let lo: number;
    let hi: number;
    if (part === "*") {
      lo = minVal;
      hi = expandMax;
    } else if (part.includes("-")) {
      const [loStr, hiStr] = part.split("-");
      lo = Number.parseInt(loStr, 10);
      hi = Number.parseInt(hiStr, 10);
      if (!Number.isInteger(lo) || !Number.isInteger(hi)) {
        throw new CronError(`${name}: bad range ${part} in ${field}`);
      }
    } else {
      lo = Number.parseInt(part, 10);
      if (!Number.isInteger(lo)) throw new CronError(`${name}: bad value ${part} in ${field}`);
      // "5/10" means from here to the end of the range.
      hi = step > 1 ? expandMax : lo;
    }
    if (lo < minVal || hi > expandMax || hi < lo) {
      throw new CronError(`${name}: ${part} out of range ${minVal}-${maxVal} in ${field}`);
    }
    for (let v = lo; v <= hi; v += step) values.add(v);
  }

  const out = [...values].map((v) => (name === "weekday" && v === 7 ? 0 : v));
  if (out.length === 0) throw new CronError(`${name}: ${field} matches nothing`);
  return [...new Set(out)].sort((a, b) => a - b);
}

export interface CronFields {
  minute: number[];
  hour: number[];
  day: number[];
  month: number[];
  weekday: number[];
}

export function parseCron(expr: string): CronFields {
  if (!expr || !expr.trim()) throw new CronError("empty cron expression");
  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 5) {
    throw new CronError(`expected 5 fields (minute hour day month weekday), got ${parts.length}: ${expr}`);
  }
  const fields = {} as CronFields;
  FIELD_RANGES.forEach(([name, lo, hi], i) => {
    fields[name as keyof CronFields] = parseField(parts[i], name, lo, hi);
  });
  return fields;
}

export function isValidCron(expr: string): boolean {
  try {
    parseCron(expr);
    return true;
  } catch {
    return false;
  }
}

function resolveTz(tzName: string): string {
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: tzName });
    return tzName;
  } catch {
    // A typo in the tz would otherwise silently reschedule in UTC.
    console.warn(`[agentboom-sdk] unknown timezone '${tzName}' — falling back to UTC`);
    return "UTC";
  }
}

/** Wall-clock parts of a Date in a timezone. */
function wallParts(date: Date, tz: string) {
  const dtf = new Intl.DateTimeFormat("en-US", {
    timeZone: tz,
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
    hour12: false, weekday: "short",
  });
  const parts: Record<string, string> = {};
  for (const { type, value } of dtf.formatToParts(date)) parts[type] = value;
  const weekdayMap: Record<string, number> = {
    Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6,
  };
  return {
    year: Number.parseInt(parts.year, 10),
    month: Number.parseInt(parts.month, 10),
    day: Number.parseInt(parts.day, 10),
    hour: Number.parseInt(parts.hour, 10) % 24,
    minute: Number.parseInt(parts.minute, 10),
    weekday: weekdayMap[parts.weekday] ?? 0,
  };
}

/** Minutes offset between UTC and the tz at a given instant. */
function tzOffsetMinutes(date: Date, tz: string): number {
  const wall = wallParts(date, tz);
  const asUtc = Date.UTC(wall.year, wall.month - 1, wall.day, wall.hour, wall.minute);
  return Math.round((asUtc - date.getTime()) / 60000);
}

/**
 * First datetime strictly after `after` that matches `expr`, matching in
 * `tz` (DST-aware). Returns a UTC Date, or null when nothing matches
 * within four years.
 */
export function nextCronTime(expr: string, after?: Date, tz = "UTC"): Date | null {
  let fields: CronFields;
  try {
    fields = parseCron(expr);
  } catch {
    return null;
  }
  const zone = resolveTz(tz);
  const base = after ?? new Date();
  // Start from the next whole minute so we never return `after` itself.
  let cursor = new Date(Math.floor(base.getTime() / 60000) * 60000 + 60000);
  const deadline = cursor.getTime() + MAX_SEARCH_DAYS * 86400000;

  while (cursor.getTime() < deadline) {
    const offset = tzOffsetMinutes(cursor, zone);
    const wall = wallParts(cursor, zone);

    if (!fields.month.includes(wall.month)) {
      // Jump to the 1st of the next month (approximate, in the tz).
      const nextMonth = wall.month === 12 ? 1 : wall.month + 1;
      const year = wall.month === 12 ? wall.year + 1 : wall.year;
      const guessUtc = Date.UTC(year, nextMonth - 1, 1, 0, 0) - offset * 60000;
      cursor = new Date(Math.max(guessUtc, cursor.getTime() + 60000));
      continue;
    }

    const domOk = fields.day.includes(wall.day);
    const dowOk = fields.weekday.includes(wall.weekday);
    const domRestricted = fields.day.length < 31;
    const dowRestricted = fields.weekday.length < 7;
    const dayOk = domRestricted && dowRestricted
      ? domOk || dowOk
      : domRestricted ? domOk
      : dowRestricted ? dowOk
      : true;
    if (!dayOk) {
      cursor = new Date(cursor.getTime() + (24 - wall.hour) * 3600000 - wall.minute * 60000);
      continue;
    }
    if (!fields.hour.includes(wall.hour)) {
      cursor = new Date(cursor.getTime() + (60 - wall.minute) * 60000);
      continue;
    }
    if (fields.minute.includes(wall.minute)) return cursor;
    cursor = new Date(cursor.getTime() + 60000);
  }
  return null;
}
