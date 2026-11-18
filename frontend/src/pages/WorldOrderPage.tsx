import { useQuery } from "@tanstack/react-query";

import { Badge, Card, ErrorBox, Loading } from "../components/common/Card";
import { useLang } from "../i18n";
import { api } from "../lib/api";
import { DASH, isNum } from "../lib/format";
import type { WorldDimension, WorldMetric } from "../types";

/**
 * MAREA — huellas de precio del orden mundial, económico y geopolítico.
 *
 * Un "cambio de orden" no se mide con un número; sus huellas en los precios
 * sí. Seis dimensiones, cada una con una métrica principal (la que decide la
 * lectura) y algunas de apoyo. Por dimensión: la **tendencia larga** (signo a
 * 5 años) y la **fase corta** (signo a 13 semanas); cuando difieren, la fase
 * corta va contra la tendencia, y eso es lo que conviene mirar.
 *
 * **No hay índice compuesto ni pronóstico.** En qué etapa está el orden
 * mundial es juicio, y lo declara como tal el skill `/macro`, no esta página.
 */

const DIM_LABEL: Record<string, { name: [string, string]; up: [string, string] }> = {
  dolar: {
    name: ["Dollar", "Dólar"],
    up: ["the dollar strengthens against trading partners", "el dólar se fortalece contra sus socios comerciales"],
  },
  reserva: {
    name: ["Reserve asset", "Activo de reserva"],
    up: ["gold beats Treasuries as a store of value", "el oro le gana al Tesoro como reserva de valor"],
  },
  costo_dinero: {
    name: ["Cost of money", "Costo del dinero"],
    up: ["money gets more expensive in real terms", "el dinero se encarece en términos reales"],
  },
  shock_real: {
    name: ["Real and geopolitical shock", "Shock real y geopolítico"],
    up: ["energy gets more expensive", "sube el costo de la energía"],
  },
  excepcionalismo: {
    name: ["US exceptionalism", "Excepcionalismo de EE.UU."],
    up: ["the rest of the world beats the US", "el resto del mundo le gana a EE.UU."],
  },
  crecimiento: {
    name: ["Global growth", "Crecimiento global"],
    up: ["the market pays for growth over protection", "el mercado paga crecimiento por sobre protección"],
  },
};

const METRIC_LABEL: Record<string, [string, string]> = {
  dolar_amplio: ["Broad dollar (FRED)", "Dólar amplio (FRED)"],
  eurusd: ["EUR in USD (up = stronger euro)", "Euro en dólares (sube = euro más fuerte)"],
  usdjpy: ["Yen per dollar", "Yenes por dólar"],
  usdcny: ["Yuan per dollar", "Yuanes por dólar"],
  oro_vs_tesoro: ["Gold vs long Treasury (GLD/TLT)", "Oro contra Tesoro largo (GLD/TLT)"],
  oro_vs_acciones: ["Gold vs S&P 500 (GLD/SPY)", "Oro contra S&P 500 (GLD/SPY)"],
  tasa_real: ["10-year real rate (TIPS)", "Tasa real 10 años (TIPS)"],
  tesoro_10a: ["10-year Treasury", "Tesoro 10 años"],
  tesoro_vs_fed: ["10-year minus fed funds", "Tesoro 10 años menos fed funds"],
  inflacion_esperada: ["10-year expected inflation", "Inflación esperada 10 años"],
  brent: ["Brent crude", "Petróleo Brent"],
  energia_vs_tecno: ["Energy vs technology (XLE/XLK)", "Energía contra tecnología (XLE/XLK)"],
  wti: ["WTI crude", "Petróleo WTI"],
  emergentes_vs_eeuu: ["Emerging vs S&P 500 (EEM/SPY)", "Emergentes contra S&P 500 (EEM/SPY)"],
  desarrollados_vs_eeuu: ["Developed ex-US vs S&P 500 (EFA/SPY)", "Desarrollados ex-EE.UU. (EFA/SPY)"],
  china_vs_eeuu: ["China vs S&P 500 (FXI/SPY)", "China contra S&P 500 (FXI/SPY)"],
  cobre_vs_oro: ["Copper vs gold (CPER/GLD)", "Cobre contra oro (CPER/GLD)"],
  vix: ["VIX", "VIX"],
};

