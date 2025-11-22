import sqlite3
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import Table, TableStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ConversationHandler, filters, ContextTypes
)
from telegram.request import HTTPXRequest
import time
import logging
import urllib.request

# Put your bot token in one place so we can call Telegram HTTP API directly
BOT_TOKEN = "8511173598:AAEr1bHAvtNSG_VeXHNMQXRq1B6N0g6oUxw"

DB = "db_keuangan.db"

# ==========================
# FORMAT RUPIAH
# ==========================
def rupiah(x: int):
    return f"Rp {x:,.0f}".replace(",", ".")

# ==========================
# DATABASE SETUP
# ==========================
def init_db():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS barang (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nama TEXT,
            modal INTEGER,
            jual INTEGER,
            margin INTEGER,
            stok_awal INTEGER,
            stok_masuk INTEGER,
            stok_keluar INTEGER
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS transaksi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            barang TEXT,
            jumlah INTEGER,
            tanggal TEXT
        )
    """)

    conn.commit()
    conn.close()

init_db()

# ==========================
# MENU UTAMA
# ==========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        ["Input Barang", "Tambah Stok"],
        ["Transaksi"],
        ["Closing Harian", "Laporan Bulanan"],
        ["List Barang"]
    ]
    await update.message.reply_text(
        "Menu Utama:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )

# ==========================
# INPUT BARANG BARU
# ==========================
NAMA, MODAL, JUAL, STOK_AWAL = range(4)

async def barang_input(update, context):
    await update.message.reply_text("Masukkan nama barang:")
    return NAMA

async def input_nama(update, context):
    context.user_data["nama"] = update.message.text
    await update.message.reply_text("Masukkan harga modal:")
    return MODAL

async def input_modal(update, context):
    context.user_data["modal"] = int(update.message.text)
    await update.message.reply_text("Masukkan harga jual:")
    return JUAL

async def input_jual(update, context):
    context.user_data["jual"] = int(update.message.text)
    await update.message.reply_text("Masukkan stok awal:")
    return STOK_AWAL

async def input_stok_awal(update, context):
    nama = context.user_data["nama"]
    modal = context.user_data["modal"]
    jual = context.user_data["jual"]
    stok_awal = int(update.message.text)
    margin = jual - modal

    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO barang (nama, modal, jual, margin, stok_awal, stok_masuk, stok_keluar)
        VALUES (?, ?, ?, ?, ?, 0, 0)
    """, (nama, modal, jual, margin, stok_awal))
    conn.commit()
    conn.close()

    await update.message.reply_text(f"Barang '{nama}' berhasil ditambahkan.")
    await start(update, context)
    return ConversationHandler.END

# Conversation states for stok and transaksi
PILIH_BARANG_TAMBAH, INPUT_TAMBAH, TRANSAKSI_BARANG, TRANSAKSI_JUMLAH = range(4, 8)

