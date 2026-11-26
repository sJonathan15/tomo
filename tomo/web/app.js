// Tomo (友) — copiloto de reuniones de MIRAI. Lee el motor local cada segundo.
// La vista en vivo es una «cabina» que se arma UNA vez y luego solo actualiza cada región cuando su HTML cambia:
// así no se pierde lo que escribes en el compositor ni la selección de la transcripción.
"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const vista = $("#vista");
let estado = null;          // último /api/estado
let modo = "";              // qué esqueleto hay pintado (cabina:<id>, inicio, historial…)
let cache = {};             // región → último HTML puesto
let elegida = null;         // pregunta en grande
let ejElegido = null;       // ejercicio abierto
let confirmarFin = false;
let cacheHist = { t: 0, datos: [] };
let monitores = null;       // /api/monitores
let verMiniaturas = false;
// transcripción completa (se trae por partes con /api/lineas?desde=N)
let tr = { id: "", lineas: [], cargando: false, estatica: false };
const marcadas = new Set();
let ultimaMarcada = null;
let buscar = "";
// compositor
let adjuntos = [];          // data URLs
let citas = [];             // textos marcados de la clase

const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const ESTADOS = { cargando: "Preparando el oído", escuchando: "Escuchando", pausado: "En pausa", resumiendo: "Escribiendo la nota", listo: "Guardada", error: "Con un problema" };
const plural = (n, p) => n === 1 ? p : p + "s";
const duracion = (m) => { m = m || 0; return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`; };
const hora = (t) => String(t || "").slice(0, 5);

async function api(ruta, cuerpo) {
  const op = cuerpo === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(cuerpo) };
  const r = await fetch(ruta, op);
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || `error ${r.status}`);
  return d;
}

function avisar(texto) {
  const a = $("#aviso");
  a.textContent = texto;
  a.classList.add("ver");
  clearTimeout(avisar.t);
  avisar.t = setTimeout(() => a.classList.remove("ver"), 2200);
}

function hace(seg) {
  if (seg < 60) return "hace un momento";
  const m = Math.round(seg / 60);
  return m < 60 ? `hace ${m} min` : `hace ${Math.floor(m / 60)} h ${m % 60} min`;
}

function fechaLarga(iso) {
  const d = new Date(iso);
  return d.toLocaleDateString("es-EC", { weekday: "long", day: "numeric", month: "long" }) + ", " +
         d.toLocaleTimeString("es-EC", { hour: "2-digit", minute: "2-digit" });
}

function region(id, html) {
  const el = document.getElementById(id);
  if (!el || cache[id] === html) return;
  cache[id] = html;
  el.innerHTML = html;
}

async function copiar(texto, msj = "Copiado") {
  try { await navigator.clipboard.writeText(texto); avisar(msj); } catch { avisar("No se pudo copiar"); }
}

// ------------------------------------------------------------------ Markdown mínimo (respuestas de Tomo)
function inline(t) {
  return esc(t).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<i>$2</i>");
}

function md(texto) {
  const partes = String(texto || "").split(/```[\w-]*\n?/);
  let html = "";
  partes.forEach((p, i) => {
    if (i % 2 === 1) {
      html += `<div class="bloque"><pre><code>${esc(p.replace(/\n$/, ""))}</code></pre>
        <button class="btn chico primario" data-copiar-bloque>Copiar</button></div>`;
      return;
    }
    let lista = null;
    const cerrar = () => { if (lista) { html += `</${lista}>`; lista = null; } };
    p.split("\n").forEach((l) => {
      const ul = l.match(/^\s*[-*•]\s+(.*)/), ol = l.match(/^\s*\d+[.)]\s+(.*)/), h = l.match(/^#{1,4}\s+(.*)/);
      if (ul || ol) {
        const tipo = ul ? "ul" : "ol";
        if (lista !== tipo) { cerrar(); html += `<${tipo}>`; lista = tipo; }
        html += `<li>${inline((ul || ol)[1])}</li>`;
      } else if (h) { cerrar(); html += `<h4>${inline(h[1])}</h4>`; }
      else if (l.trim()) { cerrar(); html += `<p>${inline(l)}</p>`; }
      else cerrar();
    });
    cerrar();
  });
  return html;
}

// ------------------------------------------------------------------ piezas
function piezaRitmo(s) {
  const r = s.ritmo || [];
  const max = Math.max(1, ...r);
  const paso = s.ritmo_paso || 60;
  const total = Math.max(1, r.length) * paso;
  const barras = r.map((n) => n ? `<div class="barra" style="height:${Math.max(8, (n / max) * 100)}%" title="${n} palabras en ${paso} s"></div>`
                                 : `<div class="barra cero"></div>`).join("") || `<div class="barra cero"></div>`;
  const qs = (s.preguntas || []).map((q) => `<button class="marca-q" style="left:${Math.min(99, (q.s / total) * 100)}%"
      data-q="${esc(q.id)}" title="${esc(hora(q.t))} · ${esc(q.cual)}" aria-label="Pregunta de las ${esc(hora(q.t))}"></button>`).join("");
  const ps = (s.marcas_p || []).map((m) => `<span class="marca-p" style="left:${Math.min(99, (m / total) * 100)}%"></span>`).join("");
  const vivo = ["escuchando", "pausado", "cargando"].includes(s.estado);
  return `<span class="num">${esc((s.inicio || "").slice(11, 16))}</span><div class="barras" aria-label="Ritmo de la clase">${barras}${qs}${ps}</div>
    <span class="num">${vivo ? "ahora" : esc((s.fin || "").slice(11, 16))}</span>`;
}

function hallazgo(s) {
  const q = (s.preguntas || [])[0];
  if (s.estado === "resumiendo") return "Escribiendo la nota con lo aprendido…";
  if (s.estado === "error") return `Algo falló: ${esc(s.error)}`;
  if (s.estado === "cargando") return "Cargando el oído, unos segundos…";
  if (q) return `Última pregunta ${hace(s.seg - q.s)}: <b>${esc(q.cual)}</b>`;
  return "Aún sin preguntas. Te aviso con un globito cuando aparezca una.";
}

function piezaCab(s, enVivo) {
  const nq = (s.preguntas || []).length;
  const acciones = enVivo ? `<div class="acciones">
      <button class="btn primario" data-accion="ayudame" ${s.estado !== "escuchando" ? "disabled" : ""}>Ayúdame ahora</button>
      <button class="btn" data-accion="${s.estado === "pausado" ? "reanudar" : "pausar"}">${s.estado === "pausado" ? "Reanudar" : "Pausar"}</button>
      <button class="btn peligro" data-accion="terminar">${confirmarFin ? "¿Seguro? Otra vez" : "Terminar"}</button></div>`
    : `<div class="acciones">${s.nota ? `<button class="btn" data-abrir="${esc(s.nota)}">Abrir la nota</button>` : ""}<a class="btn fantasma" href="#/historial">Volver</a></div>`;
  return `<div class="cab-tit"><h1 title="${esc(s.tema)}">${esc(s.tema || "Reunión sin tema")}</h1>
      <p class="bajada"><span class="pildora ${esc(s.estado)}">${ESTADOS[s.estado] || esc(s.estado)}</span>
        <span class="txt">${enVivo ? hallazgo(s) : esc(fechaLarga(s.inicio))}</span></p></div>
    <div class="cifras num"><span><b>${duracion(s.minutos)}</b></span><span><b>${s.n_lineas}</b>${plural(s.n_lineas, "frase")}</span>
      <span><b>${nq}</b>${plural(nq, "pregunta")}</span><span><b>${s.n_pantallas || 0}</b>${plural(s.n_pantallas || 0, "diapositiva")}</span></div>
    ${acciones}`;
}