const HORIZONS = ["13s", "1a", "3a", "5a"] as const;
const HORIZON_LABEL: Record<(typeof HORIZONS)[number], [string, string]> = {
  "13s": ["13 wk", "13 sem"],
  "1a": ["1 yr", "1 año"],
  "3a": ["3 yr", "3 años"],
  "5a": ["5 yr", "5 años"],
};

function change(m: WorldMetric, h: (typeof HORIZONS)[number]): string {
  const v = m[`chg_${h}`];
  if (!isNum(v)) return DASH;
  // El VIX no es una tasa: su cambio va en puntos del índice, no en puntos porcentuales.
  if (m.unit === "pp") return `${v > 0 ? "+" : ""}${v.toFixed(2)} ${m.key === "vix" ? "pts" : "pp"}`;
  return `${v > 0 ? "+" : ""}${(v * 100).toFixed(1)}%`;
}

export function WorldOrderPage() {
  const { tr } = useLang();
  const q = useQuery({
    queryKey: ["macro", "world-order"],
    queryFn: () => api.worldOrder(),
    retry: false,
    staleTime: 600_000,
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-neutral-100">{tr("World order", "Orden mundial")}</h1>
          <p className="text-xs text-neutral-500">
            {q.data
              ? tr(`as known on ${q.data.known_at} · price footprints`, `conocido al ${q.data.known_at} · huellas de precio`)
              : tr("price footprints of the economic and geopolitical order", "huellas de precio del orden económico y geopolítico")}
          </p>
        </div>
        <Badge
          tone="muted"
          title={tr(
            "Measured footprints; no composite index and no forecast. Which stage we are in is judgment.",
            "Huellas medidas; sin índice compuesto ni pronóstico. En qué etapa estamos es juicio.",
          )}
        >
          {tr("descriptive · no forecast", "descriptivo · sin pronóstico")}
        </Badge>
      </div>

      {q.isLoading && <Loading what={tr("the world order", "el orden mundial")} />}
      {q.error && <ErrorBox>{String(q.error)}</ErrorBox>}
      {q.data && (
        <>
          <Summary dims={q.data.dimensions} />
          <div className="grid gap-4 xl:grid-cols-2">
            {q.data.dimensions.map((d) => (
              <DimensionCard key={d.key} dim={d} />
            ))}
          </div>
          <Card title={tr("How to read it", "Cómo leerlo")}>
            <ul className="list-disc space-y-1 pl-5 text-xs text-neutral-400">
              <li>
                {tr(
                  "Levels (rates, dollar, Brent, VIX): the percentile is the share of the whole available history below today's value.",
                  "Niveles (tasas, dólar, Brent, VIX): el percentil es la fracción de toda la historia disponible por debajo del valor de hoy.",
                )}
              </li>
              <li>
                {tr(
                  "Ratios between ETFs: a ratio's level means nothing on its own, so the percentile is that of its 1-year change against the strict past.",
                  "Razones entre ETFs: el nivel de una razón no significa nada por sí solo, así que el percentil es el de su cambio a 1 año contra el pasado estricto.",
                )}
              </li>
              <li>
                {tr(
                  "Long trend = sign of the 5-year change of the headline metric; short phase = sign at 13 weeks. FRED is read as it was known today (vintages).",
                  "Tendencia larga = signo del cambio a 5 años de la métrica principal; fase corta = signo a 13 semanas. FRED se lee tal como se conocía hoy (vintages).",
                )}
              </li>
            </ul>
          </Card>
        </>
      )}
    </div>
  );
}

function trendWord(v: string | null, tr: (en: string, es: string) => string): string {
  if (v === "sube") return tr("up", "sube");
  if (v === "baja") return tr("down", "baja");
  if (v === "plano") return tr("flat", "plano");
  return DASH;
}

function Summary({ dims }: { dims: WorldDimension[] }) {
  const { lang, tr } = useLang();
  const i = lang === "es" ? 1 : 0;
  return (
    <Card title={tr("Trend and phase by dimension", "Tendencia y fase por dimensión")}>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-xs text-neutral-500">
            <th className="py-1 text-left font-normal">{tr("Dimension", "Dimensión")}</th>
            <th className="py-1 text-left font-normal">{tr("Up means", "Sube significa")}</th>
            <th className="py-1 text-right font-normal">{tr("5-yr trend", "Tendencia 5 años")}</th>
            <th className="py-1 text-right font-normal">{tr("13-wk phase", "Fase 13 sem")}</th>
          </tr>
        </thead>
        <tbody>
          {dims.map((d) => (
            <tr key={d.key} className="border-t border-neutral-800">
              <td className="py-1.5 text-neutral-200">{DIM_LABEL[d.key]?.name[i] ?? d.label}</td>
              <td className="py-1.5 text-xs text-neutral-400">{DIM_LABEL[d.key]?.up[i] ?? d.up_means}</td>
              <td className="py-1.5 text-right font-mono">{trendWord(d.reading.long_trend, tr)}</td>
              <td className="py-1.5 text-right font-mono">
                {trendWord(d.reading.short_phase, tr)}
                {d.reading.against_trend && (
                  <span className="ml-2 text-xs text-warn" title={tr("short phase against the long trend", "fase corta contra la tendencia larga")}>
                    {tr("against trend", "contra tendencia")}
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function DimensionCard({ dim }: { dim: WorldDimension }) {
  const { lang, tr } = useLang();
  const i = lang === "es" ? 1 : 0;
  const rows = [dim.headline, ...dim.support];
  return (
    <Card title={DIM_LABEL[dim.key]?.name[i] ?? dim.label}>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-neutral-500">
              <th className="py-1 text-left font-normal">{tr("Metric", "Métrica")}</th>
              <th className="py-1 text-right font-normal">{tr("Value", "Valor")}</th>
              {HORIZONS.map((h) => (
                <th key={h} className="py-1 text-right font-normal">{HORIZON_LABEL[h][i]}</th>
              ))}
              <th className="py-1 text-right font-normal" title={tr("See 'How to read it'", "Ver 'Cómo leerlo'")}>pct</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((m, k) => (
              <tr key={m.key} className="border-t border-neutral-800">
                <td className={`min-w-[11rem] py-1 pr-2 ${k === 0 ? "text-neutral-100" : "text-xs text-neutral-400"}`}>
                  {METRIC_LABEL[m.key]?.[i] ?? m.label}
                  {k === 0 && <span className="ml-1 text-[10px] text-neutral-500">({tr("headline", "principal")})</span>}
                </td>
                {m.error ? (
                  <td colSpan={HORIZONS.length + 2} className="py-1 text-xs text-neutral-500">{m.error}</td>
                ) : (
                  <>
                    <td className="whitespace-nowrap py-1 pl-3 text-right font-mono">{isNum(m.value) ? m.value.toFixed(2) : DASH}</td>
                    {HORIZONS.map((h) => (
                      <td key={h} className="whitespace-nowrap py-1 pl-3 text-right font-mono text-neutral-300">{change(m, h)}</td>
                    ))}
                    <td className="whitespace-nowrap py-1 pl-3 text-right font-mono text-neutral-300">{isNum(m.pct) ? m.pct.toFixed(2) : DASH}</td>
                  </>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-[11px] text-neutral-500">
        {tr("Data as of", "Dato al")} {dim.headline.as_of ?? DASH} · {tr("history since", "historia desde")} {dim.headline.since ?? DASH}
      </p>
    </Card>
  );
}
