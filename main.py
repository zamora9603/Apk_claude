import os
import flet as ft
import sqlite3
from datetime import datetime, timedelta

# ---------- CONFIGURACIÓN ----------
TIPOS_AUSENCIA = ["-", "Enfermedad", "Personal", "Vacaciones", "Permiso", "Injustificada", "Otra"]
HORA_OFICIAL = "08:00"

# En Android la carpeta de la app no es escribible: usamos la carpeta de datos
# que Flet ofrece en FLET_APP_STORAGE_DATA (en el PC cae a la carpeta actual).
DATA_DIR = os.environ.get("FLET_APP_STORAGE_DATA", os.getcwd())
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "asistencia.db")


# ---------- BASE DE DATOS ----------
def conectar():
    return sqlite3.connect(DB_PATH)


def init_db():
    conn = conectar()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS registros (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL,
            trabajador TEXT NOT NULL,
            hora_entrada TEXT,
            hora_salida TEXT,
            tarde TEXT,
            min_tarde INTEGER,
            ausente TEXT,
            tipo_ausencia TEXT,
            descripcion TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS trabajadores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL UNIQUE,
            activo INTEGER NOT NULL DEFAULT 1
        )
    """)
    # Un trabajador no puede tener dos registros el mismo día (también a nivel de base de datos)
    try:
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_registro_unico ON registros(fecha, trabajador)")
    except sqlite3.Error:
        pass
    conn.commit()
    conn.close()


# --- Trabajadores ---
def listar_trabajadores():
    conn = conectar()
    c = conn.cursor()
    c.execute("SELECT nombre FROM trabajadores WHERE activo = 1 ORDER BY nombre COLLATE NOCASE")
    nombres = [f[0] for f in c.fetchall()]
    conn.close()
    return nombres


def agregar_trabajador(nombre):
    """Devuelve 'nuevo', 'reactivado' o 'existe'."""
    conn = conectar()
    c = conn.cursor()
    c.execute("SELECT id, activo FROM trabajadores WHERE nombre = ? COLLATE NOCASE", (nombre,))
    fila = c.fetchone()
    if fila:
        if fila[1] == 1:
            conn.close()
            return "existe"
        c.execute("UPDATE trabajadores SET activo = 1 WHERE id = ?", (fila[0],))
        conn.commit()
        conn.close()
        return "reactivado"
    c.execute("INSERT INTO trabajadores (nombre, activo) VALUES (?, 1)", (nombre,))
    conn.commit()
    conn.close()
    return "nuevo"


def quitar_trabajador(nombre):
    # No se borra: se desactiva, para conservar sus registros anteriores
    conn = conectar()
    c = conn.cursor()
    c.execute("UPDATE trabajadores SET activo = 0 WHERE nombre = ?", (nombre,))
    conn.commit()
    conn.close()


# --- Registros ---
def guardar_registro(datos):
    conn = conectar()
    c = conn.cursor()
    try:
        c.execute("""
            INSERT INTO registros
            (fecha, trabajador, hora_entrada, hora_salida, tarde, min_tarde, ausente, tipo_ausencia, descripcion)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, datos)
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def obtener_registros(desde_iso=None, hasta_iso=None):
    conn = conectar()
    c = conn.cursor()
    if desde_iso and hasta_iso:
        c.execute("""SELECT * FROM registros WHERE fecha BETWEEN ? AND ?
                     ORDER BY fecha DESC, id DESC""", (desde_iso, hasta_iso))
    else:
        c.execute("SELECT * FROM registros ORDER BY fecha DESC, id DESC")
    filas = c.fetchall()
    conn.close()
    return filas


def eliminar_registro(id_reg):
    conn = conectar()
    c = conn.cursor()
    c.execute("DELETE FROM registros WHERE id = ?", (id_reg,))
    conn.commit()
    conn.close()


def existe_registro(fecha_iso, trabajador):
    conn = conectar()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM registros WHERE fecha = ? AND trabajador = ?", (fecha_iso, trabajador))
    cantidad = c.fetchone()[0]
    conn.close()
    return cantidad > 0


# ---------- LÓGICA ----------
def calcular_tarde(hora_entrada):
    if not hora_entrada or hora_entrada == "-":
        return "-", 0
    try:
        h, m = map(int, hora_entrada.split(":"))
        hof, mof = map(int, HORA_OFICIAL.split(":"))
        diff = (h * 60 + m) - (hof * 60 + mof)
        if diff > 0:
            return "Sí", diff
        return "No", 0
    except Exception:
        return "-", 0


