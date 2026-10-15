
import { useCallback, useEffect, useState } from "react";
import './DashboardStats.css'
import { adminFetch, subscribeAdminEvents } from "./adminApi.js";

export default function DashboardStats() {
  const [stats, setStats] = useState(null);
  const [error, setError] = useState("");
  const loadStats = useCallback(async () => {
    try {
      const response = await adminFetch("/api/admin/dashboard-stats");
      if (!response.ok) {
        throw new Error("Failed to load dashboard statistics.");
      }
      setStats(await response.json());
      setError("");
    } catch (err) {
      setError(err.message || "Could not load dashboard statistics.");
    }
  }, []);

  useEffect(() => {
    loadStats();
    const unsubscribe = subscribeAdminEvents((payload) => {
      if (["stats", "transaction", "alert", "case"].includes(payload.event)) {
        loadStats();
      }
    });
    return unsubscribe;
  }, [loadStats]);

  if (error) return <p role="alert">{error}</p>;
  if (!stats) return <p>Loading dashboard statistics...</p>;

  const cards = [
    {
      label: "Total Transactions",
      value: stats.total_transactions,
    },
    {
      label: "High-Risk Transactions",
      value: stats.high_risk_transactions,
    },
    {
      label: "Active Cases",
      value: stats.open_cases,
    },
    {
      label: "Resolved Cases",
      value: stats.resolved_cases,
    },
    {
      label: "Blocked Transactions",
      value: stats.blocked,
    },
    {
      label: "Frozen Accounts",
      value: stats.frozen_accounts,
    },
    {
      label: "Resolved Cases",
      value: stats.resolved_cases,
    },
  ];

  return (
    <section className="dashboard-stats">
      <h2>Fraud Monitoring Overview</h2>

      <div className="stats-grid">
        {cards.map((card) => (
          <article className="stats-card" key={card.label}>
            <p>{card.label}</p>
            <h3>{card.value}</h3>
          </article>
        ))}
      </div>
    </section>
  );
}