# ==========================
# TAMBAH STOK
# ==========================
async def tambah_stok(update, context):
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("SELECT nama FROM barang ORDER BY nama")
    items = [r[0] for r in cur.fetchall()]
    conn.close()

    if not items:
        await update.message.reply_text("Belum ada barang. Tambahkan barang terlebih dahulu.")
        return ConversationHandler.END

    # Build a simple keyboard with item names
    keyboard = [[name] for name in items]
    keyboard.append(["Batal"])
    await update.message.reply_text("Pilih barang untuk ditambah stok:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    return PILIH_BARANG_TAMBAH

async def pilih_barang_tambah(update, context):
    text = update.message.text
    if text == "Batal":
        await start(update, context)
        return ConversationHandler.END
    context.user_data["tambah_nama"] = text
    await update.message.reply_text("Masukkan jumlah stok yang masuk (angka):")
    return INPUT_TAMBAH

async def input_tambah(update, context):
    nama = context.user_data.get("tambah_nama")
    try:
        jumlah = int(update.message.text)
    except ValueError:
        await update.message.reply_text("Masukkan angka yang valid.")
        return INPUT_TAMBAH

    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    # update stok_masuk (cumulative) and stok_awal (available stock)
    cur.execute("SELECT stok_masuk, stok_awal FROM barang WHERE nama = ?", (nama,))
    row = cur.fetchone()
    if not row:
        conn.close()
        await update.message.reply_text("Barang tidak ditemukan.")
        return ConversationHandler.END
    stok_masuk, stok_awal = row
    stok_masuk = (stok_masuk or 0) + jumlah
    stok_awal = (stok_awal or 0) + jumlah
    cur.execute("UPDATE barang SET stok_masuk = ?, stok_awal = ? WHERE nama = ?", (stok_masuk, stok_awal, nama))
    conn.commit()
    conn.close()

    await update.message.reply_text(f"Stok untuk '{nama}' bertambah {jumlah}.")
    await start(update, context)
    return ConversationHandler.END

# ==========================
# TRANSAKSI (PENJUALAN)
# ==========================
async def transaksi(update, context):
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("SELECT nama FROM barang ORDER BY nama")
    items = [r[0] for r in cur.fetchall()]
    conn.close()

    if not items:
        await update.message.reply_text("Belum ada barang untuk transaksi.")
        return ConversationHandler.END

    keyboard = [[name] for name in items]
    keyboard.append(["Batal"])
    await update.message.reply_text("Pilih barang yang terjual:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    return TRANSAKSI_BARANG

async def transaksi_barang(update, context):
    text = update.message.text
    if text == "Batal":
        await start(update, context)
        return ConversationHandler.END
    context.user_data["transaksi_nama"] = text
    await update.message.reply_text("Masukkan jumlah terjual (angka):")
    return TRANSAKSI_JUMLAH

async def transaksi_jumlah(update, context):
    nama = context.user_data.get("transaksi_nama")
    try:
        jumlah = int(update.message.text)
    except ValueError:
        await update.message.reply_text("Masukkan angka yang valid.")
        return TRANSAKSI_JUMLAH

    tanggal = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    # insert transaksi
    cur.execute("INSERT INTO transaksi (barang, jumlah, tanggal) VALUES (?, ?, ?)", (nama, jumlah, tanggal))
    # update stok_keluar and stok_awal
    cur.execute("SELECT stok_keluar, stok_awal FROM barang WHERE nama = ?", (nama,))
    row = cur.fetchone()
    if row:
        stok_keluar, stok_awal = row
        stok_keluar = (stok_keluar or 0) + jumlah
        stok_awal = (stok_awal or 0) - jumlah
        if stok_awal < 0:
            stok_awal = 0
        cur.execute("UPDATE barang SET stok_keluar = ?, stok_awal = ? WHERE nama = ?", (stok_keluar, stok_awal, nama))
    conn.commit()
    conn.close()

    await update.message.reply_text(f"Transaksi dicatat: {nama} x {jumlah}.")
    await start(update, context)
    return ConversationHandler.END

# ==========================
# LIST BARANG
# ==========================
async def list_barang(update, context):
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("SELECT nama, modal, jual, margin, stok_awal, stok_masuk, stok_keluar FROM barang ORDER BY nama")
    rows = cur.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("Belum ada barang.")
        return

    lines = []
    for nama, modal, jual, margin, stok_awal, stok_masuk, stok_keluar in rows:
        lines.append(f"{nama} — Modal: {rupiah(modal)} | Jual: {rupiah(jual)} | Margin: {rupiah(margin)} | Stok: {stok_awal} | Masuk: {stok_masuk or 0} | Keluar: {stok_keluar or 0}")

    await update.message.reply_text("\n".join(lines))

# ==========================
# CLOSING HARIAN — PDF PROFESIONAL
# ==========================
async def closing(update, context):
    today = datetime.now().strftime("%Y-%m-%d")
    # Aggregate today's transactions by item and include a separate table
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("""
        SELECT t.barang, SUM(t.jumlah) as terjual, b.modal, b.jual, b.margin
        FROM transaksi t
        JOIN barang b ON t.barang = b.nama
        WHERE t.tanggal = ?
        GROUP BY t.barang
    """, (today,))
    rows = cur.fetchall()

    # Fetch incoming stock summary (cumulative stored in `stok_masuk`)
    cur.execute("SELECT nama, stok_masuk, modal FROM barang WHERE stok_masuk > 0")
    stok_rows = cur.fetchall()
    conn.close()

    if not rows and not stok_rows:
        await update.message.reply_text("Tidak ada transaksi atau stok masuk hari ini.")
        return

    # Use Platypus SimpleDocTemplate for better layout (automatic pagination)
    file = f"closing_{today}.pdf"
    width, height = A4
    left_right_margin = 30
    doc = SimpleDocTemplate(file, pagesize=A4, leftMargin=left_right_margin, rightMargin=left_right_margin,
                            topMargin=50, bottomMargin=40)

    styles = getSampleStyleSheet()
    
    # Define custom styles
    title_style = ParagraphStyle(
        name="ClosingTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        spaceAfter=4,
        alignment=1,  # center
        textColor=colors.HexColor('#0b6fa4')
    )
    
    section_style = ParagraphStyle(
        name="SectionTitle",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=14,
        spaceAfter=8,
        spaceBefore=8,
        textColor=colors.HexColor('#333333'),
        borderPadding=3
    )
    
    meta_style = ParagraphStyle(
        name="Meta",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=12,
        alignment=1  # center
    )
    
    summary_style = ParagraphStyle(
        name="Summary",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=14,
        spaceAfter=6
    )

    story = []
    
    # Header
    story.append(Paragraph("═" * 80, meta_style))
    story.append(Paragraph("LAPORAN CLOSING HARIAN", title_style))
    story.append(Paragraph(f"Tanggal: {today}", meta_style))
    story.append(Paragraph("═" * 80, meta_style))
    story.append(Spacer(1, 10))

    # Sales summary section
    story.append(Paragraph("📊 RINGKASAN PENJUALAN HARIAN", section_style))

    # Build detailed sales table
    headers = ["No", "Nama Barang", "Qty", "Harga Modal", "Harga Jual", "Margin/Unit", "Total Laba"]
    data_table = [headers]
    total_qty = 0
    total_modal_spent = 0
    total_selling_value = 0
    total_laba = 0
    
    for idx, (barang, terjual, modal, jual, margin) in enumerate(rows, start=1):
        terjual = int(terjual)
        modal = int(modal)
        jual = int(jual)
        margin = int(margin)
        
        laba = terjual * margin
        modal_spent = terjual * modal
        selling_value = terjual * jual
        
        total_qty += terjual
        total_modal_spent += modal_spent
        total_selling_value += selling_value
        total_laba += laba
        
        data_table.append([
            idx,
            barang,
            terjual,
            rupiah(modal),
            rupiah(jual),
            rupiah(margin),
            rupiah(laba)
        ])
    
    # Totals row
    data_table.append([
        "",
        "TOTAL",
        total_qty,
        rupiah(total_modal_spent // total_qty) if total_qty > 0 else "0",
        rupiah(total_selling_value // total_qty) if total_qty > 0 else "0",
        "",
        rupiah(total_laba)
    ])

    # Compute column widths
    avail_width = width - left_right_margin * 2
    col_fracs = [0.05, 0.30, 0.10, 0.15, 0.15, 0.12, 0.13]
    col_widths = [avail_width * f for f in col_fracs]

    sales_table = Table(data_table, colWidths=col_widths, repeatRows=1)
    sales_style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor('#0b6fa4')),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 10),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (2, 0), (2, -1), "CENTER"),
        ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor('#d3d3d3')),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, -1), (-1, -1), 10),
    ])
    
    # Alternating row colors
    for r in range(1, len(data_table) - 1):
        if r % 2 == 1:
            sales_style.add('BACKGROUND', (0, r), (-1, r), colors.HexColor('#f0f0f0'))
    
    sales_table.setStyle(sales_style)
    story.append(sales_table)
    story.append(Spacer(1, 12))

    # Summary metrics section
    if rows:
        story.append(Paragraph("📈 RINGKASAN PENJUALAN", section_style))
        summary_text = f"""
<b>Total Barang Terjual:</b> {total_qty} unit<br/>
<b>Total Nilai Modal Terpakai:</b> {rupiah(total_modal_spent)}<br/>
<b>Total Nilai Penjualan:</b> {rupiah(total_selling_value)}<br/>
<b>Total Laba Bersih:</b> <font color="green"><b>{rupiah(total_laba)}</b></font><br/>
<b>Margin Profit:</b> {((total_laba / total_selling_value * 100) if total_selling_value > 0 else 0):.2f}%
"""
        story.append(Paragraph(summary_text, summary_style))
        story.append(Spacer(1, 12))

    # Incoming stock table
    if stok_rows:
        story.append(Paragraph("📦 STOK MASUK (PEMBELIAN BARANG)", section_style))
        stok_data = [["No", "Nama Barang", "Qty Masuk", "Harga Modal/Unit", "Total Nilai Masuk"]]
        total_nilai_masuk = 0
        
        for idx, (nama, stok_masuk, modal) in enumerate(stok_rows, start=1):
            stok_masuk = int(stok_masuk)
            modal = int(modal)
            nilai = stok_masuk * modal
            total_nilai_masuk += nilai
            stok_data.append([
                idx,
                nama,
                stok_masuk,
                rupiah(modal),
                rupiah(nilai)
            ])
        
        stok_data.append([
            "",
            "TOTAL",
            "",
            "",
            rupiah(total_nilai_masuk)
        ])

        stok_fracs = [0.05, 0.40, 0.15, 0.18, 0.22]
        stok_col_widths = [avail_width * f for f in stok_fracs]
        stok_table = Table(stok_data, colWidths=stok_col_widths, repeatRows=1)
        stok_style = TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor('#4a7c59')),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 10),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
            ("ALIGN", (0, 0), (0, -1), "CENTER"),
            ("ALIGN", (2, 0), (2, -1), "CENTER"),
            ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 1), (-1, -1), 9),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor('#d3d3d3')),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, -1), (-1, -1), 10),
        ])
        
        for r in range(1, len(stok_data) - 1):
            if r % 2 == 1:
                stok_style.add('BACKGROUND', (0, r), (-1, r), colors.HexColor('#f0f0f0'))
        
        stok_table.setStyle(stok_style)
        story.append(stok_table)
        story.append(Spacer(1, 12))
        
        # Stock purchase summary
        story.append(Paragraph("📦 RINGKASAN PEMBELIAN", section_style))
        stock_summary = f"<b>Total Nilai Pembelian Barang:</b> {rupiah(total_nilai_masuk)}"
        story.append(Paragraph(stock_summary, summary_style))
        story.append(Spacer(1, 12))

    # Footer with timestamp
    story.append(Spacer(1, 10))
    story.append(Paragraph("═" * 80, meta_style))
    footer_time = datetime.now().strftime("%H:%M:%S")
    story.append(Paragraph(f"Laporan dibuat pada: {today} {footer_time}", meta_style))
    story.append(Paragraph("═" * 80, meta_style))

    # Build PDF
    doc.build(story)
    try:
        with open(file, "rb") as f:
            await update.message.reply_document(f)
    except Exception as e:
        logging.error(f"Failed to send closing PDF: {e}")
        await update.message.reply_text(f"Gagal mengirim PDF: {str(e)}")


