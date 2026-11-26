// Tomo — asistente de bienvenida (primer uso), recorrido guiado por la app y apoyo en GitHub.
// Usa las utilidades globales de app.js: $, $$, api, esc, avisar, estado, copiar, ruta.
"use strict";

// ------------------------------------------------------------------ tema (sistema · claro · noche)
function aplicarTema(t) {
  if (t === "claro" || t === "noche") document.documentElement.dataset.tema = t;
  else delete document.documentElement.dataset.tema;
}

// ------------------------------------------------------------------ asistente de bienvenida
const PASOS = 5;
let paso = 1;
let datosSistema = null;
let datosModelos = null;
let bienvenidaAbierta = false;

async function abrirBienvenida(desde = 1) {
  if (bienvenidaAbierta) return;
  bienvenidaAbierta = true;
  paso = desde;
  try { [datosSistema, datosModelos] = await Promise.all([api("/api/sistema"), api("/api/modelos")]); } catch { /* motor ocupado */ }
  const v = document.createElement("div");
  v.className = "velo velo-bienvenida";
  v.innerHTML = `<section class="asistente" role="dialog" aria-modal="true" aria-labelledby="asis-titulo">
    <header class="asis-cab">
      <div class="asis-progreso" role="progressbar" aria-valuemin="0" aria-valuemax="100"><i></i></div>
      <span class="asis-paso num"></span>
      <button class="btn chico fantasma" data-asis="saltar">Saltar</button>
    </header>
    <div class="asis-cuerpo" id="asis-cuerpo"></div>
    <footer class="asis-pie">
      <button class="btn fantasma" data-asis="atras">Atrás</button>
      <span class="puntos" aria-hidden="true">${Array.from({ length: PASOS }, () => "<i></i>").join("")}</span>
      <button class="btn primario" data-asis="siguiente">Siguiente</button>
    </footer>
  </section>`;
  document.body.appendChild(v);
  pintarPaso();
}

function cerrarBienvenida() {
  document.querySelector(".velo-bienvenida")?.remove();
  bienvenidaAbierta = false;
}

function filaDep(ok, nombre, detalle) {
  return `<div class="dep" data-ok="${ok}"><span class="dep-punto" aria-hidden="true"></span><b>${esc(nombre)}</b><span>${detalle}</span></div>`;
}