function piezaPreguntas(s, enVivo) {
  const qs = s.preguntas || [];
  if (!qs.length) {
    return `<div class="caja-cab"><h2>Preguntas y qué responder</h2></div>
      <p class="vacio">${enVivo ? "Aún nadie ha preguntado nada. Cuando el expositor o la diapositiva pregunte algo aparece aquí con una respuesta lista. Si te preguntan directo, pulsa <b>Ayúdame ahora</b>."
        : "En esta reunión no hubo preguntas."}</p>`;
  }
  const q = qs.find((x) => x.id === elegida) || qs[0];
  const pensando = q.estado === "pensando";
  const cuerpo = pensando && !q.respuesta ? `<p class="pensando">Pensando qué responder…</p>`
    : `<p class="q-resp">${esc(q.respuesta)}</p>${pensando ? `<p class="pensando" style="margin-top:10px">${q.borrador ? "Borrador rápido; mejorándolo…" : "Preparando otra versión…"}</p>` : ""}`;
  const otras = qs.filter((x) => x.id !== q.id).map((x) =>
    `<li><button class="q-fila" data-q="${esc(x.id)}"><span class="h num">${esc(hora(x.t))}</span><span class="t">${esc(x.cual)}</span></button></li>`).join("");
  const puede = enVivo || estado?.sesion?.id === s.id;
  return `<div class="caja-cab"><h2>Preguntas y qué responder</h2><span class="chip num">${qs.length}</span></div>
    <article class="q-actual">
      <div class="q-meta num">${esc(hora(q.t))} · ${q.pedida ? "la pediste tú" : "detectada sola"}${q.pantalla ? " · con la diapositiva" : ""}</div>
      <p class="q-cual">${esc(q.cual)}</p>
      ${cuerpo}
      ${q.respuesta ? `<div class="q-acc">
        <button class="btn chico primario" data-copiar-q>Copiar</button>
        ${puede ? `<button class="btn chico" data-variante="corta" data-id="${esc(q.id)}" ${pensando ? "disabled" : ""}>Más corta</button>
        <button class="btn chico" data-variante="otra" data-id="${esc(q.id)}" ${pensando ? "disabled" : ""}>Otra respuesta</button>` : ""}
        <button class="btn chico fantasma" data-pregunta-a-ej="${esc(q.id)}">Llevar a ejercicio</button>
      </div>` : ""}
    </article>
    ${otras ? `<ul class="q-lista">${otras}</ul>` : ""}`;
}

function piezaPantalla(s, enVivo) {
  const p = s.pantalla;
  return `<div class="caja-cab"><h2>${enVivo ? "Última diapositiva entendida" : "En la pantalla"}</h2>${p ? `<span class="chip num">${esc(hora(p.t))}</span>` : ""}
      ${enVivo ? `<button class="btn chico fantasma der" data-accion="pantalla">Mirar ahora</button>` : ""}</div>
    ${p ? `<div class="diapo"><img src="/captura/${esc(s.id)}/${esc(p.imagen)}" alt="Captura: ${esc(p.titulo)}">
        <h3>${esc(p.titulo)}</h3><p>${esc(p.resumen)}</p></div>`
      : `<p class="vacio">Cada 20 s miro la pantalla de la reunión y, si cambió la diapositiva, la leo y la resumo aquí.</p>`}
`;
}

function piezaSelector(enVivo) {
  const mons = monitores?.monitores || [];
  const elegido = estado?.ajustes?.monitor || "";
  const selector = enVivo && mons.length > 1 ? `<div class="monitores">
      <div class="segmentos" role="radiogroup" aria-label="Pantalla de la reunión">
        ${mons.map((m) => `<button role="radio" aria-checked="${elegido === m.nombre}" data-monitor="${esc(m.nombre)}" title="${esc(m.etiqueta)} · ${esc(m.px)}">${m.interna ? "Portátil" : "Externa"} ${esc(m.px.split("×")[0])}</button>`).join("")}
        <button role="radio" aria-checked="${!elegido}" data-monitor="">Todas</button>
      </div>
      <button class="enlace" data-ver-miniaturas style="font-size:12.5px;margin-top:6px">${verMiniaturas ? "Ocultar vistas" : "¿Cuál es cuál? Ver vistas"}</button>
      ${verMiniaturas ? `<div class="miniaturas">${mons.map((m) => `<button data-monitor="${esc(m.nombre)}" aria-pressed="${elegido === m.nombre}">
          <img src="/api/monitor/${esc(m.nombre)}.jpg?t=${monitores.t}" alt="Vista de ${esc(m.etiqueta)}" loading="lazy"><span>${esc(m.etiqueta)}</span></button>`).join("")}</div>` : ""}
    </div>` : "";
  return selector;
}

function piezaNotas(s, enVivo) {
  const her = s.herramientas || [];
  const ideas = s.ideas || [];
  let cuerpo = her.length ? `<div class="etiquetas">${her.map((h) => `<span class="etq">${esc(h)}</span>`).join("")}</div>`
    : `<p class="vacio">Las herramientas, sitios y repos que aparezcan en las diapositivas se juntan aquí.</p>`;
  if (ideas.length) cuerpo += `<h3 style="margin:14px 0 6px;font-size:14px">Ideas para tus proyectos</h3><ul style="margin:0;padding-left:18px">${ideas.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>`;
  const pie = s.nota ? `<p style="margin:12px 0 0"><button class="enlace" data-abrir="${esc(s.nota)}">Abrir la nota</button></p>`
    : enVivo ? `<p class="vacio" style="margin-top:10px;font-size:13px">Al terminar escribo una nota con el resumen, las herramientas, ideas y preguntas.</p>` : "";
  return `<div class="caja-cab"><h2>Para tus notas</h2>${her.length ? `<span class="chip num">${her.length}</span>` : ""}</div>${cuerpo}${pie}`;
}

// ---- ejercicios
function htmlCompositor() {
  return `<section class="compositor" id="compositor" aria-label="Resolver un ejercicio">
    <h2>Resolver un ejercicio</h2>
    <p class="ayuda">Pega el enunciado o una captura (<kbd>Ctrl</kbd>+<kbd>V</kbd>), o arrastra la imagen aquí. Marca en «Lo que se dice» las reglas que dio el profe y súmalas.</p>
    <label class="saltar" for="cmp-texto">Enunciado</label>
    <textarea id="cmp-texto" placeholder="Ej.: Escriban en máximo 5 líneas un prompt para clasificar una incidencia…"></textarea>
    <div class="adjuntos" id="cmp-adj"></div>
    <div class="citas" id="cmp-citas"></div>
    <div class="comp-pie">
      <button class="btn chico fantasma" data-subir>Subir imagen</button>
      <input type="file" id="cmp-archivo" accept="image/*" multiple hidden>
      <button class="btn chico fantasma" data-usar-seleccion>Sumar lo marcado</button>
      <span class="der">
        <button class="btn chico" data-resolver="seguir" id="cmp-seguir" hidden>Pedir cambio al abierto</button>
        <button class="btn primario" data-resolver="nuevo">Resolver <kbd>Ctrl ↵</kbd></button>
      </span>
    </div>
  </section>`;
}

function pintarCompositor() {
  const adj = $("#cmp-adj");
  if (adj) adj.innerHTML = adjuntos.map((a, i) => `<div class="adj"><img src="${a}" alt="Imagen adjunta ${i + 1}"><button data-quitar-adj="${i}" aria-label="Quitar imagen">×</button></div>`).join("");
  const cit = $("#cmp-citas");
  if (cit) cit.innerHTML = citas.map((c, i) => `<div class="cita"><span>${esc(c)}</span><button data-quitar-cita="${i}" aria-label="Quitar cita">×</button></div>`).join("");
}

