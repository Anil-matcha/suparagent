import { Suspense } from "react";
import { AuthGate } from "@/components/AuthGate";
import { SavedAppView } from "@/components/SavedAppView";

export const metadata = { title: "App · Suparagent" };

// The app is a query string (/apps/view/?id=sa_...), not a route segment, so the page also works as a static export.
export default function SavedAppPage() {
  return (
    <Suspense fallback={<p className="p-6 text-center text-sm text-muted">Loading…</p>}>
      <AuthGate>
        <SavedAppView />
      </AuthGate>
    </Suspense>
  );
}