function pintarPaso() {
  const c = $("#asis-cuerpo");
  if (!c) return;
  const a = estado?.ajustes || {};
  const si = datosSistema || {};
  const m = datosModelos || {};
  $(".asis-progreso i").style.width = `${(paso / PASOS) * 100}%`;
  $(".asis-paso").textContent = `Paso ${paso} de ${PASOS}`;
  $$(".asis-pie .puntos i").forEach((p, i) => p.classList.toggle("on", i < paso));
  $("[data-asis=atras]").style.visibility = paso > 1 ? "visible" : "hidden";
  $("[data-asis=siguiente]").textContent = paso === PASOS ? "Empezar a usar Tomo" : "Siguiente";
  let html = "";
  if (paso === 1) {
    html = `<div class="asis-hero"><span class="asis-kanji" lang="ja">友</span></div>
      <h2 id="asis-titulo">Hola, soy Tomo</h2>
      <p class="asis-desc">Tu compañero en reuniones y clases en línea: escucho, te aviso de las preguntas con una respuesta lista y te ayudo con los ejercicios. Configurémonos en un minuto.</p>
      <div class="asis-campos">
        <label class="campo-peq">¿Cómo te llamas?<input data-ajuste="nombre" value="${esc(a.nombre)}" placeholder="Opcional" autocomplete="given-name"></label>
        <label class="campo-peq">¿A qué te dedicas?<input data-ajuste="rol" value="${esc(a.rol)}" placeholder="Estudiante, docente, desarrollador…"></label>
        <label class="campo-peq">Idioma de tus reuniones<select data-ajuste="idioma">
          ${[["es", "Español"], ["en", "English"], ["pt", "Português"], ["auto", "Detectar solo"]].map(([v, t]) => `<option value="${v}" ${a.idioma === v ? "selected" : ""}>${t}</option>`).join("")}</select></label>
        <div class="campo-peq"><span>Apariencia</span><div class="segmentos" role="radiogroup" aria-label="Apariencia">
          ${[["sistema", "Como el sistema"], ["claro", "Claro"], ["noche", "Noche"]].map(([v, t]) => `<button role="radio" aria-checked="${(a.tema || "sistema") === v}" data-tema="${v}">${t}</button>`).join("")}
        </div></div>
      </div>`;
  } else if (paso === 2) {
    const ol = si.ollama || {};
    html = `<h2 id="asis-titulo">Revisé tu equipo</h2>
      <p class="asis-desc">Esto es lo que encontré. Lo marcado en amarillo trae la solución al lado.</p>
      <div class="deps">
        ${filaDep(!!si.pipewire, "Audio (PipeWire)", si.pipewire ? "listo para oír la reunión" : "falta: instala <code>pipewire</code> y vuelve a correr el instalador")}
        ${filaDep((si.capturas || []).length > 0, "Captura de pantalla", (si.capturas || []).length ? esc(si.capturas.join(", ")) + ((si.capturas || [])[0] === "portal" ? " · la primera vez tu escritorio te pedirá <b>Permitir</b>" : "") : "falta: instala python3-gi, grim, spectacle o maim")}
        ${filaDep(true, "Oído (Whisper)", si.cuda ? `tarjeta NVIDIA · usaré <b>${esc(si.whisper_sugerido)}</b>` : `sin tarjeta NVIDIA · usaré <b>${esc(si.whisper_sugerido || "small")}</b> en la CPU`)}
        ${filaDep(!!si.audio?.["notify-send"], "Avisos en el escritorio", si.audio?.["notify-send"] ? "listos" : "opcional: instala libnotify")}
        ${filaDep(!!ol.corriendo, "Modelos locales (Ollama)", ol.corriendo ? `${(ol.modelos || []).filter((x) => x.vision).length} con visión instalados` : (ol.instalado ? "instalado pero apagado" : "opcional: <a class='enlace' href='https://ollama.com/download' target='_blank' rel='noopener'>ollama.com</a>"))}
      </div>
      <div class="asis-carpeta"><span>Tus notas se guardan en</span><code>${esc(si.notas || "~/Documentos/Tomo/Notas")}</code><a class="enlace" href="#/ajustes" data-asis="cerrar">cambiar</a></div>`;
  } else if (paso === 3) {
    const ol = (datosModelos || {}).ollama || {};
    const vision = (ol.modelos || []).filter((x) => x.vision);
    const r = ol.recomendado || {};
    const listo = m.listo;
    html = `<h2 id="asis-titulo">Elige con qué piensa Tomo</h2>
      <p class="asis-desc">Para leer la pantalla y responder necesito un modelo de IA. Puedes usar los dos: una API en la nube y, si se queda sin cupo, tu modelo local.</p>
      <div class="opcion-ia">
        <div class="opcion"><span class="opcion-ico" aria-hidden="true">☁</span><div><b>Una API gratis</b><small>Google Gemini tiene plan gratis. Pega tu clave:</small>
          <div class="fila-clave"><input id="asis-clave" type="password" placeholder="clave de Gemini" autocomplete="off">
          <button class="btn chico primario" data-asis="clave">Guardar</button></div>
          <a class="enlace peq" href="https://aistudio.google.com/apikey" target="_blank" rel="noopener">Conseguir una clave gratis</a></div></div>
        <div class="opcion"><span class="opcion-ico" aria-hidden="true">⌂</span><div><b>Tu modelo local</b>
          <small>${!ol.corriendo ? "Instala Ollama para usar modelos sin internet." : vision.length ? `Usaré <b>${esc(vision[0].nombre)}</b> (ya lo tienes).` : `Recomendado para tu equipo: <b>${esc(r.nombre)}</b> · ${esc(r.nota || "")}`}</small>
          ${ol.corriendo && !vision.length ? `<button class="btn chico" data-asis="descargar" data-modelo="${esc(r.nombre)}">Descargar</button>` : ""}</div></div>
        <div class="opcion"><span class="opcion-ico" aria-hidden="true">✦</span><div><b>Que tu agente lo haga</b><small>Copia un prompt y pégalo en Claude Code, Codex o Gemini CLI: te guía paso a paso.</small>
          <button class="btn chico" data-copiar-prompt>Copiar prompt</button></div></div>
      </div>
      <p class="asis-estado ${listo ? "ok" : ""}">${listo ? "✓ Ya tengo un modelo listo." : "Puedes hacerlo luego en Ajustes › Modelos de IA."}</p>`;
  } else if (paso === 4) {
    const mons = si.monitores || [];
    html = `<h2 id="asis-titulo">¿Dónde está tu reunión?</h2>
      <p class="asis-desc">Si usas varios monitores, elige en cuál pones Zoom, Teams o Meet: solo miraré esa pantalla.</p>
      ${mons.length > 1 ? `<div class="miniaturas grande">${mons.map((x) => `<button data-monitor="${esc(x.nombre)}" aria-pressed="${a.monitor === x.nombre}">
          <img src="/api/monitor/${esc(x.nombre)}.jpg?t=${Date.now()}" alt="Vista de ${esc(x.etiqueta)}" loading="lazy"><span>${esc(x.etiqueta)}</span></button>`).join("")}
          <button data-monitor="" aria-pressed="${!a.monitor}" class="todas"><span>Todas</span></button></div>`
        : `<p class="asis-estado ok">✓ Tienes una sola pantalla: miraré esa.</p>`}
      <div class="asis-campos" style="margin-top:16px">
        <label class="campo-peq">¿De dónde escucho?<select data-ajuste="fuente">
          <option value="app" ${a.fuente !== "salida" ? "selected" : ""}>Directo de Zoom, Teams o Webex (recomendado)</option>
          <option value="salida" ${a.fuente === "salida" ? "selected" : ""}>Lo que suena en mis audífonos (Meet en el navegador)</option></select></label>
        <label class="sw"><input type="checkbox" data-ajuste="microfono" ${a.microfono ? "checked" : ""}><span>Oír también mi micrófono<small>para que la nota incluya lo que tú dices</small></span></label>
      </div>`;
  } else {
    html = `<div class="asis-hero listo"><span class="asis-kanji" lang="ja">友</span></div>
      <h2 id="asis-titulo">Todo listo${a.nombre ? `, ${esc(a.nombre)}` : ""}</h2>
      <p class="asis-desc">Cuando empiece tu reunión escribe el tema y pulsa <b>Empezar a escuchar</b>. Si te preguntan algo directo, <b>Ayúdame ahora</b>.</p>
      <div class="apoyo">
        <div><b>¿Te gusta Tomo?</b><small>Una estrella en GitHub ayuda a que más personas lo encuentren. Es gratis y libre.</small></div>
        <a class="btn estrella" href="${esc(estrellaUrl())}" target="_blank" rel="noopener" data-asis="estrella">★ Dar una estrella</a>
      </div>
      <button class="btn fantasma recorrer" data-asis="recorrido">Ver un recorrido de 30 segundos</button>`;
  }
  c.innerHTML = `<div class="asis-entra">${html}</div>`;
}