function ejerciciosDe(s) {
  return s ? (s.ejercicios || []) : (estado?.ejercicios_libres || []);
}

function piezaEjercicios(lista, sesId) {
  const seg = $("#cmp-seguir");
  if (!lista.length) {
    if (seg) seg.hidden = true;
    return `<p class="vacio" style="padding:4px 4px 0">Aquí aparece la solución, lista para copiar. Puedes pedirle cambios («más corto», «en 3 líneas») sobre la misma.</p>`;
  }
  const e = lista.find((x) => x.id === ejElegido) || lista[0];
  if (seg) seg.hidden = e.estado === "pensando";
  const carpeta = e.sesion || sesId || "ejercicios";
  const tabs = lista.length > 1 ? `<div class="ej-tabs">${lista.map((x) => `<button class="ej-tab" aria-pressed="${x.id === e.id}" data-ej="${esc(x.id)}" title="${esc(x.titulo)}">${esc(hora(x.t))} · ${esc(x.titulo)}</button>`).join("")}</div>` : "";
  const msjs = e.mensajes.map((m) => m.rol === "copiloto"
    ? `<article class="msj-cop ${m.error ? "error" : ""}">${md(m.texto)}${m.error ? "" : `<div class="msj-acc"><button class="btn chico" data-copiar-msj>Copiar todo</button></div>`}<template>${esc(m.texto)}</template></article>`
    : `<div class="msj-yo">${m.texto ? `<div class="txt">${esc(m.texto)}</div>` : ""}
        ${(m.imagenes || []).length ? `<div class="mini">${m.imagenes.map((n) => `<img src="/captura/${esc(carpeta)}/${esc(n)}" alt="Imagen del enunciado">`).join("")}</div>` : ""}
        ${(m.citas || []).length ? `<div class="nota">+ ${m.citas.length} ${plural(m.citas.length, "frase")} de la clase como reglas</div>` : ""}</div>`).join("");
  const pensando = e.estado === "pensando" ? `<div class="msj-cop"><p class="pensando" style="margin-bottom:10px">Resolviendo con lo que se dijo en clase…</p><div class="esqueleto"><i></i><i></i><i></i></div></div>` : "";
  return `${tabs}<div class="hilo">${msjs}${pensando}</div>`;
}

// ---- transcripción
function htmlTranscripcion(titulo) {
  return `<section class="tr" aria-label="${esc(titulo)}">
    <div class="tr-cab">
      <div class="fila"><h2>${esc(titulo)}</h2><span class="chip num" id="tr-n">0</span><span class="chip der" id="tr-fuente" hidden></span></div>
      <input class="buscar" id="tr-buscar" type="search" placeholder="Buscar en lo dicho… (ej.: líneas, campos, JSON)" value="${esc(buscar)}">
      <div class="fila"><span class="tr-pista">Clic en una frase para marcarla · Mayús+clic marca un tramo</span>
        <button class="btn chico fantasma der" data-copiar-todo>Copiar todo</button></div>
    </div>
    <ol class="tr-lista" id="tr-lista"></ol>
    <div class="tr-sel" id="tr-sel" hidden>
      <span class="cuantas" id="tr-cuantas"></span>
      <button class="btn chico" data-sel="copiar">Copiar</button>
      <button class="btn chico primario" data-sel="usar">Usar como reglas</button>
      <button class="btn chico fantasma" data-sel="limpiar">Quitar marcas</button>
    </div>
  </section>`;
}

