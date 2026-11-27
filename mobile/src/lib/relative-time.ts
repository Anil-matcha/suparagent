const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

export function startOfDay(ms: number): number {
  const d = new Date(ms);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

/**
 * Safely parses an ISO date or SQLite timestamp ("2026-11-10 12:50:32").
 */
export function parseIsoDate(iso: string): number {
  let then = Date.parse(iso);
  if (Number.isNaN(then) && iso.includes(' ')) {
    then = Date.parse(iso.replace(' ', 'T') + (iso.endsWith('Z') ? '' : 'Z'));
  }
  return then;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

function formatTimeOnly(date: Date): string {
  try {
    return date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  } catch {
    const hours = date.getHours();
    const minutes = String(date.getMinutes()).padStart(2, '0');
    const ampm = hours >= 12 ? 'PM' : 'AM';
    const h = hours % 12 || 12;
    return `${h}:${minutes} ${ampm}`;
  }
}

function formatDateOnly(date: Date, includeWeekday: boolean, includeYear: boolean): string {
  try {
    if (includeWeekday) {
      return date.toLocaleDateString([], includeYear ? { year: 'numeric', month: 'short', day: 'numeric' } : { weekday: 'short', month: 'short', day: 'numeric' });
    }
    return date.toLocaleDateString([], includeYear ? { year: 'numeric', month: 'short', day: 'numeric' } : { month: 'short', day: 'numeric' });
  } catch {
    const m = MONTHS[date.getMonth()];
    const d = date.getDate();
    if (includeYear) return `${m} ${d}, ${date.getFullYear()}`;
    if (includeWeekday) return `${DAYS[date.getDay()]}, ${m} ${d}`;
    return `${m} ${d}`;
  }
}

/**
 * Formats a chat message timestamp:
 * - Today: "5:26 PM" (time only)
 * - Yesterday: "Yesterday, 5:26 PM"
 * - Older (same year): "Nov 3, 5:26 PM"
 * - Different year: "Nov 3, 2025, 5:26 PM"
 */
export function formatMessageTime(iso?: string | null, now: number = Date.now()): string | null {
  if (!iso) return null;
  const then = parseIsoDate(iso);
  if (Number.isNaN(then)) return null;

  const date = new Date(then);
  const timePart = formatTimeOnly(date);

  const days = Math.round((startOfDay(now) - startOfDay(then)) / DAY);
  if (days <= 0) {
    return timePart;
  }
  if (days === 1) {
    return `Yesterday, ${timePart}`;
  }
  const nowDate = new Date(now);
  const sameYear = date.getFullYear() === nowDate.getFullYear();
  const datePart = formatDateOnly(date, false, !sameYear);
  return `${datePart}, ${timePart}`;
}

/**
 * Returns a calendar day divider label for chat streams.
 * - Today: "Today"
 * - Yesterday: "Yesterday"
 * - Older (same year): "Wed, Nov 3"
 * - Different year: "Nov 3, 2025"
 */
export function formatDateDivider(iso?: string | null, now: number = Date.now()): string | null {
  if (!iso) return null;
  const then = parseIsoDate(iso);
  if (Number.isNaN(then)) return null;

  const date = new Date(then);
  const days = Math.round((startOfDay(now) - startOfDay(then)) / DAY);
  if (days <= 0) {
    return 'Today';
  }
  if (days === 1) {
    return 'Yesterday';
  }
  const nowDate = new Date(now);
  const sameYear = date.getFullYear() === nowDate.getFullYear();
  return formatDateOnly(date, sameYear, !sameYear);
}

/**
 * Finds the keys of items that should have a DateDivider rendered before them.
 */
export function findDateDividerItemKeys(items: readonly { key: string; ts?: string | null }[]): Set<string> {
  let lastDateKey: string | null = null;
  const dividerKeys = new Set<string>();
  for (const item of items) {
    const ts = item.ts;
    if (ts) {
      const then = parseIsoDate(ts);
      if (!Number.isNaN(then)) {
        const d = new Date(then);
        const dateKey = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
        if (dateKey !== lastDateKey) {
          lastDateKey = dateKey;
          dividerKeys.add(item.key);
        }
      }
    }
  }
  return dividerKeys;
}