async function marcarBienvenida() {
  try { estado = await api("/api/ajustes", { bienvenida: true }); } catch { /* sin motor */ }
}

// ------------------------------------------------------------------ recorrido guiado
const RECORRIDO = [
  ["#form-empezar", "Empieza aquí", "Escribe de qué trata la reunión y pulsa Empezar. Tomo escucha la reunión aunque tengas el volumen bajo."],
  ["[data-accion=ayudame]", "¿Te preguntaron algo?", "«Ayúdame ahora» mira lo último que se dijo y la pantalla, y te da qué responder al instante."],
  ["#r-preg", "Preguntas y respuestas", "Cada pregunta del expositor aparece aquí con una respuesta lista para decir o pegar en el chat. Pídela más corta u otra versión."],
  ["#compositor", "Resolver ejercicios", "Pega el enunciado o una captura (Ctrl+V). Suma las reglas que dijo el profe y pulsa Resolver. Puedes pedir cambios sobre la misma solución."],
  [".vivo-ojo", "Lo que ve Tomo", "La pantalla de la reunión en vivo. Si tienes dos monitores, elige aquí en cuál está la videollamada."],
  [".tr", "Todo lo que se dijo", "La transcripción completa: busca, marca frases con un clic (Mayús+clic para un tramo) y cópialas o úsalas como reglas."],
  ["#r-ini-hist", "Tus reuniones", "Cada reunión queda guardada con su transcripción, preguntas, ejercicios y una nota en Markdown."],
  [".nav[data-vista=ajustes]", "Ajustes", "Modelos de IA, tu perfil, la pantalla, el audio y la carpeta de notas. Puedes volver a ver este recorrido con el botón «?»."],
  ["#btn-estrella", "Apoya a Tomo", "Si te sirve, una estrella en GitHub nos ayuda muchísimo. ¡Gracias!"],
];
let rec = { pasos: [], i: 0 };

