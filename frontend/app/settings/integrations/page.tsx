import { AuthGate } from "@/components/AuthGate";
import { SettingsShell } from "@/components/settings/SettingsShell";
import { IntegrationsSettings } from "@/components/settings/IntegrationsSettings";

export const metadata = { title: "Integrations · Suparagent" };

export default function Page() {
  return (
    <AuthGate>
      <SettingsShell>
        <IntegrationsSettings />
      </SettingsShell>
    </AuthGate>
  );
}
