import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { EvaluationRun } from "@/api/types";
import { useTheme } from "@/lib/theme";
import { humanize } from "@/lib/utils";
import { retrievalChartRows } from "./metrics";

// Mirrors --chart-1..4 in index.css (Recharts needs concrete colours for SVG attributes).
const PALETTE = {
  light: { dense: "#4f46e5", lexical: "#0e9384", hybrid: "#d97706", hybrid_rerank: "#c026d3", grid: "#e2e5ea", text: "#556070" },
  dark: { dense: "#8b93f8", lexical: "#2dd4bf", hybrid: "#fbbf24", hybrid_rerank: "#e879f9", grid: "#252a35", text: "#9ba4b4" },
} as const;

export function RetrievalChart({ runs }: { runs: EvaluationRun[] }) {
  const { resolved } = useTheme();
  const colors = PALETTE[resolved];
  const rows = retrievalChartRows(runs);
  return (
    <div className="h-72 w-full" role="img" aria-label="Grouped bar chart of retrieval metrics per mode; the same numbers are in the table below.">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: -16 }} barCategoryGap="20%">
          <CartesianGrid vertical={false} stroke={colors.grid} />
          <XAxis dataKey="metric" tick={{ fill: colors.text, fontSize: 11 }} tickLine={false} axisLine={{ stroke: colors.grid }} interval={0} />
          <YAxis domain={[0, 1]} tickCount={6} tick={{ fill: colors.text, fontSize: 11 }} tickLine={false} axisLine={false} />
          <Tooltip
            cursor={{ fill: colors.grid, opacity: 0.4 }}
            formatter={(value) => (typeof value === "number" ? value.toFixed(3) : "—")}
            contentStyle={{ background: "var(--card)", border: "1px solid var(--border)", borderRadius: 6, fontSize: 12, color: "var(--foreground)" }}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} formatter={(v: string) => humanize(v)} />
          {runs.map((run) => (
            <Bar
              key={run.retrieval_mode}
              dataKey={run.retrieval_mode}
              name={run.retrieval_mode}
              fill={(colors as Record<string, string>)[run.retrieval_mode] ?? colors.dense}
              radius={[3, 3, 0, 0]}
              maxBarSize={28}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