# ==========================
# LAPORAN BULANAN
# ==========================
async def laporan_bulanan(update, context):
    await update.message.reply_text("Masukkan bulan (format: YYYY-MM):")
    return 100

async def generate_bulanan(update, context):
    bulan = update.message.text
    # Group transactions by barang for the month and render a styled PDF table
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("""
        SELECT t.barang, SUM(t.jumlah) as terjual, b.modal, b.jual, b.margin
        FROM transaksi t
        JOIN barang b ON t.barang = b.nama
        WHERE t.tanggal LIKE ?
        GROUP BY t.barang
    """, (bulan + "%",))
    rows = cur.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("Tidak ada transaksi di bulan tersebut.")
        return ConversationHandler.END

    file = f"laporan_bulanan_{bulan}.pdf"
    c = canvas.Canvas(file, pagesize=A4)
    width, height = A4

    c.setFont("Helvetica-Bold", 16)
    c.drawString(40, height - 40, "LAPORAN BULANAN")
    c.setFont("Helvetica", 12)
    c.drawString(40, height - 60, f"Bulan: {bulan}")
    c.line(40, height - 72, width - 40, height - 72)

    data = [["Barang", "Terjual", "Modal", "Jual", "Margin", "Laba"]]
    total_laba = 0
    for barang, terjual, modal, jual, margin in rows:
        laba = int(terjual) * int(margin)
        total_laba += laba
        data.append([barang, int(terjual), rupiah(modal), rupiah(jual), rupiah(margin), rupiah(laba)])
    data.append(["", "", "", "", "TOTAL", rupiah(total_laba)])

    col_widths = [70*mm, 25*mm, 25*mm, 25*mm, 25*mm, 30*mm]
    table = Table(data, colWidths=col_widths)
    style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor('#333333')),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ALIGN", (1, 1), (-1, -1), "CENTER"),
        ("BACKGROUND", (0, -1), (-1, -1), colors.lightgrey),
    ])
    for i in range(1, len(data)-1):
        if i % 2 == 1:
            style.add('BACKGROUND', (0, i), (-1, i), colors.whitesmoke)
    table.setStyle(style)

    w, h = table.wrap(width - 80, height)
    table.drawOn(c, 40, height - 120 - h)
    c.save()
    try:
        with open(file, "rb") as f:
            await update.message.reply_document(f)
    except Exception as e:
        logging.error(f"Failed to send monthly report PDF: {e}")
        await update.message.reply_text(f"Gagal mengirim laporan: {str(e)}")
    return ConversationHandler.END