function liLinea(l, i) {
  let txt = esc(l.texto);
  if (buscar) {
    const re = new RegExp(buscar.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
    txt = txt.replace(re, (m) => `<mark>${m}</mark>`);
  }
  const oculto = buscar && !l.texto.toLowerCase().includes(buscar.toLowerCase());
  return `<li class="linea ${l.quien === "Yo" ? "yo" : ""} ${marcadas.has(i) ? "marcada" : ""}" data-i="${i}" ${oculto ? "hidden" : ""}>
    <span class="h num">${esc(hora(l.t))}</span><span><span class="quien">${l.quien === "Yo" ? "Tú" : "Ellos"}</span><span class="txt">${txt}</span></span></li>`;
}

function pintarLineas(desde = 0) {
  const ol = $("#tr-lista");
  if (!ol) return;
  const abajo = ol.scrollHeight - ol.scrollTop - ol.clientHeight < 60;
  if (desde === 0) ol.innerHTML = "";
  ol.insertAdjacentHTML("beforeend", tr.lineas.slice(desde).map((l, k) => liLinea(l, desde + k)).join(""));
  if (!tr.lineas.length) ol.innerHTML = `<li class="vacio" style="padding:10px">En silencio. Lo que suene en la reunión aparece aquí.</li>`;
  $("#tr-n").textContent = tr.lineas.length;
  if (abajo && !buscar) ol.scrollTop = ol.scrollHeight;
}

async function traerLineas(s) {
  if (tr.estatica || tr.cargando) return;
  if (tr.id !== s.id) { tr = { id: s.id, lineas: [], cargando: false, estatica: false }; marcadas.clear(); pintarLineas(0); }
  if (s.n_lineas <= tr.lineas.length) return;
  tr.cargando = true;
  try {
    const d = await api(`/api/lineas?desde=${tr.lineas.length}`);
    if (d.id === tr.id && d.desde === tr.lineas.length) {
      const antes = tr.lineas.length;
      tr.lineas.push(...d.lineas);
      pintarLineas(antes === 0 ? 0 : antes);
    }
  } catch { /* motor ocupado */ }
  tr.cargando = false;
}

function seleccionTexto() {
  return [...marcadas].sort((a, b) => a - b).map((i) => tr.lineas[i]).filter(Boolean);
}

function pintarSeleccion() {
  const n = marcadas.size;
  const barra = $("#tr-sel");
  if (!barra) return;
  barra.hidden = n === 0;
  $("#tr-cuantas").textContent = `${n} ${plural(n, "frase")} ${n === 1 ? "marcada" : "marcadas"}`;
}

// ------------------------------------------------------------------ vistas
function armarCabina(s, enVivo) {
  modo = `cabina:${s.id}:${enVivo}`;
  cache = {};
  vista.innerHTML = `<div class="cabina">
    <header class="cab" id="r-cab"></header>
    <div class="ritmo" id="r-ritmo"></div>
    <div class="columnas">
      <div class="columna col-q">
        <section class="caja" id="r-preg"></section>
        <section class="caja" id="r-pant"></section>
        <section class="caja" id="r-cer"></section>
      </div>
      <div class="columna col-ej">
        ${enVivo ? htmlCompositor() : `<div class="caja-cab" style="margin:4px 4px 0"><h2>Ejercicios resueltos</h2></div>`}
        <div id="r-ej"></div>
      </div>
      <div class="columna col-tr">
        ${enVivo ? `<section class="caja vivo-ojo" aria-label="Lo que ve Tomo ahora">
          <div class="caja-cab"><h2>Lo que ve ahora</h2><span class="en-vivo">en vivo</span>
            <span class="chip der" id="vivo-cual"></span></div>
          <div id="r-vivo-sel"></div>
          <div class="vivo-marco"><img id="vivo-img" alt="Pantalla de la reunión en vivo"><p class="vacio" id="vivo-vacio">Conectando con la pantalla…</p></div>
        </section>` : ""}
        ${htmlTranscripcion(enVivo ? "Lo que se dice" : "Transcripción")}</div>
    </div>
  </div>`;
  pintarCompositor();
  pintarLineas(0);
  pintarSeleccion();
  if (enVivo) refrescarVivo();
}

// ---- lo que ve el copiloto, en vivo (una imagen cada ~5 s; se carga aparte y se cambia sin parpadeo)
let vivoCargando = false;
function refrescarVivo() {
  const img = $("#vivo-img");
  if (!img || vivoCargando || document.hidden || estado?.ajustes?.pantalla === false) return;
  vivoCargando = true;
  const nueva = new Image();
  nueva.onload = () => { img.src = nueva.src; img.classList.add("lista"); $("#vivo-vacio").hidden = true; vivoCargando = false; };
  nueva.onerror = () => { $("#vivo-vacio").textContent = "No pude capturar la pantalla."; vivoCargando = false; };
  nueva.src = `/api/vivo.jpg?t=${Date.now()}`;
  const mons = monitores?.monitores || [];
  const el = estado?.ajustes?.monitor;
  const cual = $("#vivo-cual");
  if (cual) cual.textContent = el ? (mons.find((m) => m.nombre === el)?.etiqueta || el) : "todas las pantallas";
}
setInterval(refrescarVivo, 5000);

function actualizarCabina(s, enVivo) {
  region("r-cab", piezaCab(s, enVivo));
  region("r-ritmo", piezaRitmo(s));
  region("r-preg", piezaPreguntas(s, enVivo));
  region("r-pant", piezaPantalla(s, enVivo));
  region("r-vivo-sel", piezaSelector(enVivo));
  region("r-cer", piezaNotas(s, enVivo));
  region("r-ej", piezaEjercicios(ejerciciosDe(s), s.id));
  const f = $("#tr-fuente");
  if (f && enVivo && s.fuente) {
    f.hidden = false;
    f.textContent = (s.fuente === "app directa" ? (s.apps_oidas ? "oyendo la reunión" : "esperando el audio de la app") : "oyendo la salida") + (s.microfono ? " + tu mic" : "");
  }
}

function bannerConfig() {
  return estado && estado.modelos_listos === false ? `<div class="banner s12" role="status"><div><b>Falta elegir un modelo de IA.</b>
    Sin él Tomo transcribe, pero no puede leer la pantalla ni responder. Toma 1 minuto.</div>
    <a class="btn primario chico" href="#/ajustes/modelos">Configurar</a></div>` : "";
}

function pintarVivo() {
  const s = estado?.sesion;
  if (!s || s.estado === "listo" || (s.estado === "error" && !s.n_lineas)) return pintarInicio(s);
  const enVivo = ["cargando", "escuchando", "pausado"].includes(s.estado);
  if (tr.estatica) tr = { id: "", lineas: [], cargando: false, estatica: false };
  if (modo !== `cabina:${s.id}:${enVivo}`) armarCabina(s, enVivo);
  actualizarCabina(s, enVivo);
  traerLineas(s);
  if (enVivo && !monitores) cargarMonitores();
}

async function cargarMonitores() {
  monitores = { monitores: [], t: 0 };
  try { monitores = { ...(await api("/api/monitores")), t: Date.now() }; } catch { monitores = null; }
  cache["r-pant"] = null;
}

function pintarInicio(ultima) {
  const terminada = ultima && ultima.estado === "listo" ? ultima : null;
  if (modo !== "inicio") {
    modo = "inicio";
    cache = {};
    vista.innerHTML = `<div class="pagina">
      <div class="s12" id="r-banner"></div>
      <header class="titulo s12" id="r-ini-tit"></header>
      <form class="empezar caja s12" id="form-empezar">
        <div class="campo"><label for="tema">¿De qué trata la reunión?</label>
          <input id="tema" type="text" placeholder="Curso de agentes de IA — sesión 3" autocomplete="off"></div>
        <button class="btn primario" style="height:46px;padding:0 26px" type="submit">Empezar a escuchar</button>
        <div class="interruptores">
          <label class="sw"><input type="checkbox" id="mic" ${estado?.ajustes?.microfono ? "checked" : ""}>
            <span>Oír también mi micrófono<small>para que la transcripción tenga lo que tú dices</small></span></label>
          <label class="sw"><input type="checkbox" id="pant" ${estado?.ajustes?.pantalla !== false ? "checked" : ""}>
            <span>Mirar la pantalla de la reunión<small>una captura por minuto, solo si cambió (elige la pantalla en Ajustes)</small></span></label>
        </div>
      </form>
      <div class="s7" style="display:flex;flex-direction:column;gap:12px">${htmlCompositor()}<div id="r-ej"></div></div>
      <section class="caja s5" id="r-ini-hist"></section>
    </div>`;
    $("#form-empezar").addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        await api("/api/ajustes", { pantalla: $("#pant").checked });
        estado = await api("/api/empezar", { tema: $("#tema").value, microfono: $("#mic").checked });
        pintar();
      } catch (err) { avisar(err.message); }
    });
    pintarCompositor();
    setTimeout(() => $("#tema")?.focus(), 0);
  }
  region("r-ini-tit", `<h1>${terminada ? "Reunión guardada en tus notas" : "Listo para tu próxima clase"}</h1>
    <p class="bajada">${terminada ? `<b>${esc(terminada.tema || "Reunión")}</b> · ${duracion(terminada.minutos)} · ${terminada.n_lineas} ${plural(terminada.n_lineas, "frase")}.
        ${terminada.nota ? `<button class="enlace" data-abrir="${esc(terminada.nota)}">Abrir la nota</button> ·` : ""}
        <a class="enlace" href="#/historial/${esc(terminada.id)}">Ver la reunión</a>`
      : "Escribe de qué trata y pulsa Empezar: escucho la reunión, te aviso de las preguntas, te ayudo con los ejercicios y al final guardo lo aprendido."}</p>`);
  const hist = cacheHist.datos;
  region("r-ini-hist", `<div class="caja-cab"><h2>Reuniones recientes</h2>${hist.length ? `<a class="enlace der" href="#/historial">Ver todas</a>` : ""}</div>
    ${hist.length ? `<ul class="q-lista" style="margin:0">${hist.slice(0, 6).map((h) => `<li><button class="q-fila" data-ir="#/historial/${esc(h.id)}">
      <span class="h num">${esc((h.inicio || "").slice(5, 10).replace("-", "/"))}</span><span class="t">${esc(h.tema || "Reunión sin tema")}</span></button></li>`).join("")}</ul>`
      : `<p class="vacio">Todavía no hay reuniones guardadas.</p>`}`);
  region("r-ej", piezaEjercicios(ejerciciosDe(null), ""));
  region("r-banner", bannerConfig());
}

const tablaHist = (filas) => `<table class="tabla"><thead><tr><th>Reunión</th><th>Cuándo</th><th class="n">Duración</th>
  <th class="n">Frases</th><th class="n">Preguntas</th><th>Nota</th></tr></thead><tbody>${filas}</tbody></table>`;

function filaHist(h) {
  return `<tr class="fila" data-ir="#/historial/${esc(h.id)}"><td class="tema">${esc(h.tema || "Reunión sin tema")}</td>
    <td class="gris">${esc(fechaLarga(h.inicio))}</td><td class="n">${duracion(h.minutos)}</td><td class="n">${h.n_lineas}</td>
    <td class="n">${h.n_preguntas}</td><td>${h.nota ? `<button class="enlace" data-abrir="${esc(h.nota)}">Abrir</button>` : `<span class="gris">—</span>`}</td></tr>`;
}

