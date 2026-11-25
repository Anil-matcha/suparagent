import type { AccountScopes, Scopes, Shares } from "@/lib/connected-apps";

type AgentDraftState = {
  connector_scopes: Scopes;
  connector_account_scopes: AccountScopes;
  connector_shares?: Shares;
  [field: string]: unknown;
};

// These maps and account selections represent sets. Canonical order keeps the
// editor clean when someone changes a value and then restores it.
export function draftSignature<T extends AgentDraftState>(draft: T): string {
  return JSON.stringify({
    ...draft,
    connector_scopes: Object.fromEntries(Object.entries(draft.connector_scopes).sort(([a], [b]) => a.localeCompare(b))),
    connector_account_scopes: Object.fromEntries(
      Object.entries(draft.connector_account_scopes)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([app, accounts]) => [app, [...accounts].sort()]),
    ),
    connector_shares: Object.fromEntries(
      Object.entries(draft.connector_shares ?? {})
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([app, accounts]) => [app, Object.fromEntries(
          Object.entries(accounts)
            // All mail with no pins means the same as no entry, so it is not a change.
            .filter(([, share]) => share.percent !== 100 || (share.pins?.length ?? 0) > 0)
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([id, share]) => [id, { percent: share.percent, pins: [...(share.pins ?? [])].sort() }]),
        )])
        // An app left with no entries is the same as an app with none.
        .filter(([, accounts]) => Object.keys(accounts).length > 0),
    ),
  });
}