function iniciarRecorrido() {
  cerrarBienvenida();
  if (ruta() !== "/" && !ruta().startsWith("/historial/")) location.hash = "#/";
  setTimeout(() => {
    rec = { pasos: RECORRIDO.filter(([sel]) => { const el = $(sel); return el && el.offsetParent !== null; }), i: 0 };
    if (!rec.pasos.length) return;
    let capa = $("#recorrido");
    if (!capa) {
      capa = document.createElement("div");
      capa.id = "recorrido";
      capa.innerHTML = `<div class="foco"></div><div class="globo" role="dialog" aria-live="polite"><span class="globo-paso num"></span><h3></h3><p></p>
        <div class="globo-acc"><button class="btn chico fantasma" data-rec="fin">Salir</button><span class="der"><button class="btn chico" data-rec="atras">Atrás</button>
        <button class="btn chico primario" data-rec="sig">Siguiente</button></span></div></div>`;
      document.body.appendChild(capa);
    }
    pintarRecorrido();
  }, 350);
}

function pintarRecorrido() {
  const capa = $("#recorrido");
  const [sel, titulo, texto] = rec.pasos[rec.i] || [];
  const el = sel && $(sel);
  if (!capa || !el) return terminarRecorrido();
  el.scrollIntoView({ block: "nearest", behavior: "smooth" });
  requestAnimationFrame(() => {
    const r = el.getBoundingClientRect();
    const m = 8;
    Object.assign($(".foco", capa).style, { top: `${r.top - m}px`, left: `${r.left - m}px`, width: `${r.width + m * 2}px`, height: `${r.height + m * 2}px` });
    const g = $(".globo", capa);
    $(".globo-paso", capa).textContent = `${rec.i + 1} / ${rec.pasos.length}`;
    $("h3", g).textContent = titulo;
    $("p", g).textContent = texto;
    $("[data-rec=atras]", g).disabled = rec.i === 0;
    $("[data-rec=sig]", g).textContent = rec.i === rec.pasos.length - 1 ? "Terminar" : "Siguiente";
    const ancho = 320;
    const abajo = r.bottom + 14 + 170 < innerHeight;
    const top = abajo ? r.bottom + 14 : Math.max(12, r.top - 14 - g.offsetHeight);
    const left = Math.min(innerWidth - ancho - 12, Math.max(12, r.left + r.width / 2 - ancho / 2));
    Object.assign(g.style, { top: `${top}px`, left: `${left}px`, width: `${ancho}px` });
    g.classList.remove("entra"); void g.offsetWidth; g.classList.add("entra");
    $("[data-rec=sig]", g).focus({ preventScroll: true });
  });
}

function terminarRecorrido() {
  $("#recorrido")?.remove();
  localStorage.setItem("tomo-recorrido-visto", "1");
}

window.addEventListener("resize", () => { if ($("#recorrido")) pintarRecorrido(); });

// ------------------------------------------------------------------ apoyo en GitHub
let gh = { estrellas: null, url: "https://github.com/sJonathan15/tomo" };
const estrellaUrl = () => gh.url;

async function cargarEstrellas() {
  try { gh = { ...gh, ...(await api("/api/github")) }; } catch { /* sin internet */ }
  const b = $("#btn-estrella");
  if (b) {
    b.href = gh.url;
    $("#estrellas-n").textContent = gh.estrellas == null ? "" : gh.estrellas >= 1000 ? `${(gh.estrellas / 1000).toFixed(1)}k` : gh.estrellas;
  }
}

function pedirEstrellaSiToca() {
  // tras la 3.ª reunión guardada, una sola vez, un aviso amable
  if (localStorage.getItem("tomo-estrella-pedida") || (cacheHist?.datos || []).length < 3) return;
  localStorage.setItem("tomo-estrella-pedida", "1");
  const t = document.createElement("div");
  t.className = "tostada-estrella";
  t.innerHTML = `<span class="ico" aria-hidden="true">★</span><div><b>Ya llevas 3 reuniones con Tomo</b><small>Si te está sirviendo, ¿le das una estrella en GitHub?</small></div>
    <a class="btn chico estrella" href="${esc(gh.url)}" target="_blank" rel="noopener">Dar estrella</a><button class="btn chico fantasma" aria-label="Cerrar">×</button>`;
  document.body.appendChild(t);
  t.querySelector("button").onclick = () => t.remove();
  setTimeout(() => t.remove(), 20000);
}