def fecha_iso_a_display(fecha_iso):
    try:
        return datetime.strptime(fecha_iso, "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        return fecha_iso


def nombres_reporte(regs):
    """Trabajadores activos + los que tengan registros en el período (aunque ya estén quitados)."""
    activos = listar_trabajadores()
    extra = sorted({r[2] for r in regs} - set(activos), key=str.lower)
    return activos + extra


def calcular_resumen(regs, nombres):
    resumen = {n: {"asist": 0, "aus": 0, "tarde": 0, "min": 0} for n in nombres}
    for r in regs:
        d = resumen.get(r[2])
        if d is None:
            continue
        if r[7] == "Sí":
            d["aus"] += 1
        else:
            d["asist"] += 1
            if r[5] == "Sí":
                d["tarde"] += 1
                d["min"] += r[6] or 0
    return resumen


def porcentaje(d):
    total = d["asist"] + d["aus"]
    return (d["asist"] / total * 100) if total > 0 else 0


# ---------- PDF (sin librerías externas) ----------
class PDFSimple:
    ANCHO = 595
    ALTO = 842
    MARGEN = 50

    def __init__(self):
        self.paginas = []
        self.contenido = None
        self.y = 0
        self.nueva_pagina()

    def nueva_pagina(self):
        self.contenido = []
        self.paginas.append(self.contenido)
        self.y = self.ALTO - self.MARGEN

    @staticmethod
    def _esc(s):
        b = s.encode("cp1252", errors="replace")
        return b.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")

    def _texto_en(self, contenido, x, y, s, size=10, negrita=False):
        fuente = "F2" if negrita else "F1"
        cab = f"BT /{fuente} {size} Tf {x:.1f} {y:.1f} Td (".encode("ascii")
        contenido.append(cab + self._esc(s) + b") Tj ET")

    def texto_en(self, x, y, s, size=10, negrita=False):
        self._texto_en(self.contenido, x, y, s, size, negrita)

    def linea_h(self, y, x1, x2):
        self.contenido.append(f"0.5 w {x1:.1f} {y:.1f} m {x2:.1f} {y:.1f} l S".encode("ascii"))

    def asegurar(self, alto):
        if self.y - alto < self.MARGEN + 20:
            self.nueva_pagina()

    def espacio(self, n):
        self.y -= n

    @staticmethod
    def ajustar(s, size, ancho):
        maximo = max(8, int(ancho / (size * 0.52)))
        res, actual = [], ""
        for palabra in str(s).split():
            if len(palabra) > maximo:
                palabra = palabra[:maximo]
            if len(actual) + len(palabra) + (1 if actual else 0) <= maximo:
                actual = (actual + " " + palabra).strip()
            else:
                res.append(actual)
                actual = palabra
        if actual:
            res.append(actual)
        return res or [""]

    def linea_texto(self, s, size=10, negrita=False, sangria=0):
        self.asegurar(size + 4)
        self.y -= size
        self.texto_en(self.MARGEN + sangria, self.y, s, size, negrita)
        self.y -= 4

    def fila(self, celdas, anchos, size=10, negrita=False):
        cols = [self.ajustar(c, size, a - 6) for c, a in zip(celdas, anchos)]
        n = max(len(c) for c in cols)
        alto = n * (size + 3) + 2
        self.asegurar(alto)
        y0 = self.y
        x = self.MARGEN
        for lineas, a in zip(cols, anchos):
            yy = y0 - size
            for linea in lineas:
                self.texto_en(x + 3, yy, linea, size, negrita)
                yy -= size + 3
            x += a
        self.y = y0 - alto
        self.linea_h(self.y + 1, self.MARGEN, self.MARGEN + sum(anchos))

    def generar(self):
        total = len(self.paginas)
        for i, cont in enumerate(self.paginas, 1):
            self._texto_en(cont, self.ANCHO / 2 - 30, 28, f"Página {i} de {total}", 8)

        objetos = []
        kids = " ".join(f"{5 + 2 * i} 0 R" for i in range(total))
        objetos.append(b"<< /Type /Catalog /Pages 2 0 R >>")
        objetos.append(f"<< /Type /Pages /Kids [{kids}] /Count {total} >>".encode("ascii"))
        objetos.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        objetos.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        for i, cont in enumerate(self.paginas):
            flujo = b"\n".join(cont)
            objetos.append((
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {self.ANCHO} {self.ALTO}] "
                f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {6 + 2 * i} 0 R >>"
            ).encode("ascii"))
            objetos.append(f"<< /Length {len(flujo)} >>\nstream\n".encode("ascii") + flujo + b"\nendstream")

        salida = bytearray(b"%PDF-1.4\n")
        offsets = []
        for idx, obj in enumerate(objetos, 1):
            offsets.append(len(salida))
            salida += f"{idx} 0 obj\n".encode("ascii") + obj + b"\nendobj\n"
        xref = len(salida)
        salida += f"xref\n0 {len(objetos) + 1}\n".encode("ascii")
        salida += b"0000000000 65535 f \n"
        for off in offsets:
            salida += f"{off:010d} 00000 n \n".encode("ascii")
        salida += (f"trailer\n<< /Size {len(objetos) + 1} /Root 1 0 R >>\n"
                   f"startxref\n{xref}\n%%EOF\n").encode("ascii")
        return bytes(salida)