async function pintarHistorial() {
  const hist = await cargarHist(true);
  vista.innerHTML = `<div class="pagina"><header class="titulo s12"><h1>Historial</h1>
      <p class="bajada">${hist.length ? `${hist.length} reuniones escuchadas. Toca una para ver su transcripción, preguntas y ejercicios.` : "Aún no hay reuniones."}</p></header>
    <section class="caja s12">${hist.length ? tablaHist(hist.map(filaHist).join("")) : `<p class="vacio">Cuando termines tu primera reunión aparece aquí.</p>`}</section></div>`;
}

async function pintarReunion(id) {
  let d;
  try { d = await api(`/api/reunion/${encodeURIComponent(id)}`); } catch (e) { vista.innerHTML = `<div class="pagina"><p class="vacio s12">${esc(e.message)}</p></div>`; return; }
  tr = { id: d.id, lineas: d.lineas || [], cargando: false, estatica: true };
  marcadas.clear();
  armarCabina(d, false);
  modo = `/historial/${id}`;
  actualizarCabina(d, false);
}

// ---- modelos de IA (cadena de APIs + modelo local)
let modelosInfo = null;

async function refrescarModelos() {
  try { modelosInfo = await api("/api/modelos"); } catch { return; }
  region("r-modelos", piezaModelos(modelosInfo));
}

function piezaModelos(m) {
  const provs = m.proveedores || [];
  const ol = m.ollama || {};
  const loc = m.local || {};
  const desc = m.descarga || {};
  const vision = (ol.modelos || []).filter((x) => x.vision);
  const elegido = loc.vision || (vision.find((x) => !x.nombre.includes("ocr")) || {}).nombre || "";
  const cadena = provs.length ? `<ol class="cadena">${provs.map((p, i) => `<li class="eslabon ${p.activo === false ? "apagado" : ""}">
      <span class="orden num">${i + 1}</span>
      <div class="eslabon-info"><b>${esc(p.nombre)}</b>${p.agotado ? `<span class="etq-aviso">sin cupo, salta 10 min</span>` : ""}
        <small>${esc(p.vision || p.texto || "elige un modelo")} · clave ${esc(p.clave || "falta")}</small></div>
      <div class="eslabon-acc">
        <button class="btn chico fantasma" data-mod="mover" data-id="${esc(p.id)}" data-paso="-1" aria-label="Subir" ${i === 0 ? "disabled" : ""}>↑</button>
        <button class="btn chico fantasma" data-mod="mover" data-id="${esc(p.id)}" data-paso="1" aria-label="Bajar" ${i === provs.length - 1 ? "disabled" : ""}>↓</button>
        <button class="btn chico" data-mod="probar" data-id="${esc(p.id)}">Probar</button>
        <button class="btn chico fantasma" data-mod="borrar" data-id="${esc(p.id)}">Quitar</button>
      </div></li>`).join("")}
      <li class="eslabon local"><span class="orden">⌂</span><div class="eslabon-info"><b>Modelo local</b><small>${elegido ? esc(elegido) + " · si todas las APIs fallan" : "ninguno todavía"}</small></div></li></ol>`
    : `<p class="vacio">Aún no agregas ninguna API. ${elegido ? `Por ahora Tomo usa tu modelo local <b>${esc(elegido)}</b>.` : "Agrega una abajo o descarga un modelo local."}</p>`;
  let local;
  if (!ol.corriendo) {
    local = `<p class="vacio">${ol.instalado ? "Ollama está instalado pero apagado: ábrelo con <code>ollama serve</code> (o <code>systemctl start ollama</code>)." :
      `No encontré Ollama. Instálalo desde <a class="enlace" href="https://ollama.com/download" target="_blank" rel="noopener">ollama.com/download</a> para usar modelos sin internet.`}</p>`;
  } else {
    const r = ol.recomendado || {};
    const tiene = vision.some((x) => x.nombre === r.nombre);
    local = `${vision.length ? `<div class="radios" role="radiogroup" aria-label="Modelo local con visión">${vision.map((x) => `
        <label class="radio"><input type="radio" name="local-vision" value="${esc(x.nombre)}" ${x.nombre === elegido ? "checked" : ""}>
        <span>${esc(x.nombre)}<small>${x.tamano_gb} GB${x.nombre === r.nombre ? " · recomendado para tu equipo" : ""}</small></span></label>`).join("")}</div>`
      : `<p class="vacio">No tienes modelos con visión instalados.</p>`}
      ${tiene ? "" : `<div class="recomendado"><div><b>Recomendado para tu equipo: ${esc(r.nombre)}</b>
        <small>${esc(r.nota)} · ${r.vram_gb ? `${r.vram_gb} GB de video` : `sin tarjeta NVIDIA, ${r.ram_gb} GB de RAM`}</small></div>
        ${desc.estado === "descargando" ? `<div class="barra-desc" role="progressbar" aria-valuenow="${Math.round(desc.avance * 100)}" aria-valuemin="0" aria-valuemax="100"><i style="width:${Math.round(desc.avance * 100)}%"></i><span class="num">${Math.round(desc.avance * 100)} %</span></div>`
          : `<button class="btn primario chico" data-mod="descargar" data-modelo="${esc(r.nombre)}">Descargar</button>`}</div>`}
      ${desc.estado === "error" ? `<p class="error">No se pudo descargar: ${esc(desc.error)}</p>` : ""}`;
  }
  return `<h3 class="sub">Orden en que se usan</h3>${cadena}<h3 class="sub">Modelo local (Ollama)</h3>${local}`;
}

function htmlAgregarApi(plantillas) {
  const opts = Object.entries(plantillas || {}).map(([k, p]) => `<option value="${esc(k)}">${esc(p.nombre)}${p.gratis ? " · tiene plan gratis" : ""}</option>`).join("");
  return `<form class="agregar" id="form-api">
    <div class="fila2"><label>Proveedor<select id="api-tipo">${opts}</select></label>
      <label>Clave de API<input id="api-clave" type="password" autocomplete="off" placeholder="pega tu clave aquí"></label></div>
    <div class="fila2 otro" hidden><label>URL base<input id="api-url" placeholder="https://…/v1"></label>
      <label>Modelo<input id="api-modelo" placeholder="nombre del modelo (con visión si puede)"></label></div>
    <div class="comp-pie"><a class="enlace" id="api-donde" target="_blank" rel="noopener" href="#">¿Dónde consigo la clave?</a>
      <span class="der"><button class="btn primario" type="submit">Agregar y probar</button></span></div>
  </form>`;
}

