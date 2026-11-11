"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/Badge";
import { Banner } from "@/components/ui/Banner";
import { SkeletonList } from "@/components/ui/Skeleton";
import { activeWorkspace, listWorkspaces, roleLabel, sortRoster, workspaceRoster, type RosterMember, type Workspace } from "@/lib/workspaces";

// People in a hosted workspace. The account service decides who belongs and in what role, so this screen only lists
// them (names and roles, from the account service, not from people who happened to open this computer) and sends
// owners and admins to the page where people are managed.
export function HostedPeople() {
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [members, setMembers] = useState<RosterMember[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    listWorkspaces()
      .then(async (list) => {
        const current = activeWorkspace(list);
        if (!current) throw new Error("no active workspace");
        const roster = await workspaceRoster(current.id);
        if (!live) return;
        setWorkspace(current);
        setMembers(sortRoster(roster));
      })
      .catch(() => {
        if (live) setFailed(true);
      });
    return () => {
      live = false;
    };
  }, []);

  const manage = workspace?.role === "owner" || workspace?.role === "admin";
  return (
    <section aria-labelledby="people-heading" className="space-y-3">
      <h2 id="people-heading" className="text-base font-semibold">
        People{workspace ? ` in ${workspace.name}` : ""}
      </h2>
      {failed && <Banner tone="danger">Could not load the people in this workspace.</Banner>}
      {!failed && members === null && <SkeletonList rows={3} />}
      {members && (
        <ul className="divide-y divide-line rounded-md border border-line" data-testid="hosted-people">
          {members.map((m) => (
            <li key={`${m.role}-${m.name}`} className="flex items-center justify-between gap-2 px-3 py-2 text-sm">
              <span className="min-w-0 truncate">
                {m.name}
                {m.me ? " (you)" : ""}
              </span>
              <Badge>{roleLabel(m.role)}</Badge>
            </li>
          ))}
        </ul>
      )}
      <p className="text-xs text-muted">
        {manage ? (
          <>
            Invite people, change roles and permissions, or remove someone on the{" "}
            <a className="underline" href="/account/workspaces">
              workspaces page
            </a>
            .
          </>
        ) : (
          "People are managed by the workspace owner and admins."
        )}
      </p>
    </section>
  );
}