def generar_pdf(regs, desde_txt, hasta_txt, nombres):
    pdf = PDFSimple()
    pdf.linea_texto("Reporte de Asistencia", 18, True)
    pdf.linea_texto(f"Período: {desde_txt} al {hasta_txt}", 11)
    pdf.linea_texto(f"Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')}", 9)
    pdf.espacio(10)

    if not regs:
        pdf.linea_texto("No hay registros en este período.", 11)
        return pdf.generar()

    resumen = calcular_resumen(regs, nombres)
    pdf.linea_texto("Resumen por trabajador", 13, True)
    anchos = [145, 60, 70, 70, 70, 80]
    pdf.fila(["Trabajador", "Asist.", "Ausencias", "Tardanzas", "Min. tarde", "% asist."], anchos, 10, True)
    for n in nombres:
        d = resumen[n]
        pdf.fila([n, d["asist"], d["aus"], d["tarde"], d["min"], f"{porcentaje(d):.0f}%"], anchos, 10)
    pdf.espacio(12)

    pdf.linea_texto("Detalle por trabajador", 13, True)
    for n in nombres:
        regs_n = [r for r in regs if r[2] == n]
        ausencias = sorted([r for r in regs_n if r[7] == "Sí"], key=lambda r: r[1])
        tardanzas = sorted([r for r in regs_n if r[5] == "Sí" and r[7] != "Sí"], key=lambda r: r[1])

        pdf.asegurar(70)
        pdf.espacio(4)
        pdf.linea_texto(n, 12, True)

        pdf.linea_texto(f"Ausencias ({len(ausencias)})", 10, True, 8)
        if ausencias:
            a = [80, 110, 305]
            pdf.fila(["Fecha", "Tipo", "Descripción"], a, 9, True)
            for r in ausencias:
                pdf.fila([fecha_iso_a_display(r[1]), r[8] or "-", r[9] or "-"], a, 9)
        else:
            pdf.linea_texto("Sin ausencias", 9, False, 8)

        pdf.linea_texto(f"Tardanzas ({len(tardanzas)})", 10, True, 8)
        if tardanzas:
            a = [80, 80, 80, 255]
            pdf.fila(["Fecha", "Entrada", "Min. tarde", "Descripción"], a, 9, True)
            for r in tardanzas:
                pdf.fila([fecha_iso_a_display(r[1]), r[3], r[6] or 0, r[9] or "-"], a, 9)
        else:
            pdf.linea_texto("Sin tardanzas", 9, False, 8)
        pdf.espacio(8)

    return pdf.generar()


