import {
  findDateDividerItemKeys,
  formatDateDivider,
  formatMessageTime,
  parseIsoDate,
  startOfDay,
} from '@/lib/relative-time';

describe('relative-time', () => {
  // Reference instant: Wednesday, November 11, 2026 at 12:00 PM UTC
  const now = new Date('2026-11-11T12:00:00Z').getTime();

  describe('startOfDay', () => {
    it('returns timestamp for midnight', () => {
      const sod = startOfDay(now);
      const d = new Date(sod);
      expect(d.getHours()).toBe(0);
      expect(d.getMinutes()).toBe(0);
      expect(d.getSeconds()).toBe(0);
      expect(d.getMilliseconds()).toBe(0);
    });
  });

  describe('parseIsoDate', () => {
    it('parses standard ISO date', () => {
      const ms = parseIsoDate('2026-11-11T12:00:00Z');
      expect(Number.isNaN(ms)).toBe(false);
    });

    it('parses SQLite timestamp format', () => {
      const ms = parseIsoDate('2026-11-11 12:00:00');
      expect(Number.isNaN(ms)).toBe(false);
    });
  });

  describe('formatMessageTime', () => {
    it('returns null for empty or invalid input', () => {
      expect(formatMessageTime(null, now)).toBeNull();
      expect(formatMessageTime(undefined, now)).toBeNull();
      expect(formatMessageTime('invalid-date', now)).toBeNull();
    });

    it('formats today messages with time only', () => {
      const todayIso = new Date(now - 3600_000).toISOString();
      const res = formatMessageTime(todayIso, now);
      expect(res).not.toBeNull();
      expect(res).not.toContain('Yesterday');
      expect(res).not.toContain('Nov 11');
    });

    it('formats yesterday messages with "Yesterday, <time>"', () => {
      // 1 day before: November 10 at 12:00 PM
      const yesterdayIso = new Date('2026-11-10T12:00:00Z').toISOString();
      const res = formatMessageTime(yesterdayIso, now);
      expect(res).not.toBeNull();
      expect(res).toContain('Yesterday, ');
    });

    it('formats older messages in same year with date and time', () => {
      const olderIso = new Date('2026-11-05T12:00:00Z').toISOString();
      const res = formatMessageTime(olderIso, now);
      expect(res).not.toBeNull();
      expect(res).toMatch(/5.*Nov|Nov.*5/);
    });

    it('formats older messages from previous year with year, date and time', () => {
      const lastYearIso = new Date('2025-11-05T12:00:00Z').toISOString();
      const res = formatMessageTime(lastYearIso, now);
      expect(res).not.toBeNull();
      expect(res).toContain('2025');
    });
  });

  describe('formatDateDivider', () => {
    it('returns "Today" for messages today', () => {
      const todayIso = new Date(now - 1000).toISOString();
      expect(formatDateDivider(todayIso, now)).toBe('Today');
    });

    it('returns "Yesterday" for messages yesterday', () => {
      const yesterdayIso = new Date('2026-11-10T12:00:00Z').toISOString();
      expect(formatDateDivider(yesterdayIso, now)).toBe('Yesterday');
    });

    it('returns formatted date for older dates', () => {
      const olderIso = new Date('2026-11-05T12:00:00Z').toISOString();
      const divider = formatDateDivider(olderIso, now);
      expect(divider).not.toBeNull();
      expect(divider).toMatch(/5.*Nov|Nov.*5/);
    });

    it('returns null for invalid date', () => {
      expect(formatDateDivider('not-a-date', now)).toBeNull();
    });
  });

  describe('findDateDividerItemKeys', () => {
    it('returns the key of the first item on each distinct calendar day', () => {
      const items = [
        { key: 'e1', ts: '2026-11-10 12:50:32' }, // Day 1
        { key: 'e2', ts: '2026-11-10 13:00:00' }, // Day 1
        { key: 'e3', ts: '2026-11-11 05:13:36' }, // Day 2
        { key: 'e4', ts: '2026-11-11 06:45:00' }, // Day 2
      ];
      const dividerKeys = findDateDividerItemKeys(items);
      expect(dividerKeys.has('e1')).toBe(true);
      expect(dividerKeys.has('e2')).toBe(false);
      expect(dividerKeys.has('e3')).toBe(true);
      expect(dividerKeys.has('e4')).toBe(false);
    });

    it('handles items without timestamps gracefully', () => {
      const items = [
        { key: 'e1' },
        { key: 'e2', ts: '2026-11-11 05:13:36' },
      ];
      const dividerKeys = findDateDividerItemKeys(items);
      expect(dividerKeys.has('e1')).toBe(false);
      expect(dividerKeys.has('e2')).toBe(true);
    });
  });
});
