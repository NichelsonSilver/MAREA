import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  ReferenceLine,
  ResponsiveContainer,
  XAxis,
  YAxis,
} from "recharts";

import { Badge, Card, ErrorBox, Loading, Warning } from "../common/Card";
import { useLang } from "../../i18n";
import { api } from "../../lib/api";
import { INK, SERIES } from "../../lib/chartTheme";
import { DASH, isNum, signClass, signedPct } from "../../lib/format";
import type { FlowAsset, FlowGroup, FlowReport, FlowWindow } from "../../types";

/**
 * El mercado completo agrupado por la función del dinero (refugio, defensivo,
 * cíclico, energía...): macro + los 11 sectores + BTC y ETH. Responde "¿salió
 * el dinero de lo especulativo hacia lo defensivo?", que la tabla por clase
 * no ve porque salud y energía viven dentro de "acciones".
 *
 * Retorno absoluto, no relativo: mezclar sectores con SPY en una mediana
 * contaría dos veces la bolsa de EE.UU. Es precio, no flujo literal, y no es
 * pronóstico (la M3 midió que este orden no se sostiene hacia adelante).
 */

export const GROUP_LABEL: Record<string, [string, string]> = {
  refugio: ["Safe haven (dollar, cash)", "Refugio (dólar, caja)"],
  bonos: ["Treasuries", "Bonos del Tesoro"],
  defensivo: ["Defensive equities", "Acciones defensivas"],
  credito: ["Corporate credit", "Crédito corporativo"],
  inmobiliario: ["Real estate", "Inmobiliario"],
  metales: ["Gold and silver", "Oro y plata"],
  energia_materias: ["Energy and commodities", "Energía y materias primas"],
  ciclico: ["Cyclicals", "Cíclicos"],
  crecimiento: ["Growth / tech", "Crecimiento / tecnología"],
  internacional: ["International", "Internacional"],
  cripto: ["Crypto", "Cripto"],
};

const WINDOW_LABEL: Record<FlowWindow, [string, string]> = {
  "1s": ["1 wk", "1 sem"],
  "4s": ["4 wk", "4 sem"],
  "13s": ["13 wk", "13 sem"],
  "26s": ["26 wk", "26 sem"],
  "52s": ["52 wk", "52 sem"],
};

const AXIS = { stroke: INK.axis, tick: { fill: INK.muted, fontSize: 10 } };

function val(row: Record<string, unknown>, key: string): number | null {
  const v = row[key];
  return typeof v === "number" ? v : null;
}

/** Lectura del percentil del apetito por riesgo, en palabras. Sin color de estado: no es señal. */
function riskWord(pct: number | null, tr: (en: string, es: string) => string): string {
  if (!isNum(pct)) return DASH;
  if (pct <= 0.2) return tr("defensive rotation", "rotación defensiva");
  if (pct >= 0.8) return tr("risk-seeking rotation", "rotación hacia riesgo");
  return tr("no clear tilt", "sin inclinación clara");
}

export function MarketFlowCard() {
  const { lang, tr } = useLang();
  const [win, setWin] = useState<FlowWindow>("13s");
  const q = useQuery({
    queryKey: ["macro", "flow"],
    queryFn: () => api.flow(),
    retry: false,
    staleTime: 600_000,
  });
  const gl = (g: string | null) =>
    g && GROUP_LABEL[g] ? GROUP_LABEL[g][lang === "es" ? 1 : 0] : (g ?? tr("reference", "referencia"));

  return (
    <Card
      title={tr("Whole market by function", "Mercado completo por función")}
      right={
        <Badge
          tone="muted"
          title={tr(
            "Absolute total return; price, not literal money flow; not a forecast",
            "Retorno total absoluto; precio, no flujo literal; no es pronóstico",
          )}
        >
          {tr("descriptive", "descriptivo")}
        </Badge>
      }
    >
      {q.isLoading && <Loading what={tr("the market by function", "el mercado por función")} />}
      {q.error && <ErrorBox>{String(q.error)}</ErrorBox>}
      {q.data && <FlowView rep={q.data} win={win} setWin={setWin} gl={gl} />}
    </Card>
  );
}

