#!/usr/bin/env python3
"""Convert PROJECT_SUMMARY.md to a polished PDF using reportlab."""

from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Image
from reportlab.lib import colors
from reportlab.pdfgen import canvas
import textwrap
from datetime import datetime

# Read the markdown file
with open("PROJECT_SUMMARY.md", "r") as f:
    content = f.read()

# Create PDF
pdf_path = "AML_Chatbot_Project_Summary.pdf"
doc = SimpleDocTemplate(
    pdf_path,
    pagesize=letter,
    rightMargin=0.75 * inch,
    leftMargin=0.75 * inch,
    topMargin=0.75 * inch,
    bottomMargin=0.75 * inch,
)

# Define styles
styles = getSampleStyleSheet()
title_style = ParagraphStyle(
    "CustomTitle",
    parent=styles["Heading1"],
    fontSize=24,
    textColor=colors.HexColor("#1f4788"),
    spaceAfter=12,
    bold=True,
)
heading_style = ParagraphStyle(
    "CustomHeading",
    parent=styles["Heading2"],
    fontSize=14,
    textColor=colors.HexColor("#2e5c8a"),
    spaceAfter=8,
    spaceBefore=8,
    bold=True,
)
subheading_style = ParagraphStyle(
    "CustomSubHeading",
    parent=styles["Heading3"],
    fontSize=11,
    textColor=colors.HexColor("#3a6f9c"),
    spaceAfter=6,
    spaceBefore=6,
    bold=True,
)
body_style = ParagraphStyle(
    "CustomBody",
    parent=styles["BodyText"],
    fontSize=10,
    spaceAfter=6,
    alignment=0,
    leading=12,
)

# Build PDF content
story = []

# Title page
story.append(Spacer(1, 1.5 * inch))
story.append(Paragraph("AML NL→SQL Chatbot", title_style))
story.append(Paragraph("Project Summary & Status", heading_style))
story.append(Spacer(1, 0.3 * inch))
story.append(Paragraph(
    "A conversational AI chatbot for anti-money-laundering analysts that transforms natural language questions into SQL queries and delivers deterministic compliance findings backed by auditable rules.",
    body_style
))
story.append(Spacer(1, 0.5 * inch))
story.append(Paragraph(f"Generated: {datetime.now().strftime('%B %d, %Y')}", ParagraphStyle("date", parent=styles["Normal"], fontSize=9, textColor=colors.grey)))
story.append(PageBreak())

# Parse content and add to PDF
lines = content.split("\n")
i = 0
while i < len(lines):
    line = lines[i].strip()

    # Skip empty lines
    if not line:
        story.append(Spacer(1, 0.1 * inch))
        i += 1
        continue

    # Main title (skip, already on title page)
    if line.startswith("# ") and "Project Summary" in line:
        i += 1
        continue

    # Section headings
    if line.startswith("## "):
        text = line.replace("## ", "")
        story.append(Paragraph(text, heading_style))
        i += 1
        continue

    # Subsection headings
    if line.startswith("### "):
        text = line.replace("### ", "")
        story.append(Paragraph(text, subheading_style))
        i += 1
        continue

    # Code blocks (render as indented text)
    if line.startswith("```"):
        i += 1
        code_lines = []
        while i < len(lines) and not lines[i].strip().startswith("```"):
            code_lines.append(lines[i])
            i += 1
        i += 1  # skip closing ```
        code_text = "\n".join(code_lines).strip()
        code_style = ParagraphStyle(
            "Code",
            parent=styles["Normal"],
            fontSize=8,
            fontName="Courier",
            textColor=colors.HexColor("#666666"),
            backColor=colors.HexColor("#f5f5f5"),
            spaceAfter=6,
            leftIndent=0.2 * inch,
        )
        for code_line in code_text.split("\n"):
            if code_line.strip():
                story.append(Paragraph(code_line, code_style))
        story.append(Spacer(1, 0.1 * inch))
        continue

    # Tables (detect markdown tables by | separator)
    if "|" in line and "-" not in line:
        # Start of table
        table_lines = [line]
        i += 1
        while i < len(lines) and "|" in lines[i]:
            table_lines.append(lines[i])
            i += 1

        # Parse table
        table_data = []
        for table_line in table_lines:
            cells = [cell.strip() for cell in table_line.split("|")]
            cells = [cell for cell in cells if cell and cell != "---" and cell.replace("-", "") and cell.replace(":", "")]
            if cells:
                table_data.append(cells)

        if table_data:
            table = Table(table_data, colWidths=[1.5*inch, 1.5*inch, 1.5*inch])
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2e5c8a")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 10),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
                ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#f9f9f9")),
                ("GRID", (0, 0), (-1, -1), 1, colors.HexColor("#cccccc")),
                ("FONTSIZE", (0, 1), (-1, -1), 9),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f9f9f9")]),
            ]))
            story.append(table)
            story.append(Spacer(1, 0.15 * inch))
        continue

    # Bold text
    if line.startswith("**") and line.endswith("**"):
        text = line.replace("**", "")
        story.append(Paragraph(f"<b>{text}</b>", body_style))
        i += 1
        continue

    # Bullet points
    if line.startswith("- "):
        text = line.replace("- ", "").strip()
        bullet_style = ParagraphStyle(
            "Bullet",
            parent=body_style,
            leftIndent=0.25 * inch,
            bulletIndent=0.15 * inch,
        )
        story.append(Paragraph(f"• {text}", bullet_style))
        i += 1
        continue

    # Regular paragraphs
    if line:
        story.append(Paragraph(line, body_style))
    i += 1

# Build PDF
doc.build(story)
print(f"✅ PDF created: {pdf_path}")
print(f"📊 File size: {__import__('os').path.getsize(pdf_path) / 1024:.1f} KB")