# ==========================
# MAIN BOT
# ==========================
logging.basicConfig(level=logging.INFO)
logging.getLogger("telegram").setLevel(logging.WARNING)

# Create a custom request object with longer timeouts (30s instead of default)
request = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0, write_timeout=30.0, pool_timeout=30.0)
app = ApplicationBuilder().token(BOT_TOKEN).request(request).build()

# Global error handler to ensure exceptions are logged by the application
async def _global_error_handler(update: Update | None, context: ContextTypes.DEFAULT_TYPE):
    logging.exception("Unhandled exception in update: %s", context.error)

try:
    app.add_error_handler(_global_error_handler)
except Exception:
    # Older versions or different runtimes may not have add_error_handler; ignore if unavailable
    pass

app.add_handler(CommandHandler("start", start))

# Input Barang
app.add_handler(ConversationHandler(
    entry_points=[MessageHandler(filters.Regex("^Input Barang$"), barang_input)],
    states={
        NAMA: [MessageHandler(filters.TEXT, input_nama)],
        MODAL: [MessageHandler(filters.TEXT, input_modal)],
        JUAL: [MessageHandler(filters.TEXT, input_jual)],
        STOK_AWAL: [MessageHandler(filters.TEXT, input_stok_awal)]
    },
    fallbacks=[]
))