# ---------- INTERFAZ GRÁFICA ----------
def main(page: ft.Page):
    page.title = "Control de Asistencia"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.padding = 20
    page.scroll = None

    init_db()

    ahora = datetime.now()
    estado = {
        "fecha": ahora.strftime("%Y-%m-%d"),
        "entrada": "08:00",
        "salida": "16:00",
        "desde": datetime(ahora.year, ahora.month, 1),
        "hasta": datetime(ahora.year, ahora.month, ahora.day),
    }

    def aviso(mensaje, color):
        page.show_dialog(ft.SnackBar(ft.Text(mensaje), bgcolor=color))

    def a_dt(v):
        return datetime(v.year, v.month, v.day)

    # ===== PESTAÑA REGISTRAR =====
    txt_fecha = ft.Text(f"Fecha: {ahora.strftime('%d/%m/%Y')}", size=16, weight=ft.FontWeight.BOLD)

    def cambiar_fecha(e):
        if e.control.value:
            f = a_dt(e.control.value)
            estado["fecha"] = f.strftime("%Y-%m-%d")
            txt_fecha.value = f"Fecha: {f.strftime('%d/%m/%Y')}"
            page.update()

    date_picker = ft.DatePicker(
        first_date=datetime(2023, 1, 1),
        last_date=datetime(2035, 12, 31),
        on_change=cambiar_fecha,
    )
    btn_fecha = ft.Button("Seleccionar Fecha", icon=ft.Icons.CALENDAR_MONTH,
                          on_click=lambda _: page.show_dialog(date_picker))

    combo_trabajador = ft.Dropdown(label="Trabajador", options=[], width=300)
    txt_sin_trab = ft.Text("Aún no hay trabajadores. Añádelos en la pestaña Trabajadores.",
                           color=ft.Colors.ORANGE_800, visible=False)

    def refrescar_combo():
        nombres = listar_trabajadores()
        combo_trabajador.options = [ft.dropdown.Option(n) for n in nombres]
        if combo_trabajador.value not in nombres:
            combo_trabajador.value = None
        txt_sin_trab.visible = not nombres

    btn_entrada = ft.Button("Hora Entrada (08:00)", icon=ft.Icons.LOGIN)
    btn_salida = ft.Button("Hora Salida (16:00)", icon=ft.Icons.LOGOUT)

    def sel_entrada(e):
        if e.control.value:
            estado["entrada"] = e.control.value.strftime("%H:%M")
            btn_entrada.content = f"Hora Entrada ({estado['entrada']})"
            page.update()

    def sel_salida(e):
        if e.control.value:
            estado["salida"] = e.control.value.strftime("%H:%M")
            btn_salida.content = f"Hora Salida ({estado['salida']})"
            page.update()

    picker_entrada = ft.TimePicker(on_change=sel_entrada, help_text="Hora Entrada")
    picker_salida = ft.TimePicker(on_change=sel_salida, help_text="Hora Salida")
    btn_entrada.on_click = lambda _: page.show_dialog(picker_entrada)
    btn_salida.on_click = lambda _: page.show_dialog(picker_salida)

    combo_tipo = ft.Dropdown(
        label="Tipo de Ausencia",
        options=[ft.dropdown.Option(ta) for ta in TIPOS_AUSENCIA],
        value="-",
        width=300,
    )
    txt_desc = ft.TextField(label="Descripción (Opcional)", width=300)
    chk_ausente = ft.Checkbox(label="Marcar como AUSENTE", value=False)

    def guardar_click(e):
        trabajador = combo_trabajador.value
        if not trabajador:
            aviso("Selecciona un trabajador", ft.Colors.RED)
            return
        if existe_registro(estado["fecha"], trabajador):
            aviso(f"{trabajador} ya tiene registro este día", ft.Colors.ORANGE)
            return

        if chk_ausente.value:
            entrada, salida, tarde, min_t, ausente = "-", "-", "-", 0, "Sí"
            tipo = combo_tipo.value if combo_tipo.value != "-" else "Injustificada"
        else:
            entrada, salida = estado["entrada"], estado["salida"]
            tarde, min_t = calcular_tarde(entrada)
            ausente = "No"
            tipo = combo_tipo.value or "-"
        desc = (txt_desc.value or "").strip() or "-"

        if guardar_registro((estado["fecha"], trabajador, entrada, salida, tarde, min_t, ausente, tipo, desc)):
            aviso("✅ Registro guardado", ft.Colors.GREEN)
            combo_trabajador.value = None
            txt_desc.value = ""
            chk_ausente.value = False
            page.update()
        else:
            aviso(f"{trabajador} ya tiene registro este día", ft.Colors.ORANGE)

    def todos_presentes_click(e):
        nombres = listar_trabajadores()
        if not nombres:
            aviso("Primero añade trabajadores", ft.Colors.ORANGE)
            return
        por_registrar = [t for t in nombres if not existe_registro(estado["fecha"], t)]
        if not por_registrar:
            aviso("Todos ya tienen registro ese día", ft.Colors.ORANGE)
            return
        tarde, min_t = calcular_tarde(estado["entrada"])
        for t in por_registrar:
            guardar_registro((estado["fecha"], t, estado["entrada"], estado["salida"],
                              tarde, min_t, "No", "-", "-"))
        aviso(f"✅ {len(por_registrar)} asistencias guardadas", ft.Colors.GREEN)

    btn_guardar = ft.Button("Guardar Registro", icon=ft.Icons.SAVE, bgcolor=ft.Colors.GREEN,
                            color=ft.Colors.WHITE, on_click=guardar_click, width=300)
    btn_todos = ft.Button("✅ Todos Presentes", icon=ft.Icons.GROUP, bgcolor=ft.Colors.ORANGE,
                          color=ft.Colors.WHITE, on_click=todos_presentes_click, width=300)

    view_registrar = ft.Column([
        ft.Text("Nuevo Registro", size=24, weight=ft.FontWeight.BOLD),
        txt_fecha,
        btn_fecha,
        ft.Divider(),
        txt_sin_trab,
        combo_trabajador,
        ft.Row([btn_entrada, btn_salida], alignment=ft.MainAxisAlignment.CENTER, wrap=True),
        combo_tipo,
        txt_desc,
        chk_ausente,
        ft.Divider(),
        btn_guardar,
        btn_todos,
    ], scroll=ft.ScrollMode.AUTO, horizontal_alignment=ft.CrossAxisAlignment.CENTER)

    # ===== PESTAÑA REGISTROS =====
    lista_registros = ft.ListView(expand=True, spacing=10, padding=10)

    def confirmar_borrado(id_reg):
        def si(e):
            eliminar_registro(id_reg)
            page.pop_dialog()
            cargar_registros()

        def no(e):
            page.pop_dialog()

        page.show_dialog(ft.AlertDialog(
            title=ft.Text("Eliminar registro"),
            content=ft.Text("¿Seguro que quieres borrar este registro?"),
            actions=[ft.TextButton("Cancelar", on_click=no), ft.TextButton("Eliminar", on_click=si)],
        ))

    def cargar_registros():
        lista_registros.controls.clear()
        for r in obtener_registros():
            texto = f"{fecha_iso_a_display(r[1])} | {r[2]} | E:{r[3]} S:{r[4]}"
            color = ft.Colors.RED if r[7] == "Sí" else ft.Colors.BLACK
            detalle = f"Tarde: {r[5]} ({r[6]} min) - {r[8]}"
            if r[9] and r[9] != "-":
                detalle += f"\n{r[9]}"
            lista_registros.controls.append(
                ft.ListTile(
                    title=ft.Text(texto, color=color, weight=ft.FontWeight.BOLD),
                    subtitle=ft.Text(detalle),
                    trailing=ft.IconButton(
                        ft.Icons.DELETE, icon_color=ft.Colors.RED,
                        on_click=lambda e, id_reg=r[0]: confirmar_borrado(id_reg),
                    ),
                )
            )
        page.update()

    view_registros = ft.Column([
        ft.Text("Registros Recientes", size=24, weight=ft.FontWeight.BOLD),
        ft.Button("Recargar Lista", icon=ft.Icons.REFRESH, on_click=lambda _: cargar_registros()),
        lista_registros,
    ], expand=True)

    # ===== PESTAÑA REPORTES =====
    texto_reporte = ft.Text("", size=14, selectable=True)
    btn_desde = ft.Button("", icon=ft.Icons.EVENT)
    btn_hasta = ft.Button("", icon=ft.Icons.EVENT)

    def refrescar_botones_rango():
        btn_desde.content = f"Desde: {estado['desde'].strftime('%d/%m/%Y')}"
        btn_hasta.content = f"Hasta: {estado['hasta'].strftime('%d/%m/%Y')}"

    def cambiar_desde(e):
        if e.control.value:
            estado["desde"] = a_dt(e.control.value)
            generar_reporte()

    def cambiar_hasta(e):
        if e.control.value:
            estado["hasta"] = a_dt(e.control.value)
            generar_reporte()

    picker_desde = ft.DatePicker(first_date=datetime(2023, 1, 1), last_date=datetime(2035, 12, 31),
                                 on_change=cambiar_desde)
    picker_hasta = ft.DatePicker(first_date=datetime(2023, 1, 1), last_date=datetime(2035, 12, 31),
                                 on_change=cambiar_hasta)
    btn_desde.on_click = lambda _: page.show_dialog(picker_desde)
    btn_hasta.on_click = lambda _: page.show_dialog(picker_hasta)

    def datos_reporte():
        d = estado["desde"].strftime("%Y-%m-%d")
        h = estado["hasta"].strftime("%Y-%m-%d")
        regs = obtener_registros(d, h)
        return regs, nombres_reporte(regs)

    def generar_reporte():
        refrescar_botones_rango()
        if estado["desde"] > estado["hasta"]:
            texto_reporte.value = "La fecha 'Desde' no puede ser posterior a 'Hasta'."
            page.update()
            return
        regs, nombres = datos_reporte()
        resumen = calcular_resumen(regs, nombres)
        texto = (f"REPORTE ({estado['desde'].strftime('%d/%m/%Y')} - "
                 f"{estado['hasta'].strftime('%d/%m/%Y')})\n\n")
        if not nombres:
            texto += "No hay trabajadores ni registros en este período."
        for n in nombres:
            d = resumen[n]
            texto += f"👤 {n}:\n"
            texto += f"   Asistencias: {d['asist']} | Faltas: {d['aus']}\n"
            texto += f"   Tardanzas: {d['tarde']} ({d['min']} min)\n"
            texto += f"   Asistencia: {porcentaje(d):.0f}%\n\n"
        texto_reporte.value = texto
        page.update()

    def rango_semana(e):
        hoy = datetime.now()
        lunes = hoy - timedelta(days=hoy.weekday())
        estado["desde"] = datetime(lunes.year, lunes.month, lunes.day)
        estado["hasta"] = datetime(hoy.year, hoy.month, hoy.day)
        generar_reporte()

    def rango_mes(e):
        hoy = datetime.now()
        estado["desde"] = datetime(hoy.year, hoy.month, 1)
        estado["hasta"] = datetime(hoy.year, hoy.month, hoy.day)
        generar_reporte()

    def construir_pdf():
        if estado["desde"] > estado["hasta"]:
            aviso("Revisa las fechas del reporte", ft.Colors.ORANGE)
            return None, None
        regs, nombres = datos_reporte()
        if not regs:
            aviso("No hay registros en ese período", ft.Colors.ORANGE)
            return None, None
        d = estado["desde"].strftime("%d/%m/%Y")
        h = estado["hasta"].strftime("%d/%m/%Y")
        datos = generar_pdf(regs, d, h, nombres)
        nombre = (f"Reporte_Asistencia_{estado['desde'].strftime('%d-%m-%Y')}"
                  f"_{estado['hasta'].strftime('%d-%m-%Y')}.pdf")
        # copia interna de respaldo
        ruta = os.path.join(DATA_DIR, nombre)
        with open(ruta, "wb") as f:
            f.write(datos)
        return nombre, datos, ruta

    try:
        file_picker = ft.FilePicker()
        page.services.append(file_picker)
    except Exception:
        file_picker = None

    async def exportar_pdf(e):
        try:
            res = construir_pdf()
            if not res or res[0] is None:
                return
            nombre, datos, ruta = res
            if file_picker is None:
                aviso(f"PDF creado en: {ruta}", ft.Colors.GREEN)
                return
            destino = await file_picker.save_file(
                dialog_title="Guardar reporte PDF",
                file_name=nombre,
                allowed_extensions=["pdf"],
                src_bytes=datos,
            )
            if destino:
                aviso("✅ PDF guardado", ft.Colors.GREEN)
            else:
                aviso("No se confirmó el guardado (¿lo cancelaste?)", ft.Colors.ORANGE)
        except Exception as ex:
            aviso(f"No se pudo exportar: {ex}", ft.Colors.RED)

    async def compartir_pdf(e):
        try:
            res = construir_pdf()
            if not res or res[0] is None:
                return
            nombre, datos, ruta = res
            share = ft.Share()
            await share.share_files([ft.ShareFile.from_path(ruta)], text="Reporte de asistencia")
        except Exception as ex:
            aviso(f"No se pudo compartir ({ex}). Usa 'Exportar PDF' y compártelo desde Archivos.",
                  ft.Colors.RED)

    view_reportes = ft.Column([
        ft.Text("Reportes de Asistencia", size=24, weight=ft.FontWeight.BOLD),
        ft.Row([
            ft.Button("Esta semana", icon=ft.Icons.DATE_RANGE, on_click=rango_semana),
            ft.Button("Este mes", icon=ft.Icons.CALENDAR_VIEW_MONTH, on_click=rango_mes),
        ], alignment=ft.MainAxisAlignment.CENTER, wrap=True),
        ft.Row([btn_desde, btn_hasta], alignment=ft.MainAxisAlignment.CENTER, wrap=True),
        ft.Row([
            ft.Button("Exportar PDF", icon=ft.Icons.PICTURE_AS_PDF, bgcolor=ft.Colors.RED_700,
                      color=ft.Colors.WHITE, on_click=exportar_pdf),
            ft.Button("Compartir", icon=ft.Icons.SHARE, bgcolor=ft.Colors.BLUE_700,
                      color=ft.Colors.WHITE, on_click=compartir_pdf),
        ], alignment=ft.MainAxisAlignment.CENTER, wrap=True),
        ft.Divider(),
        ft.Container(content=texto_reporte, padding=10, bgcolor=ft.Colors.GREY_100, border_radius=10),
    ], scroll=ft.ScrollMode.AUTO)

    # ===== PESTAÑA TRABAJADORES =====
    txt_nuevo = ft.TextField(label="Nombre del trabajador", expand=True)
    lista_trab = ft.ListView(expand=True, spacing=5)

    def cargar_trabajadores():
        lista_trab.controls.clear()
        nombres = listar_trabajadores()
        for n in nombres:
            lista_trab.controls.append(
                ft.ListTile(
                    title=ft.Text(n),
                    trailing=ft.IconButton(
                        ft.Icons.DELETE, icon_color=ft.Colors.RED,
                        on_click=lambda e, nombre=n: confirmar_quitar(nombre),
                    ),
                )
            )
        if not nombres:
            lista_trab.controls.append(ft.Text("No hay trabajadores todavía.", italic=True))
        refrescar_combo()
        page.update()

    def anadir_trabajador(e):
        nombre = " ".join((txt_nuevo.value or "").split())
        if not nombre:
            aviso("Escribe un nombre", ft.Colors.RED)
            return
        res = agregar_trabajador(nombre)
        if res == "existe":
            aviso(f"{nombre} ya está en la lista", ft.Colors.ORANGE)
            return
        txt_nuevo.value = ""
        aviso(f"✅ {nombre} añadido", ft.Colors.GREEN)
        cargar_trabajadores()

    def confirmar_quitar(nombre):
        def si(e):
            quitar_trabajador(nombre)
            page.pop_dialog()
            cargar_trabajadores()

        def no(e):
            page.pop_dialog()

        page.show_dialog(ft.AlertDialog(
            title=ft.Text("Quitar trabajador"),
            content=ft.Text(f"¿Quitar a {nombre} de la lista?\nSus registros anteriores se conservan."),
            actions=[ft.TextButton("Cancelar", on_click=no), ft.TextButton("Quitar", on_click=si)],
        ))

    txt_nuevo.on_submit = anadir_trabajador

    view_trabajadores = ft.Column([
        ft.Text("Trabajadores", size=24, weight=ft.FontWeight.BOLD),
        ft.Row([txt_nuevo, ft.Button("Añadir", icon=ft.Icons.PERSON_ADD, on_click=anadir_trabajador)]),
        lista_trab,
    ], expand=True)

    # ===== NAVEGACIÓN INFERIOR =====
    vistas = [view_registrar, view_registros, view_reportes, view_trabajadores]

    def cambiar_pestana(e):
        i = e.control.selected_index
        contenido.content = vistas[i]
        if i == 0:
            refrescar_combo()
            page.update()
        elif i == 1:
            cargar_registros()
        elif i == 2:
            generar_reporte()
        elif i == 3:
            cargar_trabajadores()

    barra_navegacion = ft.NavigationBar(
        selected_index=0,
        on_change=cambiar_pestana,
        destinations=[
            ft.NavigationBarDestination(icon=ft.Icons.EDIT, label="Registrar"),
            ft.NavigationBarDestination(icon=ft.Icons.LIST, label="Registros"),
            ft.NavigationBarDestination(icon=ft.Icons.ASSESSMENT, label="Reportes"),
            ft.NavigationBarDestination(icon=ft.Icons.PEOPLE, label="Personal"),
        ],
    )

    refrescar_combo()
    refrescar_botones_rango()
    contenido = ft.Container(content=view_registrar, expand=True)
    page.add(contenido)
    page.navigation_bar = barra_navegacion
    page.update()


# Ejecutar la app
if __name__ == "__main__":
    ft.run(main)
