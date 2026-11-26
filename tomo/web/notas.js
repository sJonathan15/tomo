// Tomo — Notas: ver, buscar, editar y exportar las notas sin instalar nada más (Obsidian es opcional).
// Ruta: #/notas  o  #/notas/<ruta del archivo codificada>. Usa lo de app.js ($, esc, api, avisar, copiar, vista, modo).
"use strict";

const N = { lista: [], carpeta: "", actual: null, editando: false, sucio: false, guardando: false, t: 0, q: "" };

// ------------------------------------------------------------------ Markdown completo (notas)
function inlineDoc(t) {
  const codigos = [];
  let h = esc(t).replace(/`([^`]+)`/g, (_, c) => { codigos.push(c); return `\u0000${codigos.length - 1}\u0000`; });
  h = h.replace(/\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]/g, (_, n, a) =>
    `<button class="wiki" data-n-wiki="${n.trim()}">${(a || n).trim()}</button>`);
  h = h.replace(/!?\[([^\]]*)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  h = h.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
  h = h.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/(^|[^*\w])\*([^*\s][^*]*)\*/g, "$1<i>$2</i>")
    .replace(/(^|\W)_([^_\s][^_]*)_(?=\W|$)/g, "$1<i>$2</i>").replace(/~~([^~]+)~~/g, "<s>$1</s>").replace(/==([^=]+)==/g, "<mark>$1</mark>");
  return h.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codigos[+i]}</code>`);
}

function mdDoc(texto) {
  const lineas = String(texto || "").replace(/\r/g, "").split("\n");
  let html = "", i = 0;
  const celdas = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
  while (i < lineas.length) {
    const l = lineas[i];
    const cerca = l.match(/^\s*```(\S*)/);
    if (cerca) {
      const cod = [];
      for (i++; i < lineas.length && !/^\s*```/.test(lineas[i]); i++) cod.push(lineas[i]);
      i++;
      html += `<div class="bloque"><pre><code>${esc(cod.join("\n"))}</code></pre><button class="btn chico primario" data-copiar-bloque>Copiar</button></div>`;
      continue;
    }
    const h = l.match(/^(#{1,6})\s+(.*)/);
    if (h) { const n = Math.min(h[1].length, 4); html += `<h${n}>${inlineDoc(h[2])}</h${n}>`; i++; continue; }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(l)) { html += "<hr>"; i++; continue; }
    if (/^\s*\|.*\|\s*$/.test(l) && /^\s*\|?[\s:|-]+\|?\s*$/.test(lineas[i + 1] || "") && (lineas[i + 1] || "").includes("-")) {
      const cab = celdas(l);
      let filas = "";
      for (i += 2; i < lineas.length && /^\s*\|.*\|\s*$/.test(lineas[i]); i++) filas += `<tr>${celdas(lineas[i]).map((c) => `<td>${inlineDoc(c)}</td>`).join("")}</tr>`;
      html += `<div class="tabla-doc"><table><thead><tr>${cab.map((c) => `<th>${inlineDoc(c)}</th>`).join("")}</tr></thead><tbody>${filas}</tbody></table></div>`;
      continue;
    }
    if (/^\s*>/.test(l)) {
      const cita = [];
      for (; i < lineas.length && /^\s*>/.test(lineas[i]); i++) cita.push(lineas[i].replace(/^\s*>\s?/, ""));
      html += `<blockquote>${mdDoc(cita.join("\n"))}</blockquote>`;
      continue;
    }
    if (/^\s*([-*+]|\d+[.)])\s+/.test(l)) {
      const tipo = /^\s*\d/.test(l) ? "ol" : "ul";
      html += `<${tipo}>`;
      for (; i < lineas.length && /^\s*([-*+]|\d+[.)])\s+/.test(lineas[i]); i++) {
        const sangria = lineas[i].match(/^\s*/)[0].length >= 2 ? ' class="sub"' : "";
        const item = lineas[i].replace(/^\s*([-*+]|\d+[.)])\s+/, "");
        const tarea = item.match(/^\[( |x|X)\]\s+(.*)/);
        html += tarea ? `<li class="tarea"${sangria}><label><input type="checkbox" data-n-check="${i}" ${tarea[1] !== " " ? "checked" : ""}> <span>${inlineDoc(tarea[2])}</span></label></li>`
          : `<li${sangria}>${inlineDoc(item)}</li>`;
      }
      html += `</${tipo}>`;
      continue;
    }
    if (!l.trim()) { i++; continue; }
    const parr = [];
    for (; i < lineas.length && lineas[i].trim() && !/^(#{1,6}\s|\s*```|\s*>|\s*([-*+]|\d+[.)])\s+|\s*\|)/.test(lineas[i]); i++) parr.push(lineas[i]);
    if (!parr.length) { parr.push(lineas[i]); i++; }
    html += `<p>${parr.map(inlineDoc).join("<br>")}</p>`;
  }
  return html;
}

