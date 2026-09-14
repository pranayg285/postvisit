# postvisit_report_gen.py
import os
import json
import html
import logging
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
import asyncio

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Report color scheme matching doctor_report_gen.py
COLOR_PRIMARY = "#007bff"
COLOR_SECONDARY = "#e8f3ff"
COLOR_BACKGROUND = "#F8F7FF"
COLOR_BORDER = "#E0E0E0"
COLOR_TEXT = "#000000"
DEFAULT_LOGO_URL = "https://www.friska.ai/wp-content/uploads/2025/10/Group-1000001149.png.webp"

def escape_html(text: str) -> str:
    """Escape HTML to prevent XSS."""
    if not text: return "N/A"
    return html.escape(str(text), quote=True)

def format_multiline_text(text: str) -> str:
    """Converts newlines to HTML breaks and bullet points to list items."""
    if not text: return ""
    
    # Handle bullet points from the doctor summary
    lines = text.split('\n')
    formatted_html = ""
    in_list = False
    
    for line in lines:
        line = line.strip()
        if line.startswith('- ') or line.startswith('• '):
            if not in_list:
                formatted_html += '<ul style="margin: 10px 0; padding-left: 20px; list-style-type: disc;">'
                in_list = True
            formatted_html += f'<li class="section-text" style="margin-bottom: 8px;">{escape_html(line[2:])}</li>'
        else:
            if in_list:
                formatted_html += '</ul>'
                in_list = False
            if line:
                formatted_html += f'<div class="section-text">{escape_html(line)}</div>'
                
    if in_list:
        formatted_html += '</ul>'
        
    return formatted_html