// ------------------------------------------------------------------ barra de estado
function pintarBarraEstado() {
  const b = $("#barra-estado");
  if (!b || !estado) return;
  const s = estado.sesion;
  const vivo = s && ["cargando", "escuchando", "pausado"].includes(s.estado);
  b.innerHTML = `<span><i class="led ${vivo ? "on" : ""}"></i>${vivo ? `Escuchando · ${esc(s.fuente || "")}` : "En espera"}</span>
    <span>IA: ${estado.modelos_listos ? "lista" : `<a class="enlace" href="#/ajustes/modelos">configurar</a>`}</span>
    <span>Pantalla: ${estado.ajustes?.pantalla === false ? "apagada" : esc(estado.ajustes?.monitor || "todas")}</span>
    <span class="der">Tomo ${esc(estado.version || "")} · <a class="enlace" href="${esc(gh.url)}" target="_blank" rel="noopener">GitHub</a> · MIRAI</span>`;
}

// ------------------------------------------------------------------ eventos
document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-asis],[data-rec],[data-tema],#btn-ayuda");
  if (!b) return;
  const d = b.dataset;
  if (b.id === "btn-ayuda") { e.preventDefault(); return iniciarRecorrido(); }
  if (d.tema) {
    aplicarTema(d.tema);
    estado = await api("/api/ajustes", { tema: d.tema });
    $$("[data-tema]").forEach((x) => x.setAttribute("aria-checked", x.dataset.tema === d.tema));
    return;
  }
  if (d.rec) {
    if (d.rec === "fin") return terminarRecorrido();
    rec.i += d.rec === "sig" ? 1 : -1;
    if (rec.i >= rec.pasos.length) return terminarRecorrido();
    rec.i = Math.max(0, rec.i);
    return pintarRecorrido();
  }
  switch (d.asis) {
    case "saltar": await marcarBienvenida(); cerrarBienvenida(); break;
    case "cerrar": await marcarBienvenida(); cerrarBienvenida(); break;
    case "atras": paso = Math.max(1, paso - 1); pintarPaso(); break;
    case "siguiente":
      if (paso < PASOS) { paso++; if (paso === 3) { try { datosModelos = await api("/api/modelos"); } catch { /* */ } } pintarPaso(); }
      else { await marcarBienvenida(); cerrarBienvenida(); }
      break;
    case "recorrido": await marcarBienvenida(); iniciarRecorrido(); break;
    case "clave": {
      const k = $("#asis-clave").value.trim();
      if (!k) { avisar("Pega la clave primero"); return; }
      b.disabled = true; b.textContent = "Probando…";
      try {
        const r = await api("/api/modelos/proveedor", { tipo: "gemini", api_key: k });
        const p = await api("/api/modelos/probar", { id: r.id });
        avisar(p.ok ? "Lista: Gemini respondió" : `Guardada, pero no respondió: ${p.mensaje}`);
        datosModelos = await api("/api/modelos");
        estado = await api("/api/estado");
      } catch (err) { avisar(err.message); }
      pintarPaso();
      break;
    }
    case "descargar":
      await api("/api/modelos/descargar", { modelo: d.modelo });
      avisar("Descargando en segundo plano; puedes seguir");
      b.disabled = true; b.textContent = "Descargando…";
      break;
  }
});

document.addEventListener("keydown", (e) => {
  if (!$("#recorrido")) return;
  if (e.key === "Escape") terminarRecorrido();
  if (e.key === "ArrowRight") { rec.i++; rec.i >= rec.pasos.length ? terminarRecorrido() : pintarRecorrido(); }
  if (e.key === "ArrowLeft" && rec.i > 0) { rec.i--; pintarRecorrido(); }
});

// arranque: tema, estrellas y barra (app.js llama a esto en cada sondeo)
function cicloExtras() {
  aplicarTema(estado?.ajustes?.tema);
  pintarBarraEstado();
  pedirEstrellaSiToca();
}
cargarEstrellas();
