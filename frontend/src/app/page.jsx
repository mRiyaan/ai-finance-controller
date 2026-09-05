import DashboardShell from "@/components/DashboardShell";
import Header from "@/components/Header";

export default function Home() {
  return (
    <div className="app-shell">
      <Header />
      <DashboardShell />
    </div>
  );
}