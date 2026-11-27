import { AuthGate } from "@/components/AuthGate";
import { SettingsShell } from "@/components/settings/SettingsShell";
import { ChannelsSettings } from "@/components/settings/ChannelsSettings";

export const metadata = { title: "Channels · Suparagent" };

export default function Page() {
  return (
    <AuthGate>
      <SettingsShell>
        <ChannelsSettings />
      </SettingsShell>
    </AuthGate>
  );
}
