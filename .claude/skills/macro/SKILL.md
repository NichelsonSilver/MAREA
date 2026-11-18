---
name: macro
description: Análisis macro completo de MAREA sobre lo que acaba de pasar en el mercado. Actualiza datos, mira el mercado entero por función (hacia dónde rotó el dinero), la liquidez, las huellas del orden mundial y el contexto geopolítico, mide qué tan raro fue un movimiento, y entrega una lectura con escenarios y probabilidades declaradas como juicio. Usar cuando Nichelson pregunte "qué pasó", "qué ves", "hacia dónde va el dinero", "en qué etapa estamos" o traiga un movimiento puntual (ej. "vi subir el dólar").
---

# /macro — qué pasó, hacia dónde va el dinero y qué puede venir

Argumentos: `$ARGUMENTS` (opcional). Puede traer una pregunta o un movimiento
que Nichelson vio ("el dólar fue el que más rindió"), un activo o un horizonte.
Si trae una afirmación, **primero se mide** y después se interpreta: se
confirma, se matiza o se corrige con números, y se dice cuál de las tres.

Contexto obligatorio: `CLAUDE.md` (regla 2, regla 12, M3 no viable) y
`docs/REESTRUCTURACION_MAREA.md`. Corre en el hilo principal porque necesita
búsqueda web; los agentes de capa no la tienen.

## 0. Principios que no se negocian

- **Cada cifra sale de un comando de este skill o de un artefacto.** Una cifra
  de memoria es un bug de la misma gravedad que un lookahead.
- **Tres niveles, siempre separados y rotulados** en la respuesta:
  1. **Medido**: sale de MAREA (con fecha del dato).
  2. **Narrado**: sale de prensa o de la web (con fuente enlazada). Peso bajo.
     Si contradice a lo medido, se dice y gana lo medido.
  3. **Juicio**: etapa del ciclo, escenarios y probabilidades. Se escribe como
     juicio. **Sin respaldo forward**: la M3 midió que el orden entre clases no
     se sostiene hacia adelante (IC +0,036, IC95% contiene 0), así que ningún
     escenario se presenta como pronóstico del modelo.
- **Nunca órdenes.** Inclinaciones entre clases de activo, y qué mirar.
- Si un movimiento que Nichelson vio **no** es el mayor del mercado completo,
  se dice (ej.: "el dólar fue 1º del universo macro, pero energía le ganó").

## 1. Datos al día (≈3 min)

En `backend/`:

    uv run python -m marea.data.ingest todo
    uv run python -m marea.monitor

Si alguna fuente falla, se sigue y se declara el hueco. No se rellena nada.

## 2. Lo medido, en cinco comandos

Correr con `PYTHONIOENCODING=utf-8` (la consola de Windows no imprime `−`).

| comando | qué responde |
|---|---|
| `uv run python -m marea.brief` | frescura, régimen de liquidez, fuerza relativa M3, cripto 4b, narrativa |
| `uv run python -m marea.flow` | **hacia dónde rotó el dinero**: 11 grupos por función (macro + 11 sectores + BTC/ETH) a 1/4/13/26/52 semanas, rotación y **apetito por riesgo con percentil histórico** |
| `uv run python -m marea.flow --rareza TICKER` | **qué tan raro** fue el movimiento de un activo: percentil contra su propio pasado estricto y z contra la volatilidad del año previo |
| `uv run python -m marea.world_order` | **huellas del orden mundial** en seis dimensiones (dólar, activo de reserva, costo del dinero, shock real, excepcionalismo de EE.UU., crecimiento global), con tendencia a 5 años contra fase a 13 semanas |
| `uv run python -m marea.events` | qué viene en el calendario (FRED, FOMC, vencimientos de Deribit) |

Lectura mínima de cada uno:

- **Flujo**: líderes y rezagados por ventana; grupos con rotación ≥ +3 o ≤ −3;
  apetito por riesgo con percentil ≤ 0,20 = rotación defensiva, ≥ 0,80 =
  rotación hacia riesgo. Separar lo que pasó **esta semana** de la **tendencia
  de 13 semanas**: suelen contar historias distintas.
- **Forma del movimiento**: ¿qué subió **junto** con qué? Dólar arriba con
  Tesoro y oro abajo = apretón de liquidez en dólares, no huida a la calidad.
  Dólar arriba con Tesoro arriba = refugio clásico. Escribir cuál es.
- **Rareza**: un percentil 0,90 pasa una vez cada diez ventanas; no es
  extremo. Extremo es ≤ 0,02 o ≥ 0,98, o |z| ≥ 2,5.
- **Orden mundial**: las dimensiones marcadas "contra tendencia" son las que
  hay que contar: ahí la fase corta desafía la estructura larga.
- **Liquidez**: régimen, desde cuándo, y qué componentes lo empujan.