function separarFrente(texto) {
  const m = String(texto).match(/^---\n([\s\S]*?)\n---\n?/);
  if (!m) return { meta: {}, cuerpo: texto, desfase: 0 };
  const meta = Object.fromEntries([...m[1].matchAll(/^(\w+):\s*(.*)$/gm)].map((x) => [x[1], x[2]]));
  return { meta, cuerpo: texto.slice(m[0].length), desfase: m[0].split("\n").length - 1 };
}

// ------------------------------------------------------------------ vista
function itemNota(n) {
  const sel = N.actual && N.actual.ruta === n.ruta;
  return `<li><button class="n-item" data-n-abrir="${esc(n.ruta)}" ${sel ? 'aria-current="true"' : ""}>
    <span class="n-tit">${esc(n.titulo)}</span>
    <span class="n-meta num">${esc(n.fecha)}${n.carpeta ? ` · ${esc(n.carpeta)}` : ""}${n.sin_ia ? ' <span class="etq-aviso">sin IA</span>' : n.tomo ? ' <span class="n-tomo">reunión</span>' : ""}</span>
    <span class="n-ext">${esc(n.extracto)}</span></button></li>`;
}

function pintarListaNotas() {
  const ul = $("#n-items");
  if (!ul) return;
  ul.innerHTML = N.lista.length ? N.lista.map(itemNota).join("")
    : `<li class="vacio n-vacio">${N.q ? `Nada con «${esc(N.q)}».` : "Aún no hay notas. Al terminar una reunión, Tomo deja aquí su nota; también puedes crear una con «Nueva»."}</li>`;
}

function barraNota(d) {
  const ab = d.abrir || {};
  const fuera = [ab.obsidian && `<button class="btn chico" data-n-fuera="obsidian">Abrir en Obsidian</button>`,
    ab.app && `<button class="btn chico" data-n-fuera="app">Abrir con ${esc(ab.app)}</button>`,
    ab.carpeta && `<button class="btn chico fantasma" data-n-fuera="carpeta">Abrir la carpeta</button>`].filter(Boolean);
  return `<div class="n-barra">
      <div class="n-modo" role="radiogroup" aria-label="Modo">
        <button role="radio" aria-checked="${!N.editando}" data-n-modo="leer">Leer</button>
        <button role="radio" aria-checked="${N.editando}" data-n-modo="editar">Editar</button></div>
      <span class="n-guardado num" id="n-guardado" aria-live="polite"></span>
      <span class="n-sep"></span>
      ${d.sin_ia ? `<button class="btn chico primario" data-n="rehacer" title="Escribirla de nuevo con tu modelo de IA">Rehacer con IA</button>` : ""}
      <button class="btn chico" data-n="copiar">Copiar</button>
      <button class="btn chico" data-n="descargar">Descargar .md</button>
      <button class="btn chico" data-n="imprimir" title="Imprimir o guardar como PDF">PDF</button>
      ${fuera.length ? `<details class="n-fuera"><summary class="btn chico fantasma">Abrir fuera ▾</summary><div class="n-menu">${fuera.join("")}</div></details>` : ""}
    </div>`;
}