# Tambah Stok
app.add_handler(ConversationHandler(
    entry_points=[MessageHandler(filters.Regex("^Tambah Stok$"), tambah_stok)],
    states={
        PILIH_BARANG_TAMBAH: [MessageHandler(filters.TEXT, pilih_barang_tambah)],
        INPUT_TAMBAH: [MessageHandler(filters.TEXT, input_tambah)]
    },
    fallbacks=[]
))

# Transaksi
app.add_handler(ConversationHandler(
    entry_points=[MessageHandler(filters.Regex("^Transaksi$"), transaksi)],
    states={
        TRANSAKSI_BARANG: [MessageHandler(filters.TEXT, transaksi_barang)],
        TRANSAKSI_JUMLAH: [MessageHandler(filters.TEXT, transaksi_jumlah)]
    },
    fallbacks=[]
))

app.add_handler(MessageHandler(filters.Regex("^Closing Harian$"), closing))
app.add_handler(ConversationHandler(
    entry_points=[MessageHandler(filters.Regex("^Laporan Bulanan$"), laporan_bulanan)],
    states={100: [MessageHandler(filters.TEXT, generate_bulanan)]},
    fallbacks=[]
))

app.add_handler(MessageHandler(filters.Regex("^List Barang$"), list_barang))

MAX_RETRIES = 5
RETRY_DELAY = 5

from telegram import error as tg_error

for attempt in range(1, MAX_RETRIES + 1):
    try:
        # Try to remove any webhook first (helps if bot was previously set to webhook)
        try:
            # First, try synchronous HTTP delete via Telegram API (reliable and doesn't require awaiting)
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=true"
            with urllib.request.urlopen(url, timeout=10) as resp:
                logging.info("deleteWebhook response: %s", resp.read().decode(errors='ignore'))
        except Exception as e:
            logging.info("deleteWebhook HTTP call failed (continuing): %s", e)
        # Note: don't call `app.bot.delete_webhook()` here because it's a coroutine
        # and cannot be awaited in this synchronous context. We already removed
        # any webhook via the HTTP API above.

        # Start polling and drop pending updates which can help avoid conflicts
        app.run_polling(drop_pending_updates=True)
        break
    except tg_error.Conflict:
        print(f"[Attempt {attempt}] Conflict: another getUpdates is running.")
        if attempt < MAX_RETRIES:
            print(f"Waiting {RETRY_DELAY}s and retrying... (stop other instances first)")
            time.sleep(RETRY_DELAY)
            continue
        else:
            print("Max retries reached. Make sure no other bot instance is running or remove webhooks.")
            raise