## 3. Lo narrado (búsqueda web)

Dos o tres búsquedas `WebSearch` en la misma vuelta, con mes y año en la
consulta: qué movió la semana (bancos centrales, datos), geopolítica
(guerras, aranceles, petróleo, sanciones), y bancos centrales y oro. Se usa
`mode: "extended"` porque son hechos recientes. Reglas:

- Cada afirmación lleva su enlace. Si dos fuentes no coinciden, se dice.
- Se contrasta con lo medido: "la prensa dice X; los datos muestran Y".
- No se registra en `narrativa/registro.jsonl` salvo que Nichelson lo pida
  (el registro es para lo que él pega; ver `/noticia`).

## 4. La respuesta

Estructura fija, en español y sin relleno:

1. **Respuesta directa** a lo que preguntó o afirmó, en dos o tres frases y
   con cifras.
2. **Qué pasó**: el mercado completo por función (tabla corta: grupo y sus
   ventanas), la forma del movimiento y su rareza.
3. **Por qué, según los datos**: liquidez y costo del dinero.
4. **Contexto narrado**, con fuentes.
5. **Orden mundial: en qué etapa estamos.** Primero las huellas medidas
   (tendencia larga contra fase corta, por dimensión). Después la lectura de
   la etapa, **rotulada como juicio**.
6. **Escenarios (juicio, sin respaldo forward).** Tres, mutuamente
   excluyentes, con:
   - probabilidad subjetiva (suman 100%) y horizonte (semanas a meses);
   - qué clases de activo ganan y pierden en cada uno;
   - **disparadores medibles en MAREA** que lo confirmarían o lo refutarían
     (tasa real, NFCI, spread HY, VIX, stablecoins, Brent, USD/CLP, apetito
     por riesgo). Un escenario sin disparador medible no se escribe.
7. **Lente Chile**: USD/CLP, cobre, ECH, TPM.
8. **Qué mirar la próxima semana**, desde `marea.events` y los disparadores.
9. **Fuentes** (enlaces).

Al cerrar, **ofrecer** dos cosas, y no hacer ninguna sin un sí:

- guardar el análisis en `memos/AAAA-MM-DD-macro.md`;
- registrar los escenarios en el registro calificable (sección 5).

## 5. Registro calificable: el juicio se mide

Los escenarios de la sección 6 se escriben **para poder registrarse**. Así se
califica el juicio con el tiempo. El módulo es `marea.scenarios` y el archivo
`escenarios/registro.jsonl`, que es solo-agregar.

1. **Antes de escribir la sección 6**, correr `report` para ver qué
   pronósticos vencieron y cómo salieron, y contarlo en la respuesta.
2. Cada escenario lleva una **regla de resolución**: condiciones que tienen
   que cumplirse todas al horizonte. Métricas aceptadas:

   | métrica | qué mide |
   |---|---|
   | `fred:SID` | nivel al horizonte, point-in-time (`fred:VIXCLS`, `fred:BAMLH0A0HYM2`) |
   | `fred_chg:SID` | cambio en unidades desde el registro (pp en tasas) |
   | `fred_ret:SID` | cambio relativo desde el registro (`fred_ret:DCOILBRENTEU`) |
   | `ret:TICKER` | retorno total desde el registro (panel de flujo, BTC, ETH) |
   | `rel:A/B` | A contra B desde el registro (`rel:GLD/TLT`, `rel:EEM/SPY`) |
   | `riesgo:VENTANA` | apetito por riesgo de `marea.flow` al horizonte |

   Los escenarios tienen que ser **mutuamente excluyentes por sus
   condiciones**, no por su prosa: si dos pueden cumplirse a la vez, el caso
   queda ambiguo. Un escenario puede ser `residual` (sin condiciones: "ninguno
   de los otros"). Las probabilidades suman 1. El horizonte va de 1 semana a
   1 año.
3. Con el sí de Nichelson, escribir el JSON en el scratchpad y correr:

       uv run python -m marea.scenarios check --file esc.json   # valida y muestra los disparadores hoy
       uv run python -m marea.scenarios add --file esc.json     # registra

   El `id` y la hora los asigna el registro. El punto de partida es el día
   UTC anterior: lo del mismo día no cuenta.
4. Al vencer, el pronóstico se resuelve solo con los datos. Si quedó
   **ambiguo**, se puede resolver a mano con `resolve ID --scenario KEY
   --note "..."`. Lo que los datos resolvieron no se reescribe.

`report` entrega el Brier multiclase contra repartir parejo (1 − 1/k) y
cuántas veces acertó el escenario más probable. Mientras haya menos de 20
resueltos, no emite veredicto. Los análisis se solapan, así que el n efectivo
es menor. Hasta que haya veredicto, la sección 6 sigue rotulada como juicio.