function pintarDoc() {
  const caja = $("#n-doc");
  if (!caja) return;
  const d = N.actual;
  if (!d) {
    caja.innerHTML = `<div class="n-nada"><img src="/icono.svg" alt="" width="44" height="44"><h2>Tus notas, aquí mismo</h2>
      <p class="vacio">Tomo guarda cada reunión como una nota en Markdown dentro de <code>${esc(N.carpeta)}</code>. Puedes leerlas, buscarlas, editarlas y sacarlas en PDF sin instalar nada más. Si usas Obsidian, también se abren ahí.</p></div>`;
    return;
  }
  const { meta, cuerpo } = separarFrente(N.editando ? $("#n-editor")?.value ?? d.texto : d.texto);
  const chips = [meta.fecha && `<span class="chip num">${esc(meta.fecha)}</span>`,
    ...(meta.tags ? meta.tags.replace(/[[\]]/g, "").split(",").map((t) => t.trim()).filter(Boolean).map((t) => `<span class="chip">#${esc(t)}</span>`) : [])].filter(Boolean).join("");
  const aviso = d.sin_ia ? `<p class="n-aviso">Esta nota se armó sin IA porque ningún modelo respondió al terminar. Tiene todo lo que se dijo; pulsa <b>Rehacer con IA</b> cuando tengas un modelo listo.</p>` : "";
  if (N.editando) {
    if (!$("#n-editor")) {
      caja.innerHTML = `${barraNota(d)}<div class="n-edicion"><textarea id="n-editor" spellcheck="true" aria-label="Editar la nota en Markdown"></textarea>
        <article class="doc-nota" id="n-vista"></article></div>`;
      $("#n-editor").value = d.texto;
      $("#n-editor").focus();
    }
    $("#n-vista").innerHTML = mdDoc(cuerpo);
  } else {
    caja.innerHTML = `${barraNota(d)}<div class="n-hoja"><div class="n-lectura"><article class="doc-nota" id="n-vista">${chips ? `<div class="n-chips">${chips}</div>` : ""}${aviso}${mdDoc(cuerpo)}</article>
      <nav class="n-indice" id="n-indice" aria-label="Secciones de la nota"></nav></div></div>`;
    armarIndice();
  }
  marcarGuardado();
}

// índice de secciones (notas largas): se queda fijo al lado y marca dónde vas leyendo
function armarIndice() {
  const nav = $("#n-indice"), hoja = $(".n-hoja");
  const titulos = $$("#n-vista h2, #n-vista h3");
  titulos.forEach((h, i) => { h.id = `sec-${i}`; });
  if (!nav || titulos.filter((h) => h.tagName === "H2").length < 3) { nav?.remove(); return; }
  nav.innerHTML = `<p class="n-indice-tit">En esta nota</p>` + titulos.map((h, i) =>
    `<button class="${h.tagName === "H3" ? "sub" : ""}" data-n-ir="sec-${i}">${esc(h.textContent)}</button>`).join("");
  const marcar = () => {
    const tope = hoja.getBoundingClientRect().top + 90;
    let actual = titulos[0];
    for (const h of titulos) if (h.getBoundingClientRect().top <= tope) actual = h;
    $$("button", nav).forEach((b) => b.toggleAttribute("aria-current", b.dataset.nIr === actual.id));
  };
  hoja.addEventListener("scroll", marcar, { passive: true });
  marcar();
}

function marcarGuardado(txt) {
  const el = $("#n-guardado");
  if (!el) return;
  el.textContent = txt ?? (N.guardando ? "Guardando…" : N.sucio ? "Sin guardar" : N.editando ? "Guardado" : "");
}

