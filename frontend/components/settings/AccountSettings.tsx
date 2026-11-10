"use client";

import { DesktopAccountSection } from "@/components/DesktopAccountSection";
import { HostedWorkspaceCard } from "@/components/HostedWorkspaceCard";
import { DesktopPermissions } from "@/components/DesktopPermissions";
import { DevicesSection } from "@/components/DevicesSection";
import { HistoryExport } from "@/components/HistoryExport";
import { PeopleSection } from "@/components/PeopleSection";
import { Applies } from "./Applies";
import { useSession } from "@/components/AuthGate";

export function AccountSettings() {
  // The app account and the hosted-workspace card are the owner's; a member's session is refused them.
  const { session } = useSession();
  const fullAccess = session === null || session.scopes.includes("admin");
  return <div className="space-y-6">
    <p className="text-xs text-muted"><Applies when="now" /> Account, device and permission changes take effect as you make them.</p>
    <div id="setting-account-app" data-setting-id="setting-account-app" data-setting-root data-setting-container tabIndex={-1}>
      {fullAccess && <DesktopAccountSection />}
      {fullAccess && <HostedWorkspaceCard />}
      <span id="setting-account-reset" className="sr-only" />
      <span id="setting-account-delete" className="sr-only" />
    </div>
    <div id="setting-account-devices" data-setting-id="setting-account-devices" data-setting-root data-setting-container tabIndex={-1}>
      <span id="setting-account-device-list" className="sr-only" />
      <span id="setting-account-device-access" className="sr-only" />
      <span id="setting-account-device-name" className="sr-only" />
      <DevicesSection />
    </div>
    <div id="setting-account-people">
      <PeopleSection />
    </div>
    <div id="setting-account-history" data-setting-id="setting-account-history" data-setting-container tabIndex={-1}>
      <HistoryExport />
    </div>
    <div id="setting-account-permissions" data-setting-id="setting-account-permissions" data-setting-root data-setting-container tabIndex={-1}>
      <span id="setting-account-accessibility" className="sr-only" />
      <span id="setting-account-screen-recording" className="sr-only" />
      <DesktopPermissions />
    </div>
  </div>;
}