function FlowView({
  rep,
  win,
  setWin,
  gl,
}: {
  rep: FlowReport;
  win: FlowWindow;
  setWin: (w: FlowWindow) => void;
  gl: (g: string | null) => string;
}) {
  const { lang, tr } = useLang();
  const wl = (w: FlowWindow) => WINDOW_LABEL[w][lang === "es" ? 1 : 0];
  const groups = [...rep.groups].sort(
    (a, b) => (val(b, `ret_${win}`) ?? -9) - (val(a, `ret_${win}`) ?? -9),
  );
  const chart = groups.map((g) => ({ name: gl(g.group), v: val(g, `ret_${win}`) }));

  return (
    <div className="space-y-4">
      <p className="text-xs text-neutral-500">
        {tr(
          `Prices as of ${rep.as_of}. Macro ETFs, the 11 US sectors and BTC/ETH grouped by what the money is doing. SPY and QQQ are references, outside the groups.`,
          `Precios al ${rep.as_of}. ETFs macro, los 11 sectores de EE.UU. y BTC/ETH agrupados por lo que hace el dinero. SPY y QQQ son referencias, fuera de los grupos.`,
        )}
      </p>
      {rep.stale && (
        <Warning>
          {tr(`Prices are ${rep.age_days} days old.`, `Los precios tienen ${rep.age_days} días.`)}
        </Warning>
      )}

      <div>
        <div className="mb-1 text-xs uppercase tracking-wider text-neutral-500">
          {tr("Risk appetite (risk minus defense)", "Apetito por riesgo (riesgo menos defensa)")}
        </div>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
          {rep.windows.map((w) => {
            const r = rep.risk_appetite[w];
            return (
              <div key={w} className="rounded border border-neutral-800 px-2 py-1.5">
                <div className="text-[11px] text-neutral-500">{wl(w)}</div>
                <div className={`font-mono text-sm ${signClass(r.score)}`}>{signedPct(r.score, 1)}</div>
                <div className="text-[11px] text-neutral-400">
                  {tr("pct", "pct")} {isNum(r.pct) ? r.pct.toFixed(2) : DASH} · {riskWord(r.pct, tr)}
                </div>
              </div>
            );
          })}
        </div>
        <p className="mt-1 text-[11px] text-neutral-500">
          {tr(
            "Cyclicals, growth and international minus safe haven, Treasuries and defensives. Percentile against its own strict past (since 2002), without overlapping the current window.",
            "Cíclicos, crecimiento e internacional menos refugio, bonos y defensivos. Percentil contra su propio pasado estricto (desde 2002), sin solapar la ventana actual.",
          )}
        </p>
      </div>

      <div className="flex flex-wrap gap-1">
        {rep.windows.map((w) => (
          <button
            key={w}
            type="button"
            onClick={() => setWin(w)}
            className={`rounded px-2 py-0.5 text-xs ${
              w === win ? "bg-neutral-700 text-neutral-100" : "text-neutral-400 hover:text-neutral-200"
            }`}
          >
            {wl(w)}
          </button>
        ))}
      </div>

      <div className="h-80">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={chart} layout="vertical" margin={{ left: 8, right: 48 }}>
            <CartesianGrid stroke={INK.grid} horizontal={false} />
            <XAxis
              type="number"
              {...AXIS}
              domain={[(lo: number) => Math.min(lo * 1.3, -0.01), (hi: number) => Math.max(hi * 1.3, 0.01)]}
              tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`} />
            <YAxis type="category" dataKey="name" width={170} {...AXIS} />
            <ReferenceLine x={0} stroke={INK.axis} />
            <Bar dataKey="v" fill={SERIES[0]} isAnimationActive={false}>
              <LabelList
                dataKey="v"
                position="right"
                formatter={(v: number) => signedPct(v, 1)}
                style={{ fill: INK.muted, fontSize: 10 }}
              />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      <GroupTable rep={rep} groups={groups} gl={gl} wl={wl} />
    </div>
  );
}

function GroupTable({
  rep,
  groups,
  gl,
  wl,
}: {
  rep: FlowReport;
  groups: FlowGroup[];
  gl: (g: string | null) => string;
  wl: (w: FlowWindow) => string;
}) {
  const { tr } = useLang();
  const [open, setOpen] = useState<string | null>(null);
  const byGroup = (g: string | null): FlowAsset[] => rep.assets.filter((a) => a.group === g);

  const row = (key: string, label: string, r: Record<string, unknown>, sub = false, rot?: number | null) => (
    <tr
      key={key}
      className={`border-t border-neutral-800 ${sub ? "text-neutral-400" : "cursor-pointer hover:bg-neutral-800/40"}`}
      onClick={sub ? undefined : () => setOpen(open === key ? null : key)}
    >
      <td className={`py-1 pr-2 ${sub ? "pl-5 text-xs" : ""}`}>{label}</td>
      {rep.windows.map((w) => (
        <td key={w} className={`py-1 text-right font-mono ${sub ? "text-xs" : ""} ${signClass(val(r, `ret_${w}`))}`}>
          {signedPct(val(r, `ret_${w}`), 1)}
        </td>
      ))}
      <td className="py-1 text-right font-mono text-xs text-neutral-400">
        {rot === undefined ? "" : isNum(rot) ? (rot > 0 ? `↑${rot}` : rot < 0 ? `↓${-rot}` : "=") : DASH}
      </td>
    </tr>
  );

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-xs text-neutral-500">
            <th className="py-1 text-left font-normal">{tr("Group (click to open)", "Grupo (clic para abrir)")}</th>
            {rep.windows.map((w) => (
              <th key={w} className="py-1 text-right font-normal">{wl(w)}</th>
            ))}
            <th
              className="py-1 text-right font-normal"
              title={tr(
                "Rank at 13 weeks minus rank at 4: ↑ = the group is climbing",
                "Puesto a 13 semanas menos puesto a 4: ↑ = el grupo viene subiendo",
              )}
            >
              {tr("rotation", "rotación")}
            </th>
          </tr>
        </thead>
        <tbody>
          {groups.flatMap((g) => [
            row(g.group, gl(g.group), g, false, g.rotation),
            ...(open === g.group
              ? byGroup(g.group).map((a) => row(`${g.group}-${a.ticker}`, `${a.ticker} · ${a.label}`, a, true))
              : []),
          ])}
          {byGroup(null).map((a) => row(`ref-${a.ticker}`, `${a.ticker} (${tr("reference", "referencia")})`, a, true))}
        </tbody>
      </table>
    </div>
  );
}