async function pintarNotas(ruta) {
  vista.innerHTML = `<div class="notas">
    <aside class="notas-lista" aria-label="Lista de notas">
      <div class="notas-cab"><h1>Notas</h1><button class="btn chico primario" data-n="nueva">Nueva</button></div>
      <label class="n-buscar"><span class="sr">Buscar en tus notas</span><input id="n-buscar" type="search" placeholder="Buscar en tus notas" value="${esc(N.q)}" autocomplete="off"></label>
      <ul id="n-items" class="n-items"></ul>
    </aside>
    <section class="notas-doc" id="n-doc" aria-live="polite"></section></div>`;
  N.editando = false; N.sucio = false;
  await cargarListaNotas();
  const destino = ruta || (N.actual && N.lista.some((n) => n.ruta === N.actual.ruta) ? N.actual.ruta : N.lista[0]?.ruta);
  if (destino) await abrirNota(destino, false); else { N.actual = null; pintarDoc(); }
}

async function cargarListaNotas() {
  try {
    const d = await api(`/api/notas?q=${encodeURIComponent(N.q)}`);
    N.lista = d.notas; N.carpeta = d.carpeta;
  } catch (e) { N.lista = []; avisar(e.message); }
  pintarListaNotas();
}

async function abrirNota(ruta, cambiarHash = true, nombre = "") {
  if (N.sucio) await guardarNota();
  try {
    N.actual = await api(nombre ? `/api/nota?nombre=${encodeURIComponent(nombre)}` : `/api/nota?ruta=${encodeURIComponent(ruta)}`);
  } catch (e) { avisar(e.message); return; }
  N.editando = false; N.sucio = false;
  if (cambiarHash) { const h = `#/notas/${encodeURIComponent(N.actual.ruta)}`; if (location.hash !== h) { history.replaceState(null, "", h); modo = h.slice(1); } }
  pintarListaNotas();
  pintarDoc();
  $(".n-hoja")?.scrollTo(0, 0);
}

async function guardarNota(forzar = false) {
  const ed = $("#n-editor");
  if (!N.actual || !ed || (!N.sucio && !forzar)) return true;
  N.guardando = true; marcarGuardado();
  const texto = ed.value;
  try {
    const r = await api("/api/nota", { ruta: N.actual.ruta, texto, mtime: forzar ? 0 : N.actual.mtime });
    N.actual.mtime = r.mtime; N.actual.texto = texto;
    N.sucio = ed.value !== texto;
    return true;
  } catch (e) {
    if (/cambió fuera/.test(e.message)) {
      $("#n-doc .n-conflicto")?.remove();
      $("#n-doc").insertAdjacentHTML("afterbegin", `<div class="n-conflicto" role="alert">La nota cambió fuera de Tomo (quizá en Obsidian).
        <button class="btn chico" data-n="recargar">Ver la otra versión</button><button class="btn chico primario" data-n="forzar">Guardar la mía</button></div>`);
    } else avisar(e.message);
    return false;
  } finally { N.guardando = false; marcarGuardado(); }
}

function cambiarCasilla(i, marcada) {
  const lineas = N.actual.texto.split("\n");
  const { desfase } = separarFrente(N.actual.texto);
  const k = i + desfase;
  if (!lineas[k]) return;
  lineas[k] = lineas[k].replace(/\[( |x|X)\]/, marcada ? "[x]" : "[ ]");
  const texto = lineas.join("\n");
  api("/api/nota", { ruta: N.actual.ruta, texto, mtime: N.actual.mtime })
    .then((r) => { N.actual.mtime = r.mtime; N.actual.texto = texto; })
    .catch((e) => { avisar(e.message); pintarDoc(); });
}