def generate_postvisit_pdf(session_id: str, db_path: str = "DB"):
    """Reads postvisit.json from the DB folder and generates a PDF report."""
    
    session_dir = Path(db_path) / session_id
    json_file = session_dir / "postvisit.json"
    pdf_out_path = session_dir / f"{session_id}_postvisit_report.pdf"
    
    if not json_file.exists():
        logger.error(f"JSON file not found: {json_file}")
        return None

    with open(json_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Extract Data safely
    patient = data.get("patient_info", {})
    facility = data.get("facility_info", {})
    session = data.get("session_details", {})
    stability = data.get("stability_score", {})
    breakdown = stability.get("breakdown", {})
    
    # Build HTML Content
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Friska Companion Post-Visit Report</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
    <style>
        /* PAGE MARGIN FIXES */
        @page:first {{
            size: letter;
            margin-top: 0;
            margin-bottom: 60px;
        }}
        @page {{
            size: letter;
            margin-top: 80px;
            margin-bottom: 60px;
        }}
        
        body {{ font-family: 'Inter', sans-serif; font-size: 11pt; line-height: 1.6; color: {COLOR_TEXT}; margin: 0; padding: 0; }}
        .page-wrapper {{ width: 8.5in; margin: 0 auto; background: white; padding-bottom: 50px; }}
        .header-container {{ background-color: {COLOR_BACKGROUND}; padding: 25px 0.75in; display: flex; justify-content: space-between; align-items: center; }}
        .header-title {{ font-size: 16pt; font-weight: 700; color: {COLOR_PRIMARY}; }}
        .header-logo img {{ height: 40px; }}
        .divider-line {{ height: 2px; background-color: {COLOR_SECONDARY}; }}
        .body-content {{ padding: 20px 0.75in; }}
        
        .info-section {{ display: flex; margin: 20px 0; width: 100%; }}
        .info-column {{ flex: 1; padding-right: 20px; }}
        .info-label {{ font-weight: 700; color: {COLOR_PRIMARY}; font-size: 11pt; margin-bottom: 8px; display: block; }}
        .info-value {{ font-weight: 400; font-size: 11pt; margin-bottom: 4px; display: block; }}
        
        .section-title {{ font-weight: 700; color: {COLOR_PRIMARY}; font-size: 14pt; margin: 25px 0 15px 0; border-bottom: 1px solid {COLOR_BORDER}; padding-bottom: 5px; page-break-after: avoid; break-after: avoid; }}
        .subsection-title {{ font-weight: 700; font-size: 11pt; margin: 15px 0 6px 0; page-break-after: avoid; break-after: avoid;}}
        .section-text {{ margin-bottom: 12px; }}
        
        /* BOX BREAKING FIX */
        .score-box {{ 
            background: {COLOR_SECONDARY}; 
            padding: 15px; 
            border-radius: 6px; 
            margin: 15px 0; 
            border: 1px solid {COLOR_PRIMARY}; 
            page-break-inside: avoid;
            break-inside: avoid;
        }}
        
        .score-row {{ display: flex; justify-content: space-between; margin-bottom: 8px; font-weight: 600; }}
        .score-tier {{ color: {COLOR_PRIMARY}; font-size: 13pt; text-align: center; margin: 10px 0; font-weight: 700; }}
        
        .disclaimer {{ margin-top: 40px; padding-top: 15px; font-size: 9pt; border-top: 1px solid {COLOR_BORDER}; }}
    </style>
</head>
<body>
    <div class="page-wrapper">
        <div class="header-container">
            <div class="header-title">Friska Companion Clinician<br>Post-Visit Assessment Report</div>
            <div class="header-logo"><img src="{DEFAULT_LOGO_URL}" alt="Logo"></div>
        </div>
        <div class="divider-line"></div>
        
        <div class="body-content">
            <!-- Info Block -->
            <div class="info-section">
                <div class="info-column">
                    <span class="info-label">Patient</span>
                    <span class="info-value">{escape_html(patient.get('name'))}</span>
                    <span class="info-value"><strong>DOB:</strong> {escape_html(patient.get('dob'))}</span>
                    <span class="info-value"><strong>AGE:</strong> {escape_html(patient.get('age'))}</span>
                    <span class="info-value"><strong>SEX:</strong> {escape_html(patient.get('sex'))}</span>
                </div>
                <div class="info-column">
                    <span class="info-label">Facility</span>
                    <span class="info-value">{escape_html(facility.get('name'))}</span>
                    <span class="info-value">{escape_html(facility.get('phone'))}</span>
                    <span class="info-value">{escape_html(facility.get('address'))}</span>
                </div>
                <div class="info-column">
                    <span class="info-label">Session Details</span>
                    <span class="info-value">{escape_html(session.get('session_id'))}</span>
                    <span class="info-value">{escape_html(session.get('session_date'))}</span>
                </div>
            </div>
            
            <!-- Chief Complaint -->
            <div class="section-title">Initial Chief Complaint</div>
            <div class="section-text" style="font-weight: 600; font-size: 12pt;">
                "{escape_html(data.get('chief_complaint', 'Not provided'))}"
            </div>
            
            <!-- Doctor Summary -->
            <div class="section-title">Physician Post-Visit Summary</div>
            {format_multiline_text(data.get('doctor_summary', ''))}
            
            <!-- STABILITY SCORE BLOCK (Wrapped to keep heading and box together) -->
            <div style="page-break-inside: avoid; break-inside: avoid;">
                <div class="section-title">Post-Visit Stability Assessment</div>
                
                <div class="score-box">
                    <div class="score-tier">Tier: {escape_html(stability.get('tier'))} ({escape_html(stability.get('total_score'))})</div>
                    <div class="section-text" style="text-align: center; font-style: italic;">{escape_html(stability.get('tier_description'))}</div>
                    <hr style="border: 0; border-top: 1px solid #ccc; margin: 15px 0;">
                    <div class="score-row"><span>Condition Trajectory:</span> <span>{escape_html(breakdown.get('condition_trajectory_score'))}</span></div>
                    <div class="score-row"><span>Adherence:</span> <span>{escape_html(breakdown.get('adherence_score'))}</span></div>
                    <div class="score-row"><span>Red Flag Absence:</span> <span>{escape_html(breakdown.get('red_flag_score'))}</span></div>
                </div>
            </div>
            
            <div class="subsection-title">Assessment Rationale</div>
            <div class="section-text">{escape_html(breakdown.get('rationale'))}</div>
            
            <div class="subsection-title">Clinical Summary</div>
            <div class="section-text">{escape_html(stability.get('clinical_summary'))}</div>
            
            <!-- Disclaimer -->
            <div class="disclaimer">
                <strong>Friska Companion Disclaimer:</strong> This post-visit report is generated by Friska Companion and requires clinical validation. It is not a substitute for professional medical judgment. 
                <br><em>{escape_html(stability.get('tier_scale'))}</em>
            </div>
        </div>
    </div>
</body>
</html>"""

    # --- ADD THIS BLOCK TO FIX THE WINDOWS ERROR ---
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    # ----------------------------------------------
    
    # Generate PDF via Playwright
    logger.info(f"Generating PDF for {session_id}...")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(html_content, wait_until="networkidle")
            page.wait_for_timeout(1000) 
            
            footer_template = f"""
            <div style="font-size: 9pt; font-family: sans-serif; color: {COLOR_TEXT}; width: 100%; padding: 0 0.75in; display: flex; justify-content: space-between; border-top: 1px solid {COLOR_BORDER}; margin-top: 5px;">
                <span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span>
            </div>
            """
            
            page.pdf(
                path=str(pdf_out_path),
                format="Letter",
                margin={"top": "0", "right": "0", "bottom": "60px", "left": "0"},
                print_background=True,
                prefer_css_page_size=True,  # <--- CRITICAL FIX FOR MARGINS
                display_header_footer=True,
                header_template="<div></div>",
                footer_template=footer_template,
            )
            logger.info(f"Successfully generated: {pdf_out_path}")
            return str(pdf_out_path)
        finally:
            browser.close()

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python postvisit_report_gen.py <session_id>")
    else:
        # Assumes a DB folder exists in the current directory containing {session_id}/postvisit.json
        generate_postvisit_pdf(sys.argv[1])