#!/usr/bin/env python3
"""
publish_google_doc.py — Convert a local Markdown file into a styled Google Doc

This script reads a markdown file, parses it into basic HTML, and uploads
it to Google Drive using the `drive` API, requesting native Google Doc conversion.

Usage:
    python scripts/publish_google_doc.py state/travel/glacier-plan.md
"""

import sys
import os
import re
import io
from googleapiclient.http import MediaIoBaseUpload

# Add parent directory to path so we can import scripts.google_auth
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from google_auth import build_service

def markdown_to_html(md_text: str) -> str:
    """
    Very basic markdown to HTML converter to preserve structure for Google Docs.
    """
    html_lines = []
    in_list = False
    in_table = False

    # Remove frontmatter if present
    if md_text.startswith("---"):
        parts = md_text.split("---", 2)
        if len(parts) >= 3:
            md_text = parts[2]

    lines = md_text.split('\n')
    
    for line in lines:
        stripped = line.strip()
        
        # Headings
        if stripped.startswith('#'):
            # Close pending lists/tables
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            if in_table:
                html_lines.append("</table><br/>")
                in_table = False
                
            level = len(stripped) - len(stripped.lstrip('#'))
            text = stripped.lstrip('#').strip()
            # Convert formatting inside heading
            text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
            text = re.sub(r'`(.*?)`', r'<span style="font-family: monospace; background-color: #f1f1f1;">\1</span>', text)
            
            html_lines.append(f"<h{level}>{text}</h{level}>")
            continue
            
        # Tables
        if stripped.startswith('|'):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
                
            if not in_table:
                html_lines.append('<table border="1" style="border-collapse: collapse; width: 100%; border-color: #e0e0e0; margin-top: 10px; margin-bottom: 10px;">')
                in_table = True
            
            # Skip separator rows like |---|---|
            if set(stripped.replace('|', '').replace('-', '').replace(':', '').replace(' ', '')) == set():
                continue
                
            cells = [cell.strip() for cell in stripped.strip('|').split('|')]
            html_lines.append("<tr>")
            for cell in cells:
                # Format cell content
                cell = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', cell)
                cell = re.sub(r'`(.*?)`', r'<span style="font-family: monospace; background-color: #f1f1f1;">\1</span>', cell)
                html_lines.append(f"<td style='padding: 8px; border: 1px solid #e0e0e0;'>{cell}</td>")
            html_lines.append("</tr>")
            continue
        elif in_table:
            html_lines.append("</table><br/>")
            in_table = False

        # Lists
        if stripped.startswith('- ') or stripped.startswith('* '):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            text = stripped[2:].strip()
            text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
            text = re.sub(r'`(.*?)`', r'<span style="font-family: monospace; background-color: #f1f1f1;">\1</span>', text)
            html_lines.append(f"<li>{text}</li>")
            continue
        elif in_list and not stripped:
            html_lines.append("</ul>")
            in_list = False
            continue
        
        # Empty line
        if not stripped:
            html_lines.append("<br/>")
            continue
            
        # Bold and Code
        formatted = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', line)
        formatted = re.sub(r'`(.*?)`', r'<span style="font-family: monospace; background-color: #f1f1f1;">\1</span>', formatted)
        
        # Normal paragraph
        html_lines.append(f"<p>{formatted}</p>")

    if in_list:
        html_lines.append("</ul>")
    if in_table:
        html_lines.append("</table>")
        
    # Wrap in HTML
    return f"<html><body style='font-family: Arial, sans-serif; line-height: 1.5; color: #333333;'>{''.join(html_lines)}</body></html>"

def publish_to_gdoc(file_path: str) -> None:
    if not os.path.exists(file_path):
        print(f"Error: File not found: {file_path}")
        sys.exit(1)
        
    with open(file_path, "r", encoding="utf-8") as f:
        md_text = f.read()

    print("Converting Markdown to HTML...")
    html_content = markdown_to_html(md_text)
    
    print("Authenticating with Google Drive...")
    try:
        drive_service = build_service("drive", "v3")
    except Exception as e:
        print(f"Authentication failed. Please run setup or ensure credentials are correct: {e}")
        sys.exit(1)

    # Prepare file metadata and media
    file_name = os.path.basename(file_path).replace('.md', '')
    file_metadata = {
        'name': f"{file_name} (Artha Generated)",
        'mimeType': 'application/vnd.google-apps.document'
    }
    
    media = MediaIoBaseUpload(
        io.BytesIO(html_content.encode("utf-8")),
        mimetype='text/html',
        resumable=True
    )
    
    print("Uploading and converting to Google Doc...")
    try:
        file = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, webViewLink'
        ).execute()
        
        print(f"\nSuccess! Document created.")
        print(f"URL: {file.get('webViewLink')}")
        
    except Exception as e:
        print(f"Failed to create Google Doc: {e}")
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/publish_google_doc.py <path_to_markdown_file>")
        sys.exit(1)
        
    publish_to_gdoc(sys.argv[1])