// ------------------------------------------------------------------ eventos
document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-n],[data-n-abrir],[data-n-modo],[data-n-wiki],[data-n-fuera],[data-n-ir]");
  if (!b || !$(".notas")) return;
  const ds = b.dataset;
  try {
    if (ds.nIr) return document.getElementById(ds.nIr)?.scrollIntoView({ behavior: "smooth", block: "start" });
    if (ds.nAbrir) return abrirNota(ds.nAbrir);
    if (ds.nWiki) return abrirNota("", true, ds.nWiki);
    if (ds.nModo) {
      if (ds.nModo === "leer" && N.editando) { await guardarNota(); N.actual.texto = $("#n-editor")?.value ?? N.actual.texto; }
      N.editando = ds.nModo === "editar";
      return pintarDoc();
    }
    if (ds.nFuera) {
      b.closest("details")?.removeAttribute("open");
      await api("/api/abrir", { ruta: N.actual.ruta, en: ds.nFuera });
      return avisar(ds.nFuera === "carpeta" ? "Abriendo la carpeta…" : "Abriendo…");
    }
    const a = ds.n;
    if (a === "nueva") {
      const d = await api("/api/nota", { nuevo: "Nota nueva" });
      N.q = ""; $("#n-buscar").value = "";
      await cargarListaNotas();
      await abrirNota(d.ruta);
      N.editando = true; pintarDoc();
      const ed = $("#n-editor"); ed.setSelectionRange(2, 12);
    } else if (a === "copiar") copiar($("#n-editor")?.value ?? N.actual.texto, "Nota copiada en Markdown");
    else if (a === "descargar") {
      const url = URL.createObjectURL(new Blob([$("#n-editor")?.value ?? N.actual.texto], { type: "text/markdown" }));
      Object.assign(document.createElement("a"), { href: url, download: `${N.actual.nombre}.md` }).click();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
    } else if (a === "imprimir") {
      if (N.editando) { await guardarNota(); N.editando = false; pintarDoc(); }
      window.print();
    } else if (a === "rehacer") {
      b.disabled = true; b.textContent = "Rehaciendo… (hasta 4 min)";
      N.actual = await api("/api/nota/rehacer", { ruta: N.actual.ruta });
      avisar("Nota reescrita con IA");
      await cargarListaNotas(); pintarDoc();
    } else if (a === "recargar") {
      await abrirNota(N.actual.ruta, false);
    } else if (a === "forzar") {
      $("#n-doc .n-conflicto")?.remove();
      if (await guardarNota(true)) avisar("Guardada tu versión");
    }
  } catch (err) {
    avisar(err.message);
    if (ds.n === "rehacer") pintarDoc();
  }
});

document.addEventListener("change", (e) => {
  if (e.target.dataset.nCheck !== undefined && !N.editando && N.actual) cambiarCasilla(+e.target.dataset.nCheck, e.target.checked);
});

document.addEventListener("input", (e) => {
  if (e.target.id === "n-buscar") {
    clearTimeout(N.tb);
    N.tb = setTimeout(() => { N.q = e.target.value.trim(); cargarListaNotas(); }, 220);
  } else if (e.target.id === "n-editor") {
    N.sucio = true; marcarGuardado();
    clearTimeout(N.tv); N.tv = setTimeout(() => { $("#n-vista").innerHTML = mdDoc(separarFrente(e.target.value).cuerpo); }, 120);
    clearTimeout(N.tg); N.tg = setTimeout(() => guardarNota(), 900);
  }
});

document.addEventListener("keydown", (e) => {
  if (!$(".notas")) return;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); clearTimeout(N.tg); guardarNota(); }
  if (e.key === "Tab" && e.target.id === "n-editor") {      // sangrar en vez de salir del editor
    e.preventDefault();
    const t = e.target, i = t.selectionStart;
    t.setRangeText("  ", i, t.selectionEnd, "end");
    t.dispatchEvent(new Event("input", { bubbles: true }));
  }
});

window.addEventListener("beforeunload", () => {
  const ed = $("#n-editor");
  if (N.sucio && ed && N.actual) navigator.sendBeacon("/api/nota", new Blob([JSON.stringify({ ruta: N.actual.ruta, texto: ed.value, mtime: N.actual.mtime })], { type: "application/json" }));
});