async function pintarAjustes() {
  const a = estado?.ajustes || {};
  if (!monitores?.t) await cargarMonitores();
  try { modelosInfo = await api("/api/modelos"); } catch { modelosInfo = {}; }
  let si = {};
  try { si = await api("/api/sistema"); } catch { /* motor apagado */ }
  const mons = monitores?.monitores || [];
  cache = {};
  vista.innerHTML = `<div class="pagina"><header class="titulo s12"><h1>Ajustes</h1>
      <p class="bajada">Whisper (el oído) corre siempre en tu equipo. Leer la pantalla, responder y resolver ejercicios usa los modelos que elijas aquí: APIs en la nube, tu modelo local, o ambos en cadena.</p></header>
    <div class="s12">
      <div class="ajuste" id="modelos"><div><h3>Modelos de IA</h3><p>Tomo prueba las APIs en orden; si una se queda sin cupo pasa sola a la siguiente y, al final, a tu modelo local. Nunca se queda sin ojos.</p>
          <button class="btn chico" data-copiar-prompt style="margin-top:14px">Copiar prompt para tu agente</button>
          <p class="nota-peq">Pégalo en Claude Code, Codex o Gemini CLI y te guía para configurarlo.</p></div>
        <div class="opciones"><div id="r-modelos"></div><h3 class="sub">Agregar una API</h3>${htmlAgregarApi(modelosInfo.plantillas)}</div></div>
      <div class="ajuste"><div><h3>Tu perfil</h3><p>Opcional. Ayuda a que las respuestas suenen a ti y a que la nota final proponga ideas para tus proyectos.</p></div>
        <div class="opciones">
          <label class="campo-peq">Tu nombre<input data-ajuste="nombre" value="${esc(a.nombre)}" placeholder="Ana"></label>
          <label class="campo-peq">A qué te dedicas<input data-ajuste="rol" value="${esc(a.rol)}" placeholder="Estudiante de ingeniería de software"></label>
          <label class="campo-peq">Tus proyectos (uno por línea)<textarea data-ajuste="proyectos" rows="3" placeholder="App de turnos para la clínica&#10;Tesis de visión artificial">${esc(a.proyectos)}</textarea></label>
          <label class="campo-peq">Idioma de las reuniones<select data-ajuste="idioma">
            ${[["es", "Español"], ["en", "English"], ["pt", "Português"], ["auto", "Detectar solo"]].map(([v, t]) => `<option value="${v}" ${a.idioma === v ? "selected" : ""}>${t}</option>`).join("")}
          </select></label>
        </div></div>
      <div class="ajuste"><div><h3>Pantalla de la reunión</h3><p>Con varios monitores, elige en cuál está la videollamada: solo se captura esa (como cuando Zoom comparte una pantalla).</p>
          <p class="nota-peq">Escritorio: ${esc(si.escritorio || "?")} · captura con ${esc((si.capturas || []).join(", ") || "— falta instalar una —")}</p></div>
        <div class="opciones">
          ${mons.length ? `<div class="miniaturas grande">${mons.map((m) => `<button data-monitor="${esc(m.nombre)}" aria-pressed="${a.monitor === m.nombre}">
            <img src="/api/monitor/${esc(m.nombre)}.jpg?t=${monitores.t}" alt="Vista de ${esc(m.etiqueta)}" loading="lazy"><span>${esc(m.etiqueta)} · ${esc(m.px)}</span></button>`).join("")}
            <button data-monitor="" aria-pressed="${!a.monitor}" class="todas"><span>Todas las pantallas</span></button></div>` : `<p class="vacio">No pude leer las pantallas; se captura todo el escritorio.</p>`}
          <label class="sw"><input type="checkbox" data-ajuste="pantalla" ${a.pantalla !== false ? "checked" : ""}><span>Mirar la pantalla de la reunión<small>cada 20 s, solo se lee si cambió; apágalo si compartes algo privado</small></span></label>
        </div></div>
      <div class="ajuste"><div><h3>Qué escucha</h3><p>«Directo de la app» toma el audio de Zoom, Teams o Webex antes del volumen: funciona con audífonos y volumen bajo. Para reuniones en el navegador (Meet) usa «Salida de audio».</p>
          <p class="nota-peq">PipeWire: ${si.pipewire ? "listo" : "falta (pw-record, pw-link, pw-dump)"}</p></div>
        <div class="opciones">
          <select data-ajuste="fuente" aria-label="Fuente de audio">
            <option value="app" ${a.fuente !== "salida" ? "selected" : ""}>Directo de la app de reunión</option>
            <option value="salida" ${a.fuente === "salida" ? "selected" : ""}>Salida de audio (lo que suena)</option>
          </select>
          <label class="sw"><input type="checkbox" data-ajuste="microfono" ${a.microfono ? "checked" : ""}><span>Oír también mi micrófono<small>se aplica desde la próxima reunión</small></span></label>
          <label class="sw"><input type="checkbox" data-ajuste="globitos" ${a.globitos !== false ? "checked" : ""}><span>Avisos en el escritorio<small>cada pregunta aparece aunque la app esté cerrada</small></span></label>
        </div></div>
      <div class="ajuste"><div><h3>Oído (Whisper)</h3><p>${si.cuda ? "Tienes tarjeta NVIDIA: «large-v3-turbo» es el mejor equilibrio." : "Sin tarjeta NVIDIA corre en la CPU: «small» es rápido, «medium» entiende mejor pero tarda más."}</p></div>
        <div class="opciones"><select data-ajuste="modelo" aria-label="Modelo de Whisper">
          ${[["auto", `Automático (${si.whisper_sugerido || "según tu equipo"})`], ["large-v3-turbo", "large-v3-turbo"], ["large-v3", "large-v3"], ["medium", "medium"], ["small", "small"], ["base", "base"]]
            .map(([v, t]) => `<option value="${v}" ${(a.modelo || "auto") === v ? "selected" : ""}>${t}</option>`).join("")}
        </select></div></div>
      <div class="ajuste"><div><h3>Notas</h3><p>Al terminar, Tomo escribe una nota en Markdown. Si eliges una carpeta dentro de tu bóveda de Obsidian, se abre directo ahí.</p></div>
        <div class="opciones"><label class="campo-peq">Carpeta de notas<input data-ajuste="notas" value="${esc(a.notas)}" placeholder="${esc(si.notas || "~/Documentos/Tomo/Notas")}"></label></div></div>
      <p class="nota-peq s12">Tomo ${esc(si.version || "")} · un proyecto de MIRAI · datos en ${esc(si.datos || "")}</p>
    </div></div>`;
  region("r-modelos", piezaModelos(modelosInfo));
  ajustarFormApi();
  if (location.hash.includes("modelos")) $("#modelos")?.scrollIntoView();
}

function ajustarFormApi() {
  const tipo = $("#api-tipo")?.value;
  if (!tipo || !modelosInfo?.plantillas) return;
  const p = modelosInfo.plantillas[tipo];
  $(".agregar .otro").hidden = tipo !== "otro";
  const donde = $("#api-donde");
  donde.hidden = !p.clave_en;
  donde.href = p.clave_en || "#";
}

async function cargarHist(forzar) {
  if (forzar || Date.now() - cacheHist.t > 15000) {
    try { cacheHist = { t: Date.now(), datos: await api("/api/historial") }; } catch { /* motor apagado */ }
  }
  return cacheHist.datos;
}

