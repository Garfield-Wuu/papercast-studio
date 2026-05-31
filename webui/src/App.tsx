import { Outlet } from "react-router-dom";
import { Header } from "@/components/layout/Header";

/**
 * Top-level shell. Renders the persistent header and the routed page.
 * Pages own their own scroll, so the layout itself stays simple.
 */
export function App() {
  return (
    <div className="min-h-screen bg-bg text-fg flex flex-col">
      <Header />
      <main className="flex-1">
        <Outlet />
      </main>
    </div>
  );
}