// ------------------------------------------------------------------ ciclo
const ruta = () => location.hash.replace(/^#/, "") || "/";

function marcarNav() {
  const r = ruta();
  const v = r.startsWith("/historial") ? "historial" : r.startsWith("/ajustes") ? "ajustes" : "vivo";
  $$(".nav").forEach((a) => a.dataset.vista === v ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current"));
}

async function pintar() {
  marcarNav();
  const r = ruta();
  if (r === "/") {
    const s = estado?.sesion;
    if (!s || s.estado === "listo") await cargarHist();
    return pintarVivo();
  }
  if (modo === r) return;
  modo = r;
  cache = {};
  if (r === "/historial") return pintarHistorial();
  if (r.startsWith("/historial/")) return pintarReunion(r.split("/")[2]);
  if (r.startsWith("/ajustes")) return pintarAjustes();
}

// ------------------------------------------------------------------ novedades (al estrenar versión) y actualizaciones
const VISTO = "tomo-version-vista";
let novMostradas = false;

function hojaNovedades(lista, { titulo, version, actualizar } = {}) {
  cerrarNovedades();
  const mejoras = lista.flatMap((n) => n.mejoras || []);
  const chispas = Array.from({ length: 14 }, (_, i) => `<i style="left:${(i * 37) % 100}%;top:${(i * 53) % 100}%;animation-delay:${(i % 7) * .35}s"></i>`).join("");
  const v = document.createElement("div");
  v.className = "velo";
  v.innerHTML = `<section class="hoja-nov" role="dialog" aria-modal="true" aria-labelledby="nov-titulo">
    <div class="cielo">${chispas}<span class="kanji" lang="ja">友</span></div>
    <div class="cuerpo">
      <div class="version">${actualizar ? "Nueva versión" : "Versión"} ${esc(version)}</div>
      <h2 id="nov-titulo">${esc(titulo || lista[0]?.titulo || "Novedades")}</h2>
      <p class="fecha">${esc(lista[0]?.fecha || "")}${actualizar ? " · se instala en segundos y tu reunión se retoma sola" : ""}</p>
      <div id="nov-progreso"></div>
      <ul class="mejoras">${mejoras.map((m, i) => `<li style="animation-delay:${.25 + i * .09}s"><span class="ico" aria-hidden="true">${esc(m.icono || "✨")}</span>
        <div><b>${esc(m.titulo)}</b><span>${esc(m.texto)}</span></div></li>`).join("")}</ul>
      <div class="pie">${actualizar ? `<button class="btn fantasma" data-nov="cerrar">Más tarde</button><button class="btn primario" data-nov="actualizar">Actualizar ahora</button>`
        : `<button class="btn primario" data-nov="cerrar">Entendido</button>`}</div>
    </div></section>`;
  document.body.appendChild(v);
  v.querySelector(".pie .btn:last-child").focus();
  v.addEventListener("click", (e) => { if (e.target === v) cerrarNovedades(); });
}

function cerrarNovedades() { document.querySelector(".velo")?.remove(); }
document.addEventListener("keydown", (e) => { if (e.key === "Escape") cerrarNovedades(); });

const vt = (v) => String(v || "0").split(".").map((x) => +x || 0).reduce((a, x) => a * 1000 + x, 0);

async function mostrarNovedadesSiToca() {
  if (novMostradas || !estado?.version) return;
  novMostradas = true;
  const visto = localStorage.getItem(VISTO);
  if (visto === estado.version) return;
  try {
    const d = await api("/api/novedades");
    const nuevas = d.locales.filter((n) => vt(n.version) > vt(visto) && vt(n.version) <= vt(d.version));
    if (nuevas.length) hojaNovedades(nuevas, { version: d.version, titulo: visto ? "Tomo se actualizó" : nuevas[0].titulo });
  } catch { /* sin novedades */ }
  localStorage.setItem(VISTO, estado.version);
}

function pintarPildoraAct() {
  const b = $("#pildora-act");
  if (!b) return;
  const v = estado?.actualizacion;
  b.hidden = !v;
  if (v) b.textContent = estado.act_estado === "actualizando" ? "Actualizando…" : `Nueva versión ${v}`;
}

// la app se recarga sola cuando cambian sus archivos (si no estás escribiendo o marcando algo)
let versionWeb = null;
function revisarVersion() {
  const v = estado?.web;
  if (!v) return;
  if (versionWeb === null) { versionWeb = v; return; }
  if (v === versionWeb) return;
  const ocupado = ($("#cmp-texto")?.value || "").trim() || adjuntos.length || citas.length || marcadas.size ||
    ["INPUT", "TEXTAREA"].includes(document.activeElement?.tagName);
  if (!ocupado) location.reload();
  else if (!revisarVersion.dicho) { revisarVersion.dicho = true; avisar("Hay una versión nueva: se recarga cuando termines (o pulsa F5)"); }
}

async function sondear() {
  try {
    estado = await api("/api/estado");
    revisarVersion();
    pintarPildoraAct();
    mostrarNovedadesSiToca();
    $("#servicio").textContent = "motor en línea";
    $("#servicio").className = "servicio ok";
  } catch {
    $("#servicio").textContent = "motor apagado";
    $("#servicio").className = "servicio mal";
  }
  pintar();
  if (ruta().startsWith("/ajustes") && modelosInfo?.descarga?.estado === "descargando") refrescarModelos();
}

function refrescar() { cache = {}; pintar(); }

// ------------------------------------------------------------------ compositor: imágenes y envío
async function aDataURL(archivo) {
  const bmp = await createImageBitmap(archivo);
  const k = Math.min(1, 1800 / Math.max(bmp.width, bmp.height));
  const c = document.createElement("canvas");
  c.width = Math.round(bmp.width * k); c.height = Math.round(bmp.height * k);
  c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
  return c.toDataURL("image/jpeg", 0.9);
}

async function sumarImagenes(archivos) {
  const imgs = [...archivos].filter((f) => f.type.startsWith("image/"));
  if (!imgs.length) return false;
  for (const f of imgs.slice(0, 4 - adjuntos.length)) adjuntos.push(await aDataURL(f));
  pintarCompositor();
  avisar(imgs.length > 1 ? "Imágenes añadidas" : "Imagen añadida");
  return true;
}

async function enviar(seguir) {
  const ta = $("#cmp-texto");
  const texto = ta?.value.trim() || "";
  if (!texto && !adjuntos.length) { avisar("Escribe el enunciado o pega una imagen"); ta?.focus(); return; }
  const lista = ejerciciosDe(estado?.sesion && ["cargando", "escuchando", "pausado"].includes(estado.sesion.estado) ? estado.sesion : null);
  const abierto = (lista.find((x) => x.id === ejElegido) || lista[0])?.id;
  try {
    const d = await api("/api/resolver", { texto, imagenes: adjuntos, citas, sigue: seguir ? abierto : undefined });
    estado = d;
    ejElegido = d.ejercicio;
    ta.value = ""; adjuntos = []; citas = [];
    pintarCompositor();
    refrescar();
    avisar("Resolviendo…");
  } catch (err) { avisar(err.message); }
}

function usarSeleccion() {
  const sel = seleccionTexto().map((l) => l.texto);
  const nativa = String(getSelection() || "").trim();
  const nuevas = sel.length ? sel : nativa ? [nativa] : [];
  if (!nuevas.length) { avisar("Marca frases en «Lo que se dice» primero"); return; }
  citas.push(...nuevas.filter((c) => !citas.includes(c)));
  marcadas.clear();
  $$(".linea.marcada").forEach((li) => li.classList.remove("marcada"));
  pintarSeleccion();
  pintarCompositor();
  avisar(`${nuevas.length} ${plural(nuevas.length, "frase")} como reglas del ejercicio`);
}

// ------------------------------------------------------------------ eventos
document.addEventListener("click", async (e) => {
  const li = e.target.closest(".linea");
  if (li && String(getSelection() || "").trim() === "") {
    const i = +li.dataset.i;
    if (e.shiftKey && ultimaMarcada !== null) {
      const [a, b] = [Math.min(ultimaMarcada, i), Math.max(ultimaMarcada, i)];
      for (let k = a; k <= b; k++) marcadas.add(k);
    } else if (marcadas.has(i)) marcadas.delete(i); else marcadas.add(i);
    ultimaMarcada = i;
    $$(".linea").forEach((x) => x.classList.toggle("marcada", marcadas.has(+x.dataset.i)));
    pintarSeleccion();
    return;
  }
  const b = e.target.closest("button,[data-ir]");
  if (!b) return;
  const ds = b.dataset;
  try {
    if (ds.accion) {
      const a = ds.accion;
      if (a === "terminar" && !confirmarFin) { confirmarFin = true; setTimeout(() => { confirmarFin = false; refrescar(); }, 4000); return refrescar(); }
      confirmarFin = false;
      estado = await api(`/api/${a}`, {});
      if (a === "ayudame") avisar("Buscando qué responder…");
      if (a === "pantalla") avisar("Mirando la pantalla…");
      refrescar();
    } else if ("copiarQ" in ds) {
      copiar(b.closest(".q-actual").querySelector(".q-resp")?.textContent || "", "Respuesta copiada");
    } else if ("copiarBloque" in ds) {
      copiar(b.closest(".bloque").querySelector("code").textContent, "Solución copiada");
    } else if ("copiarMsj" in ds) {
      const t = document.createElement("textarea");
      t.innerHTML = b.closest(".msj-cop").querySelector("template").innerHTML;
      copiar(t.value, "Respuesta copiada");
    } else if (ds.variante) {
      estado = await api("/api/respuesta", { id: ds.id, variante: ds.variante });
      elegida = ds.id; refrescar();
    } else if (ds.q) {
      elegida = ds.q; refrescar();
    } else if (ds.ej) {
      ejElegido = ds.ej; refrescar();
    } else if (ds.preguntaAEj) {
      const q = (estado?.sesion?.preguntas || []).find((x) => x.id === ds.preguntaAEj);
      const ta = $("#cmp-texto");
      if (q && ta) { ta.value = q.cual; ta.focus(); }
    } else if ("monitor" in ds) {
      estado = await api("/api/ajustes", { monitor: ds.monitor });
      avisar(ds.monitor ? "Listo: solo miro esa pantalla" : "Miro todas las pantallas");
      setTimeout(refrescarVivo, 3200);
      if (ruta() === "/ajustes") { modo = ""; pintar(); } else refrescar();
    } else if ("verMiniaturas" in ds) {
      verMiniaturas = !verMiniaturas;
      if (verMiniaturas && monitores) monitores.t = Date.now();
      refrescar();
    } else if ("subir" in ds) {
      $("#cmp-archivo").click();
    } else if ("usarSeleccion" in ds) {
      usarSeleccion();
    } else if (ds.resolver) {
      enviar(ds.resolver === "seguir");
    } else if (ds.quitarAdj) {
      adjuntos.splice(+ds.quitarAdj, 1); pintarCompositor();
    } else if (ds.quitarCita) {
      citas.splice(+ds.quitarCita, 1); pintarCompositor();
    } else if (ds.sel) {
      const sel = seleccionTexto();
      if (ds.sel === "copiar") copiar(sel.map((l) => `[${hora(l.t)}] ${l.texto}`).join("\n"), `${sel.length} ${plural(sel.length, "frase")} copiadas`);
      if (ds.sel === "usar") usarSeleccion();
      if (ds.sel === "limpiar") { marcadas.clear(); $$(".linea.marcada").forEach((x) => x.classList.remove("marcada")); pintarSeleccion(); }
    } else if (ds.nov === "cerrar") {
      cerrarNovedades();
    } else if (ds.nov === "actualizar") {
      $("#nov-progreso").innerHTML = `<div class="progreso" aria-label="Actualizando"><i></i></div>`;
      b.disabled = true; b.textContent = "Actualizando…";
      await api("/api/actualizar", {});
      avisar("Actualizando; la app se recarga sola");
    } else if (b.id === "pildora-act") {
      const d = await api("/api/novedades");
      const a = d.actualizacion?.disponible;
      if (a) hojaNovedades(a.novedades, { version: a.version, titulo: a.novedades[0]?.titulo, actualizar: true });
    } else if (ds.mod) {
      if (ds.mod === "probar") {
        b.disabled = true; b.textContent = "Probando…";
        const r = await api("/api/modelos/probar", { id: ds.id });
        avisar(r.ok ? "Funciona: respondió bien" : `No funcionó: ${r.mensaje}`);
      } else if (ds.mod === "descargar") {
        modelosInfo = await api("/api/modelos/descargar", { modelo: ds.modelo });
        avisar("Descargando el modelo local…");
      } else if (ds.mod === "borrar" || ds.mod === "mover") {
        modelosInfo = await api(`/api/modelos/${ds.mod}`, { id: ds.id, paso: +ds.paso || 0 });
      }
      await refrescarModelos();
    } else if ("copiarPrompt" in ds) {
      const r = await api("/api/prompt-agente");
      copiar(r.prompt, "Prompt copiado: pégalo en tu agente");
    } else if ("copiarTodo" in ds) {
      copiar(tr.lineas.map((l) => `[${hora(l.t)}] ${l.quien}: ${l.texto}`).join("\n"), "Transcripción copiada");
    } else if (ds.abrir) {
      e.stopPropagation();
      await api("/api/abrir", { ruta: ds.abrir });
      avisar("Abriendo la nota…");
    } else if (ds.ir) {
      location.hash = ds.ir;
    }
  } catch (err) { avisar(err.message); }
});

document.addEventListener("input", (e) => {
  if (e.target.id === "tr-buscar") {
    buscar = e.target.value.trim();
    pintarLineas(0);
    const primera = $(".linea:not([hidden])");
    if (buscar && primera) primera.scrollIntoView({ block: "nearest" });
  }
});

document.addEventListener("keydown", (e) => {
  if (e.target.id === "cmp-texto" && e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); enviar(false); }
});

document.addEventListener("paste", async (e) => {
  if (!$("#compositor")) return;
  const archivos = [...(e.clipboardData?.files || [])];
  if (archivos.length && await sumarImagenes(archivos)) e.preventDefault();
});

document.addEventListener("submit", async (e) => {
  if (e.target.id !== "form-api") return;
  e.preventDefault();
  const tipo = $("#api-tipo").value, clave = $("#api-clave").value.trim();
  if (!clave) { avisar("Pega la clave de la API"); $("#api-clave").focus(); return; }
  const cuerpo = { tipo, api_key: clave };
  if (tipo === "otro") Object.assign(cuerpo, { base_url: $("#api-url").value.trim(), texto: $("#api-modelo").value.trim(), vision: $("#api-modelo").value.trim() });
  try {
    const r = await api("/api/modelos/proveedor", cuerpo);
    $("#api-clave").value = "";
    avisar("Guardada. Probando…");
    const prueba = await api("/api/modelos/probar", { id: r.id });
    avisar(prueba.ok ? "Lista: la API respondió" : `Guardada, pero no respondió: ${prueba.mensaje}`);
    estado = await api("/api/estado");
    await refrescarModelos();
  } catch (err) { avisar(err.message); }
});

document.addEventListener("change", async (e) => {
  if (e.target.id === "cmp-archivo") { await sumarImagenes(e.target.files); e.target.value = ""; return; }
  if (e.target.id === "api-tipo") { ajustarFormApi(); return; }
  if (e.target.name === "local-vision") {
    modelosInfo = await api("/api/modelos/local", { vision: e.target.value, texto: e.target.value });
    avisar("Modelo local elegido"); await refrescarModelos(); return;
  }
  const k = e.target.dataset.ajuste;
  if (!k) return;
  try {
    estado = await api("/api/ajustes", { [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
    avisar("Guardado");
  } catch (err) { avisar(err.message); }
});

["dragenter", "dragover"].forEach((t) => document.addEventListener(t, (e) => {
  const c = e.target.closest?.("#compositor");
  if (!c) return;
  e.preventDefault();
  c.classList.add("arrastrando");
}));
["dragleave", "drop"].forEach((t) => document.addEventListener(t, (e) => {
  const c = e.target.closest?.("#compositor");
  if (!c) return;
  e.preventDefault();
  c.classList.remove("arrastrando");
  if (t === "drop") sumarImagenes(e.dataTransfer.files);
}));

window.addEventListener("hashchange", () => { modo = ""; elegida = null; pintar(); vista.focus({ preventScroll: true }); });
sondear();
setInterval(sondear, 1000);
